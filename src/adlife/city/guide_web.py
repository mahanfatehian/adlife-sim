"""The guided presenter experience under ``/guide``: start, setup, progress, story, report.

:func:`install_guide_routes` adds exactly eight GET routes to the local workbench app.
Pages are packaged Jinja templates translated on the server (``?lang=en|de|fa`` is the
only query a page accepts), so nothing flashes in the wrong language; data-dependent
content is built in the browser from the same catalog bytes served here. The story API
returns the verified :class:`RunStoryV1` of one saved V10 run, and the report routes
render it as static HTML on demand; nothing is ever written to the workspace.

This module never imports ``workbench_web``. Saved runs are loaded through the injected
:class:`GuideRouteContext`, so no exception classes move and no import cycle exists.
Every JSON refusal is the shared ``workbench_error`` envelope; page refusals are
translated HTML pages that never echo a refused identifier or query.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Final

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FunctionLoader, StrictUndefined

from adlife.city.run_store import StoredCityRun
from adlife.city.run_story import (
    RunStoryTooLarge,
    RunStoryUnavailable,
    build_run_story,
    story_json_bytes,
)
from adlife.city.workbench_security import workbench_error
from adlife.core.ports.run_store import StorageError, UnsafeRunLocation, validate_run_id
from adlife.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    Language,
    catalog_bytes,
    load_catalog,
    parse_language,
    template_functions,
    text_direction,
)
from adlife.reporting.run_story_model import RunStoryV1

if TYPE_CHECKING:
    from adlife.reporting.run_report import RunReportLinks

GUIDE_ROUTE_PATHS: Final = (
    "/guide",
    "/guide/new",
    "/guide/jobs/{job_id}",
    "/guide/runs/{run_id}",
    "/guide/runs/{run_id}/report",
    "/guide/runs/{run_id}/report/download",
    "/guide/assets/{asset_name}",
    "/api/runs/{run_id}/story",
)
GUIDE_TEMPLATES: Final = (
    "base.html.j2",
    "error.html.j2",
    "start.html.j2",
    "new.html.j2",
    "job.html.j2",
    "story.html.j2",
)
GUIDE_STATIC_ASSETS: Final[Mapping[str, str]] = {
    "guide.css": "text/css; charset=utf-8",
    "guide-setup.css": "text/css; charset=utf-8",
    "guide-story.css": "text/css; charset=utf-8",
    "guide-core.js": "text/javascript; charset=utf-8",
    "guide-i18n.js": "text/javascript; charset=utf-8",
    "guide-connection.js": "text/javascript; charset=utf-8",
    "guide-start.js": "text/javascript; charset=utf-8",
    "guide-setup.js": "text/javascript; charset=utf-8",
    "guide-progress.js": "text/javascript; charset=utf-8",
    "guide-story.js": "text/javascript; charset=utf-8",
    "guide-map.js": "text/javascript; charset=utf-8",
}
GUIDE_CATALOG_ASSETS: Final[Mapping[str, Language]] = {
    "i18n-en.json": "en",
    "i18n-de.json": "de",
    "i18n-fa.json": "fa",
}
MAX_GUIDE_TEMPLATE_BYTES: Final = 65_536
MAX_GUIDE_ASSET_BYTES: Final = 262_144
STORY_CACHE_SIZE: Final = 8

_JOB_ID: Final = re.compile(r"^job-[0-9a-f]{32}$")
_UTF8_BOM: Final = b"\xef\xbb\xbf"
_REPORT_HEADERS: Final[Mapping[str, str]] = {
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class GuideRunMissing(LookupError):
    """A portable run ID that names no saved run in this workspace."""


class GuideRunUnverifiable(RuntimeError):
    """A saved run that cannot be freshly verified, so it is never shown."""


class GuideQueryError(ValueError):
    """A guide request query other than at most one exact ``lang`` field."""


@dataclass(frozen=True, slots=True)
class GuideRouteContext:
    """What the guide routes need from the workbench app, injected to avoid an import cycle.

    ``load_verified_run`` returns a freshly verified run or raises :class:`GuideRunMissing`
    or :class:`GuideRunUnverifiable`. ``content_fingerprint`` names the exact bytes of a
    run directory, or returns None, which only disables the story cache for that run.
    """

    csrf_token: str
    shell_headers: Mapping[str, str]
    load_verified_run: Callable[[str], StoredCityRun]
    content_fingerprint: Callable[[str], str | None]


def parse_guide_query(request: Request) -> Language:
    """Return the page language: the query may hold only ``lang`` once, exactly en/de/fa."""
    items = request.query_params.multi_items()
    if len(items) > 1 or any(name != "lang" for name, _value in items):
        raise GuideQueryError("guide query is invalid")
    if not items:
        return DEFAULT_LANGUAGE
    try:
        return parse_language(items[0][1])
    except ValueError:
        raise GuideQueryError("guide query is invalid") from None


def _invalid_query() -> JSONResponse:
    return workbench_error(
        422,
        "invalid-query",
        "One or more query fields are invalid.",
        {"request": "The query is invalid."},
    )


# --- packaged templates and assets -------------------------------------------------------


def _read_packaged(relative: tuple[str, ...], *, limit: int, label: str) -> bytes:
    resource = files("adlife.city").joinpath(*relative)
    try:
        with resource.open("rb") as source:
            data = source.read(limit + 1)
    except OSError:
        raise RuntimeError(f"guide {label} is unavailable") from None
    if len(data) > limit:
        raise RuntimeError(f"guide {label} exceeds its size limit")
    if data.startswith(_UTF8_BOM):
        raise RuntimeError(f"guide {label} must be UTF-8 without a byte order mark")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeError(f"guide {label} is not valid UTF-8") from None
    return data


def guide_template_source(name: str) -> str | None:
    """Return one allow-listed packaged template as LF-normalized text, or None."""
    if name not in GUIDE_TEMPLATES:
        return None
    data = _read_packaged(
        ("templates", "guide", name), limit=MAX_GUIDE_TEMPLATE_BYTES, label="template"
    )
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


@cache
def guide_environment() -> Environment:
    """The process-wide guide template environment (autoescaped, strict, LF)."""
    return Environment(
        loader=FunctionLoader(guide_template_source),
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _asset_response(asset_name: str, headers: Mapping[str, str]) -> Response:
    language = GUIDE_CATALOG_ASSETS.get(asset_name)
    if language is not None:
        data = catalog_bytes(language)
        if len(data) > MAX_GUIDE_ASSET_BYTES:
            raise RuntimeError("guide catalog exceeds its size limit")
        return Response(data, media_type="application/json", headers=dict(headers))
    media_type = GUIDE_STATIC_ASSETS.get(asset_name)
    if media_type is None:
        return workbench_error(404, "not-found", "The requested resource was not found.")
    data = _read_packaged(
        ("static", "guide", asset_name), limit=MAX_GUIDE_ASSET_BYTES, label="asset"
    )
    return Response(data, media_type=media_type, headers=dict(headers))


# --- the verified story cache ------------------------------------------------------------


class _StoryCache:
    """At most eight verified stories, each bound to the exact bytes it was built from.

    A hit needs a fingerprint equal to the cached one. A miss verifies and builds the
    story, and caches it only when the fingerprints before and after are equal, so a
    run that changed during verification is rebuilt next time instead of trusted.
    """

    def __init__(
        self,
        load_verified_run: Callable[[str], StoredCityRun],
        content_fingerprint: Callable[[str], str | None],
        *,
        limit: int = STORY_CACHE_SIZE,
    ) -> None:
        self._load = load_verified_run
        self._fingerprint = content_fingerprint
        self._limit = limit
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[str, RunStoryV1]] = OrderedDict()

    def get(self, run_id: str) -> RunStoryV1:
        before = self._fingerprint(run_id)
        with self._lock:
            entry = self._items.get(run_id)
            if entry is not None:
                if before is not None and entry[0] == before:
                    self._items.move_to_end(run_id)
                    return entry[1]
                del self._items[run_id]
        story = build_run_story(self._load(run_id))
        if before is not None and self._fingerprint(run_id) == before:
            with self._lock:
                self._items[run_id] = (before, story)
                self._items.move_to_end(run_id)
                while len(self._items) > self._limit:
                    self._items.popitem(last=False)
        return story

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class _StoryFailure(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _story_or_failure(stories: _StoryCache, run_id: str) -> RunStoryV1:
    """Return the story, or raise one value-free failure kind for the caller to word."""
    try:
        return stories.get(run_id)
    except GuideRunMissing:
        raise _StoryFailure("missing") from None
    except GuideRunUnverifiable:
        raise _StoryFailure("unverifiable") from None
    except RunStoryUnavailable:
        raise _StoryFailure("older") from None
    except RunStoryTooLarge:
        raise _StoryFailure("too-large") from None
    except (StorageError, TypeError, ValueError):
        # Verified evidence that cannot be projected is as untrustworthy as a failed
        # verification; nothing about it is shown.
        raise _StoryFailure("unverifiable") from None


_STORY_API_FAILURES: Final[Mapping[str, tuple[int, str, str]]] = {
    "missing": (404, "not-found", "The requested resource was not found."),
    "unverifiable": (409, "run-unavailable", "The saved run could not be verified."),
    "older": (404, "story-unavailable", "This saved run has no story view."),
    "too-large": (409, "story-too-large", "This saved run is too large for the story view."),
}
_REPORT_FAILURES: Final[Mapping[str, tuple[int, str]]] = {
    "missing": (404, "error.not_found"),
    "unverifiable": (409, "error.unavailable"),
    "older": (404, "error.older"),
    "too-large": (409, "error.too_large"),
}


def _report_links(run_id: str, language: Language) -> RunReportLinks:
    from adlife.reporting.run_report import RunReportLinks

    return RunReportLinks(
        story=f"/guide/runs/{run_id}?lang={language}",
        download=f"/guide/runs/{run_id}/report/download?lang={language}",
        languages=tuple((code, f"/guide/runs/{run_id}/report?lang={code}") for code in LANGUAGES),
    )


# --- routes ------------------------------------------------------------------------------


def install_guide_routes(app: FastAPI, context: GuideRouteContext) -> None:
    """Add the eight guide routes (spec 1.1) to ``app``; all of them are reads."""
    if not isinstance(context, GuideRouteContext):
        raise TypeError("context must be a GuideRouteContext")
    if not isinstance(context.csrf_token, str) or not context.csrf_token:
        raise ValueError("guide pages need the workbench CSRF token")
    shell_headers = dict(context.shell_headers)
    stories = _StoryCache(context.load_verified_run, context.content_fingerprint)
    app.state.guide_story_cache = stories

    def page(
        template: str,
        *,
        language: Language,
        title_key: str,
        page_css: str | None,
        page_script: str | None,
        path: str,
        csrf: bool = False,
        run_id: str | None = None,
        job_id: str | None = None,
        error: Mapping[str, object] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        catalog = load_catalog(language)
        document = (
            guide_environment()
            .get_template(template)
            .render(
                lang=language,
                dir=text_direction(language),
                title_key=title_key,
                page_css=page_css,
                page_script=page_script,
                csrf_token=context.csrf_token if csrf else None,
                run_id=run_id,
                job_id=job_id,
                lang_links=[
                    {
                        "code": code,
                        # Each language is named in its own words, whatever the page language.
                        "label": load_catalog(code).text(f"lang.{code}"),
                        "href": f"{path}?lang={code}",
                        "current": code == language,
                    }
                    for code in LANGUAGES
                ],
                error=error,
                **template_functions(catalog),
            )
        )
        return HTMLResponse(document, status_code=status_code, headers=shell_headers)

    def error_page(
        language: Language,
        *,
        status_code: int,
        key: str,
        path: str = "/guide",
    ) -> HTMLResponse:
        return page(
            "error.html.j2",
            language=language,
            title_key="error.title",
            page_css=None,
            page_script=None,
            path=path,
            error={
                "status": status_code,
                "key": key,
                "back_href": f"/guide?lang={language}",
            },
            status_code=status_code,
        )

    def portable(run_id: str) -> str | None:
        try:
            return validate_run_id(run_id)
        except UnsafeRunLocation:
            return None

    @app.get("/guide", include_in_schema=False)
    def guide_start(request: Request) -> Response:
        try:
            language = parse_guide_query(request)
        except GuideQueryError:
            return _invalid_query()
        return page(
            "start.html.j2",
            language=language,
            title_key="start.title",
            page_css="guide-setup.css",
            page_script="guide-start.js",
            path="/guide",
            csrf=True,
        )

    @app.get("/guide/new", include_in_schema=False)
    def guide_setup(request: Request) -> Response:
        try:
            language = parse_guide_query(request)
        except GuideQueryError:
            return _invalid_query()
        return page(
            "new.html.j2",
            language=language,
            title_key="setup.title",
            page_css="guide-setup.css",
            page_script="guide-setup.js",
            path="/guide/new",
            csrf=True,
        )

    @app.get("/guide/jobs/{job_id}", include_in_schema=False)
    def guide_progress(job_id: str, request: Request) -> Response:
        try:
            language = parse_guide_query(request)
        except GuideQueryError:
            return _invalid_query()
        if _JOB_ID.fullmatch(job_id) is None:
            return error_page(language, status_code=404, key="error.job_not_found")
        return page(
            "job.html.j2",
            language=language,
            title_key="progress.title",
            page_css="guide-setup.css",
            page_script="guide-progress.js",
            path=f"/guide/jobs/{job_id}",
            csrf=True,
            job_id=job_id,
        )

    @app.get("/guide/runs/{run_id}", include_in_schema=False)
    def guide_story(run_id: str, request: Request) -> Response:
        try:
            language = parse_guide_query(request)
        except GuideQueryError:
            return _invalid_query()
        portable_run_id = portable(run_id)
        if portable_run_id is None:
            return error_page(language, status_code=404, key="error.not_found")
        return page(
            "story.html.j2",
            language=language,
            title_key="story.title",
            page_css="guide-story.css",
            page_script="guide-story.js",
            path=f"/guide/runs/{portable_run_id}",
            run_id=portable_run_id,
        )

    def report(run_id: str, request: Request, *, download: bool) -> Response:
        from adlife.reporting.run_report import render_run_report_html

        try:
            language = parse_guide_query(request)
        except GuideQueryError:
            return _invalid_query()
        portable_run_id = portable(run_id)
        if portable_run_id is None:
            return error_page(language, status_code=404, key="error.not_found")
        suffix = "/report/download" if download else "/report"
        path = f"/guide/runs/{portable_run_id}{suffix}"
        try:
            story = _story_or_failure(stories, portable_run_id)
        except _StoryFailure as failure:
            status_code, key = _REPORT_FAILURES[failure.kind]
            return error_page(
                language,
                status_code=status_code,
                key=key,
                path=path,
            )
        try:
            rendered = render_run_report_html(
                story,
                language=language,
                links=None if download else _report_links(portable_run_id, language),
            )
        except ValueError:
            # The renderer refuses rather than truncates a report over its size cap.
            return error_page(language, status_code=409, key="error.too_large", path=path)
        policy = rendered.content_security_policy
        headers = {
            **_REPORT_HEADERS,
            "Content-Security-Policy": f"{policy}; frame-ancestors 'none'",
        }
        if download:
            headers["Content-Disposition"] = (
                f'attachment; filename="adlife-report-{portable_run_id}-{language}.html"'
            )
        return Response(rendered.content, media_type="text/html; charset=utf-8", headers=headers)

    @app.get("/guide/runs/{run_id}/report", include_in_schema=False)
    def guide_report(run_id: str, request: Request) -> Response:
        return report(run_id, request, download=False)

    @app.get("/guide/runs/{run_id}/report/download", include_in_schema=False)
    def guide_report_download(run_id: str, request: Request) -> Response:
        return report(run_id, request, download=True)

    @app.get("/guide/assets/{asset_name}", include_in_schema=False)
    def guide_asset(asset_name: str) -> Response:
        return _asset_response(asset_name, shell_headers)

    @app.get("/api/runs/{run_id}/story")
    def run_story(run_id: str, request: Request) -> Response:
        if request.query_params.multi_items():
            return _invalid_query()
        portable_run_id = portable(run_id)
        if portable_run_id is None:
            return workbench_error(404, "not-found", "The requested resource was not found.")
        try:
            story = _story_or_failure(stories, portable_run_id)
        except _StoryFailure as failure:
            status_code, code, message = _STORY_API_FAILURES[failure.kind]
            return workbench_error(status_code, code, message)
        return Response(
            story_json_bytes(story), media_type="application/json", headers=shell_headers
        )


__all__ = [
    "GUIDE_CATALOG_ASSETS",
    "GUIDE_ROUTE_PATHS",
    "GUIDE_STATIC_ASSETS",
    "GUIDE_TEMPLATES",
    "GuideQueryError",
    "GuideRouteContext",
    "GuideRunMissing",
    "GuideRunUnverifiable",
    "guide_environment",
    "guide_template_source",
    "install_guide_routes",
    "parse_guide_query",
]
