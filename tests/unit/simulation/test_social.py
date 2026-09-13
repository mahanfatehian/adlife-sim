from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

import pytest

from adlife.core.domain.campaign import (
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Price,
    TimeWindow,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Relationship
from adlife.core.simulation.engine import stable_event_id
from adlife.core.simulation.movement import Snapshot
from adlife.core.simulation.rng import RandomOracle


def _social_module() -> ModuleType:
    try:
        return import_module("adlife.core.simulation.social")
    except ModuleNotFoundError:
        pytest.fail("Task 8 social behavior is not implemented", pytrace=False)


def _traits(**overrides: float) -> ConsumerTraits:
    values: dict[str, float] = {
        "price_sensitivity": 0.5,
        "novelty_seeking": 1.0,
        "social_susceptibility": 1.0,
        "advertising_skepticism": 0.3,
        "mobile_attention": 0.8,
        "outdoor_attention": 0.5,
        "brand_loyalty": 0.4,
        "impulsivity": 0.3,
    }
    values.update(overrides)
    return ConsumerTraits(**values)


def _profile(agent_id: str, *, traits: ConsumerTraits | None = None) -> PersonProfile:
    return PersonProfile(
        agent_id=agent_id,
        display_name=f"Arman {agent_id[-3:]}",
        fictional=True,
        age=29,
        occupation="office-worker",
        income_band="middle",
        household_type="shared-apartment",
        home_zone="home-north",
        work_or_study_zone="office",
        interests=frozenset({"fitness", "technology"}),
        traits=traits or _traits(),
        initial_brand_sentiment=0.1,
        routine_template="office-worker",
    )


def _state(
    agent_id: str,
    *,
    location: str = "cafe",
    activity: str = "socializing",
    brand_sentiment: float = 1.0,
    social_proof: float = 0.0,
    recall_strength: float = 0.4,
    daily_reinforcement: float = 0.0,
    aware_campaign_ids: frozenset[str] = frozenset(),
) -> ConsumerState:
    return ConsumerState(
        agent_id=agent_id,
        location=location,
        activity=activity,
        mood=0.0,
        fatigue=0.1,
        brand_sentiment=brand_sentiment,
        recall_strength=recall_strength,
        purchase_intention=0.3,
        cognition_budget_remaining=6,
        daily_reinforcement=daily_reinforcement,
        social_proof=social_proof,
        aware_campaign_ids=aware_campaign_ids,
    )


def _snapshot(*pairs: tuple[PersonProfile, ConsumerState], simulated_minute: int = 600) -> Snapshot:
    return Snapshot(
        agents={profile.agent_id: (profile, state) for profile, state in pairs},
        simulated_minute=simulated_minute,
        run_id="run-social",
        next_event_sequence=0,
    )


def _noticed_event(
    agent_id: str,
    *,
    campaign_id: str = "campaign-phone",
    event_id: str | None = None,
    simulated_minute: int = 600,
    run_id: str = "run-social",
    event_type: EventType = EventType.CAMPAIGN_NOTICED,
) -> DomainEvent:
    return DomainEvent(
        event_id=event_id or f"run-social:noticed-{agent_id}-{campaign_id}",
        run_id=run_id,
        simulated_minute=simulated_minute,
        sequence=0,
        event_type=event_type,
        agent_id=agent_id,
        campaign_id=campaign_id,
        channel="mobile-feed",
        payload={"notice_probability": 0.9, "random_draw": 0.1, "placement_id": "a" * 64},
        source=EventSource.RULE,
    )


def _pair(agent_id: str, **state_overrides: Any) -> tuple[PersonProfile, ConsumerState]:
    return _profile(agent_id), _state(agent_id, **state_overrides)


@pytest.fixture
def valid_snapshot() -> Snapshot:
    return _snapshot(_pair("person-001"), _pair("person-002"))


@pytest.fixture
def noticed_events() -> tuple[DomainEvent, ...]:
    return (_noticed_event("person-001"),)


def _friendship(source_id: str, target_id: str, *, strength: float = 1.0) -> Relationship:
    return Relationship(source_id=source_id, target_id=target_id, kind="friend", strength=strength)


def _signals(*events: DomainEvent, value: float = 1.0) -> dict[str, float]:
    """The cognition share probability of each noticed event, keyed by event id."""
    return {event.event_id: value for event in events}


def _activity_event(
    agent_id: str,
    *,
    from_activity: str,
    to_activity: str,
    sequence: int = 0,
    simulated_minute: int = 600,
    run_id: str = "run-social",
) -> DomainEvent:
    """One canonical Task 6 activity movement event for the tick being planned."""
    return DomainEvent(
        event_id=stable_event_id(run_id, sequence),
        run_id=run_id,
        simulated_minute=simulated_minute,
        sequence=sequence,
        event_type=EventType.ACTIVITY_CHANGED,
        agent_id=agent_id,
        payload={"from_activity": from_activity, "to_activity": to_activity},
        source=EventSource.RULE,
    )


def _location_event(
    agent_id: str,
    *,
    from_zone: str,
    to_zone: str,
    sequence: int = 0,
    simulated_minute: int = 600,
    run_id: str = "run-social",
) -> DomainEvent:
    """One canonical Task 6 location movement event for the tick being planned."""
    return DomainEvent(
        event_id=stable_event_id(run_id, sequence),
        run_id=run_id,
        simulated_minute=simulated_minute,
        sequence=sequence,
        event_type=EventType.LOCATION_CHANGED,
        agent_id=agent_id,
        payload={
            "from_zone": from_zone,
            "to_zone": to_zone,
            "route_id": None,
            "from_route_id": None,
            "to_route_id": None,
        },
        source=EventSource.RULE,
    )


def test_social_disabled_produces_no_share(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        social_enabled=False,
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents == ()


def test_share_requires_a_relationship_edge(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    assert social.plan_social_shares(valid_snapshot, noticed_events, RandomOracle(42)) == ()


def test_share_requires_a_noticed_event(valid_snapshot: Snapshot) -> None:
    social = _social_module()
    ignored = (_noticed_event("person-001", event_type=EventType.CAMPAIGN_IGNORED),)

    with pytest.raises(ValueError, match="noticed"):
        social.plan_social_shares(
            valid_snapshot,
            ignored,
            RandomOracle(42),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*ignored),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_share_rejects_a_relationship_outside_the_snapshot(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="outside the snapshot"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(42),
            relationships=(_friendship("person-001", "person-009"),),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_share_rejects_a_self_relationship(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="self-relationship"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(42),
            relationships=(_friendship("person-001", "person-001"),),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_share_rejects_a_duplicate_relationship_edge(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="duplicate relationship"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(42),
            relationships=(
                _friendship("person-001", "person-002"),
                _friendship("person-002", "person-001"),
            ),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_share_rejects_an_event_for_an_agent_outside_the_snapshot(
    valid_snapshot: Snapshot,
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="outside the snapshot"):
        social.plan_social_shares(
            valid_snapshot,
            (_noticed_event("person-009"),),
            RandomOracle(42),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(_noticed_event("person-009")),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_positive_sentiment_produces_positive_word_of_mouth(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert len(intents) == 1
    assert intents[0].sender_id == "person-001"
    assert intents[0].receiver_id == "person-002"
    assert intents[0].valence == pytest.approx(1.0)


def test_negative_sentiment_produces_negative_word_of_mouth() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001", brand_sentiment=-1.0),
        _pair("person-002"),
    )

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert len(intents) == 1
    assert intents[0].valence == pytest.approx(-1.0)


def test_intents_are_sorted_by_sender_then_receiver() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-002"), _noticed_event("person-001")),
        RandomOracle(42),
        relationships=(
            _friendship("person-003", "person-002"),
            _friendship("person-003", "person-001"),
        ),
        share_signals=_signals(_noticed_event("person-002"), _noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id) for intent in intents] == [
        ("person-001", "person-003"),
        ("person-002", "person-003"),
    ]


def test_a_message_traverses_an_edge_at_most_once_per_tick() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"))

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"), _noticed_event("person-002")),
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(_noticed_event("person-001"), _noticed_event("person-002")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id) for intent in intents] == [
        ("person-001", "person-002")
    ]


def test_a_repeated_notice_of_one_campaign_shares_once() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"))
    events = (
        _noticed_event("person-001", event_id="run-social:noticed-a"),
        _noticed_event("person-001", event_id="run-social:noticed-b"),
    )

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert len(intents) == 1
    assert intents[0].caused_by_event_ids == ("run-social:noticed-a",)


def test_a_receiver_does_not_forward_in_the_same_tick() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (_noticed_event("person-001"),)

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(42),
        relationships=(
            _friendship("person-001", "person-002"),
            _friendship("person-002", "person-003"),
        ),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id) for intent in intents] == [
        ("person-001", "person-002")
    ]
    assert all(intent.caused_by_event_ids == (events[0].event_id,) for intent in intents)


def test_share_requires_colocation_or_online_contact() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001", location="cafe"),
        _pair("person-002", location="office", activity="work"),
    )

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents == ()


def test_an_online_relationship_reaches_a_different_zone() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001", location="cafe"),
        _pair("person-002", location="office", activity="work"),
    )
    online_edge = Relationship(
        source_id="person-001",
        target_id="person-002",
        kind="online",
        strength=1.0,
    )

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(42),
        relationships=(online_edge,),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert len(intents) == 1
    assert intents[0].relationship_kind == "online"


def test_a_sleeping_receiver_is_not_reached() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001"),
        _pair("person-002", location="cafe", activity="sleep"),
    )

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents == ()


