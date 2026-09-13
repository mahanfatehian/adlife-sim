import os
import subprocess
import sys
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from adlife.core.domain.events import EventType
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Relationship, Route, World, Zone
from adlife.core.simulation import engine, movement


def _world() -> World:
    return World(
        world_id="movement-world",
        zones=(
            Zone(zone_id="home-north", name="North Home", kind="home"),
            Zone(zone_id="office", name="Office", kind="work"),
            Zone(zone_id="highway-north", name="North Highway", kind="highway"),
            Zone(zone_id="retail-center", name="Retail Center", kind="retail"),
            Zone(zone_id="cafe", name="Cafe", kind="social"),
        ),
        routes=(
            Route(
                route_id="highway-north",
                source_zone="home-north",
                target_zone="office",
                transit_zone="highway-north",
                travel_minutes=30,
            ),
            Route(
                route_id="office-retail",
                source_zone="office",
                target_zone="retail-center",
                travel_minutes=15,
            ),
        ),
    )


def _snapshot(
    profile: PersonProfile,
    state: ConsumerState,
    *,
    simulated_minute: int = 0,
    version: int = 0,
) -> movement.Snapshot:
    return movement.Snapshot(
        agents={profile.agent_id: (profile, state)},
        simulated_minute=simulated_minute,
        run_id="run-movement",
        next_event_sequence=7,
        version=version,
    )


def _two_agent_scenario(valid_scenario: Scenario) -> Scenario:
    first = valid_scenario.population[0]
    second = first.model_copy(update={"agent_id": "person-002", "display_name": "Mina 002"})
    first_state = valid_scenario.initial_states[0]
    second_state = first_state.model_copy(update={"agent_id": second.agent_id})
    data = valid_scenario.model_dump(mode="python")
    data["population"] = (second, first)
    data["initial_states"] = (second_state, first_state)
    data["relationships"] = (
        Relationship(
            source_id=first.agent_id,
            target_id=second.agent_id,
            kind="friend",
            strength=0.8,
        ),
    )
    return Scenario.model_validate(data)


def _advance_to(model: engine.AdLifeModel, simulated_minute: int) -> None:
    clock = model.clock
    while clock.current_minute < simulated_minute:
        clock.advance()


