"""One bounded process-local worker for deterministic city workbench runs."""

from __future__ import annotations

import secrets
from collections import deque
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from adlife.city.run_store import StoredCityRun
from adlife.city.runs import PreparedCityRun
from adlife.city.workbench_runs import WorkbenchRunSummary
from adlife.city.workbench_validation import ValidatedWorkbenchRun
from adlife.core.domain.person import DomainModel
from adlife.core.ports.run_store import ISO_UTC_PATTERN

MAX_TERMINAL_JOBS = 32

WorkbenchJobPhase = Literal[
    "queued",
    "evaluating",
    "persisting",
    "verifying",
    "completed",
    "failed",
    "cancelled",
]

_TERMINAL_PHASES = frozenset({"completed", "failed", "cancelled"})
_CANCELLABLE_PHASES = frozenset({"queued", "evaluating"})


class WorkbenchJobBusy(RuntimeError):
    """Only one workbench run may be active in this process."""


class WorkbenchJobNotFound(RuntimeError):
    """The bounded process-local history has no such job."""


class WorkbenchJobCancellationConflict(RuntimeError):
    """Publication has started, so cooperative cancellation is no longer safe."""


class WorkbenchJobManagerClosed(RuntimeError):
    """The worker is draining or shut down and accepts no more jobs."""


class WorkbenchJobSubmissionError(RuntimeError):
    """The worker could not accept a prospective job."""


class WorkbenchJobFailure(DomainModel):
    """A constant screened terminal failure safe for the local control API."""

    code: Literal["evaluation-failed", "persistence-failed", "verification-failed"]
    message: Literal[
        "The deterministic city evaluation failed.",
        "The city run could not be persisted.",
        "The persisted city run could not be verified.",
    ]

    @model_validator(mode="after")
    def matching_code_and_message(self) -> Self:
        expected = {
            "evaluation-failed": "The deterministic city evaluation failed.",
            "persistence-failed": "The city run could not be persisted.",
            "verification-failed": "The persisted city run could not be verified.",
        }[self.code]
        if self.message != expected:
            raise ValueError("workbench job failure code and message do not match")
        return self


class WorkbenchJobView(DomainModel):
    """Immutable bounded operational state; timestamps never enter artifacts."""

    schema_version: Literal[1] = 1
    job_id: str = Field(pattern=r"^job-[0-9a-f]{32}$")
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    phase: WorkbenchJobPhase
    created_at: str = Field(pattern=ISO_UTC_PATTERN)
    updated_at: str = Field(pattern=ISO_UTC_PATTERN)
    cancellation_requested: bool
    can_cancel: bool
    error: WorkbenchJobFailure | None = None
    result: WorkbenchRunSummary | None = None

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("workbench job update precedes creation")
        expected_can_cancel = self.phase in _CANCELLABLE_PHASES and not self.cancellation_requested
        if self.can_cancel != expected_can_cancel:
            raise ValueError("workbench job cancellation capability is inconsistent")
        if self.phase in {"persisting", "verifying", "completed", "failed"} and (
            self.cancellation_requested
        ):
            raise ValueError("workbench job phase cannot carry a cancellation request")
        if self.phase == "completed":
            if self.result is None or self.error is not None:
                raise ValueError("completed workbench job requires only a verified result")
            if self.result.run_id != self.run_id:
                raise ValueError("completed workbench job result has a different run identifier")
        elif self.phase == "failed":
            if self.error is None or self.result is not None:
                raise ValueError("failed workbench job requires only a screened error")
        elif self.phase == "cancelled":
            if not self.cancellation_requested or self.error is not None or self.result is not None:
                raise ValueError("cancelled workbench job has inconsistent terminal data")
        elif self.error is not None or self.result is not None:
            raise ValueError("active workbench job cannot expose terminal data")
        return self


