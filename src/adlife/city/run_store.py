"""Bounded, no-clobber artifacts for saved synthetic city mobility runs."""

from __future__ import annotations

import errno
import json
import os
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from sys import exc_info
from threading import RLock
from typing import TYPE_CHECKING, BinaryIO, cast

from pydantic import BaseModel, ValidationError

from adlife import __version__
from adlife.city.loader import MAX_CITY_PACK_BYTES
from adlife.city.place_loader import MAX_CITY_PLACE_SET_BYTES
from adlife.city.response_loader import MAX_SPATIAL_RESPONSE_INPUT_BYTES
from adlife.city.spatial_loader import MAX_SPATIAL_CAMPAIGN_BYTES
from adlife.city.workbench_binding import (
    build_city_cognition_run_context,
    build_city_cognition_run_context_v2,
    validate_workbench_execution_binding,
)
from adlife.city.workbench_input import (
    WorkbenchRunInputDocument,
    WorkbenchRunInputV3,
    WorkbenchRunInputV4,
    parse_workbench_run_input_json,
)
from adlife.core.domain.city import (
    CityPack,
    CityPackDocument,
    CityPackV2,
    parse_city_pack_json,
)
from adlife.core.domain.city_cognition import (
    MAX_CITY_COGNITION_INPUT_BYTES,
    MAX_CITY_COGNITION_REQUESTS,
    MAX_CITY_COGNITION_STREAM_BYTES,
    MAX_CITY_COGNITION_SUMMARY_BYTES,
    MAX_CITY_COGNITION_TERMINAL_RECORD_BYTES,
    CityCognitionEvaluationV1,
    CityCognitionInputV1,
    CityCognitionSummaryV1,
    CityCognitionTerminalRecordV1,
)
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_SELECTION_LIMITS,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
    CityCognitionSummaryV2,
    CityCognitionTerminalRecordV2,
)
from adlife.core.domain.city_places import CityPlaceSet, parse_city_place_set_json
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
    parse_city_run_manifest_json,
)
from adlife.core.domain.serialization import DocumentNotSerialisable, canonical_json
from adlife.core.domain.spatial_campaign import (
    SpatialCampaignScenario,
    parse_spatial_campaign_scenario_json,
)
from adlife.core.domain.spatial_response import (
    SpatialResponseInput,
    parse_spatial_response_input_json,
)
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    RunNotFound,
    StorageError,
    UnsafeRunLocation,
    validate_run_id,
)
from adlife.core.simulation.city_cognition import (
    build_city_cognition_requests,
    validate_city_cognition_evaluation,
)
from adlife.core.simulation.city_cognition_v2 import (
    build_city_cognition_input_v2,
    build_city_cognition_requests_v2,
    city_cognition_terminal_lines_v2,
)
from adlife.core.simulation.city_mobility import (
    CityAgent,
    CityMobility,
    CityPlaceAssignment,
)
from adlife.core.simulation.city_trace import summarize_city_trace
from adlife.core.simulation.spatial_attention import (
    MAX_SPATIAL_ATTENTION_STREAM_BYTES,
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
    spatial_attention_lines,
    summarize_spatial_attention_artifact,
)
from adlife.core.simulation.spatial_opportunity import (
    MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES,
    SpatialOpportunityEvaluation,
    evaluate_spatial_opportunities,
    spatial_opportunity_lines,
    summarize_spatial_opportunity_artifact,
)
from adlife.core.simulation.spatial_response import (
    MAX_SPATIAL_RESPONSE_STATE_BYTES,
    MAX_SPATIAL_RESPONSE_STREAM_BYTES,
    MAX_SPATIAL_RESPONSE_SUMMARY_BYTES,
    SpatialResponseEvaluation,
    evaluate_spatial_responses,
    spatial_response_lines,
    spatial_response_state_document,
    summarize_spatial_response_artifact,
)
from adlife.core.simulation.spatial_response_v2 import (
    MAX_SPATIAL_RESPONSE_STATE_BYTES_V2,
    MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2,
    MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2,
    SpatialResponseEvaluationV2,
    spatial_response_state_document_v2,
    spatial_response_v2_lines,
    summarize_spatial_response_artifact_v2,
)

if TYPE_CHECKING:
    from adlife.city.runs import PreparedCityRun
    from adlife.city.workbench_city_registry import _OwnedWindowsHandle

MAX_CITY_MANIFEST_BYTES = 65_536
MAX_CITY_AGENTS_BYTES = 65_536
MAX_CITY_PLACE_ASSIGNMENTS_BYTES = 65_536
MAX_CITY_OPPORTUNITY_SUMMARY_BYTES = 65_536
MAX_CITY_ATTENTION_SUMMARY_BYTES = 65_536
MAX_WORKBENCH_INPUT_BYTES = 131_072
MAX_CITY_COGNITION_INPUT_BYTES_V2 = 131_072
# A V10 document is read under the widest selection-policy bounds, then held to the
# bounds of its own stored version once parsed.
MAX_CITY_COGNITION_INPUT_BYTES_V10 = max(
    limits.max_cognition_input_bytes for limits in CITY_COGNITION_SELECTION_LIMITS.values()
)
MAX_CITY_COGNITION_STREAM_BYTES_V10 = max(
    limits.max_terminal_stream_bytes for limits in CITY_COGNITION_SELECTION_LIMITS.values()
)
_IO_CHUNK_BYTES = 65_536
_V10_INPUT_NAMES = frozenset(
    {
        "agents.json",
        "city.json",
        "cognition.json",
        "spatial-campaign.json",
        "spatial-response.json",
        "workbench.json",
    }
)
_V10_OUTPUT_NAMES = frozenset(
    {
        "attention-summary.json",
        "cognition-summary.json",
        "cognition.jsonl",
        "opportunity-summary.json",
        "response-state.json",
        "response-summary.json",
        "spatial-attention.jsonl",
        "spatial-opportunities.jsonl",
        "spatial-responses.jsonl",
    }
)


@dataclass(slots=True)
class _DirectoryPin:
    owner: _OwnedWindowsHandle
    references: int


@dataclass(frozen=True, slots=True)
class _HeldArtifact:
    path: Path
    source: BinaryIO
    status: os.stat_result
    named_status: os.stat_result
    parents: tuple[tuple[Path, tuple[int, int]], ...]


_WINDOWS_PINS: dict[Path, _DirectoryPin] = {}
_PIN_LOCK = RLock()
# ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION. The workbench city registry
# anchors the workspace root with DELETE access for short critical sections and
# waits out read leases on the same bound, so a read lease waits out that anchor.
_WINDOWS_SHARING_ERRORS = frozenset({32, 33})
_WINDOWS_PIN_RETRY_SECONDS = 10.0
_WINDOWS_PIN_RETRY_INTERVAL_SECONDS = 0.01


def _acquire_windows_pin(path: Path) -> None:
    """Take one shared directory read lease, waiting out brief exclusive anchors."""
    if sys.platform == "win32":
        # Reuse the registry's native handle owner: no FILE_SHARE_DELETE keeps each
        # Windows directory entry pinned for the operation.
        from adlife.city.workbench_city_registry import _windows_create_file

        deadline = time.monotonic() + _WINDOWS_PIN_RETRY_SECONDS
        while True:
            with _PIN_LOCK:
                pin = _WINDOWS_PINS.get(path)
                if pin is not None:
                    pin.references += 1
                    return
                try:
                    # LIST_DIRECTORY participates in Windows sharing checks;
                    # READ_ATTRIBUTES alone does not deny rename. Read leases
                    # remain mutually compatible while excluding DELETE.
                    owner = _windows_create_file(
                        path,
                        desired_access=0x0001 | 0x0080,
                        share_mode=0x00000003,
                        creation_disposition=3,
                        flags=0x02000000,
                    )
                except OSError as error:
                    if error.errno not in _WINDOWS_SHARING_ERRORS or time.monotonic() >= deadline:
                        raise
                else:
                    _WINDOWS_PINS[path] = _DirectoryPin(owner, 1)
                    return
            # Retry outside the lock so other leases can still be released meanwhile.
            time.sleep(_WINDOWS_PIN_RETRY_INTERVAL_SECONDS)
    else:  # pragma: no cover - platform guard, unreachable off Windows
        raise OSError("Windows directory pins require a Windows interpreter")


def _release_windows_pin(path: Path) -> None:
    with _PIN_LOCK:
        pin = _WINDOWS_PINS[path]
        pin.references -= 1
        if not pin.references:
            del _WINDOWS_PINS[path]
            pin.owner.close()