def test_snapshot_is_a_detached_immutable_plain_domain_mapping(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    source = {valid_profile.agent_id: (valid_profile, consumer_state)}

    snapshot = movement.Snapshot(
        agents=source,
        simulated_minute=0,
        run_id="run-movement",
        next_event_sequence=0,
        version=0,
    )
    source.clear()

    assert snapshot.agents[valid_profile.agent_id] == (valid_profile, consumer_state)
    assert tuple(snapshot.agents) == (valid_profile.agent_id,)
    with pytest.raises(TypeError):
        snapshot.agents[valid_profile.agent_id] = (valid_profile, consumer_state)
    with pytest.raises(FrozenInstanceError):
        snapshot.simulated_minute = 15


def test_snapshot_fingerprint_is_independent_of_agent_input_order(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    second_profile = valid_profile.model_copy(
        update={"agent_id": "person-002", "display_name": "Mina 002"}
    )
    second_state = consumer_state.model_copy(update={"agent_id": "person-002"})
    forward = {
        valid_profile.agent_id: (valid_profile, consumer_state),
        second_profile.agent_id: (second_profile, second_state),
    }
    reverse = dict(reversed(tuple(forward.items())))

    first = movement.Snapshot(agents=forward, simulated_minute=15)
    second = movement.Snapshot(agents=reverse, simulated_minute=15)

    assert first.fingerprint == second.fingerprint
    assert tuple(first.agents) == ("person-001", "person-002")
    assert tuple(second.agents) == ("person-001", "person-002")


def test_snapshot_fingerprint_is_stable_across_python_hash_seeds() -> None:
    script = (
        "from adlife.core.domain.state import ConsumerState; "
        "from adlife.core.simulation.movement import Snapshot; "
        "from adlife.core.simulation.population import generate_population; "
        "profile = generate_population(1, 42, 'fa-IR')[0]; "
        "state = ConsumerState(agent_id=profile.agent_id, location=profile.home_zone, "
        "activity='sleep', mood=0.0, fatigue=0.0, "
        "brand_sentiment=profile.initial_brand_sentiment, recall_strength=0.0, "
        "purchase_intention=0.0, cognition_budget_remaining=6); "
        "print(Snapshot(agents={profile.agent_id: (profile, state)}).fingerprint)"
    )
    fingerprints = []
    for hash_seed in ("1", "2"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        fingerprints.append(completed.stdout.strip())

    assert fingerprints[0] == fingerprints[1]


def test_movement_intent_is_immutable() -> None:
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="office",
        activity="work",
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        intent.to_zone = "retail-center"


def test_tick_plan_rejects_a_non_snapshot_contract() -> None:
    with pytest.raises(TypeError, match="Snapshot"):
        movement.TickPlan(snapshot="not-a-snapshot", intents=())


def test_tick_plan_rejects_a_non_movement_intent(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)

    with pytest.raises(TypeError, match="MovementIntent"):
        movement.TickPlan(snapshot=snapshot, intents=("not-an-intent",))


def test_tick_plan_rejects_a_missing_agent_intent(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(
        scenario=_two_agent_scenario(valid_scenario),
        seed=42,
        run_id="run-plan-missing",
    )
    snapshot = model.snapshot()
    only_first = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="sleep",
    )

    with pytest.raises(ValueError, match="exactly match snapshot agent IDs"):
        movement.TickPlan(snapshot=snapshot, intents=(only_first,))


def test_tick_plan_rejects_an_extra_agent_intent(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)
    first = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="sleep",
    )
    extra = first.model_copy(update={"agent_id": "person-002"})

    with pytest.raises(ValueError, match="exactly match snapshot agent IDs"):
        movement.TickPlan(snapshot=snapshot, intents=(first, extra))


def test_tick_plan_rejects_duplicate_agent_intents(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(
        scenario=_two_agent_scenario(valid_scenario),
        seed=42,
        run_id="run-plan-duplicate",
    )
    snapshot = model.snapshot()
    first = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="sleep",
    )
    second = first.model_copy(update={"agent_id": "person-002"})

    with pytest.raises(ValueError, match="duplicate movement intent"):
        movement.TickPlan(snapshot=snapshot, intents=(first, second, first))


def test_tick_plan_canonicalizes_intents_by_agent_id(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(
        scenario=_two_agent_scenario(valid_scenario),
        seed=42,
        run_id="run-plan-order",
    )
    snapshot = model.snapshot()
    first = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="sleep",
    )
    second = first.model_copy(update={"agent_id": "person-002"})

    plan = movement.TickPlan(snapshot=snapshot, intents=(second, first))

    assert tuple(intent.agent_id for intent in plan.intents) == (
        "person-001",
        "person-002",
    )


def test_tick_outcome_rejects_a_non_snapshot_contract() -> None:
    with pytest.raises(TypeError, match="Snapshot"):
        movement.TickOutcome(snapshot="not-a-snapshot", events=())


def test_tick_outcome_rejects_a_non_domain_event(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)

    with pytest.raises(TypeError, match="DomainEvent"):
        movement.TickOutcome(snapshot=snapshot, events=("not-an-event",))


def test_tick_outcome_detaches_and_canonicalizes_events_by_sequence(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="office",
        activity="work",
    )
    canonical = movement.resolve_movement((intent,), _world(), snapshot)
    source = list(reversed(canonical))

    outcome = movement.TickOutcome(snapshot=snapshot, events=source)
    source.clear()

    assert outcome.events == canonical


def test_agent_cannot_teleport_between_unconnected_zones(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="retail-center",
        activity="shopping",
    )

    with pytest.raises(movement.InvalidMovement, match="no route"):
        movement.resolve_movement((intent,), _world(), snapshot)


def test_agent_cannot_cross_two_declared_routes_in_one_tick() -> None:
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="retail-center",
        activity="shopping",
    )

    with pytest.raises(movement.InvalidMovement, match="no route"):
        movement.resolve_movement((intent,), _world())


def test_movement_requires_exactly_one_matching_route() -> None:
    world = _world()
    duplicate_path = Route(
        route_id="alternate-office",
        source_zone="home-north",
        target_zone="office",
        travel_minutes=15,
    )
    ambiguous_world = world.model_copy(update={"routes": (*world.routes, duplicate_path)})
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="office",
        activity="work",
    )

    with pytest.raises(movement.InvalidMovement, match="multiple routes"):
        movement.resolve_movement((intent,), ambiguous_world)


