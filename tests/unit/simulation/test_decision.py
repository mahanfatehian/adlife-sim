from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

import pytest

from adlife.core.domain.campaign import (
    BillboardPlacement,
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Price,
    TimeWindow,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.state import ConsumerState, ExposureCount
from adlife.core.simulation.rng import RandomOracle


def _decision_module() -> ModuleType:
    try:
        return import_module("adlife.core.simulation.decision")
    except ModuleNotFoundError:
        pytest.fail("Task 8 decision behavior is not implemented", pytrace=False)


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
    agent_id: str = "person-001",
    *,
    traits: ConsumerTraits | None = None,
    interests: frozenset[str] = frozenset({"fitness", "technology"}),
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
        interests=interests,
        traits=traits or _traits(),
        initial_brand_sentiment=0.1,
        routine_template="office-worker",
    )


def _state(
    profile: PersonProfile,
    *,
    brand_sentiment: float = 0.1,
    recall_strength: float = 0.0,
    purchase_intention: float = 0.0,
    social_proof: float = 0.0,
    ad_fatigue: float = 0.0,
    daily_reinforcement: float = 0.0,
    active_need: bool = False,
    disposable_budget: float = 0.0,
    exposure_counts: tuple[ExposureCount, ...] = (),
    aware_campaign_ids: frozenset[str] = frozenset(),
) -> ConsumerState:
    return ConsumerState(
        agent_id=profile.agent_id,
        location="online",
        activity="phone-check",
        mood=0.0,
        fatigue=0.1,
        brand_sentiment=brand_sentiment,
        recall_strength=recall_strength,
        purchase_intention=purchase_intention,
        exposure_counts=exposure_counts,
        cognition_budget_remaining=6,
        ad_fatigue=ad_fatigue,
        daily_reinforcement=daily_reinforcement,
        social_proof=social_proof,
        aware_campaign_ids=aware_campaign_ids,
        active_need=active_need,
        disposable_budget=disposable_budget,
    )


def _phone_placement(*, frequency_cap: int = 3) -> PhonePlacement:
    return PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=1320),),
        frequency_cap=frequency_cap,
        visibility=0.8,
    )


def _billboard_placement() -> BillboardPlacement:
    return BillboardPlacement(
        channel="highway-billboard",
        route_id="highway-north",
        active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=600),),
        frequency_cap=7,
        visibility=0.6,
    )


def _campaign(
    campaign_id: str = "campaign-phone",
    *,
    price: float = 850.0,
    category_reference_price: float = 1000.0,
    placement: PhonePlacement | BillboardPlacement | None = None,
    target_interests: frozenset[str] = frozenset({"technology"}),
) -> Campaign:
    return Campaign(
        campaign_id=campaign_id,
        name="Pocket Launch",
        product_name="Fictional Phone",
        product_category="consumer-electronics",
        message="A fictional phone designed for a calmer daily routine.",
        call_to_action="Explore the fictional product",
        price=Price(amount=price, currency="USD"),
        category_reference_price=category_reference_price,
        target_interests=target_interests,
        start_minute=0,
        end_minute=1440,
        placements=(placement or _phone_placement(),),
        creative_features=CreativeFeatures(
            description="A fictional handset on a clean white background.",
        ),
    )


@pytest.fixture
def phone_placement() -> PhonePlacement:
    return _phone_placement()


@pytest.fixture
def cheap_campaign(phone_placement: PhonePlacement) -> Campaign:
    return _campaign("campaign-cheap", price=500.0, placement=phone_placement)


@pytest.fixture
def expensive_campaign(phone_placement: PhonePlacement) -> Campaign:
    return _campaign("campaign-expensive", price=1500.0, placement=phone_placement)


@pytest.fixture
def positive_response() -> Any:
    decision = _decision_module()
    return decision.RuleResponse(
        campaign_id="campaign-phone",
        sentiment_delta=0.07,
        recall_delta=0.25,
        purchase_intention=0.42,
        share_probability=0.02,
        valence=0.35,
        relevance=0.5,
        credibility=0.7,
    )


def _small_response(module: ModuleType) -> Any:
    """A response whose recall gain stays well inside the documented daily cap."""
    return module.RuleResponse(
        campaign_id="campaign-phone",
        sentiment_delta=0.02,
        recall_delta=0.08,
        purchase_intention=0.30,
        share_probability=0.01,
        valence=0.10,
        relevance=0.5,
        credibility=0.7,
    )


def test_jaccard_of_identical_interest_sets_is_one() -> None:
    decision = _decision_module()

    assert decision.jaccard(frozenset({"technology"}), frozenset({"technology"})) == 1.0


def test_jaccard_of_disjoint_interest_sets_is_zero() -> None:
    decision = _decision_module()

    assert decision.jaccard(frozenset({"fitness"}), frozenset({"technology"})) == 0.0


def test_jaccard_of_two_empty_sets_is_zero() -> None:
    decision = _decision_module()

    assert decision.jaccard(frozenset(), frozenset()) == 0.0


def test_jaccard_matches_the_hand_counted_overlap() -> None:
    decision = _decision_module()

    overlap = decision.jaccard(
        frozenset({"fitness", "technology"}),
        frozenset({"technology", "travel"}),
    )

    assert overlap == pytest.approx(1 / 3)


def test_channel_attention_uses_mobile_attention_for_the_mobile_feed() -> None:
    decision = _decision_module()
    profile = _profile(traits=_traits(mobile_attention=0.9, outdoor_attention=0.2))

    assert decision.channel_attention(profile, "mobile-feed") == 0.9


def test_channel_attention_uses_outdoor_attention_for_the_billboard() -> None:
    decision = _decision_module()
    profile = _profile(traits=_traits(mobile_attention=0.9, outdoor_attention=0.2))

    assert decision.channel_attention(profile, "highway-billboard") == 0.2