def test_a_sleeping_sender_does_not_share() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001", location="cafe", activity="sleep"),
        _pair("person-002"),
    )

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents == ()


def test_a_powerless_edge_never_carries_a_message() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"))

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002", strength=0.0),),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents == ()


def test_the_keyed_draw_gates_sharing() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001", brand_sentiment=0.5),
        _pair("person-002"),
    )
    relationships = (_friendship("person-001", "person-002"),)

    outcomes = [
        social.plan_social_shares(
            snapshot,
            (_noticed_event("person-001"),),
            RandomOracle(seed),
            relationships=relationships,
            share_signals=_signals(_noticed_event("person-001"), value=0.5),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )
        for seed in range(50)
    ]

    assert any(intents for intents in outcomes)
    assert any(not intents for intents in outcomes)


def test_intent_records_the_noticed_event_as_its_cause(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(42),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents[0].caused_by_event_ids == (noticed_events[0].event_id,)


def test_plan_social_shares_is_reproducible_for_the_same_seed(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()
    relationships = (_friendship("person-001", "person-002"),)

    first = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(11),
        relationships=relationships,
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )
    second = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(11),
        relationships=relationships,
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert first == second


def test_plan_social_shares_ignores_relationship_input_order() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (_noticed_event("person-001"),)
    edges = (
        _friendship("person-001", "person-002"),
        _friendship("person-001", "person-003"),
    )

    forward = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(5),
        relationships=edges,
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )
    reversed_order = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(5),
        relationships=tuple(reversed(edges)),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert forward == reversed_order


def _intent(module: ModuleType, *, valence: float = 1.0, strength: float = 1.0) -> Any:
    return module.SocialIntent(
        sender_id="person-001",
        receiver_id="person-002",
        campaign_id="campaign-phone",
        relationship_kind="friend",
        relationship_strength=strength,
        valence=valence,
        share_probability=0.8,
        random_draw=0.1,
        simulated_minute=600,
        caused_by_event_ids=("run-social:noticed-person-001-campaign-phone",),
    )


