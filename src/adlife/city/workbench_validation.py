"""Pure construction of validated workbench inputs from one strict draft."""

from __future__ import annotations

from dataclasses import dataclass

from adlife.city.catalog import CityCatalogError, UnknownCatalogCity, select_catalog_city
from adlife.city.workbench_creatives import (
    CreativeTemplateCatalogError,
    UnknownCreativeTemplate,
    select_creative_template,
)
from adlife.city.workbench_input import (
    CreativeTemplate,
    NormalizedPhonePlacementDraft,
    NormalizedRoadsidePlacementDraft,
    NormalizedWorkbenchRunDraft,
    NormalizedWorkbenchScenarioDraft,
    WorkbenchDailyWindow,
    WorkbenchRunDraft,
    WorkbenchRunInput,
)
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.spatial_campaign import (
    PhoneOpportunityPlacement,
    RoadsideBillboardPlacement,
    SpatialActiveWindow,
    SpatialCampaign,
    SpatialCampaignScenario,
    SpatialPlacement,
    road_coordinate,
    validate_spatial_scenario_against_city,
)
from adlife.core.domain.spatial_response import (
    SpatialCampaignResponseInput,
    SpatialResponseInitialState,
    SpatialResponseInput,
    SpatialResponseProfile,
    validate_spatial_response_input,
)
from adlife.core.simulation.city_mobility import CityMobility


class WorkbenchValidationError(ValueError):
    """A safe, field-addressable refusal suitable for a local HTTP response."""

    __slots__ = ("code", "field", "safe_message")

    def __init__(self, code: str, field: str, safe_message: str) -> None:
        self.code = code
        self.field = field
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class ValidatedWorkbenchRun:
    """Complete deterministic domain inputs without any artifact reservation."""

    pack: CityPackV2
    mobility: CityMobility
    workbench_input: WorkbenchRunInput
    scenario: SpatialCampaignScenario
    response_input: SpatialResponseInput


def _absolute_windows(
    windows: tuple[WorkbenchDailyWindow, ...],
    *,
    days: int,
    field: str,
) -> tuple[SpatialActiveWindow, ...]:
    if any(window.day > days for window in windows):
        raise WorkbenchValidationError(
            "invalid-schedule",
            field,
            "An active window falls outside the selected duration.",
        )
    return tuple(
        SpatialActiveWindow(
            start_minute=(window.day - 1) * 1_440 + window.start_minute,
            end_minute=(window.day - 1) * 1_440 + window.end_minute,
        )
        for window in windows
    )


def _normalize_draft(draft: WorkbenchRunDraft) -> NormalizedWorkbenchRunDraft:
    phone = draft.scenario.phone
    normalized_phone = (
        None
        if phone is None
        else NormalizedPhonePlacementDraft(
            active_windows=_absolute_windows(
                phone.active_windows,
                days=draft.settings.days,
                field="scenario.phone.active_windows",
            ),
            frequency_cap_per_agent_per_day=phone.frequency_cap_per_agent_per_day,
            eligible_activities=phone.eligible_activities,
            opportunity_probability_per_minute=phone.opportunity_probability_per_minute,
        )
    )
    roadside = draft.scenario.roadside
    normalized_roadside = (
        None
        if roadside is None
        else NormalizedRoadsidePlacementDraft(
            active_windows=_absolute_windows(
                roadside.active_windows,
                days=draft.settings.days,
                field="scenario.roadside.active_windows",
            ),
            frequency_cap_per_agent_per_day=roadside.frequency_cap_per_agent_per_day,
            road_id=roadside.road_id,
            travel_direction=roadside.travel_direction,
            road_fraction=roadside.road_fraction,
            side=roadside.side,
            orientation_degrees=roadside.orientation_degrees,
            max_view_distance_meters=roadside.max_view_distance_meters,
        )
    )
    return NormalizedWorkbenchRunDraft(
        city_id=draft.city_id,
        scenario=NormalizedWorkbenchScenarioDraft(
            scenario_id=draft.scenario.scenario_id,
            name=draft.scenario.name,
            campaign=draft.scenario.campaign,
            phone=normalized_phone,
            roadside=normalized_roadside,
        ),
        cohort=draft.cohort,
        settings=draft.settings,
    )


def _load_pack(city_id: str) -> CityPackV2:
    try:
        return select_catalog_city(city_id)
    except UnknownCatalogCity:
        raise WorkbenchValidationError(
            "unknown-city",
            "city_id",
            "The selected city is unavailable.",
        ) from None
    except CityCatalogError:
        raise WorkbenchValidationError(
            "city-catalog-unavailable",
            "city_id",
            "The city catalog is unavailable.",
        ) from None


def _load_creative(template_id: str) -> CreativeTemplate:
    try:
        return select_creative_template(template_id)
    except UnknownCreativeTemplate:
        raise WorkbenchValidationError(
            "unknown-creative-template",
            "scenario.campaign.creative_template_id",
            "The selected creative template is unavailable.",
        ) from None
    except CreativeTemplateCatalogError:
        raise WorkbenchValidationError(
            "creative-catalog-unavailable",
            "scenario.campaign.creative_template_id",
            "The creative template catalog is unavailable.",
        ) from None


