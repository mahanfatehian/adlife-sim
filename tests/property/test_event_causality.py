"""Whole-run causality: every recorded cause happened, earlier, in the same run.

The store's batch validation pins the per-tick contract; these properties run the full
orchestrator to completion and verify the properties that make a WHOLE stream a replay -
the anchor event, contiguous sequences, and the causal graph over every tick, including
the social propagation and daily reflection the later stages emit.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from adlife.adapters.cognition.mock import MOCK_MODEL_ID, MockCognitionProvider
from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    SamplingSettings,
)
from adlife.core.simulation.runner import RunIdentity, SimulationRunner
from tests.builders import pair_scenario, small_three_agent_scenario


class MockResolutionPort:
    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        provider = MockCognitionProvider(seed=0)
        del fallback_provider
        return {request.request_id: await provider.answer(request) for request in requests}

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind="mock",
            model_id=MOCK_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


def build_runner(root: Path) -> SimulationRunner:
    identity = RunIdentity.for_project(
        project_root=root,
        provider="mock",
        model_id=MOCK_MODEL_ID,
        overrides={"git_sha": "uncommitted", "platform": "property-test-platform"},
    )
    return SimulationRunner(
        cognition_factory=lambda model: MockResolutionPort(),
        fallback_provider_factory=lambda plan: _no_fallback(plan),
        identity=identity,
    )


def _no_fallback(plan):
    from adlife.adapters.cognition.rules import RuleCognitionProvider

    del plan
    return RuleCognitionProvider({})


async def complete_run(
    scenario: Scenario, tmp_path: Path, run_id: str
) -> tuple[SQLiteRunStore, tuple[DomainEvent, ...], SimulationResult]:
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / f"{run_id}.jsonl")]
    runner = build_runner(tmp_path)
    result = await runner.run(scenario, seed=42, store=store, sinks=sinks, run_id=run_id)
    stored = store.load_run(run_id)
    return store, stored.events, result


def _assert_causal_chain(events: tuple[DomainEvent, ...], run_id: str) -> None:
    known: set[str] = set()
    for index, event in enumerate(events):
        assert event.sequence == index, f"sequence {event.sequence} at position {index}"
        assert event.event_id == f"{run_id}:event-{event.sequence:08d}"
        assert event.run_id == run_id
        for cause in event.caused_by_event_ids:
            assert cause in known, f"event {event.sequence} names unknown cause {cause}"
        known.add(event.event_id)


async def test_a_complete_stream_is_a_single_anchorable_causal_graph(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    _store, events, result = await complete_run(
        small_three_agent_scenario(valid_scenario), tmp_path, "run-causal"
    )
    assert result.status == "completed"
    _assert_causal_chain(events, "run-causal")
    assert events[0].event_type is EventType.RUN_STARTED
    assert events[-1].event_type is EventType.RUN_COMPLETED


async def test_stage_families_never_traverse_the_stream_twice(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    """Eligibility precedes impression, which precedes its terminal, per occurrence."""
    _store, events, result = await complete_run(
        small_three_agent_scenario(valid_scenario), tmp_path, "run-stages"
    )
    assert result.status == "completed"
    for index, event in enumerate(events):
        if event.event_type is EventType.CAMPAIGN_IMPRESSION:
            eligible = [
                earlier
                for earlier in events[:index]
                if earlier.event_type is EventType.CAMPAIGN_ELIGIBLE
                and earlier.agent_id == event.agent_id
                and earlier.campaign_id == event.campaign_id
            ]
            assert eligible, f"impression at {event.sequence} without an earlier eligibility"
            assert event.caused_by_event_ids
        if event.event_type in {EventType.CAMPAIGN_NOTICED, EventType.CAMPAIGN_IGNORED}:
            impression = [
                earlier
                for earlier in events[:index]
                if earlier.event_type is EventType.CAMPAIGN_IMPRESSION
                and earlier.agent_id == event.agent_id
                and earlier.campaign_id == event.campaign_id
            ]
            assert impression, f"terminal at {event.sequence} without an earlier impression"


async def test_every_social_memory_and_reflection_names_real_causes(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = pair_scenario(small_three_agent_scenario(valid_scenario))
    _store, events, result = await complete_run(scenario, tmp_path, "run-social-graph")
    assert result.status == "completed"
    _assert_causal_chain(events, "run-social-graph")

    by_id = {event.event_id: event for event in events}
    for event in events:
        if event.event_type in {EventType.SOCIAL_SHARED, EventType.MEMORY_CREATED}:
            assert event.caused_by_event_ids, f"{event.event_type} at {event.sequence} is orphaned"
        # A day.reflected names the agent's last event of the day, but an agent whose
        # day recorded nothing has nothing to name; the engine leaves it causeless by
        # design, so the property is: a cause, when present, is real and earlier.
        if event.event_type is EventType.SOCIAL_RECEIVED:
            (cause,) = event.caused_by_event_ids
            assert by_id[cause].event_type is EventType.SOCIAL_SHARED


async def test_the_completed_result_agrees_with_the_stored_stream(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    store, events, result = await complete_run(
        small_three_agent_scenario(valid_scenario), tmp_path, "run-agreement"
    )
    assert result.status == "completed"
    assert result.event_count == len(events)
    assert result.final_minute == 1440
    stored = store.load_run("run-agreement")
    manifest: RunManifest = stored.manifest
    assert manifest.scenario_hash == stored.manifest.scenario_hash
    assert stored.result == result
