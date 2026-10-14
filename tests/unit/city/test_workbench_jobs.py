"""The process-local workbench worker has one truthful, bounded state machine."""

from __future__ import annotations

import importlib
import json
import re
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_run_preparation import _validated

TERMINAL = {"completed", "failed", "cancelled"}


def _modules():
    return (
        importlib.import_module("adlife.city.workbench_jobs"),
        importlib.import_module("adlife.city.workbench_runs"),
    )


def test_browser_schema2_job_keeps_frozen_personas_after_caller_edits(tmp_path: Path) -> None:
    from adlife.city.workbench_transport import parse_workbench_http_draft
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_web import _persona_draft

    jobs, runs = _modules()
    repository = runs.WorkbenchRunRepository(prepare_workbench_workspace(tmp_path / "state"))
    payload = _persona_draft()
    validated = construct_workbench_run(parse_workbench_http_draft(payload))
    manager = jobs.CityJobManager(repository)
    try:
        submitted = manager.submit(validated)
        payload["population"]["personas"][0]["display_name"] = "Changed after submission"
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "completed"
        stored = repository.load(submitted.run_id)
        assert stored.workbench_input.fingerprint == validated.workbench_input.fingerprint
        assert stored.workbench_input.draft.population.personas[1].display_name == "Fictional Mina"
        assert terminal.accepted_settings.seed == "9223372036854775807"
    finally:
        manager.close()


def _await_terminal(manager: Any, job_id: str, *, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    while time.monotonic() < deadline:
        view = manager.get(job_id)
        if view.phase in TERMINAL:
            return view
        pause.wait(0.005)
    raise AssertionError(f"job {job_id} did not terminate")


class _GatedRepository:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.evaluate_entered = threading.Event()
        self.persist_entered = threading.Event()
        self.verify_entered = threading.Event()
        self.allow_evaluate = threading.Event()
        self.allow_persist = threading.Event()
        self.allow_verify = threading.Event()

    def ensure_available(self, run_id: str) -> None:
        self.real.ensure_available(run_id)

    def prepare(self, validated: Any):
        self.evaluate_entered.set()
        if not self.allow_evaluate.wait(10):
            raise RuntimeError("evaluation gate timed out")
        return self.real.prepare(validated)

    def publish(self, prepared: Any):
        self.persist_entered.set()
        if not self.allow_persist.wait(10):
            raise RuntimeError("persistence gate timed out")
        return self.real.publish(prepared)

    def verified_summary(self, run_id: str):
        self.verify_entered.set()
        if not self.allow_verify.wait(10):
            raise RuntimeError("verification gate timed out")
        return self.real.verified_summary(run_id)


def test_accepted_job_keeps_the_exact_city_after_registry_mutation(tmp_path: Path) -> None:
    from adlife.city.workbench_city_registry import WorkspaceCityRegistry
    from adlife.city.workbench_input import WorkbenchRunDraft
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_city_registry import _document
    from tests.unit.city.test_workbench_input import draft_data

    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    registry = WorkspaceCityRegistry(workspace)
    selected_pack = registry.register(
        _document("operator-city"),
        forbidden_city_ids=frozenset({"fictional-grid-v2"}),
    )
    payload = draft_data()
    payload["city_id"] = selected_pack.city_id
    payload["settings"].update({"run_id": "frozen-city-job", "agent_count": 1, "days": 1})
    payload["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 480, "end_minute": 540}
    ]
    draft = WorkbenchRunDraft.model_validate_json(json.dumps(payload))
    validated = construct_workbench_run(draft, selected_pack=selected_pack)
    real_repository = runs.WorkbenchRunRepository(workspace)
    gated = _GatedRepository(real_repository)
    manager = jobs.CityJobManager(gated)
    try:
        submitted = manager.submit(validated)
        assert gated.evaluate_entered.wait(5)
        (workspace.root / "city-packs" / "operator-city.json").write_bytes(b"corrupt\n")

        gated.allow_evaluate.set()
        assert gated.persist_entered.wait(5)
        gated.allow_persist.set()
        assert gated.verify_entered.wait(5)
        gated.allow_verify.set()

        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "completed"
        stored = real_repository.load(submitted.run_id)
        assert stored.mobility.pack == selected_pack
        assert stored.mobility.pack.fingerprint == selected_pack.fingerprint
    finally:
        gated.allow_evaluate.set()
        gated.allow_persist.set()
        gated.allow_verify.set()
        manager.close()


class _DeferredExecutor:
    def __init__(self) -> None:
        self.call: tuple[Any, tuple[Any, ...], dict[str, Any]] | None = None
        self.future: Future[object] | None = None
        self.shutdown_called = False

    def submit(self, function: Any, /, *args: Any, **kwargs: Any) -> Future[object]:
        future: Future[object] = Future()
        self.call = (function, args, kwargs)
        self.future = future
        return future

    def run(self) -> None:
        assert self.call is not None and self.future is not None
        function, args, kwargs = self.call
        try:
            self.future.set_result(function(*args, **kwargs))
        except BaseException as error:
            self.future.set_exception(error)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_called = True


def test_schema3_job_refuses_before_run_reservation_or_worker_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_validation import cognition_draft

    jobs, _ = _modules()
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)

    class RefusingRepository:
        ensure_calls = 0

        def ensure_available(self, run_id: str) -> None:
            self.ensure_calls += 1
            raise AssertionError("schema-3 input reached run reservation")

        def prepare(self, validated: Any) -> object:
            raise AssertionError("schema-3 input reached evaluation")

        def publish(self, prepared: object) -> object:
            raise AssertionError("schema-3 input reached publication")

        def verified_summary(self, run_id: str) -> object:
            raise AssertionError("schema-3 input reached verification")

    repository = RefusingRepository()
    manager = jobs.CityJobManager(repository)
    try:
        with pytest.raises(jobs.WorkbenchJobSubmissionError, match=r"schema 3.*schema 9"):
            manager.submit(construct_workbench_run(cognition_draft()))
        assert repository.ensure_calls == 0
        assert deferred.call is None
        assert manager.active_job() is None
        assert manager.recent_jobs() == ()
    finally:
        manager.close()


