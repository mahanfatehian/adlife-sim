from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adlife.core.domain.campaign import (
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Price,
    TimeWindow,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.state import ConsumerState, ExposureCount, Memory
from adlife.core.domain.world import Relationship
from adlife.core.simulation.movement import Snapshot
from adlife.core.simulation.rng import RandomOracle

UNIT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
SIGNED_UNIT = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)


def _module(name: str) -> ModuleType:
    try:
        return import_module(f"adlife.core.simulation.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"Task 8 {name} behavior is not implemented", pytrace=False)


def _traits(**overrides: float) -> ConsumerTraits:
    values: dict[str, float] = {
        "price_sensitivity": 0.5,
        "novelty_seeking": 0.6,
        "social_susceptibility": 0.4,
        "advertising_skepticism": 0.3,
        "mobile_attention": 0.8,
        "outdoor_attention": 0.5,
        "brand_loyalty": 0.4,
        "impulsivity": 0.3,
    }
    values.update(overrides)
    return ConsumerTraits(**values)


def _profile(
    agent_id: str = "person-001", *, traits: ConsumerTraits | None = None
) -> PersonProfile:
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
    agent_id: str = "person-001",
    *,
    location: str = "cafe",
    activity: str = "socializing",
    brand_sentiment: float = 0.1,
    recall_strength: float = 0.0,
    purchase_intention: float = 0.0,
    social_proof: float = 0.0,
    ad_fatigue: float = 0.0,
    daily_reinforcement: float = 0.0,
    active_need: bool = False,
    disposable_budget: float = 0.0,
    memories: tuple[Memory, ...] = (),
    exposure_counts: tuple[ExposureCount, ...] = (),
) -> ConsumerState:
    return ConsumerState(
        agent_id=agent_id,
        location=location,
        activity=activity,
        mood=0.0,
        fatigue=0.1,
        brand_sentiment=brand_sentiment,
        recall_strength=recall_strength,
        purchase_intention=purchase_intention,
        exposure_counts=exposure_counts,
        memories=memories,
        cognition_budget_remaining=6,
        ad_fatigue=ad_fatigue,
        daily_reinforcement=daily_reinforcement,
        social_proof=social_proof,
        active_need=active_need,
        disposable_budget=disposable_budget,
    )


def _placement() -> PhonePlacement:
    return PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=1320),),
        frequency_cap=3,
        visibility=0.8,
    )


def _campaign(*, price: float = 850.0, category_reference_price: float = 1000.0) -> Campaign:
    return Campaign(
        campaign_id="campaign-phone",
        name="Pocket Launch",
        product_name="Fictional Phone",
        product_category="consumer-electronics",
        message="A fictional phone designed for a calmer daily routine.",
        call_to_action="Explore the fictional product",
        price=Price(amount=price, currency="USD"),
        category_reference_price=category_reference_price,
        target_interests=frozenset({"technology"}),
        start_minute=0,
        end_minute=1440,
        placements=(_placement(),),
        creative_features=CreativeFeatures(description="A fictional handset."),
    )


def _memory(index: int, *, salience: float, created_minute: int) -> Memory:
    return Memory(
        memory_id=f"memory-{index:03d}",
        created_minute=created_minute,
        kind="advertising",
        summary=f"A fictional impression numbered {index}.",
        salience=salience,
        campaign_id="campaign-phone",
        caused_by_event_ids=(f"event-{index:04d}",),
    )


def _response(module: ModuleType, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "campaign_id": "campaign-phone",
        "sentiment_delta": 0.1,
        "recall_delta": 0.2,
        "purchase_intention": 0.5,
        "share_probability": 0.3,
        "valence": 0.5,
        "relevance": 0.5,
        "credibility": 0.5,
    }
    values.update(overrides)
    return module.RuleResponse(**values)