def test_channel_attention_rejects_an_unknown_channel() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="channel"):
        decision.channel_attention(_profile(), "radio")


def test_rule_response_matches_the_documented_formula() -> None:
    decision = _decision_module()
    profile = _profile()
    placement = _phone_placement()

    response = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(placement=placement),
        placement,
    )

    assert response.sentiment_delta == pytest.approx(0.0702)
    assert response.recall_delta == pytest.approx(0.248)
    assert response.purchase_intention == pytest.approx(0.42654)
    assert response.share_probability == pytest.approx(0.016848)
    assert response.valence == pytest.approx(0.351)
    assert response.relevance == pytest.approx(0.5)
    assert response.credibility == pytest.approx(0.7)


def test_price_sensitivity_reduces_intention_for_expensive_product(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    cheap_campaign: Campaign,
    expensive_campaign: Campaign,
    phone_placement: PhonePlacement,
) -> None:
    decision = _decision_module()

    cheap = decision.evaluate_rule_response(
        valid_profile, consumer_state, cheap_campaign, phone_placement
    )
    expensive = decision.evaluate_rule_response(
        valid_profile, consumer_state, expensive_campaign, phone_placement
    )

    assert expensive.purchase_intention <= cheap.purchase_intention


def test_expensive_product_strictly_lowers_intention_for_a_price_sensitive_agent(
    cheap_campaign: Campaign,
    expensive_campaign: Campaign,
    phone_placement: PhonePlacement,
) -> None:
    decision = _decision_module()
    profile = _profile(traits=_traits(price_sensitivity=1.0))
    state = _state(profile)

    cheap = decision.evaluate_rule_response(profile, state, cheap_campaign, phone_placement)
    expensive = decision.evaluate_rule_response(profile, state, expensive_campaign, phone_placement)

    assert expensive.purchase_intention < cheap.purchase_intention


def test_price_is_ignored_when_the_agent_is_not_price_sensitive(
    cheap_campaign: Campaign,
    expensive_campaign: Campaign,
    phone_placement: PhonePlacement,
) -> None:
    decision = _decision_module()
    profile = _profile(traits=_traits(price_sensitivity=0.0))
    state = _state(profile)

    cheap = decision.evaluate_rule_response(profile, state, cheap_campaign, phone_placement)
    expensive = decision.evaluate_rule_response(profile, state, expensive_campaign, phone_placement)

    assert expensive.purchase_intention == cheap.purchase_intention


def test_a_skeptical_fatigued_agent_stays_above_the_sentiment_floor() -> None:
    decision = _decision_module()
    profile = _profile(
        traits=_traits(advertising_skepticism=1.0, novelty_seeking=0.0),
        interests=frozenset({"gardening"}),
    )
    placement = _phone_placement(frequency_cap=1)
    campaign = _campaign(placement=placement)
    state = _state(
        profile,
        exposure_counts=(
            ExposureCount(campaign_id="campaign-phone", channel="mobile-feed", count=5),
        ),
    )

    response = decision.evaluate_rule_response(profile, state, campaign, placement)

    assert response.sentiment_delta == pytest.approx(-0.1503)
    assert response.sentiment_delta >= -0.20


def test_an_ideal_match_stays_below_the_sentiment_ceiling() -> None:
    decision = _decision_module()
    profile = _profile(
        traits=_traits(advertising_skepticism=0.0, novelty_seeking=1.0, price_sensitivity=0.0),
        interests=frozenset({"technology"}),
    )
    placement = _phone_placement()
    campaign = _campaign(placement=placement)

    response = decision.evaluate_rule_response(profile, _state(profile), campaign, placement)

    assert response.sentiment_delta == pytest.approx(0.18)
    assert response.sentiment_delta <= 0.20


def test_recall_delta_never_exceeds_three_tenths() -> None:
    decision = _decision_module()
    profile = _profile(traits=_traits(mobile_attention=1.0, novelty_seeking=1.0))
    placement = _phone_placement()

    response = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(placement=placement),
        placement,
    )

    assert response.recall_delta == pytest.approx(0.30)


def test_frequency_fatigue_lowers_recall_and_sentiment() -> None:
    decision = _decision_module()
    profile = _profile()
    placement = _phone_placement()
    campaign = _campaign(placement=placement)
    fresh = decision.evaluate_rule_response(profile, _state(profile), campaign, placement)

    fatigued = decision.evaluate_rule_response(
        profile,
        _state(
            profile,
            exposure_counts=(
                ExposureCount(campaign_id="campaign-phone", channel="mobile-feed", count=3),
            ),
        ),
        campaign,
        placement,
    )

    assert fatigued.recall_delta == pytest.approx(fresh.recall_delta - 0.08)
    assert fatigued.sentiment_delta == pytest.approx(fresh.sentiment_delta - 0.06)


def test_relevance_reports_the_interest_overlap() -> None:
    decision = _decision_module()
    profile = _profile(interests=frozenset({"fitness", "technology"}))
    placement = _phone_placement()

    response = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(placement=placement, target_interests=frozenset({"technology"})),
        placement,
    )

    assert response.relevance == pytest.approx(0.5)


def test_credibility_is_the_complement_of_skepticism() -> None:
    decision = _decision_module()
    profile = _profile(traits=_traits(advertising_skepticism=0.25))
    placement = _phone_placement()

    response = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(placement=placement),
        placement,
    )

    assert response.credibility == pytest.approx(0.75)


def test_valence_scales_the_sentiment_delta_by_five() -> None:
    decision = _decision_module()
    profile = _profile()
    placement = _phone_placement()

    response = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(placement=placement),
        placement,
    )

    assert response.valence == pytest.approx(response.sentiment_delta * 5)