@dataclass(frozen=True, slots=True)
class StoredCityRun:
    manifest: CityRunManifestDocument
    pack: CityPackDocument
    mobility: CityMobility
    directory: Path
    spatial_scenario: SpatialCampaignScenario | None = None
    opportunity_evaluation: SpatialOpportunityEvaluation | None = None
    attention_evaluation: SpatialAttentionEvaluation | None = None
    response_input: SpatialResponseInput | None = None
    response_evaluation: SpatialResponseEvaluation | SpatialResponseEvaluationV2 | None = None
    workbench_input: WorkbenchRunInputDocument | None = None
    cognition_input: CityCognitionInputV1 | CityCognitionInputV2 | None = None
    cognition_evaluation: CityCognitionEvaluationV1 | CityCognitionEvaluationV2 | None = None


def _runtime_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _canonical_bytes(value: BaseModel | Mapping[str, object]) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _cognition_input_limit_v10(cognition_input: CityCognitionInputV2) -> int:
    """The canonical input file size, newline included, its stored version allows."""
    return cognition_input.selection_limits.max_cognition_input_bytes + 1


def _identity(status: os.stat_result) -> tuple[int, int]:
    return status.st_dev, status.st_ino


def _safe_status(status: os.stat_result, *, directory: bool, published_stage: bool = False) -> None:
    if stat.S_ISLNK(status.st_mode) or getattr(status, "st_file_attributes", 0) & 0x400:
        raise UnsafeRunLocation("city run location contains a link or reparse point")
    if directory:
        valid = stat.S_ISDIR(status.st_mode)
    else:
        valid = stat.S_ISREG(status.st_mode) and status.st_nlink in (
            (1, 2) if published_stage else (1,)
        )
    if not valid:
        raise UnsafeRunLocation("city run location has unsafe filesystem metadata")


def _directory_chain(path: Path) -> tuple[Path, ...]:
    return (*reversed(path.parents), path)


def _inspect_directories(
    path: Path, *, missing_ok: bool = False
) -> tuple[tuple[Path, tuple[int, int]], ...]:
    observed = []
    for parent in _directory_chain(path):
        try:
            status = os.lstat(parent)
        except FileNotFoundError:
            if missing_ok:
                continue
            raise UnsafeRunLocation("city run parent directory is unavailable") from None
        except OSError:
            raise UnsafeRunLocation(
                "city run parent directory cannot be inspected safely"
            ) from None
        _safe_status(status, directory=True)
        observed.append((parent, _identity(status)))
    return tuple(observed)


@contextmanager
def _hold_parents(path: Path) -> Iterator[int | None]:
    """Pin original directory components and recheck their names around every I/O."""
    observed = _inspect_directories(path)
    with ExitStack() as stack:
        descriptor = None
        for parent, identity in observed:
            if sys.platform == "win32":
                _acquire_windows_pin(parent)
                stack.callback(_release_windows_pin, parent)
            else:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(parent, flags)
                stack.callback(os.close, descriptor)
                if _identity(os.fstat(descriptor)) != identity:
                    raise UnsafeRunLocation("city run directory identity changed")
        if _inspect_directories(path) != observed:
            raise UnsafeRunLocation("city run parent directory changed")
        yield descriptor
        if _inspect_directories(path) != observed:
            raise UnsafeRunLocation("city run parent directory changed")


@contextmanager
def _open_artifact(
    path: Path, mode: str, *, published_stage: bool = False, anonymous: bool = False
) -> Iterator[BinaryIO]:
    with _hold_parents(path.parent) as parent_descriptor, ExitStack() as guards:
        if anonymous:
            if sys.platform == "win32":
                from adlife.city.workbench_city_registry import _open_windows_owned_file

                owner = _open_windows_owned_file(
                    path,
                    flags=os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0)
                    | 0x0040
                    | 0x0080,
                    share_mode=0x0020,
                    mode=0o600,
                )
                guards.callback(owner.close)
                stage = cast(BinaryIO, os.fdopen(owner.fileno(), "w+b", closefd=False))
            elif sys.platform.startswith("linux"):
                flag = getattr(os, "O_TMPFILE", 0)
                if not flag:
                    raise StorageError("atomic manifest staging is unavailable on this filesystem")
                try:
                    descriptor = os.open(path.parent, os.O_RDWR | flag, 0o600)
                except OSError as error:
                    if error.errno in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EISDIR}:
                        raise StorageError(
                            "atomic manifest staging is unavailable on this filesystem"
                        ) from None
                    raise
                stage = cast(BinaryIO, os.fdopen(descriptor, "w+b"))
            else:
                from tempfile import TemporaryFile

                stage = cast(
                    BinaryIO, guards.enter_context(TemporaryFile(mode="w+b", dir=path.parent))
                )
            with stage:
                if not stat.S_ISREG(os.fstat(stage.fileno()).st_mode):
                    raise UnsafeRunLocation("city run manifest stage is not a regular file")
                yield stage
            return
        before = None if "x" in mode else os.lstat(path)
        if before is not None:
            _safe_status(before, directory=False)
        if parent_descriptor is None:
            if mode == "rb" and sys.platform == "win32":
                from adlife.city.workbench_city_registry import _open_windows_owned_file

                read_guard = _open_windows_owned_file(
                    path,
                    flags=os.O_RDONLY | getattr(os, "O_BINARY", 0) | 0x0080,
                    share_mode=0x0020,
                )
                guards.callback(read_guard.close)
                if before is None or _identity(os.fstat(read_guard.fileno())) != _identity(before):
                    raise UnsafeRunLocation("city run pinned read identity changed")
            source = cast(BinaryIO, path.open(mode))
        else:
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL) if "x" in mode else os.O_RDONLY
            descriptor = os.open(
                path.name, flags | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_descriptor
            )
            source = cast(BinaryIO, os.fdopen(descriptor, mode))
        with source:
            opened = os.fstat(source.fileno())
            _safe_status(opened, directory=False)
            named = os.lstat(path)
            _safe_status(named, directory=False)
            if _identity(opened) != _identity(named) or (
                before is not None and _identity(before) != _identity(opened)
            ):
                raise UnsafeRunLocation("city run file identity changed while opening")
            yield source
            current = os.lstat(path)
            _safe_status(current, directory=False, published_stage=published_stage)
            after = os.fstat(source.fileno())
            _safe_status(after, directory=False, published_stage=published_stage)
            if _identity(current) != _identity(opened) or _identity(after) != _identity(opened):
                raise UnsafeRunLocation("city run file identity changed during I/O")
            if mode == "rb":
                # A deliberately published stage gains a link and therefore a
                # new POSIX ctime; ordinary artifact pins never allow that case.
                changed = (opened.st_size, opened.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                )
                deliberate_link = (
                    published_stage
                    and opened.st_nlink == 1
                    and after.st_nlink == current.st_nlink == 2
                )
                if changed or (not deliberate_link and opened.st_ctime_ns != after.st_ctime_ns):
                    raise UnsafeRunLocation("city run file bytes changed during I/O")


def _read_exact(source: BinaryIO, count: int) -> bytes:
    parts = []
    remaining = count
    while remaining:
        part = source.read(min(remaining, _IO_CHUNK_BYTES))
        if not part:
            break
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


def _write_lines(path: Path, lines: Iterator[bytes], *, limit: int) -> None:
    size = 0
    with _open_artifact(path, "xb") as target:
        for line in lines:
            size += len(line)
            if size > limit:
                raise CorruptRunArtifact("city run stream exceeds its size limit")
            if target.write(line) != len(line):
                raise OSError("short city run stream write")
        target.flush()
        os.fsync(target.fileno())


def _verify_lines(path: Path, lines: Iterator[bytes], *, size: int, limit: int) -> None:
    if size > limit:
        raise CorruptRunArtifact("city run stream exceeds its size limit")
    try:
        _inspect_directories(path.parent)
        _safe_status(os.lstat(path), directory=False)
        if path.stat().st_size != size:
            raise CorruptRunArtifact("city run stream size does not match")
        with _open_artifact(path, "rb") as source:
            total = 0
            for line in lines:
                total += len(line)
                if total > limit or _read_exact(source, len(line)) != line:
                    raise CorruptRunArtifact("city run stream does not match frozen inputs")
            if total != size or source.read(1):
                raise CorruptRunArtifact("city run stream does not match frozen inputs")
    except OSError:
        raise CorruptRunArtifact("city run stream is missing or unreadable") from None


def _sync_directory(path: Path) -> None:
    if sys.platform == "win32":
        # Windows has no portable directory-fsync operation. Each file is
        # flushed through its handle before the native exclusive commit.
        return
    with _hold_parents(path) as descriptor:
        if descriptor is not None:
            try:
                os.fsync(descriptor)
            except OSError as error:
                if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
                    raise


def _verify_sidecar_names(parent: Path, allowed: frozenset[str]) -> None:
    seen: set[str] = set()
    with os.scandir(parent) as entries:
        for child in entries:
            if child.name not in allowed or child.name in seen:
                raise CorruptRunArtifact("city run V10 sidecar set is incomplete or undeclared")
            seen.add(child.name)
    if seen != allowed:
        raise CorruptRunArtifact("city run V10 sidecar set is incomplete or undeclared")


