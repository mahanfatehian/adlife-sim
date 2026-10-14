"""Pinned, immutable cache records for provider-backed city explanations.

The cache is an optimization, never artifact-integrity evidence.  A genuine absence is
the only cache miss.  Any present but malformed, noncanonical, redirected, oversized, or
cross-bound record is a typed hard failure and is left untouched for inspection.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import sys
from contextlib import suppress
from hashlib import sha256
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, ValidationError, model_validator

import adlife.city.workbench_immutable_registry as immutable_registry
from adlife.city.workbench_immutable_registry import ImmutableWorkspaceRegistryUnavailable
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    WorkbenchSubdirectory,
    WorkbenchWorkspace,
    find_workbench_subdirectory,
    prepare_workbench_subdirectory,
    verify_workbench_workspace,
)
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_RESERVATION_TOKENS,
    MAX_CITY_COGNITION_USAGE_INTEGER,
    CityCognitionInputV1,
    CityCognitionRequestV1,
    CityCognitionResultV1,
    Hash,
)
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

CITY_COGNITION_CACHE_KEY_VERSION: Literal["city-cognition-cache-v1"] = "city-cognition-cache-v1"
MAX_CITY_COGNITION_CACHE_RECORD_BYTES = 131_072

_CACHE_DIRECTORY = "city-cognition-cache"
_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class CityCognitionCacheCorruption(RuntimeError):
    """A present cache record cannot be trusted."""

    def __init__(self) -> None:
        super().__init__("city cognition cache record is corrupt")


class CityCognitionCacheConflict(ValueError):
    """A no-clobber cache destination already exists."""

    def __init__(self) -> None:
        super().__init__("city cognition cache destination already exists")


class CityCognitionCacheUnavailable(RuntimeError):
    """The pinned cache directory cannot be used safely."""

    def __init__(self) -> None:
        super().__init__("city cognition cache is unavailable")


class CityCognitionCachePublicationError(RuntimeError):
    """Publication failed, preserving whether its final link may exist."""

    def __init__(self, potentially_committed: bool) -> None:
        if type(potentially_committed) is not bool:
            raise TypeError("publication state must be a boolean")
        self.potentially_committed = potentially_committed
        message = (
            "city cognition cache publication may already be committed"
            if potentially_committed
            else "city cognition cache publication failed before commit"
        )
        super().__init__(message)


def _validated_request_and_input(
    request: CityCognitionRequestV1,
    cognition_input: CityCognitionInputV1,
) -> tuple[CityCognitionRequestV1, CityCognitionInputV1]:
    if not isinstance(request, CityCognitionRequestV1):
        raise TypeError("request must be a CityCognitionRequestV1")
    if not isinstance(cognition_input, CityCognitionInputV1):
        raise TypeError("cognition_input must be a CityCognitionInputV1")
    checked_request: CityCognitionRequestV1 | None = None
    checked_input: CityCognitionInputV1 | None = None
    try:
        checked_request = CityCognitionRequestV1.model_validate(
            request.model_dump(mode="python", warnings="none")
        )
        checked_input = CityCognitionInputV1.model_validate(
            cognition_input.model_dump(mode="python", warnings="none")
        )
    except (TypeError, ValidationError, ValueError):
        pass
    if checked_request is None or checked_input is None:
        raise ValueError("city cognition cache key material failed validation")
    if checked_input.mode not in {"local", "remote"}:
        raise ValueError("city cognition cache requires a provider input")
    if checked_request.run_input_sha256 != checked_input.run_input_sha256:
        raise ValueError("city cognition cache key material is cross-bound")
    reservation = next(
        (
            item
            for item in checked_input.reservations
            if item.request_id == checked_request.request_id
        ),
        None,
    )
    if (
        reservation is None
        or reservation.request_sha256 != checked_request.fingerprint
        or reservation.reserved_output_tokens != CITY_COGNITION_RESERVATION_TOKENS
    ):
        raise ValueError("city cognition cache request is not a funded frozen selection")
    return checked_request, checked_input


def _key_from_digests(request_sha256: str, cognition_input_sha256: str) -> str:
    material = {
        "cache_key_version": CITY_COGNITION_CACHE_KEY_VERSION,
        "cognition_input_sha256": cognition_input_sha256,
        "request_sha256": request_sha256,
    }
    return sha256(canonical_json(material).encode("utf-8")).hexdigest()


class CityCognitionCacheRecordV1(DomainModel):
    """One validated answer and the complete non-secret contracts that address it."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    schema_version: Literal[1]
    key: Hash
    request: CityCognitionRequestV1
    cognition_input: CityCognitionInputV1
    result: CityCognitionResultV1
    prompt_tokens: int | None = Field(default=None, ge=0, le=MAX_CITY_COGNITION_USAGE_INTEGER)
    completion_tokens: int | None = Field(
        default=None,
        ge=0,
        le=MAX_CITY_COGNITION_USAGE_INTEGER,
    )

    @model_validator(mode="after")
    def coherent_binding(self) -> Self:
        checked_request, checked_input = _validated_request_and_input(
            self.request,
            self.cognition_input,
        )
        if self.result.request_id != checked_request.request_id:
            raise ValueError("city cognition cache result answers another request")
        expected = _key_from_digests(
            checked_request.fingerprint,
            checked_input.fingerprint,
        )
        if self.key != expected:
            raise ValueError("city cognition cache key does not address its contracts")
        if (self.prompt_tokens is None) != (self.completion_tokens is None):
            raise ValueError("city cognition cache usage must be wholly present or absent")
        if len((canonical_json(self) + "\n").encode("utf-8")) > (
            MAX_CITY_COGNITION_CACHE_RECORD_BYTES
        ):
            raise ValueError("city cognition cache record exceeds its size limit")
        return self


