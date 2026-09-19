"""Hybrid mock-provider runs, provider failure fallback, and resume.

The mock provider stands in for a live model: every answer is a fixture chosen by hashing
the request, so a hybrid run exercises the cognition service - budgets, concurrency, the
cache seam - without a network. The suites here pin the three behaviours specification
section 12 attaches to that mode: failures never abort a run, failures are visible as
``cognition.fallback`` events naming their reason, and a checkpoint taken at a day
boundary resumes into the same future an uninterrupted run would have had.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from adlife.adapters.cognition.mock import MOCK_MODEL_ID, MockCognitionProvider
from adlife.adapters.cognition.rules import RULE_MODEL_ID
from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.results import SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
)
from adlife.core.simulation.engine import AdLifeModel
from adlife.core.simulation.movement import TickPlan
from adlife.core.simulation.runner import InterruptedRun, RunIdentity, SimulationRunner
from tests.builders import pair_scenario, rescale_scenario


class MockResolutionPort:
    """The core-side cognition seam wired with the deterministic mock provider."""

    def __init__(self) -> None:
        self.provider = MockCognitionProvider(seed=0)
        self.answered: list[CognitionRequest] = []
        self.usage_records: list[ProviderUsage] = []

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        del fallback_provider  # the mock cannot fail; its own answers are terminal
        answers: dict[str, CognitionAnswer] = {}
        for request in requests:
            self.answered.append(request)
            answer = await self.provider.answer(request)
            self.usage_records.append(answer.usage)
            answers[request.request_id] = answer
        return answers

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind="mock",
            model_id=MOCK_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


class FirstTickFailingPort(MockResolutionPort):
    """A port whose first tick fails the way the service reports a dead endpoint.

    The real service never raises for a provider failure - it composes the rule fallback
    and stamps the answer's provenance with the reason. This port reproduces exactly
    that shape: the first tick's answers are fallback-stamped, the rest are mock
    answers, and the run must survive both.
    """

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        if not self.failed and requests:
            self.failed = True
            answers: dict[str, CognitionAnswer] = {}
            for request in requests:
                self.answered.append(request)
                fallback_answer = await fallback_provider.answer(request)
                stamped = ProviderUsage(
                    provider_kind="fallback",
                    model_id=RULE_MODEL_ID,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0,
                    cache_hit=False,
                    fallback_reason="provider-unavailable",
                )
                self.usage_records.append(stamped)
                answers[request.request_id] = CognitionAnswer(
                    result=fallback_answer.result,
                    usage=stamped,
                )
            return answers
        return await super().resolve(requests, fallback_provider=fallback_provider)


def build_mock_runner(
    store: SQLiteRunStore,
    sinks: Sequence[JsonlEventSink],
    port_factory: Callable[[], Any] = MockResolutionPort,
) -> tuple[SimulationRunner, list[Any]]:
    ports: list[Any] = []

    def cognition_factory(model: AdLifeModel):
        port = port_factory()
        ports.append(port)
        return port

    identity = RunIdentity.for_project(
        project_root=store.root,
        provider="mock",
        model_id=MOCK_MODEL_ID,
    )
    return SimulationRunner(
        cognition_factory=cognition_factory,
        fallback_provider_factory=_wired_rule_fallback,
        identity=identity,
    ), ports


def _wired_rule_fallback(plan: TickPlan):
    """The terminal fallback the real service would receive: every request wired."""
    from adlife.adapters.cognition.rules import RuleCognitionInputs, RuleCognitionProvider

    inputs: dict[str, RuleCognitionInputs] = {}
    for decision in plan.attention:
        if not decision.noticed:
            continue
        opportunity = decision.opportunity
        inputs[decision.events[2].event_id] = RuleCognitionInputs(
            profile=opportunity.profile,
            state=opportunity.state,
            campaign=opportunity.campaign,
            placement=opportunity.placement,
        )
    return RuleCognitionProvider.for_requests(plan.requests, inputs)


async def test_three_agents_complete_one_mock_day(small_scenario: Scenario, tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / "mock-day.jsonl")]
    runner, ports = build_mock_runner(store, sinks)
    scenario = small_scenario

    result = await runner.run(scenario, seed=42, store=store, sinks=sinks, run_id="run-mock")

    assert isinstance(result, SimulationResult)
    assert result.status == "completed"
    assert result.final_minute == 1440
    assert result.event_count > 0
    assert ports[0].answered, "the hybrid run answered cognition requests"
    store.load_run("run-mock")
    usage = store.load_provider_usage("run-mock")
    assert usage.run_id == "run-mock"
    assert len(usage.records) == len(ports[0].answered)
    kinds = {record.provider_kind for record in usage.records}
    assert "mock" in kinds


async def test_provider_failure_falls_back_and_the_run_survives(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / "mock-fallback.jsonl")]
    runner, _ports = build_mock_runner(store, sinks, port_factory=FirstTickFailingPort)
    scenario = small_scenario

    result = await runner.run(scenario, seed=42, store=store, sinks=sinks, run_id="run-fallback")
    assert result.status == "completed", "a provider failure must never abort a run"

    stored = store.load_run("run-fallback")
    types = [event.event_type for event in stored.events]
    assert EventType.COGNITION_FALLBACK in types
    fallback_events = [
        event for event in stored.events if event.event_type is EventType.COGNITION_FALLBACK
    ]
    assert fallback_events[0].payload["fallback_reason"] in {
        "provider-unavailable",
        "timeout",
        "invalid-response",
        "budget-exhausted",
        "cache-miss",
    }
    assert fallback_events[0].caused_by_event_ids, "a fallback is caused by its noticed event"
    sources = {event.source.value for event in fallback_events}
    assert "fallback" in sources
    assert EventType.RUN_FAILED not in types


async def test_checkpoints_are_saved_at_every_day_boundary(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = rescale_scenario(small_scenario, days=2)
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / "mock-two-days.jsonl")]
    runner, _ports = build_mock_runner(store, sinks)

    await runner.run(scenario, seed=42, store=store, sinks=sinks, run_id="run-days")

    stored = store.load_run("run-days")
    minutes = [checkpoint.simulated_minute for checkpoint in stored.checkpoints]
    assert minutes == [1440, 2880]


async def test_resume_from_a_day_boundary_reproduces_the_uninterrupted_future(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    """A run stopped at a day boundary resumes into the same future, tick for tick.

    The partial run is built the way a killed process would leave it: day one committed
    through the real store, the day-boundary checkpoint saved, the status still
    ``running``. The resumed run must then produce the day-two stream an uninterrupted
    run produced - same events, same order, same minutes.
    """
    scenario = rescale_scenario(small_scenario, days=2)

    # The uninterrupted baseline, run to completion in its own root.
    baseline = SQLiteRunStore(tmp_path / "baseline")
    baseline_sinks = [JsonlEventSink(tmp_path / "baseline" / "baseline.jsonl")]
    baseline_runner, _ports = build_mock_runner(baseline, baseline_sinks)
    await baseline_runner.run(
        scenario, seed=42, store=baseline, sinks=baseline_sinks, run_id="run-resume"
    )
    finished = baseline.load_run("run-resume")

    # The partial run: day one only, through the ports, left running.
    partial_root = tmp_path / "partial"
    store = SQLiteRunStore(partial_root)
    sinks = [JsonlEventSink(partial_root / "run-resume.jsonl")]
    identity = RunIdentity.for_project(
        project_root=partial_root, provider="mock", model_id=MOCK_MODEL_ID
    )
    manifest = identity.manifest_for(
        run_id="run-resume", scenario=scenario, seed=42, provider="mock", model_id=MOCK_MODEL_ID
    )
    store.create_run(manifest, scenario=scenario)
    model = AdLifeModel(scenario=scenario, seed=42)
    store.append_events(model.start_events("run-resume"))
    port = MockResolutionPort()
    while model.clock.current_minute < 1440:
        plan = model.plan_tick()
        answers = await port.resolve(plan.requests, fallback_provider=_wired_rule_fallback(plan))
        outcome = model.commit_tick(plan, cognition=answers)
        store.append_events(outcome.events)
        model.clock.advance()
    store.save_checkpoint(model.checkpoint())
    assert store.load_run("run-resume").status == "running"

    resumed_runner, _ports = build_mock_runner(store, sinks)
    result = await resumed_runner.resume(
        scenario,
        seed=42,
        store=store,
        sinks=sinks,
        run_id="run-resume",
        manifest=manifest,
    )
    assert result.status == "completed"
    resumed_stored = store.load_run("run-resume")
    assert resumed_stored.status == "completed"
    assert _tail_types(resumed_stored.events, 1440) == _tail_types(finished.events, 1440)


def _tail_types(events: tuple[DomainEvent, ...], after_minute: int) -> list[str]:
    return [
        f"{event.simulated_minute}:{event.event_type}:{event.agent_id}:{event.campaign_id}"
        for event in events
        if event.simulated_minute > after_minute
    ]


async def test_a_two_agent_social_run_shares_word_of_mouth(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / "mock-social.jsonl")]
    runner, _ports = build_mock_runner(store, sinks)
    scenario = pair_scenario(small_scenario)

    await runner.run(scenario, seed=42, store=store, sinks=sinks, run_id="run-social")

    stored = store.load_run("run-social")
    assert stored.result is not None and stored.result.status == "completed"
    shared = [event for event in stored.events if event.event_type is EventType.SOCIAL_SHARED]
    assert all(event.caused_by_event_ids for event in shared)
    assert all(
        set(event.caused_by_event_ids) <= {e.event_id for e in stored.events} for event in shared
    )


async def test_resume_refuses_a_completed_run(small_scenario: Scenario, tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / "mock-done.jsonl")]
    runner, _ports = build_mock_runner(store, sinks)
    scenario = small_scenario
    await runner.run(scenario, seed=42, store=store, sinks=sinks, run_id="run-done")
    finished = store.load_run("run-done")

    import pytest

    with pytest.raises(InterruptedRun):
        await runner.resume(
            scenario,
            seed=42,
            store=store,
            sinks=sinks,
            run_id="run-done",
            manifest=finished.manifest,
        )
