import pytest

from adlife.core.domain.campaign import (
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Price,
    TimeWindow,
)
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Route, RoutineBlock, World, Zone


@pytest.fixture
def valid_profile() -> PersonProfile:
    return PersonProfile(
        agent_id="person-001",
        display_name="Arman 001",
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
            advertising_skepticism=0.3,
            mobile_attention=0.8,
            outdoor_attention=0.5,
            brand_loyalty=0.4,
            impulsivity=0.3,
        ),
        initial_brand_sentiment=0.1,
        routine_template="office-worker",
    )


@pytest.fixture
def valid_campaign() -> Campaign:
    return Campaign(
        campaign_id="campaign-phone",
        name="Pocket Launch",
        product_name="Fictional Phone",
        product_category="consumer-electronics",
        message="A fictional phone designed for a calmer daily routine.",
        call_to_action="Explore the fictional product",
        price=Price(amount=850.0, currency="USD"),
        category_reference_price=1000.0,
        target_interests=frozenset({"technology"}),
        start_minute=0,
        end_minute=1440,
        placements=(
            PhonePlacement(
                channel="mobile-feed",
                active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=1320),),
                frequency_cap=3,
                visibility=0.8,
            ),
        ),
        creative_features=CreativeFeatures(
            description="A fictional handset on a clean white background.",
            visual_style="minimal-product",
            dominant_colors=("white", "green"),
            visible_text=("Fictional Phone",),
            contains_people=False,
        ),
    )


@pytest.fixture
def consumer_state(valid_profile: PersonProfile) -> ConsumerState:
    return ConsumerState(
        agent_id=valid_profile.agent_id,
        location="home-north",
        activity="sleep",
        mood=0.0,
        fatigue=0.1,
        brand_sentiment=valid_profile.initial_brand_sentiment,
        recall_strength=0.0,
        purchase_intention=0.0,
        cognition_budget_remaining=6,
    )


@pytest.fixture
def valid_scenario(
    valid_profile: PersonProfile,
    valid_campaign: Campaign,
    consumer_state: ConsumerState,
) -> Scenario:
    world = World(
        world_id="world-default",
        zones=(
            Zone(zone_id="home-north", name="North Home", kind="home"),
            Zone(zone_id="office", name="Office", kind="work"),
            Zone(zone_id="highway-north", name="North Highway", kind="highway"),
            Zone(zone_id="online", name="Online", kind="online"),
        ),
        routes=(
            Route(
                route_id="highway-north",
                source_zone="home-north",
                target_zone="office",
                transit_zone="highway-north",
                travel_minutes=30,
            ),
        ),
    )
    return Scenario(
        scenario_id="scenario-contract",
        name="Contract fixture",
        days=1,
        tick_minutes=15,
        world=world,
        population=(valid_profile,),
        initial_states=(consumer_state,),
        routine_blocks=(
            RoutineBlock(
                template_id="office-worker",
                day_type="weekday",
                start_minute_of_day=0,
                end_minute_of_day=1440,
                activity="sleep",
                zone_id="home-north",
            ),
        ),
        campaigns=(valid_campaign,),
    )
