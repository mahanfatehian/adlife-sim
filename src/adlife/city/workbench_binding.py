"""Cross-bind one frozen workbench input to the exact evaluated city inputs."""

from __future__ import annotations

from adlife.city.workbench_input import WorkbenchRunInput, parse_workbench_run_input_json
from adlife.core.domain.city import CityPackDocument, CityPackV2
from adlife.core.domain.city_places import CityPlaceSet
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    PhoneOpportunityPlacement,
    RoadsideBillboardPlacement,
    SpatialCampaignScenario,
    road_coordinate,
)
from adlife.core.domain.spatial_response import SpatialResponseInput


def validate_workbench_execution_binding(
    workbench_input: WorkbenchRunInput,
    *,
    pack: CityPackDocument,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    places: CityPlaceSet | None,
    spatial_scenario: SpatialCampaignScenario | None,
    spatial_response: SpatialResponseInput | None,
) -> WorkbenchRunInput:
    """Return a strict canonical input only when every scientific choice matches."""
    workbench_input = parse_workbench_run_input_json(canonical_json(workbench_input))
    settings = workbench_input.draft.settings
    if settings.run_id != run_id:
        raise ValueError("workbench run identifier does not match execution")
    if settings.seed != seed:
        raise ValueError("workbench seed does not match execution")
    if settings.agent_count != agent_count:
        raise ValueError("workbench population does not match execution")
    if settings.days != days:
        raise ValueError("workbench duration does not match execution")
    if workbench_input.draft.city_id != pack.city_id:
        raise ValueError("workbench city does not match execution")
    if not isinstance(pack, CityPackV2):
        raise ValueError("workbench execution requires a version 2 city")
    if places is not None:
        raise ValueError("workbench schema 1 does not represent a place set")
    if spatial_scenario is None or spatial_response is None:
        raise ValueError("workbench execution requires scenario and response evidence")
    draft_scenario = workbench_input.draft.scenario
    if (
        spatial_scenario.scenario_id != draft_scenario.scenario_id
        or spatial_scenario.name != draft_scenario.name
        or spatial_scenario.days != days
        or spatial_scenario.city_id != pack.city_id
        or spatial_scenario.city_sha256 != pack.fingerprint
        or len(spatial_scenario.campaigns) != 1
    ):
        raise ValueError("workbench scenario does not match execution")
    campaign = spatial_scenario.campaigns[0]
    draft_campaign = draft_scenario.campaign
    creative_sha256 = workbench_input.creative_template.fingerprint
    if (
        campaign.campaign_id != draft_campaign.campaign_id
        or campaign.name != draft_campaign.name
        or campaign.creative_sha256 != creative_sha256
    ):
        raise ValueError("workbench campaign does not match execution")
    placements = {placement.placement_id: placement for placement in spatial_scenario.placements}
    expected_placement_count = int(draft_scenario.phone is not None) + int(
        draft_scenario.roadside is not None
    )
    if len(placements) != expected_placement_count:
        raise ValueError("workbench placements do not match execution")
    phone_draft = draft_scenario.phone
    phone = placements.get("phone-placement")
    if phone_draft is None:
        if phone is not None:
            raise ValueError("workbench phone placement does not match execution")
    elif not isinstance(phone, PhoneOpportunityPlacement) or (
        phone.campaign_id != campaign.campaign_id
        or phone.active_windows != phone_draft.active_windows
        or phone.frequency_cap_per_agent_per_day != phone_draft.frequency_cap_per_agent_per_day
        or phone.eligible_activities != phone_draft.eligible_activities
        or phone.opportunity_probability_per_minute
        != phone_draft.opportunity_probability_per_minute
    ):
        raise ValueError("workbench phone placement does not match execution")
    roadside_draft = draft_scenario.roadside
    roadside = placements.get("roadside-placement")
    if roadside_draft is None:
        if roadside is not None:
            raise ValueError("workbench roadside placement does not match execution")
    else:
        expected_coordinate = road_coordinate(
            pack,
            road_id=roadside_draft.road_id,
            travel_direction=roadside_draft.travel_direction,
            road_fraction=roadside_draft.road_fraction,
        )
        if not isinstance(roadside, RoadsideBillboardPlacement) or (
            roadside.campaign_id != campaign.campaign_id
            or roadside.active_windows != roadside_draft.active_windows
            or roadside.frequency_cap_per_agent_per_day
            != roadside_draft.frequency_cap_per_agent_per_day
            or roadside.road_id != roadside_draft.road_id
            or roadside.travel_direction != roadside_draft.travel_direction
            or roadside.road_fraction != roadside_draft.road_fraction
            or (roadside.longitude, roadside.latitude) != expected_coordinate
            or roadside.side != roadside_draft.side
            or roadside.orientation_degrees != roadside_draft.orientation_degrees
            or roadside.max_view_distance_meters != roadside_draft.max_view_distance_meters
        ):
            raise ValueError("workbench roadside placement does not match execution")
    response_campaign = spatial_response.campaigns[0]
    if (
        spatial_response.city_sha256 != pack.fingerprint
        or spatial_response.scenario_sha256 != spatial_scenario.fingerprint
        or len(spatial_response.campaigns) != 1
        or response_campaign.campaign_id != campaign.campaign_id
        or response_campaign.creative_sha256 != creative_sha256
        or response_campaign.target_interests != draft_campaign.target_interests
        or response_campaign.relative_price != draft_campaign.relative_price
    ):
        raise ValueError("workbench response input does not match execution")
    expected_agent_ids = tuple(f"person-{index:03d}" for index in range(1, agent_count + 1))
    cohort = workbench_input.draft.cohort
    if tuple(
        profile.agent_id for profile in spatial_response.profiles
    ) != expected_agent_ids or any(
        not profile.fictional
        or profile.interests != cohort.interests
        or profile.traits != cohort.traits
        for profile in spatial_response.profiles
    ):
        raise ValueError("workbench response population does not match execution")
    initial = cohort.initial_state
    if tuple(
        state.agent_id for state in spatial_response.initial_states
    ) != expected_agent_ids or any(
        state.campaign_id != campaign.campaign_id
        or state.brand_sentiment != initial.brand_sentiment
        or state.recall_strength != initial.recall_strength
        or state.purchase_intention != initial.purchase_intention
        for state in spatial_response.initial_states
    ):
        raise ValueError("workbench initial response state does not match execution")
    return workbench_input


__all__ = ["validate_workbench_execution_binding"]
