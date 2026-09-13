from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from inspect import Parameter, signature
from types import ModuleType
from typing import get_type_hints

import pytest

from adlife.core.domain.campaign import (
    BillboardPlacement,
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Placement,
    Price,
    TimeWindow,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.state import ConsumerState, ExposureCount
from adlife.core.domain.world import Route, World, Zone
from adlife.core.simulation import engine
from adlife.core.simulation.movement import MovementIntent, Snapshot, resolve_movement
from adlife.core.simulation.rng import RandomOracle


def _exposure_module() -> ModuleType:
    try:
        return import_module("adlife.core.simulation.exposure")
    except ModuleNotFoundError:
        pytest.fail("Task 7 exposure behavior is not implemented", pytrace=False)


def _policies_module() -> ModuleType:
    try:
        return import_module("adlife.core.simulation.policies")
    except ModuleNotFoundError:
        pytest.fail("Task 7 attention policy is not implemented", pytrace=False)


def _profile(
    agent_id: str = "person-001",
    *,
    display_name: str = "Arman 001",
    mobile_attention: float = 0.8,
    outdoor_attention: float = 0.5,
    advertising_skepticism: float = 0.3,
) -> PersonProfile:
    return PersonProfile(
        agent_id=agent_id,
        display_name=display_name,
        fictional=True,
        age=29,
        occupation="office-worker",
        income_band="middle",
        household_type="shared-apartment",
        home_zone="home-north",
        work_or_study_zone="office",
        interests=frozenset({"fitness", "technology"}),
        traits=ConsumerTraits(
            price_sensitivity=0.5,
            novelty_seeking=0.6,
            social_susceptibility=0.4,
            advertising_skepticism=advertising_skepticism,
            mobile_attention=mobile_attention,
            outdoor_attention=outdoor_attention,
            brand_loyalty=0.4,
            impulsivity=0.3,
        ),
        initial_brand_sentiment=0.1,
        routine_template="office-worker",
    )


def _state(
    profile: PersonProfile,
    *,
    location: str = "online",
    activity: str = "phone-check",
    route_id: str | None = None,
    exposure_channel: str | None = None,
    exposure_count: int = 0,
) -> ConsumerState:
    counts = (
        ()
        if exposure_channel is None
        else (
            ExposureCount(
                campaign_id="campaign-phone",
                channel=exposure_channel,
                count=exposure_count,
            ),
        )
    )
    return ConsumerState(
        agent_id=profile.agent_id,
        location=location,
        current_route_id=route_id,
        activity=activity,
        mood=0.0,
        fatigue=0.1,
        brand_sentiment=0.1,
        recall_strength=0.0,
        purchase_intention=0.0,
        exposure_counts=counts,
        cognition_budget_remaining=6,
    )


def _phone_campaign(
    *,
    start_minute: int = 0,
    end_minute: int = 3000,
    windows: tuple[TimeWindow, ...] | None = None,
    placements: tuple[PhonePlacement, ...] | None = None,
) -> Campaign:
    selected_placements = placements or (
        PhonePlacement(
            channel="mobile-feed",
            active_windows=windows or (TimeWindow(start_minute_of_day=600, end_minute_of_day=780),),
            frequency_cap=3,
            visibility=0.8,
        ),
    )
    return Campaign(
        campaign_id="campaign-phone",
        name="Orbit Pocket",
        product_name="Orbit Pocket Device",
        product_category="consumer-electronics",
        message="A synthetic campaign for a fictional pocket device.",
        call_to_action="Explore the fictional device",
        price=Price(amount=850.0, currency="USD"),
        category_reference_price=1000.0,
        target_interests=frozenset({"technology"}),
        start_minute=start_minute,
        end_minute=end_minute,
        placements=selected_placements,
        creative_features=CreativeFeatures(
            description="A fictional device against a clean green field.",
            visual_style="minimal-product",
            dominant_colors=("green", "white"),
            visible_text=("Orbit Pocket",),
            contains_people=False,
        ),
    )


def _billboard_campaign() -> Campaign:
    return Campaign(
        campaign_id="campaign-billboard",
        name="Northline Transit",
        product_name="Northline Pass",
        product_category="mobility-service",
        message="A synthetic campaign for a fictional mobility pass.",
        call_to_action="Review the fictional pass",
        price=Price(amount=24.0, currency="USD"),
        category_reference_price=30.0,
        target_interests=frozenset({"technology", "fitness"}),
        start_minute=0,
        end_minute=3000,
        placements=(
            BillboardPlacement(
                channel="highway-billboard",
                route_id="highway-north",
                active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=600),),
                frequency_cap=4,
                visibility=0.9,
            ),
        ),
        creative_features=CreativeFeatures(
            description="A fictional transit pass on a roadside panel.",
            visual_style="bold-type",
            dominant_colors=("blue", "yellow"),
            visible_text=("Northline Pass",),
            contains_people=False,
        ),
    )


def _snapshot(
    profile: PersonProfile,
    state: ConsumerState,
    *additional_pairs: tuple[PersonProfile, ConsumerState],
    run_id: str | None = "run-exposure",
    next_event_sequence: int = 17,
    simulated_minute: int = 0,
) -> Snapshot:
    pairs = ((profile, state), *additional_pairs)
    return Snapshot(
        agents={profile.agent_id: (profile, state) for profile, state in pairs},
        simulated_minute=simulated_minute,
        run_id=run_id,
        next_event_sequence=next_event_sequence,
    )


class _FixedOracle(RandomOracle):
    def __init__(self, draw: float) -> None:
        super().__init__(root_seed=0)
        object.__setattr__(self, "draw", draw)

    def uniform(
        self,
        namespace: str,
        agent_id: str,
        tick: int,
        decision_index: int,
    ) -> float:
        del namespace, agent_id, tick, decision_index
        return self.draw


class _DuckOracle:
    def uniform(
        self,
        namespace: str,
        agent_id: str,
        tick: int,
        decision_index: int,
    ) -> float:
        del namespace, agent_id, tick, decision_index
        return 0.5


class _CountingOracle(RandomOracle):
    def __init__(self) -> None:
        super().__init__(root_seed=0)
        object.__setattr__(self, "calls", 0)

    def uniform(
        self,
        namespace: str,
        agent_id: str,
        tick: int,
        decision_index: int,
    ) -> float:
        del namespace, agent_id, tick, decision_index
        object.__setattr__(self, "calls", self.calls + 1)
        return 0.5


class _FloatSubclass(float):
    pass


def _bypass_event(event: DomainEvent, **updates: object) -> DomainEvent:
    field_data = {
        name: getattr(event, name) for name in DomainEvent.model_fields if hasattr(event, name)
    }
    field_data.update(updates)
    return DomainEvent.model_construct(**field_data)


