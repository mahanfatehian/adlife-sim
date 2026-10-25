"""Cross-bind one frozen workbench input to the exact evaluated city inputs."""

from __future__ import annotations

from adlife.city.workbench_input import (
    CreativeTemplate,
    WorkbenchCohortDraft,
    WorkbenchPersonaDraftV2,
    WorkbenchRunInputDocument,
    WorkbenchRunInputV2,
    WorkbenchRunInputV3,
    WorkbenchRunInputV4,
    parse_workbench_run_input_json,
)
from adlife.core.domain.city import CityPackDocument, CityPackV2
from adlife.core.domain.city_cognition import (
    CityCognitionAgentContextV1,
    CityCognitionCampaignContextV1,
    CityCognitionCreativeSnapshotV1,
    CityCognitionRunContextV1,
)
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_PROMPT_CONTRACTS,
    CityCognitionCausalAgentV2,
    CityCognitionCausalCampaignV2,
    CityCognitionInitialRuleStateV2,
    CityCognitionPersonProfileV1,
    CityCognitionRunContextV2,
)
from adlife.core.domain.city_places import CityPlaceSet
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    PhoneOpportunityPlacement,
    RoadsideBillboardPlacement,
    SpatialCampaignScenario,
    road_coordinate,
)
from adlife.core.domain.spatial_response import (
    SpatialCampaignResponseInput,
    SpatialResponseInitialState,
    SpatialResponseInput,
    SpatialResponseProfile,
)
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_response import (
    SpatialResponseEvaluation,
    SpatialResponseState,
    SpatialStateUpdated,
)


def validate_workbench_execution_binding(
    workbench_input: WorkbenchRunInputDocument,
    *,
    pack: CityPackDocument,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    places: CityPlaceSet | None,
    spatial_scenario: SpatialCampaignScenario | None,
    spatial_response: SpatialResponseInput | None,
) -> WorkbenchRunInputDocument:
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
        raise ValueError("workbench input does not represent a place set")
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
    assumptions: dict[str, WorkbenchCohortDraft]
    if isinstance(workbench_input, (WorkbenchRunInputV2, WorkbenchRunInputV3, WorkbenchRunInputV4)):
        population = workbench_input.draft.population
        personas = {persona.persona_id: persona for persona in population.personas}
        assumptions = {
            assignment.agent_id: personas[assignment.persona_id]
            for assignment in population.assignments
        }
    else:
        assumptions = {agent_id: workbench_input.draft.cohort for agent_id in expected_agent_ids}
    if tuple(
        profile.agent_id for profile in spatial_response.profiles
    ) != expected_agent_ids or any(
        not profile.fictional
        or profile.interests != assumptions[profile.agent_id].interests
        or profile.traits != assumptions[profile.agent_id].traits
        for profile in spatial_response.profiles
    ):
        raise ValueError("workbench response population does not match execution")
    if tuple(
        state.agent_id for state in spatial_response.initial_states
    ) != expected_agent_ids or any(
        state.campaign_id != campaign.campaign_id
        or state.brand_sentiment != assumptions[state.agent_id].initial_state.brand_sentiment
        or state.recall_strength != assumptions[state.agent_id].initial_state.recall_strength
        or state.purchase_intention != assumptions[state.agent_id].initial_state.purchase_intention
        for state in spatial_response.initial_states
    ):
        raise ValueError("workbench initial response state does not match execution")
    return workbench_input


# The cognition context models of one frozen input. Planning builds them only after the
# whole simulation; a new draft's acceptance builds the same ones before any job is
# reserved, so it refuses exactly the text that planning would refuse.


def _creative_snapshot(creative: CreativeTemplate) -> CityCognitionCreativeSnapshotV1:
    return CityCognitionCreativeSnapshotV1(
        template_id=creative.template_id,
        template_version=creative.template_version,
        product_name=creative.product_name,
        product_category=creative.product_category,
        message=creative.message,
        call_to_action=creative.call_to_action,
        disclosure=creative.disclosure,
    )


def city_cognition_agent_context_v1(
    agent_id: str, persona: WorkbenchPersonaDraftV2
) -> CityCognitionAgentContextV1:
    """One agent's schema-3 narrative context, copied from its frozen persona draft."""
    return CityCognitionAgentContextV1(
        schema_version=1,
        agent_id=agent_id,
        persona_id=persona.persona_id,
        fictional=True,
        display_name=persona.display_name,
        age=persona.age,
        occupation=persona.occupation,
        background=persona.background,
        mbti=persona.mbti,
        interests=tuple(sorted(persona.interests)),
        traits=persona.traits,
    )


