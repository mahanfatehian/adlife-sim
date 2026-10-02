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
from adlife.city.spatial_loader import MAX_SPATIAL_CAMPAIGN_BYTES
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
    CityRunManifestV4,
    CityRunManifestV5,
    parse_city_run_manifest_json,
)
from adlife.core.domain.serialization import DocumentNotSerialisable, canonical_json
from adlife.core.domain.spatial_campaign import (
    SpatialCampaignScenario,
    parse_spatial_campaign_scenario_json,
)
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
from adlife.core.simulation.spatial_attention import (
    MAX_SPATIAL_ATTENTION_STREAM_BYTES,
    SpatialAttentionArtifactSummary,
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
    spatial_attention_lines,
    summarize_spatial_attention_artifact,
)
from adlife.core.simulation.spatial_opportunity import (
    MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES,
    SpatialOpportunityArtifactSummary,
    SpatialOpportunityEvaluation,
    evaluate_spatial_opportunities,
    spatial_opportunity_lines,
    summarize_spatial_opportunity_artifact,
)

MAX_CITY_MANIFEST_BYTES = 65_536
MAX_CITY_AGENTS_BYTES = 65_536
MAX_CITY_PLACE_ASSIGNMENTS_BYTES = 65_536
MAX_CITY_OPPORTUNITY_SUMMARY_BYTES = 65_536
MAX_CITY_ATTENTION_SUMMARY_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class StoredCityRun:
    manifest: CityRunManifestDocument
    pack: CityPackDocument
    mobility: CityMobility
    directory: Path
    spatial_scenario: SpatialCampaignScenario | None = None
    opportunity_evaluation: SpatialOpportunityEvaluation | None = None
    attention_evaluation: SpatialAttentionEvaluation | None = None