def test_task_7_public_function_signatures_remain_exact() -> None:
    exposure = _exposure_module()
    policies = _policies_module()
    eligible_signature = signature(exposure.eligible_placements)
    eligible_hints = get_type_hints(exposure.eligible_placements, include_extras=True)
    policy_signature = signature(policies.notice_probability)
    policy_hints = get_type_hints(policies.notice_probability, include_extras=True)
    decision_signature = signature(exposure.decide_attention)
    decision_hints = get_type_hints(exposure.decide_attention, include_extras=True)

    assert tuple(
        (name, parameter.kind, eligible_hints[name], parameter.default)
        for name, parameter in eligible_signature.parameters.items()
    ) == (
        ("snapshot", Parameter.POSITIONAL_OR_KEYWORD, Snapshot, Parameter.empty),
        ("campaign", Parameter.POSITIONAL_OR_KEYWORD, Campaign, Parameter.empty),
        ("minute", Parameter.POSITIONAL_OR_KEYWORD, int, Parameter.empty),
    )
    assert eligible_hints["return"] == tuple[exposure.ExposureOpportunity, ...]
    assert tuple(
        (name, parameter.kind, policy_hints[name], parameter.default)
        for name, parameter in policy_signature.parameters.items()
    ) == (
        ("profile", Parameter.POSITIONAL_OR_KEYWORD, PersonProfile, Parameter.empty),
        ("state", Parameter.POSITIONAL_OR_KEYWORD, ConsumerState, Parameter.empty),
        ("campaign", Parameter.POSITIONAL_OR_KEYWORD, Campaign, Parameter.empty),
        ("placement", Parameter.POSITIONAL_OR_KEYWORD, Placement, Parameter.empty),
    )
    assert policy_hints["return"] is float
    assert tuple(
        (name, parameter.kind, decision_hints[name], parameter.default)
        for name, parameter in decision_signature.parameters.items()
    ) == (
        (
            "opportunity",
            Parameter.POSITIONAL_OR_KEYWORD,
            exposure.ExposureOpportunity,
            Parameter.empty,
        ),
        ("oracle", Parameter.POSITIONAL_OR_KEYWORD, RandomOracle, Parameter.empty),
    )
    assert decision_hints["return"] is exposure.AttentionDecision


@pytest.mark.parametrize(
    ("location", "activity"),
    (("office", "phone-check"), ("online", "work"), ("office", "work")),
)
def test_mobile_eligibility_requires_phone_check_and_online(
    location: str,
    activity: str,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    snapshot = _snapshot(profile, _state(profile, location=location, activity=activity))

    assert exposure.eligible_placements(snapshot, _phone_campaign(), 600) == ()


def test_mobile_eligibility_accepts_a_one_agent_plain_snapshot() -> None:
    exposure = _exposure_module()
    profile = _profile()
    snapshot = _snapshot(profile, _state(profile))

    opportunities = exposure.eligible_placements(snapshot, _phone_campaign(), 600)

    assert len(opportunities) == 1
    assert opportunities[0].agent_id == "person-001"
    assert opportunities[0].channel == "mobile-feed"


@pytest.mark.parametrize(
    ("activity", "route_id"),
    (("work", "highway-north"), ("commute", None), ("commute", "highway-center")),
)
def test_billboard_eligibility_requires_commute_on_the_matching_current_route(
    activity: str,
    route_id: str | None,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    state = _state(
        profile,
        location="highway-north",
        activity=activity,
        route_id=route_id,
    )

    assert (
        exposure.eligible_placements(
            _snapshot(profile, state),
            _billboard_campaign(),
            480,
        )
        == ()
    )


def test_billboard_eligibility_accepts_matching_highway_traversal() -> None:
    exposure = _exposure_module()
    profile = _profile()
    state = _state(
        profile,
        location="highway-north",
        activity="commute",
        route_id="highway-north",
    )

    opportunities = exposure.eligible_placements(
        _snapshot(profile, state),
        _billboard_campaign(),
        480,
    )

    assert len(opportunities) == 1
    assert opportunities[0].placement.route_id == "highway-north"


@pytest.mark.parametrize(
    ("minute", "eligible"),
    ((599, False), (600, True), (899, True), (900, False)),
)
def test_campaign_period_is_inclusive_start_and_exclusive_end(
    minute: int,
    eligible: bool,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    campaign = _phone_campaign(
        start_minute=600,
        end_minute=900,
        windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=1440),),
    )

    opportunities = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        campaign,
        minute,
    )

    assert bool(opportunities) is eligible


@pytest.mark.parametrize(
    ("minute", "eligible"),
    (
        (0, True),
        (14, True),
        (15, False),
        (1439, False),
        (1440, True),
        (1454, True),
        (1455, False),
    ),
)
def test_placement_windows_use_minute_of_day_with_exclusive_end(
    minute: int,
    eligible: bool,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    campaign = _phone_campaign(
        windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=15),),
    )

    opportunities = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        campaign,
        minute,
    )

    assert bool(opportunities) is eligible


def test_frequency_cap_prevents_an_additional_opportunity_at_the_cap() -> None:
    exposure = _exposure_module()
    profile = _profile()
    state = _state(
        profile,
        exposure_channel="mobile-feed",
        exposure_count=3,
    )

    assert (
        exposure.eligible_placements(
            _snapshot(profile, state),
            _phone_campaign(),
            600,
        )
        == ()
    )


def test_frequency_cap_allows_the_last_opportunity_below_the_cap() -> None:
    exposure = _exposure_module()
    profile = _profile()
    state = _state(
        profile,
        exposure_channel="mobile-feed",
        exposure_count=2,
    )

    assert (
        len(
            exposure.eligible_placements(
                _snapshot(profile, state),
                _phone_campaign(),
                600,
            )
        )
        == 1
    )


def test_frequency_caps_are_scoped_to_campaign_and_channel() -> None:
    exposure = _exposure_module()
    profile = _profile()
    state = _state(
        profile,
        exposure_channel="highway-billboard",
        exposure_count=14,
    )

    assert (
        len(
            exposure.eligible_placements(
                _snapshot(profile, state),
                _phone_campaign(),
                600,
            )
        )
        == 1
    )


def test_simultaneous_placements_cannot_collectively_exceed_the_frequency_cap() -> None:
    exposure = _exposure_module()
    profile = _profile()
    first = PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=1440),),
        frequency_cap=1,
        visibility=0.4,
    )
    second = first.model_copy(
        update={
            "active_windows": (TimeWindow(start_minute_of_day=500, end_minute_of_day=700),),
            "visibility": 0.9,
        }
    )
    campaign = _phone_campaign(placements=(second, first))

    opportunities = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        campaign,
        600,
    )

    assert len(opportunities) == 1


