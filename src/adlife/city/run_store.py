"""Bounded, no-clobber artifacts for saved synthetic city mobility runs."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from adlife import __version__
from adlife.city.loader import MAX_CITY_PACK_BYTES
from adlife.city.place_loader import MAX_CITY_PLACE_SET_BYTES
from adlife.core.domain.city import (
    CityPack,
    CityPackDocument,
    CityPackV2,
    parse_city_pack_json,
)
from adlife.core.domain.city_places import CityPlaceSet, parse_city_place_set_json
from adlife.core.domain.city_run import (
    CityRunManifest,
    CityRunManifestDocument,
    CityRunManifestV2,
    CityRunManifestV3,
    parse_city_run_manifest_json,
)
from adlife.core.domain.serialization import DocumentNotSerialisable, canonical_json
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    RunNotFound,
    StorageError,
    UnsafeRunLocation,
    validate_run_id,
)
from adlife.core.simulation.city_mobility import (
    CityAgent,
    CityMobility,
    CityPlaceAssignment,
)
from adlife.core.simulation.city_trace import summarize_city_trace

MAX_CITY_MANIFEST_BYTES = 65_536
MAX_CITY_AGENTS_BYTES = 65_536
MAX_CITY_PLACE_ASSIGNMENTS_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class StoredCityRun:
    manifest: CityRunManifestDocument
    pack: CityPackDocument
    mobility: CityMobility
    directory: Path


def _runtime_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _canonical_bytes(
    value: CityPackDocument | CityPlaceSet | CityRunManifestDocument | dict[str, object],
) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _document_sha256(value: Mapping[str, object]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_schema_pair(
    manifest: CityRunManifestDocument,
    pack: CityPackDocument,
    places: CityPlaceSet | None,
) -> None:
    if isinstance(manifest, CityRunManifestV3):
        if (
            manifest.city_schema_version != pack.schema_version
            or places is None
            or manifest.place_schema_version != places.schema_version
            or places.city_id != pack.city_id
            or places.city_sha256 != pack.fingerprint
        ):
            raise CorruptRunArtifact("city run manifest, city pack, and place schemas do not match")
    elif places is not None:
        raise CorruptRunArtifact("legacy city run manifest cannot contain a place set")
    elif isinstance(manifest, CityRunManifestV2):
        if not isinstance(pack, CityPackV2) or manifest.city_schema_version != pack.schema_version:
            raise CorruptRunArtifact("city run manifest and city pack schemas do not match")
    elif not isinstance(manifest, CityRunManifest) or not isinstance(pack, CityPack):
        raise CorruptRunArtifact("city run manifest and city pack schemas do not match")


def _write_new(path: Path, contents: bytes) -> None:
    with path.open("xb") as target:
        if target.write(contents) != len(contents):
            raise OSError("short city run artifact write")
        target.flush()
        os.fsync(target.fileno())


class CityRunStore:
    """A city run is complete only when its final manifest exists and verifies."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _runs_directory(self, *, create: bool = False) -> Path:
        runs = self.root / "city-runs"
        if runs.is_symlink():
            raise UnsafeRunLocation("city-runs directory cannot be a symlink")
        if create:
            try:
                runs.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise StorageError("city-runs directory could not be created") from None
        if runs.exists() and not runs.is_dir():
            raise UnsafeRunLocation("city-runs location is not a directory")
        return runs

    def run_directory(self, run_id: str, *, create_root: bool = False) -> Path:
        try:
            validate_run_id(run_id)
        except UnsafeRunLocation:
            raise UnsafeRunLocation("invalid city run identifier") from None
        runs = self._runs_directory(create=create_root)
        directory = runs / run_id
        if directory.is_symlink():
            raise UnsafeRunLocation("city run directory cannot be a symlink")
        if directory.exists() and not directory.resolve().is_relative_to(runs.resolve()):
            raise UnsafeRunLocation("city run directory escapes the output root")
        return directory

    def save(
        self,
        manifest: CityRunManifestDocument,
        pack: CityPackDocument,
        agents: tuple[CityAgent, ...],
        *,
        places: CityPlaceSet | None = None,
        place_assignments: tuple[CityPlaceAssignment, ...] = (),
    ) -> Path:
        """Reserve a fresh ID; publish the completion manifest only after frozen inputs."""
        try:
            manifest = parse_city_run_manifest_json(canonical_json(manifest))
            pack = parse_city_pack_json(canonical_json(pack))
            places = None if places is None else parse_city_place_set_json(canonical_json(places))
            _validate_schema_pair(manifest, pack, places)
            mobility = CityMobility(
                pack,
                seed=manifest.seed,
                agent_count=manifest.agent_count,
                days=manifest.days,
                places=places,
            )
            summary = summarize_city_trace(mobility)
            assignment_document = mobility.place_assignment_document()
            if isinstance(manifest, CityRunManifestV3):
                if (
                    places is None
                    or mobility.place_assignments != place_assignments
                    or manifest.place_set_sha256 != places.fingerprint
                    or manifest.place_assignments_sha256 != _document_sha256(assignment_document)
                ):
                    raise CorruptRunArtifact("city run manifest does not match place inputs")
            elif place_assignments:
                raise CorruptRunArtifact("legacy city run cannot contain place assignments")
            if (
                pack.fingerprint != manifest.city_sha256
                or mobility.agents != agents
                or summary.agents_sha256 != manifest.agents_sha256
                or summary.trace_sha256 != manifest.trace_sha256
                or summary.frame_count != manifest.frame_count
                or summary.position_count != manifest.position_count
                or manifest.package_version != __version__
                or manifest.python_version != _runtime_version()
            ):
                raise CorruptRunArtifact("city run manifest does not match generated inputs")
            city_bytes = _canonical_bytes(pack)
            agents_bytes = _canonical_bytes({"agents": [asdict(agent) for agent in agents]})
            manifest_bytes = _canonical_bytes(manifest)
            places_bytes = None if places is None else _canonical_bytes(places)
            assignments_bytes = (
                None
                if not isinstance(manifest, CityRunManifestV3)
                else _canonical_bytes(assignment_document)
            )
        except (ValidationError, ValueError, TypeError, DocumentNotSerialisable):
            raise CorruptRunArtifact("city run inputs or manifest are invalid") from None
        if (
            len(city_bytes) > MAX_CITY_PACK_BYTES
            or len(agents_bytes) > MAX_CITY_AGENTS_BYTES
            or len(manifest_bytes) > MAX_CITY_MANIFEST_BYTES
            or (places_bytes is not None and len(places_bytes) > MAX_CITY_PLACE_SET_BYTES)
            or (
                assignments_bytes is not None
                and len(assignments_bytes) > MAX_CITY_PLACE_ASSIGNMENTS_BYTES
            )
        ):
            raise CorruptRunArtifact("city run document exceeds its size limit")

        directory = self.run_directory(manifest.run_id, create_root=True)
        try:
            directory.mkdir()
        except FileExistsError:
            raise DuplicateRun("city run identifier already exists") from None
        except OSError:
            raise StorageError("city run directory could not be created") from None

        try:
            inputs = directory / "inputs"
            inputs.mkdir()
            documents: list[tuple[Path, bytes, str]] = [
                (inputs / "city.json", city_bytes, "city input"),
                (inputs / "agents.json", agents_bytes, "agent assignments"),
            ]
            if places_bytes is not None and assignments_bytes is not None:
                documents.extend(
                    [
                        (inputs / "places.json", places_bytes, "place input"),
                        (
                            inputs / "place-assignments.json",
                            assignments_bytes,
                            "place assignments",
                        ),
                    ]
                )
            for path, contents, label in documents:
                _write_new(path, contents)
                if self._read_document(path, limit=len(contents), label=label) != contents:
                    raise StorageError("city run staged input did not match its expected bytes")
            temporary = directory / ".run.json.tmp"
            try:
                _write_new(temporary, manifest_bytes)
                if (
                    self._read_document(
                        temporary, limit=len(manifest_bytes), label="staged manifest"
                    )
                    != manifest_bytes
                ):
                    raise StorageError("city run staged manifest did not match its expected bytes")
                os.replace(temporary, directory / "run.json")
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            # An incomplete directory remains visibly invalid; never reuse its ID.
            raise StorageError("city run could not be completed") from None
        return directory

    def _read_document(self, path: Path, *, limit: int, label: str) -> bytes:
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise UnsafeRunLocation(f"city run {label} has an unsafe location")
        try:
            with path.open("rb") as source:
                data = source.read(limit + 1)
        except OSError:
            raise CorruptRunArtifact(f"city run {label} is missing or unreadable") from None
        if len(data) > limit:
            raise CorruptRunArtifact(f"city run {label} exceeds its size limit")
        return data

    def load(self, run_id: str) -> StoredCityRun:
        """Refuse any missing, damaged, incompatible or partial city artifact."""
        directory = self.run_directory(run_id)
        if not directory.exists():
            raise RunNotFound("city run does not exist")
        if not directory.is_dir() or not (directory / "inputs").is_dir():
            raise CorruptRunArtifact("city run directory is incomplete")
        if (directory / "inputs").is_symlink():
            raise UnsafeRunLocation("city run inputs directory cannot be a symlink")
        manifest_bytes = self._read_document(
            directory / "run.json", limit=MAX_CITY_MANIFEST_BYTES, label="manifest"
        )
        try:
            manifest = parse_city_run_manifest_json(manifest_bytes)
        except (ValidationError, ValueError, TypeError):
            raise CorruptRunArtifact("city run manifest failed validation") from None
        city_bytes = self._read_document(
            directory / "inputs" / "city.json", limit=MAX_CITY_PACK_BYTES, label="city input"
        )
        agents_bytes = self._read_document(
            directory / "inputs" / "agents.json",
            limit=MAX_CITY_AGENTS_BYTES,
            label="agent assignments",
        )
        places_bytes: bytes | None = None
        assignments_bytes: bytes | None = None
        if isinstance(manifest, CityRunManifestV3):
            places_bytes = self._read_document(
                directory / "inputs" / "places.json",
                limit=MAX_CITY_PLACE_SET_BYTES,
                label="place input",
            )
            assignments_bytes = self._read_document(
                directory / "inputs" / "place-assignments.json",
                limit=MAX_CITY_PLACE_ASSIGNMENTS_BYTES,
                label="place assignments",
            )
        try:
            pack = parse_city_pack_json(city_bytes)
            places = None if places_bytes is None else parse_city_place_set_json(places_bytes)
            _validate_schema_pair(manifest, pack, places)
            if (
                manifest.run_id != run_id
                or manifest.package_version != __version__
                or manifest.python_version != _runtime_version()
                or manifest.city_sha256 != pack.fingerprint
                or manifest_bytes != _canonical_bytes(manifest)
                or city_bytes != _canonical_bytes(pack)
            ):
                raise CorruptRunArtifact("city run manifest or city input does not match")
            mobility = CityMobility(
                pack,
                seed=manifest.seed,
                agent_count=manifest.agent_count,
                days=manifest.days,
                places=places,
            )
            expected_agents = _canonical_bytes(
                {"agents": [asdict(agent) for agent in mobility.agents]}
            )
            if agents_bytes != expected_agents:
                raise CorruptRunArtifact("city run agent assignments do not match")
            if isinstance(manifest, CityRunManifestV3):
                if places is None or places_bytes is None or assignments_bytes is None:
                    raise CorruptRunArtifact("city run place inputs are missing")
                expected_assignments = mobility.place_assignment_document()
                if (
                    places_bytes != _canonical_bytes(places)
                    or assignments_bytes != _canonical_bytes(expected_assignments)
                    or manifest.place_set_sha256 != places.fingerprint
                    or manifest.place_assignments_sha256 != _document_sha256(expected_assignments)
                ):
                    raise CorruptRunArtifact("city run place inputs do not match")
            summary = summarize_city_trace(mobility)
            if (
                summary.agents_sha256 != manifest.agents_sha256
                or summary.trace_sha256 != manifest.trace_sha256
                or summary.frame_count != manifest.frame_count
                or summary.position_count != manifest.position_count
            ):
                raise CorruptRunArtifact("city run trace does not match its manifest")
        except (ValidationError, ValueError, TypeError, DocumentNotSerialisable):
            raise CorruptRunArtifact("city run artifact failed validation") from None
        return StoredCityRun(manifest, pack, mobility, directory)


__all__ = ["CityRunStore", "StoredCityRun"]
