"""Create and independently verify saved synthetic city mobility runs."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from adlife import __version__
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.workbench_binding import validate_workbench_execution_binding
from adlife.city.workbench_input import WorkbenchRunInput
from adlife.core.domain.city import CityPackDocument, CityPackV2
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
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.domain.spatial_response import (
    SpatialResponseInput,
    parse_spatial_response_input_json,
)
from adlife.core.ports.run_store import CorruptRunArtifact
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
    response_evaluation: SpatialResponseEvaluation | None = None
    workbench_input: WorkbenchRunInput | None = None


def _document_sha256(value: Mapping[str, object]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validated_workbench_input(
    workbench_input: WorkbenchRunInput | None,
    *,
    pack: CityPackDocument,
    run_id: str,
    seed: int,
    agent_count: int,
    days: int,
    places: CityPlaceSet | None,
    spatial_scenario: SpatialCampaignScenario | None,
    spatial_response: SpatialResponseInput | None,
) -> WorkbenchRunInput | None:
    if workbench_input is None:
        return None
    return validate_workbench_execution_binding(
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
    workbench_input: WorkbenchRunInput | None = None,
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
                manifest = CityRunManifestV7.model_validate(
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
    workbench_input: WorkbenchRunInput | None = None,
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
    if isinstance(manifest, CityRunManifestV7):
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
                (CityRunManifestV4, CityRunManifestV5, CityRunManifestV6, CityRunManifestV7),
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
    if isinstance(
        manifest,
        (CityRunManifestV4, CityRunManifestV5, CityRunManifestV6, CityRunManifestV7),
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
        if isinstance(manifest, (CityRunManifestV5, CityRunManifestV6, CityRunManifestV7)):
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
            if isinstance(manifest, (CityRunManifestV6, CityRunManifestV7)):
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
                (CityRunManifestV4, CityRunManifestV5, CityRunManifestV6, CityRunManifestV7),
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
            isinstance(manifest, (CityRunManifestV5, CityRunManifestV6, CityRunManifestV7))
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
            isinstance(manifest, (CityRunManifestV6, CityRunManifestV7))
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
    )


__all__ = [
    "CityReplayResult",
    "PreparedCityRun",
    "create_city_run",
    "prepare_city_run",
    "publish_city_run",
    "replay_city_run",
]