def test_structurally_duplicate_placements_require_a_distinct_sampling_identity() -> None:
    exposure = _exposure_module()
    profile = _profile()
    first = PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=1440),),
        frequency_cap=3,
        visibility=0.4,
    )
    treatment_variant = first.model_copy(update={"frequency_cap": 4, "visibility": 0.9})
    campaign = _phone_campaign(placements=(first, treatment_variant))
    snapshot = _snapshot(profile, _state(profile), simulated_minute=600)

    with pytest.raises(ValueError, match="semantic sampling identity"):
        exposure.eligible_placements(snapshot, campaign, 600)
    with pytest.raises(ValueError, match="semantic sampling identity"):
        exposure.allocate_exposure_batch(snapshot, (campaign,), 600)


def test_invalid_or_unbound_exposure_context_is_rejected() -> None:
    exposure = _exposure_module()
    profile = _profile()
    bound = _snapshot(profile, _state(profile))
    unbound = _snapshot(profile, _state(profile), run_id=None)

    with pytest.raises(ValueError, match="nonnegative integer"):
        exposure.eligible_placements(bound, _phone_campaign(), True)
    with pytest.raises(engine.UnboundRun, match="bind_run"):
        exposure.eligible_placements(unbound, _phone_campaign(), 600)


def test_eligibility_revalidates_bypass_constructed_nested_profile() -> None:
    exposure = _exposure_module()
    profile = _profile()
    invalid_traits = ConsumerTraits.model_construct(
        **{
            **{name: getattr(profile.traits, name) for name in ConsumerTraits.model_fields},
            "mobile_attention": 2.0,
        }
    )
    invalid_profile = PersonProfile.model_construct(
        **{
            **{name: getattr(profile, name) for name in PersonProfile.model_fields},
            "traits": invalid_traits,
        }
    )
    snapshot = _snapshot(invalid_profile, _state(profile))

    with pytest.raises(ValueError, match="less than or equal to 1"):
        exposure.eligible_placements(snapshot, _phone_campaign(), 600)


def test_eligibility_revalidates_bypass_constructed_nested_state() -> None:
    exposure = _exposure_module()
    profile = _profile()
    state = _state(profile)
    invalid_count = ExposureCount.model_construct(
        campaign_id="campaign-phone",
        channel="mobile-feed",
        count=-1,
    )
    invalid_state = ConsumerState.model_construct(
        **{
            **{name: getattr(state, name) for name in ConsumerState.model_fields},
            "exposure_counts": (invalid_count,),
        }
    )
    snapshot = _snapshot(profile, invalid_state)

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        exposure.eligible_placements(snapshot, _phone_campaign(), 600)


def test_eligibility_revalidates_bypass_constructed_campaign_placement() -> None:
    exposure = _exposure_module()
    profile = _profile()
    campaign = _phone_campaign()
    placement = campaign.placements[0]
    invalid_window = TimeWindow.model_construct(
        start_minute_of_day=900,
        end_minute_of_day=600,
    )
    invalid_placement = PhonePlacement.model_construct(
        **{
            **{name: getattr(placement, name) for name in PhonePlacement.model_fields},
            "active_windows": (invalid_window,),
        }
    )
    invalid_campaign = Campaign.model_construct(
        **{
            **{name: getattr(campaign, name) for name in Campaign.model_fields},
            "placements": (invalid_placement,),
        }
    )

    with pytest.raises(ValueError, match="must be after"):
        exposure.eligible_placements(
            _snapshot(profile, _state(profile)),
            invalid_campaign,
            600,
        )


def test_public_boundaries_reject_non_finite_nested_domain_values() -> None:
    exposure = _exposure_module()
    policies = _policies_module()
    profile = _profile()
    campaign = _phone_campaign()
    invalid_price = Price.model_construct(amount=float("inf"), currency="USD")
    invalid_campaign = Campaign.model_construct(
        **{
            **{name: getattr(campaign, name) for name in Campaign.model_fields},
            "price": invalid_price,
        }
    )
    state = _state(profile)
    invalid_state = ConsumerState.model_construct(
        **{
            **{name: getattr(state, name) for name in ConsumerState.model_fields},
            "disposable_budget": float("inf"),
        }
    )

    with pytest.raises(ValueError, match="finite"):
        exposure.eligible_placements(
            _snapshot(profile, state),
            invalid_campaign,
            600,
        )
    with pytest.raises(ValueError, match="finite"):
        policies.notice_probability(
            profile,
            invalid_state,
            campaign,
            campaign.placements[0],
        )


@pytest.mark.parametrize(
    "amount",
    (
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("NaN"),
        Decimal("1e1000000"),
    ),
)
def test_eligibility_rejects_decimal_non_finite_or_float_overflow(
    amount: Decimal,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    campaign = _phone_campaign()
    invalid_campaign = Campaign.model_construct(
        **{
            **{name: getattr(campaign, name) for name in Campaign.model_fields},
            "price": Price.model_construct(amount=amount, currency="USD"),
        }
    )

    with pytest.raises(ValueError, match="finite"):
        exposure.eligible_placements(
            _snapshot(profile, _state(profile)),
            invalid_campaign,
            600,
        )


def test_notice_probability_rejects_decimal_infinity_in_nested_state() -> None:
    policies = _policies_module()
    profile = _profile()
    state = _state(profile)
    invalid_state = ConsumerState.model_construct(
        **{
            **{name: getattr(state, name) for name in ConsumerState.model_fields},
            "disposable_budget": Decimal("Infinity"),
        }
    )
    campaign = _phone_campaign()

    with pytest.raises(ValueError, match="finite"):
        policies.notice_probability(
            profile,
            invalid_state,
            campaign,
            campaign.placements[0],
        )


def test_regular_domain_models_reject_positive_infinity() -> None:
    profile = _profile()

    with pytest.raises(ValueError, match="finite"):
        Price(amount=float("inf"), currency="USD")
    with pytest.raises(ValueError, match="finite"):
        _state(profile).model_copy(update={"disposable_budget": float("inf")})


def test_eligibility_normalizes_a_bypass_mutated_snapshot_shape() -> None:
    exposure = _exposure_module()
    profile = _profile()
    snapshot = _snapshot(profile, _state(profile))
    object.__setattr__(snapshot, "agents", ())

    with pytest.raises(TypeError, match="mapping"):
        exposure.eligible_placements(snapshot, _phone_campaign(), 600)


def test_multi_agent_and_multi_placement_outputs_are_canonical() -> None:
    exposure = _exposure_module()
    first = _profile()
    second = _profile("person-002", display_name="Mina 002")
    first_state = _state(first)
    second_state = _state(second)
    low = PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=1440),),
        frequency_cap=3,
        visibility=0.2,
    )
    high = low.model_copy(
        update={
            "active_windows": (TimeWindow(start_minute_of_day=300, end_minute_of_day=900),),
            "visibility": 0.9,
        }
    )
    forward_campaign = _phone_campaign(placements=(low, high))
    reverse_campaign = _phone_campaign(placements=(high, low))
    forward_snapshot = _snapshot(
        second,
        second_state,
        (first, first_state),
        next_event_sequence=5,
    )
    reverse_snapshot = _snapshot(
        first,
        first_state,
        (second, second_state),
        next_event_sequence=5,
    )

    forward = exposure.eligible_placements(forward_snapshot, forward_campaign, 600)
    reverse = exposure.eligible_placements(reverse_snapshot, reverse_campaign, 600)
    forward_keys = tuple((item.agent_id, item.placement_identity) for item in forward)
    reverse_keys = tuple((item.agent_id, item.placement_identity) for item in reverse)
    forward_events = tuple(
        event
        for item in forward
        for event in exposure.decide_attention(item, RandomOracle(42)).events
    )
    reverse_events = tuple(
        event
        for item in reverse
        for event in exposure.decide_attention(item, RandomOracle(42)).events
    )

    assert forward_keys == tuple(sorted(forward_keys))
    assert reverse_keys == forward_keys
    assert tuple(item.event_sequence_start for item in forward) == (5, 8, 11, 14)
    assert reverse_events == forward_events