def city_cognition_campaign_context_v1(
    workbench_input: WorkbenchRunInputV3,
) -> CityCognitionCampaignContextV1:
    """The schema-3 campaign context: the campaign and its exact creative snapshot."""
    campaign = workbench_input.draft.scenario.campaign
    creative = workbench_input.creative_template
    return CityCognitionCampaignContextV1(
        schema_version=1,
        campaign_id=campaign.campaign_id,
        name=campaign.name,
        creative_sha256=creative.fingerprint,
        creative=_creative_snapshot(creative),
        target_interests=tuple(sorted(campaign.target_interests)),
        relative_price=campaign.relative_price,
    )


def city_cognition_causal_agent_v2(
    agent_id: str, persona: WorkbenchPersonaDraftV2
) -> CityCognitionCausalAgentV2:
    """One agent's causal V2 facts: its persona's interests and traits."""
    return CityCognitionCausalAgentV2(
        schema_version=2,
        agent_id=agent_id,
        interests=tuple(sorted(persona.interests)),
        traits=persona.traits,
    )


def city_cognition_person_profile_v1(
    agent_id: str, persona: WorkbenchPersonaDraftV2
) -> CityCognitionPersonProfileV1:
    """One agent's prompt-v3 persona card, copied from its frozen persona draft."""
    return CityCognitionPersonProfileV1(
        schema_version=1,
        agent_id=agent_id,
        name=persona.display_name,
        age=persona.age,
        occupation=persona.occupation,
        background=persona.background,
        personality_type=persona.mbti,
    )


def city_cognition_causal_campaign_v2(
    workbench_input: WorkbenchRunInputV4,
) -> CityCognitionCausalCampaignV2:
    """The causal V2 campaign: its target interests, price and exact creative snapshot."""
    campaign = workbench_input.draft.scenario.campaign
    creative = workbench_input.creative_template
    return CityCognitionCausalCampaignV2(
        schema_version=2,
        campaign_id=campaign.campaign_id,
        creative_sha256=creative.fingerprint,
        creative=_creative_snapshot(creative),
        target_interests=tuple(sorted(campaign.target_interests)),
        relative_price=campaign.relative_price,
    )