class _RunOperations(Protocol):
    def ensure_available(self, run_id: str) -> None: ...

    def prepare(self, validated: ValidatedWorkbenchRun) -> PreparedCityRun: ...

    def publish(self, prepared: PreparedCityRun) -> StoredCityRun: ...

    def verified_summary(self, run_id: str) -> WorkbenchRunSummary: ...


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    run_id: str
    validated: ValidatedWorkbenchRun
    phase: WorkbenchJobPhase
    created_at: str
    updated_at: str
    cancellation_requested: bool = False
    error: WorkbenchJobFailure | None = None
    result: WorkbenchRunSummary | None = None


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _new_worker_executor() -> Executor:
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="adlife-city-job")


def _failure(phase: Literal["evaluating", "persisting", "verifying"]) -> WorkbenchJobFailure:
    if phase == "evaluating":
        return WorkbenchJobFailure(
            code="evaluation-failed",
            message="The deterministic city evaluation failed.",
        )
    if phase == "persisting":
        return WorkbenchJobFailure(
            code="persistence-failed",
            message="The city run could not be persisted.",
        )
    return WorkbenchJobFailure(
        code="verification-failed",
        message="The persisted city run could not be verified.",
    )


class CityJobManager:
    """Serialize one active run and retain at most 32 terminal public records."""

    def __init__(self, repository: _RunOperations) -> None:
        self._repository = repository
        self._executor = _new_worker_executor()
        self._lock = Lock()
        self._close_lock = Lock()
        self._records: dict[str, _JobRecord] = {}
        self._terminal_ids: deque[str] = deque()
        self._active_job_id: str | None = None
        self._closed = False

    def _new_job_id_locked(self) -> str:
        for _ in range(8):
            candidate = f"job-{secrets.token_hex(16)}"
            if candidate not in self._records:
                return candidate
        raise WorkbenchJobSubmissionError("The workbench worker could not allocate a job.")

    @staticmethod
    def _view(record: _JobRecord) -> WorkbenchJobView:
        return WorkbenchJobView(
            job_id=record.job_id,
            run_id=record.run_id,
            phase=record.phase,
            created_at=record.created_at,
            updated_at=record.updated_at,
            cancellation_requested=record.cancellation_requested,
            can_cancel=(record.phase in _CANCELLABLE_PHASES and not record.cancellation_requested),
            error=record.error,
            result=record.result,
        )

    @staticmethod
    def _touch(record: _JobRecord) -> None:
        current = _utc_now()
        record.updated_at = max(record.updated_at, current)

    def _transition_locked(self, record: _JobRecord, phase: WorkbenchJobPhase) -> None:
        record.phase = phase
        self._touch(record)

    def _terminal_locked(
        self,
        record: _JobRecord,
        phase: Literal["completed", "failed", "cancelled"],
        *,
        error: WorkbenchJobFailure | None = None,
        result: WorkbenchRunSummary | None = None,
    ) -> None:
        record.error = error
        record.result = result
        self._transition_locked(record, phase)
        if self._active_job_id == record.job_id:
            self._active_job_id = None
        self._terminal_ids.append(record.job_id)
        while len(self._terminal_ids) > MAX_TERMINAL_JOBS:
            expired = self._terminal_ids.popleft()
            self._records.pop(expired, None)

    def submit(self, validated: ValidatedWorkbenchRun) -> WorkbenchJobView:
        """Preflight and schedule one frozen request without waiting for evaluation."""
        if not isinstance(validated, ValidatedWorkbenchRun):
            raise TypeError("validated must be a ValidatedWorkbenchRun")
        run_id = validated.workbench_input.draft.settings.run_id
        with self._lock:
            if self._closed:
                raise WorkbenchJobManagerClosed("The workbench worker is closed.")
            if self._active_job_id is not None:
                raise WorkbenchJobBusy("Another workbench run is already active.")
            self._repository.ensure_available(run_id)
            job_id = self._new_job_id_locked()
            created_at = _utc_now()
            record = _JobRecord(
                job_id=job_id,
                run_id=run_id,
                validated=validated,
                phase="queued",
                created_at=created_at,
                updated_at=created_at,
            )
            self._records[job_id] = record
            self._active_job_id = job_id
            queued = self._view(record)
            try:
                self._executor.submit(self._run_job, job_id)
            except Exception:
                self._records.pop(job_id, None)
                self._active_job_id = None
                raise WorkbenchJobSubmissionError(
                    "The workbench worker could not accept the job."
                ) from None
            return queued

    def _cancel_before_publication_locked(self, record: _JobRecord) -> bool:
        if not record.cancellation_requested:
            return False
        self._terminal_locked(record, "cancelled")
        return True

    def _fail(self, job_id: str, phase: Literal["evaluating", "persisting", "verifying"]) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.phase in _TERMINAL_PHASES:
                return
            if phase == "evaluating" and record.cancellation_requested:
                self._terminal_locked(record, "cancelled")
                return
            self._terminal_locked(record, "failed", error=_failure(phase))

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            record = self._records[job_id]
            if self._cancel_before_publication_locked(record):
                return
            self._transition_locked(record, "evaluating")
            validated = record.validated

        try:
            prepared = self._repository.prepare(validated)
        except Exception:
            self._fail(job_id, "evaluating")
            return

        with self._lock:
            record = self._records[job_id]
            if self._cancel_before_publication_locked(record):
                return
            self._transition_locked(record, "persisting")

        try:
            self._repository.publish(prepared)
        except Exception:
            self._fail(job_id, "persisting")
            return

        with self._lock:
            record = self._records[job_id]
            self._transition_locked(record, "verifying")
            run_id = record.run_id

        try:
            summary = self._repository.verified_summary(run_id)
        except Exception:
            self._fail(job_id, "verifying")
            return
        if not isinstance(summary, WorkbenchRunSummary) or summary.run_id != run_id:
            self._fail(job_id, "verifying")
            return

        with self._lock:
            record = self._records[job_id]
            self._terminal_locked(record, "completed", result=summary)

    def get(self, job_id: str) -> WorkbenchJobView:
        """Return one immutable snapshot or a stable unknown-job refusal."""
        if not isinstance(job_id, str):
            raise WorkbenchJobNotFound("The workbench job does not exist.")
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise WorkbenchJobNotFound("The workbench job does not exist.")
            return self._view(record)

    def active_job(self) -> WorkbenchJobView | None:
        """Return the sole active record without exposing its frozen scientific input."""
        with self._lock:
            if self._active_job_id is None:
                return None
            return self._view(self._records[self._active_job_id])

    def recent_jobs(self) -> tuple[WorkbenchJobView, ...]:
        """Return at most 32 terminal records, newest first."""
        with self._lock:
            return tuple(
                self._view(self._records[job_id]) for job_id in reversed(self._terminal_ids)
            )

    def cancel(self, job_id: str) -> WorkbenchJobView:
        """Request cooperative cancellation only before publication starts."""
        with self._lock:
            record = self._records.get(job_id) if isinstance(job_id, str) else None
            if record is None:
                raise WorkbenchJobNotFound("The workbench job does not exist.")
            if record.phase not in _CANCELLABLE_PHASES:
                raise WorkbenchJobCancellationConflict(
                    "The workbench job can no longer be cancelled."
                )
            if not record.cancellation_requested:
                record.cancellation_requested = True
                self._touch(record)
            return self._view(record)

    def close(self) -> None:
        """Stop acceptance and drain the owned worker; safe to call repeatedly."""
        with self._close_lock:
            with self._lock:
                self._closed = True
            self._executor.shutdown(wait=True, cancel_futures=False)


__all__ = [
    "MAX_TERMINAL_JOBS",
    "CityJobManager",
    "WorkbenchJobBusy",
    "WorkbenchJobCancellationConflict",
    "WorkbenchJobFailure",
    "WorkbenchJobManagerClosed",
    "WorkbenchJobNotFound",
    "WorkbenchJobPhase",
    "WorkbenchJobSubmissionError",
    "WorkbenchJobView",
]