def test_share_probability_combines_sentiment_magnitude_with_traits() -> None:
    decision = _decision_module()
    profile = _profile(traits=_traits(social_susceptibility=1.0, novelty_seeking=0.0))
    placement = _phone_placement()

    response = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(placement=placement),
        placement,
    )

    assert response.share_probability == pytest.approx(0.0)


def test_evaluate_rejects_a_state_for_another_agent() -> None:
    decision = _decision_module()
    placement = _phone_placement()

    with pytest.raises(ValueError, match="same agent"):
        decision.evaluate_rule_response(
            _profile("person-001"),
            _state(_profile("person-002")),
            _campaign(placement=placement),
            placement,
        )


def test_evaluate_rejects_a_placement_outside_the_campaign() -> None:
    decision = _decision_module()
    profile = _profile()

    with pytest.raises(ValueError, match="belong to campaign"):
        decision.evaluate_rule_response(
            profile,
            _state(profile),
            _campaign(placement=_phone_placement()),
            _billboard_placement(),
        )


def test_rule_response_rejects_an_out_of_band_sentiment_delta() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="sentiment_delta"):
        decision.RuleResponse(
            campaign_id="campaign-phone",
            sentiment_delta=0.5,
            recall_delta=0.1,
            purchase_intention=0.4,
            share_probability=0.1,
            valence=0.2,
            relevance=0.5,
            credibility=0.7,
        )


def test_rule_response_rejects_a_non_finite_field() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="finite"):
        decision.RuleResponse(
            campaign_id="campaign-phone",
            sentiment_delta=float("nan"),
            recall_delta=0.1,
            purchase_intention=0.4,
            share_probability=0.1,
            valence=0.2,
            relevance=0.5,
            credibility=0.7,
        )


def test_recall_saturates_at_one(consumer_state: ConsumerState, positive_response: Any) -> None:
    decision = _decision_module()

    updated = decision.apply_response(consumer_state, positive_response, ("event-0001",))

    assert 0 <= updated.state.recall_strength <= 1


def test_recall_never_exceeds_one_under_repeated_reinforcement(positive_response: Any) -> None:
    decision = _decision_module()
    memory = import_module("adlife.core.simulation.memory")
    profile = _profile()
    state = _state(profile)

    day_start = 0.0
    for day in range(40):
        day_start = state.recall_strength
        for index in range(3):
            state = decision.apply_response(
                state,
                positive_response,
                (f"event-{day:04d}-{index:02d}",),
            ).state
        state = memory.decay_memories(state, day)
        assert 0.0 <= state.recall_strength <= 1.0

    assert 0.5 < state.recall_strength <= 1.0
    assert state.recall_strength - day_start < 0.001


def test_apply_response_adds_less_recall_on_every_repeat() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile)
    response = _small_response(decision)

    first = decision.apply_response(state, response, ("event-0001",)).state
    second = decision.apply_response(first, response, ("event-0002",)).state

    first_gain = first.daily_reinforcement - state.daily_reinforcement
    second_gain = second.daily_reinforcement - first.daily_reinforcement
    assert first.daily_reinforcement < decision.MAX_DAILY_REINFORCEMENT
    assert second.daily_reinforcement < decision.MAX_DAILY_REINFORCEMENT
    assert second_gain < first_gain
    assert first.recall_strength == pytest.approx(state.recall_strength)
    assert second.recall_strength == pytest.approx(state.recall_strength)


def test_apply_response_shifts_brand_sentiment_by_the_delta(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, brand_sentiment=0.1)

    transition = decision.apply_response(state, positive_response, ("event-0001",))

    assert transition.state.brand_sentiment == pytest.approx(0.17)


def test_apply_response_clamps_brand_sentiment_at_the_upper_bound(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, brand_sentiment=0.98)

    transition = decision.apply_response(state, positive_response, ("event-0001",))

    assert transition.state.brand_sentiment == 1.0


def test_apply_response_sets_purchase_intention_from_the_response(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()

    transition = decision.apply_response(_state(profile), positive_response, ("event-0001",))

    assert transition.state.purchase_intention == pytest.approx(
        positive_response.purchase_intention
    )


def test_apply_response_accumulates_capped_daily_reinforcement(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, daily_reinforcement=0.19)

    transition = decision.apply_response(state, positive_response, ("event-0001",))

    assert transition.state.daily_reinforcement == pytest.approx(0.20)


def test_apply_response_raises_ad_fatigue_without_exceeding_one(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()

    low = decision.apply_response(_state(profile), positive_response, ("event-0001",)).state
    high = decision.apply_response(
        _state(profile, ad_fatigue=0.99),
        positive_response,
        ("event-0002",),
    ).state

    assert low.ad_fatigue > 0.0
    assert high.ad_fatigue == 1.0


def test_apply_response_keeps_exposure_counts_unchanged(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()
    counts = (ExposureCount(campaign_id="campaign-phone", channel="mobile-feed", count=2),)

    transition = decision.apply_response(
        _state(profile, exposure_counts=counts),
        positive_response,
        ("event-0001",),
    )

    assert transition.state.exposure_counts == counts


def test_apply_response_records_the_causal_chain(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile)

    transition = decision.apply_response(state, positive_response, ("event-0001", "event-0002"))

    assert transition.caused_by_event_ids == ("event-0001", "event-0002")
    assert transition.previous == state


def test_apply_response_rejects_an_empty_causal_chain(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()

    with pytest.raises(ValueError, match="cause"):
        decision.apply_response(_state(profile), positive_response, ())


def test_apply_response_rejects_duplicate_causal_event_ids(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()

    with pytest.raises(ValueError, match="unique"):
        decision.apply_response(_state(profile), positive_response, ("event-0001", "event-0001"))


def test_purchase_proxy_requires_an_active_need() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.9, disposable_budget=2000.0, active_need=False)

    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(),
        RandomOracle(42),
        simulated_minute=600,
    )

    assert outcome.committed is False
    assert outcome.reason == "no-active-need"


def test_purchase_proxy_requires_a_budget_at_least_the_price() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.9, disposable_budget=849.0, active_need=True)

    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(price=850.0),
        RandomOracle(42),
        simulated_minute=600,
    )

    assert outcome.committed is False
    assert outcome.reason == "budget-below-price"


def test_purchase_proxy_requires_intention_at_the_threshold() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.69, disposable_budget=2000.0, active_need=True)

    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(),
        RandomOracle(42),
        simulated_minute=600,
    )

    assert outcome.committed is False
    assert outcome.reason == "intention-below-threshold"