def build_city_cognition_run_context(
    workbench_input: WorkbenchRunInputV3,
    *,
    pack: CityPackV2,
    spatial_scenario: SpatialCampaignScenario,
    response_input: SpatialResponseInput,
    response_evaluation: SpatialResponseEvaluation,
) -> CityCognitionRunContextV1:
    """Build narrative context only from one fully cross-bound scientific execution."""
    if not isinstance(workbench_input, WorkbenchRunInputV3):
        raise TypeError("city cognition run context requires a schema-3 workbench input")
    if not isinstance(pack, CityPackV2):
        raise TypeError("city cognition run context requires a version 2 city pack")
    if not isinstance(spatial_scenario, SpatialCampaignScenario):
        raise TypeError("city cognition run context requires a spatial scenario")
    if not isinstance(response_input, SpatialResponseInput):
        raise TypeError("city cognition run context requires a spatial response input")
    checked_input = parse_workbench_run_input_json(canonical_json(workbench_input))
    if not isinstance(checked_input, WorkbenchRunInputV3):
        raise TypeError("city cognition run context requires a schema-3 workbench input")
    try:
        checked_pack = revalidate_model(
            pack,
            CityPackV2,
            label="city cognition city pack",
        )
        checked_scenario = revalidate_model(
            spatial_scenario,
            SpatialCampaignScenario,
            label="city cognition spatial scenario",
        )
        checked_response_input = revalidate_model(
            response_input,
            SpatialResponseInput,
            label="city cognition spatial response input",
        )
        checked_evaluation = revalidate_model(
            response_evaluation,
            SpatialResponseEvaluation,
            label="city cognition response evaluation",
        )
    except (TypeError, ValueError):
        raise ValueError("city cognition scientific execution is invalid") from None

    settings = checked_input.draft.settings
    bound_input = validate_workbench_execution_binding(
        checked_input,
        pack=checked_pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        places=None,
        spatial_scenario=checked_scenario,
        spatial_response=checked_response_input,
    )
    if not isinstance(bound_input, WorkbenchRunInputV3):
        raise TypeError("city cognition run context requires a schema-3 workbench input")
    checked_input = bound_input
    if (
        checked_evaluation.city_sha256 != checked_pack.fingerprint
        or checked_evaluation.scenario_sha256 != checked_scenario.fingerprint
        or checked_evaluation.response_input_sha256 != checked_response_input.fingerprint
    ):
        raise ValueError("city cognition response evaluation does not match execution artifacts")

    draft = checked_input.draft
    population = draft.population
    personas = {persona.persona_id: persona for persona in population.personas}
    profiles = tuple(
        SpatialResponseProfile(
            agent_id=assignment.agent_id,
            fictional=True,
            interests=personas[assignment.persona_id].interests,
            traits=personas[assignment.persona_id].traits,
        )
        for assignment in population.assignments
    )
    campaign_draft = draft.scenario.campaign
    creative = checked_input.creative_template
    response_campaigns = (
        SpatialCampaignResponseInput(
            campaign_id=campaign_draft.campaign_id,
            creative_sha256=creative.fingerprint,
            target_interests=campaign_draft.target_interests,
            relative_price=campaign_draft.relative_price,
        ),
    )
    initial_states = tuple(
        SpatialResponseInitialState(
            agent_id=assignment.agent_id,
            campaign_id=campaign.campaign_id,
            brand_sentiment=personas[assignment.persona_id].initial_state.brand_sentiment,
            recall_strength=personas[assignment.persona_id].initial_state.recall_strength,
            purchase_intention=personas[assignment.persona_id].initial_state.purchase_intention,
        )
        for assignment in population.assignments
        for campaign in response_campaigns
    )
    reconstructed_response_input = SpatialResponseInput(
        city_sha256=checked_pack.fingerprint,
        scenario_sha256=checked_scenario.fingerprint,
        profiles=profiles,
        campaigns=response_campaigns,
        initial_states=initial_states,
    )
    expected_state_keys = {
        (state.agent_id, state.campaign_id) for state in reconstructed_response_input.initial_states
    }
    actual_state_keys = {
        (state.agent_id, state.campaign_id) for state in checked_evaluation.final_states
    }
    if (
        reconstructed_response_input != checked_response_input
        or actual_state_keys != expected_state_keys
    ):
        raise ValueError("city cognition response input does not match schema-3 workbench input")
    if checked_evaluation.attention_seed != draft.settings.seed:
        raise ValueError("city cognition response seed does not match schema-3 workbench input")
    first_updates: dict[tuple[str, str], SpatialStateUpdated] = {}
    for record in checked_evaluation.records:
        if isinstance(record, SpatialStateUpdated):
            first_updates.setdefault((record.agent_id, record.campaign_id), record)
    final_states = {
        (state.agent_id, state.campaign_id): state for state in checked_evaluation.final_states
    }
    for initial_state in reconstructed_response_input.initial_states:
        state_key = (initial_state.agent_id, initial_state.campaign_id)
        expected_initial = SpatialResponseState(
            agent_id=initial_state.agent_id,
            campaign_id=initial_state.campaign_id,
            brand_sentiment=initial_state.brand_sentiment,
            recall_strength=initial_state.recall_strength,
            purchase_intention=initial_state.purchase_intention,
            response_count=0,
            last_response_minute=None,
        )
        first_update = first_updates.get(state_key)
        observed_initial = (
            final_states[state_key] if first_update is None else first_update.previous_state
        )
        if observed_initial != expected_initial:
            raise ValueError(
                "city cognition initial response state does not match schema-3 workbench input"
            )

    agents = tuple(
        city_cognition_agent_context_v1(assignment.agent_id, personas[assignment.persona_id])
        for assignment in population.assignments
    )
    campaigns = (city_cognition_campaign_context_v1(checked_input),)
    return CityCognitionRunContextV1(
        schema_version=1,
        run_input_sha256=checked_input.fingerprint,
        response_input_sha256=checked_response_input.fingerprint,
        scenario_sha256=checked_scenario.fingerprint,
        city_sha256=checked_pack.fingerprint,
        agents=agents,
        campaigns=campaigns,
    )