def test_apply_social_intent_raises_receiver_social_proof() -> None:
    social = _social_module()

    transition = social.apply_social_intent(
        _profile("person-002"),
        _state("person-002", social_proof=0.0),
        _intent(social),
    )

    assert transition.state.social_proof == pytest.approx(0.25)


def test_negative_word_of_mouth_lowers_receiver_social_proof() -> None:
    social = _social_module()

    transition = social.apply_social_intent(
        _profile("person-002"),
        _state("person-002", social_proof=0.0),
        _intent(social, valence=-1.0),
    )

    assert transition.state.social_proof == pytest.approx(-0.25)


def test_a_weak_edge_moves_social_proof_less() -> None:
    social = _social_module()

    transition = social.apply_social_intent(
        _profile("person-002"),
        _state("person-002", social_proof=0.0),
        _intent(social, strength=0.4),
    )

    assert transition.state.social_proof == pytest.approx(0.1)


def test_apply_social_intent_marks_campaign_awareness() -> None:
    social = _social_module()

    transition = social.apply_social_intent(
        _profile("person-002"),
        _state("person-002"),
        _intent(social),
    )

    assert transition.state.aware_campaign_ids == frozenset({"campaign-phone"})


def test_apply_social_intent_adds_no_advertising_exposure() -> None:
    social = _social_module()

    transition = social.apply_social_intent(
        _profile("person-002"),
        _state("person-002"),
        _intent(social),
    )

    assert transition.state.exposure_counts == ()


def test_apply_social_intent_clamps_social_proof_within_bounds() -> None:
    social = _social_module()

    transition = social.apply_social_intent(
        _profile("person-002"),
        _state("person-002", social_proof=0.9),
        _intent(social),
    )

    assert transition.state.social_proof == 1.0


def test_apply_social_intent_records_the_causal_chain() -> None:
    social = _social_module()
    intent = _intent(social)

    transition = social.apply_social_intent(
        _profile("person-002"),
        _state("person-002"),
        intent,
    )

    assert transition.caused_by_event_ids == intent.caused_by_event_ids


def test_apply_social_intent_rejects_a_state_for_another_agent() -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="receiver"):
        social.apply_social_intent(
            _profile("person-003"),
            _state("person-003"),
            _intent(social),
        )


def test_apply_social_intent_rejects_a_profile_for_another_agent() -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="same agent"):
        social.apply_social_intent(
            _profile("person-003"),
            _state("person-002"),
            _intent(social),
        )


def test_social_intent_rejects_a_draw_above_its_share_probability() -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="random_draw"):
        social.SocialIntent(
            sender_id="person-001",
            receiver_id="person-002",
            campaign_id="campaign-phone",
            relationship_kind="friend",
            relationship_strength=1.0,
            valence=1.0,
            share_probability=0.2,
            random_draw=0.9,
            simulated_minute=600,
            caused_by_event_ids=("run-social:noticed",),
        )


def test_social_intent_rejects_a_self_addressed_message() -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="itself"):
        social.SocialIntent(
            sender_id="person-001",
            receiver_id="person-001",
            campaign_id="campaign-phone",
            relationship_kind="friend",
            relationship_strength=1.0,
            valence=1.0,
            share_probability=0.8,
            random_draw=0.1,
            simulated_minute=600,
            caused_by_event_ids=("run-social:noticed",),
        )


def test_share_rejects_a_noticed_event_from_another_tick(valid_snapshot: Snapshot) -> None:
    social = _social_module()
    stale = (_noticed_event("person-001", simulated_minute=615),)

    with pytest.raises(ValueError, match="pre-tick snapshot"):
        social.plan_social_shares(
            valid_snapshot,
            stale,
            RandomOracle(42),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*stale),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_share_rejects_a_noticed_event_from_another_run(valid_snapshot: Snapshot) -> None:
    social = _social_module()
    foreign = (_noticed_event("person-001", run_id="run-other"),)

    with pytest.raises(ValueError, match="pre-tick snapshot"):
        social.plan_social_shares(
            valid_snapshot,
            foreign,
            RandomOracle(42),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*foreign),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_a_cognition_share_signal_scales_the_share_probability(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()
    relationships = (_friendship("person-001", "person-002"),)

    base = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=relationships,
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )
    scaled = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=relationships,
        share_signals={noticed_events[0].event_id: 0.5},
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert base[0].share_probability == pytest.approx(1.0)
    assert scaled[0].share_probability == pytest.approx(0.5)
    assert scaled[0].random_draw == pytest.approx(base[0].random_draw)