def _online_movement_world() -> World:
    return World(
        world_id="exposure-stage-world",
        zones=(
            Zone(zone_id="office", name="Office", kind="work"),
            Zone(zone_id="online", name="Online", kind="online"),
        ),
        routes=(
            Route(
                route_id="office-online",
                source_zone="office",
                target_zone="online",
                travel_minutes=15,
            ),
        ),
    )


def test_exposure_batch_projects_movement_before_allocating_across_campaigns() -> None:
    exposure = _exposure_module()
    first_profile = _profile()
    second_profile = _profile("person-002", display_name="Mina 002")
    snapshot = _snapshot(
        second_profile,
        _state(second_profile, location="office", activity="work"),
        (first_profile, _state(first_profile, location="office", activity="work")),
        next_event_sequence=17,
        simulated_minute=600,
    )
    movement_events = resolve_movement(
        (
            MovementIntent(
                agent_id="person-002",
                from_zone="office",
                to_zone="online",
                activity="phone-check",
                route_id="office-online",
            ),
            MovementIntent(
                agent_id="person-001",
                from_zone="office",
                to_zone="online",
                activity="phone-check",
                route_id="office-online",
            ),
        ),
        _online_movement_world(),
        snapshot,
    )
    first_campaign = _phone_campaign()
    second_campaign = first_campaign.model_copy(
        update={
            "campaign_id": "campaign-phone-second",
            "name": "Orbit Pocket Second",
        }
    )

    forward = exposure.allocate_exposure_batch(
        snapshot,
        (second_campaign, first_campaign),
        600,
        preceding_events=tuple(reversed(movement_events)),
    )
    reverse = exposure.allocate_exposure_batch(
        snapshot,
        (first_campaign, second_campaign),
        600,
        preceding_events=movement_events,
    )
    forward_decisions = exposure.decide_attention_batch(forward, RandomOracle(42))
    reverse_decisions = exposure.decide_attention_batch(reverse, RandomOracle(42))
    all_events = (
        *forward.preceding_events,
        *(event for decision in forward_decisions for event in decision.events),
    )

    assert forward == reverse
    assert forward_decisions == reverse_decisions
    assert forward.start_cursor.next_event_sequence == 17
    assert tuple(event.sequence for event in forward.preceding_events) == (17, 18, 19, 20)
    assert tuple(item.event_sequence_start for item in forward.opportunities) == (
        21,
        24,
        27,
        30,
    )
    assert all(item.state.activity == "phone-check" for item in forward.opportunities)
    assert all(item.state.location == "online" for item in forward.opportunities)
    assert forward.next_cursor.next_event_sequence == 33
    assert tuple(event.sequence for event in all_events) == tuple(range(17, 33))
    assert tuple(event.event_id for event in all_events) == tuple(
        f"run-exposure:event-{sequence:08d}" for sequence in range(17, 33)
    )
    assert len({event.event_id for event in all_events}) == len(all_events)


def test_exposure_batch_projects_movement_that_removes_mobile_eligibility() -> None:
    exposure = _exposure_module()
    profile = _profile()
    snapshot = _snapshot(
        profile,
        _state(profile),
        next_event_sequence=17,
        simulated_minute=600,
    )
    movement_events = resolve_movement(
        (
            MovementIntent(
                agent_id=profile.agent_id,
                from_zone="online",
                to_zone="office",
                activity="work",
                route_id="office-online",
            ),
        ),
        _online_movement_world(),
        snapshot,
    )

    batch = exposure.allocate_exposure_batch(
        snapshot,
        (_phone_campaign(),),
        600,
        preceding_events=movement_events,
    )

    assert tuple(event.sequence for event in batch.preceding_events) == (17, 18)
    assert batch.opportunities == ()
    assert batch.next_cursor.next_event_sequence == 19


def test_exposure_batch_without_movement_uses_the_pre_tick_snapshot() -> None:
    exposure = _exposure_module()
    profile = _profile()
    snapshot = _snapshot(
        profile,
        _state(profile),
        next_event_sequence=17,
        simulated_minute=600,
    )

    batch = exposure.allocate_exposure_batch(snapshot, (_phone_campaign(),), 600)

    assert batch.preceding_events == ()
    assert len(batch.opportunities) == 1
    assert batch.opportunities[0].state == snapshot.agents[profile.agent_id][1]
    assert batch.opportunities[0].event_sequence_start == 17
    assert batch.next_cursor.next_event_sequence == 20


def test_exposure_batch_rejects_duplicate_semantic_opportunities() -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile), simulated_minute=600),
        _phone_campaign(),
        600,
    )[0]
    duplicate = opportunity.model_copy(update={"event_sequence_start": 20})
    start_cursor = exposure.ExposureCursor(
        run_id="run-exposure",
        simulated_minute=600,
        next_event_sequence=17,
    )
    next_cursor = exposure.ExposureCursor(
        run_id="run-exposure",
        simulated_minute=600,
        next_event_sequence=23,
    )

    with pytest.raises(ValueError, match="duplicate semantic opportunity"):
        exposure.ExposureBatch(
            start_cursor=start_cursor,
            preceding_events=(),
            opportunities=(opportunity, duplicate),
            next_cursor=next_cursor,
        )