@pytest.mark.parametrize("wrong_version", [False, True])
def test_schema2_jobs_retain_and_verify_the_exact_accepted_schema_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrong_version: bool,
) -> None:
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_validation import persona_draft

    jobs, runs = _modules()
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = runs.WorkbenchRunRepository(workspace)
    if wrong_version:
        original = repository.verified_summary

        def wrong_summary(run_id):
            return original(run_id).model_copy(update={"run_schema_version": 7})

        monkeypatch.setattr(repository, "verified_summary", wrong_summary)
    manager = jobs.CityJobManager(repository)
    try:
        submitted = manager.submit(construct_workbench_run(persona_draft()))
        assert submitted.accepted_settings.workbench_input_schema_version == 2
        assert submitted.accepted_settings.run_schema_version == 8
        deferred.run()
        terminal = _await_terminal(manager, submitted.job_id)
        if wrong_version:
            assert terminal.phase == "failed"
            assert terminal.error.code == "verification-failed"
            assert terminal.result is None
        else:
            assert terminal.phase == "completed"
            assert terminal.result.run_schema_version == 8
    finally:
        manager.close()


@pytest.mark.parametrize("input_version,run_version", [(1, 8), (2, 7), (True, 7), (2.0, 8)])
def test_accepted_settings_refuse_crossed_or_coerced_schema_pairs(
    input_version, run_version
) -> None:
    jobs, _ = _modules()
    with pytest.raises(ValidationError):
        jobs.WorkbenchAcceptedSettings(
            city_id="fictional-grid-v2",
            scenario_id="launch-study",
            campaign_id="fictional-launch",
            seed="42",
            agent_count=3,
            days=2,
            response_mode="deterministic-rules",
            workbench_input_schema_version=input_version,
            run_schema_version=run_version,
        )


def test_every_job_view_carries_the_exact_frozen_accepted_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)
    manager = jobs.CityJobManager(runs.WorkbenchRunRepository(workspace))
    validated = _validated(
        run_id="accepted-settings",
        seed=2**63 - 1,
        agents=30,
        days=7,
    )

    submitted = manager.submit(validated)
    try:
        expected = {
            "schema_version": 1,
            "workbench_input_schema_version": 1,
            "run_schema_version": 7,
            "city_id": "fictional-grid-v2",
            "scenario_id": "launch-study",
            "campaign_id": "fictional-launch",
            "seed": "9223372036854775807",
            "agent_count": 30,
            "days": 7,
            "response_mode": "deterministic-rules",
        }
        assert submitted.model_dump(mode="json")["accepted_settings"] == expected
        assert (
            manager.get(submitted.job_id).model_dump(mode="json")["accepted_settings"] == expected
        )
        with pytest.raises(ValidationError, match="frozen"):
            submitted.accepted_settings.seed = "0"
    finally:
        manager.cancel(submitted.job_id)
        deferred.run()
        _await_terminal(manager, submitted.job_id)
        manager.close()