def _read_manifest_stage(source: BinaryIO, limit: int) -> bytes:
    source.seek(0)
    return _read_exact(source, limit + 1)


def _write_manifest_stage(source: BinaryIO, data: bytes) -> None:
    if source.write(data) != len(data):
        raise OSError("short city manifest stage write")
    source.flush()
    os.fsync(source.fileno())


def _publish_manifest(
    directory: Path,
    data: bytes,
    store: CityRunStore,
    *,
    verify_evidence: Callable[[], None] | None = None,
) -> None:
    """Publish one verified descriptor; cleanup never unlinks a mutable stage name."""
    temporary = directory / ".run.json.tmp"
    with _hold_parents(directory) as parent_descriptor:
        with _open_artifact(temporary, "xb", anonymous=True) as source:
            stage_identity = _identity(os.fstat(source.fileno()))
            _write_manifest_stage(source, data)
            if _read_manifest_stage(source, len(data)) != data:
                raise StorageError("city run staged manifest differs from expected bytes")
            for path in (directory / "inputs", directory / "outputs", directory):
                if path.is_dir():
                    _sync_directory(path)
            _sync_directory(directory.parent)
            if verify_evidence is not None:
                verify_evidence()
            try:
                if sys.platform == "win32":
                    from adlife.city.workbench_city_registry import (
                        _HeldDirectory,
                        _link_windows_descriptor,
                    )
                    from adlife.city.workbench_workspace import (
                        WorkbenchSubdirectory,
                        WorkbenchWorkspace,
                    )

                    status = os.lstat(directory)
                    workspace = WorkbenchWorkspace(directory.parent, None, None)
                    pinned = WorkbenchSubdirectory(
                        workspace,
                        directory.name,
                        directory,
                        status.st_dev,
                        status.st_ino,
                    )
                    with _PIN_LOCK:
                        directory_handle = _WINDOWS_PINS[directory].owner.handle()
                    _link_windows_descriptor(
                        source.fileno(),
                        _HeldDirectory(pinned, None, directory_handle),
                        "run.json",
                    )
                elif sys.platform.startswith("linux"):
                    os.link(
                        f"/proc/self/fd/{source.fileno()}",
                        "run.json",
                        dst_dir_fd=parent_descriptor,
                        follow_symlinks=True,
                    )
                elif sys.platform == "darwin":
                    from adlife.city.workbench_city_registry import _clone_stage_on_darwin

                    if parent_descriptor is None:
                        raise StorageError("city run exclusive publication is unavailable")
                    _clone_stage_on_darwin(source.fileno(), parent_descriptor, "run.json")
                else:
                    raise StorageError("city run exclusive publication is unavailable")
            except FileExistsError:
                raise DuplicateRun("city run completion manifest already exists") from None
        # Closing a native delete-on-close stage removes only that owned name;
        # POSIX staging was anonymous from the start. A possible commit survives.
        published = os.lstat(directory / "run.json")
        _safe_status(published, directory=False)
        if sys.platform != "darwin" and _identity(published) != stage_identity:
            raise UnsafeRunLocation("published city manifest identity changed")
        if (
            store._read_document(
                directory / "run.json",
                limit=len(data),
                label="published manifest",
            )
            != data
        ):
            raise StorageError("published city manifest differs from verified bytes")
        if _identity(os.lstat(directory / "run.json")) != _identity(published):
            raise UnsafeRunLocation("published city manifest identity changed")
        _sync_directory(directory)


