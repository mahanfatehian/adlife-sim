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
from adlife.core.ports.cognition import (
    CognitionRequest,
    ProviderMetadata,
    SamplingSettings,
)
from adlife.core.simulation.decision import RuleResponse, evaluate_rule_response


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


@pytest.fixture
def cognition_persona(valid_profile: PersonProfile) -> dict[str, object]:
    """A minimized fictional persona projection, as a prompt may carry it."""
    return {
        "age_band": "25-34",
        "household_type": valid_profile.household_type,
        "interests": sorted(valid_profile.interests),
        "occupation": valid_profile.occupation,
    }


@pytest.fixture
def cognition_campaign(valid_campaign: Campaign) -> dict[str, object]:
    """A structured campaign description, as a prompt may carry it."""
    return {
        "call_to_action": valid_campaign.call_to_action,
        "campaign_id": valid_campaign.campaign_id,
        "message": valid_campaign.message,
        "product_category": valid_campaign.product_category,
        "product_name": valid_campaign.product_name,
    }


@pytest.fixture
def cognition_request(
    cognition_persona: dict[str, object],
    cognition_campaign: dict[str, object],
) -> CognitionRequest:
    return CognitionRequest(
        request_id="run-demo:event-00000007",
        run_id="run-demo",
        simulated_minute=480,
        agent_id="person-001",
        fictional_persona=cognition_persona,
        activity="commute",
        mood=0.2,
        relevant_memories=("Noticed a fictional phone advertisement yesterday.",),
        campaign=cognition_campaign,
        channel="mobile-feed",
        exposure_count=2,
        creative_sha256="a" * 64,
    )


@pytest.fixture
def sampling_settings() -> SamplingSettings:
    return SamplingSettings()


@pytest.fixture
def provider_metadata(sampling_settings: SamplingSettings) -> ProviderMetadata:
    return ProviderMetadata(
        kind="mock",
        model_id="mock-v1",
        sampling=sampling_settings,
        prompt_sha256="b" * 64,
    )


@pytest.fixture
def rule_response(
    valid_profile: PersonProfile,
    consumer_state: ConsumerState,
    valid_campaign: Campaign,
) -> RuleResponse:
    return evaluate_rule_response(
        valid_profile,
        consumer_state,
        valid_campaign,
        valid_campaign.placements[0],
    )
