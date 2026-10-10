"""Symlink- and reparse-safe workspace preparation for the local workbench."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UnsafeWorkbenchWorkspace(ValueError):
    """The requested workspace cannot be safely pinned to one real directory."""


@dataclass(frozen=True, slots=True)
class WorkbenchWorkspace:
    """One resolved workspace and its filesystem identity when available."""

    root: Path
    device: int | None
    inode: int | None


def _lstat(path: Path) -> os.stat_result:
    return os.lstat(path)


def _absolute_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workbench workspace path must be a Path")
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
    except (OSError, ValueError):
        raise UnsafeWorkbenchWorkspace("workbench workspace path is invalid") from None
    if absolute == Path(absolute.anchor):
        raise UnsafeWorkbenchWorkspace("workbench workspace must not be a filesystem root")
    return absolute


def _component_chain(path: Path) -> tuple[Path, ...]:
    anchor = Path(path.anchor)
    try:
        relative = path.relative_to(anchor)
    except ValueError:
        raise UnsafeWorkbenchWorkspace("workbench workspace path is invalid") from None
    current = anchor
    components = [current]
    for part in relative.parts:
        current /= part
        components.append(current)
    return tuple(components)


def _validate_directory_status(status: os.stat_result) -> None:
    attributes = int(getattr(status, "st_file_attributes", 0))
    if stat.S_ISLNK(status.st_mode) or attributes & _REPARSE_POINT:
        raise UnsafeWorkbenchWorkspace(
            "workbench workspace cannot traverse a link or reparse point"
        )
    if not stat.S_ISDIR(status.st_mode):
        raise UnsafeWorkbenchWorkspace("workbench workspace components must be directories")


def _inspect_component_chain(path: Path, *, require_complete: bool) -> None:
    missing_parent = False
    for component in _component_chain(path):
        try:
            status = _lstat(component)
        except FileNotFoundError:
            missing_parent = True
            if require_complete:
                raise UnsafeWorkbenchWorkspace(
                    "workbench workspace disappeared during verification"
                ) from None
            continue
        except OSError:
            raise UnsafeWorkbenchWorkspace(
                "workbench workspace cannot be inspected safely"
            ) from None
        if missing_parent:
            raise UnsafeWorkbenchWorkspace("workbench workspace changed during verification")
        _validate_directory_status(status)


def _create_missing_directories(path: Path) -> None:
    for component in _component_chain(path):
        try:
            status = _lstat(component)
        except FileNotFoundError:
            try:
                os.mkdir(component)
            except FileExistsError:
                pass
            except OSError:
                raise UnsafeWorkbenchWorkspace(
                    "workbench workspace could not be created safely"
                ) from None
            try:
                status = _lstat(component)
            except OSError:
                raise UnsafeWorkbenchWorkspace(
                    "workbench workspace changed during creation"
                ) from None
        except OSError:
            raise UnsafeWorkbenchWorkspace(
                "workbench workspace cannot be inspected safely"
            ) from None
        _validate_directory_status(status)


def _resolved_checked(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise UnsafeWorkbenchWorkspace("workbench workspace cannot be resolved safely") from None
    if resolved != path:
        raise UnsafeWorkbenchWorkspace("workbench workspace changed while it was being resolved")
    _inspect_component_chain(resolved, require_complete=True)
    return resolved


def _identity(status: os.stat_result) -> tuple[int | None, int | None]:
    device_value = int(status.st_dev)
    inode_value = int(status.st_ino)
    return (
        device_value if device_value != 0 else None,
        inode_value if inode_value != 0 else None,
    )


def prepare_workbench_workspace(path: Path) -> WorkbenchWorkspace:
    """Create and pin one real directory without traversing redirected components."""
    absolute = _absolute_path(path)
    _inspect_component_chain(absolute, require_complete=False)
    _create_missing_directories(absolute)
    _inspect_component_chain(absolute, require_complete=True)
    resolved = _resolved_checked(absolute)
    try:
        status = _lstat(resolved)
    except OSError:
        raise UnsafeWorkbenchWorkspace(
            "workbench workspace disappeared during verification"
        ) from None
    _validate_directory_status(status)
    device, inode = _identity(status)
    return WorkbenchWorkspace(root=resolved, device=device, inode=inode)


def verify_workbench_workspace(workspace: WorkbenchWorkspace) -> Path:
    """Recheck the pinned root before an application or publication boundary."""
    if not isinstance(workspace, WorkbenchWorkspace):
        raise TypeError("workspace must be a WorkbenchWorkspace")
    root = _absolute_path(workspace.root)
    if root != workspace.root:
        raise UnsafeWorkbenchWorkspace("workbench workspace identity is invalid")
    _inspect_component_chain(root, require_complete=True)
    resolved = _resolved_checked(root)
    try:
        status = _lstat(resolved)
    except OSError:
        raise UnsafeWorkbenchWorkspace(
            "workbench workspace disappeared during verification"
        ) from None
    _validate_directory_status(status)
    device, inode = _identity(status)
    if workspace.device is not None and device != workspace.device:
        raise UnsafeWorkbenchWorkspace("workbench workspace identity changed")
    if workspace.inode is not None and inode != workspace.inode:
        raise UnsafeWorkbenchWorkspace("workbench workspace identity changed")
    return resolved


__all__ = [
    "UnsafeWorkbenchWorkspace",
    "WorkbenchWorkspace",
    "prepare_workbench_workspace",
    "verify_workbench_workspace",
]