def test_purchase_proxy_accepts_intention_exactly_at_the_threshold() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.70, disposable_budget=2000.0, active_need=True)

    outcomes = [
        decision.purchase_proxy(
            profile,
            state,
            _campaign(),
            RandomOracle(seed),
            simulated_minute=600,
        )
        for seed in range(50)
    ]

    assert all(outcome.reason != "intention-below-threshold" for outcome in outcomes)


def test_purchase_proxy_commits_exactly_when_the_draw_is_below_intention() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)

    outcomes = [
        decision.purchase_proxy(
            profile,
            state,
            _campaign(),
            RandomOracle(seed),
            simulated_minute=600,
        )
        for seed in range(50)
    ]

    assert any(outcome.committed for outcome in outcomes)
    assert any(not outcome.committed for outcome in outcomes)
    for outcome in outcomes:
        assert outcome.committed == (outcome.random_draw < outcome.intention)
        if not outcome.committed:
            assert outcome.reason == "draw-above-intention"


def test_purchase_proxy_skips_the_draw_when_a_precondition_fails() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.9, disposable_budget=2000.0, active_need=False)

    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(),
        RandomOracle(42),
        simulated_minute=600,
    )

    assert outcome.random_draw is None


def test_purchase_proxy_is_reproducible_for_the_same_key() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)
    campaign = _campaign()

    first = decision.purchase_proxy(profile, state, campaign, RandomOracle(7), simulated_minute=600)
    second = decision.purchase_proxy(
        profile, state, campaign, RandomOracle(7), simulated_minute=600
    )

    assert first == second


def test_purchase_proxy_draw_depends_on_the_simulated_minute() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)
    campaign = _campaign()

    draws = {
        decision.purchase_proxy(
            profile,
            state,
            campaign,
            RandomOracle(7),
            simulated_minute=minute,
        ).random_draw
        for minute in (600, 615, 630, 645)
    }

    assert len(draws) == 4


def test_purchase_proxy_avoids_the_word_sale_in_machine_fields() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.9, disposable_budget=2000.0, active_need=True)

    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(),
        RandomOracle(3),
        simulated_minute=600,
    )

    rendered = " ".join((*outcome.__slots__, outcome.reason))
    assert "sale" not in rendered.lower()


def test_purchase_proxy_rejects_a_state_for_another_agent() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="same agent"):
        decision.purchase_proxy(
            _profile("person-001"),
            _state(_profile("person-002")),
            _campaign(),
            RandomOracle(42),
            simulated_minute=600,
        )


def _committed_decision(module: ModuleType, state: ConsumerState, campaign: Campaign) -> Any:
    for seed in range(200):
        outcome = module.purchase_proxy(
            _profile(),
            state,
            campaign,
            RandomOracle(seed),
            simulated_minute=600,
        )
        if outcome.committed:
            return outcome
    pytest.fail("no seed produced a committed purchase proxy", pytrace=False)


def test_apply_purchase_subtracts_the_price_from_the_budget() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.95, disposable_budget=2000.0, active_need=True)
    campaign = _campaign(price=850.0)
    outcome = _committed_decision(decision, state, campaign)

    transition = decision.apply_purchase(state, outcome, ("event-0001",))

    assert transition.state.disposable_budget == pytest.approx(1150.0)


def test_apply_purchase_clears_the_active_need() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.95, disposable_budget=2000.0, active_need=True)
    campaign = _campaign(price=850.0)
    outcome = _committed_decision(decision, state, campaign)

    transition = decision.apply_purchase(state, outcome, ("event-0001",))

    assert transition.state.active_need is False


def test_apply_purchase_rejects_an_uncommitted_decision() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.2, disposable_budget=2000.0, active_need=True)
    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(),
        RandomOracle(42),
        simulated_minute=600,
    )

    with pytest.raises(ValueError, match="committed"):
        decision.apply_purchase(state, outcome, ("event-0001",))


def test_apply_purchase_rejects_a_stale_budget() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.95, disposable_budget=2000.0, active_need=True)
    campaign = _campaign(price=850.0)
    outcome = _committed_decision(decision, state, campaign)
    spent = _state(profile, purchase_intention=0.95, disposable_budget=900.0, active_need=True)

    with pytest.raises(ValueError, match="budget"):
        decision.apply_purchase(spent, outcome, ("event-0001",))


def test_social_proof_raises_purchase_intention_by_its_documented_weight() -> None:
    decision = _decision_module()
    profile = _profile()
    campaign = _campaign()
    placement = campaign.placements[0]

    without = decision.evaluate_rule_response(
        profile,
        _state(profile, social_proof=0.0),
        campaign,
        placement,
    )
    with_proof = decision.evaluate_rule_response(
        profile,
        _state(profile, social_proof=0.5),
        campaign,
        placement,
    )

    assert with_proof.purchase_intention - without.purchase_intention == pytest.approx(0.10)


