"""The commit pipeline: nine documented stages in one stable order.

Task 12 wires the pure stage modules - movement, exposure, attention, cognition,
rule-bounded state change, memory, social propagation, the purchase proxy and the daily
reflection - into the engine, so a commit is one atomic, ordered, replayable step. The
tests here pin the ORDER and the DETERMINISM; the stage modules' own suites already pin
each stage's arithmetic.
"""

from __future__ import annotations

import json

import pytest

from adlife.adapters.cognition.rules import RULE_MODEL_ID, rule_cognition_result
from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.results import SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import CognitionAnswer, ProviderUsage
from adlife.core.ports.run_store import RunCheckpoint
from adlife.core.simulation.decision import evaluate_rule_response
from adlife.core.simulation.engine import (
    AdLifeModel,
    MissingCognitionAnswer,
    RunAlreadyBound,
)
from adlife.core.simulation.movement import TickPlan

_STAGE_FAMILY: tuple[tuple[str, ...], ...] = (
    ("agent.location_changed", "agent.activity_changed"),
    ("campaign.eligible",),
    ("campaign.impression",),
    ("campaign.noticed", "campaign.ignored"),
    ("cognition.completed", "cognition.fallback"),
    ("agent.state_updated",),
    ("memory.created",),
)


def _stage_families(events: tuple[DomainEvent, ...]) -> list[str]:
    """Collapse the stream into the ordered stage families it visits."""
    families: list[str] = []
    for event in events:
        family = next((names for names in _STAGE_FAMILY if event.event_type in names), None)
        label = family[0] if family else str(event.event_type)
        if not families or families[-1] != label:
            families.append(label)
    return families


def _rule_answers(plan: TickPlan) -> dict[str, CognitionAnswer]:
    """Answer every planned request with the documented rule formula.

    This is exactly what a rules-mode run resolves: the same projected state the planner
    allocated, the same formula, no provider. The runner composes the same mapping from
    the cognition service; tests build it directly so the pipeline is pinned without an
    adapter in the loop.
    """
    by_request = {request.request_id: request for request in plan.requests}
    answers: dict[str, CognitionAnswer] = {}
    for decision in plan.attention:
        terminal = decision.events[2]
        if terminal.event_type is not EventType.CAMPAIGN_NOTICED:
            continue
        request = by_request[terminal.event_id]
        opportunity = decision.opportunity
        response = evaluate_rule_response(
            opportunity.profile,
            opportunity.state,
            opportunity.campaign,
            opportunity.placement,
        )
        answers[request.request_id] = CognitionAnswer(
            result=rule_cognition_result(request, response),
            usage=ProviderUsage(
                provider_kind="rule",
                model_id=RULE_MODEL_ID,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                cache_hit=False,
            ),
        )
    return answers


