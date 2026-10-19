"""The process-local workbench worker has one truthful, bounded state machine."""

from __future__ import annotations

import asyncio
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
from adlife.core.domain.city_cognition_v2 import CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_run_preparation import _validated

TERMINAL = {"completed", "failed", "cancelled"}


def test_schema4_provider_check_precedes_destination_and_job_reservation(
    tmp_path,
    monkeypatch,
):
    from adlife.city.provider_compatibility import (
        WorkbenchProviderCompatibilityRecord,
        WorkbenchProviderCompatibilityRegistry,
        build_workbench_provider_compatibility_key,
    )
    from adlife.city.provider_profiles import ProviderProfileRegistry
    from tests.unit.city.test_provider_profiles import _draft
    from tests.unit.city.test_workbench_transport_v4 import compiled_v4

    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    profiles = ProviderProfileRegistry(workspace)
    profile = profiles.create(_draft(profile_id="provider-main"))
    validated = compiled_v4(
        mode="local",
        limit=10,
        mutation=lambda payload: (
            payload["settings"].update(run_id="ordered-v10"),
            payload["cognition"].update(provider_revision_sha256=profile.revision_sha256),
        ),
    )
    registry = WorkbenchProviderCompatibilityRegistry()
    registry.record(
        WorkbenchProviderCompatibilityRecord(
            key=build_workbench_provider_compatibility_key(
                profile,
                effect_scope="bounded-signals-v1",
            ),
            status="compatible",
            reason_code=None,
        )
    )
    repository = runs.WorkbenchRunRepository(
        workspace,
        provider_registry=profiles,
        compatibility_registry=registry,
    )
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)
    events: list[str] = []
    original_require = registry.require_success
    original_bind = repository._endpoint_policy.bind
    original_resolve = repository._credential_store.resolve
    original_ensure = repository.ensure_available

    def require_success(key):
        events.append("compatibility")
        return original_require(key)

    class EndpointSpy:
        def bind(self, candidate):
            events.append("endpoint")
            assert events == ["compatibility", "endpoint"]
            return original_bind(candidate)

    class CredentialSpy:
        def resolve(self, candidate):
            events.append("credential")
            assert events == ["compatibility", "endpoint", "credential"]
            return original_resolve(candidate)

    def ensure(run_id):
        events.append("destination")
        assert events == ["compatibility", "endpoint", "credential", "destination"]
        return original_ensure(run_id)

    monkeypatch.setattr(registry, "require_success", require_success)
    monkeypatch.setattr(repository, "_endpoint_policy", EndpointSpy())
    monkeypatch.setattr(repository, "_credential_store", CredentialSpy())
    monkeypatch.setattr(repository, "ensure_available", ensure)
    manager = jobs.CityJobManager(repository)
    original_job_id = manager._new_job_id_locked

    def new_job_id():
        events.append("job-id")
        assert events[-2:] == ["destination", "job-id"]
        return original_job_id()

    monkeypatch.setattr(manager, "_new_job_id_locked", new_job_id)
    try:
        submitted = manager.submit(validated)
        events.append("scheduled")
        assert submitted.phase == "queued"
        assert events == [
            "compatibility",
            "endpoint",
            "credential",
            "destination",
            "job-id",
            "scheduled",
        ]
        assert deferred.call is not None
        manager.cancel(submitted.job_id)
        deferred.run()
        assert _await_terminal(manager, submitted.job_id).phase == "cancelled"
    finally:
        manager.close()


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


class _GatedCognitionRepository:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.simulate_entered = threading.Event()
        self.select_entered = threading.Event()
        self.request_entered = threading.Event()
        self.persist_entered = threading.Event()
        self.verify_entered = threading.Event()
        self.allow_simulate = threading.Event()
        self.allow_select = threading.Event()
        self.allow_request = threading.Event()
        self.allow_persist = threading.Event()
        self.allow_verify = threading.Event()

    def preflight_cognition(self, validated: Any):
        return self.real.preflight_cognition(validated)

    def ensure_available(self, run_id: str) -> None:
        self.real.ensure_available(run_id)

    def prepare_scientific(self, validated: Any, operation: Any):
        self.simulate_entered.set()
        if not self.allow_simulate.wait(10):
            raise RuntimeError("simulation gate timed out")
        return self.real.prepare_scientific(validated, operation)

    def select_explanations(self, prepared: Any, operation: Any) -> None:
        self.select_entered.set()
        if not self.allow_select.wait(10):
            raise RuntimeError("selection gate timed out")
        self.real.select_explanations(prepared, operation)

    def resolve_cognition(self, prepared: Any, operation: Any, cancellation: Any):
        self.request_entered.set()
        if not self.allow_request.wait(10):
            raise RuntimeError("cognition gate timed out")
        return self.real.resolve_cognition(prepared, operation, cancellation)

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