def test_declared_route_allows_endpoint_or_transit_movement() -> None:
    intents = (
        movement.MovementIntent(
            agent_id="person-001",
            from_zone="home-north",
            to_zone="office",
            activity="work",
        ),
        movement.MovementIntent(
            agent_id="person-002",
            from_zone="office",
            to_zone="highway-north",
            activity="commute",
            route_id="highway-north",
        ),
    )

    events = movement.resolve_movement(intents, _world())
    location_events = tuple(
        event for event in events if event.event_type == EventType.LOCATION_CHANGED
    )

    assert len(location_events) == 2
    assert all(event.payload["route_id"] == "highway-north" for event in location_events)


def test_same_zone_activity_change_does_not_emit_location_event(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="breakfast",
    )

    events = movement.resolve_movement((intent,), _world(), snapshot)

    assert tuple(event.event_type for event in events) == (EventType.ACTIVITY_CHANGED,)


def test_same_zone_route_set_emits_a_canonical_location_event(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="sleep",
        route_id="highway-north",
    )

    events = movement.resolve_movement((intent,), _world(), snapshot)

    assert tuple(event.event_type for event in events) == (EventType.LOCATION_CHANGED,)
    assert dict(events[0].payload) == {
        "from_route_id": None,
        "from_zone": "home-north",
        "route_id": "highway-north",
        "to_route_id": "highway-north",
        "to_zone": "home-north",
    }


def test_same_zone_route_clear_emits_a_canonical_location_event(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    routed_state = consumer_state.model_copy(update={"current_route_id": "highway-north"})
    snapshot = _snapshot(valid_profile, routed_state)
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="sleep",
    )

    events = movement.resolve_movement((intent,), _world(), snapshot)

    assert tuple(event.event_type for event in events) == (EventType.LOCATION_CHANGED,)
    assert dict(events[0].payload) == {
        "from_route_id": "highway-north",
        "from_zone": "home-north",
        "route_id": None,
        "to_route_id": None,
        "to_zone": "home-north",
    }


def test_location_event_payload_can_replay_zone_and_route_transition(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="office",
        activity="work",
    )

    events = movement.resolve_movement((intent,), _world(), snapshot)
    location_event = next(
        event for event in events if event.event_type == EventType.LOCATION_CHANGED
    )

    assert dict(location_event.payload) == {
        "from_route_id": None,
        "from_zone": "home-north",
        "route_id": "highway-north",
        "to_route_id": "highway-north",
        "to_zone": "office",
    }
    replayed = consumer_state.model_copy(
        update={
            "location": location_event.payload["to_zone"],
            "current_route_id": location_event.payload["to_route_id"],
        }
    )
    assert (replayed.location, replayed.current_route_id) == (
        "office",
        "highway-north",
    )


