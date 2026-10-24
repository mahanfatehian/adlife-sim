"""One bounded process-local worker for deterministic city workbench runs."""

from __future__ import annotations

import asyncio
import secrets
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal, Protocol, Self, cast

from pydantic import Field, field_validator, model_validator

from adlife.adapters.cognition.city_service import CityCognitionCancellation
from adlife.city.run_store import StoredCityRun
from adlife.city.runs import (
    PreparedCityRun,
    PreparedCityScientificRun,
    PreparedCityScientificRunV2,
)
from adlife.city.workbench_input import WorkbenchRunInputV3, WorkbenchRunInputV4
from adlife.city.workbench_providers import (
    WorkbenchOperationLease,
    WorkbenchOperationState,
    WorkbenchProviderBusy,
    WorkbenchProviderClosed,
)
from adlife.city.workbench_runs import (
    WorkbenchCognitionOperation,
    WorkbenchCognitionOperationV2,
    WorkbenchRunSummary,
)
from adlife.city.workbench_validation import ValidatedWorkbenchRun
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_MAX_AGGREGATE_OUTPUT_TOKENS,
    CITY_COGNITION_SELECTION_LIMITS,
    CityCognitionEvaluationV2,
    CityCognitionFundedResponseLimit,
    city_cognition_funding_versions,
)
from adlife.core.domain.person import DomainModel
from adlife.core.ports.run_store import ISO_UTC_PATTERN

MAX_TERMINAL_JOBS = 32
_MAX_ACCEPTED_TOKEN_CEILING = CITY_COGNITION_MAX_AGGREGATE_OUTPUT_TOKENS
_OFFLINE_LIMITS = frozenset(
    limits.offline_funded_response_limit for limits in CITY_COGNITION_SELECTION_LIMITS.values()
)


def _versioned_provider_funding(mode: object, limit: object, ceiling: object) -> bool:
    """Whether some frozen selection-policy version allows this provider funding."""
    return bool(city_cognition_funding_versions(mode, limit, ceiling))


WorkbenchJobPhase = Literal[
    "queued",
    "evaluating",
    "simulating",
    "selecting-explanations",
    "requesting-explanations",
    "planning-cognition",
    "requesting-cognition",
    "applying-cognition",
    "persisting",
    "verifying",
    "completed",
    "failed",
    "cancelled",
]

_TERMINAL_PHASES = frozenset({"completed", "failed", "cancelled"})
_CANCELLABLE_PHASES = frozenset(
    {
        "queued",
        "evaluating",
        "simulating",
        "selecting-explanations",
        "requesting-explanations",
        "planning-cognition",
        "requesting-cognition",
        "applying-cognition",
    }
)
_SEED_PATTERN = r"^(?:0|[1-9][0-9]{0,18})$"
# The widest frozen V10 selection admits at most 1000 requests per run (policy V3;
# V2 runs fund at most 60), so no run funds more.
MAX_COGNITION_PROGRESS_REQUESTS = max(
    limits.selected_requests_per_run for limits in CITY_COGNITION_SELECTION_LIMITS.values()
)
# The widest frozen in-flight request ceiling of any selection-policy version.
MAX_COGNITION_PROGRESS_CONCURRENCY = max(
    max(limits.concurrency_limit("remote"), limits.concurrency_limit("local"))
    for limits in CITY_COGNITION_SELECTION_LIMITS.values()
)
# Planning has frozen the funded requests in these phases, so each reports a live count.
_PROGRESS_PHASES = frozenset(
    {"requesting-cognition", "applying-cognition", "persisting", "verifying", "completed"}
)
# Once cognition has returned complete evidence, every funded request has an outcome.
_FINAL_PROGRESS_PHASES = frozenset({"applying-cognition", "persisting", "verifying", "completed"})


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