def test_a_zero_cognition_share_signal_blocks_the_message(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals={noticed_events[0].event_id: 0.0},
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents == ()


def test_a_cognition_share_signal_must_match_a_noticed_event(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="noticed event"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals={"run-social:absent": 0.5},
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_a_cognition_share_signal_must_be_a_probability(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="share signal"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals={noticed_events[0].event_id: 1.5},
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def _composed_probability(social: ModuleType, **trait_overrides: float) -> float:
    """The composed share probability of one sender, driven by its real rule cognition."""
    decision = import_module("adlife.core.simulation.decision")
    values: dict[str, float] = {"price_sensitivity": 0.0, "advertising_skepticism": 0.0}
    values.update(trait_overrides)
    profile = _profile("person-001", traits=_traits(**values))
    state = _state("person-001")
    campaign = _campaign()
    response = decision.evaluate_rule_response(profile, state, campaign, campaign.placements[0])
    event = _noticed_event("person-001")

    intents = social.plan_social_shares(
        _snapshot((profile, state), _pair("person-002")),
        (event,),
        RandomOracle(90),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals={event.event_id: response.share_probability},
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert len(intents) == 1
    return float(intents[0].share_probability)


def test_novelty_seeking_scales_the_share_probability() -> None:
    """Novelty seeking reaches the composed probability through the cognition result."""
    social = _social_module()

    eager = _composed_probability(social, novelty_seeking=1.0)
    reluctant = _composed_probability(social, novelty_seeking=0.5)

    assert eager == pytest.approx(0.18)
    assert reluctant == pytest.approx(0.18 * 0.875 * 0.5)
    assert reluctant < eager


def test_social_susceptibility_scales_the_share_probability() -> None:
    """Social susceptibility reaches the composed probability through the cognition result."""
    social = _social_module()

    outgoing = _composed_probability(social, social_susceptibility=1.0)
    reserved = _composed_probability(social, social_susceptibility=0.5)

    assert outgoing == pytest.approx(0.18)
    assert reserved == pytest.approx(outgoing * 0.5)


def test_edge_trust_scales_the_share_probability() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"))

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002", strength=0.5),),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert intents[0].share_probability == pytest.approx(0.5)


def test_sentiment_magnitude_scales_the_share_probability() -> None:
    """The sentiment magnitude of the reaction reaches the composed probability once."""
    social = _social_module()

    convinced = _composed_probability(social)
    doubtful = _composed_probability(social, advertising_skepticism=0.5)

    assert convinced == pytest.approx(0.18)
    assert doubtful == pytest.approx(0.12)
    assert doubtful < convinced


def test_social_edge_key_is_symmetric_for_one_campaign(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert social.social_edge_key(intents[0]) == (
        0,
        "campaign-phone",
        "person-001",
        "person-002",
    )


def test_an_edge_that_already_carried_the_message_today_is_skipped(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()
    relationships = (_friendship("person-001", "person-002"),)
    first = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=relationships,
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    again = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=relationships,
        traversed_edges=tuple(social.social_edge_key(intent) for intent in first),
        share_signals=_signals(*noticed_events),
        movement_events=(),
        carried_messages=(),
    )

    assert len(first) == 1
    assert again == ()


def test_a_carried_edge_does_not_block_another_edge() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    relationships = (
        _friendship("person-001", "person-002"),
        _friendship("person-001", "person-003"),
    )

    intents = social.plan_social_shares(
        snapshot,
        (_noticed_event("person-001"),),
        RandomOracle(10),
        relationships=relationships,
        traversed_edges=((0, "campaign-phone", "person-002", "person-001"),),
        share_signals=_signals(_noticed_event("person-001")),
        movement_events=(),
        carried_messages=(),
    )

    assert [intent.receiver_id for intent in intents] == ["person-003"]


def test_a_carried_edge_of_another_campaign_still_carries_this_one(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        traversed_edges=((0, "campaign-watch", "person-001", "person-002"),),
        share_signals=_signals(*noticed_events),
        movement_events=(),
        carried_messages=(),
    )

    assert len(intents) == 1


def test_a_traversed_edge_must_name_a_campaign_and_two_agents(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="traversed edge"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            traversed_edges=(("campaign-phone", "person-001"),),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            carried_messages=(),
        )


def test_a_trusted_conversation_reinforces_receiver_recall() -> None:
    """The conversation banks reinforcement; the daily reflection turns it into recall."""
    social = _social_module()
    memory = import_module("adlife.core.simulation.memory")
    state = _state("person-002", recall_strength=0.4)

    transition = social.apply_social_intent(_profile("person-002"), state, _intent(social))

    assert transition.state.daily_reinforcement > state.daily_reinforcement
    assert transition.state.recall_strength == pytest.approx(state.recall_strength)
    assert memory.decay_memories(transition.state, 0).recall_strength == pytest.approx(
        0.4 * 0.85 + transition.state.daily_reinforcement
    )


def test_a_trusted_conversation_banks_its_daily_reinforcement() -> None:
    social = _social_module()
    state = _state("person-002", recall_strength=0.4)

    transition = social.apply_social_intent(_profile("person-002"), state, _intent(social))

    gain = transition.state.daily_reinforcement - state.daily_reinforcement
    assert gain > 0.0
    assert transition.state.daily_reinforcement == pytest.approx(gain)
    assert transition.state.recall_strength == pytest.approx(state.recall_strength)


def test_social_recall_reinforcement_scales_with_trust_and_headroom() -> None:
    social = _social_module()
    state = _state("person-002", recall_strength=0.4)

    transition = social.apply_social_intent(
        _profile("person-002"),
        state,
        _intent(social, strength=0.5),
    )

    assert transition.state.daily_reinforcement == pytest.approx(0.10 * 0.5 * 1.0 * 0.6)
    assert transition.state.recall_strength == pytest.approx(0.4)


def test_a_weak_edge_reinforces_recall_less() -> None:
    social = _social_module()
    state = _state("person-002", recall_strength=0.4)

    strong = social.apply_social_intent(
        _profile("person-002"),
        state,
        _intent(social, strength=1.0),
    ).state
    weak = social.apply_social_intent(
        _profile("person-002"),
        state,
        _intent(social, strength=0.25),
    ).state

    assert state.daily_reinforcement < weak.daily_reinforcement < strong.daily_reinforcement


def test_social_reinforcement_never_exceeds_the_daily_cap() -> None:
    social = _social_module()
    state = _state("person-002", recall_strength=0.4, daily_reinforcement=0.19)

    transition = social.apply_social_intent(_profile("person-002"), state, _intent(social))

    assert transition.state.daily_reinforcement == pytest.approx(0.20)


def test_social_reinforcement_keeps_recall_inside_its_bounds() -> None:
    social = _social_module()
    memory = import_module("adlife.core.simulation.memory")
    state = _state("person-002", recall_strength=1.0)

    transition = social.apply_social_intent(_profile("person-002"), state, _intent(social))

    assert transition.state.recall_strength == pytest.approx(1.0)
    assert transition.state.daily_reinforcement == pytest.approx(0.0)
    assert memory.decay_memories(transition.state, 0).recall_strength <= 1.0


def test_share_signals_must_be_a_mapping(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(TypeError, match="share_signals"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=(0.5,),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_a_share_signal_must_be_a_number(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(TypeError, match="finite number"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals={noticed_events[0].event_id: True},
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_a_share_signal_must_be_finite(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="share signal"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals={noticed_events[0].event_id: float("nan")},
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_traversed_edges_must_be_a_collection(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(TypeError, match="traversed_edges"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            traversed_edges="campaign-phone",
            share_signals=_signals(*noticed_events),
            movement_events=(),
            carried_messages=(),
        )


def test_a_traversed_edge_must_be_a_quadruple(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(TypeError, match="campaign and agent quadruple"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            traversed_edges=("campaign-phone",),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            carried_messages=(),
        )


def test_a_traversed_edge_must_name_two_different_agents(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="two different agents"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            traversed_edges=((0, "campaign-phone", "person-001", "person-001"),),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            carried_messages=(),
        )


def test_a_message_advances_at_most_one_edge_per_simulated_day() -> None:
    social = _social_module()
    snapshot = _snapshot(*(_pair(f"person-{index:03d}") for index in range(1, 9)))
    events = (_noticed_event("person-001"),)
    relationships = tuple(_friendship("person-001", f"person-{index:03d}") for index in range(2, 9))

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=relationships,
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert len(intents) == 1
    assert intents[0].sender_id == "person-001"
    assert intents[0].receiver_id in {f"person-{index:03d}" for index in range(2, 9)}


def test_every_sender_still_advances_its_own_message() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (_noticed_event("person-001"), _noticed_event("person-003"))

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(
            _friendship("person-001", "person-002"),
            _friendship("person-003", "person-002"),
        ),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id) for intent in intents] == [
        ("person-001", "person-002"),
        ("person-003", "person-002"),
    ]


def test_a_message_of_another_campaign_still_advances() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (
        _noticed_event("person-001", campaign_id="campaign-phone"),
        _noticed_event("person-001", campaign_id="campaign-watch"),
    )

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(
            _friendship("person-001", "person-002"),
            _friendship("person-001", "person-003"),
        ),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [intent.campaign_id for intent in intents] == [
        "campaign-phone",
        "campaign-watch",
    ]
    assert all(intent.receiver_id in {"person-002", "person-003"} for intent in intents)


def test_social_message_key_names_the_campaign_and_its_sender(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*noticed_events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert social.social_message_key(intents[0]) == (0, "campaign-phone", "person-001")


def test_a_message_that_already_hopped_today_does_not_hop_again() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (_noticed_event("person-001"),)
    relationships = (
        _friendship("person-001", "person-002"),
        _friendship("person-001", "person-003"),
    )
    first = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=relationships,
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    again = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=relationships,
        share_signals=_signals(*events),
        carried_messages=tuple(social.social_message_key(intent) for intent in first),
        movement_events=(),
        traversed_edges=(),
    )

    assert len(first) == 1
    assert again == ()


def test_a_carried_message_of_another_sender_still_carries_this_one(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    intents = social.plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*noticed_events),
        carried_messages=((0, "campaign-phone", "person-002"),),
        movement_events=(),
        traversed_edges=(),
    )

    assert len(intents) == 1


def test_carried_messages_must_be_a_collection(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(TypeError, match="carried_messages"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*noticed_events),
            carried_messages="campaign-phone",
            movement_events=(),
            traversed_edges=(),
        )


def test_a_carried_message_must_name_a_campaign_and_a_sender(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="carried message"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*noticed_events),
            carried_messages=(("campaign-phone",),),
            movement_events=(),
            traversed_edges=(),
        )


def test_planning_a_share_requires_the_cognition_result_of_each_noticed_event(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="cognition share signal"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_a_missing_cognition_result_is_never_read_as_certain_sharing() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (_noticed_event("person-001"), _noticed_event("person-002"))

    with pytest.raises(ValueError, match="cognition share signal"):
        social.plan_social_shares(
            snapshot,
            events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(events[0]),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )


def test_social_reinforcement_shares_one_daily_cap_with_advertising() -> None:
    social = _social_module()
    state = _state("person-002", recall_strength=0.4, daily_reinforcement=0.20)

    transition = social.apply_social_intent(_profile("person-002"), state, _intent(social))

    assert transition.state.recall_strength == pytest.approx(0.4)
    assert transition.state.daily_reinforcement == pytest.approx(0.20)


def test_a_partly_spent_daily_cap_admits_only_its_remainder_for_a_conversation() -> None:
    social = _social_module()
    state = _state("person-002", recall_strength=0.4, daily_reinforcement=0.19)

    transition = social.apply_social_intent(_profile("person-002"), state, _intent(social))

    assert transition.state.recall_strength == pytest.approx(0.4)
    assert transition.state.daily_reinforcement == pytest.approx(0.20)


def test_campaign_awareness_serializes_in_a_sorted_stable_order() -> None:
    social = _social_module()
    campaign_ids = (
        "campaign-watch",
        "campaign-phone",
        "campaign-bike",
        "campaign-radio",
        "campaign-tablet",
        "campaign-laptop",
        "campaign-camera",
        "campaign-shoes",
    )
    seeded = tuple(name for name in campaign_ids if name != "campaign-phone")
    state = _state("person-002", aware_campaign_ids=frozenset(seeded))

    transition = social.apply_social_intent(_profile("person-002"), state, _intent(social))
    dumped = transition.state.model_dump(mode="json")["aware_campaign_ids"]

    assert list(dumped) == sorted(campaign_ids)
    assert transition.state.model_dump_json() == transition.state.model_dump_json()


def test_a_receiver_that_noticed_the_advertisement_sends_its_own_message() -> None:
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (_noticed_event("person-001"), _noticed_event("person-002"))

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(42),
        relationships=(
            _friendship("person-001", "person-002"),
            _friendship("person-002", "person-003"),
        ),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id) for intent in intents] == [
        ("person-001", "person-002"),
        ("person-002", "person-003"),
    ]
    assert intents[0].caused_by_event_ids == (events[0].event_id,)
    assert intents[1].caused_by_event_ids == (events[1].event_id,)


def test_a_noticed_advertisement_and_a_conversation_both_mark_awareness() -> None:
    social = _social_module()
    decision = import_module("adlife.core.simulation.decision")
    response = decision.RuleResponse(
        campaign_id="campaign-watch",
        sentiment_delta=0.05,
        recall_delta=0.10,
        purchase_intention=0.4,
        share_probability=0.1,
        valence=0.25,
        relevance=0.5,
        credibility=0.7,
    )
    direct = decision.apply_response(_state("person-002"), response, ("event-0001",)).state

    indirect = social.apply_social_intent(_profile("person-002"), direct, _intent(social)).state

    assert direct.aware_campaign_ids == frozenset({"campaign-watch"})
    assert indirect.aware_campaign_ids == frozenset({"campaign-phone", "campaign-watch"})


def test_planning_a_share_requires_the_day_scoped_traversed_edges(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    """An omitted day accumulator would let one message hop the same edge every tick."""
    social = _social_module()

    with pytest.raises(ValueError, match="traversed_edges"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            carried_messages=(),
        )


def test_planning_a_share_requires_the_day_scoped_carried_messages(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="carried_messages"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            traversed_edges=(),
        )


def test_one_message_crosses_one_edge_across_the_ticks_of_one_day() -> None:
    """Spec 11.4 bounds the hop per simulated day, not per tick."""
    social = _social_module()
    relationships = (_friendship("person-001", "person-002"),)
    traversed: tuple[tuple[int, str, str, str], ...] = ()
    carried: tuple[tuple[int, str, str], ...] = ()
    threaded: list[Any] = []
    per_tick_only: list[Any] = []

    for minute in range(600, 705, 15):
        snapshot = _snapshot(_pair("person-001"), _pair("person-002"), simulated_minute=minute)
        events = (
            _noticed_event(
                "person-001",
                simulated_minute=minute,
                event_id=f"run-social:noticed-{minute}",
            ),
        )
        intents = social.plan_social_shares(
            snapshot,
            events,
            RandomOracle(10),
            relationships=relationships,
            share_signals=_signals(*events),
            movement_events=(),
            traversed_edges=traversed,
            carried_messages=carried,
        )
        threaded.extend(intents)
        traversed += tuple(social.social_edge_key(intent) for intent in intents)
        carried += tuple(social.social_message_key(intent) for intent in intents)
        per_tick_only.extend(
            social.plan_social_shares(
                snapshot,
                events,
                RandomOracle(10),
                relationships=relationships,
                share_signals=_signals(*events),
                movement_events=(),
                traversed_edges=(),
                carried_messages=(),
            )
        )

    assert len(threaded) == 1
    assert len(per_tick_only) > 1


def test_planning_a_share_requires_this_ticks_movement_events(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    """Co-location is decided in the same frame the noticed events were allocated in."""
    social = _social_module()

    with pytest.raises(ValueError, match="movement_events"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*noticed_events),
            traversed_edges=(),
            carried_messages=(),
        )


def test_contact_uses_the_projected_activity_of_the_tick() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001", location="cafe", activity="sleep"),
        _pair("person-002", location="cafe"),
    )
    events = (_noticed_event("person-001"),)
    movement = (_activity_event("person-001", from_activity="sleep", to_activity="phone-check"),)

    projected = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*events),
        movement_events=movement,
        traversed_edges=(),
        carried_messages=(),
    )
    stale = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id) for intent in projected] == [
        ("person-001", "person-002")
    ]
    assert stale == ()


def test_contact_uses_the_projected_location_of_the_tick() -> None:
    social = _social_module()
    snapshot = _snapshot(
        _pair("person-001", location="cafe"),
        _pair("person-002", location="office", activity="work"),
    )
    events = (_noticed_event("person-001"),)
    movement = (_location_event("person-001", from_zone="cafe", to_zone="office"),)

    projected = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*events),
        movement_events=movement,
        traversed_edges=(),
        carried_messages=(),
    )
    stale = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id) for intent in projected] == [
        ("person-001", "person-002")
    ]
    assert stale == ()


