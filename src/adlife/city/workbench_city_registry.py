"""Immutable, restart-safe workspace storage for operator-supplied city packs."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import ValidationError

from adlife.city.workbench_security import (
    WorkbenchJsonContentError,
    WorkbenchJsonError,
    strict_json_object,
)
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    WorkbenchSubdirectory,
    WorkbenchWorkspace,
    find_workbench_subdirectory,
    prepare_workbench_subdirectory,
    verify_workbench_subdirectory,
    verify_workbench_workspace,
)
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.identifiers import WINDOWS_RESERVED_RUN_IDS
from adlife.core.domain.serialization import DocumentNotSerialisable, canonical_json

MAX_CITY_PACK_UPLOAD_BYTES = 4_194_304
MAX_WORKSPACE_CITY_PACKS = 50

_RECORD_DIRECTORY = "city-packs"
_STAGING_DIRECTORY = "city-pack-staging"
_LOCK_FILE = ".city-pack-registry.lock"
_WINDOWS_ANCHOR_RETRY_SECONDS = 10.0
_WINDOWS_ANCHOR_RETRY_INTERVAL_SECONDS = 0.01
_RECORD_NAME = re.compile(r"^(?P<city_id>[a-z0-9][a-z0-9-]{0,79})\.json$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LINUX_TMPFILE_UNSUPPORTED_ERRNOS = frozenset({errno.EOPNOTSUPP, errno.EISDIR, errno.ENOENT})
_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[tuple[str, int | None, int | None], threading.RLock] = {}
_strict_stored_json_object = strict_json_object


class MalformedCityPackUpload(ValueError):
    """The raw upload is not one unambiguous, bounded JSON object."""

    def __init__(self, *_ignored: object) -> None:
        super().__init__("city pack upload is malformed")


class InvalidCityPackUpload(ValueError):
    """The parsed upload is not a valid portable CityPack V2 document."""

    def __init__(self, *_ignored: object) -> None:
        super().__init__("city pack upload failed validation")


class CityPackUploadTooLarge(ValueError):
    """The raw or canonical upload exceeds the one published byte limit."""

    def __init__(self, *_ignored: object) -> None:
        super().__init__("city pack upload exceeds its size limit")


class CityPackRegistrationConflict(ValueError):
    """The identifier, immutable destination, or registry capacity conflicts."""

    def __init__(self, *_ignored: object) -> None:
        super().__init__("city pack registration conflicts with workspace state")


class WorkspaceCityRegistryError(RuntimeError):
    """The workspace registry cannot be read or trusted as a whole."""

    def __init__(self, *_ignored: object) -> None:
        super().__init__("workspace city registry is unavailable")


class CityPackPublicationError(RuntimeError):
    """Publication failed, with an explicit durable-commit ambiguity bit."""

    def __init__(self, potentially_committed: bool) -> None:
        if type(potentially_committed) is not bool:
            raise TypeError("publication state must be a boolean")
        self.potentially_committed = potentially_committed
        message = (
            "city pack publication may already be committed"
            if potentially_committed
            else "city pack publication failed before commit"
        )
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _HeldDirectory:
    pinned: WorkbenchSubdirectory
    file_descriptor: int | None
    windows_handle: int | None

    @property
    def path(self) -> Path:
        return self.pinned.path


@dataclass(frozen=True, slots=True)
class _HeldRoot:
    workspace: WorkbenchWorkspace
    path: Path
    identity: tuple[int, int]
    file_descriptor: int | None
    windows_handle: int | None


_IdentityRelation = Literal["same", "different", "indeterminate"]


@dataclass(frozen=True, slots=True)
class _FileEvidence:
    identity: tuple[int, int]
    size: int
    modified_ns: int
    changed_ns: int
    digest: bytes


@dataclass(frozen=True, slots=True)
class _LoadedRecord:
    pack: CityPackV2
    evidence: _FileEvidence


@dataclass(slots=True)
class _PublicationState:
    stage_name: str | None = None
    stage_file: _StageFile | None = None
    stage_evidence: _FileEvidence | None = None
    stage_is_unnamed: bool = False
    potentially_committed: bool = False
    publication_completed: bool = False


@dataclass(slots=True)
class _LockState:
    lock_file: _StageFile | None = None


@dataclass(slots=True)
class _AnchorState:
    descriptor_owner: _FileOwner | None = None
    windows_owner: _OwnedWindowsHandle | None = None


class _FileOwner(Protocol):
    def fileno(self) -> int: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class _StageFile:
    stream: _FileOwner

    def fileno(self) -> int:
        return self.stream.fileno()

    def close(self) -> None:
        self.stream.close()

    def __del__(self) -> None:
        with suppress(BaseException):
            self.close()


class _CloseTicket:
    """Run one native close away from main-thread asynchronous interrupts."""

    def __init__(self, token: int, closer: Callable[[int], None]) -> None:
        self.token = token
        self._closer = closer
        self._done = threading.Event()
        self._running = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def _run(self) -> None:
        self._running.set()
        try:
            self._closer(self.token)
        except BaseException as error:
            self._error = error
        finally:
            self._done.set()

    def complete(self) -> None:
        deferred_interrupt: KeyboardInterrupt | None = None
        while not self._running.is_set() and not self._done.is_set():
            try:
                self._thread.start()
            except KeyboardInterrupt as error:
                deferred_interrupt = error
                continue
            except RuntimeError:
                # A start that was interrupted after dispatch still owns the
                # one thread; its running/done events are authoritative.
                if self._thread.ident is None and not self._running.is_set():
                    raise
                break
            else:
                break
        while not self._done.is_set():
            try:
                self._done.wait()
            except KeyboardInterrupt as error:
                deferred_interrupt = error
        if deferred_interrupt is not None:
            raise deferred_interrupt
        if self._error is not None:
            raise self._error


_ORIGINAL_OS_CLOSE = os.close


def _close_descriptor_token(descriptor: int) -> None:
    _ORIGINAL_OS_CLOSE(descriptor)


def _workspace_key(workspace: WorkbenchWorkspace) -> tuple[str, int | None, int | None]:
    return (os.path.normcase(os.fspath(workspace.root)), workspace.device, workspace.inode)


def _process_lock(workspace: WorkbenchWorkspace) -> threading.RLock:
    key = _workspace_key(workspace)
    with _LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _attributes(status: os.stat_result) -> int:
    return int(getattr(status, "st_file_attributes", 0))


def _unsafe_link(status: os.stat_result) -> bool:
    return stat.S_ISLNK(status.st_mode) or bool(_attributes(status) & _REPARSE_POINT)


def _usable_identity(status: os.stat_result) -> tuple[int | None, int | None]:
    device = int(status.st_dev)
    inode = int(status.st_ino)
    return (device or None, inode or None)


def _identity_relation(first: os.stat_result, second: os.stat_result) -> _IdentityRelation:
    first_identity = _usable_identity(first)
    second_identity = _usable_identity(second)
    if None in first_identity or None in second_identity:
        return "indeterminate"
    return "same" if first_identity == second_identity else "different"


def _same_file_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        _identity_relation(first, second) == "same"
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
        and first.st_ctime_ns == second.st_ctime_ns
    )


def _same_path_handle_snapshot(path_status: os.stat_result, handle_status: os.stat_result) -> bool:
    if sys.platform != "win32":
        return _same_file_snapshot(path_status, handle_status)
    # Named-path stat and CRT-handle fstat expose different ctime meanings on
    # Windows. The handle denies writes/deletion and bytes are re-read, so use
    # every stable field shared by those two interfaces.
    return (
        _identity_relation(path_status, handle_status) == "same"
        and path_status.st_size == handle_status.st_size
        and path_status.st_mtime_ns == handle_status.st_mtime_ns
    )


def _workspace_root(workspace: WorkbenchWorkspace) -> Path:
    try:
        return verify_workbench_workspace(workspace)
    except (OSError, UnsafeWorkbenchWorkspace, ValueError):
        raise WorkspaceCityRegistryError() from None


def _subdirectory(
    root: _HeldRoot,
    name: str,
    *,
    create: bool,
) -> WorkbenchSubdirectory | None:
    if name not in {_RECORD_DIRECTORY, _STAGING_DIRECTORY}:
        raise WorkspaceCityRegistryError()
    if root.file_descriptor is not None:
        _verify_held_root(root)
        try:
            status = os.stat(name, dir_fd=root.file_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                _verify_held_root(root)
                return None
            try:
                with suppress(FileExistsError):
                    os.mkdir(name, mode=0o700, dir_fd=root.file_descriptor)
                status = os.stat(name, dir_fd=root.file_descriptor, follow_symlinks=False)
            except OSError:
                raise WorkspaceCityRegistryError() from None
        except OSError:
            raise WorkspaceCityRegistryError() from None
        if _unsafe_link(status) or not stat.S_ISDIR(status.st_mode):
            raise WorkspaceCityRegistryError()
        device, inode = _required_identity(status)
        pinned = WorkbenchSubdirectory(
            workspace=root.workspace,
            name=name,
            path=root.path / name,
            device=device,
            inode=inode,
        )
        _verify_held_root(root)
        _verify_subdirectory(pinned)
        return pinned
    try:
        if create:
            return prepare_workbench_subdirectory(root.workspace, name)
        return find_workbench_subdirectory(root.workspace, name)
    except (OSError, UnsafeWorkbenchWorkspace, ValueError):
        raise WorkspaceCityRegistryError() from None


def _verify_subdirectory(pinned: WorkbenchSubdirectory) -> None:
    try:
        verify_workbench_subdirectory(pinned)
    except (OSError, UnsafeWorkbenchWorkspace, ValueError):
        raise WorkspaceCityRegistryError() from None


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _NATIVE_KERNEL32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    _NATIVE_CLOSE_HANDLE = _NATIVE_KERNEL32.CloseHandle
    _NATIVE_CLOSE_HANDLE.argtypes = (wintypes.HANDLE,)
    _NATIVE_CLOSE_HANDLE.restype = wintypes.BOOL

    def _close_windows_token(handle: int) -> None:
        if not _NATIVE_CLOSE_HANDLE(wintypes.HANDLE(handle)):
            raise OSError(ctypes.get_last_error(), "filesystem handle could not be closed")

    class _OwnedWindowsHandle(ctypes.c_void_p):
        _close_ticket: _CloseTicket | None

        def handle(self) -> int:
            value = self.value
            if value is None or getattr(self, "_close_ticket", None) is not None:
                raise OSError(errno.EBADF, "filesystem handle is closed")
            return int(value)

        def close(self) -> None:
            value = self.value
            if value is None:
                return
            ticket = cast(_CloseTicket | None, getattr(self, "_close_ticket", None))
            if ticket is None:
                ticket = _CloseTicket(int(value), _close_windows_token)
                self._close_ticket = ticket
            try:
                ticket.complete()
            finally:
                if ticket.done:
                    self.value = None
                    self._close_ticket = None

        def __del__(self) -> None:
            with suppress(BaseException):
                self.close()

    class _OwnedCrtDescriptor:
        """A CRT descriptor whose storage exists before the opening call."""

        __slots__ = ("_close_ticket", "_value")

        def __init__(self) -> None:
            self._value = ctypes.c_int(-1)
            self._close_ticket: _CloseTicket | None = None

        def fileno(self) -> int:
            descriptor = int(self._value.value)
            if descriptor < 0 or self._close_ticket is not None:
                raise OSError(errno.EBADF, "filesystem descriptor is closed")
            return descriptor

        def close(self) -> None:
            descriptor = int(self._value.value)
            if descriptor < 0:
                return
            ticket = self._close_ticket
            if ticket is None:
                ticket = _CloseTicket(descriptor, _close_descriptor_token)
                self._close_ticket = ticket
            try:
                ticket.complete()
            finally:
                if ticket.done:
                    self._value.value = -1
                    self._close_ticket = None

        def __del__(self) -> None:
            with suppress(BaseException):
                self.close()

    def _windows_create_file(
        path: Path,
        *,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags: int,
    ) -> _OwnedWindowsHandle:
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
        create_file.restype = _OwnedWindowsHandle
        handle = cast(
            _OwnedWindowsHandle,
            create_file(
                os.fspath(path),
                desired_access,
                share_mode,
                None,
                creation_disposition,
                flags | 0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
                None,
            ),
        )
        if handle.value == ctypes.c_void_p(-1).value:
            error_number = ctypes.get_last_error()
            handle.value = None
            raise OSError(error_number, "filesystem handle could not be opened")
        return handle

    def _open_windows_directory(
        path: Path,
        state: _AnchorState | None = None,
    ) -> _OwnedWindowsHandle:
        owner = _windows_create_file(
            path,
            # DELETE access plus no FILE_SHARE_DELETE pins the directory's own
            # parent entry against rename/removal for the handle lifetime.
            desired_access=0x0080 | 0x00010000,  # FILE_READ_ATTRIBUTES | DELETE
            share_mode=0x00000001 | 0x00000002,  # share read/write, not delete
            creation_disposition=3,  # OPEN_EXISTING
            flags=0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
        )
        if state is not None:
            state.windows_owner = owner
        return owner

    def _close_windows_handle(handle: _OwnedWindowsHandle) -> None:
        handle.close()

    def _open_windows_lock(path: Path) -> _FileOwner:
        return _open_windows_owned_file(
            path,
            flags=(
                os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | 0x0080  # _O_NOINHERIT
            ),
            share_mode=0x0040,  # _SH_DENYNO; OS locking provides exclusion
            mode=0o600,
        )

    def _open_windows_owned_file(
        path: Path,
        *,
        flags: int,
        share_mode: int,
        mode: int = 0,
    ) -> _FileOwner:
        owner = _OwnedCrtDescriptor()
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
        try:
            result = int(
                open_secure(
                    ctypes.byref(owner._value),
                    os.fspath(path),
                    flags,
                    share_mode,
                    mode,
                )
            )
        except BaseException:
            with suppress(BaseException):
                owner.close()
            raise
        if result != 0:
            with suppress(BaseException):
                owner.close()
            raise OSError(result, "filesystem file could not be opened")
        return owner

    def _open_platform_record(directory: _HeldDirectory, name: str) -> _FileOwner:
        return _open_windows_owned_file(
            directory.path / name,
            flags=(
                os.O_RDONLY | getattr(os, "O_BINARY", 0) | 0x0080  # _O_NOINHERIT
            ),
            share_mode=0x0020,  # _SH_DENYWR
        )

    def _open_platform_stage(
        directory: _HeldDirectory,
        name: str,
        operation: _PublicationState,
    ) -> _StageFile:
        owner = _open_windows_owned_file(
            directory.path / name,
            flags=(
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | 0x0040  # _O_TEMPORARY: delete this exact file on close
                | 0x0080  # _O_NOINHERIT
            ),
            share_mode=0x0020,  # _SH_DENYWR
            mode=0o600,
        )
        stage_file = _StageFile(owner)
        operation.stage_file = stage_file
        return stage_file

    def _link_windows_descriptor(
        descriptor: int,
        records: _HeldDirectory,
        record_name: str,
    ) -> None:
        root_handle = records.windows_handle
        if root_handle is None:
            raise OSError(errno.EBADF, "pinned directory handle is unavailable")

        class _ReplaceOrFlags(ctypes.Union):
            _fields_ = (
                ("replace_if_exists", ctypes.c_ubyte),
                ("flags", wintypes.ULONG),
            )

        class _FileLinkInfo(ctypes.Structure):
            _fields_ = (
                ("replace_or_flags", _ReplaceOrFlags),
                ("root_directory", wintypes.HANDLE),
                ("file_name_length", wintypes.DWORD),
                ("file_name", ctypes.c_wchar * len(record_name)),
            )

        class _IoStatusBlock(ctypes.Structure):
            _fields_ = (
                ("status", ctypes.c_void_p),
                ("information", ctypes.c_size_t),
            )

        source_handle = msvcrt.get_osfhandle(descriptor)
        if source_handle == -1:
            raise OSError(errno.EBADF, "staging file handle is unavailable")
        information = _FileLinkInfo(
            replace_or_flags=_ReplaceOrFlags(replace_if_exists=0),
            root_directory=wintypes.HANDLE(root_handle),
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
        if error_number in {80, 183}:  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
            raise FileExistsError(error_number, "city record already exists", record_name)
        raise OSError(error_number, "pinned publication failed", record_name)

    def _open_posix_path(
        _path: Path,
        *,
        flags: int,
        state: _AnchorState | None = None,
    ) -> _FileOwner:
        raise OSError("POSIX filesystem descriptors are unavailable")

    def _open_posix_owned_at(
        _directory_descriptor: int,
        _name: str,
        *,
        flags: int,
        mode: int = 0o600,
        state: _AnchorState | None = None,
    ) -> _FileOwner:
        raise OSError("POSIX filesystem descriptors are unavailable")

    def _lock_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)

    def _unlock_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import ctypes
    import fcntl

    class _OwnedWindowsHandle:
        def handle(self) -> int:
            raise OSError("Windows directory handles are unavailable")

        def close(self) -> None:
            raise OSError("Windows record handles are unavailable")

    def _open_windows_directory(
        _path: Path,
        _state: _AnchorState | None = None,
    ) -> _OwnedWindowsHandle:
        raise OSError("Windows directory handles are unavailable")

    def _close_windows_handle(_handle: _OwnedWindowsHandle) -> None:
        raise OSError("Windows directory handles are unavailable")

    def _open_windows_lock(_path: Path) -> _FileOwner:
        raise OSError("Windows lock handles are unavailable")

    class _OwnedPosixDescriptor(ctypes.c_int):
        _close_ticket: _CloseTicket | None

        def fileno(self) -> int:
            descriptor = int(self.value)
            if descriptor < 0 or getattr(self, "_close_ticket", None) is not None:
                raise OSError(errno.EBADF, "filesystem descriptor is closed")
            return descriptor

        def close(self) -> None:
            descriptor = int(self.value)
            if descriptor < 0:
                return
            ticket = cast(_CloseTicket | None, getattr(self, "_close_ticket", None))
            if ticket is None:
                ticket = _CloseTicket(descriptor, _close_descriptor_token)
                self._close_ticket = ticket
            try:
                ticket.complete()
            finally:
                if ticket.done:
                    self.value = -1
                    self._close_ticket = None

        def __del__(self) -> None:
            with suppress(BaseException):
                self.close()

    def _open_posix_path(
        path: Path,
        *,
        flags: int,
        state: _AnchorState | None = None,
    ) -> _FileOwner:
        libc: Any = ctypes.CDLL(None, use_errno=True)
        open_file = libc.open
        open_file.argtypes = (ctypes.c_char_p, ctypes.c_int)
        open_file.restype = _OwnedPosixDescriptor
        ctypes.set_errno(0)
        owner = cast(_OwnedPosixDescriptor, open_file(os.fsencode(path), flags))
        if owner.value < 0:
            error_number = ctypes.get_errno()
            owner.value = -1
            raise OSError(error_number, "filesystem path could not be opened")
        if state is not None:
            state.descriptor_owner = owner
        return owner

    def _open_posix_owned_at(
        directory_descriptor: int,
        name: str,
        *,
        flags: int,
        mode: int = 0o600,
        state: _AnchorState | None = None,
    ) -> _FileOwner:
        libc: Any = ctypes.CDLL(None, use_errno=True)
        open_at = libc.openat
        # openat is variadic; declare only its fixed arguments (required on
        # Apple arm64) and pass the creation mode explicitly below.
        open_at.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
        open_at.restype = _OwnedPosixDescriptor
        ctypes.set_errno(0)
        owner = cast(
            _OwnedPosixDescriptor,
            open_at(directory_descriptor, os.fsencode(name), flags, ctypes.c_uint(mode)),
        )
        if owner.value < 0:
            error_number = ctypes.get_errno()
            owner.value = -1
            raise OSError(error_number, "filesystem file could not be opened", name)
        if state is not None:
            state.descriptor_owner = owner
        return owner

    def _open_posix_owned_file(
        directory: _HeldDirectory,
        name: str,
        *,
        flags: int,
        mode: int = 0o600,
    ) -> _FileOwner:
        descriptor = directory.file_descriptor
        if descriptor is None:
            raise OSError(errno.EBADF, "pinned directory descriptor is unavailable")
        return _open_posix_owned_at(
            descriptor,
            name,
            flags=flags,
            mode=mode,
        )

    def _open_platform_record(directory: _HeldDirectory, name: str) -> _FileOwner:
        return _open_posix_owned_file(
            directory,
            name,
            flags=(os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)),
        )

    def _open_platform_stage(
        directory: _HeldDirectory,
        name: str,
        operation: _PublicationState,
    ) -> _StageFile:
        if sys.platform.startswith("linux"):
            temporary_flag = getattr(os, "O_TMPFILE", 0)
            if temporary_flag != 0:
                try:
                    owner = _open_posix_owned_file(
                        directory,
                        ".",
                        flags=(os.O_RDWR | temporary_flag | getattr(os, "O_CLOEXEC", 0)),
                        mode=0o600,
                    )
                except OSError as error:
                    if error.errno not in _LINUX_TMPFILE_UNSUPPORTED_ERRNOS:
                        raise
                else:
                    operation.stage_is_unnamed = True
                    stage_file = _StageFile(owner)
                    operation.stage_file = stage_file
                    return stage_file
            owner = _open_posix_owned_file(
                directory,
                name,
                flags=(
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                ),
                mode=0o600,
            )
        elif sys.platform == "darwin":
            owner = _open_posix_owned_file(
                directory,
                name,
                flags=(
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                ),
                mode=0o600,
            )
        else:
            raise OSError(errno.ENOTSUP, "unnamed staging is unavailable")
        stage_file = _StageFile(owner)
        operation.stage_file = stage_file
        return stage_file

    def _link_windows_descriptor(
        _descriptor: int,
        _records: _HeldDirectory,
        _record_name: str,
    ) -> None:
        raise OSError("Windows descriptor publication is unavailable")

    def _lock_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.flock(descriptor, fcntl.LOCK_EX)

    def _unlock_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _required_identity(status: os.stat_result) -> tuple[int, int]:
    device, inode = _usable_identity(status)
    if device is None or inode is None:
        raise WorkspaceCityRegistryError()
    return device, inode


def _open_windows_root(path: Path, state: _AnchorState) -> _OwnedWindowsHandle:
    deadline = time.monotonic() + _WINDOWS_ANCHOR_RETRY_SECONDS
    while True:
        try:
            return _open_windows_directory(path, state)
        except OSError as error:
            if error.errno not in {32, 33}:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(_WINDOWS_ANCHOR_RETRY_INTERVAL_SECONDS, remaining))


def _verify_held_root(root: _HeldRoot) -> None:
    current_path = _workspace_root(root.workspace)
    if current_path != root.path:
        raise WorkspaceCityRegistryError()
    try:
        current = os.lstat(root.path)
    except OSError:
        raise WorkspaceCityRegistryError() from None
    if _unsafe_link(current) or not stat.S_ISDIR(current.st_mode):
        raise WorkspaceCityRegistryError()
    if _required_identity(current) != root.identity:
        raise WorkspaceCityRegistryError()


@contextmanager
def _hold_workspace_root(workspace: WorkbenchWorkspace) -> Iterator[_HeldRoot]:
    path = _workspace_root(workspace)
    try:
        before = os.lstat(path)
    except OSError:
        raise WorkspaceCityRegistryError() from None
    if _unsafe_link(before) or not stat.S_ISDIR(before.st_mode):
        raise WorkspaceCityRegistryError()
    identity = _required_identity(before)
    file_descriptor: int | None = None
    windows_handle: int | None = None
    state = _AnchorState()
    try:
        if sys.platform == "win32":
            windows_owner = _open_windows_root(path, state)
            windows_handle = windows_owner.handle()
        else:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor_owner = _open_posix_path(path, flags=flags, state=state)
            file_descriptor = descriptor_owner.fileno()
            opened = os.fstat(file_descriptor)
            if not stat.S_ISDIR(opened.st_mode) or _identity_relation(before, opened) != "same":
                raise OSError("workspace root identity changed")
        after = os.lstat(path)
        if _unsafe_link(after) or not stat.S_ISDIR(after.st_mode):
            raise OSError("workspace root identity changed")
        if _required_identity(after) != identity:
            raise OSError("workspace root identity changed")
        held = _HeldRoot(workspace, path, identity, file_descriptor, windows_handle)
        _verify_held_root(held)
        yield held
        _verify_held_root(held)
    except WorkspaceCityRegistryError:
        raise
    except OSError:
        raise WorkspaceCityRegistryError() from None
    finally:
        if state.descriptor_owner is not None:
            with suppress(OSError):
                state.descriptor_owner.close()
        if state.windows_owner is not None:
            with suppress(OSError):
                _close_windows_handle(state.windows_owner)


@contextmanager
def _hold_directory(root: _HeldRoot, pinned: WorkbenchSubdirectory) -> Iterator[_HeldDirectory]:
    _verify_held_root(root)
    _verify_subdirectory(pinned)
    expected = (pinned.device, pinned.inode)
    if None in expected:
        raise WorkspaceCityRegistryError()
    file_descriptor: int | None = None
    windows_handle: int | None = None
    state = _AnchorState()
    try:
        if sys.platform == "win32":
            windows_owner = _open_windows_directory(pinned.path, state)
            windows_handle = windows_owner.handle()
        else:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            if root.file_descriptor is None:
                raise OSError("workspace root handle is unavailable")
            descriptor_owner = _open_posix_owned_at(
                root.file_descriptor,
                pinned.name,
                flags=flags,
                state=state,
            )
            file_descriptor = descriptor_owner.fileno()
            status = os.fstat(file_descriptor)
            if not stat.S_ISDIR(status.st_mode):
                raise OSError("pinned path is not a directory")
            if _usable_identity(status) != expected:
                raise OSError("pinned directory identity changed")
        _verify_held_root(root)
        _verify_subdirectory(pinned)
        yield _HeldDirectory(pinned, file_descriptor, windows_handle)
        _verify_held_root(root)
        _verify_subdirectory(pinned)
    except WorkspaceCityRegistryError:
        raise
    except OSError:
        raise WorkspaceCityRegistryError() from None
    finally:
        if state.descriptor_owner is not None:
            with suppress(OSError):
                state.descriptor_owner.close()
        if state.windows_owner is not None:
            with suppress(OSError):
                _close_windows_handle(state.windows_owner)


def _root_entry_status(root: _HeldRoot, name: str) -> os.stat_result:
    try:
        if root.file_descriptor is not None:
            return os.stat(name, dir_fd=root.file_descriptor, follow_symlinks=False)
        return os.lstat(root.path / name)
    except FileNotFoundError:
        raise
    except OSError:
        raise WorkspaceCityRegistryError() from None


def _open_lock_file(root: _HeldRoot, state: _LockState) -> _StageFile:
    _verify_held_root(root)
    try:
        before = _root_entry_status(root, _LOCK_FILE)
    except FileNotFoundError:
        before = None
    if before is not None and (
        _unsafe_link(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
    ):
        raise WorkspaceCityRegistryError()
    try:
        if sys.platform == "win32":
            owner = _open_windows_lock(root.path / _LOCK_FILE)
        else:
            flags = (
                os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            assert root.file_descriptor is not None
            owner = _open_posix_owned_at(
                root.file_descriptor,
                _LOCK_FILE,
                flags=flags,
                mode=0o600,
            )
    except OSError:
        raise WorkspaceCityRegistryError() from None
    lock_file = _StageFile(owner)
    state.lock_file = lock_file
    descriptor = lock_file.fileno()
    try:
        opened = os.fstat(descriptor)
        observed = _root_entry_status(root, _LOCK_FILE)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _unsafe_link(observed)
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or _identity_relation(opened, observed) != "same"
            or (before is not None and _identity_relation(before, opened) != "same")
        ):
            raise WorkspaceCityRegistryError()
        if opened.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, b"\0") != 1:
                raise OSError("lock initialization was incomplete")
            os.fsync(descriptor)
        elif opened.st_size != 1:
            raise WorkspaceCityRegistryError()
        final_status = os.fstat(descriptor)
        final_observed = _root_entry_status(root, _LOCK_FILE)
        if (
            final_status.st_nlink != 1
            or final_observed.st_nlink != 1
            or _identity_relation(final_status, final_observed) != "same"
        ):
            raise WorkspaceCityRegistryError()
        _verify_held_root(root)
        return lock_file
    except BaseException:
        with suppress(BaseException):
            lock_file.close()
        state.lock_file = None
        raise


def _verify_lock_file(root: _HeldRoot, descriptor: int) -> None:
    try:
        opened = os.fstat(descriptor)
        observed = _root_entry_status(root, _LOCK_FILE)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size != 1
            or opened.st_nlink != 1
            or _unsafe_link(observed)
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or _identity_relation(opened, observed) != "same"
        ):
            raise WorkspaceCityRegistryError()
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 1) != b"\0":
            raise WorkspaceCityRegistryError()
    except OSError:
        raise WorkspaceCityRegistryError() from None
    _verify_held_root(root)


def _close_lock_state(state: _LockState) -> None:
    lock_file = state.lock_file
    if lock_file is None:
        return
    lock_file.close()
    state.lock_file = None


@contextmanager
def _locked_registry_descriptor(root: _HeldRoot, state: _LockState) -> Iterator[_HeldRoot]:
    lock_file = state.lock_file
    if lock_file is None:
        raise WorkspaceCityRegistryError()
    descriptor = lock_file.fileno()
    locked = False
    try:
        try:
            _lock_descriptor(descriptor)
            locked = True
        except OSError:
            raise WorkspaceCityRegistryError() from None
        _verify_lock_file(root, descriptor)
        yield root
        _verify_lock_file(root, descriptor)
    finally:
        if locked:
            with suppress(OSError):
                _unlock_descriptor(descriptor)
        with suppress(OSError):
            _close_lock_state(state)


@contextmanager
def _registry_lock(workspace: WorkbenchWorkspace) -> Iterator[_HeldRoot]:
    process_lock = _process_lock(workspace)
    with process_lock:
        state = _LockState()
        if sys.platform != "win32":
            try:
                with _hold_workspace_root(workspace) as root:
                    _open_lock_file(root, state)
                    with _locked_registry_descriptor(root, state) as locked_root:
                        yield locked_root
            finally:
                with suppress(BaseException):
                    _close_lock_state(state)
            return

        try:
            with _hold_workspace_root(workspace) as anchored_root:
                _open_lock_file(anchored_root, state)
                root = _HeldRoot(
                    workspace=anchored_root.workspace,
                    path=anchored_root.path,
                    identity=anchored_root.identity,
                    file_descriptor=None,
                    windows_handle=None,
                )
        except BaseException:
            with suppress(BaseException):
                _close_lock_state(state)
            raise
        try:
            with _locked_registry_descriptor(root, state) as locked_root:
                yield locked_root
        finally:
            with suppress(BaseException):
                _close_lock_state(state)


def _scandir_directory(directory: _HeldDirectory) -> Any:
    target: int | Path = (
        directory.file_descriptor if directory.file_descriptor is not None else directory.path
    )
    return os.scandir(target)


def _directory_names(directory: _HeldDirectory) -> tuple[str, ...]:
    _verify_subdirectory(directory.pinned)
    names: list[str] = []
    try:
        with _scandir_directory(directory) as entries:
            for entry in entries:
                name = entry.name
                if not isinstance(name, str):
                    raise WorkspaceCityRegistryError()
                names.append(name)
                if len(names) > MAX_WORKSPACE_CITY_PACKS:
                    raise WorkspaceCityRegistryError()
    except WorkspaceCityRegistryError:
        raise
    except OSError:
        raise WorkspaceCityRegistryError() from None
    _verify_subdirectory(directory.pinned)
    return tuple(sorted(names))


def _entry_status(directory: _HeldDirectory, name: str) -> os.stat_result:
    try:
        if directory.file_descriptor is not None:
            return os.stat(name, dir_fd=directory.file_descriptor, follow_symlinks=False)
        return os.lstat(directory.path / name)
    except FileNotFoundError:
        raise
    except OSError:
        raise WorkspaceCityRegistryError() from None


def _open_regular_file(
    directory: _HeldDirectory,
    name: str,
    *,
    owners: dict[str, _StageFile] | None = None,
) -> _StageFile:
    owned: _StageFile | None = None
    try:
        owned = _StageFile(_open_platform_record(directory, name))
        if owners is not None:
            owners[name] = owned
        return owned
    except BaseException as error:
        if owned is not None and (owners is None or owners.get(name) is not owned):
            with suppress(BaseException):
                owned.close()
        if isinstance(error, OSError):
            raise WorkspaceCityRegistryError() from None
        raise


def _open_stage_file(
    directory: _HeldDirectory,
    name: str,
    mode: Literal["rb", "xb"],
    operation: _PublicationState,
) -> _StageFile:
    _verify_subdirectory(directory.pinned)
    if Path(name).name != name or name in {"", ".", ".."}:
        raise WorkspaceCityRegistryError()
    if mode == "rb" and operation.stage_file is not None:
        return operation.stage_file
    owned: _StageFile | None = None
    try:
        if mode == "xb":
            owned = _open_platform_stage(directory, name, operation)
        else:
            owned = _StageFile(stream=_open_platform_record(directory, name))
            # Publish ownership before any validation that may be interrupted.
            operation.stage_file = owned
        opened = os.fstat(owned.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise WorkspaceCityRegistryError()
        if not operation.stage_is_unnamed:
            observed = _entry_status(directory, name)
            if (
                _unsafe_link(observed)
                or not stat.S_ISREG(observed.st_mode)
                or _identity_relation(opened, observed) != "same"
            ):
                raise WorkspaceCityRegistryError()
        else:
            try:
                _entry_status(directory, name)
            except FileNotFoundError:
                pass
            else:
                raise WorkspaceCityRegistryError()
        _verify_subdirectory(directory.pinned)
        return owned
    except BaseException:
        if owned is not None and operation.stage_file is not owned:
            with suppress(BaseException):
                owned.close()
        raise


def _file_evidence(status: os.stat_result, data: bytes) -> _FileEvidence:
    return _FileEvidence(
        identity=_required_identity(status),
        size=int(status.st_size),
        modified_ns=int(status.st_mtime_ns),
        changed_ns=int(status.st_ctime_ns),
        digest=hashlib.sha256(data).digest(),
    )


def _read_regular_file_with_evidence(
    directory: _HeldDirectory,
    name: str,
    *,
    limit: int,
    descriptor: _StageFile | None = None,
    sync: bool = False,
) -> tuple[bytes, _FileEvidence]:
    _verify_subdirectory(directory.pinned)
    try:
        before = _entry_status(directory, name)
    except FileNotFoundError:
        raise WorkspaceCityRegistryError() from None
    if _unsafe_link(before) or not stat.S_ISREG(before.st_mode):
        raise WorkspaceCityRegistryError()
    owns_descriptor = descriptor is None
    if descriptor is None:
        descriptor = _open_regular_file(directory, name)
    try:
        file_descriptor = descriptor.fileno()
        opened = os.fstat(file_descriptor)
        opened_path = _entry_status(directory, name)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_path_handle_snapshot(before, opened)
            or not _same_file_snapshot(before, opened_path)
            or not _same_path_handle_snapshot(opened_path, opened)
        ):
            raise WorkspaceCityRegistryError()
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(file_descriptor, min(remaining, 65_536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise WorkspaceCityRegistryError()
        if sync:
            os.fsync(file_descriptor)
        after_open = os.fstat(file_descriptor)
        if len(data) != opened.st_size or not _same_file_snapshot(opened, after_open):
            raise WorkspaceCityRegistryError()
        after_path = _entry_status(directory, name)
        if not _same_file_snapshot(before, after_path) or not _same_path_handle_snapshot(
            after_path, after_open
        ):
            raise WorkspaceCityRegistryError()
        evidence = _file_evidence(after_path, data)
    except (OSError, WorkspaceCityRegistryError):
        raise WorkspaceCityRegistryError() from None
    finally:
        if owns_descriptor:
            try:
                descriptor.close()
            except OSError:
                raise WorkspaceCityRegistryError() from None
    _verify_subdirectory(directory.pinned)
    return data, evidence


def _read_regular_file(
    directory: _HeldDirectory,
    name: str,
    *,
    limit: int,
) -> bytes:
    data, _evidence = _read_regular_file_with_evidence(directory, name, limit=limit)
    return data


def _read_pinned_file_with_evidence(
    stage_file: _StageFile,
    *,
    limit: int,
    sync: bool = False,
) -> tuple[bytes, _FileEvidence]:
    try:
        descriptor = stage_file.fileno()
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise WorkspaceCityRegistryError()
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise WorkspaceCityRegistryError()
        if sync:
            os.fsync(descriptor)
        after = os.fstat(descriptor)
        if len(data) != opened.st_size or not _same_file_snapshot(opened, after):
            raise WorkspaceCityRegistryError()
        evidence = _file_evidence(after, data)
    except (OSError, WorkspaceCityRegistryError):
        raise WorkspaceCityRegistryError() from None
    return data, evidence


def _portable_city_id(city_id: str) -> bool:
    return _RECORD_NAME.fullmatch(f"{city_id}.json") is not None and (
        city_id not in WINDOWS_RESERVED_RUN_IDS
    )


def _validate_city_payload(payload: dict[str, object]) -> CityPackV2:
    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != 2:
        raise InvalidCityPackUpload()
    try:
        normalized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        pack = CityPackV2.model_validate_json(normalized, strict=True)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise InvalidCityPackUpload() from None
    if not _portable_city_id(pack.city_id):
        raise InvalidCityPackUpload()
    return pack


def _canonical_pack_bytes(pack: CityPackV2) -> bytes:
    try:
        encoded = canonical_json(pack).encode("utf-8") + b"\n"
    except (DocumentNotSerialisable, UnicodeEncodeError, ValueError):
        raise InvalidCityPackUpload() from None
    if len(encoded) > MAX_CITY_PACK_UPLOAD_BYTES:
        raise CityPackUploadTooLarge()
    return encoded


def _load_record(
    directory: _HeldDirectory,
    name: str,
    *,
    descriptor: _StageFile | None = None,
) -> _LoadedRecord:
    match = _RECORD_NAME.fullmatch(name)
    if match is None or not _portable_city_id(match.group("city_id")):
        raise WorkspaceCityRegistryError()
    data, evidence = _read_regular_file_with_evidence(
        directory,
        name,
        limit=MAX_CITY_PACK_UPLOAD_BYTES,
        descriptor=descriptor,
    )
    if not data.endswith(b"\n") or data.endswith(b"\n\n"):
        raise WorkspaceCityRegistryError()
    try:
        payload = _strict_stored_json_object(
            data[:-1],
            max_body_bytes=MAX_CITY_PACK_UPLOAD_BYTES,
        )
        pack = _validate_city_payload(payload)
        canonical = _canonical_pack_bytes(pack)
    except (
        CityPackUploadTooLarge,
        InvalidCityPackUpload,
        WorkbenchJsonContentError,
        WorkbenchJsonError,
        TypeError,
        ValueError,
    ):
        raise WorkspaceCityRegistryError() from None
    if pack.city_id != match.group("city_id") or data != canonical:
        raise WorkspaceCityRegistryError()
    return _LoadedRecord(pack=pack, evidence=evidence)


def _revalidate_record(
    directory: _HeldDirectory,
    name: str,
    loaded: _LoadedRecord,
    *,
    descriptor: _StageFile | None = None,
) -> None:
    _data, evidence = _read_regular_file_with_evidence(
        directory,
        name,
        limit=MAX_CITY_PACK_UPLOAD_BYTES,
        descriptor=descriptor,
    )
    if evidence != loaded.evidence:
        raise WorkspaceCityRegistryError()


def _validate_open_record(
    directory: _HeldDirectory,
    name: str,
    descriptor: _StageFile,
    before: os.stat_result,
) -> None:
    try:
        opened = os.fstat(descriptor.fileno())
        observed = _entry_status(directory, name)
    except (OSError, FileNotFoundError, WorkspaceCityRegistryError):
        raise WorkspaceCityRegistryError() from None
    if (
        _unsafe_link(before)
        or not stat.S_ISREG(before.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or not _same_path_handle_snapshot(before, opened)
        or not _same_file_snapshot(before, observed)
        or not _same_path_handle_snapshot(observed, opened)
    ):
        raise WorkspaceCityRegistryError()


@contextmanager
def _hold_windows_record_snapshot(
    directory: _HeldDirectory,
    names: tuple[str, ...],
) -> Iterator[dict[str, _StageFile]]:
    descriptors: dict[str, _StageFile] = {}
    close_failed = False
    try:
        for name in names:
            try:
                before = _entry_status(directory, name)
            except FileNotFoundError:
                raise WorkspaceCityRegistryError() from None
            descriptor = _open_regular_file(directory, name, owners=descriptors)
            _validate_open_record(directory, name, descriptor, before)
        yield descriptors
        for name, descriptor in descriptors.items():
            try:
                observed = _entry_status(directory, name)
                opened = os.fstat(descriptor.fileno())
            except (OSError, FileNotFoundError, WorkspaceCityRegistryError):
                raise WorkspaceCityRegistryError() from None
            if not _same_path_handle_snapshot(observed, opened):
                raise WorkspaceCityRegistryError()
    finally:
        for descriptor in reversed(tuple(descriptors.values())):
            try:
                descriptor.close()
            except OSError:
                close_failed = True
        if close_failed:
            raise WorkspaceCityRegistryError()


def _load_complete_snapshot(
    records: _HeldDirectory,
    names: tuple[str, ...],
    descriptors: dict[str, _StageFile] | None = None,
) -> tuple[_LoadedRecord, ...]:
    loaded_records = tuple(
        _load_record(
            records,
            name,
            descriptor=None if descriptors is None else descriptors[name],
        )
        for name in names
    )
    for name, loaded in zip(names, loaded_records, strict=True):
        _revalidate_record(
            records,
            name,
            loaded,
            descriptor=None if descriptors is None else descriptors[name],
        )
    if _directory_names(records) != names:
        raise WorkspaceCityRegistryError()
    city_ids = tuple(loaded.pack.city_id for loaded in loaded_records)
    if len(city_ids) != len(set(city_ids)):
        raise WorkspaceCityRegistryError()
    return tuple(sorted(loaded_records, key=lambda loaded: loaded.pack.city_id))


def _discover_records_locked(records: _HeldDirectory | None) -> tuple[_LoadedRecord, ...]:
    if records is None:
        return ()
    names = _directory_names(records)
    if sys.platform != "win32":
        return _load_complete_snapshot(records, names)
    with _hold_windows_record_snapshot(records, names) as descriptors:
        return _load_complete_snapshot(records, names, descriptors)


def _discover_locked(records: _HeldDirectory | None) -> tuple[CityPackV2, ...]:
    return tuple(loaded.pack for loaded in _discover_records_locked(records))


def _write_staging_chunk(file_descriptor: int, data: memoryview) -> int:
    return os.write(file_descriptor, data)


def _flush_staging(file_descriptor: int) -> None:
    os.fsync(file_descriptor)


def _close_staging(stage_file: _StageFile) -> None:
    stage_file.close()


def _random_stage_name() -> str:
    return f".{secrets.token_hex(24)}.stage"


def _create_stage(
    directory: _HeldDirectory, operation: _PublicationState
) -> tuple[str, _StageFile]:
    for _attempt in range(16):
        name = _random_stage_name()
        operation.stage_name = name
        operation.stage_is_unnamed = False
        try:
            stage_file = _open_stage_file(directory, name, "xb", operation)
        except FileExistsError:
            operation.stage_name = None
            continue
        except OSError:
            raise CityPackPublicationError(False) from None
        return name, stage_file
    raise CityPackPublicationError(False)


def _release_stage_file(operation: _PublicationState) -> None:
    stage_file = operation.stage_file
    if stage_file is None:
        return
    _close_staging(stage_file)
    operation.stage_file = None


def _cleanup_publication_stage(
    directory: _HeldDirectory,
    operation: _PublicationState,
) -> None:
    with suppress(BaseException):
        _remove_owned_stage(directory, operation, required=False)
    operation.stage_evidence = None


def _release_ambiguous_stage(
    directory: _HeldDirectory,
    operation: _PublicationState,
) -> None:
    if sys.platform == "darwin":
        # Darwin publishes a clone, so the pinned staging inode is independent
        # of any destination that may have committed. Quarantine its payload
        # before releasing it; path-based unlink would risk deleting a
        # same-user replacement. Linux and Windows publish the pinned inode
        # itself, so ambiguous completion must only release the handle.
        _remove_owned_stage(directory, operation, required=False)
        return
    _release_stage_file(operation)


def _write_stage(
    directory: _HeldDirectory,
    content: bytes,
    operation: _PublicationState,
) -> None:
    try:
        _name, stage_file = _create_stage(directory, operation)
        descriptor = stage_file.fileno()
        remaining = memoryview(content)
        while remaining:
            written = _write_staging_chunk(descriptor, remaining)
            if type(written) is not int or written <= 0 or written > len(remaining):
                raise OSError("staging write did not make progress")
            remaining = remaining[written:]
        _flush_staging(descriptor)
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size != len(content):
            raise OSError("staging file size is invalid")
    except BaseException:
        _cleanup_publication_stage(directory, operation)
        raise


def _verify_staging(
    directory: _HeldDirectory,
    name: str,
    expected: bytes,
    operation: _PublicationState,
) -> None:
    stage_file = _open_stage_file(directory, name, "rb", operation)
    data, evidence = _read_pinned_file_with_evidence(
        stage_file,
        limit=MAX_CITY_PACK_UPLOAD_BYTES,
    )
    if data != expected:
        raise OSError("staging verification failed")
    operation.stage_evidence = evidence


def _link_stage(
    staging: _HeldDirectory,
    stage_name: str,
    records: _HeldDirectory,
    record_name: str,
    operation: _PublicationState,
) -> None:
    stage_file = operation.stage_file
    evidence = operation.stage_evidence
    if stage_file is None or evidence is None:
        raise OSError("verified staging identity is unavailable")
    descriptor = stage_file.fileno()
    _data, current_evidence = _read_pinned_file_with_evidence(
        stage_file,
        limit=MAX_CITY_PACK_UPLOAD_BYTES,
    )
    if current_evidence != evidence:
        raise OSError("verified staging identity changed")
    # Mark the operation ambiguous before entering the syscall so an interrupt
    # at its return boundary cannot erase a completed exclusive publication.
    # A synchronous OSError plus trustworthy destination evidence may prove
    # this was precommit; asynchronous interruption remains ambiguous.
    operation.potentially_committed = True
    _publish_pinned_stage(staging, stage_name, records, record_name, descriptor, operation)
    try:
        final = _entry_status(records, record_name)
        pinned = os.fstat(descriptor)
    except (OSError, FileNotFoundError, WorkspaceCityRegistryError):
        raise OSError("published record identity is unavailable") from None
    if sys.platform != "darwin" and _identity_relation(final, pinned) != "same":
        raise OSError("published record identity changed")


def _publish_pinned_stage(
    staging: _HeldDirectory,
    stage_name: str,
    records: _HeldDirectory,
    record_name: str,
    descriptor: int,
    operation: _PublicationState,
) -> None:
    if sys.platform.startswith("linux"):
        if records.file_descriptor is None:
            raise OSError(errno.ENOTSUP, "pinned publication is unavailable")
        os.link(
            f"/proc/self/fd/{descriptor}",
            record_name,
            dst_dir_fd=records.file_descriptor,
            follow_symlinks=True,
        )
    elif sys.platform == "darwin":
        if records.file_descriptor is None:
            raise OSError(errno.ENOTSUP, "pinned publication is unavailable")
        _clone_stage_on_darwin(descriptor, records.file_descriptor, record_name)
    elif sys.platform == "win32":
        _link_windows_descriptor(descriptor, records, record_name)
    else:
        raise OSError(errno.ENOTSUP, "pinned publication is unavailable")
    operation.publication_completed = True


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
        raise FileExistsError(error_number, "city record already exists", destination_name)
    raise OSError(error_number, "pinned publication failed", destination_name)


def _publication_relation(
    records: _HeldDirectory,
    record_name: str,
    operation: _PublicationState,
) -> _IdentityRelation:
    stage_file = operation.stage_file
    if stage_file is None:
        return "indeterminate"
    try:
        staged = os.fstat(stage_file.fileno())
        published = _entry_status(records, record_name)
    except FileNotFoundError:
        return "different"
    except (Exception, KeyboardInterrupt):
        return "indeterminate"
    relation = _identity_relation(staged, published)
    if relation != "same":
        return relation
    if (
        _unsafe_link(published)
        or not stat.S_ISREG(staged.st_mode)
        or not stat.S_ISREG(published.st_mode)
    ):
        return "indeterminate"
    return "same"


def _publication_destination_may_exist(
    records: _HeldDirectory,
    record_name: str,
) -> bool:
    try:
        _entry_status(records, record_name)
    except FileNotFoundError:
        return False
    except (Exception, KeyboardInterrupt):
        return True
    return True


def _verify_committed(
    stage_file: _StageFile,
    records: _HeldDirectory,
    record_name: str,
    expected: bytes,
) -> None:
    descriptor = None if sys.platform == "darwin" else stage_file
    try:
        data, _evidence = _read_regular_file_with_evidence(
            records,
            record_name,
            limit=MAX_CITY_PACK_UPLOAD_BYTES,
            descriptor=descriptor,
            sync=sys.platform in {"darwin", "win32"},
        )
    except WorkspaceCityRegistryError:
        raise OSError("committed record identity is invalid") from None
    if data != expected:
        raise OSError("committed record content is invalid")


def _sync_directory(directory: _HeldDirectory) -> None:
    if directory.file_descriptor is None:
        return
    try:
        os.fsync(directory.file_descriptor)
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
            raise


def _remove_owned_stage(
    directory: _HeldDirectory,
    operation: _PublicationState,
    *,
    required: bool,
) -> None:
    stage_file = operation.stage_file
    stage_name = operation.stage_name
    if stage_file is None:
        if required and stage_name is not None:
            raise OSError("staging file ownership is unavailable")
        operation.stage_name = None
        operation.stage_is_unnamed = False
        return

    removal_error: BaseException | None = None
    try:
        # POSIX has no descriptor-conditional unlink primitive. Zeroing and
        # syncing a named fallback inode preserves an unrelated replacement;
        # Linux prefers O_TMPFILE and Windows uses delete-on-close.
        if sys.platform != "win32" and (
            not operation.publication_completed or sys.platform == "darwin"
        ):
            try:
                descriptor = stage_file.fileno()
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
            except BaseException as error:
                removal_error = error
        try:
            _release_stage_file(operation)
        except BaseException as error:
            if removal_error is None:
                removal_error = error
        if sys.platform == "win32" and stage_name is not None:
            try:
                _entry_status(directory, stage_name)
            except FileNotFoundError:
                pass
            except BaseException as error:
                if removal_error is None:
                    removal_error = error
            else:
                if removal_error is None:
                    removal_error = OSError("staging path was not removed")
    finally:
        # The owner is idempotent even if an injected close wrapper raised
        # after releasing the descriptor.
        if operation.stage_file is not None:
            with suppress(BaseException):
                operation.stage_file.close()
            operation.stage_file = None
        operation.stage_name = None
        operation.stage_is_unnamed = False
        operation.stage_evidence = None
    if required and removal_error is not None:
        raise removal_error


def _publish(
    staging: _HeldDirectory,
    records: _HeldDirectory,
    pack: CityPackV2,
    canonical: bytes,
    precommit_snapshot: tuple[_LoadedRecord, ...],
    operation: _PublicationState,
) -> None:
    record_name = f"{pack.city_id}.json"
    try:
        _write_stage(staging, canonical, operation)
        stage_name = operation.stage_name
        if stage_name is None:
            raise OSError("staging file identity is unavailable")
        _verify_staging(staging, stage_name, canonical, operation)
        try:
            _link_stage(staging, stage_name, records, record_name, operation)
        except FileExistsError:
            if operation.publication_completed:
                operation.potentially_committed = True
                raise OSError("publication completed before conflicting signal") from None
            relation = _publication_relation(records, record_name, operation)
            if relation == "same":
                operation.potentially_committed = True
            elif relation == "different":
                operation.potentially_committed = False
                raise CityPackRegistrationConflict() from None
            else:
                operation.potentially_committed = True
                raise OSError("publication evidence is indeterminate") from None
        except OSError:
            if operation.publication_completed:
                operation.potentially_committed = True
            else:
                relation = _publication_relation(records, record_name, operation)
                if relation == "same":
                    operation.potentially_committed = True
                elif relation == "different":
                    operation.potentially_committed = _publication_destination_may_exist(
                        records,
                        record_name,
                    )
                else:
                    operation.potentially_committed = True
            raise
        except (Exception, KeyboardInterrupt):
            if not operation.publication_completed and not operation.potentially_committed:
                operation.potentially_committed = _publication_destination_may_exist(
                    records,
                    record_name,
                )
            raise
        stage_file = operation.stage_file
        if stage_file is None:
            raise OSError("verified staging identity is unavailable")
        _verify_committed(stage_file, records, record_name, canonical)
        _sync_directory(records)
        _remove_owned_stage(staging, operation, required=True)
        _sync_directory(staging)
        committed_snapshot = _discover_records_locked(records)
        committed_by_id = {loaded.pack.city_id: loaded for loaded in committed_snapshot}
        expected_ids = {loaded.pack.city_id for loaded in precommit_snapshot} | {pack.city_id}
        published_record = committed_by_id.get(pack.city_id)
        if (
            set(committed_by_id) != expected_ids
            or published_record is None
            or published_record.pack.fingerprint != pack.fingerprint
            or published_record.evidence.digest != hashlib.sha256(canonical).digest()
            or any(committed_by_id.get(item.pack.city_id) != item for item in precommit_snapshot)
        ):
            raise OSError("committed registry snapshot is invalid")
    except CityPackRegistrationConflict:
        if not operation.potentially_committed:
            _cleanup_publication_stage(staging, operation)
        elif operation.stage_file is not None:
            with suppress(BaseException):
                _release_ambiguous_stage(staging, operation)
        raise
    except CityPackPublicationError:
        if not operation.potentially_committed:
            _cleanup_publication_stage(staging, operation)
        elif operation.stage_file is not None:
            with suppress(BaseException):
                _release_ambiguous_stage(staging, operation)
        raise
    except (Exception, KeyboardInterrupt):
        if not operation.potentially_committed:
            _cleanup_publication_stage(staging, operation)
        elif operation.stage_file is not None:
            with suppress(BaseException):
                _release_ambiguous_stage(staging, operation)
        raise CityPackPublicationError(operation.potentially_committed) from None


class WorkspaceCityRegistry:
    """Read and exclusively publish canonical CityPack V2 workspace records."""

    def __init__(self, workspace: WorkbenchWorkspace) -> None:
        if not isinstance(workspace, WorkbenchWorkspace):
            raise TypeError("workspace must be a WorkbenchWorkspace")
        self.workspace = workspace

    def discover(self) -> tuple[CityPackV2, ...]:
        """Return every complete canonical record, refusing the registry as a whole."""
        try:
            with _registry_lock(self.workspace) as root, ExitStack() as stack:
                records_pin = _subdirectory(
                    root,
                    _RECORD_DIRECTORY,
                    create=False,
                )
                staging_pin = _subdirectory(
                    root,
                    _STAGING_DIRECTORY,
                    create=False,
                )
                records = (
                    stack.enter_context(_hold_directory(root, records_pin))
                    if records_pin is not None
                    else None
                )
                if staging_pin is not None:
                    stack.enter_context(_hold_directory(root, staging_pin))
                return _discover_locked(records)
        except WorkspaceCityRegistryError:
            raise
        except (OSError, UnsafeWorkbenchWorkspace, ValueError):
            raise WorkspaceCityRegistryError() from None

    def register(
        self,
        document: bytes,
        *,
        forbidden_city_ids: frozenset[str],
    ) -> CityPackV2:
        """Validate and immutably publish one raw CityPack V2 JSON document."""
        if not isinstance(document, bytes):
            raise TypeError("city pack upload must be bytes")
        if not isinstance(forbidden_city_ids, frozenset) or any(
            not isinstance(item, str) for item in forbidden_city_ids
        ):
            raise TypeError("forbidden city identifiers must be a frozenset of strings")
        if len(document) > MAX_CITY_PACK_UPLOAD_BYTES:
            raise CityPackUploadTooLarge()
        try:
            payload = strict_json_object(
                document,
                max_body_bytes=MAX_CITY_PACK_UPLOAD_BYTES,
            )
        except WorkbenchJsonContentError:
            raise InvalidCityPackUpload() from None
        except WorkbenchJsonError:
            raise MalformedCityPackUpload() from None
        pack = _validate_city_payload(payload)
        canonical = _canonical_pack_bytes(pack)
        if pack.city_id in forbidden_city_ids:
            raise CityPackRegistrationConflict()

        operation = _PublicationState()
        try:
            with _registry_lock(self.workspace) as root, ExitStack() as stack:
                records_pin = _subdirectory(
                    root,
                    _RECORD_DIRECTORY,
                    create=True,
                )
                staging_pin = _subdirectory(
                    root,
                    _STAGING_DIRECTORY,
                    create=True,
                )
                assert records_pin is not None and staging_pin is not None
                records = stack.enter_context(_hold_directory(root, records_pin))
                staging = stack.enter_context(_hold_directory(root, staging_pin))
                existing = _discover_records_locked(records)
                if pack.city_id in {item.pack.city_id for item in existing}:
                    raise CityPackRegistrationConflict()
                if len(existing) >= MAX_WORKSPACE_CITY_PACKS:
                    raise CityPackRegistrationConflict()
                _publish(staging, records, pack, canonical, existing, operation)
                return pack
        except (CityPackPublicationError, CityPackRegistrationConflict):
            raise
        except WorkspaceCityRegistryError:
            if operation.potentially_committed:
                raise CityPackPublicationError(True) from None
            raise
        except (Exception, KeyboardInterrupt):
            raise CityPackPublicationError(operation.potentially_committed) from None


__all__ = [
    "MAX_CITY_PACK_UPLOAD_BYTES",
    "MAX_WORKSPACE_CITY_PACKS",
    "CityPackPublicationError",
    "CityPackRegistrationConflict",
    "CityPackUploadTooLarge",
    "InvalidCityPackUpload",
    "MalformedCityPackUpload",
    "WorkspaceCityRegistry",
    "WorkspaceCityRegistryError",
]