def test_decide_attention_batch_rejects_bypassed_duplicates_before_drawing() -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile), simulated_minute=600),
        _phone_campaign(),
        600,
    )[0]
    duplicate = opportunity.model_copy(update={"event_sequence_start": 20})
    invalid_batch = exposure.ExposureBatch.model_construct(
        start_cursor=exposure.ExposureCursor(
            run_id="run-exposure",
            simulated_minute=600,
            next_event_sequence=17,
        ),
        preceding_events=(),
        opportunities=(opportunity, duplicate),
        next_cursor=exposure.ExposureCursor(
            run_id="run-exposure",
            simulated_minute=600,
            next_event_sequence=23,
        ),
    )
    oracle = _CountingOracle()

    with pytest.raises(ValueError, match="duplicate semantic opportunity"):
        exposure.decide_attention_batch(invalid_batch, oracle)
    assert oracle.calls == 0


def test_exposure_batch_rejects_conflicting_definitions_for_one_campaign_id() -> None:
    exposure = _exposure_module()
    profile = _profile()
    snapshot = _snapshot(
        profile,
        _state(profile),
        next_event_sequence=17,
        simulated_minute=600,
    )
    first_campaign = _phone_campaign()
    second_placement = first_campaign.placements[0].model_copy(
        update={"active_windows": (TimeWindow(start_minute_of_day=500, end_minute_of_day=700),)}
    )
    second_campaign = first_campaign.model_copy(
        update={
            "name": "Conflicting Orbit Definition",
            "placements": (second_placement,),
        }
    )

    with pytest.raises(ValueError, match="campaign IDs"):
        exposure.allocate_exposure_batch(
            snapshot,
            (first_campaign, second_campaign),
            600,
        )

    raw_opportunities = (
        exposure.eligible_placements(snapshot, first_campaign, 600)[0],
        exposure.eligible_placements(snapshot, second_campaign, 600)[0],
    )
    ordered = tuple(
        sorted(
            raw_opportunities,
            key=lambda item: (
                item.agent_id,
                item.campaign_id,
                item.placement_identity,
                item.sampling_identity,
            ),
        )
    )
    opportunities = tuple(
        item.model_copy(update={"event_sequence_start": 17 + index * 3})
        for index, item in enumerate(ordered)
    )

    with pytest.raises(ValueError, match="campaign definition"):
        exposure.ExposureBatch(
            start_cursor=exposure.ExposureCursor(
                run_id="run-exposure",
                simulated_minute=600,
                next_event_sequence=17,
            ),
            preceding_events=(),
            opportunities=opportunities,
            next_cursor=exposure.ExposureCursor(
                run_id="run-exposure",
                simulated_minute=600,
                next_event_sequence=23,
            ),
        )


@pytest.mark.parametrize(
    "corruption",
    (
        "contradictory",
        "contradictory-location",
        "duplicate",
        "incomplete",
        "impossible",
        "noncanonical",
        "route-alias",
        "route-type",
        "unknown-agent",
    ),
)
def test_exposure_batch_rejects_malformed_movement_chains(corruption: str) -> None:
    exposure = _exposure_module()
    profile = _profile()
    snapshot = _snapshot(
        profile,
        _state(profile, location="office", activity="work"),
        next_event_sequence=17,
        simulated_minute=600,
    )
    activity, location = resolve_movement(
        (
            MovementIntent(
                agent_id=profile.agent_id,
                from_zone="office",
                to_zone="online",
                activity="phone-check",
                route_id="office-online",
            ),
        ),
        _online_movement_world(),
        snapshot,
    )
    events = [activity, location]

    if corruption == "contradictory":
        events[0] = _bypass_event(
            activity,
            payload={"from_activity": "sleep", "to_activity": "phone-check"},
        )
    elif corruption == "contradictory-location":
        events[1] = _bypass_event(
            location,
            payload={**dict(location.payload), "from_zone": "home-north"},
        )
    elif corruption == "duplicate":
        events[1] = _bypass_event(
            activity,
            sequence=18,
            event_id=engine.stable_event_id("run-exposure", 18),
        )
    elif corruption == "incomplete":
        events[1] = _bypass_event(
            location,
            payload={
                "to_zone": "online",
                "route_id": "office-online",
                "from_route_id": None,
                "to_route_id": "office-online",
            },
        )
    elif corruption == "impossible":
        events[1] = _bypass_event(
            location,
            payload={
                "from_zone": "office",
                "to_zone": "office",
                "route_id": None,
                "from_route_id": None,
                "to_route_id": None,
            },
        )
    elif corruption == "noncanonical":
        events = [
            _bypass_event(
                location,
                sequence=17,
                event_id=engine.stable_event_id("run-exposure", 17),
            ),
            _bypass_event(
                activity,
                sequence=18,
                event_id=engine.stable_event_id("run-exposure", 18),
            ),
        ]
    elif corruption == "route-alias":
        events[1] = _bypass_event(
            location,
            payload={**dict(location.payload), "route_id": None},
        )
    elif corruption == "route-type":
        events[1] = _bypass_event(
            location,
            payload={**dict(location.payload), "route_id": True},
        )
    elif corruption == "unknown-agent":
        events[1] = _bypass_event(location, agent_id="person-999")

    with pytest.raises(ValueError, match="movement"):
        exposure.allocate_exposure_batch(
            snapshot,
            (_phone_campaign(),),
            600,
            preceding_events=events,
        )


def test_active_window_reordering_preserves_placement_identity_and_draw() -> None:
    exposure = _exposure_module()
    profile = _profile()
    morning = TimeWindow(start_minute_of_day=0, end_minute_of_day=720)
    evening = TimeWindow(start_minute_of_day=720, end_minute_of_day=1440)
    forward_placement = PhonePlacement(
        channel="mobile-feed",
        active_windows=(morning, evening),
        frequency_cap=3,
        visibility=0.8,
    )
    reverse_placement = forward_placement.model_copy(update={"active_windows": (evening, morning)})
    snapshot = _snapshot(profile, _state(profile))
    forward = exposure.eligible_placements(
        snapshot,
        _phone_campaign(placements=(forward_placement,)),
        600,
    )[0]
    reverse = exposure.eligible_placements(
        snapshot,
        _phone_campaign(placements=(reverse_placement,)),
        600,
    )[0]

    assert reverse.placement_identity == forward.placement_identity
    assert reverse.sampling_identity == forward.sampling_identity
    reverse_draw = exposure.decide_attention(reverse, RandomOracle(42)).random_draw
    forward_draw = exposure.decide_attention(forward, RandomOracle(42)).random_draw
    assert reverse_draw == forward_draw


def test_visibility_treatment_changes_probability_but_preserves_random_draw() -> None:
    exposure = _exposure_module()
    profile = _profile()
    low = PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=1440),),
        frequency_cap=3,
        visibility=0.2,
    )
    high = low.model_copy(update={"visibility": 0.9})
    snapshot = _snapshot(profile, _state(profile))
    low_opportunity = exposure.eligible_placements(
        snapshot,
        _phone_campaign(placements=(low,)),
        600,
    )[0]
    high_opportunity = exposure.eligible_placements(
        snapshot,
        _phone_campaign(placements=(high,)),
        600,
    )[0]
    low_decision = exposure.decide_attention(low_opportunity, RandomOracle(42))
    high_decision = exposure.decide_attention(high_opportunity, RandomOracle(42))

    assert low_opportunity.placement_identity != high_opportunity.placement_identity
    assert low_opportunity.sampling_identity == high_opportunity.sampling_identity
    assert low_decision.random_draw == high_decision.random_draw
    assert low_decision.notice_probability < high_decision.notice_probability