def test_movement_events_must_match_this_ticks_snapshot(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()
    foreign = (
        _activity_event(
            "person-001",
            from_activity="socializing",
            to_activity="phone-check",
            simulated_minute=615,
        ),
    )

    with pytest.raises(ValueError, match="movement"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*noticed_events),
            movement_events=foreign,
            traversed_edges=(),
            carried_messages=(),
        )


def test_movement_events_must_be_a_sequence(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(TypeError, match="movement_events"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            share_signals=_signals(*noticed_events),
            movement_events="person-001",
            traversed_edges=(),
            carried_messages=(),
        )


def test_intents_are_sorted_by_receiver_before_campaign() -> None:
    """The documented (sender, receiver) order decides which message survives."""
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), _pair("person-003"))
    events = (
        _noticed_event("person-001", campaign_id="campaign-alpha"),
        _noticed_event("person-001", campaign_id="campaign-beta"),
    )

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(
            _friendship("person-001", "person-002"),
            _friendship("person-001", "person-003"),
        ),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(
            (0, "campaign-alpha", "person-001", "person-002"),
            (0, "campaign-beta", "person-001", "person-003"),
        ),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id, intent.campaign_id) for intent in intents] == [
        ("person-001", "person-002", "campaign-beta"),
        ("person-001", "person-003", "campaign-alpha"),
    ]


