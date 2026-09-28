"""Bounded, no-clobber artifacts for saved synthetic city mobility runs."""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from adlife import __version__
from adlife.city.loader import MAX_CITY_PACK_BYTES
from adlife.core.domain.city import CityPack
from adlife.core.domain.city_run import CityRunManifest
from adlife.core.domain.serialization import DocumentNotSerialisable, canonical_json
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    RunNotFound,
    StorageError,
    UnsafeRunLocation,
    validate_run_id,
)
from adlife.core.simulation.city_mobility import CityAgent, CityMobility
from adlife.core.simulation.city_trace import summarize_city_trace

MAX_CITY_MANIFEST_BYTES = 65_536
MAX_CITY_AGENTS_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class StoredCityRun:
    manifest: CityRunManifest
    pack: CityPack
    mobility: CityMobility
    directory: Path


def _runtime_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _canonical_bytes(value: CityPack | CityRunManifest | dict[str, object]) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


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
        self, manifest: CityRunManifest, pack: CityPack, agents: tuple[CityAgent, ...]
    ) -> Path:
        """Reserve a fresh ID; publish the completion manifest only after frozen inputs."""
        try:
            manifest = CityRunManifest.model_validate(manifest.model_dump(mode="json"))
            pack = CityPack.model_validate_json(canonical_json(pack))
            mobility = CityMobility(
                pack,
                seed=manifest.seed,
                agent_count=manifest.agent_count,
                days=manifest.days,
            )
            summary = summarize_city_trace(mobility)
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
        except (ValidationError, ValueError, TypeError, DocumentNotSerialisable):
            raise CorruptRunArtifact("city run inputs or manifest are invalid") from None
        if (
            len(city_bytes) > MAX_CITY_PACK_BYTES
            or len(agents_bytes) > MAX_CITY_AGENTS_BYTES
            or len(manifest_bytes) > MAX_CITY_MANIFEST_BYTES
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
            for path, contents, label in (
                (inputs / "city.json", city_bytes, "city input"),
                (inputs / "agents.json", agents_bytes, "agent assignments"),
            ):
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
        city_bytes = self._read_document(
            directory / "inputs" / "city.json", limit=MAX_CITY_PACK_BYTES, label="city input"
        )
        agents_bytes = self._read_document(
            directory / "inputs" / "agents.json",
            limit=MAX_CITY_AGENTS_BYTES,
            label="agent assignments",
        )
        try:
            manifest = CityRunManifest.model_validate_json(manifest_bytes)
            pack = CityPack.model_validate_json(city_bytes)
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
                pack, seed=manifest.seed, agent_count=manifest.agent_count, days=manifest.days
            )
            expected_agents = _canonical_bytes(
                {"agents": [asdict(agent) for agent in mobility.agents]}
            )
            if agents_bytes != expected_agents:
                raise CorruptRunArtifact("city run agent assignments do not match")
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
