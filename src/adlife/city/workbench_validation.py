"""Pure construction of validated workbench inputs from one strict draft."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from adlife.city.catalog import CityCatalogError, UnknownCatalogCity, select_catalog_city
from adlife.city.workbench_binding import (
    city_cognition_agent_context_v1,
    city_cognition_campaign_context_v1,
    city_cognition_causal_agent_v2,
    city_cognition_causal_campaign_v2,
    city_cognition_person_profile_v1,
)
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
    WorkbenchPersonaDraftV2,
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
    parse_workbench_run_input_json,
    workbench_selection_limit_refusal,
)
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.city_cognition import _reject_user_labelled_secret_sequence
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
    CITY_COGNITION_PROMPT_CONTRACTS,
    CITY_COGNITION_SELECTION_LIMITS,
    CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT,
    CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    CityCognitionCausalContractV2,
)
from adlife.core.domain.person import (
    contains_labelled_secret_member,
    contains_written_identifier_text,
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
from adlife.core.simulation.city_cognition_v2 import build_city_cognition_persona_person_v1
from adlife.core.simulation.city_mobility import CityMobility

_PERSONA_TEXT_REFUSAL: Final = (
    "Rewrite this persona text without path-like slashes or credential labels."
)
_CAMPAIGN_TEXT_REFUSAL: Final = (
    "Rewrite this campaign text without path-like slashes, credential labels or line breaks."
)
_PERSONA_IDENTIFIER_REFUSAL: Final = "Rewrite this persona text without phone or identity numbers."
# The draft field that each cognition context model field is copied from.
_PERSONA_TEXT_FIELDS: Final[Mapping[str, str]] = {
    "display_name": "display_name",
    "name": "display_name",
    "age": "age",
    "occupation": "occupation",
    "background": "background",
    "mbti": "mbti",
    "personality_type": "mbti",
    "interests": "interests",
    "hobbies_and_interests": "interests",
    "traits": "traits",
}
_CAMPAIGN_TEXT_FIELDS: Final[Mapping[str, str]] = {
    "campaign_id": "campaign_id",
    "name": "name",
    "target_interests": "target_interests",
    "relative_price": "relative_price",
}


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
        # A draft that asks for inner thoughts freezes the thoughts contract in place of the
        # current one; any other contract has no room for them.
        if (
            selection.thought_languages
            and contract.fingerprint == CURRENT_CITY_COGNITION_CAUSAL_CONTRACT.fingerprint
        ):
            contract = CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT
        thoughts_prompt = CITY_COGNITION_PROMPT_CONTRACTS[contract.prompt_contract_version].thoughts
        if bool(selection.thought_languages) != thoughts_prompt:
            raise WorkbenchValidationError(
                "invalid-cognition",
                "cognition.thought_languages",
                "Inner thoughts need the current AI contract and at least one language."
                if selection.thought_languages
                else "This contract writes inner thoughts, so choose at least one language.",
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
                    thought_languages=selection.thought_languages,
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
                    max_output_tokens_per_attempt=limits.output_tokens_per_attempt,
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


def _is_credential_label(value: str) -> bool:
    # The cross-field screen refuses a whole credential label beside any other text.
    try:
        _reject_user_labelled_secret_sequence((value, "text"))
    except ValueError:
        return True
    return False


def _refused_field(
    error: ValueError,
    fields: Mapping[str, str],
    labelled: tuple[tuple[str, tuple[str, ...]], ...],
) -> str | None:
    """The draft field whose copy a context model refused, when one field explains it."""
    if isinstance(error, ValidationError):
        for detail in error.errors(include_url=False, include_context=False, include_input=False):
            if detail["loc"]:
                return fields.get(str(detail["loc"][0]))
    # A refusal without a field is a cross-field credential-label screen.
    for field, values in labelled:
        if any(_is_credential_label(value) for value in values):
            return field
    return None


def _persona_refusal(
    index: int, persona: WorkbenchPersonaDraftV2, error: ValueError
) -> WorkbenchValidationError:
    field = _refused_field(
        error,
        _PERSONA_TEXT_FIELDS,
        (
            ("display_name", (persona.display_name,)),
            ("occupation", () if persona.occupation is None else (persona.occupation,)),
            ("background", () if persona.background is None else (persona.background,)),
            ("interests", tuple(sorted(persona.interests))),
        ),
    )
    path = f"population.personas.{index}"
    return WorkbenchValidationError(
        "invalid-persona-text",
        path if field is None else f"{path}.{field}",
        _PERSONA_TEXT_REFUSAL,
    )


def _campaign_refusal(
    error: ValueError, labelled: tuple[tuple[str, tuple[str, ...]], ...]
) -> WorkbenchValidationError:
    field = _refused_field(error, _CAMPAIGN_TEXT_FIELDS, labelled)
    return WorkbenchValidationError(
        "invalid-campaign-text",
        "scenario.campaign" if field is None else f"scenario.campaign.{field}",
        _CAMPAIGN_TEXT_REFUSAL,
    )


def _screen_cognition_context_v1(workbench_input: WorkbenchRunInputV3) -> None:
    population = workbench_input.draft.population
    indexes = {persona.persona_id: index for index, persona in enumerate(population.personas)}
    for assignment in population.assignments:
        index = indexes[assignment.persona_id]
        persona = population.personas[index]
        try:
            city_cognition_agent_context_v1(assignment.agent_id, persona)
        except ValueError as error:
            raise _persona_refusal(index, persona, error) from None
    campaign = workbench_input.draft.scenario.campaign
    try:
        city_cognition_campaign_context_v1(workbench_input)
    except ValueError as error:
        raise _campaign_refusal(
            error,
            (
                ("campaign_id", (campaign.campaign_id,)),
                ("name", (campaign.name,)),
                ("target_interests", tuple(sorted(campaign.target_interests))),
            ),
        ) from None


def _screen_cognition_context_v2(workbench_input: WorkbenchRunInputV4) -> None:
    draft = workbench_input.draft
    persona_card = CITY_COGNITION_PROMPT_CONTRACTS[
        draft.cognition.causal_contract.prompt_contract_version
    ].persona_context
    population = draft.population
    indexes = {persona.persona_id: index for index, persona in enumerate(population.personas)}
    for assignment in population.assignments:
        index = indexes[assignment.persona_id]
        persona = population.personas[index]
        try:
            agent = city_cognition_causal_agent_v2(assignment.agent_id, persona)
            if persona_card:
                # Planning builds every agent's request person from its persona card too.
                build_city_cognition_persona_person_v1(
                    city_cognition_person_profile_v1(assignment.agent_id, persona), agent
                )
        except ValueError as error:
            raise _persona_refusal(index, persona, error) from None
    campaign = draft.scenario.campaign
    try:
        city_cognition_causal_campaign_v2(workbench_input)
    except ValueError as error:
        raise _campaign_refusal(
            error,
            (
                ("campaign_id", (campaign.campaign_id,)),
                ("target_interests", tuple(sorted(campaign.target_interests))),
            ),
        ) from None


def _canonical_input(workbench_input: WorkbenchRunInputDocument) -> WorkbenchRunInputDocument:
    """The frozen input exactly as the job re-parses it."""
    try:
        return parse_workbench_run_input_json(canonical_json(workbench_input))
    except ValueError:
        # Interest sets freeze sorted, so the job's structural walk can find a credential
        # label before a member that the submitted order never put after it.
        targets = workbench_input.draft.scenario.campaign.target_interests
        if contains_labelled_secret_member(tuple(sorted(targets))):
            raise WorkbenchValidationError(
                "invalid-campaign-text",
                "scenario.campaign.target_interests",
                _CAMPAIGN_TEXT_REFUSAL,
            ) from None
        raise


def _screen_written_identifiers(workbench_input: WorkbenchRunInputDocument) -> None:
    """Refuse phone and identity numbers in the shapes recognized since saved runs were made.

    Stored inputs re-run the older shapes whenever a saved run loads, so the newer ones are
    met only here, where a draft is accepted, and a saved run holding one still opens.
    """
    population = getattr(workbench_input.draft, "population", None)
    for index, persona in enumerate(() if population is None else population.personas):
        for field, values in (
            ("display_name", (persona.display_name,)),
            ("occupation", () if persona.occupation is None else (persona.occupation,)),
            ("background", () if persona.background is None else (persona.background,)),
            ("interests", tuple(sorted(persona.interests))),
        ):
            if any(contains_written_identifier_text(value) for value in values):
                raise WorkbenchValidationError(
                    "invalid-persona-text",
                    f"population.personas.{index}.{field}",
                    _PERSONA_IDENTIFIER_REFUSAL,
                )


def _screen_new_input(workbench_input: WorkbenchRunInputDocument) -> None:
    """Refuse now, by field, what the job would refuse only after the whole simulation.

    The job re-parses the canonical frozen input, and planning then builds the cognition
    context models from it with these same projections. Saved runs never pass through
    here, so their stored inputs load, verify and replay exactly as before. A new draft
    also meets the phone and identity number shapes recognized since saved runs were made.
    """
    checked = _canonical_input(workbench_input)
    _screen_written_identifiers(checked)
    if isinstance(checked, WorkbenchRunInputV4):
        _screen_cognition_context_v2(checked)
    elif isinstance(checked, WorkbenchRunInputV3):
        _screen_cognition_context_v1(checked)


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

    The frozen input must also survive the job's canonical re-parse and, for schema 3
    and 4, the cognition context models its planning builds; a refusal names the persona
    (``population.personas.<index>``, in canonical ID order) or campaign field.
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
    _screen_new_input(workbench_input)
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