def test_route_only_event_order_is_independent_of_intent_order(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    second_profile = valid_profile.model_copy(
        update={"agent_id": "person-002", "display_name": "Mina 002"}
    )
    second_state = consumer_state.model_copy(
        update={
            "agent_id": "person-002",
            "location": "office",
            "current_route_id": "office-retail",
        }
    )
    snapshot = movement.Snapshot(
        agents={
            second_profile.agent_id: (second_profile, second_state),
            valid_profile.agent_id: (valid_profile, consumer_state),
        },
        run_id="run-movement",
        next_event_sequence=11,
    )
    intents = (
        movement.MovementIntent(
            agent_id="person-002",
            from_zone="office",
            to_zone="office",
            activity="sleep",
        ),
        movement.MovementIntent(
            agent_id="person-001",
            from_zone="home-north",
            to_zone="home-north",
            activity="sleep",
            route_id="highway-north",
        ),
    )

    forward = movement.resolve_movement(intents, _world(), snapshot)
    reverse = movement.resolve_movement(tuple(reversed(intents)), _world(), snapshot)

    assert forward == reverse
    assert tuple((event.event_type, event.agent_id) for event in forward) == (
        (EventType.LOCATION_CHANGED, "person-001"),
        (EventType.LOCATION_CHANGED, "person-002"),
    )
    assert tuple(event.event_id for event in forward) == (
        "run-movement:event-00000011",
        "run-movement:event-00000012",
    )


def test_snapshot_location_must_match_intent_origin(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = _snapshot(valid_profile, consumer_state)
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="office",
        to_zone="retail-center",
        activity="shopping",
    )

    with pytest.raises(movement.InvalidMovement, match="snapshot location"):
        movement.resolve_movement((intent,), _world(), snapshot)


def test_reordering_intents_does_not_change_events() -> None:
    intents = (
        movement.MovementIntent(
            agent_id="person-002",
            from_zone="office",
            to_zone="retail-center",
            activity="shopping",
        ),
        movement.MovementIntent(
            agent_id="person-001",
            from_zone="home-north",
            to_zone="office",
            activity="work",
        ),
    )

    forward = movement.resolve_movement(intents, _world())
    reverse = movement.resolve_movement(tuple(reversed(intents)), _world())

    assert forward == reverse
    assert [(event.event_type, event.agent_id) for event in forward] == [
        (EventType.ACTIVITY_CHANGED, "person-001"),
        (EventType.ACTIVITY_CHANGED, "person-002"),
        (EventType.LOCATION_CHANGED, "person-001"),
        (EventType.LOCATION_CHANGED, "person-002"),
    ]
    assert [event.sequence for event in forward] == [0, 1, 2, 3]
    assert [event.event_id for event in forward] == [
        "movement:event-00000000",
        "movement:event-00000001",
        "movement:event-00000002",
        "movement:event-00000003",
    ]


def test_consumer_agent_plans_from_snapshot_routine_without_mutating_state(
    valid_scenario: Scenario,
) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42)
    _advance_to(model, 480)
    agent = model.agent_by_id["person-001"]
    live_state = agent.state
    snapshot_state = live_state.model_copy(update={"location": "office", "activity": "work"})
    snapshot = movement.Snapshot(
        agents={agent.profile.agent_id: (agent.profile, snapshot_state)},
        simulated_minute=480,
    )

    intent = agent.plan_movement(snapshot)

    assert intent == movement.MovementIntent(
        agent_id="person-001",
        from_zone="office",
        to_zone="highway-north",
        activity="commute",
        route_id="highway-north",
    )
    assert agent.state is live_state
    assert agent.state.location == "home-north"
    assert snapshot.agents["person-001"][1] is snapshot_state


def test_adlife_model_uses_seeded_mesa_ownership_and_exports_plain_snapshot(
    valid_scenario: Scenario,
) -> None:
    scenario = _two_agent_scenario(valid_scenario)

    first = engine.AdLifeModel(scenario=scenario, seed=42)
    second = engine.AdLifeModel(scenario=scenario, seed=42)
    snapshot = first.snapshot()

    assert first.random.random() == second.random.random()
    assert tuple(first.agent_by_id) == ("person-001", "person-002")
    assert tuple(agent.unique_id for agent in first.agents) == (
        "person-001",
        "person-002",
    )
    assert set(first.agents) == set(first.agent_by_id.values())
    assert tuple(snapshot.agents) == ("person-001", "person-002")
    assert all(
        isinstance(profile, PersonProfile) and isinstance(state, ConsumerState)
        for profile, state in snapshot.agents.values()
    )


def test_plan_tick_is_stable_by_agent_id(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(scenario=_two_agent_scenario(valid_scenario), seed=42)
    _advance_to(model, 480)

    plan = model.plan_tick()

    assert tuple(intent.agent_id for intent in plan.intents) == (
        "person-001",
        "person-002",
    )
    assert tuple(plan.snapshot.agents) == ("person-001", "person-002")


def test_commit_tick_updates_state_and_returns_canonical_events(
    valid_scenario: Scenario,
) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42, run_id="run-movement")
    _advance_to(model, 480)
    plan = model.plan_tick()

    outcome = model.commit_tick(plan, cognition={})
    state = model.snapshot().agents["person-001"][1]

    assert state.location == "highway-north"
    assert state.activity == "commute"
    assert state.current_route_id == "highway-north"
    assert tuple(event.event_type for event in outcome.events) == (
        EventType.ACTIVITY_CHANGED,
        EventType.LOCATION_CHANGED,
    )
    assert tuple(event.event_id for event in outcome.events) == (
        "run-movement:event-00000000",
        "run-movement:event-00000001",
    )
    assert outcome.snapshot == model.snapshot()