def test_frequency_cap_treatment_preserves_draw_and_changes_policy_and_eligibility() -> None:
    exposure = _exposure_module()
    profile = _profile()
    low_cap = PhonePlacement(
        channel="mobile-feed",
        active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=1440),),
        frequency_cap=2,
        visibility=0.8,
    )
    high_cap = low_cap.model_copy(update={"frequency_cap": 4})
    once_exposed = _state(
        profile,
        exposure_channel="mobile-feed",
        exposure_count=1,
    )
    snapshot = _snapshot(profile, once_exposed)
    low_opportunity = exposure.eligible_placements(
        snapshot,
        _phone_campaign(placements=(low_cap,)),
        600,
    )[0]
    high_opportunity = exposure.eligible_placements(
        snapshot,
        _phone_campaign(placements=(high_cap,)),
        600,
    )[0]
    low_decision = exposure.decide_attention(low_opportunity, RandomOracle(42))
    high_decision = exposure.decide_attention(high_opportunity, RandomOracle(42))
    capped_state = _state(
        profile,
        exposure_channel="mobile-feed",
        exposure_count=2,
    )

    assert low_opportunity.sampling_identity == high_opportunity.sampling_identity
    assert low_decision.random_draw == high_decision.random_draw
    assert low_decision.notice_probability < high_decision.notice_probability
    assert (
        exposure.eligible_placements(
            _snapshot(profile, capped_state),
            _phone_campaign(placements=(low_cap,)),
            600,
        )
        == ()
    )
    assert (
        len(
            exposure.eligible_placements(
                _snapshot(profile, capped_state),
                _phone_campaign(placements=(high_cap,)),
                600,
            )
        )
        == 1
    )


def test_notice_probability_matches_the_documented_formula() -> None:
    policies = _policies_module()
    profile = _profile()
    campaign = _phone_campaign()
    placement = campaign.placements[0]

    probability = policies.notice_probability(
        profile,
        _state(profile),
        campaign,
        placement,
    )

    assert probability == pytest.approx(0.7170752854929725)


def test_notice_probability_revalidates_nested_values_and_relationships() -> None:
    policies = _policies_module()
    profile = _profile()
    campaign = _phone_campaign()
    invalid_traits = ConsumerTraits.model_construct(
        **{
            **{name: getattr(profile.traits, name) for name in ConsumerTraits.model_fields},
            "mobile_attention": float("nan"),
        }
    )
    invalid_profile = PersonProfile.model_construct(
        **{
            **{name: getattr(profile, name) for name in PersonProfile.model_fields},
            "traits": invalid_traits,
        }
    )
    other_profile = _profile("person-002", display_name="Mina 002")
    unrelated_placement = campaign.placements[0].model_copy(update={"visibility": 0.4})

    with pytest.raises(ValueError, match="finite"):
        policies.notice_probability(
            invalid_profile,
            _state(profile),
            campaign,
            campaign.placements[0],
        )
    with pytest.raises(ValueError, match="same agent"):
        policies.notice_probability(
            profile,
            _state(other_profile),
            campaign,
            campaign.placements[0],
        )
    with pytest.raises(ValueError, match="belong"):
        policies.notice_probability(
            profile,
            _state(profile),
            campaign,
            unrelated_placement,
        )


@pytest.mark.parametrize(("value", "expected"), ((-1e308, 0.0), (1e308, 1.0)))
def test_sigmoid_handles_extreme_finite_logits_without_overflow(
    value: float,
    expected: float,
) -> None:
    policies = _policies_module()

    assert policies.sigmoid(value) == expected


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_sigmoid_rejects_non_finite_values(value: float) -> None:
    policies = _policies_module()

    with pytest.raises(ValueError, match="finite"):
        policies.sigmoid(value)


@pytest.mark.parametrize(
    ("channel", "attention_field"),
    (("mobile-feed", "mobile_attention"), ("highway-billboard", "outdoor_attention")),
)
def test_higher_channel_attention_never_reduces_notice_probability(
    channel: str,
    attention_field: str,
) -> None:
    policies = _policies_module()
    campaign = _phone_campaign() if channel == "mobile-feed" else _billboard_campaign()
    placement = campaign.placements[0]
    low = _profile(mobile_attention=0.1, outdoor_attention=0.1)
    high_traits = low.traits.model_copy(update={attention_field: 0.9})
    high = low.model_copy(update={"traits": high_traits})

    low_probability = policies.notice_probability(low, _state(low), campaign, placement)
    high_probability = policies.notice_probability(high, _state(high), campaign, placement)

    assert high_probability >= low_probability


def test_higher_billboard_visibility_strictly_increases_notice_probability() -> None:
    policies = _policies_module()
    profile = _profile()
    campaign = _billboard_campaign()
    placement = campaign.placements[0]
    low_placement = placement.model_copy(update={"visibility": 0.1})
    high_placement = placement.model_copy(update={"visibility": 0.9})
    low_campaign = campaign.model_copy(update={"placements": (low_placement,)})
    high_campaign = campaign.model_copy(update={"placements": (high_placement,)})
    state = _state(
        profile,
        location="highway-north",
        activity="commute",
        route_id="highway-north",
    )

    low_probability = policies.notice_probability(
        profile,
        state,
        low_campaign,
        low_placement,
    )
    high_probability = policies.notice_probability(
        profile,
        state,
        high_campaign,
        high_placement,
    )

    assert high_probability > low_probability


@pytest.mark.parametrize(("low", "high"), ((0.0, 0.25), (0.25, 0.75), (0.75, 1.0)))
def test_higher_advertising_skepticism_never_increases_notice_probability(
    low: float,
    high: float,
) -> None:
    policies = _policies_module()
    campaign = _phone_campaign()
    placement = campaign.placements[0]
    low_profile = _profile(advertising_skepticism=low)
    high_profile = _profile(advertising_skepticism=high)

    low_probability = policies.notice_probability(
        low_profile,
        _state(low_profile),
        campaign,
        placement,
    )
    high_probability = policies.notice_probability(
        high_profile,
        _state(high_profile),
        campaign,
        placement,
    )

    assert high_probability <= low_probability


