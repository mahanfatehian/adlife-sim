from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from adlife.cli.errors import ExitCode
from adlife.core.domain.campaign import Campaign

_HASH_CHUNK_SIZE = 1024 * 1024

app = typer.Typer(
    name="campaign",
    help="Validate and inspect campaign definitions.",
    no_args_is_help=True,
)


@app.callback()
def campaign_root() -> None:
    """Operate on campaign definitions."""


class CampaignImportError(ValueError):
    """Raised when an untrusted campaign cannot be safely imported."""


def _adapt_yaml_sequences(document: Mapping[object, object]) -> dict[object, object]:
    data = dict(document)
    interests = data.get("target_interests")
    if isinstance(interests, list):
        data["target_interests"] = frozenset(interests)

    placements = data.get("placements")
    if isinstance(placements, list):
        adapted_placements: list[object] = []
        for placement in placements:
            if not isinstance(placement, Mapping):
                adapted_placements.append(placement)
                continue
            adapted_placement = dict(placement)
            windows = adapted_placement.get("active_windows")
            if isinstance(windows, list):
                adapted_placement["active_windows"] = tuple(windows)
            adapted_placements.append(adapted_placement)
        data["placements"] = tuple(adapted_placements)

    creative = data.get("creative_features")
    if isinstance(creative, Mapping):
        adapted_creative = dict(creative)
        for field_name in ("dominant_colors", "visible_text"):
            value = adapted_creative.get(field_name)
            if isinstance(value, list):
                adapted_creative[field_name] = tuple(value)
        data["creative_features"] = adapted_creative
    return data


def _windows_open_file_path(descriptor: int) -> Path:
    # Windows-only modules, imported inside the branch that only Windows reaches, so
    # non-Windows checkers and runtimes never see them; the win32 Verifier typing is
    # declared for every platform and the attribute access is guarded at runtime.
    import ctypes
    import msvcrt

    if sys.platform != "win32":  # pragma: no cover - platform guard, unreachable off Windows
        raise OSError("Windows handle verification requires a Windows interpreter")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final_path.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(descriptor)
    buffer_size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(buffer_size)
        length = get_final_path(handle, buffer, buffer_size, 0)
        if length == 0:
            error_code = ctypes.get_last_error()
            raise OSError(error_code, ctypes.FormatError(error_code))
        if length < buffer_size:
            raw_path = buffer.value
            break
        buffer_size = length + 1

    if raw_path.startswith("\\\\?\\UNC\\"):
        raw_path = "\\\\" + raw_path[8:]
    elif raw_path.startswith("\\\\?\\"):
        raw_path = raw_path[4:]
    path = Path(raw_path)
    if not path.is_absolute():
        raise OSError("Windows handle did not resolve to an absolute path")
    return path


def _posix_open_file_path(descriptor: int) -> Path:
    raw_path: str
    if sys.platform.startswith("linux"):
        raw_path = os.readlink(f"/proc/self/fd/{descriptor}")
        if raw_path.endswith(" (deleted)"):
            raise OSError("open asset path was deleted")
    elif sys.platform == "darwin":
        import fcntl

        result = fcntl.fcntl(descriptor, getattr(fcntl, "F_GETPATH", 50), b"\0" * 4096)
        if not isinstance(result, bytes):
            raise OSError("macOS handle path lookup returned no path")
        raw_path = os.fsdecode(result.split(b"\0", 1)[0])
    else:
        raise OSError(f"handle path verification is unsupported on {sys.platform}")

    path = Path(raw_path)
    if not path.is_absolute():
        raise OSError("open handle did not resolve to an absolute path")
    return path


def _open_file_path(descriptor: int) -> Path:
    return (
        _windows_open_file_path(descriptor)
        if os.name == "nt"
        else _posix_open_file_path(descriptor)
    )


def _verified_relative_path(
    descriptor: int,
    resolved_root: Path,
    raw_path: str,
) -> str:
    try:
        opened_path = _open_file_path(descriptor)
    except Exception as error:
        raise CampaignImportError(f"could not verify asset location: {raw_path}") from error
    try:
        return opened_path.relative_to(resolved_root).as_posix()
    except ValueError as error:
        raise CampaignImportError(f"asset path is outside project root: {raw_path}") from error


