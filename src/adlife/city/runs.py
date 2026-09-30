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
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import CorruptRunArtifact
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.city_trace import summarize_city_trace


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
    if places is not None:
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
    )
    return StoredCityRun(manifest, pack, mobility, directory)


def replay_city_run(stored: StoredCityRun) -> CityReplayResult:
    """Recompute all minute frames; a mismatch is corruption, never a repair."""
    summary = summarize_city_trace(stored.mobility)
    manifest = stored.manifest
    place_set_sha256: str | None = None
    place_assignments_sha256: str | None = None
    if isinstance(manifest, CityRunManifestV3):
        if stored.mobility.places is None:
            raise CorruptRunArtifact("city run is missing its frozen place set")
        place_set_sha256 = stored.mobility.places.fingerprint
        place_assignments_sha256 = _document_sha256(stored.mobility.place_assignment_document())
    if (
        stored.pack.fingerprint != manifest.city_sha256
        or summary.agents_sha256 != manifest.agents_sha256
        or summary.trace_sha256 != manifest.trace_sha256
        or summary.frame_count != manifest.frame_count
        or summary.position_count != manifest.position_count
        or (
            isinstance(manifest, CityRunManifestV3)
            and (
                place_set_sha256 != manifest.place_set_sha256
                or place_assignments_sha256 != manifest.place_assignments_sha256
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
    )


__all__ = ["CityReplayResult", "create_city_run", "replay_city_run"]