class WorkbenchAcceptedSettings(DomainModel):
    """Bounded immutable scientific settings retained through every job phase."""

    schema_version: Literal[1] = 1
    workbench_input_schema_version: Literal[1, 2, 3, 4]
    run_schema_version: Literal[7, 8, 9, 10]
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    seed: str = Field(pattern=_SEED_PATTERN, max_length=19)
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    response_mode: Literal["deterministic-rules"]
    effect_scope: Literal["bounded-signals-v1"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    cognition_mode: Literal["rules", "mock", "local", "remote"] | None = None
    provider_profile_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
    )
    provider_revision_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    funded_response_limit: CityCognitionFundedResponseLimit | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    aggregate_output_token_ceiling: int | None = Field(
        default=None, ge=0, le=_MAX_ACCEPTED_TOKEN_CEILING
    )

    @field_validator(
        "workbench_input_schema_version",
        "run_schema_version",
        "funded_response_limit",
        mode="before",
    )
    @classmethod
    def exact_schema_versions(cls, value: object) -> object:
        if value is None:
            return value
        if type(value) is not int:
            raise ValueError("accepted version and funding values must be exact integers")
        return value

    @model_validator(mode="after")
    def legal_schema_pair(self) -> Self:
        if (self.workbench_input_schema_version, self.run_schema_version) not in (
            (1, 7),
            (2, 8),
            (3, 9),
            (4, 10),
        ):
            raise ValueError("accepted workbench input and run schema versions do not match")
        cognition_fields = (
            self.cognition_mode,
            self.provider_profile_id,
            self.provider_revision_sha256,
            self.aggregate_output_token_ceiling,
        )
        v10_fields = (self.effect_scope, self.funded_response_limit)
        if self.workbench_input_schema_version not in {3, 4}:
            if any(value is not None for value in cognition_fields):
                raise ValueError("pre-schema-3 settings cannot carry cognition configuration")
            if any(value is not None for value in v10_fields):
                raise ValueError("pre-schema-4 settings cannot carry bounded cognition fields")
            return self
        if self.workbench_input_schema_version == 4:
            if self.effect_scope != "bounded-signals-v1" or self.funded_response_limit is None:
                raise ValueError("schema-4 settings require bounded cognition configuration")
            if self.cognition_mode is None or self.aggregate_output_token_ceiling is None:
                raise ValueError("schema-4 settings require cognition configuration")
            provider_mode = self.cognition_mode in {"local", "remote"}
            if provider_mode:
                if self.provider_profile_id is None or self.provider_revision_sha256 is None:
                    raise ValueError("schema-4 provider binding is incomplete")
                if not _versioned_provider_funding(
                    self.cognition_mode,
                    self.funded_response_limit,
                    self.aggregate_output_token_ceiling,
                ):
                    raise ValueError("schema-4 provider funding ceiling is inconsistent")
            elif (
                self.provider_profile_id is not None
                or self.provider_revision_sha256 is not None
                or self.funded_response_limit not in _OFFLINE_LIMITS
                or self.aggregate_output_token_ceiling != 0
            ):
                raise ValueError("offline schema-4 provider configuration is misplaced")
            return self
        if any(value is not None for value in v10_fields):
            raise ValueError("schema-3 settings cannot carry bounded cognition fields")
        if self.cognition_mode is None or self.aggregate_output_token_ceiling is None:
            raise ValueError("schema-3 settings require cognition configuration")
        provider_mode = self.cognition_mode in {"local", "remote"}
        if provider_mode != (
            self.provider_profile_id is not None and self.provider_revision_sha256 is not None
        ):
            raise ValueError("schema-3 provider binding is incomplete or misplaced")
        if not provider_mode and self.aggregate_output_token_ceiling != 0:
            raise ValueError("offline cognition cannot reserve provider tokens")
        return self

    @field_validator("seed")
    @classmethod
    def bounded_seed(cls, value: str) -> str:
        if int(value) > 2**63 - 1:
            raise ValueError("accepted job seed exceeds the supported range")
        return value


def _summary_matches_accepted_settings(
    summary: WorkbenchRunSummary,
    settings: WorkbenchAcceptedSettings,
) -> bool:
    return (
        summary.run_schema_version == settings.run_schema_version
        and summary.workbench_input_schema_version
        == (
            settings.workbench_input_schema_version
            if settings.workbench_input_schema_version == 4
            else None
        )
        and summary.city_id == settings.city_id
        and summary.scenario_id == settings.scenario_id
        and summary.campaign_ids == (settings.campaign_id,)
        and summary.seed == settings.seed
        and summary.agent_count == settings.agent_count
        and summary.days == settings.days
        and summary.effect_scope == settings.effect_scope
        and summary.cognition_mode == settings.cognition_mode
        and summary.provider_profile_id == settings.provider_profile_id
        and summary.provider_revision_sha256 == settings.provider_revision_sha256
        and summary.funded_response_limit == settings.funded_response_limit
        and summary.aggregate_output_token_ceiling == settings.aggregate_output_token_ceiling
    )