def _hash_verified_asset(
    resolved_asset: Path,
    resolved_root: Path,
    raw_path: str,
) -> tuple[str, str]:
    try:
        before_open = os.stat(resolved_asset, follow_symlinks=False)
    except (OSError, ValueError) as error:
        raise CampaignImportError(f"asset is not a regular file: {raw_path}") from error
    if not S_ISREG(before_open.st_mode):
        raise CampaignImportError(f"asset is not a regular file: {raw_path}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved_asset, flags)
    except (OSError, ValueError) as error:
        raise CampaignImportError(f"asset could not be opened safely: {raw_path}") from error

    try:
        asset_file = os.fdopen(descriptor, "rb")
    except (OSError, ValueError) as error:
        with suppress(OSError):
            os.close(descriptor)
        raise CampaignImportError(f"asset could not be opened safely: {raw_path}") from error

    digest = sha256()
    normalized_path: str
    try:
        with asset_file:
            after_open = os.fstat(asset_file.fileno())
            if not S_ISREG(after_open.st_mode):
                raise CampaignImportError(f"asset is not a regular file: {raw_path}")
            if (before_open.st_dev, before_open.st_ino) != (
                after_open.st_dev,
                after_open.st_ino,
            ):
                raise CampaignImportError(f"asset changed while being opened: {raw_path}")
            normalized_path = _verified_relative_path(
                asset_file.fileno(),
                resolved_root,
                raw_path,
            )
            while chunk := asset_file.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
    except CampaignImportError:
        raise
    except (OSError, ValueError) as error:
        raise CampaignImportError(f"asset could not be read: {raw_path}") from error
    return normalized_path, digest.hexdigest()


def _resolve_asset(project_root: Path, raw_path: str) -> tuple[str, str]:
    candidate = Path(raw_path)
    if any(part == ".." for part in candidate.parts):
        raise CampaignImportError(f"asset path traversal is not allowed: {raw_path}")
    if raw_path.startswith(("\\\\", "//")):
        raise CampaignImportError("network asset paths are not allowed")

    try:
        resolved_root = project_root.resolve(strict=True)
    except OSError as error:
        raise CampaignImportError(f"project root is unavailable: {project_root}") from error
    if not resolved_root.is_dir():
        raise CampaignImportError(f"project root is not a directory: {project_root}")

    unresolved = candidate if candidate.is_absolute() else resolved_root / candidate
    try:
        resolved_asset = unresolved.resolve(strict=True)
    except OSError as error:
        raise CampaignImportError(f"asset is not a regular file: {raw_path}") from error
    if not resolved_asset.is_relative_to(resolved_root):
        raise CampaignImportError(f"asset path is outside project root: {raw_path}")

    return _hash_verified_asset(resolved_asset, resolved_root, raw_path)


def import_campaign(path: Path, project_root: Path) -> Campaign:
    try:
        if not path.is_file():
            raise CampaignImportError(f"campaign is not a regular file: {path}")
        with path.open("r", encoding="utf-8") as campaign_file:
            document = yaml.safe_load(campaign_file)
    except yaml.YAMLError as error:
        raise CampaignImportError(f"{path}: campaign could not be parsed: {error}") from error
    except UnicodeError as error:
        raise CampaignImportError(f"{path}: campaign is not valid UTF-8") from error
    except OSError as error:
        raise CampaignImportError(f"campaign could not be read: {path}") from error

    if not isinstance(document, Mapping):
        raise CampaignImportError(f"{path}: campaign must contain a mapping")

    try:
        data = _adapt_yaml_sequences(document)
    except (TypeError, ValueError) as error:
        raise CampaignImportError(f"{path}: invalid campaign: {error}") from error
    raw_asset_path = data.get("asset_path")
    supplied_digest = data.get("asset_sha256")
    if raw_asset_path is None:
        if supplied_digest is not None:
            raise CampaignImportError("asset_sha256 requires asset_path")
    elif isinstance(raw_asset_path, str):
        normalized_path, digest = _resolve_asset(project_root, raw_asset_path)
        data["asset_path"] = normalized_path
        data["asset_sha256"] = digest

    try:
        return Campaign.model_validate(data)
    except ValidationError as error:
        raise CampaignImportError(f"{path}: invalid campaign: {error}") from error


@app.command("import")
def import_command(
    path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
        ),
    ] = Path("."),
) -> None:
    try:
        campaign = import_campaign(path, project_root)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=int(ExitCode.INPUT_ERROR)) from error
    typer.echo(campaign.model_dump_json())


__all__ = ["CampaignImportError", "app", "import_campaign"]