def _unsafe_status(value: os.stat_result) -> bool:
    attributes = int(getattr(value, "st_file_attributes", 0))
    return stat.S_ISLNK(value.st_mode) or bool(attributes & _REPARSE_POINT)


def _identity(value: os.stat_result) -> tuple[int, int]:
    device = int(value.st_dev)
    inode = int(value.st_ino)
    if device == 0 or inode == 0:
        raise OSError("filesystem identity is unavailable")
    return device, inode


def _validate_regular(value: os.stat_result, *, links: frozenset[int]) -> None:
    if _unsafe_status(value) or not stat.S_ISREG(value.st_mode) or int(value.st_nlink) not in links:
        raise OSError("cache entry is not an owned regular file")


def _read_descriptor(descriptor: int, limit: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise OSError("cache entry exceeds its size limit")
    return data


def _read_record_file(directory: WorkbenchSubdirectory, filename: str) -> bytes | None:
    failure: Literal["corrupt", "unavailable"] | None = None
    try:
        with immutable_registry._pin_subdirectory(directory) as pinned:
            path = pinned.path / filename
            try:
                named_before = os.lstat(path)
            except FileNotFoundError:
                return None
            _validate_regular(named_before, links=frozenset({1}))
            data = immutable_registry._read_record(
                pinned,
                filename,
                limit=MAX_CITY_COGNITION_CACHE_RECORD_BYTES,
            )
            named_after = os.lstat(path)
            _validate_regular(named_after, links=frozenset({1}))
            if _identity(named_after) != _identity(named_before) or named_after.st_size != len(
                data
            ):
                raise OSError("cache entry changed while being read")
            return data
    except ImmutableWorkspaceRegistryUnavailable:
        failure = "unavailable"
    except (OSError, ValueError, UnsafeWorkbenchWorkspace):
        failure = "corrupt"
    if failure == "unavailable":
        raise CityCognitionCacheUnavailable()
    raise CityCognitionCacheCorruption()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _parse_record(data: bytes, key: str) -> CityCognitionCacheRecordV1:
    record: CityCognitionCacheRecordV1 | None = None
    canonical: bytes | None = None
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError("cache document is not an object")
        # The domain contracts are strict Python models whose tuple fields are JSON
        # arrays on disk.  Validate the original JSON representation after the
        # duplicate/depth/finite preflight so Pydantic applies its JSON tuple semantics
        # instead of incorrectly treating decoded lists as Python caller input.
        record = CityCognitionCacheRecordV1.model_validate_json(text)
        canonical = (canonical_json(record) + "\n").encode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValidationError, ValueError):
        pass
    if record is None or canonical is None:
        raise CityCognitionCacheCorruption()
    if canonical != data or record.key != key:
        raise CityCognitionCacheCorruption()
    return record