class WorkbenchCognitionProgress(DomainModel):
    """How many funded provider requests of one bounded-cognition job have an outcome.

    ``total`` is fixed when planning freezes the funded requests: the requests a local or
    remote run sends to its model, including those its verified cache then answers. Rules
    and mock send none, so their total is zero; budget-skipped requests are never counted.
    ``resolved`` counts provider answers, cache hits, and neutral fallbacks alike.
    """

    resolved: int = Field(ge=0, le=MAX_COGNITION_PROGRESS_REQUESTS)
    total: int = Field(ge=0, le=MAX_COGNITION_PROGRESS_REQUESTS)
    # The run's frozen in-flight request ceiling, so the page can estimate the wait.
    concurrency_limit: int = Field(ge=1, le=MAX_COGNITION_PROGRESS_CONCURRENCY)

    @field_validator("resolved", "total", "concurrency_limit", mode="before")
    @classmethod
    def exact_progress_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("cognition progress counts must be exact integers")
        return value

    @model_validator(mode="after")
    def bounded_count(self) -> Self:
        if self.resolved > self.total:
            raise ValueError("cognition progress cannot resolve more requests than it funds")
        return self


class WorkbenchCognitionProgressCounter:
    """Thread-safe, monotonic count the job worker advances and status readers snapshot.

    The worker creates one counter when planning has frozen the funded requests and hands
    ``advance`` to the cognition service, which calls it once for every funded request that
    reaches a terminal outcome. ``complete`` makes the count final after the service has
    returned complete evidence. ``advance`` never raises and never counts past ``total``,
    so reporting progress can neither fail nor alter a run; readers only ever receive an
    immutable ``snapshot``.
    """

    __slots__ = ("_concurrency_limit", "_lock", "_resolved", "_total")

    def __init__(self, total: int, *, concurrency_limit: int = 4) -> None:
        if type(total) is not int:
            raise TypeError("cognition progress total must be an exact integer")
        if not 0 <= total <= MAX_COGNITION_PROGRESS_REQUESTS:
            raise ValueError("cognition progress total is outside the funded request range")
        if type(concurrency_limit) is not int:
            raise TypeError("cognition progress concurrency must be an exact integer")
        if not 1 <= concurrency_limit <= MAX_COGNITION_PROGRESS_CONCURRENCY:
            raise ValueError("cognition progress concurrency is outside the frozen range")
        self._lock = Lock()
        self._resolved = 0
        self._total = total
        self._concurrency_limit = concurrency_limit

    def advance(self) -> None:
        """Count one more funded request with a terminal outcome."""
        with self._lock:
            if self._resolved < self._total:
                self._resolved += 1

    def complete(self) -> None:
        """Mark every funded request resolved once complete terminal evidence exists."""
        with self._lock:
            self._resolved = self._total

    def snapshot(self) -> WorkbenchCognitionProgress:
        """Return one immutable, internally consistent reading."""
        with self._lock:
            resolved, total = self._resolved, self._total
        return WorkbenchCognitionProgress(
            resolved=resolved, total=total, concurrency_limit=self._concurrency_limit
        )