def test_plan_social_shares_ignores_noticed_event_input_order() -> None:
    """The recorded cause must not depend on the order the caller collected notices in."""
    social = _social_module()
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"))
    events = (
        _noticed_event("person-001", event_id="run-social:noticed-a"),
        _noticed_event("person-001", event_id="run-social:noticed-b"),
    )
    relationships = (_friendship("person-001", "person-002"),)

    forward = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=relationships,
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )
    reversed_order = social.plan_social_shares(
        snapshot,
        tuple(reversed(events)),
        RandomOracle(10),
        relationships=relationships,
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert forward == reversed_order
    assert [intent.caused_by_event_ids for intent in forward] == [("run-social:noticed-a",)]


def _phone_placement() -> PhonePlacement:
    return PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=1320),),
        frequency_cap=3,
        visibility=0.8,
    )


def _campaign(campaign_id: str = "campaign-phone") -> Campaign:
    """The campaign the rule cognition of the composed-probability tests answers."""
    return Campaign(
        campaign_id=campaign_id,
        name="Pocket Launch",
        product_name="Fictional Phone",
        product_category="consumer-electronics",
        message="A fictional phone designed for a calmer daily routine.",
        call_to_action="Explore the fictional product",
        price=Price(amount=850.0, currency="USD"),
        category_reference_price=1000.0,
        target_interests=frozenset({"fitness", "technology"}),
        start_minute=0,
        end_minute=1440,
        placements=(_phone_placement(),),
        creative_features=CreativeFeatures(
            description="A fictional handset on a clean white background.",
        ),
    )