def test_negative_social_proof_lowers_purchase_intention_by_its_documented_weight() -> None:
    decision = _decision_module()
    profile = _profile()
    campaign = _campaign()
    placement = campaign.placements[0]

    neutral = decision.evaluate_rule_response(
        profile,
        _state(profile, social_proof=0.0),
        campaign,
        placement,
    )
    doubted = decision.evaluate_rule_response(
        profile,
        _state(profile, social_proof=-0.5),
        campaign,
        placement,
    )

    assert neutral.purchase_intention - doubted.purchase_intention == pytest.approx(0.10)


def test_partial_frequency_fatigue_scales_with_the_frequency_cap() -> None:
    decision = _decision_module()
    profile = _profile()
    campaign = _campaign(placement=_phone_placement(frequency_cap=4))
    placement = campaign.placements[0]
    counts = (ExposureCount(campaign_id="campaign-phone", channel="mobile-feed", count=1),)

    fresh = decision.evaluate_rule_response(profile, _state(profile), campaign, placement)
    quarter = decision.evaluate_rule_response(
        profile,
        _state(profile, exposure_counts=counts),
        campaign,
        placement,
    )

    assert quarter.sentiment_delta == pytest.approx(fresh.sentiment_delta - 0.06 * 0.25)
    assert quarter.recall_delta == pytest.approx(fresh.recall_delta - 0.08 * 0.25)


def test_value_match_uses_affordability_against_the_category_reference_price() -> None:
    decision = _decision_module()
    profile = _profile()
    placement = _phone_placement()
    reference = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(price=1000.0, category_reference_price=1000.0, placement=placement),
        placement,
    )
    double = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(price=2000.0, category_reference_price=1000.0, placement=placement),
        placement,
    )

    assert reference.sentiment_delta - double.sentiment_delta == pytest.approx(0.18 * 0.20 * 0.5)
    assert double.purchase_intention < reference.purchase_intention


def test_affordability_saturates_below_the_reference_price() -> None:
    decision = _decision_module()
    profile = _profile()
    placement = _phone_placement()

    cheap = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(price=200.0, category_reference_price=1000.0, placement=placement),
        placement,
    )
    cheaper = decision.evaluate_rule_response(
        profile,
        _state(profile),
        _campaign(price=400.0, category_reference_price=1000.0, placement=placement),
        placement,
    )

    assert cheap.sentiment_delta == pytest.approx(cheaper.sentiment_delta)
    assert cheap.purchase_intention == pytest.approx(cheaper.purchase_intention)


def test_recall_gain_scales_with_the_remaining_headroom(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()

    memory = import_module("adlife.core.simulation.memory")

    updated = decision.apply_response(
        _state(profile, recall_strength=0.6),
        positive_response,
        ("event-0001",),
    ).state

    assert updated.daily_reinforcement == pytest.approx(0.25 * 0.4)
    assert updated.recall_strength == pytest.approx(0.6)
    assert memory.decay_memories(updated, 0).recall_strength == pytest.approx(
        0.6 * 0.85 + 0.25 * 0.4
    )


def test_purchase_proxy_draw_depends_on_the_decision_index() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)
    campaign = _campaign()

    draws = {
        decision.purchase_proxy(
            profile,
            state,
            campaign,
            RandomOracle(7),
            simulated_minute=600,
            decision_index=index,
        ).random_draw
        for index in range(4)
    }

    assert len(draws) == 4


def test_purchase_proxy_draw_depends_on_the_agent() -> None:
    decision = _decision_module()
    draws = set()
    for agent_id in ("person-001", "person-002", "person-003"):
        profile = _profile(agent_id)
        state = _state(
            profile,
            purchase_intention=0.75,
            disposable_budget=2000.0,
            active_need=True,
        )
        draws.add(
            decision.purchase_proxy(
                profile,
                state,
                _campaign(),
                RandomOracle(7),
                simulated_minute=600,
            ).random_draw
        )

    assert len(draws) == 3


def test_purchase_proxy_draw_depends_on_the_campaign() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)

    draws = {
        decision.purchase_proxy(
            profile,
            state,
            _campaign(campaign_id),
            RandomOracle(7),
            simulated_minute=600,
        ).random_draw
        for campaign_id in ("campaign-phone", "campaign-watch", "campaign-shoes")
    }

    assert len(draws) == 3


def test_purchase_proxy_records_the_key_material_of_its_draw() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)
    campaign = _campaign()

    outcome = decision.purchase_proxy(
        profile,
        state,
        campaign,
        RandomOracle(7),
        simulated_minute=615,
        decision_index=2,
    )

    assert outcome.simulated_minute == 615
    assert outcome.decision_index == 2
    assert outcome.random_draw == pytest.approx(
        RandomOracle(7).uniform(
            f"purchase-proxy:{campaign.campaign_id}",
            state.agent_id,
            615,
            2,
        )
    )


def test_purchase_proxy_records_the_key_material_of_a_skipped_draw() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=False)

    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(),
        RandomOracle(7),
        simulated_minute=615,
        decision_index=2,
    )

    assert outcome.random_draw is None
    assert outcome.simulated_minute == 615
    assert outcome.decision_index == 2