def _schema3_validated(*, run_id: str, mode: str = "rules"):
    from adlife.city.workbench_input import WorkbenchRunDraftV3
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_input import draft_v3_data

    payload = draft_v3_data(mode=mode, aggregate_output_token_ceiling=0)
    payload["settings"]["run_id"] = run_id
    return construct_workbench_run(WorkbenchRunDraftV3.model_validate_json(json.dumps(payload)))


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


def test_schema3_job_preflights_before_run_location_check_and_cleans_up_on_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_validation import cognition_draft

    jobs, _ = _modules()
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)

    events: list[str] = []

    class Operation:
        cleared = False

        def clear(self) -> None:
            self.cleared = True
            events.append("clear")

    operation = Operation()

    class RefusingRepository:
        ensure_calls = 0

        def preflight_cognition(self, validated: Any) -> Operation:
            events.append("preflight")
            return operation

        def ensure_available(self, run_id: str) -> None:
            self.ensure_calls += 1
            events.append("ensure")
            raise RuntimeError("existing run location")

        def prepare(self, validated: Any) -> object:
            raise AssertionError("schema-3 input reached evaluation")

        def publish(self, prepared: object) -> object:
            raise AssertionError("schema-3 input reached publication")

        def verified_summary(self, run_id: str) -> object:
            raise AssertionError("schema-3 input reached verification")

    repository = RefusingRepository()
    manager = jobs.CityJobManager(repository)
    try:
        with pytest.raises(RuntimeError, match="existing run location"):
            manager.submit(construct_workbench_run(cognition_draft()))
        assert repository.ensure_calls == 1
        assert events == ["preflight", "ensure", "clear"]
        assert operation.cleared is True
        assert deferred.call is None
        assert manager.active_job() is None
        assert manager.recent_jobs() == ()
    finally:
        manager.close()


def test_schema3_job_clears_preflight_credential_when_job_id_allocation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs, _ = _modules()
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)

    class Operation:
        cleared = False

        def clear(self) -> None:
            self.cleared = True

    operation = Operation()

    class Repository:
        def preflight_cognition(self, validated: Any) -> Operation:
            return operation

        def ensure_available(self, run_id: str) -> None:
            return None

    manager = jobs.CityJobManager(Repository())
    monkeypatch.setattr(
        manager,
        "_new_job_id_locked",
        lambda: (_ for _ in ()).throw(RuntimeError("job id allocation failed")),
    )
    try:
        with pytest.raises(RuntimeError, match="job id allocation failed"):
            manager.submit(_schema3_validated(run_id="allocation-failure"))
        assert operation.cleared is True
        assert deferred.call is None
        assert manager.active_job() is None
        assert manager.recent_jobs() == ()
    finally:
        manager.close()


@pytest.mark.parametrize("hard_failure_stage", ["simulation", "selection"])
def test_schema3_job_clears_credential_and_terminates_after_hard_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
    hard_failure_stage: str,
) -> None:
    jobs, _ = _modules()
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)

    class Operation:
        cleared = False

        def clear(self) -> None:
            self.cleared = True

    operation = Operation()

    class Repository:
        def preflight_cognition(self, validated: Any) -> Operation:
            return operation

        def ensure_available(self, run_id: str) -> None:
            return None

        def prepare_scientific(self, validated: Any, accepted: Operation) -> object:
            if hard_failure_stage == "simulation":
                raise KeyboardInterrupt("hard simulation interruption")
            return object()

        def select_explanations(self, prepared: object, accepted: Operation) -> None:
            raise KeyboardInterrupt("hard selection interruption")

    manager = jobs.CityJobManager(Repository())
    try:
        submitted = manager.submit(_schema3_validated(run_id=f"hard-{hard_failure_stage}"))
        deferred.run()
        assert deferred.future is not None
        assert isinstance(deferred.future.exception(), KeyboardInterrupt)
        terminal = manager.get(submitted.job_id)
        assert terminal.phase == "failed"
        assert terminal.error is not None
        assert terminal.error.code == "evaluation-failed"
        assert "interruption" not in terminal.error.message.lower()
        assert operation.cleared is True
        assert manager.active_job() is None
    finally:
        manager.close()


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_schema3_offline_job_publishes_verified_v9_with_frozen_cognition_settings(
    tmp_path: Path,
    mode: str,
) -> None:
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_input import draft_v3_data

    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / mode)
    repository = runs.WorkbenchRunRepository(workspace)
    payload = draft_v3_data(mode=mode, aggregate_output_token_ceiling=0)
    payload["settings"]["run_id"] = f"{mode}-v9"
    validated = construct_workbench_run(
        importlib.import_module(
            "adlife.city.workbench_input"
        ).WorkbenchRunDraftV3.model_validate_json(json.dumps(payload))
    )
    manager = jobs.CityJobManager(repository)
    try:
        submitted = manager.submit(validated)
        assert submitted.accepted_settings.workbench_input_schema_version == 3
        assert submitted.accepted_settings.run_schema_version == 9
        assert submitted.accepted_settings.cognition_mode == mode
        assert submitted.accepted_settings.provider_profile_id is None
        assert submitted.accepted_settings.provider_revision_sha256 is None
        assert submitted.accepted_settings.aggregate_output_token_ceiling == 0

        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "completed"
        assert terminal.result is not None
        assert terminal.result.run_schema_version == 9
        assert terminal.result.cognition_mode == mode
        assert terminal.result.provider_profile_id is None
        assert terminal.result.provider_revision_sha256 is None
        assert terminal.result.aggregate_output_token_ceiling == 0
        stored = repository.load(submitted.run_id)
        assert stored.manifest.schema_version == 9
        assert stored.cognition_input is not None
        assert stored.cognition_input.mode == mode
        assert stored.cognition_evaluation is not None
        assert len(stored.cognition_evaluation.records) == len(stored.cognition_input.reservations)
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