def build_city_cognition_run_context_v2(
    workbench_input: WorkbenchRunInputV4,
    *,
    pack: CityPackV2,
    spatial_scenario: SpatialCampaignScenario,
    response_input: SpatialResponseInput,
    baseline_evaluation: SpatialResponseEvaluation,
) -> CityCognitionRunContextV2:
    """Bind complete scientific evidence before projecting only causal V2 facts."""
    if not isinstance(workbench_input, WorkbenchRunInputV4):
        raise TypeError("city cognition V2 context requires a schema-4 workbench input")
    checked_input = parse_workbench_run_input_json(canonical_json(workbench_input))
    if not isinstance(checked_input, WorkbenchRunInputV4):
        raise TypeError("city cognition V2 context requires a schema-4 workbench input")
    try:
        checked_pack = revalidate_model(pack, CityPackV2, label="city cognition city pack")
        checked_scenario = revalidate_model(
            spatial_scenario, SpatialCampaignScenario, label="city cognition spatial scenario"
        )
        checked_response = revalidate_model(
            response_input, SpatialResponseInput, label="city cognition spatial response input"
        )
        baseline = revalidate_model(
            baseline_evaluation,
            SpatialResponseEvaluation,
            label="city cognition baseline evaluation",
        )
    except (TypeError, ValueError):
        raise ValueError("city cognition scientific execution is invalid") from None

    draft = checked_input.draft
    settings = draft.settings
    bound_input = validate_workbench_execution_binding(
        checked_input,
        pack=checked_pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        places=None,
        spatial_scenario=checked_scenario,
        spatial_response=checked_response,
    )
    if not isinstance(bound_input, WorkbenchRunInputV4):
        raise TypeError("city cognition V2 context requires a schema-4 workbench input")
    if (
        baseline.city_sha256 != checked_pack.fingerprint
        or baseline.scenario_sha256 != checked_scenario.fingerprint
        or baseline.response_input_sha256 != checked_response.fingerprint
        or baseline.attention_seed != settings.seed
    ):
        raise ValueError("city cognition baseline does not match scientific execution")

    population = draft.population
    personas = {persona.persona_id: persona for persona in population.personas}
    assumptions = {
        assignment.agent_id: personas[assignment.persona_id]
        for assignment in population.assignments
    }
    campaign_draft = draft.scenario.campaign
    creative = checked_input.creative_template
    response_campaign = SpatialCampaignResponseInput(
        campaign_id=campaign_draft.campaign_id,
        creative_sha256=creative.fingerprint,
        target_interests=campaign_draft.target_interests,
        relative_price=campaign_draft.relative_price,
    )
    reconstructed = SpatialResponseInput(
        city_sha256=checked_pack.fingerprint,
        scenario_sha256=checked_scenario.fingerprint,
        profiles=tuple(
            SpatialResponseProfile(
                agent_id=agent_id,
                fictional=True,
                interests=persona.interests,
                traits=persona.traits,
            )
            for agent_id, persona in assumptions.items()
        ),
        campaigns=(response_campaign,),
        initial_states=tuple(
            SpatialResponseInitialState(
                agent_id=agent_id,
                campaign_id=campaign_draft.campaign_id,
                brand_sentiment=persona.initial_state.brand_sentiment,
                recall_strength=persona.initial_state.recall_strength,
                purchase_intention=persona.initial_state.purchase_intention,
            )
            for agent_id, persona in assumptions.items()
        ),
    )
    expected_keys = {(state.agent_id, state.campaign_id) for state in reconstructed.initial_states}
    final_states = {(state.agent_id, state.campaign_id): state for state in baseline.final_states}
    if reconstructed != checked_response or set(final_states) != expected_keys:
        raise ValueError("city cognition baseline population does not match schema-4 input")
    first_updates: dict[tuple[str, str], SpatialStateUpdated] = {}
    for record in baseline.records:
        if isinstance(record, SpatialStateUpdated):
            first_updates.setdefault((record.agent_id, record.campaign_id), record)
    for state in reconstructed.initial_states:
        key = (state.agent_id, state.campaign_id)
        expected = SpatialResponseState(
            agent_id=state.agent_id,
            campaign_id=state.campaign_id,
            brand_sentiment=state.brand_sentiment,
            recall_strength=state.recall_strength,
            purchase_intention=state.purchase_intention,
            response_count=0,
            last_response_minute=None,
        )
        update = first_updates.get(key)
        observed = final_states[key] if update is None else update.previous_state
        if observed != expected:
            raise ValueError("city cognition initial baseline state does not match schema-4 input")

    # Only a persona (prompt v3) contract receives the descriptive persona cards, copied
    # from this run's own frozen persona drafts; prompt-v2 runs never carry them.
    persona_prompt = CITY_COGNITION_PROMPT_CONTRACTS[
        draft.cognition.causal_contract.prompt_contract_version
    ].persona_context
    profiles = (
        {
            "profiles": tuple(
                city_cognition_person_profile_v1(agent_id, persona)
                for agent_id, persona in assumptions.items()
            )
        }
        if persona_prompt
        else {}
    )
    return CityCognitionRunContextV2(
        schema_version=2,
        run_input_sha256=bound_input.fingerprint,
        response_input_sha256=checked_response.fingerprint,
        scenario_sha256=checked_scenario.fingerprint,
        city_sha256=checked_pack.fingerprint,
        # The reconstructed response profiles equal these personas' interests and traits.
        agents=tuple(
            city_cognition_causal_agent_v2(agent_id, persona)
            for agent_id, persona in assumptions.items()
        ),
        campaigns=(city_cognition_causal_campaign_v2(checked_input),),
        initial_states=tuple(
            CityCognitionInitialRuleStateV2(
                schema_version=2,
                agent_id=state.agent_id,
                campaign_id=state.campaign_id,
                brand_sentiment=state.brand_sentiment,
                recall_strength=state.recall_strength,
            )
            for state in reconstructed.initial_states
        ),
        **profiles,
    )


__all__ = [
    "build_city_cognition_run_context",
    "build_city_cognition_run_context_v2",
    "city_cognition_agent_context_v1",
    "city_cognition_campaign_context_v1",
    "city_cognition_causal_agent_v2",
    "city_cognition_causal_campaign_v2",
    "city_cognition_person_profile_v1",
    "validate_workbench_execution_binding",
]