def test_frequency_fatigue_reduces_notice_probability_and_results_stay_bounded() -> None:
    policies = _policies_module()
    campaign = _phone_campaign()
    placement = campaign.placements[0]
    low_profile = _profile(
        mobile_attention=0.0,
        advertising_skepticism=1.0,
    )
    high_profile = low_profile.model_copy(
        update={
            "interests": frozenset({"technology"}),
            "traits": low_profile.traits.model_copy(
                update={"mobile_attention": 1.0, "advertising_skepticism": 0.0}
            ),
        }
    )
    fresh = _state(low_profile)
    fatigued = _state(
        low_profile,
        exposure_channel="mobile-feed",
        exposure_count=3,
    )

    fresh_probability = policies.notice_probability(low_profile, fresh, campaign, placement)
    fatigued_probability = policies.notice_probability(
        low_profile,
        fatigued,
        campaign,
        placement,
    )
    high_probability = policies.notice_probability(
        high_profile,
        _state(high_profile),
        campaign,
        placement,
    )

    assert fatigued_probability < fresh_probability
    assert 0.0 <= fatigued_probability <= 1.0
    assert 0.0 <= high_probability <= 1.0


def test_attention_draw_uses_the_named_stable_identity_stream() -> None:
    exposure = _exposure_module()
    profile = _profile()
    campaign = _phone_campaign()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        campaign,
        600,
    )[0]
    oracle = RandomOracle(42)

    decision = exposure.decide_attention(opportunity, oracle)
    expected = oracle.uniform(
        f"campaign-attention:campaign-phone:mobile-feed:{opportunity.sampling_identity}",
        "person-001",
        600,
        0,
    )

    assert decision.random_draw == expected
    assert decision == exposure.decide_attention(opportunity, oracle)


def test_decide_attention_revalidates_opportunity_and_requires_random_oracle() -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        _phone_campaign(),
        600,
    )[0]
    invalid_opportunity = exposure.ExposureOpportunity.model_construct(
        **{
            **{
                name: getattr(opportunity, name)
                for name in exposure.ExposureOpportunity.model_fields
            },
            "placement_identity": "0" * 64,
        }
    )
    ineligible_opportunity = exposure.ExposureOpportunity.model_construct(
        **{
            **{
                name: getattr(opportunity, name)
                for name in exposure.ExposureOpportunity.model_fields
            },
            "state": _state(profile, activity="work"),
        }
    )

    with pytest.raises(ValueError, match="placement_identity"):
        exposure.decide_attention(invalid_opportunity, RandomOracle(42))
    with pytest.raises(ValueError, match="eligible"):
        exposure.decide_attention(ineligible_opportunity, RandomOracle(42))
    with pytest.raises(TypeError, match="RandomOracle"):
        exposure.decide_attention(opportunity, _DuckOracle())


@pytest.mark.parametrize(
    "draw",
    (float("nan"), float("inf"), float("-inf"), -0.1, 1.0),
)
def test_decide_attention_rejects_invalid_random_oracle_draws(draw: float) -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        _phone_campaign(),
        600,
    )[0]

    with pytest.raises(ValueError, match="RandomOracle draw"):
        exposure.decide_attention(opportunity, _FixedOracle(draw))


def test_display_name_does_not_change_attention_or_causal_events() -> None:
    exposure = _exposure_module()
    original = _profile(display_name="Arman 001")
    renamed = _profile(display_name="Fictional Alias 001")
    campaign = _phone_campaign()
    original_opportunity = exposure.eligible_placements(
        _snapshot(original, _state(original)),
        campaign,
        600,
    )[0]
    renamed_opportunity = exposure.eligible_placements(
        _snapshot(renamed, _state(renamed)),
        campaign,
        600,
    )[0]

    original_decision = exposure.decide_attention(original_opportunity, RandomOracle(42))
    renamed_decision = exposure.decide_attention(renamed_opportunity, RandomOracle(42))

    assert renamed_decision.notice_probability == original_decision.notice_probability
    assert renamed_decision.random_draw == original_decision.random_draw
    assert renamed_decision.events == original_decision.events


@pytest.mark.parametrize(
    ("draw", "terminal_type", "noticed"),
    (
        (0.0, EventType.CAMPAIGN_NOTICED, True),
        (0.999999, EventType.CAMPAIGN_IGNORED, False),
    ),
)
def test_attention_emits_one_complete_deterministic_causal_chain(
    draw: float,
    terminal_type: EventType,
    noticed: bool,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    campaign = _phone_campaign()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        campaign,
        600,
    )[0]

    decision = exposure.decide_attention(opportunity, _FixedOracle(draw))
    eligible, impression, terminal = decision.events

    assert decision.noticed is noticed
    assert tuple(event.event_type for event in decision.events) == (
        EventType.CAMPAIGN_ELIGIBLE,
        EventType.CAMPAIGN_IMPRESSION,
        terminal_type,
    )
    assert tuple(event.sequence for event in decision.events) == (17, 18, 19)
    assert tuple(event.event_id for event in decision.events) == (
        "run-exposure:event-00000017",
        "run-exposure:event-00000018",
        "run-exposure:event-00000019",
    )
    assert eligible.caused_by_event_ids == ()
    assert impression.caused_by_event_ids == (eligible.event_id,)
    assert terminal.caused_by_event_ids == (impression.event_id,)
    assert all(event.agent_id == "person-001" for event in decision.events)
    assert all(event.campaign_id == "campaign-phone" for event in decision.events)
    assert all(event.channel == "mobile-feed" for event in decision.events)
    assert all(event.source == EventSource.RULE for event in decision.events)
    assert dict(eligible.payload) == {
        "frequency_cap": 3,
        "placement_id": opportunity.placement_identity,
        "prior_exposures": 0,
        "visibility": 0.8,
        "zone": "online",
    }
    assert dict(impression.payload) == {
        "delivery": "rendered",
        "placement_id": opportunity.placement_identity,
    }
    assert dict(terminal.payload) == {
        "notice_probability": decision.notice_probability,
        "placement_id": opportunity.placement_identity,
        "random_draw": draw,
    }
    assert (
        sum(
            event.event_type in {EventType.CAMPAIGN_NOTICED, EventType.CAMPAIGN_IGNORED}
            for event in decision.events
        )
        == 1
    )


def test_attention_decision_revalidates_valid_bypass_constructed_events() -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        _phone_campaign(),
        600,
    )[0]
    decision = exposure.decide_attention(opportunity, _FixedOracle(0.0))
    bypassed_events = tuple(_bypass_event(event) for event in decision.events)

    revalidated = exposure.AttentionDecision(
        opportunity=decision.opportunity,
        notice_probability=decision.notice_probability,
        random_draw=decision.random_draw,
        noticed=decision.noticed,
        events=bypassed_events,
    )

    assert revalidated == decision
    assert all(
        checked is not bypassed
        for checked, bypassed in zip(revalidated.events, bypassed_events, strict=True)
    )


