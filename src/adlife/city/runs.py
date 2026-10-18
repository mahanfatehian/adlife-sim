"""Create and independently verify saved synthetic city mobility runs."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from adlife import __version__
from adlife.city.provider_profiles import ProviderProfileRevisionV1
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.workbench_binding import (
    build_city_cognition_run_context,
    build_city_cognition_run_context_v2,
    validate_workbench_execution_binding,
)
from adlife.city.workbench_input import (
    WorkbenchRunInput,
    WorkbenchRunInputDocument,
    WorkbenchRunInputV2,
    WorkbenchRunInputV3,
    WorkbenchRunInputV4,
)
from adlife.core.domain.city import CityPackDocument, CityPackV2
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
    CITY_COGNITION_DEFAULT_SELECTION_POLICY,
    CITY_COGNITION_FALLBACK_POLICY_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_TEXT,
    CITY_COGNITION_RESULT_SCHEMA_DOCUMENT,
    CITY_COGNITION_RESULT_SCHEMA_SHA256,
    CITY_COGNITION_SELECTION_POLICY_SHA256,
    CityCognitionEffectiveSettingsV1,
    CityCognitionEvaluationV1,
    CityCognitionInputV1,
    CityCognitionProviderProfileSnapshotV1,
    CityCognitionRequestV1,
)
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
    CityCognitionRequestV2,
)
from adlife.core.domain.city_places import CityPlaceSet
from adlife.core.domain.city_run import (
    CityRunManifest,
    CityRunManifestDocument,
    CityRunManifestV2,
    CityRunManifestV3,
    CityRunManifestV4,
    CityRunManifestV5,
    CityRunManifestV6,
    CityRunManifestV7,
    CityRunManifestV8,
    CityRunManifestV9,
    CityRunManifestV10,
    build_city_cognition_manifest_receipt,
    build_city_cognition_manifest_receipt_v2,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.domain.spatial_response import (
    SpatialResponseInput,
    parse_spatial_response_input_json,
)
from adlife.core.ports.run_store import CorruptRunArtifact
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.city_cognition import (
    allocate_city_cognition_reservations,
    build_city_cognition_requests,
    validate_city_cognition_evaluation,
)
from adlife.core.simulation.city_cognition_v2 import (
    build_city_cognition_input_v2,
    build_city_cognition_requests_v2,
    validate_city_cognition_evaluation_v2,
)
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.city_trace import summarize_city_trace
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionCounts,
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
    summarize_spatial_attention_artifact,
)
from adlife.core.simulation.spatial_opportunity import (
    SpatialOpportunityCounts,
    SpatialOpportunityEvaluation,
    evaluate_spatial_opportunities,
    summarize_spatial_opportunity_artifact,
)
from adlife.core.simulation.spatial_response import (
    SpatialResponseCounts,
    SpatialResponseEvaluation,
    evaluate_spatial_responses,
    summarize_spatial_response_artifact,
)
from adlife.core.simulation.spatial_response_v2 import (
    SpatialResponseEvaluationV2,
    apply_spatial_cognition_v2,
    summarize_spatial_response_artifact_v2,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class CityReplayResult:
    run_id: str
    identical: bool
    city_sha256: str
    agents_sha256: str
    trace_sha256: str
    frame_count: int
    position_count: int
    place_set_sha256: str | None
    place_assignments_sha256: str | None
    scenario_sha256: str | None
    opportunity_stream_sha256: str | None
    opportunity_summary_sha256: str | None
    opportunity_stream_bytes: int | None
    opportunity_count: int | None
    opportunity_counts: SpatialOpportunityCounts | None
    attention_model_id: str | None
    attention_claim_scope: str | None
    attention_notice_probability: float | None
    attention_stream_sha256: str | None
    attention_summary_sha256: str | None
    attention_stream_bytes: int | None
    impression_count: int | None
    noticed_count: int | None
    attention_counts: SpatialAttentionCounts | None
    response_model_id: str | None
    response_claim_scope: str | None
    response_input_sha256: str | None
    response_stream_sha256: str | None
    response_state_sha256: str | None
    response_summary_sha256: str | None
    response_stream_bytes: int | None
    response_count: int | None
    state_update_count: int | None
    response_campaign_count: int | None
    final_state_count: int | None
    response_counts: SpatialResponseCounts | None
    cognition_evaluation: CityCognitionEvaluationV1 | None


@dataclass(frozen=True, slots=True)
class PreparedCityRun:
    """Complete deterministic city evidence that has not been published."""

    manifest: CityRunManifestDocument
    pack: CityPackDocument
    mobility: CityMobility
    spatial_scenario: SpatialCampaignScenario | None = None
    opportunity_evaluation: SpatialOpportunityEvaluation | None = None
    attention_evaluation: SpatialAttentionEvaluation | None = None
    response_input: SpatialResponseInput | None = None
    response_evaluation: SpatialResponseEvaluation | SpatialResponseEvaluationV2 | None = None
    workbench_input: WorkbenchRunInputDocument | None = None
    cognition_input: CityCognitionInputV1 | CityCognitionInputV2 | None = None
    cognition_evaluation: CityCognitionEvaluationV1 | CityCognitionEvaluationV2 | None = None


@dataclass(frozen=True, slots=True)
class PreparedCityScientificRun:
    """Rule-owned city evidence plus frozen, noncausal explanation requests.

    This staging value intentionally has no manifest, storage path, provider result, or
    publication method. Finalization can occur only after the cognition service returns
    one validated terminal record for every selected request.
    """

    pack: CityPackV2
    mobility: CityMobility
    spatial_scenario: SpatialCampaignScenario
    opportunity_evaluation: SpatialOpportunityEvaluation
    attention_evaluation: SpatialAttentionEvaluation
    response_input: SpatialResponseInput
    response_evaluation: SpatialResponseEvaluation
    workbench_input: WorkbenchRunInputV3
    cognition_input: CityCognitionInputV1
    cognition_requests: tuple[CityCognitionRequestV1, ...]


@dataclass(frozen=True, slots=True)
class PreparedCityScientificRunV2:
    """Unpublished rule baseline and frozen bounded-cognition admission evidence."""

    pack: CityPackV2
    mobility: CityMobility
    spatial_scenario: SpatialCampaignScenario
    opportunity_evaluation: SpatialOpportunityEvaluation
    attention_evaluation: SpatialAttentionEvaluation
    response_input: SpatialResponseInput
    baseline_evaluation: SpatialResponseEvaluation
    workbench_input: WorkbenchRunInputV4
    cognition_input: CityCognitionInputV2
    cognition_requests: tuple[CityCognitionRequestV2, ...]


def _document_sha256(value: Mapping[str, object]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _value_free_revalidate(
    value: object,
    model_type: type[_ModelT],
    *,
    label: str,
    refusal: str,
) -> _ModelT:
    checked: _ModelT | None = None
    invalid = False
    try:
        checked = revalidate_model(value, model_type, label=label)
    except ValueError:
        invalid = True
    if invalid or checked is None:
        raise ValueError(refusal) from None
    return checked


def _validated_workbench_input(
    workbench_input: WorkbenchRunInputDocument | None,
    *,
    pack: CityPackDocument,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    places: CityPlaceSet | None,
    spatial_scenario: SpatialCampaignScenario | None,
    spatial_response: SpatialResponseInput | None,
) -> WorkbenchRunInputDocument | None:
    if workbench_input is None:
        return None
    bound_input = validate_workbench_execution_binding(
        workbench_input,
        pack=pack,
        run_id=run_id,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
        spatial_scenario=spatial_scenario,
        spatial_response=spatial_response,
    )
    return bound_input


def _freeze_city_provider_profile(
    workbench_input: WorkbenchRunInputV3,
    provider_profile: ProviderProfileRevisionV1 | None,
) -> tuple[
    CityCognitionProviderProfileSnapshotV1 | None,
    CityCognitionEffectiveSettingsV1 | None,
]:
    """Cross-bind and copy one immutable non-secret profile into core evidence."""
    cognition = workbench_input.draft.cognition
    if cognition.mode in {"rules", "mock"}:
        if provider_profile is not None:
            raise ValueError("offline cognition cannot carry a provider profile")
        return None, None
    if provider_profile is None:
        raise ValueError("provider cognition requires the selected provider profile")
    if type(provider_profile) is not ProviderProfileRevisionV1:
        raise TypeError("provider_profile must be a ProviderProfileRevisionV1")
    checked_profile: ProviderProfileRevisionV1 | None = None
    profile_snapshot: CityCognitionProviderProfileSnapshotV1 | None = None
    invalid_profile = False
    try:
        checked_profile = revalidate_model(
            provider_profile, ProviderProfileRevisionV1, label="provider profile"
        )
        profile_snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
            checked_profile.model_dump(mode="json", warnings="none")
        )
    except ValueError:
        invalid_profile = True
    if invalid_profile or checked_profile is None or profile_snapshot is None:
        raise ValueError("provider profile failed validation") from None
    if (
        profile_snapshot.mode != cognition.mode
        or profile_snapshot.profile_id != cognition.provider_profile_id
        or profile_snapshot.revision_sha256 != cognition.provider_revision_sha256
    ):
        raise ValueError("provider profile does not match the schema-3 run input")
    effective_settings = CityCognitionEffectiveSettingsV1(
        schema_version=1,
        protocol=profile_snapshot.protocol,
        temperature=profile_snapshot.temperature,
        top_p=profile_snapshot.top_p,
        seed_policy=profile_snapshot.seed_policy,
        seed=profile_snapshot.seed,
        max_output_tokens=profile_snapshot.max_output_tokens,
        attempt_timeout_seconds=profile_snapshot.attempt_timeout_seconds,
        transient_retries=profile_snapshot.transient_retries,
        repair_attempts=profile_snapshot.repair_attempts,
    )
    return profile_snapshot, effective_settings


def _freeze_city_cognition_input(
    workbench_input: WorkbenchRunInputV3,
    requests: tuple[CityCognitionRequestV1, ...],
    *,
    provider_profile: CityCognitionProviderProfileSnapshotV1 | None,
    effective_settings: CityCognitionEffectiveSettingsV1 | None,
) -> CityCognitionInputV1:
    cognition = workbench_input.draft.cognition
    reservations = allocate_city_cognition_reservations(
        requests,
        aggregate_output_token_ceiling=cognition.aggregate_output_token_ceiling,
    )
    return CityCognitionInputV1(
        schema_version=1,
        mode=cognition.mode,
        run_input_sha256=workbench_input.fingerprint,
        explainer_id=(
            f"city-{cognition.mode}-explainer-v1"
            if cognition.mode in {"rules", "mock"}
            else "city-provider-explainer-v1"
        ),
        provider_profile=provider_profile,
        effective_settings=effective_settings,
        prompt_contract_version=cognition.prompt_contract_version,
        prompt_contract_text=CITY_COGNITION_PROMPT_CONTRACT_TEXT,
        prompt_contract_sha256=CITY_COGNITION_PROMPT_CONTRACT_SHA256,
        result_schema_version=cognition.result_schema_version,
        result_schema_document=CITY_COGNITION_RESULT_SCHEMA_DOCUMENT,
        result_schema_contract_sha256=CITY_COGNITION_RESULT_SCHEMA_SHA256,
        selection_policy_version=cognition.selection_policy_version,
        selection_policy=CITY_COGNITION_DEFAULT_SELECTION_POLICY,
        selection_policy_sha256=CITY_COGNITION_SELECTION_POLICY_SHA256,
        fallback_policy_version=cognition.fallback_policy_version,
        fallback_policy=CITY_COGNITION_DEFAULT_FALLBACK_POLICY,
        fallback_policy_sha256=CITY_COGNITION_FALLBACK_POLICY_SHA256,
        selected_requests_per_agent=cognition.selected_requests_per_agent,
        selected_requests_per_run=cognition.selected_requests_per_run,
        concurrency_limit=cognition.concurrency_limit,
        max_request_bytes=cognition.max_request_bytes,
        max_wire_response_bytes=cognition.max_wire_response_bytes,
        max_output_tokens_per_attempt=cognition.max_output_tokens_per_attempt,
        reservation_tokens_per_request=cognition.reservation_tokens_per_request,
        initial_attempts=cognition.initial_attempts,
        transient_retries_ceiling=cognition.transient_retries,
        repair_attempts_ceiling=cognition.repair_attempts,
        attempt_timeout_seconds_ceiling=cognition.attempt_timeout_seconds,
        aggregate_deadline_seconds=cognition.aggregate_deadline_seconds,
        aggregate_output_token_ceiling=cognition.aggregate_output_token_ceiling,
        reservations=reservations,
    )


def prepare_city_scientific_run(
    pack: CityPackDocument,
    *,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    spatial_scenario: SpatialCampaignScenario,
    spatial_response: SpatialResponseInput,
    workbench_input: WorkbenchRunInputV3,
    places: CityPlaceSet | None = None,
    provider_profile: ProviderProfileRevisionV1 | None = None,
) -> PreparedCityScientificRun:
    """Compute rule-owned schema-3 science and freeze explanation requests only."""
    if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
        raise ValueError("city run seed must be between 0 and 2^63-1")
    if type(agent_count) is not int or not 1 <= agent_count <= 30:
        raise ValueError("saved city runs require 1 to 30 agents")
    if type(days) is not int or not 1 <= days <= 7:
        raise ValueError("saved city runs require 1 to 7 days")
    if not isinstance(workbench_input, WorkbenchRunInputV3):
        raise TypeError("scientific city preparation requires a schema-3 workbench input")
    if not isinstance(pack, CityPackV2):
        raise TypeError("scientific city preparation requires a version 2 city pack")
    if not isinstance(spatial_scenario, SpatialCampaignScenario):
        raise TypeError("scientific city preparation requires a spatial scenario")
    if not isinstance(spatial_response, SpatialResponseInput):
        raise TypeError("scientific city preparation requires a spatial response input")

    checked_pack = _value_free_revalidate(
        pack,
        CityPackV2,
        label="scientific city pack",
        refusal="scientific city pack failed validation",
    )
    checked_scenario = _value_free_revalidate(
        spatial_scenario,
        SpatialCampaignScenario,
        label="scientific city scenario",
        refusal="scientific city scenario failed validation",
    )
    checked_response = _value_free_revalidate(
        spatial_response,
        SpatialResponseInput,
        label="scientific city response input",
        refusal="scientific city response input failed validation",
    )
    checked_workbench_source = _value_free_revalidate(
        workbench_input,
        WorkbenchRunInputV3,
        label="scientific workbench input",
        refusal="scientific workbench input failed validation",
    )
    checked_workbench = _validated_workbench_input(
        checked_workbench_source,
        pack=checked_pack,
        run_id=run_id,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
        spatial_scenario=checked_scenario,
        spatial_response=checked_response,
    )
    if not isinstance(checked_workbench, WorkbenchRunInputV3):
        raise TypeError("scientific city preparation requires a schema-3 workbench input")
    profile_snapshot, effective_settings = _freeze_city_provider_profile(
        checked_workbench,
        provider_profile,
    )

    mobility = CityMobility(
        checked_pack,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
    )
    opportunity_evaluation = evaluate_spatial_opportunities(mobility, checked_scenario)
    attention_evaluation = evaluate_spatial_attention(opportunity_evaluation, seed=seed)
    response_evaluation = evaluate_spatial_responses(
        checked_response,
        checked_scenario,
        opportunity_evaluation,
        attention_evaluation,
        agent_ids=tuple(agent.agent_id for agent in mobility.agents),
    )
    cognition_context = build_city_cognition_run_context(
        checked_workbench,
        pack=checked_pack,
        spatial_scenario=checked_scenario,
        response_input=checked_response,
        response_evaluation=response_evaluation,
    )
    cognition_requests = build_city_cognition_requests(
        cognition_context,
        mobility,
        response_evaluation,
    )
    cognition_input = _freeze_city_cognition_input(
        checked_workbench,
        cognition_requests,
        provider_profile=profile_snapshot,
        effective_settings=effective_settings,
    )
    return PreparedCityScientificRun(
        pack=checked_pack,
        mobility=mobility,
        spatial_scenario=checked_scenario,
        opportunity_evaluation=opportunity_evaluation,
        attention_evaluation=attention_evaluation,
        response_input=checked_response,
        response_evaluation=response_evaluation,
        workbench_input=checked_workbench,
        cognition_input=cognition_input,
        cognition_requests=cognition_requests,
    )


def finalize_city_run(
    prepared: PreparedCityScientificRun,
    *,
    cognition_input: CityCognitionInputV1,
    cognition_evaluation: CityCognitionEvaluationV1,
) -> PreparedCityRun:
    """Bind complete validated terminal explanations to a schema-9 run manifest."""
    if type(prepared) is not PreparedCityScientificRun:
        raise TypeError("prepared must be a PreparedCityScientificRun")
    if type(cognition_input) is not CityCognitionInputV1:
        raise TypeError("cognition_input must be a CityCognitionInputV1")
    if type(cognition_evaluation) is not CityCognitionEvaluationV1:
        raise TypeError("cognition_evaluation must be a CityCognitionEvaluationV1")

    checked_input = _value_free_revalidate(
        cognition_input,
        CityCognitionInputV1,
        label="city cognition input",
        refusal="city cognition input failed finalization validation",
    )
    checked_evaluation = _value_free_revalidate(
        cognition_evaluation,
        CityCognitionEvaluationV1,
        label="city cognition evaluation",
        refusal="city cognition evaluation failed finalization validation",
    )
    if checked_input != prepared.cognition_input:
        raise ValueError("city cognition input does not match scientific preparation")
    try:
        verified_evaluation = validate_city_cognition_evaluation(
            checked_input,
            prepared.cognition_requests,
            checked_evaluation.records,
        )
    except ValueError:
        raise ValueError(
            "city cognition evaluation does not match scientific preparation"
        ) from None
    if verified_evaluation != checked_evaluation:
        raise ValueError("city cognition evaluation does not match scientific preparation")

    trace = summarize_city_trace(prepared.mobility)
    opportunity = summarize_spatial_opportunity_artifact(prepared.opportunity_evaluation)
    attention = summarize_spatial_attention_artifact(prepared.attention_evaluation)
    response = summarize_spatial_response_artifact(prepared.response_evaluation)
    opportunity_summary_bytes = (canonical_json(opportunity) + "\n").encode("utf-8")
    attention_summary_bytes = (canonical_json(attention) + "\n").encode("utf-8")
    response_summary_bytes = (canonical_json(response) + "\n").encode("utf-8")
    places = prepared.mobility.places
    settings = prepared.workbench_input.draft.settings
    manifest = CityRunManifestV9(
        run_id=settings.run_id,
        package_version=__version__,
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        city_sha256=prepared.pack.fingerprint,
        agents_sha256=trace.agents_sha256,
        trace_sha256=trace.trace_sha256,
        scenario_sha256=prepared.spatial_scenario.fingerprint,
        opportunity_stream_sha256=opportunity.stream_sha256,
        opportunity_summary_sha256=sha256(opportunity_summary_bytes).hexdigest(),
        opportunity_stream_bytes=opportunity.stream_bytes,
        opportunity_count=opportunity.counts.opportunity_count,
        attention_stream_sha256=attention.stream_sha256,
        attention_summary_sha256=sha256(attention_summary_bytes).hexdigest(),
        attention_stream_bytes=attention.stream_bytes,
        impression_count=attention.counts.impression_count,
        noticed_count=attention.counts.noticed_count,
        spatial_response_schema_version=prepared.response_evaluation.schema_version,
        spatial_response_model_id=prepared.response_evaluation.model_id,
        response_input_sha256=prepared.response_input.fingerprint,
        response_stream_sha256=response.stream_sha256,
        response_state_sha256=response.state_document_sha256,
        response_summary_sha256=sha256(response_summary_bytes).hexdigest(),
        response_stream_bytes=response.stream_bytes,
        response_count=response.counts.response_count,
        state_update_count=response.counts.state_update_count,
        response_campaign_count=response.counts.campaign_count,
        final_state_count=response.counts.final_state_count,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        frame_count=trace.frame_count,
        position_count=trace.position_count,
        city_schema_version=prepared.pack.schema_version,
        spatial_scenario_schema_version=prepared.spatial_scenario.schema_version,
        spatial_opportunity_schema_version=prepared.opportunity_evaluation.schema_version,
        spatial_attention_schema_version=prepared.attention_evaluation.schema_version,
        spatial_attention_model_id=prepared.attention_evaluation.model_id,
        place_schema_version=None if places is None else places.schema_version,
        place_set_sha256=None if places is None else places.fingerprint,
        place_assignments_sha256=(
            None
            if places is None
            else _document_sha256(prepared.mobility.place_assignment_document())
        ),
        workbench_input_schema_version=prepared.workbench_input.schema_version,
        workbench_input_sha256=prepared.workbench_input.fingerprint,
        cognition=build_city_cognition_manifest_receipt(
            checked_input,
            verified_evaluation,
        ),
    )
    return PreparedCityRun(
        manifest=manifest,
        pack=prepared.pack,
        mobility=prepared.mobility,
        spatial_scenario=prepared.spatial_scenario,
        opportunity_evaluation=prepared.opportunity_evaluation,
        attention_evaluation=prepared.attention_evaluation,
        response_input=prepared.response_input,
        response_evaluation=prepared.response_evaluation,
        workbench_input=prepared.workbench_input,
        cognition_input=checked_input,
        cognition_evaluation=verified_evaluation,
    )


def _freeze_city_provider_profile_v2(
    workbench_input: WorkbenchRunInputV4,
    provider_profile: ProviderProfileRevisionV1 | None,
) -> CityCognitionProviderProfileSnapshotV1 | None:
    """Copy the selected nonsecret revision without consulting its registry."""
    cognition = workbench_input.draft.cognition
    if cognition.mode in {"rules", "mock"}:
        if provider_profile is not None:
            raise ValueError("offline cognition cannot carry a provider profile")
        return None
    if provider_profile is None:
        raise ValueError("provider cognition requires the selected provider profile")
    if type(provider_profile) is not ProviderProfileRevisionV1:
        raise TypeError("provider_profile must be a ProviderProfileRevisionV1")
    checked = _value_free_revalidate(
        provider_profile,
        ProviderProfileRevisionV1,
        label="provider profile",
        refusal="provider profile failed validation",
    )
    snapshot: CityCognitionProviderProfileSnapshotV1 | None = None
    with suppress(ValueError):
        snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
            checked.model_dump(mode="json", warnings="none")
        )
    if snapshot is None:
        raise ValueError("provider profile failed validation") from None
    if (
        snapshot.mode != cognition.mode
        or snapshot.profile_id != cognition.provider_profile_id
        or snapshot.revision_sha256 != cognition.provider_revision_sha256
    ):
        raise ValueError("provider profile does not match the schema-4 run input")
    return snapshot


def prepare_city_scientific_run_v2(
    pack: CityPackDocument,
    *,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    spatial_scenario: SpatialCampaignScenario,
    spatial_response: SpatialResponseInput,
    workbench_input: WorkbenchRunInputV4,
    places: CityPlaceSet | None = None,
    provider_profile: ProviderProfileRevisionV1 | None = None,
) -> PreparedCityScientificRunV2:
    """Prepare deterministic science and freeze all V2 requests before provider work."""
    if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
        raise ValueError("city run seed must be between 0 and 2^63-1")
    if type(agent_count) is not int or not 1 <= agent_count <= 30:
        raise ValueError("saved city runs require 1 to 30 agents")
    if type(days) is not int or not 1 <= days <= 7:
        raise ValueError("saved city runs require 1 to 7 days")
    if type(workbench_input) is not WorkbenchRunInputV4:
        raise TypeError("scientific city preparation requires a schema-4 workbench input")
    if type(pack) is not CityPackV2:
        raise TypeError("scientific city preparation requires a version 2 city pack")
    if type(spatial_scenario) is not SpatialCampaignScenario:
        raise TypeError("scientific city preparation requires a spatial scenario")
    if type(spatial_response) is not SpatialResponseInput:
        raise TypeError("scientific city preparation requires a spatial response input")

    checked_pack = _value_free_revalidate(
        pack,
        CityPackV2,
        label="scientific city pack",
        refusal="scientific city pack failed validation",
    )
    checked_scenario = _value_free_revalidate(
        spatial_scenario,
        SpatialCampaignScenario,
        label="scientific city scenario",
        refusal="scientific city scenario failed validation",
    )
    checked_response = _value_free_revalidate(
        spatial_response,
        SpatialResponseInput,
        label="scientific city response input",
        refusal="scientific city response input failed validation",
    )
    checked_source = _value_free_revalidate(
        workbench_input,
        WorkbenchRunInputV4,
        label="scientific workbench input",
        refusal="scientific workbench input failed validation",
    )
    checked_workbench = _validated_workbench_input(
        checked_source,
        pack=checked_pack,
        run_id=run_id,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
        spatial_scenario=checked_scenario,
        spatial_response=checked_response,
    )
    if type(checked_workbench) is not WorkbenchRunInputV4:
        raise TypeError("scientific city preparation requires a schema-4 workbench input")
    snapshot = _freeze_city_provider_profile_v2(checked_workbench, provider_profile)
    mobility = CityMobility(
        checked_pack,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
    )
    opportunities = evaluate_spatial_opportunities(mobility, checked_scenario)
    attention = evaluate_spatial_attention(opportunities, seed=seed)
    baseline = evaluate_spatial_responses(
        checked_response,
        checked_scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in mobility.agents),
    )
    context = build_city_cognition_run_context_v2(
        checked_workbench,
        pack=checked_pack,
        spatial_scenario=checked_scenario,
        response_input=checked_response,
        baseline_evaluation=baseline,
    )
    cognition = checked_workbench.draft.cognition
    requests = build_city_cognition_requests_v2(
        context,
        mobility,
        baseline,
        causal_contract=cognition.causal_contract,
    )
    frozen = build_city_cognition_input_v2(
        context,
        requests,
        mode=cognition.mode,
        funded_response_limit=cognition.funded_response_limit,
        provider_profile=snapshot,
        causal_contract=cognition.causal_contract,
    )
    return PreparedCityScientificRunV2(
        pack=checked_pack,
        mobility=mobility,
        spatial_scenario=checked_scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        response_input=checked_response,
        baseline_evaluation=baseline,
        workbench_input=checked_workbench,
        cognition_input=frozen,
        cognition_requests=requests,
    )


def finalize_city_run_v2(
    prepared: PreparedCityScientificRunV2,
    *,
    cognition_input: CityCognitionInputV2,
    cognition_evaluation: CityCognitionEvaluationV2,
) -> PreparedCityRun:
    """Validate frozen terminal evidence and derive complete unpublished V10 artifacts."""
    if type(prepared) is not PreparedCityScientificRunV2:
        raise TypeError("prepared must be a PreparedCityScientificRunV2")
    if type(cognition_input) is not CityCognitionInputV2:
        raise TypeError("cognition_input must be a CityCognitionInputV2")
    if type(cognition_evaluation) is not CityCognitionEvaluationV2:
        raise TypeError("cognition_evaluation must be a CityCognitionEvaluationV2")
    checked_input = _value_free_revalidate(
        cognition_input,
        CityCognitionInputV2,
        label="city cognition input",
        refusal="city cognition input failed finalization validation",
    )
    checked_evaluation = _value_free_revalidate(
        cognition_evaluation,
        CityCognitionEvaluationV2,
        label="city cognition evaluation",
        refusal="city cognition evaluation failed finalization validation",
    )
    if checked_input != prepared.cognition_input:
        raise ValueError("city cognition input does not match scientific preparation")
    verified: CityCognitionEvaluationV2 | None = None
    applied: SpatialResponseEvaluationV2 | None = None
    try:
        verified = validate_city_cognition_evaluation_v2(
            checked_input,
            prepared.cognition_requests,
            checked_evaluation.records,
            baseline_evaluation=prepared.baseline_evaluation,
        )
        if verified == checked_evaluation:
            applied = apply_spatial_cognition_v2(
                prepared.baseline_evaluation,
                checked_input,
                verified,
            )
    except ValueError:
        pass
    if verified is None or applied is None:
        raise ValueError(
            "city cognition evaluation does not match scientific preparation"
        ) from None

    trace = summarize_city_trace(prepared.mobility)
    opportunity = summarize_spatial_opportunity_artifact(prepared.opportunity_evaluation)
    attention = summarize_spatial_attention_artifact(prepared.attention_evaluation)
    response = summarize_spatial_response_artifact_v2(applied)
    settings = prepared.workbench_input.draft.settings
    places = prepared.mobility.places
    contract = checked_input.causal_contract
    manifest = CityRunManifestV10(
        schema_version=10,
        run_id=settings.run_id,
        package_version=__version__,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        city_sha256=prepared.pack.fingerprint,
        agents_sha256=trace.agents_sha256,
        trace_sha256=trace.trace_sha256,
        scenario_sha256=prepared.spatial_scenario.fingerprint,
        opportunity_stream_sha256=opportunity.stream_sha256,
        opportunity_summary_sha256=sha256(
            (canonical_json(opportunity) + "\n").encode()
        ).hexdigest(),
        opportunity_stream_bytes=opportunity.stream_bytes,
        opportunity_count=opportunity.counts.opportunity_count,
        attention_stream_sha256=attention.stream_sha256,
        attention_summary_sha256=sha256((canonical_json(attention) + "\n").encode()).hexdigest(),
        attention_stream_bytes=attention.stream_bytes,
        impression_count=attention.counts.impression_count,
        noticed_count=attention.counts.noticed_count,
        spatial_response_schema_version=2,
        spatial_response_model_id="spatial-response-v2",
        response_input_sha256=prepared.response_input.fingerprint,
        response_stream_sha256=response.stream_sha256,
        response_state_sha256=response.state_document_sha256,
        response_summary_sha256=sha256((canonical_json(response) + "\n").encode()).hexdigest(),
        response_stream_bytes=response.stream_bytes,
        response_count=response.counts.baseline_response_count,
        state_update_count=response.counts.dual_state_update_count,
        response_campaign_count=response.counts.campaign_count,
        final_state_count=response.counts.final_rule_state_count,
        baseline_response_count=response.counts.baseline_response_count,
        applied_response_count=response.counts.applied_response_count,
        dual_state_update_count=response.counts.dual_state_update_count,
        final_cognition_state_count=response.counts.final_cognition_state_count,
        rule_state_document_sha256=response.rule_state_document_sha256,
        cognition_state_document_sha256=response.cognition_state_document_sha256,
        influence_policy_id=contract.influence_policy.policy_id,
        influence_policy_sha256=contract.influence_policy_sha256,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        frame_count=trace.frame_count,
        position_count=trace.position_count,
        city_schema_version=prepared.pack.schema_version,
        spatial_scenario_schema_version=prepared.spatial_scenario.schema_version,
        spatial_opportunity_schema_version=prepared.opportunity_evaluation.schema_version,
        spatial_attention_schema_version=prepared.attention_evaluation.schema_version,
        spatial_attention_model_id=prepared.attention_evaluation.model_id,
        place_schema_version=None if places is None else places.schema_version,
        place_set_sha256=None if places is None else places.fingerprint,
        place_assignments_sha256=(
            None
            if places is None
            else _document_sha256(prepared.mobility.place_assignment_document())
        ),
        workbench_input_schema_version=4,
        workbench_input_sha256=prepared.workbench_input.fingerprint,
        cognition=build_city_cognition_manifest_receipt_v2(checked_input, verified),
    )
    return PreparedCityRun(
        manifest=manifest,
        pack=prepared.pack,
        mobility=prepared.mobility,
        spatial_scenario=prepared.spatial_scenario,
        opportunity_evaluation=prepared.opportunity_evaluation,
        attention_evaluation=prepared.attention_evaluation,
        response_input=prepared.response_input,
        response_evaluation=applied,
        workbench_input=prepared.workbench_input,
        cognition_input=checked_input,
        cognition_evaluation=verified,
    )


def prepare_city_run(
    pack: CityPackDocument,
    *,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    places: CityPlaceSet | None = None,
    spatial_scenario: SpatialCampaignScenario | None = None,
    spatial_response: SpatialResponseInput | None = None,
    workbench_input: WorkbenchRunInputDocument | None = None,
) -> PreparedCityRun:
    """Evaluate a bounded mobility trace without reserving or writing a run ID."""
    if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
        raise ValueError("city run seed must be between 0 and 2^63-1")
    if type(agent_count) is not int or not 1 <= agent_count <= 30:
        raise ValueError("saved city runs require 1 to 30 agents")
    if type(days) is not int or not 1 <= days <= 7:
        raise ValueError("saved city runs require 1 to 7 days")
    if spatial_response is not None and spatial_scenario is None:
        raise ValueError("spatial response input requires a spatial campaign scenario")
    if isinstance(workbench_input, WorkbenchRunInputV3):
        raise ValueError("workbench input schema 3 requires city run manifest schema 9")
    if spatial_response is not None:
        spatial_response = parse_spatial_response_input_json(canonical_json(spatial_response))
    workbench_input = _validated_workbench_input(
        workbench_input,
        pack=pack,
        run_id=run_id,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
        spatial_scenario=spatial_scenario,
        spatial_response=spatial_response,
    )
    mobility = CityMobility(
        pack,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
    )
    summary = summarize_city_trace(mobility)
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    manifest: CityRunManifestDocument
    opportunity_evaluation: SpatialOpportunityEvaluation | None = None
    attention_evaluation: SpatialAttentionEvaluation | None = None
    response_evaluation: SpatialResponseEvaluation | None = None
    if spatial_scenario is not None:
        opportunity_evaluation = evaluate_spatial_opportunities(mobility, spatial_scenario)
        opportunity_summary = summarize_spatial_opportunity_artifact(opportunity_evaluation)
        opportunity_summary_bytes = (canonical_json(opportunity_summary) + "\n").encode("utf-8")
        attention_evaluation = evaluate_spatial_attention(opportunity_evaluation, seed=seed)
        attention_summary = summarize_spatial_attention_artifact(attention_evaluation)
        attention_summary_bytes = (canonical_json(attention_summary) + "\n").encode("utf-8")
        place_schema_version = None if places is None else places.schema_version
        place_set_sha256 = None if places is None else places.fingerprint
        place_assignments_sha256 = (
            None if places is None else _document_sha256(mobility.place_assignment_document())
        )
        common_spatial_receipt = {
            "run_id": run_id,
            "city_schema_version": pack.schema_version,
            "spatial_scenario_schema_version": spatial_scenario.schema_version,
            "spatial_opportunity_schema_version": opportunity_evaluation.schema_version,
            "place_schema_version": place_schema_version,
            "place_set_sha256": place_set_sha256,
            "place_assignments_sha256": place_assignments_sha256,
            "package_version": __version__,
            "python_version": python_version,
            "city_sha256": pack.fingerprint,
            "agents_sha256": summary.agents_sha256,
            "trace_sha256": summary.trace_sha256,
            "scenario_sha256": spatial_scenario.fingerprint,
            "opportunity_stream_sha256": opportunity_summary.stream_sha256,
            "opportunity_summary_sha256": sha256(opportunity_summary_bytes).hexdigest(),
            "opportunity_stream_bytes": opportunity_summary.stream_bytes,
            "opportunity_count": opportunity_summary.counts.opportunity_count,
            "spatial_attention_schema_version": attention_evaluation.schema_version,
            "spatial_attention_model_id": attention_evaluation.model_id,
            "attention_stream_sha256": attention_summary.stream_sha256,
            "attention_summary_sha256": sha256(attention_summary_bytes).hexdigest(),
            "attention_stream_bytes": attention_summary.stream_bytes,
            "impression_count": attention_summary.counts.impression_count,
            "noticed_count": attention_summary.counts.noticed_count,
            "seed": seed,
            "agent_count": agent_count,
            "days": days,
            "frame_count": summary.frame_count,
            "position_count": summary.position_count,
        }
        if spatial_response is None:
            manifest = CityRunManifestV5.model_validate(common_spatial_receipt)
        else:
            response_evaluation = evaluate_spatial_responses(
                spatial_response,
                spatial_scenario,
                opportunity_evaluation,
                attention_evaluation,
                agent_ids=tuple(agent.agent_id for agent in mobility.agents),
            )
            response_summary = summarize_spatial_response_artifact(response_evaluation)
            response_summary_bytes = (canonical_json(response_summary) + "\n").encode("utf-8")
            response_receipt = {
                **common_spatial_receipt,
                "spatial_response_schema_version": response_evaluation.schema_version,
                "spatial_response_model_id": response_evaluation.model_id,
                "response_input_sha256": response_evaluation.response_input_sha256,
                "response_stream_sha256": response_summary.stream_sha256,
                "response_state_sha256": response_summary.state_document_sha256,
                "response_summary_sha256": sha256(response_summary_bytes).hexdigest(),
                "response_stream_bytes": response_summary.stream_bytes,
                "response_count": response_summary.counts.response_count,
                "state_update_count": response_summary.counts.state_update_count,
                "response_campaign_count": response_summary.counts.campaign_count,
                "final_state_count": response_summary.counts.final_state_count,
            }
            if workbench_input is None:
                manifest = CityRunManifestV6.model_validate(response_receipt)
            else:
                manifest_model: type[CityRunManifestV7] | type[CityRunManifestV8]
                if isinstance(workbench_input, WorkbenchRunInput):
                    manifest_model = CityRunManifestV7
                elif isinstance(workbench_input, WorkbenchRunInputV2):
                    manifest_model = CityRunManifestV8
                else:
                    raise ValueError("unsupported workbench input schema version")
                manifest = manifest_model.model_validate(
                    {
                        **response_receipt,
                        "workbench_input_schema_version": workbench_input.schema_version,
                        "workbench_input_sha256": workbench_input.fingerprint,
                    }
                )
    elif places is not None:
        assignments = mobility.place_assignment_document()
        manifest = CityRunManifestV3(
            run_id=run_id,
            city_schema_version=pack.schema_version,
            place_schema_version=places.schema_version,
            package_version=__version__,
            python_version=python_version,
            city_sha256=pack.fingerprint,
            place_set_sha256=places.fingerprint,
            place_assignments_sha256=_document_sha256(assignments),
            agents_sha256=summary.agents_sha256,
            trace_sha256=summary.trace_sha256,
            seed=seed,
            agent_count=agent_count,
            days=days,
            frame_count=summary.frame_count,
            position_count=summary.position_count,
        )
    elif isinstance(pack, CityPackV2):
        manifest = CityRunManifestV2(
            run_id=run_id,
            city_schema_version=pack.schema_version,
            package_version=__version__,
            python_version=python_version,
            city_sha256=pack.fingerprint,
            agents_sha256=summary.agents_sha256,
            trace_sha256=summary.trace_sha256,
            seed=seed,
            agent_count=agent_count,
            days=days,
            frame_count=summary.frame_count,
            position_count=summary.position_count,
        )
    else:
        manifest = CityRunManifest(
            run_id=run_id,
            package_version=__version__,
            python_version=python_version,
            city_sha256=pack.fingerprint,
            agents_sha256=summary.agents_sha256,
            trace_sha256=summary.trace_sha256,
            seed=seed,
            agent_count=agent_count,
            days=days,
            frame_count=summary.frame_count,
            position_count=summary.position_count,
        )
    return PreparedCityRun(
        manifest,
        pack,
        mobility,
        spatial_scenario=spatial_scenario,
        opportunity_evaluation=opportunity_evaluation,
        attention_evaluation=attention_evaluation,
        response_input=spatial_response,
        response_evaluation=response_evaluation,
        workbench_input=workbench_input,
    )


def publish_city_run(prepared: PreparedCityRun, *, root: Path) -> StoredCityRun:
    """Publish already evaluated evidence through the no-clobber city store."""
    if not isinstance(prepared, PreparedCityRun):
        raise TypeError("prepared must be a PreparedCityRun")
    directory = CityRunStore(root).save(
        prepared.manifest,
        prepared.pack,
        prepared.mobility.agents,
        places=prepared.mobility.places,
        place_assignments=prepared.mobility.place_assignments,
        spatial_scenario=prepared.spatial_scenario,
        opportunity_evaluation=prepared.opportunity_evaluation,
        attention_evaluation=prepared.attention_evaluation,
        response_input=prepared.response_input,
        response_evaluation=prepared.response_evaluation,
        workbench_input=prepared.workbench_input,
        cognition_input=prepared.cognition_input,
        cognition_evaluation=prepared.cognition_evaluation,
    )
    return StoredCityRun(
        prepared.manifest,
        prepared.pack,
        prepared.mobility,
        directory,
        spatial_scenario=prepared.spatial_scenario,
        opportunity_evaluation=prepared.opportunity_evaluation,
        attention_evaluation=prepared.attention_evaluation,
        response_input=prepared.response_input,
        response_evaluation=prepared.response_evaluation,
        workbench_input=prepared.workbench_input,
        cognition_input=prepared.cognition_input,
        cognition_evaluation=prepared.cognition_evaluation,
    )


def create_city_run(
    pack: CityPackDocument,
    *,
    root: Path,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    places: CityPlaceSet | None = None,
    spatial_scenario: SpatialCampaignScenario | None = None,
    spatial_response: SpatialResponseInput | None = None,
    workbench_input: WorkbenchRunInputDocument | None = None,
) -> StoredCityRun:
    """Prepare and publish a fresh, never-reused city run."""
    prepared = prepare_city_run(
        pack,
        run_id=run_id,
        seed=seed,
        agent_count=agent_count,
        days=days,
        places=places,
        spatial_scenario=spatial_scenario,
        spatial_response=spatial_response,
        workbench_input=workbench_input,
    )
    return publish_city_run(prepared, root=root)


def replay_city_run(stored: StoredCityRun) -> CityReplayResult:
    """Recompute all minute frames; a mismatch is corruption, never a repair."""
    summary = summarize_city_trace(stored.mobility)
    manifest = stored.manifest
    replayed_workbench: WorkbenchRunInputDocument | None = None
    if isinstance(manifest, (CityRunManifestV7, CityRunManifestV8, CityRunManifestV9)):
        if stored.workbench_input is None:
            raise CorruptRunArtifact("city run is missing its frozen workbench input")
        try:
            workbench_input = validate_workbench_execution_binding(
                stored.workbench_input,
                pack=stored.pack,
                run_id=manifest.run_id,
                seed=manifest.seed,
                agent_count=manifest.agent_count,
                days=manifest.days,
                places=stored.mobility.places,
                spatial_scenario=stored.spatial_scenario,
                spatial_response=stored.response_input,
            )
        except (ValueError, TypeError):
            raise CorruptRunArtifact(
                "city run workbench input does not replay identically"
            ) from None
        if (
            manifest.workbench_input_schema_version != workbench_input.schema_version
            or manifest.workbench_input_sha256 != workbench_input.fingerprint
        ):
            raise CorruptRunArtifact("city run workbench input does not replay identically")
        replayed_workbench = workbench_input
    elif stored.workbench_input is not None:
        raise CorruptRunArtifact("legacy city run has an undeclared workbench input")
    place_set_sha256: str | None = None
    place_assignments_sha256: str | None = None
    place_manifest = (
        manifest
        if isinstance(manifest, CityRunManifestV3)
        or (
            isinstance(
                manifest,
                (
                    CityRunManifestV4,
                    CityRunManifestV5,
                    CityRunManifestV6,
                    CityRunManifestV7,
                    CityRunManifestV8,
                    CityRunManifestV9,
                ),
            )
            and manifest.place_schema_version is not None
        )
        else None
    )
    if place_manifest is not None:
        if stored.mobility.places is None:
            raise CorruptRunArtifact("city run is missing its frozen place set")
        place_set_sha256 = stored.mobility.places.fingerprint
        place_assignments_sha256 = _document_sha256(stored.mobility.place_assignment_document())
    scenario_sha256: str | None = None
    opportunity_stream_sha256: str | None = None
    opportunity_summary_sha256: str | None = None
    opportunity_stream_bytes: int | None = None
    opportunity_count: int | None = None
    opportunity_counts: SpatialOpportunityCounts | None = None
    attention_model_id: str | None = None
    attention_claim_scope: str | None = None
    attention_notice_probability: float | None = None
    attention_stream_sha256: str | None = None
    attention_summary_sha256: str | None = None
    attention_stream_bytes: int | None = None
    impression_count: int | None = None
    noticed_count: int | None = None
    attention_counts: SpatialAttentionCounts | None = None
    response_model_id: str | None = None
    response_claim_scope: str | None = None
    response_input_sha256: str | None = None
    response_stream_sha256: str | None = None
    response_state_sha256: str | None = None
    response_summary_sha256: str | None = None
    response_stream_bytes: int | None = None
    response_count: int | None = None
    state_update_count: int | None = None
    response_campaign_count: int | None = None
    final_state_count: int | None = None
    response_counts: SpatialResponseCounts | None = None
    replayed_response_evaluation: SpatialResponseEvaluation | None = None
    cognition_evaluation: CityCognitionEvaluationV1 | None = None
    if isinstance(
        manifest,
        (
            CityRunManifestV4,
            CityRunManifestV5,
            CityRunManifestV6,
            CityRunManifestV7,
            CityRunManifestV8,
            CityRunManifestV9,
        ),
    ):
        if stored.spatial_scenario is None or stored.opportunity_evaluation is None:
            raise CorruptRunArtifact("city run is missing its frozen spatial evidence")
        opportunity_evaluation = evaluate_spatial_opportunities(
            stored.mobility, stored.spatial_scenario
        )
        opportunity_summary = summarize_spatial_opportunity_artifact(opportunity_evaluation)
        opportunity_summary_bytes = (canonical_json(opportunity_summary) + "\n").encode("utf-8")
        scenario_sha256 = stored.spatial_scenario.fingerprint
        opportunity_stream_sha256 = opportunity_summary.stream_sha256
        opportunity_summary_sha256 = sha256(opportunity_summary_bytes).hexdigest()
        opportunity_stream_bytes = opportunity_summary.stream_bytes
        opportunity_count = opportunity_summary.counts.opportunity_count
        opportunity_counts = opportunity_summary.counts
        if opportunity_evaluation != stored.opportunity_evaluation:
            raise CorruptRunArtifact("city run spatial evidence does not replay identically")
        if isinstance(
            manifest,
            (
                CityRunManifestV5,
                CityRunManifestV6,
                CityRunManifestV7,
                CityRunManifestV8,
                CityRunManifestV9,
            ),
        ):
            if stored.attention_evaluation is None:
                raise CorruptRunArtifact("city run is missing its frozen attention evidence")
            attention_evaluation = evaluate_spatial_attention(
                opportunity_evaluation,
                seed=manifest.seed,
            )
            attention_summary = summarize_spatial_attention_artifact(attention_evaluation)
            attention_summary_bytes = (canonical_json(attention_summary) + "\n").encode("utf-8")
            attention_model_id = attention_evaluation.model_id
            attention_claim_scope = attention_evaluation.claim_scope
            attention_notice_probability = attention_evaluation.notice_probability
            attention_stream_sha256 = attention_summary.stream_sha256
            attention_summary_sha256 = sha256(attention_summary_bytes).hexdigest()
            attention_stream_bytes = attention_summary.stream_bytes
            impression_count = attention_summary.counts.impression_count
            noticed_count = attention_summary.counts.noticed_count
            attention_counts = attention_summary.counts
            if attention_evaluation != stored.attention_evaluation:
                raise CorruptRunArtifact("city run attention evidence does not replay identically")
            if isinstance(
                manifest,
                (
                    CityRunManifestV6,
                    CityRunManifestV7,
                    CityRunManifestV8,
                    CityRunManifestV9,
                ),
            ):
                if stored.response_input is None or stored.response_evaluation is None:
                    raise CorruptRunArtifact("city run is missing its frozen response evidence")
                response_evaluation = evaluate_spatial_responses(
                    stored.response_input,
                    stored.spatial_scenario,
                    opportunity_evaluation,
                    attention_evaluation,
                    agent_ids=tuple(agent.agent_id for agent in stored.mobility.agents),
                )
                response_summary = summarize_spatial_response_artifact(response_evaluation)
                response_summary_bytes = (canonical_json(response_summary) + "\n").encode("utf-8")
                response_model_id = response_evaluation.model_id
                response_claim_scope = response_evaluation.claim_scope
                response_input_sha256 = stored.response_input.fingerprint
                response_stream_sha256 = response_summary.stream_sha256
                response_state_sha256 = response_summary.state_document_sha256
                response_summary_sha256 = sha256(response_summary_bytes).hexdigest()
                response_stream_bytes = response_summary.stream_bytes
                response_count = response_summary.counts.response_count
                state_update_count = response_summary.counts.state_update_count
                response_campaign_count = response_summary.counts.campaign_count
                final_state_count = response_summary.counts.final_state_count
                response_counts = response_summary.counts
                if response_evaluation != stored.response_evaluation:
                    raise CorruptRunArtifact(
                        "city run response evidence does not replay identically"
                    )
                replayed_response_evaluation = response_evaluation
    if isinstance(manifest, CityRunManifestV9):
        if (
            not isinstance(replayed_workbench, WorkbenchRunInputV3)
            or not isinstance(stored.pack, CityPackV2)
            or not isinstance(stored.cognition_input, CityCognitionInputV1)
            or not isinstance(stored.cognition_evaluation, CityCognitionEvaluationV1)
            or stored.spatial_scenario is None
            or stored.response_input is None
            or replayed_response_evaluation is None
        ):
            raise CorruptRunArtifact("city run is missing its frozen cognition evidence")
        try:
            cognition_context = build_city_cognition_run_context(
                replayed_workbench,
                pack=stored.pack,
                spatial_scenario=stored.spatial_scenario,
                response_input=stored.response_input,
                response_evaluation=replayed_response_evaluation,
            )
            cognition_requests = build_city_cognition_requests(
                cognition_context,
                stored.mobility,
                replayed_response_evaluation,
            )
            cognition_evaluation = validate_city_cognition_evaluation(
                stored.cognition_input,
                cognition_requests,
                stored.cognition_evaluation.records,
            )
            cognition_receipt = build_city_cognition_manifest_receipt(
                stored.cognition_input,
                cognition_evaluation,
            )
        except ValueError:
            raise CorruptRunArtifact(
                "city run cognition evidence does not replay identically"
            ) from None
        if (
            cognition_evaluation != stored.cognition_evaluation
            or cognition_receipt != manifest.cognition
        ):
            raise CorruptRunArtifact("city run cognition evidence does not replay identically")
    elif stored.cognition_input is not None or stored.cognition_evaluation is not None:
        raise CorruptRunArtifact("legacy city run has undeclared cognition evidence")
    if (
        stored.pack.fingerprint != manifest.city_sha256
        or summary.agents_sha256 != manifest.agents_sha256
        or summary.trace_sha256 != manifest.trace_sha256
        or summary.frame_count != manifest.frame_count
        or summary.position_count != manifest.position_count
        or (
            place_manifest is not None
            and (
                place_set_sha256 != place_manifest.place_set_sha256
                or place_assignments_sha256 != place_manifest.place_assignments_sha256
            )
        )
        or (
            isinstance(
                manifest,
                (
                    CityRunManifestV4,
                    CityRunManifestV5,
                    CityRunManifestV6,
                    CityRunManifestV7,
                    CityRunManifestV8,
                    CityRunManifestV9,
                ),
            )
            and (
                scenario_sha256 != manifest.scenario_sha256
                or opportunity_stream_sha256 != manifest.opportunity_stream_sha256
                or opportunity_summary_sha256 != manifest.opportunity_summary_sha256
                or opportunity_stream_bytes != manifest.opportunity_stream_bytes
                or opportunity_count != manifest.opportunity_count
            )
        )
        or (
            isinstance(
                manifest,
                (
                    CityRunManifestV5,
                    CityRunManifestV6,
                    CityRunManifestV7,
                    CityRunManifestV8,
                    CityRunManifestV9,
                ),
            )
            and (
                attention_model_id != manifest.spatial_attention_model_id
                or attention_stream_sha256 != manifest.attention_stream_sha256
                or attention_summary_sha256 != manifest.attention_summary_sha256
                or attention_stream_bytes != manifest.attention_stream_bytes
                or impression_count != manifest.impression_count
                or noticed_count != manifest.noticed_count
            )
        )
        or (
            isinstance(
                manifest,
                (
                    CityRunManifestV6,
                    CityRunManifestV7,
                    CityRunManifestV8,
                    CityRunManifestV9,
                ),
            )
            and (
                response_model_id != manifest.spatial_response_model_id
                or response_input_sha256 != manifest.response_input_sha256
                or response_stream_sha256 != manifest.response_stream_sha256
                or response_state_sha256 != manifest.response_state_sha256
                or response_summary_sha256 != manifest.response_summary_sha256
                or response_stream_bytes != manifest.response_stream_bytes
                or response_count != manifest.response_count
                or state_update_count != manifest.state_update_count
                or response_campaign_count != manifest.response_campaign_count
                or final_state_count != manifest.final_state_count
            )
        )
    ):
        raise CorruptRunArtifact("city run does not replay identically")
    return CityReplayResult(
        run_id=manifest.run_id,
        identical=True,
        city_sha256=manifest.city_sha256,
        agents_sha256=summary.agents_sha256,
        trace_sha256=summary.trace_sha256,
        frame_count=summary.frame_count,
        position_count=summary.position_count,
        place_set_sha256=place_set_sha256,
        place_assignments_sha256=place_assignments_sha256,
        scenario_sha256=scenario_sha256,
        opportunity_stream_sha256=opportunity_stream_sha256,
        opportunity_summary_sha256=opportunity_summary_sha256,
        opportunity_stream_bytes=opportunity_stream_bytes,
        opportunity_count=opportunity_count,
        opportunity_counts=opportunity_counts,
        attention_model_id=attention_model_id,
        attention_claim_scope=attention_claim_scope,
        attention_notice_probability=attention_notice_probability,
        attention_stream_sha256=attention_stream_sha256,
        attention_summary_sha256=attention_summary_sha256,
        attention_stream_bytes=attention_stream_bytes,
        impression_count=impression_count,
        noticed_count=noticed_count,
        attention_counts=attention_counts,
        response_model_id=response_model_id,
        response_claim_scope=response_claim_scope,
        response_input_sha256=response_input_sha256,
        response_stream_sha256=response_stream_sha256,
        response_state_sha256=response_state_sha256,
        response_summary_sha256=response_summary_sha256,
        response_stream_bytes=response_stream_bytes,
        response_count=response_count,
        state_update_count=state_update_count,
        response_campaign_count=response_campaign_count,
        final_state_count=final_state_count,
        response_counts=response_counts,
        cognition_evaluation=cognition_evaluation,
    )


__all__ = [
    "CityReplayResult",
    "PreparedCityRun",
    "PreparedCityScientificRun",
    "PreparedCityScientificRunV2",
    "create_city_run",
    "finalize_city_run",
    "finalize_city_run_v2",
    "prepare_city_run",
    "prepare_city_scientific_run",
    "prepare_city_scientific_run_v2",
    "publish_city_run",
    "replay_city_run",
]
