"""Static, deterministic spatial-study evidence rendering.

The renderer's only scientific input is the fully revalidated compact result.
It has a dedicated environment; only the exact packaged stylesheet is trusted HTML.
"""

from __future__ import annotations

import base64
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from hashlib import sha256
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import uuid4

from jinja2 import Environment, StrictUndefined
from markupsafe import Markup
from pydantic import Field, field_validator, model_validator

from adlife.core.domain.identifiers import validate_portable_run_identifier
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_study import SpatialStudyResult
from adlife.core.simulation._validation import revalidate_model

MAX_SPATIAL_REPORT_BYTES = 8_388_608


class SpatialReportPublicationError(OSError):
    """Generic path-free filesystem refusal or publication failure."""


class SpatialReportConflict(SpatialReportPublicationError):
    """Any existing destination, including a dangling link, is immutable."""


class SpatialReportReceipt(DomainModel):
    """Fixed public receipt over exact report bytes, never an absolute local path."""

    schema_version: Literal[1] = 1
    format_id: Literal["spatial-study-html-v1"] = "spatial-study-html-v1"
    claim_scope: Literal["synthetic-study-not-observed-or-causal-effect"] = (
        "synthetic-study-not-observed-or-causal-effect"
    )
    study_id: str
    study_definition_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    study_result_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    report_path: str
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    report_bytes: int = Field(gt=0, le=MAX_SPATIAL_REPORT_BYTES)

    @field_validator("study_id")
    @classmethod
    def safe_study_id(cls, value: str) -> str:
        return validate_portable_run_identifier(value)

    @field_validator("report_path")
    @classmethod
    def fixed_relative_path(cls, value: str) -> str:
        prefix = "city-reports/"
        if not value.startswith(prefix) or not value.endswith(".html"):
            raise ValueError("spatial report path is invalid")
        identifier = value[len(prefix) : -len(".html")]
        validate_portable_run_identifier(identifier)
        return value

    @model_validator(mode="after")
    def matching_study_path(self) -> Self:
        if self.report_path != f"city-reports/{self.study_id}.html":
            raise ValueError("spatial report path does not match study identifier")
        return self


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def render_spatial_study_html(result: SpatialStudyResult) -> bytes:
    """Render all aggregate evidence and pair provenance, never raw/scalar input rows."""
    checked = revalidate_model(result, SpatialStudyResult, label="spatial study result")
    packaged = resources.files("adlife.reporting")
    css = _lf((packaged / "static" / "spatial-report.css").read_text(encoding="utf-8"))
    source = _lf((packaged / "templates" / "spatial-report.html.j2").read_text(encoding="utf-8"))
    style_hash = base64.b64encode(sha256(css.encode("utf-8")).digest()).decode("ascii")
    csp = (
        "default-src 'none'; script-src 'none'; "
        f"style-src 'sha256-{style_hash}'; "
        "img-src 'none'; font-src 'none'; connect-src 'none'; media-src 'none'; "
        "object-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    # Exclusion happens before templating so per-seed scalar triples cannot reach it.
    document = checked.model_dump(mode="json", exclude={"pairs": {"__all__": {"scalars"}}})
    protocol = {
        key: value
        for key, value in document.items()
        if key not in {"city", "definition", "pairs", "statistics"}
    }
    environment = Environment(
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        newline_sequence="\n",
    )
    content = (
        environment.from_string(source)
        .render(
            study=document,
            protocol=protocol,
            definition_json=canonical_json(checked.definition),
            result_sha256=sha256(canonical_json(checked).encode("utf-8")).hexdigest(),
            css=Markup(css),
            csp=csp,
            response_analyzed=checked.definition.analysis_scope == "attention-and-response",
        )
        .encode("utf-8")
    )
    if len(content) > MAX_SPATIAL_REPORT_BYTES:
        raise ValueError("spatial study report exceeds the HTML size limit")
    return content


def _check_directory(directory: Path, root: Path) -> os.stat_result:
    status = directory.lstat()
    if (
        not stat.S_ISDIR(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or directory.resolve(strict=True) != root / "city-reports"
    ):
        raise SpatialReportPublicationError("report directory is invalid")
    return status


@contextmanager
def _publication_directory(root: Path, *, create: bool = True) -> Iterator[tuple[Path, int | None]]:
    """Bind publication to one real directory, with no link/junction traversal.

    POSIX operations use its open descriptor. Windows holds a directory handle
    without FILE_SHARE_DELETE, preventing replacement while using path-based APIs.
    Reparse attributes work on supported Python 3.11 as well as newer runtimes.
    """
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise SpatialReportPublicationError("report directory is invalid")
    directory = resolved / "city-reports"
    if create:
        # lstat below classifies files and dangling links as an invalid parent.
        with suppress(FileExistsError):
            directory.mkdir(exist_ok=True)
    before = _check_directory(directory, resolved)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        close_handle = kernel.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        # GENERIC_READ, SHARE_READ | SHARE_WRITE, OPEN_EXISTING,
        # BACKUP_SEMANTICS | OPEN_REPARSE_POINT; deliberately no SHARE_DELETE.
        # An attributes-only handle does not enforce Windows sharing checks.
        # Hard-link creation needs SHARE_WRITE on the directory. Identity and
        # reparse attributes are checked again immediately before the commit.
        handle = create_file(str(directory), 0x80000000, 0x3, None, 3, 0x02200000, None)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise SpatialReportPublicationError("report directory is invalid")
        try:
            after = _check_directory(directory, resolved)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise SpatialReportPublicationError("report directory is invalid")
            yield directory, None
        finally:
            close_handle(handle)
    else:
        descriptor = os.open(
            directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise SpatialReportPublicationError("report directory is invalid")
            yield directory, descriptor
        finally:
            os.close(descriptor)


def preflight_spatial_study_report(root: Path, study_id: str) -> None:
    """Refuse an existing contained destination before analysis, without writing.

    This is only an early refusal; the publisher retains its atomic final check.
    Missing roots or report directories are left for source verification/publication.
    """
    filename = f"{validate_portable_run_identifier(study_id)}.html"
    try:
        with _publication_directory(root, create=False) as (directory, directory_fd):
            kwargs = {} if directory_fd is None else {"dir_fd": directory_fd}
            destination: str | Path = directory / filename if directory_fd is None else filename
            os.stat(destination, follow_symlinks=False, **kwargs)
            raise SpatialReportConflict("report destination already exists")
    except FileNotFoundError:
        return
    except SpatialReportPublicationError:
        raise
    except OSError:
        raise SpatialReportPublicationError("spatial study report could not be published") from None


def _fsync_directory(directory: Path, descriptor: int | None) -> None:
    if descriptor is not None:
        os.fsync(descriptor)
    else:
        # Some platforms do not permit directory handles through os.open. This
        # whole post-link step is best effort; the complete file is already durable.
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def publish_spatial_study_report(result: SpatialStudyResult, root: Path) -> SpatialReportReceipt:
    """Publish using atomic hard-link no-clobber; never replace an existing report."""
    checked = revalidate_model(result, SpatialStudyResult, label="spatial study result")
    content = render_spatial_study_html(checked)
    filename = f"{checked.definition.study_id}.html"
    receipt = SpatialReportReceipt(
        study_id=checked.definition.study_id,
        study_definition_sha256=checked.study_definition_sha256,
        study_result_sha256=sha256(canonical_json(checked).encode("utf-8")).hexdigest(),
        report_path=f"city-reports/{filename}",
        report_sha256=sha256(content).hexdigest(),
        report_bytes=len(content),
    )
    try:
        with _publication_directory(root) as (directory, directory_fd):
            bound_status = directory.lstat() if directory_fd is None else os.fstat(directory_fd)
            kwargs = {} if directory_fd is None else {"dir_fd": directory_fd}
            destination: str | Path = directory / filename if directory_fd is None else filename
            try:
                os.stat(destination, follow_symlinks=False, **kwargs)
            except FileNotFoundError:
                pass
            else:
                raise SpatialReportConflict("report destination already exists")
            temporary = f".spatial-report-{uuid4().hex}.tmp"
            temporary_path: str | Path = (
                directory / temporary if directory_fd is None else temporary
            )
            created = False
            try:
                descriptor = os.open(
                    temporary_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                    0o600,
                    **kwargs,
                )
                created = True
                try:
                    remaining = memoryview(content)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written <= 0 or written > len(remaining):
                            raise OSError("report write failed")
                        remaining = remaining[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                latest_status = _check_directory(directory, directory.parent)
                if (bound_status.st_dev, bound_status.st_ino) != (
                    latest_status.st_dev,
                    latest_status.st_ino,
                ):
                    raise SpatialReportPublicationError("report directory is invalid")
                try:
                    if directory_fd is None:
                        os.link(temporary_path, destination, follow_symlinks=False)
                    else:
                        os.link(
                            temporary,
                            filename,
                            src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                except FileExistsError:
                    raise SpatialReportConflict("report destination already exists") from None
            finally:
                if created:
                    os.unlink(temporary_path, **kwargs)
            # The link is the commit. Unsupported or failed parent fsync cannot
            # turn an already complete publication into a reported failure.
            with suppress(OSError):
                _fsync_directory(directory, directory_fd)
    except SpatialReportPublicationError:
        raise
    except OSError:
        raise SpatialReportPublicationError("spatial study report could not be published") from None
    return receipt


__all__ = [
    "MAX_SPATIAL_REPORT_BYTES",
    "SpatialReportConflict",
    "SpatialReportPublicationError",
    "SpatialReportReceipt",
    "preflight_spatial_study_report",
    "publish_spatial_study_report",
    "render_spatial_study_html",
]
