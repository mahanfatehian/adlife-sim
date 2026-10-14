"""Descriptor-pinned primitives for small immutable workspace registries.

The workbench has several content-addressed registries.  This module owns the
filesystem mechanics shared by those registries without knowing anything about a
city pack, provider, or domain document.  Callers remain responsible for parsing
and canonically revalidating every returned byte string.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from typing import Any, cast

from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    WorkbenchSubdirectory,
    WorkbenchWorkspace,
    find_workbench_subdirectory,
    prepare_workbench_subdirectory,
    verify_workbench_subdirectory,
    verify_workbench_workspace,
)

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DIRECTORY_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_LOCK_NAME = re.compile(r"^\.[a-z][a-z0-9-]{0,62}\.lock$")
_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[tuple[str, int | None, int | None, str], threading.RLock] = {}


class ImmutableWorkspaceRegistryUnavailable(RuntimeError):
    """A registry cannot be read as one trustworthy immutable snapshot."""

    def __init__(self) -> None:
        super().__init__("immutable workspace registry is unavailable")


class ImmutableWorkspaceRegistryConflict(ValueError):
    """A no-clobber destination or a configured registry capacity conflicts."""

    def __init__(self) -> None:
        super().__init__("immutable workspace registry conflicts with workspace state")


class ImmutableWorkspacePublicationError(RuntimeError):
    """Publication failed and records whether the destination may now exist."""

    def __init__(self, potentially_committed: bool) -> None:
        if type(potentially_committed) is not bool:
            raise TypeError("publication state must be a boolean")
        self.potentially_committed = potentially_committed
        message = (
            "immutable publication may already be committed"
            if potentially_committed
            else "immutable publication failed before commit"
        )
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ImmutableWorkspaceRecord:
    """Path-free bytes read from one pinned group and immutable record name."""

    group_id: str
    record_id: str
    document: bytes


@dataclass(frozen=True, slots=True)
class ImmutableWorkspaceRegistryLayout:
    """Closed layout and bounds for one two-level content-addressed registry."""

    records_directory: str
    staging_directory: str
    lock_filename: str
    group_pattern: Pattern[str]
    record_pattern: Pattern[str]
    max_groups: int
    max_records_per_group: int
    max_record_bytes: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.records_directory, str)
            or _DIRECTORY_NAME.fullmatch(self.records_directory) is None
            or not isinstance(self.staging_directory, str)
            or _DIRECTORY_NAME.fullmatch(self.staging_directory) is None
            or self.records_directory == self.staging_directory
            or not isinstance(self.lock_filename, str)
            or _LOCK_NAME.fullmatch(self.lock_filename) is None
            or not isinstance(self.group_pattern, re.Pattern)
            or not isinstance(self.record_pattern, re.Pattern)
            or "record_id" not in self.record_pattern.groupindex
            or type(self.max_groups) is not int
            or not 1 <= self.max_groups <= 1_000
            or type(self.max_records_per_group) is not int
            or not 1 <= self.max_records_per_group <= 1_000
            or type(self.max_record_bytes) is not int
            or not 1 <= self.max_record_bytes <= 16_777_216
        ):
            raise ValueError("immutable registry layout is invalid")


@dataclass(frozen=True, slots=True)
class _PinnedDirectory:
    pinned: WorkbenchSubdirectory
    descriptor: int | None
    windows_handle: _WindowsHandle | None

    @property
    def path(self) -> Path:
        return self.pinned.path


@dataclass(frozen=True, slots=True)
class _PinnedWorkspaceRoot:
    workspace: WorkbenchWorkspace
    path: Path
    identity: tuple[int, int]
    descriptor: int | None
    windows_handle: _WindowsHandle | None


def _unsafe_status(status: os.stat_result) -> bool:
    attributes = int(getattr(status, "st_file_attributes", 0))
    return stat.S_ISLNK(status.st_mode) or bool(attributes & _REPARSE_POINT)


def _identity(status: os.stat_result) -> tuple[int, int]:
    device = int(status.st_dev)
    inode = int(status.st_ino)
    if device == 0 or inode == 0:
        raise OSError("filesystem identity is unavailable")
    return device, inode


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return _identity(first) == _identity(second)


def _verify_regular(status: os.stat_result) -> None:
    if _unsafe_status(status) or not stat.S_ISREG(status.st_mode):
        raise OSError("registry entry is not a real regular file")


def _verify_directory(status: os.stat_result) -> None:
    if _unsafe_status(status) or not stat.S_ISDIR(status.st_mode):
        raise OSError("registry entry is not a real directory")


def _process_lock(
    workspace: WorkbenchWorkspace,
    lock_filename: str,
) -> threading.RLock:
    key = (
        os.path.normcase(os.fspath(workspace.root)),
        workspace.device,
        workspace.inode,
        lock_filename,
    )
    with _LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    @dataclass(slots=True)
    class _WindowsHandle:
        value: int | None

        def close(self) -> None:
            if self.value is None:
                return
            kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            value = self.value
            self.value = None
            if not close_handle(wintypes.HANDLE(value)):
                raise OSError(ctypes.get_last_error(), "filesystem handle could not be closed")

    def _open_windows_directory(path: Path) -> _WindowsHandle:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        raw = create_file(
            os.fspath(path),
            0x0080 | 0x0020,  # FILE_READ_ATTRIBUTES | FILE_TRAVERSE
            0x00000001 | 0x00000002,  # share read/write, but pin against deletion
            None,
            3,  # OPEN_EXISTING
            0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
            None,
        )
        value = cast(int | None, raw)
        if value == _INVALID_HANDLE_VALUE or value is None:
            raise OSError(ctypes.get_last_error(), "directory handle could not be opened")
        return _WindowsHandle(int(value))

    def _open_windows_stage(path: Path) -> int:
        descriptor = ctypes.c_int(-1)
        runtime: Any = ctypes.CDLL("ucrtbase", use_errno=True)
        open_secure = runtime._wsopen_s
        open_secure.argtypes = (
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        )
        open_secure.restype = ctypes.c_int
        result = int(
            open_secure(
                ctypes.byref(descriptor),
                os.fspath(path),
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | 0x0040  # _O_TEMPORARY: unlink this stage name on close
                | 0x0080,  # _O_NOINHERIT
                0x0020,  # _SH_DENYWR
                0o600,
            )
        )
        if result != 0 or descriptor.value < 0:
            raise OSError(result, "staging file could not be opened")
        return int(descriptor.value)

    def _link_windows_descriptor(
        descriptor: int,
        destination: _PinnedDirectory,
        record_name: str,
    ) -> None:
        handle = destination.windows_handle
        if handle is None or handle.value is None:
            raise OSError(errno.EBADF, "pinned directory handle is unavailable")

        class _ReplaceOrFlags(ctypes.Union):
            _fields_ = (("replace_if_exists", ctypes.c_ubyte), ("flags", wintypes.ULONG))

        class _FileLinkInfo(ctypes.Structure):
            _fields_ = (
                ("replace_or_flags", _ReplaceOrFlags),
                ("root_directory", wintypes.HANDLE),
                ("file_name_length", wintypes.DWORD),
                ("file_name", ctypes.c_wchar * len(record_name)),
            )

        class _IoStatusBlock(ctypes.Structure):
            _fields_ = (("status", ctypes.c_void_p), ("information", ctypes.c_size_t))

        source_handle = msvcrt.get_osfhandle(descriptor)
        if source_handle == -1:
            raise OSError(errno.EBADF, "staging handle is unavailable")
        information = _FileLinkInfo(
            replace_or_flags=_ReplaceOrFlags(replace_if_exists=0),
            root_directory=wintypes.HANDLE(handle.value),
            file_name_length=len(record_name.encode("utf-16-le")),
            file_name=record_name,
        )
        ntdll: Any = ctypes.WinDLL("ntdll", use_last_error=True)
        set_information = ntdll.NtSetInformationFile
        set_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_IoStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.c_int,
        )
        set_information.restype = ctypes.c_long
        io_status = _IoStatusBlock()
        status = int(
            set_information(
                wintypes.HANDLE(source_handle),
                ctypes.byref(io_status),
                ctypes.byref(information),
                ctypes.sizeof(information),
                11,  # FileLinkInformation
            )
        )
        if status >= 0:
            return
        convert_status = ntdll.RtlNtStatusToDosError
        convert_status.argtypes = (ctypes.c_long,)
        convert_status.restype = wintypes.ULONG
        error_number = int(convert_status(status))
        if error_number in {80, 183}:
            raise FileExistsError(error_number, "immutable destination already exists")
        raise OSError(error_number, "pinned immutable publication failed")

    def _lock_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)

    def _unlock_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    @dataclass(slots=True)
    class _WindowsHandle:
        value: int | None = None

        def close(self) -> None:
            return

    def _open_windows_directory(_path: Path) -> _WindowsHandle:
        raise OSError("Windows directory handles are unavailable")

    def _open_windows_stage(_path: Path) -> int:
        raise OSError("Windows staging handles are unavailable")

    def _link_windows_descriptor(
        _descriptor: int,
        _destination: _PinnedDirectory,
        _record_name: str,
    ) -> None:
        raise OSError("Windows descriptor publication is unavailable")

    def _lock_descriptor(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_EX)

    def _unlock_descriptor(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _pin_subdirectory(pinned: WorkbenchSubdirectory) -> Iterator[_PinnedDirectory]:
    descriptor: int | None = None
    windows_handle: _WindowsHandle | None = None
    try:
        try:
            path = verify_workbench_subdirectory(pinned)
            before = os.lstat(path)
            _verify_directory(before)
            expected = _identity(before)
            if sys.platform == "win32":
                windows_handle = _open_windows_directory(path)
            else:
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                opened = os.fstat(descriptor)
                _verify_directory(opened)
                if _identity(opened) != expected:
                    raise OSError("registry directory identity changed")
            after = os.lstat(path)
            _verify_directory(after)
            if _identity(after) != expected:
                raise OSError("registry directory identity changed")
        except (OSError, ValueError, UnsafeWorkbenchWorkspace):
            raise ImmutableWorkspaceRegistryUnavailable() from None
        yield _PinnedDirectory(pinned, descriptor, windows_handle)
        try:
            final = os.lstat(path)
            _verify_directory(final)
            if _identity(final) != expected:
                raise OSError("registry directory identity changed")
            verify_workbench_subdirectory(pinned)
        except (OSError, ValueError, UnsafeWorkbenchWorkspace):
            raise ImmutableWorkspaceRegistryUnavailable() from None
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if windows_handle is not None:
            with suppress(OSError):
                windows_handle.close()


@contextmanager
def _pin_workspace_root(workspace: WorkbenchWorkspace) -> Iterator[_PinnedWorkspaceRoot]:
    descriptor: int | None = None
    windows_handle: _WindowsHandle | None = None
    try:
        try:
            path = verify_workbench_workspace(workspace)
            before = os.lstat(path)
            _verify_directory(before)
            expected = _identity(before)
            if sys.platform == "win32":
                windows_handle = _open_windows_directory(path)
            else:
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                opened = os.fstat(descriptor)
                _verify_directory(opened)
                if _identity(opened) != expected:
                    raise OSError("workspace root identity changed")
            after = os.lstat(path)
            _verify_directory(after)
            if _identity(after) != expected:
                raise OSError("workspace root identity changed")
        except (OSError, ValueError, UnsafeWorkbenchWorkspace):
            raise ImmutableWorkspaceRegistryUnavailable() from None
        yield _PinnedWorkspaceRoot(workspace, path, expected, descriptor, windows_handle)
        try:
            final = os.lstat(path)
            _verify_directory(final)
            if _identity(final) != expected:
                raise OSError("workspace root identity changed")
            verify_workbench_workspace(workspace)
        except (OSError, ValueError, UnsafeWorkbenchWorkspace):
            raise ImmutableWorkspaceRegistryUnavailable() from None
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if windows_handle is not None:
            with suppress(OSError):
                windows_handle.close()


def _nested_workspace(pinned: WorkbenchSubdirectory) -> WorkbenchWorkspace:
    verify_workbench_subdirectory(pinned)
    if pinned.device is None or pinned.inode is None:
        raise OSError("nested registry identity is unavailable")
    workspace = WorkbenchWorkspace(pinned.path, pinned.device, pinned.inode)
    verify_workbench_workspace(workspace)
    return workspace


def _path_subdirectory(
    workspace: WorkbenchWorkspace,
    name: str,
    *,
    create: bool,
) -> WorkbenchSubdirectory | None:
    try:
        return (
            prepare_workbench_subdirectory(workspace, name)
            if create
            else find_workbench_subdirectory(workspace, name)
        )
    except (OSError, ValueError, UnsafeWorkbenchWorkspace):
        raise ImmutableWorkspaceRegistryUnavailable() from None


def _direct_subdirectory(
    parent: _PinnedWorkspaceRoot | _PinnedDirectory,
    name: str,
    *,
    create: bool,
) -> WorkbenchSubdirectory | None:
    """Resolve one child relative to a pinned descriptor whenever the OS permits."""
    try:
        workspace = (
            parent.workspace
            if isinstance(parent, _PinnedWorkspaceRoot)
            else _nested_workspace(parent.pinned)
        )
    except (OSError, ValueError, UnsafeWorkbenchWorkspace):
        raise ImmutableWorkspaceRegistryUnavailable() from None
    if parent.descriptor is None:
        return _path_subdirectory(workspace, name, create=create)
    try:
        status = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent.descriptor)
            status = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        except FileExistsError:
            status = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        except OSError:
            raise ImmutableWorkspaceRegistryUnavailable() from None
    except OSError:
        raise ImmutableWorkspaceRegistryUnavailable() from None
    try:
        _verify_directory(status)
        device, inode = _identity(status)
        pinned = WorkbenchSubdirectory(
            workspace=workspace,
            name=name,
            path=parent.path / name,
            device=device,
            inode=inode,
        )
        verify_workbench_subdirectory(pinned)
        return pinned
    except (OSError, ValueError, UnsafeWorkbenchWorkspace):
        raise ImmutableWorkspaceRegistryUnavailable() from None


def _open_safe_lock(root: _PinnedWorkspaceRoot, filename: str) -> int:
    path = root.path / filename
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = (
        os.open(filename, flags, 0o600, dir_fd=root.descriptor)
        if root.descriptor is not None
        else os.open(path, flags, 0o600)
    )
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(path)
        _verify_regular(opened)
        _verify_regular(named)
        if (
            opened.st_nlink != 1
            or named.st_nlink != 1
            or not _same_identity(opened, named)
            or opened.st_size not in {0, 1}
        ):
            raise OSError("registry lock identity changed")
        if opened.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        verify_workbench_workspace(root.workspace)
        final_handle = os.fstat(descriptor)
        final_name = os.lstat(path)
        _verify_regular(final_handle)
        _verify_regular(final_name)
        if (
            final_handle.st_nlink != 1
            or final_name.st_nlink != 1
            or final_handle.st_size != 1
            or final_name.st_size != 1
            or not _same_identity(opened, final_handle)
            or not _same_identity(final_handle, final_name)
        ):
            raise OSError("registry lock identity changed")
        return descriptor
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _verify_safe_lock(
    root: _PinnedWorkspaceRoot,
    filename: str,
    descriptor: int,
) -> None:
    path = root.path / filename
    opened = os.fstat(descriptor)
    named = os.lstat(path)
    _verify_regular(opened)
    _verify_regular(named)
    if (
        opened.st_nlink != 1
        or named.st_nlink != 1
        or opened.st_size != 1
        or named.st_size != 1
        or not _same_identity(opened, named)
    ):
        raise OSError("registry lock identity changed")
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.read(descriptor, 2) != b"\0":
        raise OSError("registry lock contents changed")
    verify_workbench_workspace(root.workspace)
    final_handle = os.fstat(descriptor)
    final_name = os.lstat(path)
    _verify_regular(final_handle)
    _verify_regular(final_name)
    if (
        final_handle.st_nlink != 1
        or final_name.st_nlink != 1
        or final_handle.st_size != 1
        or final_name.st_size != 1
        or not _same_identity(opened, final_handle)
        or not _same_identity(final_handle, final_name)
    ):
        raise OSError("registry lock identity changed")


@contextmanager
def _registry_lock(
    workspace: WorkbenchWorkspace,
    filename: str,
) -> Iterator[_PinnedWorkspaceRoot]:
    process_lock = _process_lock(workspace, filename)
    with process_lock:
        descriptor: int | None = None
        locked = False
        try:
            with _pin_workspace_root(workspace) as root:
                try:
                    descriptor = _open_safe_lock(root, filename)
                    _lock_descriptor(descriptor)
                    locked = True
                    _verify_safe_lock(root, filename, descriptor)
                except (OSError, ValueError, UnsafeWorkbenchWorkspace):
                    raise ImmutableWorkspaceRegistryUnavailable() from None
                yield root
                try:
                    _verify_safe_lock(root, filename, descriptor)
                except (OSError, ValueError, UnsafeWorkbenchWorkspace):
                    raise ImmutableWorkspaceRegistryUnavailable() from None
        finally:
            if descriptor is not None:
                if locked:
                    with suppress(OSError):
                        _unlock_descriptor(descriptor)
                with suppress(OSError):
                    os.close(descriptor)


def _directory_names(directory: _PinnedDirectory) -> tuple[str, ...]:
    try:
        verify_workbench_subdirectory(directory.pinned)
        scan_target: int | Path = (
            directory.descriptor if directory.descriptor is not None else directory.path
        )
        with os.scandir(scan_target) as entries:
            names = tuple(entry.name for entry in entries)
        verify_workbench_subdirectory(directory.pinned)
    except (OSError, ValueError, UnsafeWorkbenchWorkspace):
        raise ImmutableWorkspaceRegistryUnavailable() from None
    return tuple(sorted(names))


def _open_record(directory: _PinnedDirectory, name: str) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if directory.descriptor is not None:
        return os.open(name, flags, dir_fd=directory.descriptor)
    return os.open(directory.path / name, flags)


def _read_record(
    directory: _PinnedDirectory,
    name: str,
    *,
    limit: int,
) -> bytes:
    path = directory.path / name
    before = os.lstat(path)
    _verify_regular(before)
    if before.st_size > limit:
        raise OSError("registry record exceeds its size limit")
    descriptor = _open_record(directory, name)
    try:
        opened = os.fstat(descriptor)
        _verify_regular(opened)
        if not _same_identity(before, opened):
            raise OSError("registry record identity changed")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        document = b"".join(chunks)
        if len(document) > limit:
            raise OSError("registry record exceeds its size limit")
        after_handle = os.fstat(descriptor)
        after_path = os.lstat(path)
        _verify_regular(after_handle)
        _verify_regular(after_path)
        if not _same_identity(opened, after_handle) or not _same_identity(after_handle, after_path):
            raise OSError("registry record identity changed")
        if (
            opened.st_size != after_handle.st_size
            or opened.st_mtime_ns != after_handle.st_mtime_ns
            or len(document) != after_handle.st_size
        ):
            raise OSError("registry record changed while it was read")
        return document
    finally:
        os.close(descriptor)


def _sync_workspace_root(root: _PinnedWorkspaceRoot) -> None:
    """Persist direct-child directory creation where directory fsync is supported."""
    if root.descriptor is None:
        verify_workbench_workspace(root.workspace)
        return
    try:
        os.fsync(root.descriptor)
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
            raise
    verify_workbench_workspace(root.workspace)


def _sync_pinned_directory(directory: _PinnedDirectory) -> None:
    if directory.descriptor is None:
        verify_workbench_subdirectory(directory.pinned)
        return
    try:
        os.fsync(directory.descriptor)
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
            raise
    verify_workbench_subdirectory(directory.pinned)


def _release_stage_descriptor(descriptor: int | None, *, committed: bool) -> None:
    """Release a stage without deleting a same-user path replacement.

    Windows stages are delete-on-close.  A named POSIX stage that did not publish is
    zeroed through its still-owned descriptor before close.  Its harmless zero-byte
    tombstone may remain because POSIX has no descriptor-conditional unlink primitive.
    Once a hard-link may have committed, truncation is forbidden because it would also
    truncate the immutable destination.
    """
    if descriptor is None:
        return
    try:
        if sys.platform != "win32" and not committed:
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _clone_stage_on_darwin(
    source_descriptor: int,
    destination_directory_descriptor: int,
    destination_name: str,
) -> None:
    import ctypes

    libc: Any = ctypes.CDLL(None, use_errno=True)
    try:
        clone = libc.fclonefileat
    except AttributeError:
        raise OSError(errno.ENOTSUP, "pinned publication is unavailable") from None
    clone.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
    clone.restype = ctypes.c_int
    if (
        clone(
            source_descriptor,
            destination_directory_descriptor,
            os.fsencode(destination_name),
            0,
        )
        == 0
    ):
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, "immutable destination already exists")
    raise OSError(error_number, "pinned immutable publication failed")


def _publish_stage_descriptor(
    descriptor: int,
    destination: _PinnedDirectory,
    record_name: str,
) -> None:
    """No-clobber from the owned stage descriptor, never its replaceable name."""
    if sys.platform.startswith("linux"):
        if destination.descriptor is None:
            raise OSError(errno.ENOTSUP, "pinned publication is unavailable")
        os.link(
            f"/proc/self/fd/{descriptor}",
            record_name,
            dst_dir_fd=destination.descriptor,
            follow_symlinks=True,
        )
    elif sys.platform == "darwin":
        if destination.descriptor is None:
            raise OSError(errno.ENOTSUP, "pinned publication is unavailable")
        _clone_stage_on_darwin(descriptor, destination.descriptor, record_name)
    elif sys.platform == "win32":
        _link_windows_descriptor(descriptor, destination, record_name)
    else:
        raise OSError(errno.ENOTSUP, "pinned publication is unavailable")


def _remove_empty_owned_group(
    record_root: WorkbenchSubdirectory,
    group: WorkbenchSubdirectory,
) -> None:
    """Best-effort rollback for a group created by an uncommitted publication."""
    try:
        verify_workbench_subdirectory(record_root)
        verify_workbench_subdirectory(group)
        with os.scandir(group.path) as entries:
            if any(entries):
                return
        with _pin_subdirectory(record_root) as pinned_root:
            if pinned_root.descriptor is not None:
                os.rmdir(group.name, dir_fd=pinned_root.descriptor)
            else:
                os.rmdir(group.path)
            _sync_pinned_directory(pinned_root)
    except (OSError, ValueError, UnsafeWorkbenchWorkspace, ImmutableWorkspaceRegistryUnavailable):
        return


class LockedImmutableWorkspaceRegistry:
    """One lock-scoped raw-byte view; instances expire with ``locked()``."""

    def __init__(
        self,
        store: ImmutableWorkspaceRegistry,
        active: list[bool],
        root: _PinnedWorkspaceRoot,
    ) -> None:
        self._store = store
        self._active = active
        self._root = root

    def _require_active(self) -> None:
        if not self._active[0]:
            raise RuntimeError("immutable registry transaction is closed")

    def discover(self) -> tuple[ImmutableWorkspaceRecord, ...]:
        """Read every bounded record in stable order or refuse the whole tree."""
        self._require_active()
        return self._store._discover_locked(self._root)

    def publish(self, group_id: str, record_id: str, document: bytes) -> None:
        """Atomically no-clobber one exact byte string into its immutable name."""
        self._require_active()
        self._store._publish_locked(self._root, group_id, record_id, document)


class ImmutableWorkspaceRegistry:
    """Generic, two-level immutable byte registry rooted in one pinned workspace."""

    def __init__(
        self,
        workspace: WorkbenchWorkspace,
        layout: ImmutableWorkspaceRegistryLayout,
    ) -> None:
        if not isinstance(workspace, WorkbenchWorkspace):
            raise TypeError("workspace must be a WorkbenchWorkspace")
        if not isinstance(layout, ImmutableWorkspaceRegistryLayout):
            raise TypeError("layout must be an ImmutableWorkspaceRegistryLayout")
        self.workspace = workspace
        self.layout = layout

    @contextmanager
    def locked(self) -> Iterator[LockedImmutableWorkspaceRegistry]:
        """Hold process and OS exclusion for one complete caller transaction."""
        active = [True]
        try:
            with _registry_lock(self.workspace, self.layout.lock_filename) as root:
                yield LockedImmutableWorkspaceRegistry(self, active, root)
        finally:
            active[0] = False

    def _record_root(
        self,
        root: _PinnedWorkspaceRoot,
        *,
        create: bool,
    ) -> WorkbenchSubdirectory | None:
        return _direct_subdirectory(root, self.layout.records_directory, create=create)

    def _staging_root(
        self,
        root: _PinnedWorkspaceRoot,
        *,
        create: bool,
    ) -> WorkbenchSubdirectory | None:
        return _direct_subdirectory(root, self.layout.staging_directory, create=create)

    def _discover_locked(
        self,
        root: _PinnedWorkspaceRoot,
    ) -> tuple[ImmutableWorkspaceRecord, ...]:
        record_root = self._record_root(root, create=False)
        staging_root = self._staging_root(root, create=False)
        if staging_root is not None:
            verify_workbench_subdirectory(staging_root)
        if record_root is None:
            return ()
        results: list[ImmutableWorkspaceRecord] = []
        with _pin_subdirectory(record_root) as pinned_root:
            group_names = _directory_names(pinned_root)
            if len(group_names) > self.layout.max_groups:
                raise ImmutableWorkspaceRegistryUnavailable()
            for group_id in group_names:
                if self.layout.group_pattern.fullmatch(group_id) is None:
                    raise ImmutableWorkspaceRegistryUnavailable()
                group = _direct_subdirectory(pinned_root, group_id, create=False)
                if group is None:
                    raise ImmutableWorkspaceRegistryUnavailable()
                with _pin_subdirectory(group) as pinned_group:
                    record_names = _directory_names(pinned_group)
                    if len(record_names) > self.layout.max_records_per_group:
                        raise ImmutableWorkspaceRegistryUnavailable()
                    for record_name in record_names:
                        match = self.layout.record_pattern.fullmatch(record_name)
                        if match is None:
                            raise ImmutableWorkspaceRegistryUnavailable()
                        record_id = match.group("record_id")
                        try:
                            document = _read_record(
                                pinned_group,
                                record_name,
                                limit=self.layout.max_record_bytes,
                            )
                        except (OSError, ValueError):
                            raise ImmutableWorkspaceRegistryUnavailable() from None
                        results.append(ImmutableWorkspaceRecord(group_id, record_id, document))
        return tuple(sorted(results, key=lambda item: (item.group_id, item.record_id)))

    def _validate_publication_arguments(
        self,
        group_id: str,
        record_id: str,
        document: bytes,
    ) -> str:
        if not isinstance(group_id, str) or self.layout.group_pattern.fullmatch(group_id) is None:
            raise ValueError("immutable registry group identifier is invalid")
        if not isinstance(record_id, str):
            raise ValueError("immutable registry record identifier is invalid")
        record_name: str | None = None
        # The layout owns the suffix and any surrounding syntax.  Find the sole
        # filename spelling that round-trips through its named record-id capture.
        for candidate in (record_id, f"{record_id}.json"):
            match = self.layout.record_pattern.fullmatch(candidate)
            if match is not None and match.group("record_id") == record_id:
                record_name = candidate
                break
        if record_name is None:
            raise ValueError("immutable registry record identifier is invalid")
        if not isinstance(document, bytes):
            raise TypeError("immutable registry document must be bytes")
        if not 1 <= len(document) <= self.layout.max_record_bytes:
            raise ValueError("immutable registry document exceeds its size limit")
        return record_name

    def _publish_locked(
        self,
        root: _PinnedWorkspaceRoot,
        group_id: str,
        record_id: str,
        document: bytes,
    ) -> None:
        record_name = self._validate_publication_arguments(group_id, record_id, document)
        record_root: WorkbenchSubdirectory | None = None
        staging_root: WorkbenchSubdirectory | None = None
        group: WorkbenchSubdirectory | None = None
        created_group = False
        setup_failure = False
        try:
            record_root = self._record_root(root, create=True)
            staging_root = self._staging_root(root, create=True)
            assert record_root is not None and staging_root is not None
            _sync_workspace_root(root)
            with _pin_subdirectory(record_root) as pinned_root:
                existing_group = _direct_subdirectory(pinned_root, group_id, create=False)
                if existing_group is None:
                    group_names = _directory_names(pinned_root)
                    if len(group_names) >= self.layout.max_groups:
                        raise ImmutableWorkspaceRegistryConflict()
                    group = _direct_subdirectory(pinned_root, group_id, create=True)
                    created_group = True
                else:
                    group = existing_group
                _sync_pinned_directory(pinned_root)
        except ImmutableWorkspaceRegistryConflict:
            raise
        except (Exception, KeyboardInterrupt):
            setup_failure = True
        if setup_failure:
            if created_group and record_root is not None and group is not None:
                _remove_empty_owned_group(record_root, group)
            raise ImmutableWorkspacePublicationError(False)
        assert record_root is not None and staging_root is not None and group is not None
        stage_name = f"stage-{os.urandom(16).hex()}.tmp"
        stage_descriptor: int | None = None
        potentially_committed = False
        try:
            with _pin_subdirectory(staging_root) as staging, _pin_subdirectory(group) as target:
                if sys.platform == "win32":
                    stage_descriptor = _open_windows_stage(staging.path / stage_name)
                else:
                    if staging.descriptor is None:
                        raise OSError(errno.EBADF, "staging directory descriptor is unavailable")
                    stage_descriptor = os.open(
                        stage_name,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=staging.descriptor,
                    )
                view = memoryview(document)
                while view:
                    written = os.write(stage_descriptor, view)
                    if written <= 0:
                        raise OSError("staging write did not make progress")
                    view = view[written:]
                os.fsync(stage_descriptor)
                os.lseek(stage_descriptor, 0, os.SEEK_SET)
                if os.read(stage_descriptor, len(document) + 1) != document:
                    raise OSError("staging verification failed")
                # An interrupt can land after the OS commits but before the next
                # Python statement, so the syscall boundary starts ambiguous.
                potentially_committed = True
                try:
                    _publish_stage_descriptor(stage_descriptor, target, record_name)
                except FileExistsError:
                    potentially_committed = False
                    raise
                except BaseException:
                    # A synchronous refusal before the no-clobber syscall commits is
                    # provably pre-commit when the pinned destination is still absent.
                    # Exact committed bytes prove the opposite; unreadable or changed
                    # evidence remains conservatively ambiguous.
                    try:
                        potentially_committed = (
                            _read_record(
                                target,
                                record_name,
                                limit=self.layout.max_record_bytes,
                            )
                            == document
                        )
                    except FileNotFoundError:
                        potentially_committed = False
                    except (OSError, ValueError):
                        potentially_committed = True
                    raise
                if (
                    _read_record(target, record_name, limit=self.layout.max_record_bytes)
                    != document
                ):
                    raise OSError("published record verification failed")
                if target.descriptor is not None:
                    _sync_pinned_directory(target)
                # Never unlink a replaceable POSIX stage name. Linux retains the
                # verified hard-link; Darwin clones from the descriptor and can zero
                # its independent stage on release. Windows is delete-on-close.
                _sync_pinned_directory(staging)
            assert stage_descriptor is not None
            _release_stage_descriptor(
                stage_descriptor,
                committed=sys.platform.startswith("linux"),
            )
            stage_descriptor = None
            # Re-open after the Windows delete-on-close transition and after the
            # POSIX directory sync.  A mismatch is a committed-but-untrusted error.
            with _pin_subdirectory(group) as target:
                if (
                    _read_record(target, record_name, limit=self.layout.max_record_bytes)
                    != document
                ):
                    raise OSError("committed record verification failed")
        except FileExistsError:
            with suppress(OSError):
                _release_stage_descriptor(stage_descriptor, committed=False)
            if created_group:
                _remove_empty_owned_group(record_root, group)
            raise ImmutableWorkspaceRegistryConflict() from None
        except ImmutableWorkspaceRegistryConflict:
            raise
        except (Exception, KeyboardInterrupt):
            with suppress(OSError):
                _release_stage_descriptor(
                    stage_descriptor,
                    committed=potentially_committed,
                )
            if created_group and not potentially_committed:
                _remove_empty_owned_group(record_root, group)
            raise ImmutableWorkspacePublicationError(potentially_committed) from None


__all__ = [
    "ImmutableWorkspacePublicationError",
    "ImmutableWorkspaceRecord",
    "ImmutableWorkspaceRegistry",
    "ImmutableWorkspaceRegistryConflict",
    "ImmutableWorkspaceRegistryLayout",
    "ImmutableWorkspaceRegistryUnavailable",
    "LockedImmutableWorkspaceRegistry",
]