@given(
    price_sensitivity=UNIT,
    novelty_seeking=UNIT,
    advertising_skepticism=UNIT,
    mobile_attention=UNIT,
    impulsivity=UNIT,
    brand_sentiment=SIGNED_UNIT,
    social_proof=SIGNED_UNIT,
    price=st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    prior_exposures=st.integers(min_value=0, max_value=14),
)
def test_rule_response_fields_stay_inside_the_documented_bands(
    price_sensitivity: float,
    novelty_seeking: float,
    advertising_skepticism: float,
    mobile_attention: float,
    impulsivity: float,
    brand_sentiment: float,
    social_proof: float,
    price: float,
    prior_exposures: int,
) -> None:
    decision = _module("decision")
    profile = _profile(
        traits=_traits(
            price_sensitivity=price_sensitivity,
            novelty_seeking=novelty_seeking,
            advertising_skepticism=advertising_skepticism,
            mobile_attention=mobile_attention,
            impulsivity=impulsivity,
        )
    )
    state = _state(
        brand_sentiment=brand_sentiment,
        social_proof=social_proof,
        exposure_counts=(
            ExposureCount(
                campaign_id="campaign-phone",
                channel="mobile-feed",
                count=prior_exposures,
            ),
        ),
    )
    campaign = _campaign(price=price)

    response = decision.evaluate_rule_response(profile, state, campaign, _placement())

    assert -0.20 <= response.sentiment_delta <= 0.20
    assert 0.0 <= response.recall_delta <= 0.30
    assert 0.0 <= response.purchase_intention <= 1.0
    assert 0.0 <= response.share_probability <= 1.0
    assert -1.0 <= response.valence <= 1.0
    assert 0.0 <= response.relevance <= 1.0
    assert 0.0 <= response.credibility <= 1.0


@given(
    sentiment_delta=st.floats(min_value=-0.2, max_value=0.2, allow_nan=False, allow_infinity=False),
    recall_delta=st.floats(min_value=0.0, max_value=0.3, allow_nan=False, allow_infinity=False),
    purchase_intention=UNIT,
    brand_sentiment=SIGNED_UNIT,
    recall_strength=UNIT,
    ad_fatigue=UNIT,
    daily_reinforcement=st.floats(
        min_value=0.0, max_value=0.2, allow_nan=False, allow_infinity=False
    ),
)
def test_applying_a_response_keeps_every_state_field_inside_its_bounds(
    sentiment_delta: float,
    recall_delta: float,
    purchase_intention: float,
    brand_sentiment: float,
    recall_strength: float,
    ad_fatigue: float,
    daily_reinforcement: float,
) -> None:
    decision = _module("decision")
    response = _response(
        decision,
        sentiment_delta=sentiment_delta,
        recall_delta=recall_delta,
        purchase_intention=purchase_intention,
    )
    state = _state(
        brand_sentiment=brand_sentiment,
        recall_strength=recall_strength,
        ad_fatigue=ad_fatigue,
        daily_reinforcement=daily_reinforcement,
    )

    updated = decision.apply_response(state, response, ("event-0001",)).state

    assert -1.0 <= updated.brand_sentiment <= 1.0
    assert 0.0 <= updated.recall_strength <= 1.0
    assert 0.0 <= updated.purchase_intention <= 1.0
    assert 0.0 <= updated.ad_fatigue <= 1.0
    assert 0.0 <= updated.daily_reinforcement <= 0.2


@given(
    recall_strength=UNIT,
    daily_reinforcement=st.floats(
        min_value=0.0, max_value=0.2, allow_nan=False, allow_infinity=False
    ),
    ad_fatigue=UNIT,
    salience=UNIT,
    day_index=st.integers(min_value=1, max_value=6),
)
def test_daily_reflection_keeps_recall_fatigue_and_salience_inside_bounds(
    recall_strength: float,
    daily_reinforcement: float,
    ad_fatigue: float,
    salience: float,
    day_index: int,
) -> None:
    memory = _module("memory")
    state = _state(
        recall_strength=recall_strength,
        daily_reinforcement=daily_reinforcement,
        ad_fatigue=ad_fatigue,
        memories=(_memory(1, salience=salience, created_minute=60),),
    )

    updated = memory.decay_memories(state, day_index)

    assert 0.0 <= updated.recall_strength <= 1.0
    assert updated.recall_strength == pytest.approx(
        min(1.0, state.recall_strength * 0.85 + state.daily_reinforcement)
    )
    if state.daily_reinforcement == 0.0:
        assert updated.recall_strength <= state.recall_strength
    assert 0.0 <= updated.ad_fatigue <= 1.0
    assert updated.daily_reinforcement == 0.0
    assert all(0.0 <= item.salience <= 1.0 for item in updated.memories)