def test_success_exposes_each_truthful_phase_and_only_verified_result(tmp_path: Path) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedRepository(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    validated = _validated(run_id="phase-run", agents=1, days=2)
    try:
        queued = manager.submit(validated)
        assert queued.phase == "queued"
        assert queued.result is None
        assert gated.evaluate_entered.wait(5)
        assert manager.get(queued.job_id).phase == "evaluating"

        gated.allow_evaluate.set()
        assert gated.persist_entered.wait(10)
        assert manager.get(queued.job_id).phase == "persisting"

        gated.allow_persist.set()
        assert gated.verify_entered.wait(10)
        assert manager.get(queued.job_id).phase == "verifying"

        gated.allow_verify.set()
        completed = _await_terminal(manager, queued.job_id)
        assert completed.phase == "completed"
        assert completed.result is not None
        assert completed.result.run_id == "phase-run"
        assert completed.error is None
        assert completed.can_cancel is False
        with pytest.raises(ValidationError, match="run identifier"):
            completed.model_copy(update={"run_id": "different-run"})
        assert manager.active_job() is None
        assert runs.WorkbenchRunRepository(workspace).load("phase-run").manifest.schema_version == 7
    finally:
        gated.allow_evaluate.set()
        gated.allow_persist.set()
        gated.allow_verify.set()
        manager.close()


def test_queued_and_evaluating_cancellation_publish_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")

    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)
    queued_manager = jobs.CityJobManager(runs.WorkbenchRunRepository(workspace))
    queued = queued_manager.submit(_validated(run_id="queued-cancel", agents=1, days=2))
    requested = queued_manager.cancel(queued.job_id)
    assert requested.phase == "queued"
    assert requested.cancellation_requested is True
    assert requested.can_cancel is False
    deferred.run()
    cancelled = _await_terminal(queued_manager, queued.job_id)
    assert cancelled.phase == "cancelled"
    assert not (workspace.root / "city-runs" / "queued-cancel").exists()
    queued_manager.close()

    monkeypatch.undo()
    gated = _GatedRepository(runs.WorkbenchRunRepository(workspace))
    evaluating_manager = jobs.CityJobManager(gated)
    active = evaluating_manager.submit(_validated(run_id="evaluating-cancel", agents=1, days=2))
    try:
        assert gated.evaluate_entered.wait(5)
        requested = evaluating_manager.cancel(active.job_id)
        assert requested.phase == "evaluating"
        assert requested.cancellation_requested is True
        gated.allow_evaluate.set()
        cancelled = _await_terminal(evaluating_manager, active.job_id)
        assert cancelled.phase == "cancelled"
        assert not (workspace.root / "city-runs" / "evaluating-cancel").exists()
        assert not gated.persist_entered.is_set()
    finally:
        gated.allow_evaluate.set()
        evaluating_manager.close()