class WorkbenchJobView(DomainModel):
    """Immutable bounded operational state; timestamps never enter artifacts.

    ``cognition_progress`` is the live count of a schema-4 job, ``{"resolved": k,
    "total": N}`` (see ``WorkbenchCognitionProgress``). It is ``null`` for schema 1-3
    jobs and until planning has frozen the funded requests, then present from
    ``requesting-cognition`` on: ``N`` is fixed, ``k`` only grows, never exceeds ``N``, and
    equals it from ``applying-cognition`` on. A failed or cancelled job keeps its last
    count. Counts advance between lifecycle transitions without moving ``updated_at``, and
    like the timestamps they never enter a run artifact, replay, or manifest.
    """

    schema_version: Literal[1] = 1
    job_id: str = Field(pattern=r"^job-[0-9a-f]{32}$")
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    accepted_settings: WorkbenchAcceptedSettings
    phase: WorkbenchJobPhase
    created_at: str = Field(pattern=ISO_UTC_PATTERN)
    updated_at: str = Field(pattern=ISO_UTC_PATTERN)
    cancellation_requested: bool
    can_cancel: bool
    error: WorkbenchJobFailure | None = None
    result: WorkbenchRunSummary | None = None
    cognition_progress: WorkbenchCognitionProgress | None = None

    @field_validator("accepted_settings", mode="before")
    @classmethod
    def revalidate_accepted_settings(cls, value: object) -> WorkbenchAcceptedSettings:
        if isinstance(value, WorkbenchAcceptedSettings):
            value = value.model_dump(mode="python")
        return WorkbenchAcceptedSettings.model_validate(value)

    @field_validator("result", mode="before")
    @classmethod
    def revalidate_result(cls, value: object) -> WorkbenchRunSummary | None:
        if value is None:
            return None
        if isinstance(value, WorkbenchRunSummary):
            value = value.model_dump(mode="python")
        return WorkbenchRunSummary.model_validate(value)

    @field_validator("cognition_progress", mode="before")
    @classmethod
    def revalidate_cognition_progress(cls, value: object) -> WorkbenchCognitionProgress | None:
        if value is None:
            return None
        if isinstance(value, WorkbenchCognitionProgress):
            value = value.model_dump(mode="python")
        return WorkbenchCognitionProgress.model_validate(value)

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
            if not _summary_matches_accepted_settings(self.result, self.accepted_settings):
                raise ValueError("completed workbench job result has different accepted settings")
        elif self.phase == "failed":
            if self.error is None or self.result is not None:
                raise ValueError("failed workbench job requires only a screened error")
        elif self.phase == "cancelled":
            if not self.cancellation_requested or self.error is not None or self.result is not None:
                raise ValueError("cancelled workbench job has inconsistent terminal data")
        elif self.error is not None or self.result is not None:
            raise ValueError("active workbench job cannot expose terminal data")
        return self

    @model_validator(mode="after")
    def coherent_cognition_progress(self) -> Self:
        progress = self.cognition_progress
        settings = self.accepted_settings
        if settings.workbench_input_schema_version != 4:
            if progress is not None:
                raise ValueError("pre-schema-4 workbench jobs cannot report cognition progress")
            return self
        if progress is None:
            if self.phase in _PROGRESS_PHASES:
                raise ValueError("planned bounded cognition requires progress")
            return self
        if self.phase not in _PROGRESS_PHASES | {"failed", "cancelled"}:
            raise ValueError("cognition progress cannot precede planning")
        provider_mode = settings.cognition_mode in {"local", "remote"}
        funded_ceiling = (settings.funded_response_limit or 0) if provider_mode else 0
        if progress.total > funded_ceiling:
            raise ValueError("cognition progress exceeds the frozen provider funding")
        if self.phase in _FINAL_PROGRESS_PHASES and progress.resolved != progress.total:
            raise ValueError("cognition progress must be final after cognition")
        if (
            self.phase == "completed"
            and provider_mode
            and self.result is not None
            and progress.total != self.result.funded_request_count
        ):
            raise ValueError("completed cognition progress does not match its funded requests")
        return self


class _RunOperations(Protocol):
    def preflight_cognition(
        self, validated: ValidatedWorkbenchRun
    ) -> WorkbenchCognitionOperation: ...

    def preflight_cognition_v2(
        self, validated: ValidatedWorkbenchRun
    ) -> WorkbenchCognitionOperationV2: ...

    def ensure_available(self, run_id: str) -> None: ...

    def prepare(self, validated: ValidatedWorkbenchRun) -> PreparedCityRun: ...

    def prepare_scientific(
        self,
        validated: ValidatedWorkbenchRun,
        operation: WorkbenchCognitionOperation,
    ) -> PreparedCityScientificRun: ...

    def prepare_scientific_v2(
        self,
        validated: ValidatedWorkbenchRun,
        operation: WorkbenchCognitionOperationV2,
    ) -> PreparedCityScientificRunV2: ...

    def select_explanations(
        self,
        prepared: PreparedCityScientificRun,
        operation: WorkbenchCognitionOperation,
    ) -> None: ...

    def resolve_cognition(
        self,
        prepared: PreparedCityScientificRun,
        operation: WorkbenchCognitionOperation,
        cancellation: CityCognitionCancellation,
    ) -> PreparedCityRun: ...

    def resolve_cognition_v2(
        self,
        prepared: PreparedCityScientificRunV2,
        operation: WorkbenchCognitionOperationV2,
        cancellation: CityCognitionCancellation,
        *,
        progress: Callable[[], None],
    ) -> CityCognitionEvaluationV2: ...

    def finalize_cognition_v2(
        self,
        prepared: PreparedCityScientificRunV2,
        evaluation: CityCognitionEvaluationV2,
    ) -> PreparedCityRun: ...

    def publish(self, prepared: PreparedCityRun) -> StoredCityRun: ...

    def verified_summary(self, run_id: str) -> WorkbenchRunSummary: ...


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    run_id: str
    accepted_settings: WorkbenchAcceptedSettings
    validated: ValidatedWorkbenchRun
    phase: WorkbenchJobPhase
    created_at: str
    updated_at: str
    cancellation_requested: bool = False
    error: WorkbenchJobFailure | None = None
    result: WorkbenchRunSummary | None = None
    cognition_progress: WorkbenchCognitionProgressCounter | None = None


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _new_worker_executor() -> Executor:
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="adlife-city-job")