def test_purchase_proxy_hands_the_event_owner_an_enforced_record() -> None:
    """The proxy record is a memory.created contract, not a prose obligation."""
    decision = _decision_module()
    memory = import_module("adlife.core.simulation.memory")
    profile = _profile()
    state = _state(profile, purchase_intention=0.95, disposable_budget=2000.0, active_need=True)

    outcome = _committed_decision(decision, state, _campaign())

    assert {
        "agent_id",
        "campaign_id",
        "simulated_minute",
        "decision_index",
        "intention",
        "threshold",
        "price",
        "budget_before",
        "budget_after",
        "random_draw",
        "committed",
        "reason",
    } <= set(outcome.__slots__)
    record = memory.purchase_proxy_memory(
        outcome,
        memory_id="run-proxy:memory-0001",
        caused_by_event_ids=("run-proxy:noticed-person-001",),
    )
    assert record.kind == "purchase-proxy"
    assert record.campaign_id == outcome.campaign_id
    assert record.created_minute == outcome.simulated_minute


def test_purchase_proxy_rejects_a_negative_decision_index() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)

    with pytest.raises(ValueError, match="decision_index"):
        decision.purchase_proxy(
            profile,
            state,
            _campaign(),
            RandomOracle(7),
            simulated_minute=600,
            decision_index=-1,
        )


def test_purchase_proxy_debits_exactly_the_price_and_leaves_no_debt() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.95, disposable_budget=850.0, active_need=True)
    campaign = _campaign(price=850.0)

    outcome = _committed_decision(decision, state, campaign)

    assert outcome.budget_before == pytest.approx(850.0)
    assert outcome.budget_after == 0.0
    assert outcome.budget_before - outcome.budget_after == pytest.approx(850.0)


def _strong_response(module: ModuleType, *, recall_delta: float = 0.30) -> Any:
    return module.RuleResponse(
        campaign_id="campaign-phone",
        sentiment_delta=0.05,
        recall_delta=recall_delta,
        purchase_intention=0.4,
        share_probability=0.1,
        valence=0.25,
        relevance=0.5,
        credibility=0.7,
    )


def test_one_day_of_responses_cannot_reinforce_recall_beyond_the_daily_cap() -> None:
    decision = _decision_module()
    memory = import_module("adlife.core.simulation.memory")
    profile = _profile()
    state = _state(profile)
    response = _strong_response(decision)

    for index in range(6):
        state = decision.apply_response(state, response, (f"event-{index:04d}",)).state

    assert state.recall_strength == pytest.approx(0.0)
    assert state.daily_reinforcement == pytest.approx(decision.MAX_DAILY_REINFORCEMENT)
    assert memory.decay_memories(state, 0).recall_strength == pytest.approx(
        decision.MAX_DAILY_REINFORCEMENT
    )


def test_a_response_reinforces_nothing_once_the_daily_cap_is_reached() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, recall_strength=0.3, daily_reinforcement=0.20)

    updated = decision.apply_response(state, _strong_response(decision), ("event-0001",)).state

    assert updated.recall_strength == pytest.approx(0.3)
    assert updated.daily_reinforcement == pytest.approx(0.20)


def test_a_partly_spent_daily_cap_admits_only_its_remainder() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, recall_strength=0.15, daily_reinforcement=0.15)

    updated = decision.apply_response(state, _strong_response(decision), ("event-0001",)).state

    assert updated.recall_strength == pytest.approx(0.15)
    assert updated.daily_reinforcement == pytest.approx(0.20)


def test_purchase_decision_rejects_a_budget_that_does_not_conserve_value() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="budget_after"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.9,
            threshold=0.7,
            price=850.0,
            budget_before=900.0,
            budget_after=5000.0,
            random_draw=0.1,
            committed=True,
            reason="committed",
        )


def test_purchase_decision_rejects_a_committed_proxy_that_pays_nothing() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="budget_after"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.9,
            threshold=0.7,
            price=850.0,
            budget_before=900.0,
            budget_after=900.0,
            random_draw=0.1,
            committed=True,
            reason="committed",
        )


def test_purchase_decision_rejects_a_budget_change_without_a_commitment() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="budget_after"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.5,
            threshold=0.7,
            price=850.0,
            budget_before=900.0,
            budget_after=50.0,
            random_draw=None,
            committed=False,
            reason="intention-below-threshold",
        )


def test_purchase_decision_accepts_the_conserving_budget() -> None:
    decision = _decision_module()

    outcome = decision.PurchaseDecision(
        agent_id="person-001",
        campaign_id="campaign-phone",
        simulated_minute=600,
        decision_index=0,
        intention=0.9,
        threshold=0.7,
        price=850.0,
        budget_before=900.0,
        budget_after=50.0,
        random_draw=0.1,
        committed=True,
        reason="committed",
    )

    assert outcome.budget_after == pytest.approx(50.0)


def test_a_committed_proxy_above_the_budget_is_rejected_rather_than_left_debt_free() -> None:
    """A price above the budget is `budget-below-price`, never a commitment clamped to zero."""
    decision = _decision_module()

    with pytest.raises(ValueError, match="budget_before"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.9,
            threshold=0.7,
            price=1200.0,
            budget_before=900.0,
            budget_after=0.0,
            random_draw=0.1,
            committed=True,
            reason="committed",
        )


def test_ad_fatigue_accumulates_the_documented_amount_per_response(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()

    first = decision.apply_response(_state(profile), positive_response, ("event-0001",)).state
    second = decision.apply_response(first, positive_response, ("event-0002",)).state

    assert first.ad_fatigue == pytest.approx(decision.AD_FATIGUE_PER_RESPONSE)
    assert first.ad_fatigue == pytest.approx(0.10)
    assert second.ad_fatigue == pytest.approx(0.20)


def test_purchase_proxy_requires_an_oracle_and_a_simulated_minute() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)

    with pytest.raises(TypeError, match="oracle"):
        decision.purchase_proxy(profile, state, _campaign())
    with pytest.raises(TypeError, match="simulated_minute"):
        decision.purchase_proxy(profile, state, _campaign(), RandomOracle(7))