def _open_unnamed_stage(directory: Any, stage_name: str) -> int:
    """Open a stage whose replaceable pathname is never used for publication."""
    if sys.platform == "win32":
        return immutable_registry._open_windows_stage(directory.path / stage_name)
    if directory.descriptor is None:
        raise OSError("pinned cache publication is unavailable")
    flags = (
        os.O_RDWR
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if sys.platform.startswith("linux"):
        temporary_flag = getattr(os, "O_TMPFILE", 0)
        if temporary_flag == 0:
            raise OSError("unnamed cache publication is unavailable")
        descriptor = os.open(".", flags | temporary_flag, 0o600, dir_fd=directory.descriptor)
        opened = os.fstat(descriptor)
        _validate_regular(opened, links=frozenset({0}))
        return descriptor
    if sys.platform == "darwin":
        descriptor = os.open(
            stage_name,
            flags | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory.descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            _validate_regular(opened, links=frozenset({1}))
            os.unlink(stage_name, dir_fd=directory.descriptor)
            _validate_regular(os.fstat(descriptor), links=frozenset({0}))
            return descriptor
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise
    raise OSError("pinned cache publication is unavailable")


def _release_stage(descriptor: int | None, *, committed: bool) -> None:
    if descriptor is None:
        return
    immutable_registry._release_stage_descriptor(descriptor, committed=committed)


def _sync_directory(directory: Any) -> None:
    immutable_registry._sync_pinned_directory(directory)


class CityCognitionCache:
    """Read and no-clobber publish bounded city cognition cache records."""

    __slots__ = ("workspace",)

    def __init__(self, workspace: WorkbenchWorkspace) -> None:
        if not isinstance(workspace, WorkbenchWorkspace):
            raise TypeError("workspace must be a WorkbenchWorkspace")
        unavailable = False
        try:
            verify_workbench_workspace(workspace)
        except (OSError, ValueError, UnsafeWorkbenchWorkspace):
            unavailable = True
        if unavailable:
            raise CityCognitionCacheUnavailable()
        self.workspace = workspace

    @staticmethod
    def make_key(
        request: CityCognitionRequestV1,
        cognition_input: CityCognitionInputV1,
    ) -> str:
        checked_request, checked_input = _validated_request_and_input(
            request,
            cognition_input,
        )
        return _key_from_digests(checked_request.fingerprint, checked_input.fingerprint)

    @staticmethod
    def _filename(key: str) -> str:
        if not isinstance(key, str) or _KEY_PATTERN.fullmatch(key) is None:
            raise ValueError("city cognition cache key is invalid")
        return f"{key}.json"

    def _directory(self, *, create: bool) -> WorkbenchSubdirectory | None:
        directory: WorkbenchSubdirectory | None = None
        unavailable = False
        try:
            if not create:
                directory = find_workbench_subdirectory(self.workspace, _CACHE_DIRECTORY)
            else:
                directory = prepare_workbench_subdirectory(self.workspace, _CACHE_DIRECTORY)
                with immutable_registry._pin_workspace_root(self.workspace) as root:
                    immutable_registry._sync_workspace_root(root)
        except (
            OSError,
            ValueError,
            UnsafeWorkbenchWorkspace,
            ImmutableWorkspaceRegistryUnavailable,
        ):
            unavailable = True
        if unavailable:
            raise CityCognitionCacheUnavailable()
        return directory

    def get(self, key: str) -> CityCognitionCacheRecordV1 | None:
        """Return one exact record; only an absent name is a cache miss."""
        filename = self._filename(key)
        directory = self._directory(create=False)
        if directory is None:
            return None
        data = _read_record_file(directory, filename)
        if data is None:
            return None
        return _parse_record(data, key)

    def put(self, key: str, record: CityCognitionCacheRecordV1) -> None:
        """Publish one canonical cache record atomically without replacing a name."""
        filename = self._filename(key)
        if not isinstance(record, CityCognitionCacheRecordV1):
            raise TypeError("record must be a CityCognitionCacheRecordV1")
        checked: CityCognitionCacheRecordV1 | None = None
        with suppress(TypeError, ValidationError, ValueError):
            checked = CityCognitionCacheRecordV1.model_validate(
                record.model_dump(mode="python", warnings="none")
            )
        if checked is None:
            raise ValueError("city cognition cache record failed validation")
        if checked.key != key:
            raise ValueError("city cognition cache key does not match the record")
        document = (canonical_json(checked) + "\n").encode("utf-8")
        if len(document) > MAX_CITY_COGNITION_CACHE_RECORD_BYTES:
            raise ValueError("city cognition cache record exceeds its size limit")

        directory = self._directory(create=True)
        assert directory is not None
        stage_name = f".stage-{secrets.token_hex(16)}.tmp"
        descriptor: int | None = None
        potentially_committed = False
        failure: Literal["conflict", "publication"] | None = None
        try:
            with immutable_registry._pin_subdirectory(directory) as pinned:
                descriptor = _open_unnamed_stage(pinned, stage_name)
                opened = os.fstat(descriptor)
                _validate_regular(opened, links=frozenset({0, 1}))
                remaining = memoryview(document)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("cache stage write did not make progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
                if _read_descriptor(descriptor, len(document)) != document:
                    raise OSError("cache stage verification failed")

                # The no-clobber operation consumes only the verified open handle.
                # No replaceable stage pathname is ever an authority for publication.
                potentially_committed = True
                try:
                    immutable_registry._publish_stage_descriptor(
                        descriptor,
                        pinned,
                        filename,
                    )
                except FileExistsError:
                    potentially_committed = False
                    raise
                if (
                    immutable_registry._read_record(
                        pinned,
                        filename,
                        limit=MAX_CITY_COGNITION_CACHE_RECORD_BYTES,
                    )
                    != document
                ):
                    raise OSError("cache publication verification failed")
                _sync_directory(pinned)

            successful_stage = descriptor
            descriptor = None
            _release_stage(successful_stage, committed=True)
            if _read_record_file(directory, filename) != document:
                raise OSError("committed cache record verification failed")
        except FileExistsError:
            conflicting_stage = descriptor
            descriptor = None
            with suppress(OSError):
                _release_stage(conflicting_stage, committed=False)
            failure = "conflict"
        except (Exception, KeyboardInterrupt):
            if potentially_committed:
                try:
                    with immutable_registry._pin_subdirectory(directory) as pinned:
                        immutable_registry._read_record(
                            pinned,
                            filename,
                            limit=MAX_CITY_COGNITION_CACHE_RECORD_BYTES,
                        )
                except FileNotFoundError:
                    potentially_committed = False
                except (
                    OSError,
                    ValueError,
                    UnsafeWorkbenchWorkspace,
                    ImmutableWorkspaceRegistryUnavailable,
                ):
                    # Once the no-clobber syscall begins, only proven absence can
                    # downgrade ambiguity.  Unreadable or changed evidence may still
                    # be a durably committed destination.
                    potentially_committed = True
            failed_stage = descriptor
            descriptor = None
            with suppress(OSError):
                _release_stage(
                    failed_stage,
                    committed=potentially_committed,
                )
            failure = "publication"
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    _release_stage(
                        descriptor,
                        committed=potentially_committed,
                    )
        if failure == "conflict":
            raise CityCognitionCacheConflict()
        if failure == "publication":
            raise CityCognitionCachePublicationError(potentially_committed)


__all__ = [
    "CITY_COGNITION_CACHE_KEY_VERSION",
    "MAX_CITY_COGNITION_CACHE_RECORD_BYTES",
    "CityCognitionCache",
    "CityCognitionCacheConflict",
    "CityCognitionCacheCorruption",
    "CityCognitionCachePublicationError",
    "CityCognitionCacheRecordV1",
    "CityCognitionCacheUnavailable",
]
