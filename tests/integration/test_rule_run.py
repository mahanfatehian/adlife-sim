"""End-to-end rule-mode runs: the orchestrator drives a whole scenario to an artifact.

These tests exercise the documented loop through its ports - a cognition service that
resolves through the rule formula, the :class:`SQLiteRunStore`, and two sinks - so the
artifact left behind is exactly what Task 14's CLI will publish and Task 16's reports
will read. The stage arithmetic itself is pinned by the unit suites; here it is the
WHOLE that is under test.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from adlife.adapters.cognition.rules import (
    RULE_MODEL_ID,
    RuleCognitionInputs,
    RuleCognitionProvider,
)
from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderKind,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
)
from adlife.core.ports.run_store import StoredRun
from adlife.core.simulation.engine import AdLifeModel
from adlife.core.simulation.movement import TickPlan
from adlife.core.simulation.runner import (
    InterruptedRun,
    RunIdentity,
    SimulationRunner,
)
from tests.builders import strip_campaigns

SERVICE_KIND: ProviderKind = "rule"

SINKS: tuple[str, ...] = ("run-small.jsonl", "run-small-mirror.jsonl")


def _repo_root() -> Path:
    """The checkout that holds ``uv.lock`` - the reproducibility input is the project."""
    return Path(__file__).resolve().parents[2]


def rule_inputs(plan: TickPlan) -> dict[str, RuleCognitionInputs]:
    """Wire the terminal rule fallback for every request a plan raises."""
    inputs: dict[str, RuleCognitionInputs] = {}
    for decision in plan.attention:
        if not decision.noticed:
            continue
        request_id = decision.events[2].event_id
        opportunity = decision.opportunity
        inputs[request_id] = RuleCognitionInputs(
            profile=opportunity.profile,
            state=opportunity.state,
            campaign=opportunity.campaign,
            placement=opportunity.placement,
        )
    return inputs


def rule_usage() -> ProviderUsage:
    return ProviderUsage(
        provider_kind="rule",
        model_id=RULE_MODEL_ID,
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=0,
        cache_hit=False,
    )


class RuleResolutionPort:
    """The core-side cognition seam a rules run is wired with.

    It answers every request straight through the terminal fallback the runner builds
    from the plan - which IS the documented rule formula - in-process, with no budget
    and no retry: the rule provider cannot fail, so a service wrapper would add nothing
    here. The runner sees only this port, never an adapter.
    """

    def __init__(self) -> None:
        self.answered: list[CognitionRequest] = []

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        answers: dict[str, CognitionAnswer] = {}
        for request in requests:
            self.answered.append(request)
            assert isinstance(fallback_provider, RuleCognitionProvider)
            answers[request.request_id] = await fallback_provider.answer(request)
        return answers

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        return ProviderMetadata(
            kind="rule",
            model_id=RULE_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="d" * 64,
        )


def build_runner(
    store: SQLiteRunStore,
    sinks: Sequence[JsonlEventSink],
    *,
    resolve_factory: Callable[[], RuleResolutionPort] | None = None,
    manifest_overrides: dict[str, object] | None = None,
) -> tuple[SimulationRunner, list[RuleResolutionPort]]:
    """Assemble the runner the way the CLI will, with the rule port behind the seam."""
    ports: list[RuleResolutionPort] = []

    def cognition_factory(model: AdLifeModel):
        port = (resolve_factory or RuleResolutionPort)()
        ports.append(port)
        return port

    identity = RunIdentity.for_project(
        project_root=_repo_root(),
        provider="rules",
        model_id=RULE_MODEL_ID,
        overrides=manifest_overrides,
    )
    runner = SimulationRunner(
        cognition_factory=cognition_factory,
        fallback_provider_factory=lambda plan: _rule_fallback(plan),
        identity=identity,
    )
    return runner, ports


def _rule_fallback(plan: TickPlan):
    return RuleCognitionProvider.for_requests(plan.requests, rule_inputs(plan))


async def drive_rule_run(
    scenario: Scenario,
    root: Path,
    *,
    run_id: str,
    sinks: Sequence[str] = SINKS,
) -> tuple[SimulationResult, SQLiteRunStore, list[JsonlEventSink]]:
    store = SQLiteRunStore(root)
    event_sinks = [JsonlEventSink(root / name) for name in sinks]
    runner, _ports = build_runner(store, event_sinks)
    result = await runner.run(scenario, seed=42, store=store, sinks=event_sinks, run_id=run_id)
    return result, store, event_sinks


async def test_three_agents_complete_one_rule_day(small_scenario: Scenario, tmp_path: Path) -> None:
    result, store, _sinks = await drive_rule_run(small_scenario, tmp_path, run_id="run-small")

    assert isinstance(result, SimulationResult)
    assert result.status == "completed"
    assert result.final_minute == 1440
    assert result.event_count > 0
    for name in SINKS:
        assert (tmp_path / name).exists(), name

    stored: StoredRun = store.load_run("run-small")
    assert stored.status == "completed"
    assert stored.result == result
    types = [event.event_type for event in stored.events]
    assert types[0] is EventType.RUN_STARTED
    assert types[-1] is EventType.RUN_COMPLETED
    assert EventType.DAY_REFLECTED in types
    assert EventType.RUN_FAILED not in types


async def test_the_run_started_event_is_the_stream_anchor(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    result, store, _sinks = await drive_rule_run(small_scenario, tmp_path, run_id="run-anchor")
    assert result.status == "completed"
    stored = store.load_run("run-anchor")
    started = [event for event in stored.events if event.event_type is EventType.RUN_STARTED]
    assert len(started) == 1
    assert started[0].sequence == 0
    assert started[0].event_id == "run-anchor:event-00000000"


async def test_a_no_campaign_control_still_completes(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    control = strip_campaigns(small_scenario)
    result, store, _sinks = await drive_rule_run(control, tmp_path, run_id="run-control")

    assert result.status == "completed"
    stored = store.load_run("run-control")
    types = [event.event_type for event in stored.events]
    assert EventType.CAMPAIGN_ELIGIBLE not in types
    assert EventType.CAMPAIGN_IMPRESSION not in types
    assert EventType.RUN_COMPLETED in types


async def test_the_manifest_records_the_reproducibility_input(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    _result, store, _sinks = await drive_rule_run(small_scenario, tmp_path, run_id="run-manifest")

    stored = store.load_run("run-manifest")
    manifest: RunManifest = stored.manifest
    assert manifest.run_id == "run-manifest"
    assert manifest.scenario_id == small_scenario.scenario_id
    assert manifest.provider == "rules"
    assert manifest.model_id == RULE_MODEL_ID
    assert manifest.prompt_version == "cognition-v1"
    assert len(manifest.scenario_hash) == 64
    assert manifest.git_sha == "uncommitted" or len(manifest.git_sha) == 40
    assert manifest.lockfile_sha256 != "0" * 64


async def test_a_service_refusal_records_the_run_failed(
    small_scenario: Scenario, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adlife.core.simulation.runner import RunnerRefused

    store = SQLiteRunStore(tmp_path)
    event_sinks = [JsonlEventSink(tmp_path / "run-failed.jsonl")]
    runner, _ports = build_runner(store, event_sinks)

    class RefusingPort:
        async def resolve(self, requests, *, fallback_provider):
            raise RunnerRefused("the cognition seam refused the tick")

    monkeypatch.setattr(
        SimulationRunner,
        "_cognition_port_for",
        lambda self, model, provider: RefusingPort(),
    )
    with pytest.raises(RunnerRefused):
        await runner.run(
            small_scenario,
            seed=42,
            store=store,
            sinks=event_sinks,
            run_id="run-failed",
        )

    stored = store.load_run("run-failed")
    assert stored.status == "failed"
    assert stored.result is not None
    assert stored.result.status == "failed"
    assert stored.result.failure_reason is not None
    assert "RunnerRefused" in stored.result.failure_reason
    types = [event.event_type for event in stored.events]
    assert EventType.RUN_FAILED in types


async def test_events_reach_the_store_before_any_sink_sees_them(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    from adlife.core.ports.event_sink import EventSinkError

    store = SQLiteRunStore(tmp_path)
    observed: list[EventType] = []

    class ExplodingSink:
        def append_many(self, run_id: str, events: Sequence[DomainEvent]) -> None:
            observed.extend(event.event_type for event in events)
            raise EventSinkError("the sink is down")

    runner, _ports = build_runner(store, [])
    with pytest.raises(EventSinkError):
        await runner.run(
            small_scenario,
            seed=42,
            store=store,
            sinks=[ExplodingSink()],
            run_id="run-order",
        )

    stored_types = [event.event_type for event in store.iter_events("run-order")]
    assert observed, "the sink was reached"
    assert stored_types[: len(observed)] == observed
    assert stored_types[0] is EventType.RUN_STARTED


async def test_provider_metadata_describes_the_rules_run(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / "run-meta.jsonl")]
    runner, ports = build_runner(store, sinks)
    await runner.run(small_scenario, seed=42, store=store, sinks=sinks, run_id="run-meta")

    metadata = ports[0].provider_metadata
    assert metadata is not None
    assert metadata.kind == "rule"
    assert metadata.model_id == RULE_MODEL_ID
    assert isinstance(metadata.sampling, SamplingSettings)


async def test_an_interrupted_run_is_recorded_and_can_be_abandoned(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    """A run stopped mid-loop records an interruption and is closed for writes.

    The suspension point is real: a pausing port yields to the event loop on its first
    resolution, the task is cancelled there, and the runner's stop path records the
    ``interrupted`` status with everything committed so far. Whether the stop then lands
    as CancelledError (cancelled before the store closed the run) or as InterruptedRun
    (the runner's own re-raise) is scheduling, not contract.
    """
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / "run-interrupted.jsonl")]

    class PausingPort(RuleResolutionPort):
        """Resolves nothing: the first dispatch is cancelled, like a killed process."""

        async def resolve(self, requests, *, fallback_provider):
            raise asyncio.CancelledError()

    runner, _ports = build_runner(store, sinks, resolve_factory=lambda: PausingPort())

    async def execute() -> SimulationResult:
        return await runner.run(
            small_scenario, seed=42, store=store, sinks=sinks, run_id="run-interrupted"
        )

    task = asyncio.ensure_future(execute())
    for _ in range(10):
        await asyncio.sleep(0)
        if task.done():
            break
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, InterruptedRun):
        await task

    stored = store.load_run("run-interrupted")
    assert stored.status in {"failed", "interrupted"}
    assert stored.result is not None
    assert stored.completed_at is not None