@given(
    saliences=st.lists(UNIT, min_size=1, max_size=12),
)
def test_episodic_memory_never_exceeds_five_entries(saliences: list[float]) -> None:
    memory = _module("memory")
    state = _state()

    for index, salience in enumerate(saliences):
        state = memory.encode_memory(
            state,
            _memory(index, salience=salience, created_minute=index * 15),
        )

    assert len(state.memories) <= 5
    strengths = [item.salience for item in state.memories]
    assert strengths == sorted(strengths, reverse=True)


@given(valence=SIGNED_UNIT, strength=UNIT, susceptibility=UNIT, social_proof=SIGNED_UNIT)
def test_social_proof_stays_inside_its_bounds(
    valence: float,
    strength: float,
    susceptibility: float,
    social_proof: float,
) -> None:
    social = _module("social")
    intent = social.SocialIntent(
        sender_id="person-001",
        receiver_id="person-002",
        campaign_id="campaign-phone",
        relationship_kind="friend",
        relationship_strength=strength,
        valence=valence,
        share_probability=0.8,
        random_draw=0.1,
        simulated_minute=600,
        caused_by_event_ids=("event-0001",),
    )
    profile = _profile("person-002", traits=_traits(social_susceptibility=susceptibility))

    updated = social.apply_social_intent(
        profile,
        _state("person-002", social_proof=social_proof),
        intent,
    ).state

    assert -1.0 <= updated.social_proof <= 1.0
    assert updated.exposure_counts == ()


def test_purchase_proxy_never_overdraws_the_budget_across_one_hundred_seeds() -> None:
    decision = _module("decision")
    profile = _profile()
    state = _state(purchase_intention=0.8, disposable_budget=900.0, active_need=True)
    campaign = _campaign(price=850.0)
    committed = 0

    for seed in range(100):
        outcome = decision.purchase_proxy(
            profile,
            state,
            campaign,
            RandomOracle(seed),
            simulated_minute=600,
        )
        assert outcome.budget_after >= 0.0
        if outcome.committed:
            committed += 1
            transition = decision.apply_purchase(state, outcome, ("event-0001",))
            assert transition.state.disposable_budget >= 0.0

    assert committed > 0


def test_social_plans_hold_their_invariants_across_one_hundred_seeds() -> None:
    social = _module("social")
    profiles = tuple(_profile(f"person-00{index}") for index in (1, 2, 3))
    agents = {
        profile.agent_id: (profile, _state(profile.agent_id, brand_sentiment=0.5))
        for profile in profiles
    }
    snapshot = Snapshot(
        agents=agents,
        simulated_minute=600,
        run_id="run-social",
        next_event_sequence=0,
    )
    events = tuple(
        DomainEvent(
            event_id=f"run-social:noticed-{agent_id}",
            run_id="run-social",
            simulated_minute=600,
            sequence=index,
            event_type=EventType.CAMPAIGN_NOTICED,
            agent_id=agent_id,
            campaign_id="campaign-phone",
            channel="mobile-feed",
            payload={},
            source=EventSource.RULE,
        )
        for index, agent_id in enumerate(("person-001", "person-002", "person-003"))
    )
    relationships = (
        Relationship(
            source_id="person-001",
            target_id="person-002",
            kind="friend",
            strength=0.8,
        ),
        Relationship(
            source_id="person-002",
            target_id="person-003",
            kind="friend",
            strength=0.6,
        ),
        Relationship(
            source_id="person-001",
            target_id="person-003",
            kind="online",
            strength=0.4,
        ),
    )
    # A realistic cognition result, so the keyed draw both admits and refuses messages.
    signals = {event.event_id: 0.5 for event in events}
    shared_seeds = 0

    for seed in range(100):
        intents = social.plan_social_shares(
            snapshot,
            events,
            RandomOracle(seed),
            relationships=relationships,
            share_signals=signals,
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )
        keys = [(intent.sender_id, intent.receiver_id) for intent in intents]
        assert keys == sorted(keys)
        edges = {frozenset(key) for key in keys}
        assert len(edges) == len(keys)
        assert all(-1.0 <= intent.valence <= 1.0 for intent in intents)
        assert all(0.0 <= intent.share_probability <= 1.0 for intent in intents)
        assert all(intent.random_draw < intent.share_probability for intent in intents)
        repeated = social.plan_social_shares(
            snapshot,
            events,
            RandomOracle(seed),
            relationships=relationships,
            share_signals=signals,
            movement_events=(),
            traversed_edges=(),
            carried_messages=(),
        )
        assert repeated == intents
        shared_seeds += 1 if intents else 0

    assert 0 < shared_seeds < 100