def _document_sha256(value: Mapping[str, object]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_schema_pair(
    manifest: CityRunManifestDocument,
    pack: CityPackDocument,
    places: CityPlaceSet | None,
) -> None:
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
    with _open_artifact(path, "xb") as target:
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


def _write_response_stream(
    path: Path, evaluation: SpatialResponseEvaluation | SpatialResponseEvaluationV2
) -> None:
    if isinstance(evaluation, SpatialResponseEvaluationV2):
        _write_lines(
            path, spatial_response_v2_lines(evaluation), limit=MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2
        )
        return
    with path.open("xb") as target:
        for line in spatial_response_lines(evaluation):
            if target.write(line) != len(line):
                raise OSError("short city response stream write")
        target.flush()
        os.fsync(target.fileno())


def _write_cognition_stream(
    path: Path, evaluation: CityCognitionEvaluationV1 | CityCognitionEvaluationV2
) -> None:
    if isinstance(evaluation, CityCognitionEvaluationV2):
        _write_lines(
            path,
            city_cognition_terminal_lines_v2(evaluation),
            limit=evaluation.summary.selection_limits.max_terminal_stream_bytes,
        )
        return
    with path.open("xb") as target:
        for record in evaluation.records:
            line = (canonical_json(record) + "\n").encode("utf-8")
            if target.write(line) != len(line):
                raise OSError("short city cognition stream write")
        target.flush()
        os.fsync(target.fileno())


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("city cognition JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("city cognition JSON contains a non-finite constant")


def _inspect_cognition_json(document: bytes) -> None:
    try:
        text = document.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=lambda token: token,
            parse_int=lambda token: token,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ValueError("city cognition artifact is not strict UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise ValueError("city cognition artifact must be a JSON object")


def _parse_cognition_input(document: bytes) -> CityCognitionInputV1:
    _inspect_cognition_json(document)
    return CityCognitionInputV1.model_validate_json(document)


def _parse_cognition_summary(document: bytes) -> CityCognitionSummaryV1:
    _inspect_cognition_json(document)
    return CityCognitionSummaryV1.model_validate_json(document)


def _parse_cognition_stream(document: bytes) -> tuple[CityCognitionTerminalRecordV1, ...]:
    if len(document) > MAX_CITY_COGNITION_STREAM_BYTES:
        raise ValueError("city cognition terminal stream exceeds its size limit")
    if not document:
        return ()
    lines = document.splitlines(keepends=True)
    if len(lines) > MAX_CITY_COGNITION_REQUESTS:
        raise ValueError("city cognition terminal stream has too many records")
    records: list[CityCognitionTerminalRecordV1] = []
    for line in lines:
        if not line.endswith(b"\n") or line.endswith(b"\r\n"):
            raise ValueError("city cognition terminal stream is not canonical JSONL")
        payload = line[:-1]
        if not payload or len(payload) > MAX_CITY_COGNITION_TERMINAL_RECORD_BYTES:
            raise ValueError("city cognition terminal record exceeds its size limit")
        _inspect_cognition_json(payload)
        record = CityCognitionTerminalRecordV1.model_validate_json(payload)
        if line != (canonical_json(record) + "\n").encode("utf-8"):
            raise ValueError("city cognition terminal record is not canonical")
        records.append(record)
    return tuple(records)


def _validate_cognition_workbench_binding(
    cognition_input: CityCognitionInputV1,
    workbench_input: WorkbenchRunInputV3,
) -> None:
    cognition = workbench_input.draft.cognition
    profile = cognition_input.provider_profile
    if (
        cognition_input.run_input_sha256 != workbench_input.fingerprint
        or cognition_input.mode != cognition.mode
        or cognition_input.aggregate_output_token_ceiling
        != cognition.aggregate_output_token_ceiling
        or cognition_input.prompt_contract_version != cognition.prompt_contract_version
        or cognition_input.result_schema_version != cognition.result_schema_version
        or cognition_input.selection_policy_version != cognition.selection_policy_version
        or cognition_input.fallback_policy_version != cognition.fallback_policy_version
        or cognition_input.selected_requests_per_agent != cognition.selected_requests_per_agent
        or cognition_input.selected_requests_per_run != cognition.selected_requests_per_run
        or cognition_input.concurrency_limit != cognition.concurrency_limit
        or cognition_input.max_request_bytes != cognition.max_request_bytes
        or cognition_input.max_wire_response_bytes != cognition.max_wire_response_bytes
        or cognition_input.max_output_tokens_per_attempt != cognition.max_output_tokens_per_attempt
        or cognition_input.reservation_tokens_per_request
        != cognition.reservation_tokens_per_request
        or cognition_input.initial_attempts != cognition.initial_attempts
        or cognition_input.transient_retries_ceiling != cognition.transient_retries
        or cognition_input.repair_attempts_ceiling != cognition.repair_attempts
        or cognition_input.attempt_timeout_seconds_ceiling != cognition.attempt_timeout_seconds
        or cognition_input.aggregate_deadline_seconds != cognition.aggregate_deadline_seconds
        or (None if profile is None else profile.profile_id) != cognition.provider_profile_id
        or (None if profile is None else profile.revision_sha256)
        != cognition.provider_revision_sha256
    ):
        raise ValueError("city cognition input does not match schema-3 workbench input")


class CityRunStore:
    """A city run is complete only when its final manifest exists and verifies."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root))
        _inspect_directories(self.root, missing_ok=True)

    def _runs_directory(self, *, create: bool = False) -> Path:
        _inspect_directories(self.root, missing_ok=True)
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
        _inspect_directories(runs, missing_ok=True)
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
        try:
            status = os.lstat(directory)
        except FileNotFoundError:
            pass
        except OSError:
            raise UnsafeRunLocation("city run directory cannot be inspected safely") from None
        else:
            _safe_status(status, directory=True)
        return directory

    def _reconstruct_v10(
        self,
        manifest: CityRunManifestV10,
        pack: CityPackV2,
        scenario: SpatialCampaignScenario,
        response_input: SpatialResponseInput,
        workbench_input: WorkbenchRunInputV4,
        cognition_input: CityCognitionInputV2,
        evaluation: CityCognitionEvaluationV2,
        *,
        historical_metadata: bool = False,
    ) -> PreparedCityRun:
        """Rebuild every derivable input, allocation, state, and receipt offline."""
        from adlife.city.runs import PreparedCityScientificRunV2, finalize_city_run_v2

        checked_workbench = validate_workbench_execution_binding(
            workbench_input,
            pack=pack,
            run_id=manifest.run_id,
            seed=manifest.seed,
            agent_count=manifest.agent_count,
            days=manifest.days,
            places=None,
            spatial_scenario=scenario,
            spatial_response=response_input,
        )
        if not isinstance(checked_workbench, WorkbenchRunInputV4):
            raise CorruptRunArtifact("V10 requires schema-4 workbench evidence")
        mobility = CityMobility(
            pack, seed=manifest.seed, agent_count=manifest.agent_count, days=manifest.days
        )
        opportunities = evaluate_spatial_opportunities(mobility, scenario)
        attention = evaluate_spatial_attention(opportunities, seed=manifest.seed)
        baseline = evaluate_spatial_responses(
            response_input,
            scenario,
            opportunities,
            attention,
            agent_ids=tuple(agent.agent_id for agent in mobility.agents),
        )
        context = build_city_cognition_run_context_v2(
            checked_workbench,
            pack=pack,
            spatial_scenario=scenario,
            response_input=response_input,
            baseline_evaluation=baseline,
        )
        settings = checked_workbench.draft.cognition
        profile = cognition_input.provider_profile
        if (
            settings.causal_contract != cognition_input.causal_contract
            or settings.provider_profile_id != (None if profile is None else profile.profile_id)
            or settings.provider_revision_sha256
            != (None if profile is None else profile.revision_sha256)
        ):
            raise CorruptRunArtifact("city cognition input does not match workbench authority")
        requests = build_city_cognition_requests_v2(
            context,
            mobility,
            baseline,
            causal_contract=cognition_input.causal_contract,
            funded_response_limit=settings.funded_response_limit,
        )
        rebuilt_input = build_city_cognition_input_v2(
            context,
            requests,
            mode=settings.mode,
            funded_response_limit=settings.funded_response_limit,
            provider_profile=profile,
            causal_contract=cognition_input.causal_contract,
        )
        if rebuilt_input != cognition_input:
            raise CorruptRunArtifact("city cognition input does not match reconstructed requests")
        staged = PreparedCityScientificRunV2(
            pack=pack,
            mobility=mobility,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=attention,
            response_input=response_input,
            baseline_evaluation=baseline,
            workbench_input=checked_workbench,
            cognition_input=rebuilt_input,
            cognition_requests=requests,
        )
        rebuilt = finalize_city_run_v2(
            staged, cognition_input=rebuilt_input, cognition_evaluation=evaluation
        )
        if historical_metadata:
            rebuilt = replace(
                rebuilt,
                manifest=rebuilt.manifest.model_copy(
                    update={
                        "package_version": manifest.package_version,
                        "python_version": manifest.python_version,
                    }
                ),
            )
        if rebuilt.manifest != manifest:
            raise CorruptRunArtifact("city run manifest does not match reconstructed V10 evidence")
        return rebuilt

    @staticmethod
    def _v10_documents(prepared: PreparedCityRun) -> dict[str, tuple[bytes, int]]:
        response = prepared.response_evaluation
        cognition = prepared.cognition_evaluation
        if not isinstance(response, SpatialResponseEvaluationV2) or not isinstance(
            cognition, CityCognitionEvaluationV2
        ):
            raise CorruptRunArtifact("V10 requires V2 response and cognition evaluations")
        documents: dict[str, tuple[BaseModel | Mapping[str, object], int]] = {
            "inputs/city.json": (prepared.pack, MAX_CITY_PACK_BYTES),
            "inputs/agents.json": (
                {"agents": [asdict(agent) for agent in prepared.mobility.agents]},
                MAX_CITY_AGENTS_BYTES,
            ),
            "outputs/response-state.json": (
                spatial_response_state_document_v2(response),
                MAX_SPATIAL_RESPONSE_STATE_BYTES_V2,
            ),
            "outputs/response-summary.json": (
                summarize_spatial_response_artifact_v2(response),
                MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2,
            ),
            "outputs/cognition-summary.json": (
                cognition.summary,
                MAX_CITY_COGNITION_SUMMARY_BYTES + 1,
            ),
            "run.json": (prepared.manifest, MAX_CITY_MANIFEST_BYTES),
        }
        required = (
            ("inputs/spatial-campaign.json", prepared.spatial_scenario, MAX_SPATIAL_CAMPAIGN_BYTES),
            (
                "inputs/spatial-response.json",
                prepared.response_input,
                MAX_SPATIAL_RESPONSE_INPUT_BYTES,
            ),
            ("inputs/workbench.json", prepared.workbench_input, MAX_WORKBENCH_INPUT_BYTES + 1),
            (
                "inputs/cognition.json",
                prepared.cognition_input,
                _cognition_input_limit_v10(prepared.cognition_input)
                if isinstance(prepared.cognition_input, CityCognitionInputV2)
                else MAX_CITY_COGNITION_INPUT_BYTES_V10 + 1,
            ),
        )
        for name, model, limit in required:
            if model is None:
                raise CorruptRunArtifact("V10 document evidence is incomplete")
            documents[name] = model, limit
        if prepared.opportunity_evaluation is None or prepared.attention_evaluation is None:
            raise CorruptRunArtifact("V10 spatial evidence is incomplete")
        documents["outputs/opportunity-summary.json"] = (
            summarize_spatial_opportunity_artifact(prepared.opportunity_evaluation),
            MAX_CITY_OPPORTUNITY_SUMMARY_BYTES,
        )
        documents["outputs/attention-summary.json"] = (
            summarize_spatial_attention_artifact(prepared.attention_evaluation),
            MAX_CITY_ATTENTION_SUMMARY_BYTES,
        )
        encoded = {
            name: (_canonical_bytes(model), limit) for name, (model, limit) in documents.items()
        }
        if any(len(data) > limit for data, limit in encoded.values()):
            raise CorruptRunArtifact("city run document exceeds its size limit")
        return encoded

    def _save_v10(
        self,
        manifest: CityRunManifestV10,
        pack: CityPackDocument,
        agents: tuple[CityAgent, ...],
        *,
        places: CityPlaceSet | None,
        place_assignments: tuple[CityPlaceAssignment, ...],
        spatial_scenario: SpatialCampaignScenario | None,
        opportunity_evaluation: SpatialOpportunityEvaluation | None,
        attention_evaluation: SpatialAttentionEvaluation | None,
        response_input: SpatialResponseInput | None,
        response_evaluation: SpatialResponseEvaluation | SpatialResponseEvaluationV2 | None,
        workbench_input: WorkbenchRunInputDocument | None,
        cognition_input: CityCognitionInputV1 | CityCognitionInputV2 | None,
        cognition_evaluation: CityCognitionEvaluationV1 | CityCognitionEvaluationV2 | None,
    ) -> Path:
        try:
            if (
                type(pack) is not CityPackV2
                or type(workbench_input) is not WorkbenchRunInputV4
                or type(cognition_input) is not CityCognitionInputV2
                or type(cognition_evaluation) is not CityCognitionEvaluationV2
                or type(response_evaluation) is not SpatialResponseEvaluationV2
                or spatial_scenario is None
                or response_input is None
                or places is not None
                or place_assignments
            ):
                raise CorruptRunArtifact("V10 city run inputs are incomplete or incompatible")
            checked_pack = CityPackV2.model_validate_json(canonical_json(pack))
            checked_workbench = WorkbenchRunInputV4.model_validate_json(
                canonical_json(workbench_input)
            )
            checked_input = CityCognitionInputV2.model_validate_json(
                canonical_json(cognition_input)
            )
            checked_evaluation = CityCognitionEvaluationV2.model_validate_json(
                canonical_json(cognition_evaluation)
            )
            checked_response = SpatialResponseEvaluationV2.model_validate(response_evaluation)
            rebuilt = self._reconstruct_v10(
                manifest,
                checked_pack,
                parse_spatial_campaign_scenario_json(canonical_json(spatial_scenario)),
                parse_spatial_response_input_json(canonical_json(response_input)),
                checked_workbench,
                checked_input,
                checked_evaluation,
            )
            if (
                rebuilt.mobility.agents != agents
                or rebuilt.opportunity_evaluation != opportunity_evaluation
                or rebuilt.attention_evaluation != attention_evaluation
                or rebuilt.response_evaluation != checked_response
            ):
                raise CorruptRunArtifact("V10 supplied evidence does not match frozen inputs")
            documents = self._v10_documents(rebuilt)
        except (ValueError, DocumentNotSerialisable):
            raise CorruptRunArtifact("city run V10 inputs failed validation") from None
        directory = self.run_directory(manifest.run_id, create_root=True)
        try:
            directory.mkdir()
        except FileExistsError:
            raise DuplicateRun("city run identifier already exists") from None
        except OSError:
            raise StorageError("city run directory could not be created") from None
        pins = ExitStack()
        held_artifacts: list[_HeldArtifact] = []

        def hold_artifact(path: Path) -> None:
            source = pins.enter_context(_open_artifact(path, "rb"))
            held_artifacts.append(
                _HeldArtifact(
                    path,
                    source,
                    os.fstat(source.fileno()),
                    os.lstat(path),
                    _inspect_directories(path.parent),
                )
            )

        def verify_held_evidence() -> None:
            for artifact in held_artifacts:
                if _inspect_directories(artifact.path.parent) != artifact.parents:
                    raise UnsafeRunLocation("city run sidecar parent changed before publication")
                named = os.lstat(artifact.path)
                opened = os.fstat(artifact.source.fileno())
                _safe_status(named, directory=False)
                _safe_status(opened, directory=False)
                for status, expected in (
                    (named, artifact.named_status),
                    (opened, artifact.status),
                ):
                    if (
                        _identity(status),
                        status.st_size,
                        status.st_mtime_ns,
                        status.st_ctime_ns,
                    ) != (
                        _identity(expected),
                        expected.st_size,
                        expected.st_mtime_ns,
                        expected.st_ctime_ns,
                    ):
                        raise UnsafeRunLocation(
                            "city run verified sidecar changed before publication"
                        )
            for name, allowed in (("inputs", _V10_INPUT_NAMES), ("outputs", _V10_OUTPUT_NAMES)):
                _verify_sidecar_names(directory / name, allowed)

        try:
            (directory / "inputs").mkdir()
            (directory / "outputs").mkdir()
            for relative, (data, limit) in documents.items():
                if relative == "run.json":
                    continue
                path = directory / relative
                _write_new(path, data)
                hold_artifact(path)
                if self._read_document(path, limit=limit, label="staged V10 document") != data:
                    raise StorageError("city run staged V10 document differs from expected bytes")
            assert rebuilt.opportunity_evaluation is not None
            assert rebuilt.attention_evaluation is not None
            assert isinstance(rebuilt.response_evaluation, SpatialResponseEvaluationV2)
            assert isinstance(rebuilt.cognition_evaluation, CityCognitionEvaluationV2)
            streams = (
                (
                    "spatial-opportunities.jsonl",
                    spatial_opportunity_lines(rebuilt.opportunity_evaluation),
                    manifest.opportunity_stream_bytes,
                    MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES,
                ),
                (
                    "spatial-attention.jsonl",
                    spatial_attention_lines(rebuilt.attention_evaluation),
                    manifest.attention_stream_bytes,
                    MAX_SPATIAL_ATTENTION_STREAM_BYTES,
                ),
            )
            for name, lines, size, limit in streams:
                path = directory / "outputs" / name
                _write_lines(path, lines, limit=limit)
                hold_artifact(path)
                expected = (
                    spatial_opportunity_lines(rebuilt.opportunity_evaluation)
                    if name == "spatial-opportunities.jsonl"
                    else spatial_attention_lines(rebuilt.attention_evaluation)
                )
                _verify_lines(path, expected, size=size, limit=limit)
            response_path = directory / "outputs/spatial-responses.jsonl"
            _write_response_stream(response_path, rebuilt.response_evaluation)
            hold_artifact(response_path)
            self._verify_response_stream(response_path, rebuilt.response_evaluation)
            cognition_path = directory / "outputs/cognition.jsonl"
            _write_cognition_stream(cognition_path, rebuilt.cognition_evaluation)
            hold_artifact(cognition_path)
            cognition_limits = rebuilt.cognition_evaluation.summary.selection_limits
            _verify_lines(
                cognition_path,
                city_cognition_terminal_lines_v2(rebuilt.cognition_evaluation),
                size=manifest.cognition.terminal_stream_bytes,
                limit=cognition_limits.max_terminal_stream_bytes,
            )
            _publish_manifest(
                directory, documents["run.json"][0], self, verify_evidence=verify_held_evidence
            )
        except OSError:
            raise StorageError("city run could not be completed") from None
        except CorruptRunArtifact:
            raise StorageError("city run staged V10 evidence failed verification") from None
        finally:
            primary_failure = exc_info()[0] is not None
            try:
                pins.close()
            except (OSError, StorageError) as error:
                if not primary_failure:
                    if isinstance(error, StorageError):
                        raise
                    raise StorageError("city run verified sidecars became unavailable") from None
        return directory

    def _load_v10(
        self, directory: Path, manifest: CityRunManifestV10, manifest_bytes: bytes, run_id: str
    ) -> StoredCityRun:
        pins = ExitStack()
        try:
            if manifest.run_id != run_id:
                raise CorruptRunArtifact("city run identifier does not match its manifest")
            for name, allowed in (("inputs", _V10_INPUT_NAMES), ("outputs", _V10_OUTPUT_NAMES)):
                parent = directory / name
                _inspect_directories(parent)
                _verify_sidecar_names(parent, allowed)
                for child in (parent / child_name for child_name in sorted(allowed)):
                    _safe_status(os.lstat(child), directory=False)
                    pins.enter_context(_open_artifact(child, "rb"))
            pins.enter_context(_open_artifact(directory / "run.json", "rb"))
            if (
                self._read_document(
                    directory / "run.json", limit=MAX_CITY_MANIFEST_BYTES, label="pinned manifest"
                )
                != manifest_bytes
            ):
                raise CorruptRunArtifact("city run manifest changed while pinning sidecars")
            limits = {
                "inputs/city.json": MAX_CITY_PACK_BYTES,
                "inputs/agents.json": MAX_CITY_AGENTS_BYTES,
                "inputs/workbench.json": MAX_WORKBENCH_INPUT_BYTES + 1,
                "inputs/spatial-campaign.json": MAX_SPATIAL_CAMPAIGN_BYTES,
                "inputs/spatial-response.json": MAX_SPATIAL_RESPONSE_INPUT_BYTES,
                "inputs/cognition.json": MAX_CITY_COGNITION_INPUT_BYTES_V10 + 1,
                "outputs/cognition-summary.json": MAX_CITY_COGNITION_SUMMARY_BYTES + 1,
                "outputs/response-state.json": MAX_SPATIAL_RESPONSE_STATE_BYTES_V2,
                "outputs/response-summary.json": MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2,
                "outputs/opportunity-summary.json": MAX_CITY_OPPORTUNITY_SUMMARY_BYTES,
                "outputs/attention-summary.json": MAX_CITY_ATTENTION_SUMMARY_BYTES,
            }
            documents = {
                name: self._read_document(directory / name, limit=limit, label="V10 document")
                for name, limit in limits.items()
            }
            for data in documents.values():
                _inspect_cognition_json(data)
            pack = CityPackV2.model_validate_json(documents["inputs/city.json"])
            workbench = WorkbenchRunInputV4.model_validate_json(documents["inputs/workbench.json"])
            scenario = parse_spatial_campaign_scenario_json(
                documents["inputs/spatial-campaign.json"]
            )
            response_input = parse_spatial_response_input_json(
                documents["inputs/spatial-response.json"]
            )
            frozen = CityCognitionInputV2.model_validate_json(documents["inputs/cognition.json"])
            if len(documents["inputs/cognition.json"]) > _cognition_input_limit_v10(frozen):
                raise CorruptRunArtifact("city run cognition input exceeds its version limit")
            summary = CityCognitionSummaryV2.model_validate_json(
                documents["outputs/cognition-summary.json"]
            )
            # The stored input's own selection-policy version bounds its terminal stream.
            selection_limits = frozen.selection_limits
            stream = self._read_document(
                directory / "outputs/cognition.jsonl",
                limit=selection_limits.max_terminal_stream_bytes,
                label="V10 cognition stream",
            )
            records = []
            for index, line in enumerate(stream.splitlines(keepends=True)):
                if (
                    index >= selection_limits.max_terminal_records
                    or not line.endswith(b"\n")
                    or line.endswith(b"\r\n")
                    or len(line) - 1 > frozen.max_terminal_record_bytes
                ):
                    raise CorruptRunArtifact("city run terminal grammar is invalid")
                _inspect_cognition_json(line)
                record = CityCognitionTerminalRecordV2.model_validate_json(line)
                if record.selection_index != index or _canonical_bytes(record) != line:
                    raise CorruptRunArtifact(
                        "city run terminal order or canonical bytes are invalid"
                    )
                records.append(record)
            evaluation = CityCognitionEvaluationV2(
                schema_version=2,
                cognition_input_sha256=frozen.fingerprint,
                run_input_sha256=frozen.run_input_sha256,
                effect_scope=frozen.effect_scope,
                mode=frozen.mode,
                records=tuple(records),
                summary=summary,
            )
            rebuilt = self._reconstruct_v10(
                manifest,
                pack,
                scenario,
                response_input,
                workbench,
                frozen,
                evaluation,
                historical_metadata=True,
            )
            documents["run.json"] = manifest_bytes
            for name, (expected, _limit) in self._v10_documents(rebuilt).items():
                if documents[name] != expected:
                    raise CorruptRunArtifact(
                        "city run V10 document does not match reconstructed evidence"
                    )
            assert rebuilt.opportunity_evaluation is not None
            assert rebuilt.attention_evaluation is not None
            assert isinstance(rebuilt.response_evaluation, SpatialResponseEvaluationV2)
            _verify_lines(
                directory / "outputs/spatial-opportunities.jsonl",
                spatial_opportunity_lines(rebuilt.opportunity_evaluation),
                size=manifest.opportunity_stream_bytes,
                limit=MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES,
            )
            _verify_lines(
                directory / "outputs/spatial-attention.jsonl",
                spatial_attention_lines(rebuilt.attention_evaluation),
                size=manifest.attention_stream_bytes,
                limit=MAX_SPATIAL_ATTENTION_STREAM_BYTES,
            )
            self._verify_response_stream(
                directory / "outputs/spatial-responses.jsonl", rebuilt.response_evaluation
            )
            for name, allowed in (("inputs", _V10_INPUT_NAMES), ("outputs", _V10_OUTPUT_NAMES)):
                _verify_sidecar_names(directory / name, allowed)
            return StoredCityRun(
                manifest,
                pack,
                rebuilt.mobility,
                directory,
                spatial_scenario=scenario,
                opportunity_evaluation=rebuilt.opportunity_evaluation,
                attention_evaluation=rebuilt.attention_evaluation,
                response_input=response_input,
                response_evaluation=rebuilt.response_evaluation,
                workbench_input=workbench,
                cognition_input=frozen,
                cognition_evaluation=evaluation,
            )
        except (ValueError, DocumentNotSerialisable, OSError):
            raise CorruptRunArtifact("city run V10 artifact failed validation") from None
        finally:
            primary_failure = exc_info()[0] is not None
            try:
                pins.close()
            except (OSError, StorageError) as error:
                if not primary_failure:
                    if isinstance(error, StorageError):
                        raise
                    raise CorruptRunArtifact(
                        "city run verified sidecars became unavailable"
                    ) from None

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
        response_input: SpatialResponseInput | None = None,
        response_evaluation: SpatialResponseEvaluation | SpatialResponseEvaluationV2 | None = None,
        workbench_input: WorkbenchRunInputDocument | None = None,
        cognition_input: CityCognitionInputV1 | CityCognitionInputV2 | None = None,
        cognition_evaluation: CityCognitionEvaluationV1 | CityCognitionEvaluationV2 | None = None,
    ) -> Path:
        """Reserve a fresh ID; publish the completion manifest only after frozen inputs."""
        try:
            selected = parse_city_run_manifest_json(canonical_json(manifest))
        except (ValueError, DocumentNotSerialisable):
            raise CorruptRunArtifact("city run manifest failed validation") from None
        if isinstance(selected, CityRunManifestV10):
            return self._save_v10(
                selected,
                pack,
                agents,
                places=places,
                place_assignments=place_assignments,
                spatial_scenario=spatial_scenario,
                opportunity_evaluation=opportunity_evaluation,
                attention_evaluation=attention_evaluation,
                response_input=response_input,
                response_evaluation=response_evaluation,
                workbench_input=workbench_input,
                cognition_input=cognition_input,
                cognition_evaluation=cognition_evaluation,
            )
        try:
            manifest = parse_city_run_manifest_json(canonical_json(manifest))
            pack = parse_city_pack_json(canonical_json(pack))
            places = None if places is None else parse_city_place_set_json(canonical_json(places))
            parsed_workbench_input = (
                None
                if workbench_input is None
                else parse_workbench_run_input_json(canonical_json(workbench_input))
            )
            workbench_input = parsed_workbench_input
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
            response_input_bytes: bytes | None = None
            response_summary_bytes: bytes | None = None
            response_state_bytes: bytes | None = None
            workbench_input_bytes: bytes | None = None
            cognition_input_bytes: bytes | None = None
            cognition_summary_bytes: bytes | None = None
            verified_evaluation: SpatialOpportunityEvaluation | None = None
            verified_attention: SpatialAttentionEvaluation | None = None
            verified_response: SpatialResponseEvaluation | None = None
            verified_cognition: CityCognitionEvaluationV1 | None = None
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
                    if isinstance(
                        manifest,
                        (
                            CityRunManifestV6,
                            CityRunManifestV7,
                            CityRunManifestV8,
                            CityRunManifestV9,
                        ),
                    ):
                        if not isinstance(response_input, SpatialResponseInput) or not isinstance(
                            response_evaluation,
                            SpatialResponseEvaluation,
                        ):
                            raise CorruptRunArtifact("spatial response inputs are incomplete")
                        response_input = parse_spatial_response_input_json(
                            canonical_json(response_input)
                        )
                        verified_response = evaluate_spatial_responses(
                            response_input,
                            spatial_scenario,
                            verified_evaluation,
                            verified_attention,
                            agent_ids=tuple(agent.agent_id for agent in mobility.agents),
                        )
                        if verified_response != response_evaluation:
                            raise CorruptRunArtifact(
                                "spatial response evaluation does not match frozen inputs"
                            )
                        response_summary = summarize_spatial_response_artifact(verified_response)
                        response_state = spatial_response_state_document(verified_response)
                        response_input_bytes = _canonical_bytes(response_input)
                        response_summary_bytes = _canonical_bytes(response_summary)
                        response_state_bytes = _canonical_bytes(response_state)
                        if (
                            manifest.spatial_response_schema_version
                            != verified_response.schema_version
                            or manifest.spatial_response_model_id != verified_response.model_id
                            or manifest.response_input_sha256 != response_input.fingerprint
                            or manifest.response_stream_sha256 != response_summary.stream_sha256
                            or manifest.response_state_sha256
                            != response_summary.state_document_sha256
                            or manifest.response_summary_sha256
                            != sha256(response_summary_bytes).hexdigest()
                            or manifest.response_stream_bytes != response_summary.stream_bytes
                            or manifest.response_count != response_summary.counts.response_count
                            or manifest.state_update_count
                            != response_summary.counts.state_update_count
                            or manifest.response_campaign_count
                            != response_summary.counts.campaign_count
                            or manifest.final_state_count
                            != response_summary.counts.final_state_count
                        ):
                            raise CorruptRunArtifact(
                                "city run manifest does not match spatial response inputs"
                            )
                    elif response_input is not None or response_evaluation is not None:
                        raise CorruptRunArtifact("v5 city run cannot contain response evidence")
                elif (
                    attention_evaluation is not None
                    or response_input is not None
                    or response_evaluation is not None
                ):
                    raise CorruptRunArtifact(
                        "v4 city run cannot contain attention or response evidence"
                    )
            elif (
                spatial_scenario is not None
                or opportunity_evaluation is not None
                or attention_evaluation is not None
                or response_input is not None
                or response_evaluation is not None
            ):
                raise CorruptRunArtifact("legacy city run cannot contain spatial inputs")
            if isinstance(manifest, (CityRunManifestV7, CityRunManifestV8, CityRunManifestV9)):
                if workbench_input is None:
                    raise CorruptRunArtifact("city run requires a workbench input")
                bound_input = validate_workbench_execution_binding(
                    workbench_input,
                    pack=pack,
                    run_id=manifest.run_id,
                    seed=manifest.seed,
                    agent_count=manifest.agent_count,
                    days=manifest.days,
                    places=places,
                    spatial_scenario=spatial_scenario,
                    spatial_response=response_input,
                )
                workbench_input = bound_input
                if (
                    manifest.workbench_input_schema_version != workbench_input.schema_version
                    or manifest.workbench_input_sha256 != workbench_input.fingerprint
                ):
                    raise CorruptRunArtifact("city run manifest does not match workbench input")
                workbench_input_bytes = _canonical_bytes(workbench_input)
            elif workbench_input is not None:
                raise CorruptRunArtifact("legacy city run cannot contain a workbench input")
            if isinstance(manifest, CityRunManifestV9):
                if (
                    not isinstance(workbench_input, WorkbenchRunInputV3)
                    or not isinstance(pack, CityPackV2)
                    or verified_response is None
                    or spatial_scenario is None
                    or response_input is None
                    or type(cognition_input) is not CityCognitionInputV1
                    or type(cognition_evaluation) is not CityCognitionEvaluationV1
                ):
                    raise CorruptRunArtifact("schema-9 city run cognition inputs are incomplete")
                cognition_input = _parse_cognition_input(_canonical_bytes(cognition_input))
                _validate_cognition_workbench_binding(cognition_input, workbench_input)
                cognition_context = build_city_cognition_run_context(
                    workbench_input,
                    pack=pack,
                    spatial_scenario=spatial_scenario,
                    response_input=response_input,
                    response_evaluation=verified_response,
                )
                cognition_requests = build_city_cognition_requests(
                    cognition_context,
                    mobility,
                    verified_response,
                )
                verified_cognition = validate_city_cognition_evaluation(
                    cognition_input,
                    cognition_requests,
                    cognition_evaluation.records,
                )
                if verified_cognition != cognition_evaluation:
                    raise CorruptRunArtifact(
                        "city cognition evaluation does not match frozen scientific inputs"
                    )
                expected_receipt = build_city_cognition_manifest_receipt(
                    cognition_input,
                    verified_cognition,
                )
                if manifest.cognition != expected_receipt:
                    raise CorruptRunArtifact("city run manifest does not match cognition evidence")
                cognition_input_bytes = _canonical_bytes(cognition_input)
                cognition_summary_bytes = _canonical_bytes(verified_cognition.summary)
            elif cognition_input is not None or cognition_evaluation is not None:
                raise CorruptRunArtifact("legacy city run cannot contain cognition evidence")
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
                workbench_input_bytes is not None
                and len(workbench_input_bytes)
                > MAX_WORKBENCH_INPUT_BYTES
                + (1 if isinstance(manifest, (CityRunManifestV8, CityRunManifestV9)) else 0)
            )
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
                response_input_bytes is not None
                and len(response_input_bytes) > MAX_SPATIAL_RESPONSE_INPUT_BYTES
            )
            or (
                response_summary_bytes is not None
                and len(response_summary_bytes) > MAX_SPATIAL_RESPONSE_SUMMARY_BYTES
            )
            or (
                response_state_bytes is not None
                and len(response_state_bytes) > MAX_SPATIAL_RESPONSE_STATE_BYTES
            )
            or (
                assignments_bytes is not None
                and len(assignments_bytes) > MAX_CITY_PLACE_ASSIGNMENTS_BYTES
            )
            or (
                cognition_input_bytes is not None
                and len(cognition_input_bytes) > MAX_CITY_COGNITION_INPUT_BYTES
            )
            or (
                cognition_summary_bytes is not None
                and len(cognition_summary_bytes) > MAX_CITY_COGNITION_SUMMARY_BYTES
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
            if response_input_bytes is not None:
                documents.append(
                    (
                        inputs / "spatial-response.json",
                        response_input_bytes,
                        "spatial response input",
                    )
                )
            if workbench_input_bytes is not None:
                documents.append(
                    (
                        inputs / "workbench.json",
                        workbench_input_bytes,
                        "workbench input",
                    )
                )
            if cognition_input_bytes is not None:
                documents.append(
                    (
                        inputs / "cognition.json",
                        cognition_input_bytes,
                        "cognition input",
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
            if (
                response_summary_bytes is not None
                and response_state_bytes is not None
                and verified_response is not None
            ):
                outputs = directory / "outputs"
                summary_path = outputs / "response-summary.json"
                state_path = outputs / "response-state.json"
                stream_path = outputs / "spatial-responses.jsonl"
                _write_new(summary_path, response_summary_bytes)
                if (
                    self._read_document(
                        summary_path,
                        limit=len(response_summary_bytes),
                        label="response summary",
                    )
                    != response_summary_bytes
                ):
                    raise StorageError(
                        "city run staged response summary did not match expected bytes"
                    )
                _write_new(state_path, response_state_bytes)
                if (
                    self._read_document(
                        state_path,
                        limit=len(response_state_bytes),
                        label="response state",
                    )
                    != response_state_bytes
                ):
                    raise StorageError(
                        "city run staged response state did not match expected bytes"
                    )
                _write_response_stream(stream_path, verified_response)
                try:
                    self._verify_response_stream(stream_path, verified_response)
                except CorruptRunArtifact:
                    raise StorageError(
                        "city run staged response stream did not match expected bytes"
                    ) from None
            if cognition_summary_bytes is not None and verified_cognition is not None:
                outputs = directory / "outputs"
                summary_path = outputs / "cognition-summary.json"
                stream_path = outputs / "cognition.jsonl"
                _write_new(summary_path, cognition_summary_bytes)
                if (
                    self._read_document(
                        summary_path,
                        limit=len(cognition_summary_bytes),
                        label="cognition summary",
                    )
                    != cognition_summary_bytes
                ):
                    raise StorageError(
                        "city run staged cognition summary did not match expected bytes"
                    )
                _write_cognition_stream(stream_path, verified_cognition)
                try:
                    self._verify_cognition_stream(stream_path, verified_cognition)
                except CorruptRunArtifact:
                    raise StorageError(
                        "city run staged cognition stream did not match expected bytes"
                    ) from None
            # Shared security correction: final visibility is exclusive,
            # including a destination introduced after ID reservation.
            _publish_manifest(directory, manifest_bytes, self)
        except OSError:
            # An incomplete directory remains visibly invalid; never reuse its ID.
            raise StorageError("city run could not be completed") from None
        return directory

    def _read_document(self, path: Path, *, limit: int, label: str) -> bytes:
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.root):
            raise UnsafeRunLocation(f"city run {label} has an unsafe location")
        _inspect_directories(path.parent)
        try:
            with _open_artifact(path, "rb") as source:
                data = _read_exact(source, limit + 1)
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

    def _verify_response_stream(
        self,
        path: Path,
        evaluation: SpatialResponseEvaluation | SpatialResponseEvaluationV2,
    ) -> None:
        if isinstance(evaluation, SpatialResponseEvaluationV2):
            expected_summary_v2 = summarize_spatial_response_artifact_v2(evaluation)
            _verify_lines(
                path,
                spatial_response_v2_lines(evaluation),
                size=expected_summary_v2.stream_bytes,
                limit=MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2,
            )
            return
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise UnsafeRunLocation("city run response stream has an unsafe location")
        expected_summary = summarize_spatial_response_artifact(evaluation)
        try:
            stream_size = path.stat().st_size
            if (
                stream_size != expected_summary.stream_bytes
                or stream_size > MAX_SPATIAL_RESPONSE_STREAM_BYTES
            ):
                raise CorruptRunArtifact("city run response stream size does not match")
            with path.open("rb") as source:
                for expected_line in spatial_response_lines(evaluation):
                    if source.read(len(expected_line)) != expected_line:
                        raise CorruptRunArtifact(
                            "city run response stream does not match frozen inputs"
                        )
                if source.read(1) != b"":
                    raise CorruptRunArtifact(
                        "city run response stream does not match frozen inputs"
                    )
        except FileNotFoundError:
            raise CorruptRunArtifact("city run response stream is missing") from None
        except OSError:
            raise CorruptRunArtifact("city run response stream is unreadable") from None

    def _verify_cognition_stream(
        self,
        path: Path,
        evaluation: CityCognitionEvaluationV1,
    ) -> None:
        document = self._read_document(
            path,
            limit=MAX_CITY_COGNITION_STREAM_BYTES,
            label="cognition stream",
        )
        try:
            records = _parse_cognition_stream(document)
        except (ValidationError, ValueError, TypeError):
            raise CorruptRunArtifact("city run cognition stream failed validation") from None
        if records != evaluation.records:
            raise CorruptRunArtifact("city run cognition stream does not match frozen inputs")

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
        if isinstance(manifest, CityRunManifestV10):
            return self._load_v10(directory, manifest, manifest_bytes, run_id)
        city_bytes = self._read_document(
            directory / "inputs" / "city.json", limit=MAX_CITY_PACK_BYTES, label="city input"
        )
        agents_bytes = self._read_document(
            directory / "inputs" / "agents.json",
            limit=MAX_CITY_AGENTS_BYTES,
            label="agent assignments",
        )
        workbench_input_bytes: bytes | None = None
        workbench_input_path = directory / "inputs" / "workbench.json"
        if isinstance(manifest, (CityRunManifestV7, CityRunManifestV8, CityRunManifestV9)):
            workbench_input_bytes = self._read_document(
                workbench_input_path,
                # Schema 2 bounds canonical JSON; the sidecar adds exactly one newline.
                limit=MAX_WORKBENCH_INPUT_BYTES
                + (1 if isinstance(manifest, (CityRunManifestV8, CityRunManifestV9)) else 0),
                label="workbench input",
            )
        elif workbench_input_path.is_symlink() or workbench_input_path.exists():
            raise CorruptRunArtifact("legacy city run has an undeclared workbench input")
        places_bytes: bytes | None = None
        assignments_bytes: bytes | None = None
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
        response_input_bytes: bytes | None = None
        response_summary_bytes: bytes | None = None
        response_state_bytes: bytes | None = None
        cognition_input_bytes: bytes | None = None
        cognition_stream_bytes: bytes | None = None
        cognition_summary_bytes: bytes | None = None
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
                attention_summary_bytes = self._read_document(
                    outputs / "attention-summary.json",
                    limit=MAX_CITY_ATTENTION_SUMMARY_BYTES,
                    label="attention summary",
                )
            if isinstance(
                manifest,
                (CityRunManifestV6, CityRunManifestV7, CityRunManifestV8, CityRunManifestV9),
            ):
                response_input_bytes = self._read_document(
                    directory / "inputs" / "spatial-response.json",
                    limit=MAX_SPATIAL_RESPONSE_INPUT_BYTES,
                    label="spatial response input",
                )
                response_summary_bytes = self._read_document(
                    outputs / "response-summary.json",
                    limit=MAX_SPATIAL_RESPONSE_SUMMARY_BYTES,
                    label="response summary",
                )
                response_state_bytes = self._read_document(
                    outputs / "response-state.json",
                    limit=MAX_SPATIAL_RESPONSE_STATE_BYTES,
                    label="response state",
                )
        cognition_paths = (
            directory / "inputs" / "cognition.json",
            directory / "outputs" / "cognition.jsonl",
            directory / "outputs" / "cognition-summary.json",
        )
        if isinstance(manifest, CityRunManifestV9):
            cognition_input_bytes = self._read_document(
                cognition_paths[0],
                limit=MAX_CITY_COGNITION_INPUT_BYTES,
                label="cognition input",
            )
            cognition_stream_bytes = self._read_document(
                cognition_paths[1],
                limit=MAX_CITY_COGNITION_STREAM_BYTES,
                label="cognition stream",
            )
            cognition_summary_bytes = self._read_document(
                cognition_paths[2],
                limit=MAX_CITY_COGNITION_SUMMARY_BYTES,
                label="cognition summary",
            )
        elif any(path.is_symlink() or path.exists() for path in cognition_paths):
            raise CorruptRunArtifact("legacy city run has undeclared cognition evidence")
        try:
            pack = parse_city_pack_json(city_bytes)
            places = None if places_bytes is None else parse_city_place_set_json(places_bytes)
            workbench_input = (
                None
                if workbench_input_bytes is None
                else parse_workbench_run_input_json(
                    workbench_input_bytes.removesuffix(b"\n")
                    if isinstance(manifest, (CityRunManifestV8, CityRunManifestV9))
                    else workbench_input_bytes
                )
            )
            cognition_input: CityCognitionInputV1 | None = None
            cognition_records: tuple[CityCognitionTerminalRecordV1, ...] | None = None
            cognition_summary: CityCognitionSummaryV1 | None = None
            cognition_evaluation: CityCognitionEvaluationV1 | None = None
            if isinstance(manifest, CityRunManifestV9):
                if (
                    cognition_input_bytes is None
                    or cognition_stream_bytes is None
                    or cognition_summary_bytes is None
                ):
                    raise CorruptRunArtifact("city run cognition artifacts are missing")
                cognition_input = _parse_cognition_input(cognition_input_bytes)
                cognition_records = _parse_cognition_stream(cognition_stream_bytes)
                cognition_summary = _parse_cognition_summary(cognition_summary_bytes)
                if cognition_input_bytes != _canonical_bytes(
                    cognition_input
                ) or cognition_summary_bytes != _canonical_bytes(cognition_summary):
                    raise CorruptRunArtifact("city run cognition artifacts are not canonical")
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
            if isinstance(manifest, (CityRunManifestV7, CityRunManifestV8, CityRunManifestV9)) and (
                workbench_input is None
                or workbench_input_bytes != _canonical_bytes(workbench_input)
                or manifest.workbench_input_schema_version != workbench_input.schema_version
                or manifest.workbench_input_sha256 != workbench_input.fingerprint
            ):
                raise CorruptRunArtifact("city run workbench input does not match")
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
            response_input: SpatialResponseInput | None = None
            response_evaluation: SpatialResponseEvaluation | None = None
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
                    if isinstance(
                        manifest,
                        (
                            CityRunManifestV6,
                            CityRunManifestV7,
                            CityRunManifestV8,
                            CityRunManifestV9,
                        ),
                    ):
                        if (
                            response_input_bytes is None
                            or response_summary_bytes is None
                            or response_state_bytes is None
                        ):
                            raise CorruptRunArtifact("city run response inputs are missing")
                        response_input = parse_spatial_response_input_json(response_input_bytes)
                        response_evaluation = evaluate_spatial_responses(
                            response_input,
                            spatial_scenario,
                            opportunity_evaluation,
                            attention_evaluation,
                            agent_ids=tuple(agent.agent_id for agent in mobility.agents),
                        )
                        response_summary = summarize_spatial_response_artifact(response_evaluation)
                        response_state = spatial_response_state_document(response_evaluation)
                        expected_response_summary_bytes = _canonical_bytes(response_summary)
                        expected_response_state_bytes = _canonical_bytes(response_state)
                        if (
                            response_input_bytes != _canonical_bytes(response_input)
                            or response_summary_bytes != expected_response_summary_bytes
                            or response_state_bytes != expected_response_state_bytes
                            or manifest.spatial_response_schema_version
                            != response_evaluation.schema_version
                            or manifest.spatial_response_model_id != response_evaluation.model_id
                            or manifest.response_input_sha256 != response_input.fingerprint
                            or manifest.response_stream_sha256 != response_summary.stream_sha256
                            or manifest.response_state_sha256
                            != response_summary.state_document_sha256
                            or manifest.response_summary_sha256
                            != sha256(expected_response_summary_bytes).hexdigest()
                            or manifest.response_stream_bytes != response_summary.stream_bytes
                            or manifest.response_count != response_summary.counts.response_count
                            or manifest.state_update_count
                            != response_summary.counts.state_update_count
                            or manifest.response_campaign_count
                            != response_summary.counts.campaign_count
                            or manifest.final_state_count
                            != response_summary.counts.final_state_count
                        ):
                            raise CorruptRunArtifact(
                                "city run spatial response artifacts do not match"
                            )
                        self._verify_response_stream(
                            directory / "outputs" / "spatial-responses.jsonl",
                            response_evaluation,
                        )
            if isinstance(manifest, (CityRunManifestV7, CityRunManifestV8, CityRunManifestV9)):
                if workbench_input is None:
                    raise CorruptRunArtifact("city run workbench input is missing")
                try:
                    bound_input = validate_workbench_execution_binding(
                        workbench_input,
                        pack=pack,
                        run_id=manifest.run_id,
                        seed=manifest.seed,
                        agent_count=manifest.agent_count,
                        days=manifest.days,
                        places=places,
                        spatial_scenario=spatial_scenario,
                        spatial_response=response_input,
                    )
                    workbench_input = bound_input
                except (ValidationError, ValueError, TypeError):
                    raise CorruptRunArtifact(
                        "city run workbench input does not match frozen evidence"
                    ) from None
            if isinstance(manifest, CityRunManifestV9):
                if (
                    not isinstance(workbench_input, WorkbenchRunInputV3)
                    or not isinstance(pack, CityPackV2)
                    or spatial_scenario is None
                    or response_input is None
                    or response_evaluation is None
                    or cognition_input is None
                    or cognition_records is None
                    or cognition_summary is None
                ):
                    raise CorruptRunArtifact("city run cognition evidence is incomplete")
                _validate_cognition_workbench_binding(cognition_input, workbench_input)
                cognition_context = build_city_cognition_run_context(
                    workbench_input,
                    pack=pack,
                    spatial_scenario=spatial_scenario,
                    response_input=response_input,
                    response_evaluation=response_evaluation,
                )
                cognition_requests = build_city_cognition_requests(
                    cognition_context,
                    mobility,
                    response_evaluation,
                )
                cognition_evaluation = validate_city_cognition_evaluation(
                    cognition_input,
                    cognition_requests,
                    cognition_records,
                )
                if cognition_summary != cognition_evaluation.summary:
                    raise CorruptRunArtifact(
                        "city run cognition summary does not match terminal evidence"
                    )
                expected_cognition_receipt = build_city_cognition_manifest_receipt(
                    cognition_input,
                    cognition_evaluation,
                )
                if manifest.cognition != expected_cognition_receipt:
                    raise CorruptRunArtifact(
                        "city run cognition receipt does not match frozen evidence"
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
            response_input=response_input,
            response_evaluation=response_evaluation,
            workbench_input=workbench_input,
            cognition_input=cognition_input,
            cognition_evaluation=cognition_evaluation,
        )


__all__ = [
    "MAX_WORKBENCH_INPUT_BYTES",
    "CityRunStore",
    "StoredCityRun",
]