def _roadside_coordinate(
    pack: CityPackV2,
    roadside: NormalizedRoadsidePlacementDraft,
) -> tuple[float, float]:
    road = next((item for item in pack.roads if item.road_id == roadside.road_id), None)
    if road is None:
        raise WorkbenchValidationError(
            "invalid-road-placement",
            "scenario.roadside.road_id",
            "The roadside placement does not match a verified city road.",
        )
    if roadside.travel_direction not in road.directions:
        raise WorkbenchValidationError(
            "invalid-road-placement",
            "scenario.roadside.travel_direction",
            "The roadside travel direction is unavailable for that road.",
        )
    try:
        return road_coordinate(
            pack,
            road_id=roadside.road_id,
            travel_direction=roadside.travel_direction,
            road_fraction=roadside.road_fraction,
        )
    except (TypeError, ValueError):
        raise WorkbenchValidationError(
            "invalid-road-placement",
            "scenario.roadside.road_fraction",
            "The roadside position is invalid for that road.",
        ) from None


def construct_workbench_run(draft: WorkbenchRunDraft) -> ValidatedWorkbenchRun:
    """Construct complete deterministic inputs without network or filesystem writes."""
    if not isinstance(draft, WorkbenchRunDraft):
        raise TypeError("draft must be a WorkbenchRunDraft")

    normalized = _normalize_draft(draft)
    pack = _load_pack(normalized.city_id)
    creative = _load_creative(normalized.scenario.campaign.creative_template_id)
    workbench_input = WorkbenchRunInput(
        draft=normalized,
        creative_template=creative,
    )
    mobility = CityMobility(
        pack,
        seed=normalized.settings.seed,
        agent_count=normalized.settings.agent_count,
        days=normalized.settings.days,
    )

    campaign_draft = normalized.scenario.campaign
    campaign = SpatialCampaign(
        campaign_id=campaign_draft.campaign_id,
        name=campaign_draft.name,
        creative_sha256=creative.fingerprint,
    )
    placements: list[SpatialPlacement] = []
    phone = normalized.scenario.phone
    if phone is not None:
        placements.append(
            PhoneOpportunityPlacement(
                placement_id=phone.placement_id,
                campaign_id=campaign.campaign_id,
                channel="mobile-feed",
                active_windows=phone.active_windows,
                frequency_cap_per_agent_per_day=phone.frequency_cap_per_agent_per_day,
                opportunity_model="keyed-activity-minute-v1",
                eligible_activities=phone.eligible_activities,
                opportunity_probability_per_minute=(phone.opportunity_probability_per_minute),
            )
        )
    roadside = normalized.scenario.roadside
    if roadside is not None:
        longitude, latitude = _roadside_coordinate(pack, roadside)
        placements.append(
            RoadsideBillboardPlacement(
                placement_id=roadside.placement_id,
                campaign_id=campaign.campaign_id,
                channel="roadside-billboard",
                active_windows=roadside.active_windows,
                frequency_cap_per_agent_per_day=(roadside.frequency_cap_per_agent_per_day),
                road_id=roadside.road_id,
                travel_direction=roadside.travel_direction,
                road_fraction=roadside.road_fraction,
                longitude=longitude,
                latitude=latitude,
                side=roadside.side,
                orientation_degrees=roadside.orientation_degrees,
                max_view_distance_meters=roadside.max_view_distance_meters,
            )
        )

    scenario = SpatialCampaignScenario(
        scenario_id=normalized.scenario.scenario_id,
        name=normalized.scenario.name,
        days=normalized.settings.days,
        city_id=pack.city_id,
        city_sha256=pack.fingerprint,
        campaigns=(campaign,),
        placements=tuple(placements),
    )
    validate_spatial_scenario_against_city(scenario, pack)

    cohort = normalized.cohort
    profiles = tuple(
        SpatialResponseProfile(
            agent_id=agent.agent_id,
            fictional=True,
            interests=cohort.interests,
            traits=cohort.traits,
        )
        for agent in mobility.agents
    )
    response_campaign = SpatialCampaignResponseInput(
        campaign_id=campaign.campaign_id,
        creative_sha256=campaign.creative_sha256,
        target_interests=campaign_draft.target_interests,
        relative_price=campaign_draft.relative_price,
    )
    initial = cohort.initial_state
    initial_states = tuple(
        SpatialResponseInitialState(
            agent_id=agent.agent_id,
            campaign_id=campaign.campaign_id,
            brand_sentiment=initial.brand_sentiment,
            recall_strength=initial.recall_strength,
            purchase_intention=initial.purchase_intention,
        )
        for agent in mobility.agents
    )
    response_input = SpatialResponseInput(
        city_sha256=pack.fingerprint,
        scenario_sha256=scenario.fingerprint,
        profiles=profiles,
        campaigns=(response_campaign,),
        initial_states=initial_states,
    )
    validate_spatial_response_input(
        response_input,
        scenario,
        agent_ids=tuple(agent.agent_id for agent in mobility.agents),
    )
    return ValidatedWorkbenchRun(
        pack=pack,
        mobility=mobility,
        workbench_input=workbench_input,
        scenario=scenario,
        response_input=response_input,
    )


__all__ = [
    "ValidatedWorkbenchRun",
    "WorkbenchValidationError",
    "construct_workbench_run",
]