@pytest.mark.parametrize(
    "input_version,run_version",
    [
        (1, 8),
        (2, 7),
        (3, 8),
        (2, 9),
        (3, 10),
        (4, 9),
        (True, 7),
        (2.0, 8),
        (3, 9.0),
        (4, "10"),
    ],
)
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
            "cognition_mode": None,
            "provider_profile_id": None,
            "provider_revision_sha256": None,
            "aggregate_output_token_ceiling": None,
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


def test_schema3_success_exposes_simulation_selection_cognition_and_publication_phases(
    tmp_path: Path,
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedCognitionRepository(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    validated = _schema3_validated(run_id="phase-v9", mode="rules")
    try:
        queued = manager.submit(validated)
        assert queued.phase == "queued"
        assert gated.simulate_entered.wait(5)
        assert manager.get(queued.job_id).phase == "simulating"

        gated.allow_simulate.set()
        assert gated.select_entered.wait(10)
        assert manager.get(queued.job_id).phase == "selecting-explanations"

        gated.allow_select.set()
        assert gated.request_entered.wait(10)
        assert manager.get(queued.job_id).phase == "requesting-explanations"

        gated.allow_request.set()
        assert gated.persist_entered.wait(10)
        assert manager.get(queued.job_id).phase == "persisting"

        gated.allow_persist.set()
        assert gated.verify_entered.wait(10)
        assert manager.get(queued.job_id).phase == "verifying"

        gated.allow_verify.set()
        completed = _await_terminal(manager, queued.job_id)
        assert completed.phase == "completed"
        assert completed.result is not None
        assert completed.result.run_schema_version == 9
    finally:
        gated.allow_simulate.set()
        gated.allow_select.set()
        gated.allow_request.set()
        gated.allow_persist.set()
        gated.allow_verify.set()
        manager.close()


def test_schema3_cancellation_interrupts_cognition_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedCognitionRepository(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    submitted = manager.submit(_schema3_validated(run_id="cancel-v9", mode="rules"))
    try:
        gated.allow_simulate.set()
        gated.allow_select.set()
        assert gated.request_entered.wait(10)
        requested = manager.cancel(submitted.job_id)
        assert requested.phase == "requesting-explanations"
        assert requested.cancellation_requested is True
        gated.allow_request.set()
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "cancelled"
        assert terminal.error is None
        assert not (workspace.root / "city-runs" / "cancel-v9").exists()
        assert not gated.persist_entered.is_set()
    finally:
        gated.allow_simulate.set()
        gated.allow_select.set()
        gated.allow_request.set()
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


def test_close_cancels_an_active_schema3_provider_phase_before_executor_drain(
    tmp_path: Path,
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedCognitionRepository(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    submitted = manager.submit(_schema3_validated(run_id="shutdown-v9", mode="rules"))
    gated.allow_simulate.set()
    gated.allow_select.set()
    assert gated.request_entered.wait(10)

    closed = threading.Event()

    def close() -> None:
        manager.close()
        closed.set()

    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert not closed.wait(0.05)
        gated.allow_request.set()
        closer.join(10)
        assert closed.is_set()
        terminal = manager.get(submitted.job_id)
        assert terminal.phase == "cancelled"
        assert terminal.cancellation_requested is True
        assert not (workspace.root / "city-runs" / "shutdown-v9").exists()
    finally:
        gated.allow_request.set()
        closer.join(10)


def test_shared_provider_operation_refuses_job_before_repository_preflight(
    tmp_path: Path,
) -> None:
    jobs, runs = _modules()
    providers = importlib.import_module("adlife.city.workbench_providers")
    workspace = prepare_workbench_workspace(tmp_path / "state")

    class ObservedRepository(_GatedRepository):
        def __init__(self, real: Any) -> None:
            super().__init__(real)
            self.availability_checks = 0

        def ensure_available(self, run_id: str) -> None:
            self.availability_checks += 1
            super().ensure_available(run_id)

    repository = ObservedRepository(runs.WorkbenchRunRepository(workspace))
    operations = providers.WorkbenchOperationState()
    check_lease = operations.begin_check()
    manager = jobs.CityJobManager(repository, operations=operations)
    try:
        with pytest.raises(jobs.WorkbenchJobBusy):
            manager.submit(_validated(run_id="blocked-before-preflight", agents=1, days=2))
        assert repository.availability_checks == 0
        assert not (workspace.root / "city-runs" / "blocked-before-preflight").exists()
    finally:
        operations.finish(check_lease)
        manager.close()


def test_active_job_holds_shared_provider_lease_until_terminal_state(tmp_path: Path) -> None:
    jobs, runs = _modules()
    providers = importlib.import_module("adlife.city.workbench_providers")
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = _GatedRepository(runs.WorkbenchRunRepository(workspace))
    operations = providers.WorkbenchOperationState()
    manager = jobs.CityJobManager(repository, operations=operations)
    try:
        submitted = manager.submit(_validated(run_id="provider-lease", agents=1, days=2))
        assert repository.evaluate_entered.wait(5)
        with pytest.raises(providers.WorkbenchProviderBusy):
            operations.begin_check()
        with pytest.raises(providers.WorkbenchProviderBusy):
            operations.begin_credential()

        repository.allow_evaluate.set()
        assert repository.persist_entered.wait(10)
        repository.allow_persist.set()
        assert repository.verify_entered.wait(10)
        repository.allow_verify.set()
        assert _await_terminal(manager, submitted.job_id).phase == "completed"

        check_lease = operations.begin_check()
        operations.finish(check_lease)
    finally:
        repository.allow_evaluate.set()
        repository.allow_persist.set()
        repository.allow_verify.set()
        manager.close()


def test_failed_job_submission_releases_shared_provider_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, runs = _modules()
    providers = importlib.import_module("adlife.city.workbench_providers")
    workspace = prepare_workbench_workspace(tmp_path / "state")
    operations = providers.WorkbenchOperationState()

    class RejectingExecutor:
        def submit(self, function: Any, /, *args: Any, **kwargs: Any) -> Future[object]:
            raise RuntimeError("refused")

        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            return None

    monkeypatch.setattr(jobs, "_new_worker_executor", RejectingExecutor)
    manager = jobs.CityJobManager(
        runs.WorkbenchRunRepository(workspace),
        operations=operations,
    )
    try:
        with pytest.raises(jobs.WorkbenchJobSubmissionError):
            manager.submit(_validated(run_id="rejected-provider-lease", agents=1, days=2))
        check_lease = operations.begin_check()
        operations.finish(check_lease)
    finally:
        manager.close()


def test_job_manager_close_closes_shared_provider_admission(tmp_path: Path) -> None:
    jobs, runs = _modules()
    providers = importlib.import_module("adlife.city.workbench_providers")
    workspace = prepare_workbench_workspace(tmp_path / "state")
    operations = providers.WorkbenchOperationState()
    manager = jobs.CityJobManager(
        runs.WorkbenchRunRepository(workspace),
        operations=operations,
    )

    manager.close()

    with pytest.raises(providers.WorkbenchProviderClosed):
        operations.begin_check()
    with pytest.raises(jobs.WorkbenchJobManagerClosed):
        manager.submit(_validated(run_id="closed-provider-admission", agents=1, days=2))


async def test_job_manager_shutdown_cancels_an_active_provider_check(tmp_path: Path) -> None:
    from tests.unit.city.test_provider_profiles import _draft
    from tests.unit.city.test_workbench_providers import _controller, _Provider

    jobs, runs = _modules()

    def factory(**kwargs: object) -> _Provider:
        provider = _Provider(**kwargs)
        provider.release = asyncio.Event()
        return provider

    controller, _, providers = _controller(tmp_path, provider_factory=factory)
    local = controller.create(_draft())
    manager = jobs.CityJobManager(
        runs.WorkbenchRunRepository(controller.registry.workspace),
        operations=controller.operations,
    )
    task = asyncio.create_task(controller.check(local.profile_id, local.revision_sha256))
    while not providers:
        await asyncio.sleep(0)
    await providers[0].entered.wait()

    await asyncio.to_thread(manager.close)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert providers[0].closed
    assert providers[0].kwargs["credential"].secret.cleared
    assert controller.list().profiles[0].last_check is None


def _schema4_validated(*, run_id: str, mode: str = "rules", limit: int | None = None):
    from tests.unit.city.test_workbench_transport_v4 import compiled_v4

    return compiled_v4(
        mode=mode,
        limit=limit,
        mutation=lambda payload: (
            payload["settings"].update(
                run_id=run_id,
                agent_count=1,
                days=2,
            ),
            payload["population"]["personas"][0].update(count=1),
        ),
    )


class _GatedCognitionRepositoryV2:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.failure_phase: str | None = None
        self.failure_type: type[BaseException] = RuntimeError
        self.planning_entered = threading.Event()
        self.requesting_entered = threading.Event()
        self.applying_entered = threading.Event()
        self.persist_entered = threading.Event()
        self.verify_entered = threading.Event()
        self.allow_planning = threading.Event()
        self.allow_requesting = threading.Event()
        self.allow_applying = threading.Event()
        self.allow_persist = threading.Event()
        self.allow_verify = threading.Event()
        self.published_bytes: dict[str, bytes] = {}

    def preflight_cognition_v2(self, validated: Any):
        return self.real.preflight_cognition_v2(validated)

    def preflight_cognition(self, validated: Any):
        raise AssertionError("schema-4 input reached the legacy preflight")

    def ensure_available(self, run_id: str) -> None:
        self.real.ensure_available(run_id)

    def prepare(self, validated: Any):
        raise AssertionError("schema-4 input reached legacy preparation")

    def prepare_scientific_v2(self, validated: Any, operation: Any):
        self.planning_entered.set()
        if not self.allow_planning.wait(10):
            raise RuntimeError("V2 planning gate timed out")
        if self.failure_phase == "planning-cognition":
            raise self.failure_type("private V2 planning defect")
        return self.real.prepare_scientific_v2(validated, operation)

    def prepare_scientific(self, validated: Any, operation: Any):
        raise AssertionError("schema-4 input reached legacy scientific preparation")

    def resolve_cognition_v2(self, prepared: Any, operation: Any, cancellation: Any):
        self.requesting_entered.set()
        if not self.allow_requesting.wait(10):
            raise RuntimeError("V2 request gate timed out")
        if self.failure_phase == "requesting-cognition":
            raise self.failure_type("private V2 request defect")
        return self.real.resolve_cognition_v2(prepared, operation, cancellation)

    def resolve_cognition(self, prepared: Any, operation: Any, cancellation: Any):
        raise AssertionError("schema-4 input reached legacy cognition")

    def finalize_cognition_v2(self, prepared: Any, evaluation: Any):
        self.applying_entered.set()
        if not self.allow_applying.wait(10):
            raise RuntimeError("V2 application gate timed out")
        if self.failure_phase == "applying-cognition":
            raise self.failure_type("private V2 application defect")
        return self.real.finalize_cognition_v2(prepared, evaluation)

    def publish(self, prepared: Any):
        self.persist_entered.set()
        if not self.allow_persist.wait(10):
            raise RuntimeError("V10 persistence gate timed out")
        if self.failure_phase == "persisting":
            raise self.failure_type("private V10 persistence defect")
        stored = self.real.publish(prepared)
        self.published_bytes = {
            path.name: path.read_bytes() for path in stored.directory.iterdir() if path.is_file()
        }
        return stored

    def verified_summary(self, run_id: str):
        self.verify_entered.set()
        if not self.allow_verify.wait(10):
            raise RuntimeError("V10 verification gate timed out")
        if self.failure_phase == "verifying":
            raise self.failure_type("private V10 verification defect")
        return self.real.verified_summary(run_id)

    def release_all(self) -> None:
        self.allow_planning.set()
        self.allow_requesting.set()
        self.allow_applying.set()
        self.allow_persist.set()
        self.allow_verify.set()


def test_schema4_success_uses_only_v2_paths_and_exact_phase_order(tmp_path: Path) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedCognitionRepositoryV2(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    try:
        queued = manager.submit(_schema4_validated(run_id="phase-v10"))
        assert queued.phase == "queued"
        assert queued.accepted_settings.model_dump(mode="json") == {
            "schema_version": 1,
            "workbench_input_schema_version": 4,
            "run_schema_version": 10,
            "city_id": "fictional-grid-v2",
            "scenario_id": "launch-study",
            "campaign_id": "fictional-launch",
            "seed": "42",
            "agent_count": 1,
            "days": 2,
            "response_mode": "deterministic-rules",
            "effect_scope": "bounded-signals-v1",
            "cognition_mode": "rules",
            "provider_profile_id": None,
            "provider_revision_sha256": None,
            "funded_response_limit": 60,
            "aggregate_output_token_ceiling": 0,
        }

        assert gated.planning_entered.wait(5)
        assert manager.get(queued.job_id).phase == "planning-cognition"
        gated.allow_planning.set()
        assert gated.requesting_entered.wait(10)
        assert manager.get(queued.job_id).phase == "requesting-cognition"
        gated.allow_requesting.set()
        assert gated.applying_entered.wait(10)
        assert manager.get(queued.job_id).phase == "applying-cognition"
        gated.allow_applying.set()
        assert gated.persist_entered.wait(10)
        assert manager.get(queued.job_id).phase == "persisting"
        gated.allow_persist.set()
        assert gated.verify_entered.wait(10)
        assert manager.get(queued.job_id).phase == "verifying"
        gated.allow_verify.set()

        completed = _await_terminal(manager, queued.job_id)
        assert completed.phase == "completed"
        assert completed.result is not None
        assert completed.result.run_schema_version == 10
        assert completed.result.workbench_input_schema_version == 4
        assert (
            runs.WorkbenchRunRepository(workspace).load("phase-v10").manifest.schema_version == 10
        )
    finally:
        gated.release_all()
        manager.close()


@pytest.mark.parametrize(
    ("failure_phase", "error_code"),
    [
        ("persisting", "persistence-failed"),
        ("verifying", "verification-failed"),
    ],
)
def test_schema4_persistence_boundary_distinguishes_precommit_and_postcommit_failure(
    tmp_path: Path,
    failure_phase: str,
    error_code: str,
) -> None:
    from adlife.city.run_store import CityRunStore

    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    gated = _GatedCognitionRepositoryV2(runs.WorkbenchRunRepository(workspace))
    gated.failure_phase = failure_phase
    gated.release_all()
    manager = jobs.CityJobManager(gated)
    try:
        submitted = manager.submit(_schema4_validated(run_id=f"fault-{failure_phase}-v10"))
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "failed"
        assert terminal.error is not None and terminal.error.code == error_code

        run_directory = workspace.root / "city-runs" / terminal.run_id
        if failure_phase == "persisting":
            assert not run_directory.exists()
            assert gated.published_bytes == {}
        else:
            assert gated.published_bytes
            assert {
                path.name: path.read_bytes() for path in run_directory.iterdir() if path.is_file()
            } == gated.published_bytes
            assert CityRunStore(workspace.root).load(terminal.run_id).manifest.schema_version == 10
    finally:
        manager.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("workbench_input_schema_version", True),
        ("run_schema_version", 10.0),
        ("funded_response_limit", "60"),
        ("aggregate_output_token_ceiling", False),
    ],
)
def test_v10_accepted_settings_refuse_coerced_schema_and_count_tokens(field, value) -> None:
    jobs, _ = _modules()
    document = {
        "workbench_input_schema_version": 4,
        "run_schema_version": 10,
        "city_id": "fictional-grid-v2",
        "scenario_id": "launch-study",
        "campaign_id": "fictional-launch",
        "seed": "42",
        "agent_count": 1,
        "days": 1,
        "response_mode": "deterministic-rules",
        "effect_scope": "bounded-signals-v1",
        "cognition_mode": "rules",
        "funded_response_limit": 60,
        "aggregate_output_token_ceiling": 0,
    }
    document[field] = value
    with pytest.raises(ValidationError):
        jobs.WorkbenchAcceptedSettings.model_validate(document)


@pytest.mark.parametrize(
    "updates",
    [
        {"effect_scope": "explanation-only"},
        {"cognition_mode": "rules", "provider_profile_id": "provider-main"},
        {"cognition_mode": "mock", "provider_revision_sha256": "a" * 64},
        {"cognition_mode": "local", "provider_profile_id": None},
        {"cognition_mode": "local", "provider_revision_sha256": None},
        {"cognition_mode": "remote", "aggregate_output_token_ceiling": 0},
        {"cognition_mode": "local", "funded_response_limit": 20},
        {"funded_response_limit": 20},
    ],
)
def test_v10_accepted_settings_refuse_misplaced_provider_and_funding(updates) -> None:
    jobs, _ = _modules()
    document = {
        "workbench_input_schema_version": 4,
        "run_schema_version": 10,
        "city_id": "fictional-grid-v2",
        "scenario_id": "launch-study",
        "campaign_id": "fictional-launch",
        "seed": "42",
        "agent_count": 1,
        "days": 1,
        "response_mode": "deterministic-rules",
        "effect_scope": "bounded-signals-v1",
        "cognition_mode": "rules",
        "provider_profile_id": None,
        "provider_revision_sha256": None,
        "funded_response_limit": 60,
        "aggregate_output_token_ceiling": 0,
    }
    if updates.get("cognition_mode") in {"local", "remote"}:
        document.update(
            provider_profile_id="provider-main",
            provider_revision_sha256="a" * 64,
            funded_response_limit=10,
            aggregate_output_token_ceiling=10 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
        )
    document.update(updates)
    with pytest.raises(ValidationError):
        jobs.WorkbenchAcceptedSettings.model_validate(document)


def test_legacy_accepted_settings_json_shape_is_byte_stable() -> None:
    jobs, _ = _modules()
    settings = jobs.WorkbenchAcceptedSettings(
        workbench_input_schema_version=1,
        run_schema_version=7,
        city_id="fictional-grid-v2",
        scenario_id="launch-study",
        campaign_id="fictional-launch",
        seed="42",
        agent_count=1,
        days=1,
        response_mode="deterministic-rules",
    )
    assert settings.model_dump_json() == (
        '{"schema_version":1,"workbench_input_schema_version":1,'
        '"run_schema_version":7,"city_id":"fictional-grid-v2",'
        '"scenario_id":"launch-study","campaign_id":"fictional-launch",'
        '"seed":"42","agent_count":1,"days":1,'
        '"response_mode":"deterministic-rules","cognition_mode":null,'
        '"provider_profile_id":null,"provider_revision_sha256":null,'
        '"aggregate_output_token_ceiling":null}'
    )
    assert "effect_scope" not in settings.model_dump_json()
    assert "funded_response_limit" not in settings.model_dump_json()


def test_schema4_rollback_clears_operation_before_releasing_lease(monkeypatch) -> None:
    jobs, _ = _modules()
    providers = importlib.import_module("adlife.city.workbench_providers")
    events: list[str] = []

    class Operations(providers.WorkbenchOperationState):
        def finish(self, lease):
            events.append("finish")
            super().finish(lease)

    class Operation:
        def clear(self) -> None:
            events.append("clear")

    class Repository:
        def preflight_cognition_v2(self, validated):
            return Operation()

        def preflight_cognition(self, validated):
            raise AssertionError("schema-4 used legacy preflight")

        def ensure_available(self, run_id):
            raise RuntimeError("destination refusal")

    manager = jobs.CityJobManager(Repository(), operations=Operations())
    try:
        with pytest.raises(RuntimeError, match="destination refusal"):
            manager.submit(_schema4_validated(run_id="cleanup-order"))
        assert events == ["clear", "finish"]
    finally:
        manager.close()


@pytest.mark.parametrize(
    "phase,release_before",
    [
        ("planning-cognition", ()),
        ("requesting-cognition", ("planning",)),
        ("applying-cognition", ("planning", "requesting")),
    ],
)
def test_schema4_cancellation_wins_at_every_v2_phase(
    tmp_path: Path,
    phase: str,
    release_before: tuple[str, ...],
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / phase)
    gated = _GatedCognitionRepositoryV2(runs.WorkbenchRunRepository(workspace))
    manager = jobs.CityJobManager(gated)
    submitted = manager.submit(_schema4_validated(run_id=f"cancel-{phase}"))
    try:
        for name in release_before:
            getattr(gated, f"allow_{name}").set()
            assert getattr(gated, f"{name}_entered").wait(10)
        entered = getattr(gated, f"{phase.split('-')[0]}_entered")
        assert entered.wait(10)
        requested = manager.cancel(submitted.job_id)
        assert requested.phase == phase
        getattr(gated, f"allow_{phase.split('-')[0]}").set()
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "cancelled"
        assert terminal.error is None
        assert not (workspace.root / "city-runs" / f"cancel-{phase}").exists()
        assert not gated.persist_entered.is_set()
    finally:
        gated.release_all()
        manager.close()


@pytest.mark.parametrize(
    "phase,failure_type",
    [
        ("planning-cognition", RuntimeError),
        ("requesting-cognition", RuntimeError),
        ("applying-cognition", RuntimeError),
        ("planning-cognition", KeyboardInterrupt),
        ("requesting-cognition", KeyboardInterrupt),
        ("applying-cognition", KeyboardInterrupt),
    ],
)
def test_schema4_v2_phase_faults_are_screened_and_release_all_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    failure_type: type[BaseException],
) -> None:
    jobs, runs = _modules()
    workspace = prepare_workbench_workspace(tmp_path / f"{phase}-{failure_type.__name__}")
    gated = _GatedCognitionRepositoryV2(runs.WorkbenchRunRepository(workspace))
    gated.failure_phase = phase
    gated.failure_type = failure_type
    gated.release_all()
    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)
    manager = jobs.CityJobManager(gated)
    try:
        submitted = manager.submit(
            _schema4_validated(run_id=f"fault-{phase[0]}-{failure_type.__name__.lower()[:3]}")
        )
        deferred.run()
        terminal = _await_terminal(manager, submitted.job_id)
        assert terminal.phase == "failed"
        assert terminal.error is not None
        assert terminal.error.model_dump(mode="json") == {
            "code": "evaluation-failed",
            "message": "The deterministic city evaluation failed.",
        }
        assert manager.active_job() is None
        assert manager._operation_leases == {}
        assert manager._cognition_operations == {}
        assert manager._cancellations == {}
        assert not (workspace.root / "city-runs").exists()
    finally:
        manager.close()


def test_schema4_terminal_cleanup_clears_operation_before_releasing_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs, _ = _modules()
    providers = importlib.import_module("adlife.city.workbench_providers")
    events: list[str] = []

    class Operations(providers.WorkbenchOperationState):
        def finish(self, lease):
            events.append("finish")
            super().finish(lease)

    class Operation:
        def clear(self) -> None:
            events.append("clear")

    class Repository:
        def preflight_cognition_v2(self, validated):
            return Operation()

        def ensure_available(self, run_id):
            return None

    deferred = _DeferredExecutor()
    monkeypatch.setattr(jobs, "_new_worker_executor", lambda: deferred)
    manager = jobs.CityJobManager(Repository(), operations=Operations())
    submitted = manager.submit(_schema4_validated(run_id="terminal-cleanup-order"))
    try:
        manager.cancel(submitted.job_id)
        deferred.run()
        assert _await_terminal(manager, submitted.job_id).phase == "cancelled"
        assert events == ["clear", "finish"]
    finally:
        manager.close()


def test_nested_constructed_v10_settings_and_summary_are_revalidated(tmp_path: Path) -> None:
    from adlife.city.run_store import CityRunStore
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    jobs, runs = _modules()
    valid = jobs.WorkbenchAcceptedSettings(
        workbench_input_schema_version=4,
        run_schema_version=10,
        city_id="fictional-grid-v2",
        scenario_id="launch-study",
        campaign_id="fictional-launch",
        seed="42",
        agent_count=1,
        days=1,
        response_mode="deterministic-rules",
        effect_scope="bounded-signals-v1",
        cognition_mode="rules",
        funded_response_limit=60,
        aggregate_output_token_ceiling=0,
    )
    settings_document = valid.model_dump(mode="python")
    settings_document["run_schema_version"] = 9
    forged_settings = jobs.WorkbenchAcceptedSettings.model_construct(**settings_document)
    with pytest.raises(ValidationError):
        jobs.WorkbenchJobView.model_validate(
            {
                "job_id": "job-" + "a" * 32,
                "run_id": "nested-v10",
                "accepted_settings": forged_settings,
                "phase": "queued",
                "created_at": "2026-10-18T00:00:00.000000Z",
                "updated_at": "2026-10-18T00:00:00.000000Z",
                "cancellation_requested": False,
                "can_cancel": True,
            }
        )

    _saved_v10(tmp_path, mode="rules", agents=1, days=2, run_id="nested-v10")
    summary = runs._summary(CityRunStore(tmp_path).load("nested-v10"))
    summary_document = summary.model_dump(mode="python")
    summary_document["applied_response_count"] = summary.response_count + 1
    forged_summary = runs.WorkbenchRunSummary.model_construct(**summary_document)
    with pytest.raises(ValidationError):
        jobs.WorkbenchJobView.model_validate(
            {
                "job_id": "job-" + "b" * 32,
                "run_id": "nested-v10",
                "accepted_settings": valid,
                "phase": "completed",
                "created_at": "2026-10-18T00:00:00.000000Z",
                "updated_at": "2026-10-18T00:00:00.000000Z",
                "cancellation_requested": False,
                "can_cancel": False,
                "result": forged_summary,
            }
        )

    provider_settings = jobs.WorkbenchAcceptedSettings(
        workbench_input_schema_version=4,
        run_schema_version=10,
        city_id="fictional-grid-v2",
        scenario_id="launch-study",
        campaign_id="fictional-launch",
        seed="42",
        agent_count=1,
        days=1,
        response_mode="deterministic-rules",
        effect_scope="bounded-signals-v1",
        cognition_mode="local",
        provider_profile_id="provider-main",
        provider_revision_sha256="c" * 64,
        funded_response_limit=10,
        aggregate_output_token_ceiling=10 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
    )
    provider_summary = runs.WorkbenchRunSummary(
        run_id="nested-provider-v10",
        run_schema_version=10,
        workbench_input_schema_version=4,
        city_id="fictional-grid-v2",
        city_schema_version=2,
        city_sha256="a" * 64,
        scenario_id="launch-study",
        scenario_sha256="b" * 64,
        campaign_ids=("fictional-launch",),
        channels=("mobile-feed",),
        seed="42",
        days=1,
        agent_count=1,
        frame_count=1_440,
        opportunity_count=12,
        impression_count=12,
        noticed_count=12,
        response_count=12,
        effect_scope="bounded-signals-v1",
        cognition_mode="local",
        provider_profile_id="provider-main",
        provider_revision_sha256="c" * 64,
        funded_response_limit=10,
        aggregate_output_token_ceiling=10 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
        eligible_response_count=12,
        selected_request_count=12,
        funded_request_count=10,
        budget_skipped_count=2,
        unselected_response_count=0,
        applied_response_count=12,
        rules_count=0,
        mock_count=0,
        provider_count=10,
        cache_count=0,
        fallback_count=0,
        neutralized_response_count=2,
        uncertain_result_count=0,
        safety_neutralized_result_count=0,
        inspector_url="/runs/nested-provider-v10",
    )
    forged_documents = []
    wrong_funding = provider_summary.model_dump(mode="python")
    wrong_funding.update(
        funded_request_count=9,
        budget_skipped_count=3,
        provider_count=9,
        neutralized_response_count=3,
    )
    forged_documents.append(wrong_funding)
    wrong_city = provider_summary.model_dump(mode="python")
    wrong_city["city_schema_version"] = 1
    forged_documents.append(wrong_city)
    for index, document in enumerate(forged_documents):
        forged = runs.WorkbenchRunSummary.model_construct(**document)
        with pytest.raises(ValidationError):
            jobs.WorkbenchJobView.model_validate(
                {
                    "job_id": "job-" + str(index) * 32,
                    "run_id": "nested-provider-v10",
                    "accepted_settings": provider_settings,
                    "phase": "completed",
                    "created_at": "2026-10-18T00:00:00.000000Z",
                    "updated_at": "2026-10-18T00:00:00.000000Z",
                    "cancellation_requested": False,
                    "can_cancel": False,
                    "result": forged,
                }
            )
