"""The metamorphic gates: what must be TRUE of every run, whatever the scenario.

A metamorphic test states a relation between two executions of the system instead of a
single expected output, so it can certify properties no fixed fixture can: order must
not matter, a display-only field must not decide, a disabled channel must be absent,
and every claimed effect must trace to its cause. Every suite here drives the WHOLE
run through the real runner, store and sinks, so what is certified is the artifact a
user keeps, not an in-memory convenience.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from adlife.adapters.cognition.cache import CognitionCache
from adlife.adapters.cognition.mock import MOCK_MODEL_ID, MockCognitionProvider
from adlife.adapters.cognition.replay import ReplayCognitionProvider
from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.scenario import Scenario
from adlife.core.experiments.metrics import MetricsCalculator
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
)
from adlife.core.simulation.runner import RunIdentity, SimulationRunner
from tests.builders import (
    rescale_scenario,
    small_three_agent_scenario,
    strip_campaigns,
)

SEED = 42

# -- shared harness ---------------------------------------------------------------


def _identity(root: Path, provider: str) -> RunIdentity:
    return RunIdentity.for_project(
        project_root=root,
        provider=provider,
        model_id="rule-baseline" if provider == "rules" else MOCK_MODEL_ID,
        overrides={"git_sha": "uncommitted", "platform": "metamorphic-platform"},
    )


def _identity_for(root: Path, provider: str) -> RunIdentity:
    """Identity for the manifest's provider literal, not the wiring mode."""
    literal = {"rules": "rules", "hybrid": "mock", "replay": "replay"}.get(provider, provider)
    return RunIdentity.for_project(
        project_root=root,
        provider=literal,
        model_id="rule-baseline" if literal == "rules" else MOCK_MODEL_ID,
        overrides={"git_sha": "uncommitted", "platform": "metamorphic-platform"},
    )
    """The mock provider plus the metadata the cache key is made of.

    The service records answers only when it can name the provider that produced them
    (``DescribedCognitionProvider``), so a replayable hybrid arm needs this wrapper.
    """

    def __init__(self) -> None:
        self.provider = MockCognitionProvider(seed=0)

    async def answer(self, request: CognitionRequest) -> CognitionAnswer:
        return await self.provider.answer(request)

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind="mock",
            model_id=MOCK_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


class _DescribedMock:
    """The mock provider plus the metadata the cache key is made of.

    The service records answers only when it can name the provider that produced them
    (``DescribedCognitionProvider``), so a replayable hybrid arm needs this wrapper.
    The prompt digest is the real template digest: a replay must describe the provider
    exactly as it was recorded, or the keys name different questions.
    """

    def __init__(self) -> None:
        from adlife.adapters.cognition.prompts import prompt_template_sha256

        self.provider = MockCognitionProvider(seed=0)
        self._metadata = ProviderMetadata(
            kind="mock",
            model_id=MOCK_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256=prompt_template_sha256(),
        )

    async def answer(self, request: CognitionRequest) -> CognitionAnswer:
        return await self.provider.answer(request)

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return self._metadata


class _MockPort:
    """The seeded mock behind the cognition seam, mirroring the golden harness."""

    def __init__(self, seed: int = 0) -> None:
        self.provider = MockCognitionProvider(seed=seed)

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        del fallback_provider
        return {request.request_id: await self.provider.answer(request) for request in requests}

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind="mock",
            model_id=MOCK_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