def test_commit_tick_persists_the_inferred_route(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42, run_id="run-inferred")
    snapshot = model.snapshot()
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="office",
        activity="work",
    )

    outcome = model.commit_tick(
        movement.TickPlan(snapshot=snapshot, intents=(intent,)),
        cognition={},
    )

    state = outcome.snapshot.agents["person-001"][1]
    location_event = next(
        event for event in outcome.events if event.event_type == EventType.LOCATION_CHANGED
    )
    assert state.current_route_id == "highway-north"
    assert location_event.payload["to_route_id"] == state.current_route_id


def test_commit_tick_is_atomic_when_a_later_agent_intent_is_invalid(
    valid_scenario: Scenario,
) -> None:
    model = engine.AdLifeModel(
        scenario=_two_agent_scenario(valid_scenario),
        seed=42,
        run_id="run-atomic",
    )
    original = model.snapshot()
    valid_first = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="breakfast",
    )
    invalid_second = movement.MovementIntent(
        agent_id="person-002",
        from_zone="home-north",
        to_zone="online",
        activity="phone-check",
    )
    plan = movement.TickPlan(
        snapshot=original,
        intents=(invalid_second, valid_first),
    )

    assert tuple(intent.agent_id for intent in plan.intents) == (
        "person-001",
        "person-002",
    )

    with pytest.raises(movement.InvalidMovement, match="no route"):
        model.commit_tick(plan, cognition={})

    after = model.snapshot()
    assert tuple(after.agents) == tuple(original.agents)
    assert tuple(state for _, state in after.agents.values()) == tuple(
        state for _, state in original.agents.values()
    )
    assert after.fingerprint == original.fingerprint
    assert after.version == original.version
    assert after.next_event_sequence == original.next_event_sequence


def test_commit_tick_defensively_rejects_a_corrupted_partial_plan(
    valid_scenario: Scenario,
) -> None:
    model = engine.AdLifeModel(
        scenario=_two_agent_scenario(valid_scenario),
        seed=42,
        run_id="run-defensive-plan",
    )
    original = model.snapshot()
    plan = model.plan_tick()
    object.__setattr__(plan, "intents", plan.intents[:1])

    with pytest.raises(ValueError, match="exactly match snapshot agent IDs"):
        model.commit_tick(plan, cognition={})

    assert model.snapshot() == original


def test_bypass_constructed_intent_is_revalidated_before_atomic_commit(
    valid_scenario: Scenario,
) -> None:
    model = engine.AdLifeModel(
        scenario=_two_agent_scenario(valid_scenario),
        seed=42,
        run_id="run-bypass-intent",
    )
    original = model.snapshot()
    original_live_states = {agent_id: agent.state for agent_id, agent in model.agent_by_id.items()}
    valid_first = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="breakfast",
    )
    valid_second = movement.MovementIntent(
        agent_id="person-002",
        from_zone="home-north",
        to_zone="home-north",
        activity="sleep",
    )
    malformed_second = movement.MovementIntent.model_construct(
        schema_version=1,
        agent_id="person-002",
        from_zone="home-north",
        to_zone="home-north",
        activity="not-an-activity",
        route_id=None,
    )
    corrupted_plan = movement.TickPlan(
        snapshot=original,
        intents=(valid_first, valid_second),
    )
    object.__setattr__(
        corrupted_plan,
        "intents",
        (valid_first, malformed_second),
    )

    with pytest.raises(ValidationError, match="activity"):
        model.commit_tick(corrupted_plan, cognition={})

    after = model.snapshot()
    assert all(
        model.agent_by_id[agent_id].state is original_state
        for agent_id, original_state in original_live_states.items()
    )
    assert tuple(after.agents) == tuple(original.agents)
    assert tuple(state for _, state in after.agents.values()) == tuple(
        state for _, state in original.agents.values()
    )
    assert after.fingerprint == original.fingerprint
    assert after.version == original.version
    assert after.next_event_sequence == original.next_event_sequence

    with pytest.raises(ValidationError, match="activity"):
        movement.TickPlan(
            snapshot=original,
            intents=(valid_first, malformed_second),
        )


