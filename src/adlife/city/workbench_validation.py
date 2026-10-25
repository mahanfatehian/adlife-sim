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
    NormalizedWorkbenchCognitionV3,
    NormalizedWorkbenchCognitionV4,
    NormalizedWorkbenchRunDraft,
    NormalizedWorkbenchRunDraftV2,
    NormalizedWorkbenchRunDraftV3,
    NormalizedWorkbenchRunDraftV4,
    NormalizedWorkbenchScenarioDraft,
    WorkbenchCohortDraft,
    WorkbenchDailyWindow,
    WorkbenchRunDraft,
    WorkbenchRunDraftDocument,
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV3,
    WorkbenchRunDraftV4,
    WorkbenchRunInput,
    WorkbenchRunInputDocument,
    WorkbenchRunInputV2,
    WorkbenchRunInputV3,
    WorkbenchRunInputV4,
    materialize_workbench_population,
    workbench_selection_limit_refusal,
)
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
    CITY_COGNITION_PROMPT_CONTRACTS,
    CITY_COGNITION_SELECTION_LIMITS,
    CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
    CityCognitionCausalContractV2,
)
from adlife.core.domain.serialization import canonical_json
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
    workbench_input: WorkbenchRunInputDocument
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


def _normalize_draft(
    draft: WorkbenchRunDraftDocument,
    *,
    causal_contract: CityCognitionCausalContractV2,
) -> (
    NormalizedWorkbenchRunDraft
    | NormalizedWorkbenchRunDraftV2
    | NormalizedWorkbenchRunDraftV3
    | NormalizedWorkbenchRunDraftV4
):
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
    scenario = NormalizedWorkbenchScenarioDraft(
        scenario_id=draft.scenario.scenario_id,
        name=draft.scenario.name,
        campaign=draft.scenario.campaign,
        phone=normalized_phone,
        roadside=normalized_roadside,
    )
    if isinstance(draft, WorkbenchRunDraftV4):
        selection = draft.cognition
        contract = CityCognitionCausalContractV2.model_validate_json(
            canonical_json(causal_contract)
        )
        limits = CITY_COGNITION_SELECTION_LIMITS[contract.selection_policy_version]
        refusal = workbench_selection_limit_refusal(
            mode=selection.mode,
            funded_response_limit=selection.funded_response_limit,
            selection_policy_version=contract.selection_policy_version,
        )
        if refusal is not None:
            raise WorkbenchValidationError(
                "invalid-cognition", "cognition.funded_response_limit", refusal
            )
        return NormalizedWorkbenchRunDraftV4(
            city_id=draft.city_id,
            scenario=scenario,
            population=materialize_workbench_population(
                draft.population, agent_count=draft.settings.agent_count
            ),
            settings=draft.settings,
            # The limit row's plain integers are re-checked against the version's literals.
            cognition=NormalizedWorkbenchCognitionV4.model_validate(
                dict(
                    effect_scope=selection.effect_scope,
                    mode=selection.mode,
                    provider_profile_id=selection.provider_profile_id,
                    provider_revision_sha256=selection.provider_revision_sha256,
                    funded_response_limit=selection.funded_response_limit,
                    causal_contract=contract,
                    causal_contract_sha256=contract.fingerprint,
                    request_schema_version=2,
                    selected_requests_per_agent=limits.selected_requests_per_agent,
                    selected_requests_per_run=limits.selected_requests_per_run,
                    concurrency_limit=limits.concurrency_limit(selection.mode),
                    max_request_bytes=CITY_COGNITION_PROMPT_CONTRACTS[
                        contract.prompt_contract_version
                    ].max_request_bytes,
                    max_terminal_record_bytes=8192,
                    max_wire_response_bytes=1_048_576,
                    max_output_tokens_per_attempt=CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
                    reservation_tokens_per_request=limits.reservation_tokens_per_request,
                    initial_attempts=1,
                    transient_retries=limits.transient_retries_ceiling,
                    repair_attempts=limits.repair_attempts_ceiling,
                    attempt_timeout_seconds=30,
                    aggregate_deadline_seconds=limits.aggregate_deadline_seconds,
                    aggregate_output_token_ceiling=limits.aggregate_output_token_ceiling(
                        selection.mode, selection.funded_response_limit
                    ),
                    response_model="spatial-response-v2",
                    rule_state_noninterference=True,
                )
            ),
        )
    if isinstance(draft, WorkbenchRunDraftV3):
        cognition = draft.cognition
        return NormalizedWorkbenchRunDraftV3(
            city_id=draft.city_id,
            scenario=scenario,
            population=materialize_workbench_population(
                draft.population, agent_count=draft.settings.agent_count
            ),
            settings=draft.settings,
            cognition=NormalizedWorkbenchCognitionV3(
                mode=cognition.mode,
                provider_profile_id=cognition.provider_profile_id,
                provider_revision_sha256=cognition.provider_revision_sha256,
                aggregate_output_token_ceiling=cognition.aggregate_output_token_ceiling,
                prompt_contract_version=1,
                result_schema_version=1,
                selection_policy_version=1,
                fallback_policy_version=1,
                selected_requests_per_agent=4,
                selected_requests_per_run=60,
                concurrency_limit=4,
                max_request_bytes=8_192,
                max_wire_response_bytes=1_048_576,
                max_output_tokens_per_attempt=384,
                reservation_tokens_per_request=1_536,
                initial_attempts=1,
                transient_retries=2,
                repair_attempts=1,
                attempt_timeout_seconds=30,
                aggregate_deadline_seconds=180,
            ),
        )
    if isinstance(draft, WorkbenchRunDraftV2):
        return NormalizedWorkbenchRunDraftV2(
            city_id=draft.city_id,
            scenario=scenario,
            population=materialize_workbench_population(
                draft.population, agent_count=draft.settings.agent_count
            ),
            settings=draft.settings,
        )
    return NormalizedWorkbenchRunDraft(
        city_id=draft.city_id,
        scenario=scenario,
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


def construct_workbench_run(
    draft: WorkbenchRunDraftDocument,
    *,
    selected_pack: CityPackV2 | None = None,
    causal_contract: CityCognitionCausalContractV2 = CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
) -> ValidatedWorkbenchRun:
    """Construct complete deterministic inputs without network or filesystem writes.

    A schema-4 draft is frozen with ``causal_contract`` and checked against that
    contract's selection-policy version. The workbench server always passes
    ``CURRENT_CITY_COGNITION_CAUSAL_CONTRACT``, so every new run uses the current
    version; the V2 default keeps direct callers of the historical contract unchanged.
    """
    if not isinstance(causal_contract, CityCognitionCausalContractV2):
        raise TypeError("causal_contract must be a CityCognitionCausalContractV2")
    if not isinstance(
        draft, (WorkbenchRunDraft, WorkbenchRunDraftV2, WorkbenchRunDraftV3, WorkbenchRunDraftV4)
    ):
        raise TypeError("draft must be a supported WorkbenchRunDraft")
    if selected_pack is not None and not isinstance(selected_pack, CityPackV2):
        raise TypeError("selected_pack must be a CityPackV2")
    draft = type(draft).model_validate_json(canonical_json(draft))

    normalized = _normalize_draft(draft, causal_contract=causal_contract)
    if selected_pack is None:
        pack = _load_pack(normalized.city_id)
    elif selected_pack.city_id != normalized.city_id:
        raise WorkbenchValidationError(
            "unknown-city",
            "city_id",
            "The selected city is unavailable.",
        )
    else:
        pack = selected_pack
    creative = _load_creative(normalized.scenario.campaign.creative_template_id)
    workbench_input: WorkbenchRunInputDocument
    assumptions: dict[str, WorkbenchCohortDraft]
    if isinstance(
        normalized,
        (
            NormalizedWorkbenchRunDraftV2,
            NormalizedWorkbenchRunDraftV3,
            NormalizedWorkbenchRunDraftV4,
        ),
    ):
        if isinstance(normalized, NormalizedWorkbenchRunDraftV4):
            workbench_input = WorkbenchRunInputV4(draft=normalized, creative_template=creative)
        elif isinstance(normalized, NormalizedWorkbenchRunDraftV3):
            workbench_input = WorkbenchRunInputV3(draft=normalized, creative_template=creative)
        else:
            workbench_input = WorkbenchRunInputV2(draft=normalized, creative_template=creative)
        personas = {persona.persona_id: persona for persona in normalized.population.personas}
        assumptions = {
            assignment.agent_id: personas[assignment.persona_id]
            for assignment in normalized.population.assignments
        }
    else:
        workbench_input = WorkbenchRunInput(draft=normalized, creative_template=creative)
        assumptions = {
            f"person-{index:03d}": normalized.cohort
            for index in range(1, normalized.settings.agent_count + 1)
        }
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

    profiles = tuple(
        SpatialResponseProfile(
            agent_id=agent.agent_id,
            fictional=True,
            interests=assumptions[agent.agent_id].interests,
            traits=assumptions[agent.agent_id].traits,
        )
        for agent in mobility.agents
    )
    response_campaign = SpatialCampaignResponseInput(
        campaign_id=campaign.campaign_id,
        creative_sha256=campaign.creative_sha256,
        target_interests=campaign_draft.target_interests,
        relative_price=campaign_draft.relative_price,
    )
    initial_states = tuple(
        SpatialResponseInitialState(
            agent_id=agent.agent_id,
            campaign_id=campaign.campaign_id,
            brand_sentiment=assumptions[agent.agent_id].initial_state.brand_sentiment,
            recall_strength=assumptions[agent.agent_id].initial_state.recall_strength,
            purchase_intention=assumptions[agent.agent_id].initial_state.purchase_intention,
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