def test_attention_decision_revalidates_a_bypass_constructed_decision() -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        _phone_campaign(),
        600,
    )[0]
    decision = exposure.decide_attention(opportunity, _FixedOracle(0.0))
    invalid = exposure.AttentionDecision.model_construct(
        opportunity=decision.opportunity,
        notice_probability=decision.notice_probability,
        random_draw=decision.random_draw,
        noticed=False,
        events=decision.events,
    )

    with pytest.raises(ValueError, match="noticed"):
        exposure.AttentionDecision.model_validate(invalid)


@pytest.mark.parametrize(
    ("event_index", "field_name"),
    (
        (0, "frequency_cap"),
        (0, "prior_exposures"),
        (0, "visibility"),
        (2, "notice_probability"),
        (2, "random_draw"),
    ),
)
def test_attention_decision_requires_exact_numeric_payload_leaf_types(
    event_index: int,
    field_name: str,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    campaign = _phone_campaign()
    if field_name == "visibility":
        placement = campaign.placements[0].model_copy(update={"visibility": 1.0})
        campaign = campaign.model_copy(update={"placements": (placement,)})
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        campaign,
        600,
    )[0]
    decision = exposure.decide_attention(opportunity, _FixedOracle(0.0))
    events = list(decision.events)
    event = events[event_index]
    original = event.payload[field_name]
    replacements: dict[str, object] = {
        "frequency_cap": float(original),
        "prior_exposures": False,
        "visibility": int(original),
        "notice_probability": _FloatSubclass(float(original)),
        "random_draw": False,
    }
    events[event_index] = _bypass_event(
        event,
        payload={**dict(event.payload), field_name: replacements[field_name]},
    )

    with pytest.raises(ValueError, match="payload"):
        exposure.AttentionDecision(
            opportunity=decision.opportunity,
            notice_probability=decision.notice_probability,
            random_draw=decision.random_draw,
            noticed=decision.noticed,
            events=tuple(events),
        )


def test_attention_decision_payload_mapping_order_is_irrelevant() -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        _phone_campaign(),
        600,
    )[0]
    decision = exposure.decide_attention(opportunity, _FixedOracle(0.0))
    reordered = tuple(
        _bypass_event(event, payload=dict(reversed(tuple(event.payload.items()))))
        for event in decision.events
    )

    checked = exposure.AttentionDecision(
        opportunity=decision.opportunity,
        notice_probability=decision.notice_probability,
        random_draw=decision.random_draw,
        noticed=decision.noticed,
        events=reordered,
    )

    assert checked == decision


@pytest.mark.parametrize(
    "corruption",
    (
        "noticed",
        "decision-probability",
        "event-order",
        "sequence",
        "event-id",
        "run-id",
        "minute",
        "agent-id",
        "campaign-id",
        "channel",
        "source",
        "eligibility-payload",
        "impression-payload",
        "terminal-probability",
        "terminal-draw",
        "eligibility-cause",
        "impression-cause",
        "terminal-cause",
        "terminal-type",
    ),
)
def test_attention_decision_rejects_semantically_corrupt_event_chains(
    corruption: str,
) -> None:
    exposure = _exposure_module()
    profile = _profile()
    opportunity = exposure.eligible_placements(
        _snapshot(profile, _state(profile)),
        _phone_campaign(),
        600,
    )[0]
    decision = exposure.decide_attention(opportunity, _FixedOracle(0.0))
    eligible, impression, terminal = decision.events
    events = [eligible, impression, terminal]
    noticed = decision.noticed
    notice_probability = decision.notice_probability

    if corruption == "noticed":
        noticed = not noticed
    elif corruption == "decision-probability":
        notice_probability = 0.1
        events[2] = _bypass_event(
            terminal,
            payload={**dict(terminal.payload), "notice_probability": notice_probability},
        )
    elif corruption == "event-order":
        events = [impression, eligible, terminal]
    elif corruption == "sequence":
        events[1] = _bypass_event(impression, sequence=20)
    elif corruption == "event-id":
        events[1] = _bypass_event(impression, event_id="run-exposure:event-99999999")
    elif corruption == "run-id":
        events[1] = _bypass_event(impression, run_id="other-run")
    elif corruption == "minute":
        events[1] = _bypass_event(impression, simulated_minute=601)
    elif corruption == "agent-id":
        events[1] = _bypass_event(impression, agent_id="person-002")
    elif corruption == "campaign-id":
        events[1] = _bypass_event(impression, campaign_id="other-campaign")
    elif corruption == "channel":
        events[1] = _bypass_event(impression, channel="highway-billboard")
    elif corruption == "source":
        events[1] = _bypass_event(impression, source=EventSource.MOCK)
    elif corruption == "eligibility-payload":
        events[0] = _bypass_event(
            eligible,
            payload={**dict(eligible.payload), "placement_id": "0" * 64},
        )
    elif corruption == "impression-payload":
        events[1] = _bypass_event(
            impression,
            payload={**dict(impression.payload), "delivery": "crossed"},
        )
    elif corruption == "terminal-probability":
        events[2] = _bypass_event(
            terminal,
            payload={**dict(terminal.payload), "notice_probability": 0.1},
        )
    elif corruption == "terminal-draw":
        events[2] = _bypass_event(
            terminal,
            payload={**dict(terminal.payload), "random_draw": 0.1},
        )
    elif corruption == "eligibility-cause":
        events[0] = _bypass_event(eligible, caused_by_event_ids=(impression.event_id,))
    elif corruption == "impression-cause":
        events[1] = _bypass_event(impression, caused_by_event_ids=())
    elif corruption == "terminal-cause":
        events[2] = _bypass_event(terminal, caused_by_event_ids=(eligible.event_id,))
    elif corruption == "terminal-type":
        events[2] = _bypass_event(terminal, event_type=EventType.CAMPAIGN_IGNORED)

    with pytest.raises(ValueError):
        exposure.AttentionDecision(
            opportunity=decision.opportunity,
            notice_probability=notice_probability,
            random_draw=decision.random_draw,
            noticed=noticed,
            events=tuple(events),
        )


def test_billboard_impression_records_a_crossed_route() -> None:
    exposure = _exposure_module()
    profile = _profile()
    state = _state(
        profile,
        location="highway-north",
        activity="commute",
        route_id="highway-north",
    )
    opportunity = exposure.eligible_placements(
        _snapshot(profile, state),
        _billboard_campaign(),
        480,
    )[0]

    decision = exposure.decide_attention(opportunity, _FixedOracle(0.0))

    assert dict(decision.events[0].payload) == {
        "frequency_cap": 4,
        "placement_id": opportunity.placement_identity,
        "prior_exposures": 0,
        "route_id": "highway-north",
        "visibility": 0.9,
    }
    assert dict(decision.events[1].payload) == {
        "delivery": "crossed",
        "placement_id": opportunity.placement_identity,
    }