def test_a_campaign_draw_never_depends_on_how_many_campaigns_preceded_it() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.75, disposable_budget=2000.0, active_need=True)
    campaign = _campaign("campaign-watch")

    alone = decision.purchase_proxy(
        profile,
        state,
        campaign,
        RandomOracle(7),
        simulated_minute=600,
    )
    after_another = decision.purchase_proxy(
        profile,
        state,
        campaign,
        RandomOracle(7),
        simulated_minute=600,
        decision_index=0,
    )
    counted = decision.purchase_proxy(
        profile,
        state,
        campaign,
        RandomOracle(7),
        simulated_minute=600,
        decision_index=1,
    )
    other_campaign = decision.purchase_proxy(
        profile,
        state,
        _campaign("campaign-phone"),
        RandomOracle(7),
        simulated_minute=600,
    )

    assert alone.random_draw == after_another.random_draw
    assert alone.random_draw != other_campaign.random_draw
    assert alone.random_draw != counted.random_draw


def test_evaluate_rule_response_names_the_campaign_it_answers() -> None:
    decision = _decision_module()
    profile = _profile()
    campaign = _campaign()

    response = decision.evaluate_rule_response(
        profile,
        _state(profile),
        campaign,
        campaign.placements[0],
    )

    assert response.campaign_id == campaign.campaign_id


def test_rule_response_rejects_a_campaign_identifier_that_is_not_a_slug() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="campaign_id"):
        decision.RuleResponse(
            campaign_id="Campaign Phone",
            sentiment_delta=0.05,
            recall_delta=0.1,
            purchase_intention=0.4,
            share_probability=0.1,
            valence=0.2,
            relevance=0.5,
            credibility=0.7,
        )


def test_apply_response_marks_direct_campaign_awareness(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()

    transition = decision.apply_response(_state(profile), positive_response, ("event-0001",))

    assert transition.state.aware_campaign_ids == frozenset({"campaign-phone"})


def test_direct_awareness_joins_the_awareness_a_conversation_already_left(
    positive_response: Any,
) -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, aware_campaign_ids=frozenset({"campaign-watch"}))

    transition = decision.apply_response(state, positive_response, ("event-0001",))

    assert transition.state.aware_campaign_ids == frozenset({"campaign-phone", "campaign-watch"})


def test_a_repeated_notice_keeps_one_awareness_entry(positive_response: Any) -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile)

    first = decision.apply_response(state, positive_response, ("event-0001",)).state
    second = decision.apply_response(first, positive_response, ("event-0002",)).state

    assert second.aware_campaign_ids == frozenset({"campaign-phone"})


def test_the_deferred_proxy_record_fits_an_existing_event_payload() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, purchase_intention=0.95, disposable_budget=2000.0, active_need=True)
    campaign = _campaign(price=850.0)
    outcome = _committed_decision(decision, state, campaign)

    payload = {field: getattr(outcome, field) for field in outcome.__slots__}
    event = DomainEvent(
        event_id="run-proxy:state-updated-person-001",
        run_id="run-proxy",
        simulated_minute=outcome.simulated_minute,
        sequence=0,
        event_type=EventType.STATE_UPDATED,
        agent_id=outcome.agent_id,
        campaign_id=outcome.campaign_id,
        payload=payload,
        source=EventSource.RULE,
        caused_by_event_ids=("run-proxy:noticed-person-001",),
    )

    assert event.payload["campaign_id"] == campaign.campaign_id
    assert event.payload["committed"] is True
    assert event.payload["reason"] == "committed"
    assert "sale" not in event.model_dump_json().lower()


def test_purchase_decision_rejects_a_price_that_is_not_positive() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="price"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.9,
            threshold=0.7,
            price=-100.0,
            budget_before=900.0,
            budget_after=1000.0,
            random_draw=0.1,
            committed=True,
            reason="committed",
        )

    with pytest.raises(ValueError, match="price"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.9,
            threshold=0.7,
            price=0.0,
            budget_before=900.0,
            budget_after=900.0,
            random_draw=0.1,
            committed=True,
            reason="committed",
        )


def test_a_negative_price_can_never_mint_disposable_budget() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, disposable_budget=900.0, active_need=True, purchase_intention=0.9)

    with pytest.raises(ValueError, match="price"):
        minted = decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.9,
            threshold=0.7,
            price=-100.0,
            budget_before=900.0,
            budget_after=1000.0,
            random_draw=0.1,
            committed=True,
            reason="committed",
        )
        decision.apply_purchase(state, minted, ("event-0001",))


def test_purchase_decision_rejects_a_commitment_below_its_threshold() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="threshold"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.05,
            threshold=0.70,
            price=850.0,
            budget_before=900.0,
            budget_after=50.0,
            random_draw=0.01,
            committed=True,
            reason="committed",
        )


def test_purchase_decision_rejects_a_commitment_the_budget_cannot_cover() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="budget_before"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.9,
            threshold=0.70,
            price=850.0,
            budget_before=0.0,
            budget_after=0.0,
            random_draw=0.1,
            committed=True,
            reason="committed",
        )


def test_purchase_decision_rejects_a_commitment_whose_draw_missed_the_intention() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="random_draw"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.80,
            threshold=0.70,
            price=850.0,
            budget_before=900.0,
            budget_after=50.0,
            random_draw=0.99,
            committed=True,
            reason="committed",
        )


def test_purchase_decision_rejects_a_random_draw_of_one() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="random_draw"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.5,
            threshold=0.70,
            price=850.0,
            budget_before=900.0,
            budget_after=900.0,
            random_draw=1.0,
            committed=False,
            reason="draw-above-intention",
        )