def _canonical(events: tuple[DomainEvent, ...]) -> str:
    return json.dumps(
        [
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "event_type": str(event.event_type),
                "agent_id": event.agent_id,
                "campaign_id": event.campaign_id,
                "payload": dict(event.payload),
                "caused_by": list(event.caused_by_event_ids),
            }
            for event in events
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


def _two_day(scenario: Scenario) -> Scenario:
    return scenario.model_copy(update={"days": 2})


def _full_world(scenario: Scenario):
    """Extend the contract fixture's four-zone world to the documented ten.

    The built-in office-worker routine visits ``retail-center`` and ``cafe`` as literal
    zones, so a whole-day drive needs the documented world, not the four-zone contract
    fixture. The commute route is unchanged; ``online`` needs no route because it is
    where a phone-check happens.
    """
    from adlife.core.domain.world import Route, Zone

    world = scenario.world
    zones = tuple(
        [
            *world.zones,
            Zone(zone_id="retail-center", name="Retail Center", kind="retail"),
            Zone(zone_id="cafe", name="Cafe", kind="social"),
        ]
    )
    routes = tuple(
        [
            *world.routes,
            Route(
                route_id="retail-commute",
                source_zone="office",
                target_zone="retail-center",
                transit_zone="highway-north",
                travel_minutes=30,
            ),
            Route(
                route_id="retail-social",
                source_zone="retail-center",
                target_zone="cafe",
                travel_minutes=15,
            ),
            Route(
                route_id="cafe-home",
                source_zone="cafe",
                target_zone="home-north",
                travel_minutes=15,
            ),
        ]
    )
    return scenario.model_copy(
        update={
            "world": world.model_copy(update={"zones": zones, "routes": routes}),
        }
    )


def test_one_rule_tick_runs_the_documented_stages_in_order(
    valid_scenario: Scenario,
) -> None:
    model = AdLifeModel(scenario=_full_world(valid_scenario), seed=42, run_id="run-pipeline")
    plan = model.plan_tick()
    while not plan.attention and not model.clock.finished:
        model.clock.advance()
        plan = model.plan_tick()
    assert plan.attention, "the documented day must reach a commute with an opportunity"
    outcome = model.commit_tick(plan, cognition=_rule_answers(plan))

    families = _stage_families(outcome.events)
    movement_first = families.index("agent.location_changed")
    eligible = families.index("campaign.eligible")
    impression = families.index("campaign.impression")
    terminal = families.index("campaign.noticed")
    cognition = families.index("cognition.completed")
    state = families.index("agent.state_updated")
    memory = families.index("memory.created")
    assert movement_first < eligible < impression < terminal
    assert terminal < cognition < state < memory


def test_every_event_names_only_earlier_causes_of_its_own_run(
    valid_scenario: Scenario,
) -> None:
    model = AdLifeModel(
        scenario=_full_world(_two_day(valid_scenario)), seed=42, run_id="run-causal"
    )
    known: set[str] = set()
    while not model.clock.finished:
        plan = model.plan_tick()
        outcome = model.commit_tick(plan, cognition=_rule_answers(plan))
        for event in outcome.events:
            assert set(event.caused_by_event_ids) <= known
            assert event.run_id == "run-causal"
            assert event.event_id == f"run-causal:event-{event.sequence:08d}"
            known.add(event.event_id)
        model.clock.advance()


def test_the_final_tick_of_a_run_reflects_then_completes(
    valid_scenario: Scenario,
) -> None:
    model = AdLifeModel(scenario=_full_world(valid_scenario), seed=42, run_id="run-complete")
    outcome = None
    while not model.clock.finished:
        plan = model.plan_tick()
        outcome = model.commit_tick(plan, cognition=_rule_answers(plan))
        model.clock.advance()
    assert outcome is not None
    assert outcome.ends_simulated_day is True
    assert outcome.events[-1].event_type is EventType.RUN_COMPLETED
    reflected = [event for event in outcome.events if event.event_type is EventType.DAY_REFLECTED]
    assert reflected, "the last tick of a day must carry the daily reflection"

    result = model.complete()
    assert isinstance(result, SimulationResult)
    assert result.status == "completed"
    assert result.final_minute == 1440
    assert result.event_count == model._next_event_sequence


def test_a_day_boundary_checkpoint_resumes_into_identical_events(
    valid_scenario: Scenario,
) -> None:
    scenario = _full_world(_two_day(valid_scenario))
    model = AdLifeModel(scenario=scenario, seed=42, run_id="run-resume")
    boundary: RunCheckpoint | None = None
    resumed: AdLifeModel | None = None
    original_day_two: list[str] = []
    resumed_day_two: list[str] = []
    while not model.clock.finished:
        plan = model.plan_tick()
        outcome = model.commit_tick(plan, cognition=_rule_answers(plan))
        model.clock.advance()
        if outcome.ends_simulated_day and boundary is None:
            boundary = model.checkpoint()
            resumed = AdLifeModel.restore(scenario=scenario, seed=42, checkpoint=boundary)
            continue
        if boundary is not None:
            original_day_two.append(_canonical(outcome.events))
    assert boundary is not None and resumed is not None
    assert resumed.clock.current_minute == boundary.simulated_minute
    while not resumed.clock.finished:
        plan = resumed.plan_tick()
        outcome = resumed.commit_tick(plan, cognition=_rule_answers(plan))
        resumed.clock.advance()
        resumed_day_two.append(_canonical(outcome.events))

    assert original_day_two == resumed_day_two


def test_permuted_population_order_produces_an_identical_stream(
    valid_scenario: Scenario,
) -> None:
    first = AdLifeModel(scenario=_full_world(valid_scenario), seed=42, run_id="run-order")
    second = AdLifeModel(scenario=_full_world(valid_scenario), seed=42, run_id="run-order")
    first_stream: list[str] = []
    second_stream: list[str] = []
    for model, sink in ((first, first_stream), (second, second_stream)):
        while not model.clock.finished:
            plan = model.plan_tick()
            outcome = model.commit_tick(plan, cognition=_rule_answers(plan))
            sink.append(_canonical(outcome.events))
            model.clock.advance()
    assert first_stream == second_stream


def test_a_missing_cognition_answer_is_refused_before_anything_changes(
    valid_scenario: Scenario,
) -> None:
    model = AdLifeModel(scenario=_full_world(valid_scenario), seed=42, run_id="run-missing")
    plan = model.plan_tick()
    while not plan.requests and not model.clock.finished:
        model.clock.advance()
        plan = model.plan_tick()
    assert plan.requests, "the documented day must reach a cognition request"
    before = {agent_id: agent.state for agent_id, agent in model.agent_by_id.items()}
    with pytest.raises(MissingCognitionAnswer):
        model.commit_tick(plan, cognition={})
    after = {agent_id: agent.state for agent_id, agent in model.agent_by_id.items()}
    assert after == before
    assert model._next_event_sequence == 0


def test_start_events_bind_the_run_and_mint_run_started_once(
    valid_scenario: Scenario,
) -> None:
    model = AdLifeModel(scenario=valid_scenario, seed=42)
    (started,) = model.start_events("run-started")
    assert started.event_type is EventType.RUN_STARTED
    assert started.sequence == 0
    assert started.event_id == "run-started:event-00000000"
    with pytest.raises(RunAlreadyBound):
        model.start_events("run-started")