def test_unbound_model_rejects_even_a_noop_commit(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42)
    original = model.snapshot()
    plan = model.plan_tick()

    with pytest.raises(RuntimeError, match="bind_run") as caught:
        model.commit_tick(plan, cognition={})

    assert isinstance(caught.value, engine.UnboundRun)
    assert model.snapshot() == original


def test_snapshot_aware_resolution_rejects_an_unbound_event_stream(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
) -> None:
    snapshot = movement.Snapshot(agents={"person-001": (valid_profile, consumer_state)})
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="home-north",
        activity="breakfast",
    )

    with pytest.raises(RuntimeError, match="bind_run") as caught:
        movement.resolve_movement((intent,), _world(), snapshot)

    assert isinstance(caught.value, engine.UnboundRun)


def test_construct_then_bind_stales_prior_plan_and_honors_sequence_start(
    valid_scenario: Scenario,
) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42)
    prebind_plan = model.plan_tick()
    bind_run = getattr(model, "bind_run", None)
    assert callable(bind_run)

    bind_run("run-task12", next_event_sequence=23)

    with pytest.raises(engine.StaleTickPlan, match="state changed between plan and commit"):
        model.commit_tick(prebind_plan, cognition={})

    snapshot = model.snapshot()
    plan = movement.TickPlan(
        snapshot=snapshot,
        intents=(
            movement.MovementIntent(
                agent_id="person-001",
                from_zone="home-north",
                to_zone="home-north",
                activity="breakfast",
            ),
        ),
    )
    outcome = model.commit_tick(plan, cognition={})

    assert tuple((event.run_id, event.sequence, event.event_id) for event in outcome.events) == (
        ("run-task12", 23, "run-task12:event-00000023"),
    )
    assert outcome.snapshot.next_event_sequence == 24


def test_independently_bound_models_mint_distinct_event_ids(
    valid_scenario: Scenario,
) -> None:
    models = (
        engine.AdLifeModel(scenario=valid_scenario, seed=42),
        engine.AdLifeModel(scenario=valid_scenario, seed=42),
    )
    event_ids: list[str] = []
    for model, run_id in zip(models, ("run-first", "run-second"), strict=True):
        bind_run = getattr(model, "bind_run", None)
        assert callable(bind_run)
        bind_run(run_id, next_event_sequence=0)
        snapshot = model.snapshot()
        plan = movement.TickPlan(
            snapshot=snapshot,
            intents=(
                movement.MovementIntent(
                    agent_id="person-001",
                    from_zone="home-north",
                    to_zone="home-north",
                    activity="breakfast",
                ),
            ),
        )
        event_ids.append(model.commit_tick(plan, cognition={}).events[0].event_id)

    assert event_ids == [
        "run-first:event-00000000",
        "run-second:event-00000000",
    ]
    assert len(set(event_ids)) == 2


def test_run_binding_cannot_be_replaced_after_commit(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42)
    bind_run = getattr(model, "bind_run", None)
    assert callable(bind_run)
    bind_run("run-original", next_event_sequence=0)
    model.commit_tick(model.plan_tick(), cognition={})
    committed = model.snapshot()

    with pytest.raises(RuntimeError, match="already bound") as caught:
        bind_run("run-replacement", next_event_sequence=9)

    assert isinstance(caught.value, engine.RunAlreadyBound)
    assert model.snapshot() == committed


@pytest.mark.parametrize(
    ("run_id", "next_event_sequence"),
    (("Bad-Run", 0), ("run-valid", -1), ("run-valid", True)),
)
def test_run_binding_rejects_invalid_identity_or_sequence(
    valid_scenario: Scenario,
    run_id: str,
    next_event_sequence: int,
) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42)
    original = model.snapshot()
    bind_run = getattr(model, "bind_run", None)
    assert callable(bind_run)

    with pytest.raises(ValueError):
        bind_run(run_id, next_event_sequence=next_event_sequence)

    assert model.snapshot() == original


def test_consumed_noop_plan_is_rejected_as_stale(valid_scenario: Scenario) -> None:
    model = engine.AdLifeModel(scenario=valid_scenario, seed=42, run_id="run-stale")
    stale = model.plan_tick()
    current = model.plan_tick()

    outcome = model.commit_tick(current, cognition={})

    assert outcome.events == ()
    with pytest.raises(engine.StaleTickPlan, match="state changed between plan and commit"):
        model.commit_tick(stale, cognition={})