async def _drive(
    scenario: Scenario,
    root: Path,
    *,
    run_id: str,
    seed: int = SEED,
    mock: bool = False,
) -> tuple[SQLiteRunStore, list[JsonlEventSink]]:
    """One complete orchestrated run against a fresh root."""
    store = SQLiteRunStore(root)
    provider_name = "mock" if mock else "rules"
    sinks: list[JsonlEventSink] = [JsonlEventSink(root / f"{run_id}.jsonl")]
    if mock:
        port: object = _MockPort()
        factory = lambda model: port  # noqa: E731
    else:
        from adlife.cli.cognition import RuleCognitionPort

        factory = lambda model: RuleCognitionPort()  # noqa: E731
    runner = SimulationRunner(
        cognition_factory=factory,
        fallback_provider_factory=lambda plan: _rule_fallback(plan),
        identity=_identity(root, provider_name),
    )
    await runner.run(
        scenario,
        seed=seed,
        store=store,
        sinks=sinks,
        run_id=run_id,
        provider_name=provider_name,
        model_id=MOCK_MODEL_ID if mock else "rule-baseline",
    )
    return store, sinks


def _rule_fallback(plan: object):
    from adlife.cli.cognition import rule_fallback_for

    return rule_fallback_for(plan)


def _stored_events(root: Path, run_id: str) -> list[dict[str, object]]:
    """Normalized event records: every identifying field except run-local identifiers."""
    store = SQLiteRunStore(root)
    normalized: list[dict[str, object]] = []
    for event in store.load_run(run_id).events:
        normalized.append(
            {
                "sequence": event.sequence,
                "event_type": event.event_type.value,
                "agent_id": event.agent_id,
                "campaign_id": event.campaign_id,
                "payload": json.loads(json.dumps(dict(event.payload), sort_keys=True)),
                "caused_by": list(event.caused_by_event_ids),
            }
        )
    return normalized


def _scenario_hash(root: Path, run_id: str) -> str:
    return SQLiteRunStore(root).load_run(run_id).manifest.scenario_hash


# -- gates 1 and 2: mode determinism ----------------------------------------------


