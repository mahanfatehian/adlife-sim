"""Create and independently verify saved synthetic city mobility runs."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from adlife import __version__
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.core.domain.city import CityPackDocument, CityPackV2
from adlife.core.domain.city_places import CityPlaceSet
from adlife.core.domain.city_run import (
    CityRunManifest,
    CityRunManifestDocument,
    CityRunManifestV2,
    CityRunManifestV3,
    CityRunManifestV4,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.ports.run_store import CorruptRunArtifact
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.city_trace import summarize_city_trace
from adlife.core.simulation.spatial_opportunity import (
    SpatialOpportunityCounts,
    SpatialOpportunityEvaluation,
    evaluate_spatial_opportunities,
    summarize_spatial_opportunity_artifact,
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


def _document_sha256(value: Mapping[str, object]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


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
) -> StoredCityRun:
    """Freeze a bounded mobility trace under a fresh, never-reused city run ID."""
    if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
        raise ValueError("city run seed must be between 0 and 2^63-1")
    if type(agent_count) is not int or not 1 <= agent_count <= 30:
        raise ValueError("saved city runs require 1 to 30 agents")
    if type(days) is not int or not 1 <= days <= 7:
        raise ValueError("saved city runs require 1 to 7 days")
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
    if spatial_scenario is not None:
        opportunity_evaluation = evaluate_spatial_opportunities(mobility, spatial_scenario)
        opportunity_summary = summarize_spatial_opportunity_artifact(opportunity_evaluation)
        opportunity_summary_bytes = (canonical_json(opportunity_summary) + "\n").encode("utf-8")
        place_schema_version = None if places is None else places.schema_version
        place_set_sha256 = None if places is None else places.fingerprint
        place_assignments_sha256 = (
            None if places is None else _document_sha256(mobility.place_assignment_document())
        )
        manifest = CityRunManifestV4(
            run_id=run_id,
            city_schema_version=pack.schema_version,
            spatial_scenario_schema_version=spatial_scenario.schema_version,
            spatial_opportunity_schema_version=opportunity_evaluation.schema_version,
            place_schema_version=place_schema_version,
            place_set_sha256=place_set_sha256,
            place_assignments_sha256=place_assignments_sha256,
            package_version=__version__,
            python_version=python_version,
            city_sha256=pack.fingerprint,
            agents_sha256=summary.agents_sha256,
            trace_sha256=summary.trace_sha256,
            scenario_sha256=spatial_scenario.fingerprint,
            opportunity_stream_sha256=opportunity_summary.stream_sha256,
            opportunity_summary_sha256=sha256(opportunity_summary_bytes).hexdigest(),
            opportunity_stream_bytes=opportunity_summary.stream_bytes,
            opportunity_count=opportunity_summary.counts.opportunity_count,
            seed=seed,
            agent_count=agent_count,
            days=days,
            frame_count=summary.frame_count,
            position_count=summary.position_count,
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
    directory = CityRunStore(root).save(
        manifest,
        pack,
        mobility.agents,
        places=places,
        place_assignments=mobility.place_assignments,
        spatial_scenario=spatial_scenario,
        opportunity_evaluation=opportunity_evaluation,
    )
    return StoredCityRun(
        manifest,
        pack,
        mobility,
        directory,
        spatial_scenario=spatial_scenario,
        opportunity_evaluation=opportunity_evaluation,
    )


def replay_city_run(stored: StoredCityRun) -> CityReplayResult:
    """Recompute all minute frames; a mismatch is corruption, never a repair."""
    summary = summarize_city_trace(stored.mobility)
    manifest = stored.manifest
    place_set_sha256: str | None = None
    place_assignments_sha256: str | None = None
    place_manifest = (
        manifest
        if isinstance(manifest, CityRunManifestV3)
        or (isinstance(manifest, CityRunManifestV4) and manifest.place_schema_version is not None)
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
    if isinstance(manifest, CityRunManifestV4):
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
            isinstance(manifest, CityRunManifestV4)
            and (
                scenario_sha256 != manifest.scenario_sha256
                or opportunity_stream_sha256 != manifest.opportunity_stream_sha256
                or opportunity_summary_sha256 != manifest.opportunity_summary_sha256
                or opportunity_stream_bytes != manifest.opportunity_stream_bytes
                or opportunity_count != manifest.opportunity_count
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
    )


__all__ = ["CityReplayResult", "create_city_run", "replay_city_run"]