def test_intention_normalizes_a_sentiment_the_state_can_hold_at_its_ceiling() -> None:
    decision = _decision_module()
    profile = _profile()
    state = _state(profile, brand_sentiment=1.0)
    campaign = _campaign()
    placement = _phone_placement()

    response = decision.evaluate_rule_response(profile, state, campaign, placement)

    value_match = 0.55 * 0.5 + 0.25 * 0.6 + 0.20 * 0.825
    assert response.sentiment_delta > 0.0
    assert response.purchase_intention == pytest.approx(
        0.40 * 1.0 + 0.25 * value_match + 0.15 * 0.3
    )


def test_intention_normalizes_a_sentiment_the_state_can_hold_at_its_floor() -> None:
    decision = _decision_module()
    profile = _profile(
        traits=_traits(advertising_skepticism=1.0, novelty_seeking=0.0, price_sensitivity=1.0),
        interests=frozenset({"fitness"}),
    )
    state = _state(profile, brand_sentiment=-1.0)
    campaign = _campaign()
    placement = _phone_placement()

    response = decision.evaluate_rule_response(profile, state, campaign, placement)

    value_match = 0.20 * 0.4
    assert response.sentiment_delta < 0.0
    assert response.purchase_intention == pytest.approx(
        0.40 * 0.0 + 0.25 * value_match + 0.15 * 0.3
    )


def test_purchase_decision_rejects_a_negative_price_on_an_uncommitted_record() -> None:
    """The price guard is not a side effect of the commitment path."""
    decision = _decision_module()

    with pytest.raises(ValueError, match="price must be a positive amount"):
        decision.PurchaseDecision(
            agent_id="person-001",
            campaign_id="campaign-phone",
            simulated_minute=600,
            decision_index=0,
            intention=0.2,
            threshold=0.7,
            price=-100.0,
            budget_before=900.0,
            budget_after=900.0,
            random_draw=None,
            committed=False,
            reason="no-active-need",
        )


def _proxy_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "agent_id": "person-001",
        "campaign_id": "campaign-phone",
        "simulated_minute": 600,
        "decision_index": 0,
        "intention": 0.9,
        "threshold": 0.7,
        "price": 850.0,
        "budget_before": 900.0,
        "budget_after": 50.0,
        "random_draw": 0.1,
        "committed": True,
        "reason": "committed",
    }
    fields.update(overrides)
    return fields


def test_purchase_decision_rejects_a_commitment_flag_that_is_not_a_bool() -> None:
    decision = _decision_module()

    with pytest.raises(TypeError, match="committed must be a bool"):
        decision.PurchaseDecision(**_proxy_fields(committed=1))


def test_purchase_decision_rejects_an_unsupported_reason() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="unsupported purchase proxy reason"):
        decision.PurchaseDecision(**_proxy_fields(committed=False, reason="sold-out"))


def test_purchase_decision_rejects_a_commitment_that_contradicts_its_reason() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="committed must agree with the recorded reason"):
        decision.PurchaseDecision(
            **_proxy_fields(committed=False, reason="committed", budget_after=900.0)
        )

    with pytest.raises(ValueError, match="committed must agree with the recorded reason"):
        decision.PurchaseDecision(
            **_proxy_fields(committed=True, reason="draw-above-intention", budget_after=900.0)
        )


def test_purchase_decision_rejects_a_commitment_without_a_recorded_draw() -> None:
    decision = _decision_module()

    with pytest.raises(ValueError, match="must record its random draw"):
        decision.PurchaseDecision(**_proxy_fields(random_draw=None))


def _proxy_draw(
    decision: ModuleType,
    *,
    agent_id: str = "person-001",
    campaign_id: str = "campaign-phone",
    simulated_minute: int = 600,
    decision_index: int = 0,
) -> float:
    profile = _profile(agent_id)
    state = _state(profile, purchase_intention=0.99, disposable_budget=5000.0, active_need=True)
    outcome = decision.purchase_proxy(
        profile,
        state,
        _campaign(campaign_id),
        RandomOracle(7),
        simulated_minute=simulated_minute,
        decision_index=decision_index,
    )
    assert outcome.random_draw is not None
    return float(outcome.random_draw)


def test_the_purchase_proxy_key_carries_every_documented_component() -> None:
    """Dropping agent, campaign, minute or decision index from the key must fail here."""
    decision = _decision_module()
    baseline = _proxy_draw(decision)

    assert _proxy_draw(decision, agent_id="person-002") != baseline
    assert _proxy_draw(decision, campaign_id="campaign-watch") != baseline
    assert _proxy_draw(decision, simulated_minute=615) != baseline
    assert _proxy_draw(decision, decision_index=1) != baseline
    assert _proxy_draw(decision) == baseline


def test_the_decision_index_cannot_break_common_random_numbers() -> None:
    """Paired arms that evaluate the same agent, campaign and minute draw the same number.

    ``decision_index`` is scoped to one agent, campaign and simulated minute, so it is 0
    for the first proxy of that triple in every arm; the arms cannot diverge because one
    of them happened to evaluate more campaigns first.
    """
    decision = _decision_module()
    profile = _profile()
    campaigns = ("campaign-phone", "campaign-watch", "campaign-bike")

    treatment = {
        campaign_id: _proxy_draw(decision, campaign_id=campaign_id) for campaign_id in campaigns
    }
    control = {
        campaign_id: _proxy_draw(decision, campaign_id=campaign_id)
        for campaign_id in reversed(campaigns)
    }
    control["campaign-extra"] = _proxy_draw(decision, campaign_id="campaign-extra")

    assert profile.agent_id == "person-001"
    assert all(control[campaign_id] == treatment[campaign_id] for campaign_id in campaigns)
    assert len(set(treatment.values())) == len(campaigns)