def test_accepted_evaluating_cancellation_wins_over_a_later_evaluation_fault(
    tmp_path: Path,
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    real = runs.WorkbenchRunRepository(workspace)
    entered = threading.Event()
    release = threading.Event()

    class FaultAfterCancellationRepository:
        def ensure_available(self, run_id: str) -> None:
            real.ensure_available(run_id)

        def prepare(self, validated: Any) -> object:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("evaluation gate timed out")
            raise RuntimeError("secret evaluation failure")

        def publish(self, prepared: object) -> None:
            raise AssertionError("cancelled evaluation must not publish")

        def verified_summary(self, run_id: str) -> None:
            raise AssertionError("cancelled evaluation must not verify")

    manager = jobs.CityJobManager(FaultAfterCancellationRepository())
    submitted = manager.submit(_validated(run_id="cancelled-fault", agents=1, days=2))
    try:
        assert entered.wait(5)
        assert manager.cancel(submitted.job_id).cancellation_requested is True
        release.set()
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "cancelled"
        assert terminal.error is None
        assert not (workspace.root / "city-runs" / "cancelled-fault").exists()
    finally:
        release.set()
        manager.close()


def test_one_active_job_and_late_cancellation_are_stable_conflicts(tmp_path: Path) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedRepository(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    first = manager.submit(_validated(run_id="first-active", agents=1, days=2))
    try:
        assert gated.evaluate_entered.wait(5)
        with pytest.raises(jobs.WorkbenchJobBusy):
            manager.submit(_validated(run_id="second-active", agents=1, days=2))
        assert not (workspace.root / "city-runs" / "second-active").exists()

        gated.allow_evaluate.set()
        assert gated.persist_entered.wait(10)
        with pytest.raises(jobs.WorkbenchJobCancellationConflict):
            manager.cancel(first.job_id)
        gated.allow_persist.set()
        assert gated.verify_entered.wait(10)
        with pytest.raises(jobs.WorkbenchJobCancellationConflict):
            manager.cancel(first.job_id)
        gated.allow_verify.set()
        assert _await_terminal(manager, first.job_id).phase == "completed"
    finally:
        gated.allow_evaluate.set()
        gated.allow_persist.set()
        gated.allow_verify.set()
        manager.close()


@pytest.mark.parametrize(
    ("stage", "code", "message"),
    [
        ("evaluating", "evaluation-failed", "The deterministic city evaluation failed."),
        ("persisting", "persistence-failed", "The city run could not be persisted."),
        ("verifying", "verification-failed", "The persisted city run could not be verified."),
        (
            "verifying-mismatch",
            "verification-failed",
            "The persisted city run could not be verified.",
        ),
        (
            "verifying-settings-mismatch",
            "verification-failed",
            "The persisted city run could not be verified.",
        ),
    ],
)
def test_failures_are_terminal_bounded_and_never_expose_exception_text(
    tmp_path: Path, stage: str, code: str, message: str
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / stage)
    real = runs.WorkbenchRunRepository(workspace)
    secret = "sk-live-never-leak-this-value"

    class FailingRepository:
        def ensure_available(self, run_id: str) -> None:
            real.ensure_available(run_id)

        def prepare(self, validated: Any):
            if stage == "evaluating":
                raise RuntimeError(secret)
            return real.prepare(validated)

        def publish(self, prepared: Any):
            if stage == "persisting":
                raise RuntimeError(secret)
            return real.publish(prepared)

        def verified_summary(self, run_id: str):
            if stage == "verifying":
                raise RuntimeError(secret)
            summary = real.verified_summary(run_id)
            if stage == "verifying-mismatch":
                return summary.model_copy(
                    update={
                        "run_id": "different-run",
                        "inspector_url": "/runs/different-run",
                    }
                )
            if stage == "verifying-settings-mismatch":
                return summary.model_copy(update={"seed": "43"})
            return summary

    manager = jobs.CityJobManager(FailingRepository())
    submitted = manager.submit(_validated(run_id=f"fail-{stage}", agents=1, days=2))
    try:
        failed = _await_terminal(manager, submitted.job_id)
        assert failed.phase == "failed"
        assert failed.error is not None
        assert failed.error.model_dump(mode="json") == {"code": code, "message": message}
        assert failed.result is None
        assert secret not in canonical_json(failed)
        assert len(canonical_json(failed)) < 2_048
        assert manager.active_job() is None
    finally:
        manager.close()


def test_public_records_are_exact_frozen_bounded_and_use_utc_operational_times(
    tmp_path: Path,
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedRepository(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    submitted = manager.submit(_validated(run_id="record-run", agents=1, days=2))
    try:
        assert gated.evaluate_entered.wait(5)
        document = manager.get(submitted.job_id).model_dump(mode="json")
        assert set(document) == {
            "schema_version",
            "job_id",
            "run_id",
            "accepted_settings",
            "phase",
            "created_at",
            "updated_at",
            "cancellation_requested",
            "can_cancel",
            "error",
            "result",
        }
        assert re.fullmatch(r"job-[0-9a-f]{32}", document["job_id"])
        assert re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z",
            document["created_at"],
        )
        assert document["updated_at"] >= document["created_at"]
        inconsistent = dict(document)
        inconsistent.update(
            {
                "phase": "persisting",
                "cancellation_requested": True,
                "can_cancel": False,
            }
        )
        with pytest.raises(ValidationError, match="cancellation request"):
            jobs.WorkbenchJobView.model_validate(inconsistent)
        with pytest.raises(jobs.WorkbenchJobNotFound):
            manager.get("unknown")
        with pytest.raises(jobs.WorkbenchJobNotFound):
            manager.cancel("unknown")
        with pytest.raises(ValidationError, match="code and message"):
            jobs.WorkbenchJobFailure(
                code="evaluation-failed",
                message="The city run could not be persisted.",
            )
        with pytest.raises(ValidationError, match="frozen"):
            submitted.phase = "failed"
    finally:
        manager.cancel(submitted.job_id)
        gated.allow_evaluate.set()
        _await_terminal(manager, submitted.job_id)
        manager.close()


def test_terminal_history_is_bounded_to_32_most_recent_records() -> None:
    jobs, runs = _modules()

    class InstantRepository:
        def ensure_available(self, run_id: str) -> None:
            return None

        def prepare(self, validated: Any) -> object:
            return object()

        def publish(self, prepared: object) -> None:
            return None

        def verified_summary(self, run_id: str):
            return runs.WorkbenchRunSummary(
                run_id=run_id,
                run_schema_version=7,
                city_id="fictional-grid-v2",
                city_schema_version=2,
                city_sha256="1" * 64,
                scenario_id="launch-study",
                scenario_sha256="2" * 64,
                campaign_ids=("fictional-launch",),
                channels=("mobile-feed",),
                seed="42",
                days=2,
                agent_count=1,
                frame_count=2_880,
                opportunity_count=1,
                impression_count=1,
                noticed_count=1,
                response_count=1,
                inspector_url=f"/runs/{run_id}",
            )

    manager = jobs.CityJobManager(InstantRepository())
    submitted: list[Any] = []
    try:
        for index in range(35):
            view = manager.submit(_validated(run_id=f"history-{index:02d}", agents=1, days=2))
            submitted.append(view)
            assert _await_terminal(manager, view.job_id).phase == "completed"
        recent = manager.recent_jobs()
        assert len(recent) == 32
        assert [view.run_id for view in recent[:3]] == [
            "history-34",
            "history-33",
            "history-32",
        ]
        for evicted in submitted[:3]:
            with pytest.raises(jobs.WorkbenchJobNotFound):
                manager.get(evicted.job_id)
    finally:
        manager.close()


def test_executor_submission_failure_and_submit_close_race_leave_no_ghost_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = runs.WorkbenchRunRepository(workspace)
    entered = threading.Event()
    release = threading.Event()

    class RejectingExecutor:
        def submit(self, function: Any, /, *args: Any, **kwargs: Any) -> Future[object]:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("submit gate timed out")
            raise RuntimeError("executor rejected a secret-bearing task")

        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            return None

    monkeypatch.setattr(jobs, "_new_worker_executor", RejectingExecutor)
    manager = jobs.CityJobManager(repository)
    submit_errors: list[BaseException] = []
    close_started = threading.Event()
    close_finished = threading.Event()

    def submit() -> None:
        try:
            manager.submit(_validated(run_id="raced-submit", agents=1, days=2))
        except BaseException as error:
            submit_errors.append(error)

    submit_thread = threading.Thread(target=submit)
    submit_thread.start()
    assert entered.wait(5)

    def close() -> None:
        close_started.set()
        manager.close()
        close_finished.set()

    close_thread = threading.Thread(target=close)
    close_thread.start()
    assert close_started.wait(5)
    assert close_thread.is_alive()
    assert not close_finished.is_set()
    release.set()
    submit_thread.join(5)
    close_thread.join(5)

    assert len(submit_errors) == 1
    assert isinstance(submit_errors[0], jobs.WorkbenchJobSubmissionError)
    assert "secret" not in str(submit_errors[0]).lower()
    assert manager.active_job() is None
    assert manager.recent_jobs() == ()
    assert not (workspace.root / "city-runs" / "raced-submit").exists()
    with pytest.raises(jobs.WorkbenchJobManagerClosed):
        manager.submit(_validated(run_id="after-close", agents=1, days=2))


def test_close_drains_an_active_job_then_refuses_new_submissions(tmp_path: Path) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedRepository(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    submitted = manager.submit(_validated(run_id="drained-run", agents=1, days=2))
    assert gated.evaluate_entered.wait(5)
    close_started = threading.Event()
    closed = threading.Event()

    def close() -> None:
        close_started.set()
        manager.close()
        closed.set()

    closer = threading.Thread(target=close)
    closer.start()
    assert close_started.wait(5)
    assert closer.is_alive()
    assert not closed.is_set()

    gated.allow_evaluate.set()
    assert gated.persist_entered.wait(10)
    gated.allow_persist.set()
    assert gated.verify_entered.wait(10)
    gated.allow_verify.set()
    closer.join(10)

    assert closed.is_set()
    assert manager.get(submitted.job_id).phase == "completed"
    with pytest.raises(jobs.WorkbenchJobManagerClosed):
        manager.submit(_validated(run_id="refused-run", agents=1, days=2))