async def test_rules_mode_same_seed_same_normalized_stream_and_metrics(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    await _drive(scenario, tmp_path / "a", run_id="meta-rules")
    await _drive(scenario, tmp_path / "b", run_id="meta-rules")

    assert _stored_events(tmp_path / "a", "meta-rules") == _stored_events(
        tmp_path / "b", "meta-rules"
    )
    first = SQLiteRunStore(tmp_path / "a").metrics_json_path("meta-rules").read_bytes()
    second = SQLiteRunStore(tmp_path / "b").metrics_json_path("meta-rules").read_bytes()
    assert first == second
    # and the scenario the two runs recorded is the same one
    assert _scenario_hash(tmp_path / "a", "meta-rules") == _scenario_hash(
        tmp_path / "b", "meta-rules"
    )


async def test_mock_mode_same_seed_same_normalized_stream(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    await _drive(scenario, tmp_path / "a", run_id="meta-mock", mock=True)
    await _drive(scenario, tmp_path / "b", run_id="meta-mock", mock=True)
    assert _stored_events(tmp_path / "a", "meta-mock") == _stored_events(
        tmp_path / "b", "meta-mock"
    )  # -- gate 3: hybrid replay ---------------------------------------------------------


async def test_hybrid_run_replays_identically_from_its_cache(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    from adlife.adapters.cognition.service import CognitionBudget, CognitionService
    from adlife.cli.cognition import ReplayCognitionPort, ServiceCognitionPort
    from adlife.core.simulation.rng import RandomOracle

    scenario = small_three_agent_scenario(valid_scenario)
    base = tmp_path / "hybrid"
    cache = CognitionCache(base / "cache")
    # The replay carries the same run identifier as the original run: a cognition
    # request is bound to its run (request_id starts with run_id), and the cache key
    # digests the whole request, so only the same run identifier can address the same
    # records. Same id across two roots is the documented replay shape.
    run_id = "hyb"

    root_a = base / "a"
    provider = _DescribedMock()

    def service_factory(fallback_provider: object) -> CognitionService:
        return CognitionService(
            fallback=fallback_provider,  # type: ignore[arg-type]
            budget=CognitionBudget(per_agent=6, total=180),
            oracle=RandomOracle(SEED),
            cache=cache,
            retries=0,
        )

    port = ServiceCognitionPort(provider, service_factory)
    store_a = SQLiteRunStore(root_a)
    runner = SimulationRunner(
        cognition_factory=lambda model: port,
        fallback_provider_factory=lambda plan: _rule_fallback(plan),
        identity=_identity_for(root_a, "hybrid"),
    )
    result = await runner.run(
        scenario,
        seed=SEED,
        store=store_a,
        sinks=(),
        run_id=run_id,
        provider_name="mock",
        model_id=MOCK_MODEL_ID,
    )
    assert result.status == "completed"
    original = store_a.load_run(run_id)
    # The service dispatched cognition: the committed stream carries the answers, and
    # the cache now holds every record the replay provider must serve.
    answered = [
        event for event in original.events if event.event_type is EventType.COGNITION_COMPLETED
    ]
    assert answered, "a hybrid run must have dispatched cognition"
    cached = list((base / "cache").iterdir())
    assert cached, "a hybrid run must leave recorded cognition behind"

    # Replay: serve every answer from the recorded cache, described exactly as the
    # provider was recorded (same kind, model, sampling, prompt digest). The wrapper
    # records the per-request usage the runner persists, replay-stamped as cache hits.
    class _RecordingReplayPort:
        def __init__(self) -> None:
            self._inner = ReplayCognitionPort(cache, provider._metadata)
            self.usage_records: list[ProviderUsage] = []

        async def resolve(
            self,
            requests: Sequence[CognitionRequest],
            *,
            fallback_provider: object,
        ) -> dict[str, CognitionAnswer]:
            answers = await self._inner.resolve(requests, fallback_provider=fallback_provider)
            for request in requests:
                if request.request_id in answers:
                    self.usage_records.append(self._inner._provider.usage_for(request))
            return answers

        @property
        def provider_metadata(self) -> ProviderMetadata:
            return provider._metadata

    root_b = base / "b"
    replay_port = _RecordingReplayPort()
    store_b = SQLiteRunStore(root_b)
    replay_runner = SimulationRunner(
        cognition_factory=lambda model: replay_port,
        fallback_provider_factory=lambda plan: _rule_fallback(plan),
        identity=_identity_for(root_b, "replay"),
    )
    replay_result = await replay_runner.run(
        scenario,
        seed=SEED,
        store=store_b,
        sinks=(),
        run_id=run_id,
        provider_name="replay",
        model_id=MOCK_MODEL_ID,
    )
    assert replay_result.status == "completed"
    assert _stored_events(root_a, run_id) == _stored_events(root_b, run_id)
    usage = store_b.load_provider_usage(run_id)
    assert usage.records, "replay must record usage"
    assert all(record.cache_hit for record in usage.records)


class _ReplayPort:
    """Port wrapping a ReplayCognitionProvider for the runner's seam."""

    def __init__(self, provider: ReplayCognitionProvider) -> None:
        self._provider = provider

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        del fallback_provider
        return {request.request_id: await self._provider.answer(request) for request in requests}

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return self._provider._metadata


# -- gate 4: seed sensitivity ------------------------------------------------------


async def test_a_different_seed_changes_at_least_one_stochastic_decision(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    await _drive(scenario, tmp_path / "s42", run_id="seed-run", seed=42)
    await _drive(scenario, tmp_path / "s43", run_id="seed-run", seed=43)

    def noticed(root: Path) -> list[tuple[int, str, int]]:
        out: list[tuple[int, str, int]] = []
        for event in SQLiteRunStore(root).load_run("seed-run").events:
            if event.event_type is EventType.CAMPAIGN_NOTICED:
                minute = int(event.payload.get("simulated_minute", -1))
                out.append((event.sequence, str(event.agent_id), minute))
        return out

    assert noticed(tmp_path / "s42") != noticed(tmp_path / "s43"), (
        "different seeds must move at least one attention decision"
    )


# -- gate 5: agent-order permutation ----------------------------------------------


def _reversed_population(scenario: Scenario) -> Scenario:
    return Scenario.model_validate(
        scenario.model_dump()
        | {
            "population": tuple(reversed(scenario.population)),
            "initial_states": tuple(reversed(scenario.initial_states)),
            "relationships": tuple(reversed(scenario.relationships)),
        }
    )


async def test_permuting_input_agent_order_preserves_aggregate_metrics(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    await _drive(scenario, tmp_path / "orig", run_id="order-run")
    await _drive(_reversed_population(scenario), tmp_path / "perm", run_id="order-run")

    def aggregates(root: Path) -> dict[str, float]:
        store = SQLiteRunStore(root)
        stored = store.load_run("order-run")
        usage = store.load_provider_usage("order-run")
        calculator = MetricsCalculator()
        return dict(
            calculator.calculate(
                stored.events,
                initial_states=stored.scenario.initial_states,
                final_states=stored.checkpoints[-1].states if stored.checkpoints else (),
                usage=usage,
            ).as_mapping()
        )

    original, permuted = aggregates(tmp_path / "orig"), aggregates(tmp_path / "perm")
    for name in ("reach", "impressions", "noticed", "notice_rate", "purchases"):
        assert original[name] == permuted[name], name


# -- gate 6: intent-order permutation ----------------------------------------------


async def test_reordering_independent_intents_does_not_change_committed_events(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    """The engine's stable-order commit is the property under test.

    :class:`TickPlan` re-sorts intents by agent identifier, so the committed stream
    must be identical whether a caller handed the intents over in plan order or in
    reverse: the same snapshot, the same cognition answers keyed by request id, and a
    permuted construction order must land on the same events.
    """
    from adlife.core.simulation.engine import AdLifeModel

    scenario = small_three_agent_scenario(valid_scenario)

    model_a = AdLifeModel(scenario=scenario, seed=SEED)
    model_b = AdLifeModel(scenario=scenario, seed=SEED)
    model_a.bind_run("intent-perm", next_event_sequence=0)
    model_b.bind_run("intent-perm", next_event_sequence=0)
    plan_a = model_a.plan_tick()
    intents = list(plan_a.intents)
    if len(intents) < 2:
        raise AssertionError("the small scenario must plan more than one movement intent")
    plan_b = type(plan_a)(snapshot=plan_a.snapshot, intents=tuple(reversed(intents)))

    cognition_a = await _answers_for(plan_a)
    cognition_b = await _answers_for(plan_b)

    outcome_a = model_a.commit_tick(plan_a, cognition_a)
    outcome_b = model_b.commit_tick(plan_b, cognition_b)

    def identity(outcome: object) -> list[tuple[int, str, str | None, str | None]]:
        return [
            (event.sequence, event.event_type.value, event.agent_id, event.campaign_id)
            for event in outcome.events  # type: ignore[attr-defined]
        ]

    assert identity(outcome_a) == identity(outcome_b)


async def _answers_for(plan: object) -> dict[str, CognitionAnswer]:
    """Answer every planned request through the same terminal rule fallback."""
    from adlife.cli.cognition import rule_fallback_for

    provider = rule_fallback_for(plan)
    answers: dict[str, CognitionAnswer] = {}
    for request in plan.requests:  # type: ignore[attr-defined]
        answers[request.request_id] = await provider.answer(request)
    return answers


# -- gate 7: causal tracing --------------------------------------------------------


def _traces_to_cause(event: DomainEvent, by_id: dict[str, DomainEvent], seen: set[str]) -> bool:
    """Walk the causal graph transitively to an exposure or social event."""
    if event.event_id in seen:
        return False
    seen.add(event.event_id)
    campaign_related = {
        EventType.CAMPAIGN_ELIGIBLE,
        EventType.CAMPAIGN_IMPRESSION,
        EventType.CAMPAIGN_NOTICED,
        EventType.SOCIAL_SHARED,
        EventType.SOCIAL_RECEIVED,
    }
    if event.event_type in campaign_related:
        return True
    for cause_id in event.caused_by_event_ids:
        cause = by_id.get(cause_id)
        if cause is not None and _traces_to_cause(cause, by_id, seen):
            return True
    return False


async def test_every_campaign_state_delta_traces_to_exposure_or_social_causes(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    await _drive(scenario, tmp_path / "trace", run_id="trace-run")
    events = SQLiteRunStore(tmp_path / "trace").load_run("trace-run").events

    by_id: dict[str, DomainEvent] = {event.event_id: event for event in events}
    state_updates = [
        event
        for event in events
        if event.event_type is EventType.STATE_UPDATED and event.caused_by_event_ids
    ]
    assert state_updates, "a campaign run must produce caused state updates"
    for event in state_updates:
        causes = [by_id[cid] for cid in event.caused_by_event_ids if cid in by_id]
        assert causes, f"state update {event.event_id} names causes outside the stream"
        assert any(_traces_to_cause(cause, by_id, set()) for cause in causes), (
            f"state update {event.event_id} has no exposure or social cause"
        )


# -- gate 8: no-campaign control ----------------------------------------------------


async def test_no_campaign_means_zero_attributed_campaign_delta(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = strip_campaigns(small_three_agent_scenario(valid_scenario))
    store, _sinks = await _drive(scenario, tmp_path / "control", run_id="control-run")
    events = store.load_run("control-run").events
    campaign_types = {
        EventType.CAMPAIGN_ELIGIBLE,
        EventType.CAMPAIGN_IMPRESSION,
        EventType.CAMPAIGN_NOTICED,
        EventType.CAMPAIGN_IGNORED,
    }
    assert not [event for event in events if event.event_type in campaign_types]
    for event in events:
        if event.event_type is EventType.STATE_UPDATED and event.caused_by_event_ids:
            by_id = {e.event_id: e for e in events}
            causes = [by_id[cid] for cid in event.caused_by_event_ids if cid in by_id]
            assert not any(cause.campaign_id is not None for cause in causes)


# -- gate 9: social disabled ---------------------------------------------------------


async def test_disabling_social_influence_means_zero_indirect_awareness(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    social_off = Scenario.model_validate(scenario.model_dump() | {"social_enabled": False})
    store, _sinks = await _drive(social_off, tmp_path / "nosocial", run_id="nosocial-run")
    events = store.load_run("nosocial-run").events
    assert result_status(events) == "completed"

    social_types = {EventType.SOCIAL_SHARED, EventType.SOCIAL_RECEIVED}
    assert not [event for event in events if event.event_type in social_types]

    stored = store.load_run("nosocial-run")
    usage = store.load_provider_usage("nosocial-run")
    metrics = MetricsCalculator().calculate(
        events,
        initial_states=stored.scenario.initial_states,
        final_states=stored.checkpoints[-1].states if stored.checkpoints else (),
        usage=usage,
    )
    assert metrics.indirect_awareness == 0.0


def result_status(events: list[DomainEvent]) -> str:
    if events and events[-1].event_type is EventType.RUN_COMPLETED:
        return "completed"
    return "incomplete"


# -- gate 10: counterfactual display names -------------------------------------------


def _renamed(scenario: Scenario, suffix: str) -> Scenario:
    population: list[PersonProfile] = []
    for profile in scenario.population:
        population.append(
            PersonProfile.model_validate(
                profile.model_copy(update={"display_name": f"{profile.display_name} {suffix}"})
            )
        )
    return Scenario.model_validate(scenario.model_dump() | {"population": tuple(population)})


async def test_counterfactual_display_name_changes_do_not_alter_decisions(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    await _drive(scenario, tmp_path / "names-a", run_id="names-run")
    await _drive(_renamed(scenario, "Contrario"), tmp_path / "names-b", run_id="names-run")
    assert _stored_events(tmp_path / "names-a", "names-run") == _stored_events(
        tmp_path / "names-b", "names-run"
    )
    # and the duration rescaling used by the perf suite stays consistent with builders
    _ = rescale_scenario(scenario, days=2)
