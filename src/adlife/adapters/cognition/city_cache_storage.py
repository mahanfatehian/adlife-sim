"""Namespace-pinned, schema-blind bytes for immutable city cognition caches."""

from __future__ import annotations

import os
import re
import secrets
import stat
import sys
from contextlib import suppress
from typing import Any, Literal

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

CacheNamespace = Literal["city-cognition-cache", "city-cognition-cache-v2"]

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


def _read_record_file(
    directory: WorkbenchSubdirectory, filename: str, *, limit: int
) -> bytes | None:
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
                limit=limit,
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
        try:
            opened = os.fstat(descriptor)
            _validate_regular(opened, links=frozenset({0}))
            return descriptor
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise
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


class CityCognitionCacheByteStorage:
    """Read and no-clobber publish bounded bytes in one closed cache namespace."""

    __slots__ = ("max_record_bytes", "namespace", "workspace")

    def __init__(
        self,
        workspace: WorkbenchWorkspace,
        *,
        namespace: CacheNamespace,
        max_record_bytes: int,
    ) -> None:
        if not isinstance(namespace, str) or namespace not in {
            "city-cognition-cache",
            "city-cognition-cache-v2",
        }:
            raise ValueError("city cognition cache namespace is invalid")
        if type(max_record_bytes) is not int or max_record_bytes < 1:
            raise ValueError("city cognition cache byte limit is invalid")
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
        self.namespace = namespace
        self.max_record_bytes = max_record_bytes

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
                directory = find_workbench_subdirectory(self.workspace, self.namespace)
            else:
                directory = prepare_workbench_subdirectory(self.workspace, self.namespace)
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

    def read(self, key: str) -> bytes | None:
        """Return exact bytes; only a genuinely absent name is a miss."""
        filename = self._filename(key)
        directory = self._directory(create=False)
        if directory is None:
            return None
        return _read_record_file(directory, filename, limit=self.max_record_bytes)

    def publish(self, key: str, document: bytes) -> None:
        """Publish verified bytes from an owned stage handle without clobbering."""
        filename = self._filename(key)
        if not isinstance(document, bytes):
            raise TypeError("document must be bytes")
        if len(document) > self.max_record_bytes:
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
                        limit=self.max_record_bytes,
                    )
                    != document
                ):
                    raise OSError("cache publication verification failed")
                _sync_directory(pinned)

            successful_stage = descriptor
            descriptor = None
            _release_stage(successful_stage, committed=True)
            if _read_record_file(directory, filename, limit=self.max_record_bytes) != document:
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
                            limit=self.max_record_bytes,
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
    "CacheNamespace",
    "CityCognitionCacheByteStorage",
    "CityCognitionCacheConflict",
    "CityCognitionCacheCorruption",
    "CityCognitionCachePublicationError",
    "CityCognitionCacheUnavailable",
]