def _failure(
    phase: Literal[
        "evaluating",
        "simulating",
        "selecting-explanations",
        "requesting-explanations",
        "planning-cognition",
        "requesting-cognition",
        "applying-cognition",
        "persisting",
        "verifying",
    ],
) -> WorkbenchJobFailure:
    if phase in {
        "evaluating",
        "simulating",
        "selecting-explanations",
        "requesting-explanations",
        "planning-cognition",
        "requesting-cognition",
        "applying-cognition",
    }:
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

    def __init__(
        self,
        repository: _RunOperations,
        *,
        operations: WorkbenchOperationState | None = None,
    ) -> None:
        self._repository = repository
        self._executor = _new_worker_executor()
        self._operations = operations if operations is not None else WorkbenchOperationState()
        self._lock = self._operations.lock
        self._close_lock = Lock()
        self._records: dict[str, _JobRecord] = {}
        self._terminal_ids: deque[str] = deque()
        self._active_job_id: str | None = None
        self._operation_leases: dict[str, WorkbenchOperationLease] = {}
        self._cognition_operations: dict[
            str, WorkbenchCognitionOperation | WorkbenchCognitionOperationV2
        ] = {}
        self._cancellations: dict[str, CityCognitionCancellation] = {}
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
            accepted_settings=record.accepted_settings,
            phase=record.phase,
            created_at=record.created_at,
            updated_at=record.updated_at,
            cancellation_requested=record.cancellation_requested,
            can_cancel=(record.phase in _CANCELLABLE_PHASES and not record.cancellation_requested),
            error=record.error,
            result=record.result,
            cognition_progress=(
                None if record.cognition_progress is None else record.cognition_progress.snapshot()
            ),
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
        operation = self._cognition_operations.pop(record.job_id, None)
        if operation is not None:
            operation.clear()
        self._cancellations.pop(record.job_id, None)
        lease = self._operation_leases.pop(record.job_id, None)
        if lease is not None:
            self._operations.finish(lease)
        self._terminal_ids.append(record.job_id)
        while len(self._terminal_ids) > MAX_TERMINAL_JOBS:
            expired = self._terminal_ids.popleft()
            self._records.pop(expired, None)

    def _rollback_submission_locked(
        self,
        job_id: str | None,
        operation: WorkbenchCognitionOperation | WorkbenchCognitionOperationV2 | None,
        lease: WorkbenchOperationLease | None,
    ) -> None:
        mapped_operation: WorkbenchCognitionOperation | WorkbenchCognitionOperationV2 | None = None
        mapped_lease: WorkbenchOperationLease | None = None
        if job_id is not None:
            self._records.pop(job_id, None)
            mapped_operation = self._cognition_operations.pop(job_id, None)
            self._cancellations.pop(job_id, None)
            if self._active_job_id == job_id:
                self._active_job_id = None
            mapped_lease = self._operation_leases.pop(job_id, None)
        credential_owner = mapped_operation if mapped_operation is not None else operation
        if credential_owner is not None:
            credential_owner.clear()
        if mapped_lease is not None:
            self._operations.finish(mapped_lease)
            lease = None
        if lease is not None:
            self._operations.finish(lease)

    def submit(self, validated: ValidatedWorkbenchRun) -> WorkbenchJobView:
        """Preflight and schedule one frozen request without waiting for evaluation."""
        if not isinstance(validated, ValidatedWorkbenchRun):
            raise TypeError("validated must be a ValidatedWorkbenchRun")
        workbench_input = validated.workbench_input
        run_id = workbench_input.draft.settings.run_id
        draft = workbench_input.draft
        cognition_mode = None
        provider_profile_id = None
        provider_revision_sha256 = None
        aggregate_output_token_ceiling = None
        effect_scope = None
        funded_response_limit = None
        if type(workbench_input) is WorkbenchRunInputV4:
            cognition_v4 = workbench_input.draft.cognition
            cognition_mode = cognition_v4.mode
            provider_profile_id = cognition_v4.provider_profile_id
            provider_revision_sha256 = cognition_v4.provider_revision_sha256
            aggregate_output_token_ceiling = cognition_v4.aggregate_output_token_ceiling
            effect_scope = cognition_v4.effect_scope
            funded_response_limit = cognition_v4.funded_response_limit
        elif type(workbench_input) is WorkbenchRunInputV3:
            cognition_v3 = workbench_input.draft.cognition
            cognition_mode = cognition_v3.mode
            provider_profile_id = cognition_v3.provider_profile_id
            provider_revision_sha256 = cognition_v3.provider_revision_sha256
            aggregate_output_token_ceiling = cognition_v3.aggregate_output_token_ceiling
        input_schema_version = workbench_input.schema_version
        run_schema_version: Literal[7, 8, 9, 10]
        if input_schema_version == 1:
            run_schema_version = 7
        elif input_schema_version == 2:
            run_schema_version = 8
        elif input_schema_version == 3:
            run_schema_version = 9
        else:
            run_schema_version = 10
        accepted_settings = WorkbenchAcceptedSettings(
            workbench_input_schema_version=input_schema_version,
            run_schema_version=run_schema_version,
            city_id=draft.city_id,
            scenario_id=draft.scenario.scenario_id,
            campaign_id=draft.scenario.campaign.campaign_id,
            seed=str(draft.settings.seed),
            agent_count=draft.settings.agent_count,
            days=draft.settings.days,
            response_mode=draft.settings.response_mode,
            effect_scope=effect_scope,
            cognition_mode=cognition_mode,
            provider_profile_id=provider_profile_id,
            provider_revision_sha256=provider_revision_sha256,
            funded_response_limit=funded_response_limit,
            aggregate_output_token_ceiling=aggregate_output_token_ceiling,
        )
        with self._lock:
            if self._closed:
                raise WorkbenchJobManagerClosed("The workbench worker is closed.")
            if self._active_job_id is not None:
                raise WorkbenchJobBusy("Another workbench run is already active.")
            operation: WorkbenchCognitionOperation | WorkbenchCognitionOperationV2 | None = None
            lease: WorkbenchOperationLease | None = None
            job_id: str | None = None
            try:
                try:
                    lease = self._operations.begin_job()
                except WorkbenchProviderBusy:
                    raise WorkbenchJobBusy(
                        "Another workbench operation is already active."
                    ) from None
                except WorkbenchProviderClosed:
                    raise WorkbenchJobManagerClosed("The workbench worker is closed.") from None
                if type(workbench_input) is WorkbenchRunInputV4:
                    operation = self._repository.preflight_cognition_v2(validated)
                elif type(workbench_input) is WorkbenchRunInputV3:
                    operation = self._repository.preflight_cognition(validated)
                self._repository.ensure_available(run_id)
                job_id = self._new_job_id_locked()
                created_at = _utc_now()
                record = _JobRecord(
                    job_id=job_id,
                    run_id=run_id,
                    accepted_settings=accepted_settings,
                    validated=validated,
                    phase="queued",
                    created_at=created_at,
                    updated_at=created_at,
                )
                self._records[job_id] = record
                self._operation_leases[job_id] = lease
                lease = None
                if operation is not None:
                    self._cognition_operations[job_id] = operation
                    self._cancellations[job_id] = CityCognitionCancellation()
                self._active_job_id = job_id
                queued = self._view(record)
            except BaseException:
                self._rollback_submission_locked(job_id, operation, lease)
                raise
            try:
                self._executor.submit(self._run_job, job_id)
            except BaseException as error:
                self._rollback_submission_locked(job_id, operation, lease)
                if isinstance(error, Exception):
                    raise WorkbenchJobSubmissionError(
                        "The workbench worker could not accept the job."
                    ) from None
                raise
            return queued

    def _cancel_before_publication_locked(self, record: _JobRecord) -> bool:
        if not record.cancellation_requested:
            return False
        self._terminal_locked(record, "cancelled")
        return True

    def _fail(
        self,
        job_id: str,
        phase: Literal[
            "evaluating",
            "simulating",
            "selecting-explanations",
            "requesting-explanations",
            "planning-cognition",
            "requesting-cognition",
            "applying-cognition",
            "persisting",
            "verifying",
        ],
    ) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.phase in _TERMINAL_PHASES:
                return
            if phase in _CANCELLABLE_PHASES and record.cancellation_requested:
                self._terminal_locked(record, "cancelled")
                return
            self._terminal_locked(record, "failed", error=_failure(phase))

    def _run_job(self, job_id: str) -> None:
        try:
            self._run_job_checked(job_id)
        except BaseException:
            self._fail_after_hard_worker_interruption(job_id)
            raise

    def _fail_after_hard_worker_interruption(self, job_id: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.phase in _TERMINAL_PHASES:
                return
            if record.phase in _CANCELLABLE_PHASES and record.cancellation_requested:
                self._terminal_locked(record, "cancelled")
                return
            phase: Literal[
                "evaluating",
                "simulating",
                "selecting-explanations",
                "requesting-explanations",
                "planning-cognition",
                "requesting-cognition",
                "applying-cognition",
                "persisting",
                "verifying",
            ]
            match record.phase:
                case "queued":
                    if type(record.validated.workbench_input) is WorkbenchRunInputV4:
                        phase = "planning-cognition"
                    elif type(record.validated.workbench_input) is WorkbenchRunInputV3:
                        phase = "simulating"
                    else:
                        phase = "evaluating"
                case "evaluating":
                    phase = "evaluating"
                case "simulating":
                    phase = "simulating"
                case "selecting-explanations":
                    phase = "selecting-explanations"
                case "requesting-explanations":
                    phase = "requesting-explanations"
                case "planning-cognition":
                    phase = "planning-cognition"
                case "requesting-cognition":
                    phase = "requesting-cognition"
                case "applying-cognition":
                    phase = "applying-cognition"
                case "persisting":
                    phase = "persisting"
                case "verifying":
                    phase = "verifying"
                case _:
                    return
            self._terminal_locked(record, "failed", error=_failure(phase))

    def _run_job_checked(self, job_id: str) -> None:
        with self._lock:
            record = self._records[job_id]
            if type(record.validated.workbench_input) is WorkbenchRunInputV4:
                is_v10 = True
            else:
                is_v10 = False
        if is_v10:
            self._run_v10_checked(job_id)
            return

        with self._lock:
            record = self._records[job_id]
            if self._cancel_before_publication_locked(record):
                return
            validated = record.validated
            operation = self._cognition_operations.get(job_id)
            cancellation = self._cancellations.get(job_id)
            self._transition_locked(
                record,
                "simulating" if operation is not None else "evaluating",
            )

        if operation is None:
            try:
                prepared = self._repository.prepare(validated)
            except Exception:
                self._fail(job_id, "evaluating")
                return
        else:
            operation_v1 = cast(WorkbenchCognitionOperation, operation)
            assert cancellation is not None
            try:
                scientific = self._repository.prepare_scientific(validated, operation_v1)
            except Exception:
                operation_v1.clear()
                self._fail(job_id, "simulating")
                return

            with self._lock:
                record = self._records[job_id]
                if self._cancel_before_publication_locked(record):
                    operation_v1.clear()
                    self._cognition_operations.pop(job_id, None)
                    self._cancellations.pop(job_id, None)
                    return
                self._transition_locked(record, "selecting-explanations")
            try:
                self._repository.select_explanations(scientific, operation_v1)
            except Exception:
                operation_v1.clear()
                self._fail(job_id, "selecting-explanations")
                return

            with self._lock:
                record = self._records[job_id]
                if self._cancel_before_publication_locked(record):
                    operation_v1.clear()
                    self._cognition_operations.pop(job_id, None)
                    self._cancellations.pop(job_id, None)
                    return
                self._transition_locked(record, "requesting-explanations")
            try:
                prepared = self._repository.resolve_cognition(
                    scientific,
                    operation_v1,
                    cancellation,
                )
            except asyncio.CancelledError:
                operation_v1.clear()
                with self._lock:
                    record = self._records[job_id]
                    if record.cancellation_requested:
                        self._terminal_locked(record, "cancelled")
                    else:
                        self._terminal_locked(
                            record,
                            "failed",
                            error=_failure("requesting-explanations"),
                        )
                return
            except Exception:
                operation_v1.clear()
                self._fail(job_id, "requesting-explanations")
                return
            finally:
                operation_v1.clear()
                with self._lock:
                    self._cognition_operations.pop(job_id, None)
                    self._cancellations.pop(job_id, None)

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
        if (
            not isinstance(summary, WorkbenchRunSummary)
            or summary.run_id != run_id
            or not _summary_matches_accepted_settings(summary, record.accepted_settings)
        ):
            self._fail(job_id, "verifying")
            return

        with self._lock:
            record = self._records[job_id]
            self._terminal_locked(record, "completed", result=summary)

    def _run_v10_checked(self, job_id: str) -> None:
        with self._lock:
            record = self._records[job_id]
            if self._cancel_before_publication_locked(record):
                return
            validated = record.validated
            operation = self._cognition_operations.get(job_id)
            cancellation = self._cancellations.get(job_id)
            if not isinstance(operation, WorkbenchCognitionOperationV2) or cancellation is None:
                raise TypeError("schema-4 job is missing its private V2 operation")
            self._transition_locked(record, "planning-cognition")

        try:
            scientific = self._repository.prepare_scientific_v2(validated, operation)
            # Planning froze the funding: the count of funded provider requests is final.
            progress = WorkbenchCognitionProgressCounter(
                scientific.funded_provider_request_count,
                concurrency_limit=scientific.cognition_input.concurrency_limit,
            )
        except Exception:
            self._fail(job_id, "planning-cognition")
            return

        with self._lock:
            record = self._records[job_id]
            if self._cancel_before_publication_locked(record):
                return
            record.cognition_progress = progress
            self._transition_locked(record, "requesting-cognition")
        try:
            evaluation = self._repository.resolve_cognition_v2(
                scientific,
                operation,
                cancellation,
                progress=progress.advance,
            )
        except asyncio.CancelledError:
            with self._lock:
                record = self._records[job_id]
                if record.cancellation_requested:
                    self._terminal_locked(record, "cancelled")
                else:
                    self._terminal_locked(
                        record,
                        "failed",
                        error=_failure("requesting-cognition"),
                    )
            return
        except Exception:
            self._fail(job_id, "requesting-cognition")
            return
        finally:
            operation.clear()
        # Complete validated evidence holds one terminal outcome for every funded request.
        progress.complete()

        with self._lock:
            record = self._records[job_id]
            if self._cancel_before_publication_locked(record):
                return
            self._transition_locked(record, "applying-cognition")
        try:
            prepared = self._repository.finalize_cognition_v2(scientific, evaluation)
        except Exception:
            self._fail(job_id, "applying-cognition")
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
        if (
            not isinstance(summary, WorkbenchRunSummary)
            or summary.run_id != run_id
            or not _summary_matches_accepted_settings(summary, record.accepted_settings)
        ):
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
            cancellation = self._cancellations.get(record.job_id)
            view = self._view(record)
        if cancellation is not None:
            cancellation.cancel()
        return view

    def close(self) -> None:
        """Stop acceptance, cancel active cognition, and drain the owned worker."""
        with self._close_lock:
            cancellation: CityCognitionCancellation | None = None
            self._operations.close()
            with self._lock:
                self._closed = True
                if self._active_job_id is not None:
                    record = self._records[self._active_job_id]
                    cancellation = self._cancellations.get(record.job_id)
                    if cancellation is not None and record.phase in _CANCELLABLE_PHASES:
                        record.cancellation_requested = True
                        self._touch(record)
            if cancellation is not None:
                cancellation.cancel()
            self._executor.shutdown(wait=True, cancel_futures=False)


__all__ = [
    "MAX_COGNITION_PROGRESS_REQUESTS",
    "MAX_TERMINAL_JOBS",
    "CityJobManager",
    "WorkbenchAcceptedSettings",
    "WorkbenchCognitionProgress",
    "WorkbenchCognitionProgressCounter",
    "WorkbenchJobBusy",
    "WorkbenchJobCancellationConflict",
    "WorkbenchJobFailure",
    "WorkbenchJobManagerClosed",
    "WorkbenchJobNotFound",
    "WorkbenchJobPhase",
    "WorkbenchJobSubmissionError",
    "WorkbenchJobView",
]