def test_the_composed_share_probability_applies_each_documented_factor_once() -> None:
    """The composed value carries cognition, novelty seeking, sentiment and trust once each."""
    social = _social_module()
    decision = import_module("adlife.core.simulation.decision")
    profile = _profile(
        "person-001",
        traits=_traits(
            price_sensitivity=0.0,
            novelty_seeking=0.6,
            social_susceptibility=0.8,
            advertising_skepticism=0.0,
        ),
    )
    state = _state("person-001", brand_sentiment=0.5)
    campaign = _campaign()
    response = decision.evaluate_rule_response(profile, state, campaign, campaign.placements[0])
    event = _noticed_event("person-001")

    intents = social.plan_social_shares(
        _snapshot((profile, state), _pair("person-002")),
        (event,),
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002", strength=0.8),),
        share_signals={event.event_id: response.share_probability},
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    cognition = response.share_probability
    assert cognition == pytest.approx(abs(response.sentiment_delta) * 0.8 * 0.6)
    assert len(intents) == 1
    assert intents[0].share_probability == pytest.approx(cognition * 0.8)
    assert intents[0].share_probability == pytest.approx(
        abs(response.sentiment_delta) * 0.6 * 0.8 * 0.8
    )


def test_the_composed_share_probability_squares_no_trait_and_no_sentiment() -> None:
    """The round-3 composition squared two traits and multiplied two sentiment magnitudes."""
    social = _social_module()
    decision = import_module("adlife.core.simulation.decision")
    profile = _profile(
        "person-001",
        traits=_traits(
            price_sensitivity=0.0,
            novelty_seeking=0.6,
            social_susceptibility=0.8,
            advertising_skepticism=0.0,
        ),
    )
    state = _state("person-001", brand_sentiment=0.5)
    campaign = _campaign()
    response = decision.evaluate_rule_response(profile, state, campaign, campaign.placements[0])
    event = _noticed_event("person-001")

    intents = social.plan_social_shares(
        _snapshot((profile, state), _pair("person-002")),
        (event,),
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002", strength=0.8),),
        share_signals={event.event_id: response.share_probability},
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    squared = response.share_probability * abs(state.brand_sentiment) * 0.6 * 0.8 * 0.8
    assert len(intents) == 1
    assert intents[0].share_probability > squared
    assert intents[0].share_probability / squared == pytest.approx(1.0 / (0.5 * 0.6 * 0.8))


def test_the_receiver_is_drawn_from_the_relationship_graph_not_the_alphabet() -> None:
    """Alphabetical selection would concentrate indirect awareness on one neighbor."""
    social = _social_module()
    snapshot = _snapshot(*(_pair(f"person-{index:03d}") for index in range(1, 6)))
    relationships = tuple(_friendship("person-001", f"person-{index:03d}") for index in range(2, 6))
    event = _noticed_event("person-001")

    receivers = set()
    for seed in range(40):
        intents = social.plan_social_shares(
            snapshot,
            (event,),
            RandomOracle(seed),
            relationships=relationships,
            share_signals=_signals(event),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )
        receivers.update(intent.receiver_id for intent in intents)

    assert receivers == {"person-002", "person-003", "person-004", "person-005"}


def test_the_drawn_receiver_is_always_a_relationship_neighbor() -> None:
    """The draw ranges over the relationship graph, never over the whole snapshot."""
    social = _social_module()
    snapshot = _snapshot(*(_pair(f"person-{index:03d}") for index in range(1, 6)))
    relationships = (
        _friendship("person-001", "person-004"),
        _friendship("person-001", "person-005"),
    )
    event = _noticed_event("person-001")

    receivers = set()
    for seed in range(40):
        intents = social.plan_social_shares(
            snapshot,
            (event,),
            RandomOracle(seed),
            relationships=relationships,
            share_signals=_signals(event),
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )
        receivers.update(intent.receiver_id for intent in intents)

    assert receivers == {"person-004", "person-005"}


def test_a_message_crosses_an_edge_again_on_the_next_simulated_day() -> None:
    """The bound is day-scoped: an accumulator from day 0 cannot silence day 1."""
    social = _social_module()
    relationships = (_friendship("person-001", "person-002"),)
    traversed: tuple[tuple[int, str, str, str], ...] = ()
    carried: tuple[tuple[int, str, str], ...] = ()
    per_day: list[int] = []

    for minute in (600, 615, 2040, 2055):
        snapshot = _snapshot(_pair("person-001"), _pair("person-002"), simulated_minute=minute)
        events = (
            _noticed_event(
                "person-001",
                simulated_minute=minute,
                event_id=f"run-social:noticed-{minute}",
            ),
        )
        intents = social.plan_social_shares(
            snapshot,
            events,
            RandomOracle(10),
            relationships=relationships,
            share_signals=_signals(*events),
            movement_events=(),
            traversed_edges=traversed,
            carried_messages=carried,
        )
        per_day.append(len(intents))
        traversed += tuple(social.social_edge_key(intent) for intent in intents)
        carried += tuple(social.social_message_key(intent) for intent in intents)

    assert per_day == [1, 0, 1, 0]
    assert {key[0] for key in traversed} == {0, 1}
    assert {key[0] for key in carried} == {0, 1}


def test_the_day_of_a_message_is_part_of_both_accumulator_keys() -> None:
    """Without the day in the key a carried accumulator would block every later day."""
    social = _social_module()
    minute = 2 * 1440 + 600
    snapshot = _snapshot(_pair("person-001"), _pair("person-002"), simulated_minute=minute)
    events = (
        _noticed_event("person-001", simulated_minute=minute, event_id="run-social:noticed-day2"),
    )

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(_friendship("person-001", "person-002"),),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(),
        carried_messages=(),
    )

    assert social.social_edge_key(intents[0])[0] == 2
    assert social.social_message_key(intents[0])[0] == 2
    assert social.day_index(minute) == 2


def test_intents_are_sorted_by_sender_then_receiver_across_several_senders() -> None:
    """The brief fixes the emission order; the planning order must not leak through."""
    social = _social_module()
    snapshot = _snapshot(*(_pair(f"person-{index:03d}") for index in range(1, 5)))
    events = (
        _noticed_event("person-001", campaign_id="campaign-alpha"),
        _noticed_event("person-001", campaign_id="campaign-beta"),
        _noticed_event("person-004", campaign_id="campaign-alpha"),
    )

    intents = social.plan_social_shares(
        snapshot,
        events,
        RandomOracle(10),
        relationships=(
            _friendship("person-001", "person-002"),
            _friendship("person-001", "person-003"),
            _friendship("person-004", "person-002"),
        ),
        share_signals=_signals(*events),
        movement_events=(),
        traversed_edges=(
            (0, "campaign-alpha", "person-001", "person-002"),
            (0, "campaign-beta", "person-001", "person-003"),
        ),
        carried_messages=(),
    )

    assert [(intent.sender_id, intent.receiver_id, intent.campaign_id) for intent in intents] == [
        ("person-001", "person-002", "campaign-beta"),
        ("person-001", "person-003", "campaign-alpha"),
        ("person-004", "person-002", "campaign-alpha"),
    ]


def test_a_traversed_edge_must_name_a_nonnegative_simulated_day(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    for day in (-1, "0", True):
        with pytest.raises(ValueError, match="nonnegative simulated day"):
            social.plan_social_shares(
                valid_snapshot,
                noticed_events,
                RandomOracle(10),
                relationships=(_friendship("person-001", "person-002"),),
                traversed_edges=((day, "campaign-phone", "person-001", "person-002"),),
                share_signals=_signals(*noticed_events),
                movement_events=(),
                carried_messages=(),
            )


def test_a_traversed_edge_must_name_a_campaign_and_two_agent_strings(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()
    broken = (
        (0, "", "person-001", "person-002"),
        (0, "campaign-phone", 1, "person-002"),
        (0, "campaign-phone", "person-001", ""),
    )

    for entry in broken:
        with pytest.raises(ValueError, match="campaign and two agents"):
            social.plan_social_shares(
                valid_snapshot,
                noticed_events,
                RandomOracle(10),
                relationships=(_friendship("person-001", "person-002"),),
                traversed_edges=(entry,),
                share_signals=_signals(*noticed_events),
                movement_events=(),
                carried_messages=(),
            )


def test_a_carried_message_must_name_a_nonnegative_day_and_two_strings(
    valid_snapshot: Snapshot,
    noticed_events: tuple[DomainEvent, ...],
) -> None:
    social = _social_module()

    with pytest.raises(ValueError, match="nonnegative simulated day"):
        social.plan_social_shares(
            valid_snapshot,
            noticed_events,
            RandomOracle(10),
            relationships=(_friendship("person-001", "person-002"),),
            carried_messages=((-1, "campaign-phone", "person-001"),),
            share_signals=_signals(*noticed_events),
            movement_events=(),
            traversed_edges=(),
        )

    for entry in ((0, "", "person-001"), (0, "campaign-phone", 2)):
        with pytest.raises(ValueError, match="campaign and a sender"):
            social.plan_social_shares(
                valid_snapshot,
                noticed_events,
                RandomOracle(10),
                relationships=(_friendship("person-001", "person-002"),),
                carried_messages=(entry,),
                share_signals=_signals(*noticed_events),
                movement_events=(),
                traversed_edges=(),
            )


def test_day_index_rejects_a_negative_or_boolean_minute() -> None:
    social = _social_module()

    for minute in (-1, True, 1.0):
        with pytest.raises(ValueError, match="nonnegative integer"):
            social.day_index(minute)