def _runtime_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _canonical_bytes(
    value: (
        CityPackDocument
        | CityPlaceSet
        | CityRunManifestDocument
        | SpatialCampaignScenario
        | SpatialAttentionArtifactSummary
        | SpatialOpportunityArtifactSummary
        | dict[str, object]
    ),
) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _document_sha256(value: Mapping[str, object]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_schema_pair(
    manifest: CityRunManifestDocument,
    pack: CityPackDocument,
    places: CityPlaceSet | None,
) -> None:
    if isinstance(manifest, (CityRunManifestV4, CityRunManifestV5)):
        if manifest.city_schema_version != pack.schema_version:
            raise CorruptRunArtifact("city run manifest and city pack schemas do not match")
        if manifest.place_schema_version is None:
            if places is not None:
                raise CorruptRunArtifact("spatial city run has an undeclared place set")
        elif (
            places is None
            or manifest.place_schema_version != places.schema_version
            or places.city_id != pack.city_id
            or places.city_sha256 != pack.fingerprint
        ):
            raise CorruptRunArtifact("city run manifest, city pack, and place schemas do not match")
    elif isinstance(manifest, CityRunManifestV3):
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


def _write_opportunity_stream(path: Path, evaluation: SpatialOpportunityEvaluation) -> None:
    with path.open("xb") as target:
        for line in spatial_opportunity_lines(evaluation):
            if target.write(line) != len(line):
                raise OSError("short city opportunity stream write")
        target.flush()
        os.fsync(target.fileno())


def _write_attention_stream(path: Path, evaluation: SpatialAttentionEvaluation) -> None:
    with path.open("xb") as target:
        for line in spatial_attention_lines(evaluation):
            if target.write(line) != len(line):
                raise OSError("short city attention stream write")
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
        spatial_scenario: SpatialCampaignScenario | None = None,
        opportunity_evaluation: SpatialOpportunityEvaluation | None = None,
        attention_evaluation: SpatialAttentionEvaluation | None = None,
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
            place_manifest = (
                manifest
                if isinstance(manifest, CityRunManifestV3)
                or (
                    isinstance(manifest, (CityRunManifestV4, CityRunManifestV5))
                    and manifest.place_schema_version is not None
                )
                else None
            )
            if place_manifest is not None:
                if (
                    places is None
                    or mobility.place_assignments != place_assignments
                    or place_manifest.place_set_sha256 != places.fingerprint
                    or place_manifest.place_assignments_sha256
                    != _document_sha256(assignment_document)
                ):
                    raise CorruptRunArtifact("city run manifest does not match place inputs")
            elif place_assignments:
                raise CorruptRunArtifact("legacy city run cannot contain place assignments")
            spatial_scenario_bytes: bytes | None = None
            opportunity_summary_bytes: bytes | None = None
            attention_summary_bytes: bytes | None = None
            verified_evaluation: SpatialOpportunityEvaluation | None = None
            verified_attention: SpatialAttentionEvaluation | None = None
            if isinstance(manifest, (CityRunManifestV4, CityRunManifestV5)):
                if spatial_scenario is None or opportunity_evaluation is None:
                    raise CorruptRunArtifact("spatial city run inputs are incomplete")
                spatial_scenario = parse_spatial_campaign_scenario_json(
                    canonical_json(spatial_scenario)
                )
                if not isinstance(opportunity_evaluation, SpatialOpportunityEvaluation):
                    raise CorruptRunArtifact("spatial opportunity evaluation is invalid")
                verified_evaluation = evaluate_spatial_opportunities(mobility, spatial_scenario)
                if verified_evaluation != opportunity_evaluation:
                    raise CorruptRunArtifact(
                        "spatial opportunity evaluation does not match frozen inputs"
                    )
                opportunity_summary = summarize_spatial_opportunity_artifact(verified_evaluation)
                spatial_scenario_bytes = _canonical_bytes(spatial_scenario)
                opportunity_summary_bytes = _canonical_bytes(opportunity_summary)
                if (
                    manifest.scenario_sha256 != spatial_scenario.fingerprint
                    or manifest.spatial_scenario_schema_version != spatial_scenario.schema_version
                    or manifest.spatial_opportunity_schema_version
                    != verified_evaluation.schema_version
                    or manifest.opportunity_stream_sha256 != opportunity_summary.stream_sha256
                    or manifest.opportunity_summary_sha256
                    != sha256(opportunity_summary_bytes).hexdigest()
                    or manifest.opportunity_stream_bytes != opportunity_summary.stream_bytes
                    or manifest.opportunity_count != opportunity_summary.counts.opportunity_count
                ):
                    raise CorruptRunArtifact(
                        "city run manifest does not match spatial opportunity inputs"
                    )
                if isinstance(manifest, CityRunManifestV5):
                    if not isinstance(attention_evaluation, SpatialAttentionEvaluation):
                        raise CorruptRunArtifact("spatial attention evaluation is incomplete")
                    verified_attention = evaluate_spatial_attention(
                        verified_evaluation,
                        seed=manifest.seed,
                    )
                    if verified_attention != attention_evaluation:
                        raise CorruptRunArtifact(
                            "spatial attention evaluation does not match frozen inputs"
                        )
                    attention_summary = summarize_spatial_attention_artifact(verified_attention)
                    attention_summary_bytes = _canonical_bytes(attention_summary)
                    if (
                        manifest.spatial_attention_schema_version
                        != verified_attention.schema_version
                        or manifest.spatial_attention_model_id != verified_attention.model_id
                        or manifest.attention_stream_sha256 != attention_summary.stream_sha256
                        or manifest.attention_summary_sha256
                        != sha256(attention_summary_bytes).hexdigest()
                        or manifest.attention_stream_bytes != attention_summary.stream_bytes
                        or manifest.impression_count != attention_summary.counts.impression_count
                        or manifest.noticed_count != attention_summary.counts.noticed_count
                    ):
                        raise CorruptRunArtifact(
                            "city run manifest does not match spatial attention inputs"
                        )
                elif attention_evaluation is not None:
                    raise CorruptRunArtifact("v4 city run cannot contain attention evidence")
            elif (
                spatial_scenario is not None
                or opportunity_evaluation is not None
                or attention_evaluation is not None
            ):
                raise CorruptRunArtifact("legacy city run cannot contain spatial inputs")
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
                None if place_manifest is None else _canonical_bytes(assignment_document)
            )
        except (ValidationError, ValueError, TypeError, DocumentNotSerialisable):
            raise CorruptRunArtifact("city run inputs or manifest are invalid") from None
        if (
            len(city_bytes) > MAX_CITY_PACK_BYTES
            or len(agents_bytes) > MAX_CITY_AGENTS_BYTES
            or len(manifest_bytes) > MAX_CITY_MANIFEST_BYTES
            or (places_bytes is not None and len(places_bytes) > MAX_CITY_PLACE_SET_BYTES)
            or (
                spatial_scenario_bytes is not None
                and len(spatial_scenario_bytes) > MAX_SPATIAL_CAMPAIGN_BYTES
            )
            or (
                opportunity_summary_bytes is not None
                and len(opportunity_summary_bytes) > MAX_CITY_OPPORTUNITY_SUMMARY_BYTES
            )
            or (
                attention_summary_bytes is not None
                and len(attention_summary_bytes) > MAX_CITY_ATTENTION_SUMMARY_BYTES
            )
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
            if spatial_scenario_bytes is not None:
                documents.append(
                    (
                        inputs / "spatial-campaign.json",
                        spatial_scenario_bytes,
                        "spatial campaign input",
                    )
                )
            for path, contents, label in documents:
                _write_new(path, contents)
                if self._read_document(path, limit=len(contents), label=label) != contents:
                    raise StorageError("city run staged input did not match its expected bytes")
            if opportunity_summary_bytes is not None and verified_evaluation is not None:
                outputs = directory / "outputs"
                outputs.mkdir()
                summary_path = outputs / "opportunity-summary.json"
                stream_path = outputs / "spatial-opportunities.jsonl"
                _write_new(summary_path, opportunity_summary_bytes)
                if (
                    self._read_document(
                        summary_path,
                        limit=len(opportunity_summary_bytes),
                        label="opportunity summary",
                    )
                    != opportunity_summary_bytes
                ):
                    raise StorageError(
                        "city run staged opportunity summary did not match expected bytes"
                    )
                _write_opportunity_stream(stream_path, verified_evaluation)
                try:
                    self._verify_opportunity_stream(stream_path, verified_evaluation)
                except CorruptRunArtifact:
                    raise StorageError(
                        "city run staged opportunity stream did not match expected bytes"
                    ) from None
            if attention_summary_bytes is not None and verified_attention is not None:
                outputs = directory / "outputs"
                summary_path = outputs / "attention-summary.json"
                stream_path = outputs / "spatial-attention.jsonl"
                _write_new(summary_path, attention_summary_bytes)
                if (
                    self._read_document(
                        summary_path,
                        limit=len(attention_summary_bytes),
                        label="attention summary",
                    )
                    != attention_summary_bytes
                ):
                    raise StorageError(
                        "city run staged attention summary did not match expected bytes"
                    )
                _write_attention_stream(stream_path, verified_attention)
                try:
                    self._verify_attention_stream(stream_path, verified_attention)
                except CorruptRunArtifact:
                    raise StorageError(
                        "city run staged attention stream did not match expected bytes"
                    ) from None
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

    def _verify_opportunity_stream(
        self,
        path: Path,
        evaluation: SpatialOpportunityEvaluation,
    ) -> None:
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise UnsafeRunLocation("city run opportunity stream has an unsafe location")
        expected_summary = summarize_spatial_opportunity_artifact(evaluation)
        try:
            if (
                path.stat().st_size != expected_summary.stream_bytes
                or path.stat().st_size > MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES
            ):
                raise CorruptRunArtifact("city run opportunity stream size does not match")
            with path.open("rb") as source:
                for expected_line in spatial_opportunity_lines(evaluation):
                    if source.read(len(expected_line)) != expected_line:
                        raise CorruptRunArtifact(
                            "city run opportunity stream does not match frozen inputs"
                        )
                if source.read(1) != b"":
                    raise CorruptRunArtifact(
                        "city run opportunity stream does not match frozen inputs"
                    )
        except FileNotFoundError:
            raise CorruptRunArtifact("city run opportunity stream is missing") from None
        except OSError:
            raise CorruptRunArtifact("city run opportunity stream is unreadable") from None

    def _verify_attention_stream(
        self,
        path: Path,
        evaluation: SpatialAttentionEvaluation,
    ) -> None:
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise UnsafeRunLocation("city run attention stream has an unsafe location")
        expected_summary = summarize_spatial_attention_artifact(evaluation)
        try:
            stream_size = path.stat().st_size
            if (
                stream_size != expected_summary.stream_bytes
                or stream_size > MAX_SPATIAL_ATTENTION_STREAM_BYTES
            ):
                raise CorruptRunArtifact("city run attention stream size does not match")
            with path.open("rb") as source:
                for expected_line in spatial_attention_lines(evaluation):
                    if source.read(len(expected_line)) != expected_line:
                        raise CorruptRunArtifact(
                            "city run attention stream does not match frozen inputs"
                        )
                if source.read(1) != b"":
                    raise CorruptRunArtifact(
                        "city run attention stream does not match frozen inputs"
                    )
        except FileNotFoundError:
            raise CorruptRunArtifact("city run attention stream is missing") from None
        except OSError:
            raise CorruptRunArtifact("city run attention stream is unreadable") from None

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
        place_manifest = (
            manifest
            if isinstance(manifest, CityRunManifestV3)
            or (
                isinstance(manifest, (CityRunManifestV4, CityRunManifestV5))
                and manifest.place_schema_version is not None
            )
            else None
        )
        if place_manifest is not None:
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
        spatial_scenario_bytes: bytes | None = None
        opportunity_summary_bytes: bytes | None = None
        attention_summary_bytes: bytes | None = None
        if isinstance(manifest, (CityRunManifestV4, CityRunManifestV5)):
            outputs = directory / "outputs"
            if not outputs.is_dir():
                raise CorruptRunArtifact("city run outputs directory is incomplete")
            if outputs.is_symlink():
                raise UnsafeRunLocation("city run outputs directory cannot be a symlink")
            spatial_scenario_bytes = self._read_document(
                directory / "inputs" / "spatial-campaign.json",
                limit=MAX_SPATIAL_CAMPAIGN_BYTES,
                label="spatial campaign input",
            )
            opportunity_summary_bytes = self._read_document(
                outputs / "opportunity-summary.json",
                limit=MAX_CITY_OPPORTUNITY_SUMMARY_BYTES,
                label="opportunity summary",
            )
            if isinstance(manifest, CityRunManifestV5):
                attention_summary_bytes = self._read_document(
                    outputs / "attention-summary.json",
                    limit=MAX_CITY_ATTENTION_SUMMARY_BYTES,
                    label="attention summary",
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
            if place_manifest is not None:
                if places is None or places_bytes is None or assignments_bytes is None:
                    raise CorruptRunArtifact("city run place inputs are missing")
                expected_assignments = mobility.place_assignment_document()
                if (
                    places_bytes != _canonical_bytes(places)
                    or assignments_bytes != _canonical_bytes(expected_assignments)
                    or place_manifest.place_set_sha256 != places.fingerprint
                    or place_manifest.place_assignments_sha256
                    != _document_sha256(expected_assignments)
                ):
                    raise CorruptRunArtifact("city run place inputs do not match")
            spatial_scenario: SpatialCampaignScenario | None = None
            opportunity_evaluation: SpatialOpportunityEvaluation | None = None
            attention_evaluation: SpatialAttentionEvaluation | None = None
            if isinstance(manifest, (CityRunManifestV4, CityRunManifestV5)):
                if spatial_scenario_bytes is None or opportunity_summary_bytes is None:
                    raise CorruptRunArtifact("city run spatial inputs are missing")
                spatial_scenario = parse_spatial_campaign_scenario_json(spatial_scenario_bytes)
                opportunity_evaluation = evaluate_spatial_opportunities(mobility, spatial_scenario)
                opportunity_summary = summarize_spatial_opportunity_artifact(opportunity_evaluation)
                expected_opportunity_summary_bytes = _canonical_bytes(opportunity_summary)
                if (
                    spatial_scenario_bytes != _canonical_bytes(spatial_scenario)
                    or opportunity_summary_bytes != expected_opportunity_summary_bytes
                    or manifest.scenario_sha256 != spatial_scenario.fingerprint
                    or manifest.spatial_scenario_schema_version != spatial_scenario.schema_version
                    or manifest.spatial_opportunity_schema_version
                    != opportunity_evaluation.schema_version
                    or manifest.opportunity_stream_sha256 != opportunity_summary.stream_sha256
                    or manifest.opportunity_summary_sha256
                    != sha256(expected_opportunity_summary_bytes).hexdigest()
                    or manifest.opportunity_stream_bytes != opportunity_summary.stream_bytes
                    or manifest.opportunity_count != opportunity_summary.counts.opportunity_count
                ):
                    raise CorruptRunArtifact("city run spatial opportunity artifacts do not match")
                self._verify_opportunity_stream(
                    directory / "outputs" / "spatial-opportunities.jsonl",
                    opportunity_evaluation,
                )
                if isinstance(manifest, CityRunManifestV5):
                    if attention_summary_bytes is None:
                        raise CorruptRunArtifact("city run attention inputs are missing")
                    attention_evaluation = evaluate_spatial_attention(
                        opportunity_evaluation,
                        seed=manifest.seed,
                    )
                    attention_summary = summarize_spatial_attention_artifact(attention_evaluation)
                    expected_attention_summary_bytes = _canonical_bytes(attention_summary)
                    if (
                        attention_summary_bytes != expected_attention_summary_bytes
                        or manifest.spatial_attention_schema_version
                        != attention_evaluation.schema_version
                        or manifest.spatial_attention_model_id != attention_evaluation.model_id
                        or manifest.attention_stream_sha256 != attention_summary.stream_sha256
                        or manifest.attention_summary_sha256
                        != sha256(expected_attention_summary_bytes).hexdigest()
                        or manifest.attention_stream_bytes != attention_summary.stream_bytes
                        or manifest.impression_count != attention_summary.counts.impression_count
                        or manifest.noticed_count != attention_summary.counts.noticed_count
                    ):
                        raise CorruptRunArtifact(
                            "city run spatial attention artifacts do not match"
                        )
                    self._verify_attention_stream(
                        directory / "outputs" / "spatial-attention.jsonl",
                        attention_evaluation,
                    )
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
        return StoredCityRun(
            manifest,
            pack,
            mobility,
            directory,
            spatial_scenario=spatial_scenario,
            opportunity_evaluation=opportunity_evaluation,
            attention_evaluation=attention_evaluation,
        )


__all__ = ["CityRunStore", "StoredCityRun"]
