"""The guide routes: translated pages, allow-listed assets, the story API and the report.

Requests go through the real workbench app, security middleware included, or through a
bare app with injected run lookups where only the guide's own decisions are under test.
Saved runs are built by the real pure pipeline (``run_story_fixtures``); nothing here
calls a provider, and every route is a read that leaves the workspace byte-for-byte as
it was.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jinja2 import StrictUndefined
from markupsafe import escape

from adlife.city import guide_web
from adlife.city import run_story as run_story_module
from adlife.city.guide_web import (
    GUIDE_CATALOG_ASSETS,
    GUIDE_ROUTE_PATHS,
    GUIDE_STATIC_ASSETS,
    GUIDE_TEMPLATES,
    GuideRouteContext,
    GuideRunMissing,
    GuideRunUnverifiable,
    guide_environment,
    guide_template_source,
    install_guide_routes,
)
from adlife.city.run_store import StoredCityRun
from adlife.city.run_story import (
    RunStoryTooLarge,
    RunStoryUnavailable,
    build_run_story,
    story_json_bytes,
)
from adlife.city.workbench_runs import WorkbenchRunRepository
from adlife.city.workbench_web import _SHELL_HEADERS, create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.ports.run_store import CorruptRunArtifact
from adlife.i18n import LANGUAGES, Language, catalog_bytes, load_catalog, template_functions
from adlife.reporting import run_report as run_report_module
from adlife.reporting.run_report import RunReportLinks, render_run_report_html
from tests.unit.city.run_story_fixtures import saved_story_run

HOST = "http://127.0.0.1:8765"
TOKEN = "guide-web-test-token"
STORY_RUN = "guide-default"
OLDER_RUN = "guide-older"
MISSING_RUN = "nobody-saved-this"
JOB_ID = "job-" + "0123456789abcdef" * 2
PAGES = ("/guide", "/guide/new", f"/guide/jobs/{JOB_ID}", f"/guide/runs/{STORY_RUN}")
REPORTS = (f"/guide/runs/{STORY_RUN}/report", f"/guide/runs/{STORY_RUN}/report/download")
DIRECTION = {"en": "ltr", "de": "ltr", "fa": "rtl"}
INVALID_QUERY = {
    "schema_version": 1,
    "error": {
        "code": "invalid-query",
        "message": "One or more query fields are invalid.",
        "fields": {"request": "The query is invalid."},
    },
}


@dataclass(frozen=True, slots=True)
class _Workspace:
    root: Path
    story: StoredCityRun


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> _Workspace:
    root = tmp_path_factory.mktemp("guide-web") / "workspace"
    stored = saved_story_run(root, "default-mock", run_id=STORY_RUN)
    saved_story_run(root, "v9", run_id=OLDER_RUN)
    return _Workspace(root, stored)


@pytest.fixture(scope="module")
def client(workspace: _Workspace) -> Iterator[TestClient]:
    app = create_city_workbench_app(prepare_workbench_workspace(workspace.root), csrf_token=TOKEN)
    with TestClient(app, base_url=HOST) as test_client:
        yield test_client


@pytest.fixture
def verifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every full verification the workbench repository performs."""
    calls: list[str] = []
    original = WorkbenchRunRepository.load

    def counted(self: WorkbenchRunRepository, run_id: str) -> StoredCityRun:
        calls.append(run_id)
        return original(self, run_id)

    monkeypatch.setattr(WorkbenchRunRepository, "load", counted)
    return calls


def _tr(language: Language, key: str) -> str:
    return str(template_functions(load_catalog(language))["tr"](key))


def _tr_text(language: Language, key: str) -> str:
    return str(template_functions(load_catalog(language))["tr_text"](key))


def _snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            entries.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            entries.append((relative, "directory", ""))
    return tuple(entries)


def _report_links(run_id: str, language: Language) -> RunReportLinks:
    return RunReportLinks(
        story=f"/guide/runs/{run_id}?lang={language}",
        technical=f"/runs/{run_id}",
        download=f"/guide/runs/{run_id}/report/download?lang={language}",
        languages=tuple((code, f"/guide/runs/{run_id}/report?lang={code}") for code in LANGUAGES),
    )


def _meta_policy(document: str) -> str:
    found = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', document)
    assert found is not None
    return html.unescape(found.group(1))


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[dict[str, str | None], str]] = []
        self._open: list[tuple[dict[str, str | None], list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._open.append((dict(attrs), []))

    def handle_data(self, data: str) -> None:
        for _attrs, parts in self._open:
            parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._open:
            attrs, parts = self._open.pop()
            self.anchors.append((attrs, "".join(parts).strip()))


def _anchors(document: str) -> list[tuple[dict[str, str | None], str]]:
    parser = _Anchors()
    parser.feed(document)
    parser.close()
    return parser.anchors


def _assert_shell_headers(response: Any) -> None:
    for name, value in _SHELL_HEADERS.items():
        assert response.headers[name] == value, name
    assert response.headers["cache-control"] == "no-store"


def _bare_app(
    load: Callable[[str], StoredCityRun],
    *,
    token: str = TOKEN,
    fingerprint: Callable[[str], str | None] = lambda _run_id: None,
) -> FastAPI:
    app = FastAPI()
    install_guide_routes(
        app,
        GuideRouteContext(
            csrf_token=token,
            shell_headers=_SHELL_HEADERS,
            load_verified_run=load,
            content_fingerprint=fingerprint,
        ),
    )
    return app


def _refuse(error: Exception) -> Callable[[str], StoredCityRun]:
    def load(_run_id: str) -> StoredCityRun:
        raise error

    return load


def _tamper(artifact: Path) -> bytes:
    """Change one digit of an artifact in place, keeping its size; return the original."""
    original = artifact.read_bytes()
    index = next(offset for offset, byte in enumerate(original) if 0x30 <= byte <= 0x39)
    changed = bytearray(original)
    changed[index] = 0x30 + (original[index] - 0x30 + 1) % 10
    artifact.write_bytes(bytes(changed))
    return original


# --- routes ------------------------------------------------------------------------------


def test_install_guide_routes_adds_exactly_the_eight_get_routes() -> None:
    app = FastAPI()
    before = {route.path for route in app.routes if isinstance(route, APIRoute)}
    install_guide_routes(
        app,
        GuideRouteContext(TOKEN, _SHELL_HEADERS, _refuse(GuideRunMissing()), lambda _r: None),
    )
    added = [
        route for route in app.routes if isinstance(route, APIRoute) and route.path not in before
    ]
    assert [route.path for route in added] == list(GUIDE_ROUTE_PATHS)
    assert len(GUIDE_ROUTE_PATHS) == 8
    for route in added:
        assert route.methods == {"GET"}, route.path
        assert route.include_in_schema is (route.path == "/api/runs/{run_id}/story")


def test_the_workbench_app_serves_the_guide_routes(client: TestClient) -> None:
    paths = {route.path for route in client.app.routes if isinstance(route, APIRoute)}  # type: ignore[attr-defined]
    assert set(GUIDE_ROUTE_PATHS) <= paths


def test_install_guide_routes_refuses_an_incomplete_context() -> None:
    with pytest.raises(TypeError):
        install_guide_routes(FastAPI(), object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        install_guide_routes(
            FastAPI(),
            GuideRouteContext("", _SHELL_HEADERS, _refuse(GuideRunMissing()), lambda _r: None),
        )


@pytest.mark.parametrize("path", [*PAGES, *REPORTS, "/guide/assets/guide.css"])
def test_guide_paths_accept_no_writes(client: TestClient, path: str) -> None:
    headers = {"Origin": HOST, "X-AdLife-CSRF": TOKEN, "Content-Type": "application/json"}
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.request(method, path, headers=headers, content=b"{}")
        assert response.status_code == 405, (method, path)
    response = client.post(f"/api/runs/{STORY_RUN}/story", headers=headers, content=b"{}")
    assert response.status_code == 405


# --- language and query ------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize(
    ("query", "language"),
    [("", "en"), ("?lang=en", "en"), ("?lang=de", "de"), ("?lang=fa", "fa")],
)
def test_pages_render_in_the_requested_language(
    client: TestClient, path: str, query: str, language: Language
) -> None:
    response = client.get(path + query)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    document = response.text
    assert document.startswith("<!doctype html>\n")
    assert f'<html lang="{language}" dir="{DIRECTION[language]}">' in document
    assert str(escape(_tr_text(language, "common.app"))) in document
    _assert_shell_headers(response)


@pytest.mark.parametrize("path", [*PAGES, *REPORTS])
@pytest.mark.parametrize(
    "query",
    [
        "?lang=FA",
        "?lang=",
        "?lang",
        "?lang=en-US",
        "?lang=%20en",
        "?LANG=en",
        "?lang=en&lang=en",
        "?lang=en&lang=de",
        "?lang=en&x=1",
        "?x=1",
    ],
)
def test_page_queries_other_than_one_exact_lang_are_refused(
    client: TestClient, path: str, query: str
) -> None:
    response = client.get(path + query)
    assert response.status_code == 422
    assert response.json() == INVALID_QUERY
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("path", PAGES)
def test_language_links_switch_the_same_page_and_name_each_language_in_itself(
    client: TestClient, path: str, language: Language
) -> None:
    anchors = _anchors(client.get(f"{path}?lang={language}").text)
    switches = [(attrs, text) for attrs, text in anchors if "hreflang" in attrs]
    assert [attrs["href"] for attrs, _text in switches] == [
        f"{path}?lang={code}" for code in LANGUAGES
    ]
    for (attrs, text), code in zip(switches, LANGUAGES, strict=True):
        assert attrs["lang"] == code and attrs["hreflang"] == code
        assert text == load_catalog(code).text(f"lang.{code}")
        assert attrs.get("aria-current") == ("true" if code == language else None)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    ("path", "technical"),
    [
        ("/guide", "/"),
        ("/guide/new", "/"),
        (f"/guide/jobs/{JOB_ID}", "/"),
        (f"/guide/runs/{STORY_RUN}", f"/runs/{STORY_RUN}"),
    ],
)
def test_guide_links_keep_the_language_and_technical_links_open_a_new_tab(
    client: TestClient, path: str, technical: str, language: Language
) -> None:
    anchors = _anchors(client.get(f"{path}?lang={language}").text)
    new_tab = [attrs for attrs, _text in anchors if attrs.get("target") == "_blank"]
    assert technical in [attrs.get("href") for attrs in new_tab]
    for attrs in new_tab:
        assert attrs["rel"] == "noopener noreferrer"
    # Anchors without an href are filled in by the page script from the same catalog.
    for href in [attrs["href"] for attrs, _text in anchors if attrs.get("href") is not None]:
        assert href is not None and href.startswith(("/", "#")), href
    for attrs, _text in anchors:
        href = attrs.get("href") or ""
        if href.startswith("/guide") and "hreflang" not in attrs:
            assert href.endswith(f"?lang={language}"), href


def test_pages_name_their_job_and_run(client: TestClient) -> None:
    progress = client.get(f"/guide/jobs/{JOB_ID}").text
    assert f'<meta name="adlife-job-id" content="{JOB_ID}">' in progress
    story = client.get(f"/guide/runs/{STORY_RUN}").text
    assert f'<meta name="adlife-run-id" content="{STORY_RUN}">' in story
    assert "adlife-job-id" not in story


def test_the_story_page_never_verifies_the_run(
    client: TestClient, verifications: list[str]
) -> None:
    # Only the identifier grammar is checked here; the JSON call does the verification.
    assert client.get(f"/guide/runs/{MISSING_RUN}").status_code == 200
    assert client.get(f"/guide/runs/{OLDER_RUN}?lang=fa").status_code == 200
    assert verifications == []


def test_the_csrf_meta_is_escaped_on_posting_pages_and_absent_elsewhere() -> None:
    hostile = '"><script>window.pwned=1</script>&'
    app = _bare_app(_refuse(GuideRunMissing()), token=hostile)
    with TestClient(app, base_url=HOST) as client:
        for path in PAGES[:3]:
            document = client.get(path).text
            assert f'<meta name="adlife-csrf-token" content="{escape(hostile)}">' in document
            assert hostile not in document and "<script>window" not in document
        for path in (PAGES[3], "/guide/jobs/job-1", "/guide/runs/Not-Portable"):
            document = client.get(path).text
            assert "adlife-csrf-token" not in document
            assert "pwned" not in document


def test_the_real_csrf_token_reaches_only_the_posting_pages(client: TestClient) -> None:
    meta = f'<meta name="adlife-csrf-token" content="{TOKEN}">'
    for path in PAGES[:3]:
        assert meta in client.get(path).text
    assert TOKEN not in client.get(PAGES[3]).text
    assert TOKEN not in client.get(REPORTS[0]).text


# --- error pages -------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    ("path", "marker", "key"),
    [
        ("/guide/jobs/job-123", "job-123", "error.job_not_found"),
        ("/guide/jobs/job-" + "A" * 32, "A" * 32, "error.job_not_found"),
        ("/guide/jobs/run-" + "0" * 32, "run-" + "0" * 32, "error.job_not_found"),
        ("/guide/runs/Not-Portable-Run", "Not-Portable-Run", "error.not_found"),
        ("/guide/runs/-leading-dash", "-leading-dash", "error.not_found"),
        ("/guide/runs/" + "z" * 41, "z" * 41, "error.not_found"),
        ("/guide/runs/Bad_Run/report", "Bad_Run", "error.not_found"),
        ("/guide/runs/Bad_Run/report/download", "Bad_Run", "error.not_found"),
    ],
)
def test_malformed_identifiers_get_a_translated_404_page_without_the_request(
    client: TestClient, path: str, marker: str, key: str, language: Language
) -> None:
    response = client.get(f"{path}?lang={language}")
    assert response.status_code == 404
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    document = response.text
    assert f'<html lang="{language}" dir="{DIRECTION[language]}">' in document
    assert _tr(language, key) in document
    assert f'href="/guide?lang={language}"' in document
    assert marker not in document
    assert "<script" not in document and "adlife-csrf-token" not in document
    assert "content-disposition" not in response.headers
    _assert_shell_headers(response)


@pytest.mark.parametrize("suffix", ["/report", "/report/download"])
def test_report_refusals_are_translated_pages(client: TestClient, suffix: str) -> None:
    missing = client.get(f"/guide/runs/{MISSING_RUN}{suffix}?lang=de")
    assert missing.status_code == 404
    assert _tr("de", "error.not_found") in missing.text
    older = client.get(f"/guide/runs/{OLDER_RUN}{suffix}?lang=fa")
    assert older.status_code == 404
    assert 'dir="rtl"' in older.text and _tr("fa", "error.older") in older.text
    technical = [attrs["href"] for attrs, _text in _anchors(older.text)]
    assert f"/runs/{OLDER_RUN}" in technical
    for response in (missing, older):
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert "content-disposition" not in response.headers
        assert "<script" not in response.text
        _assert_shell_headers(response)


# --- assets ------------------------------------------------------------------------------


def test_the_asset_allowlist_is_exactly_the_packaged_guide_files() -> None:
    assert set(GUIDE_STATIC_ASSETS) | set(GUIDE_CATALOG_ASSETS) == {
        "guide.css",
        "guide-setup.css",
        "guide-story.css",
        "guide-core.js",
        "guide-i18n.js",
        "guide-connection.js",
        "guide-start.js",
        "guide-setup.js",
        "guide-progress.js",
        "guide-story.js",
        "guide-map.js",
        "i18n-en.json",
        "i18n-de.json",
        "i18n-fa.json",
    }
    packaged = files("adlife.city").joinpath("static", "guide")
    assert {item.name for item in packaged.iterdir()} == set(GUIDE_STATIC_ASSETS)
    templates = files("adlife.city").joinpath("templates", "guide")
    assert {item.name for item in templates.iterdir()} == set(GUIDE_TEMPLATES)


@pytest.mark.parametrize(("name", "media_type"), sorted(GUIDE_STATIC_ASSETS.items()))
def test_static_assets_are_the_exact_packaged_bytes(
    client: TestClient, name: str, media_type: str
) -> None:
    response = client.get(f"/guide/assets/{name}?ignored=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    assert response.content == files("adlife.city").joinpath("static", "guide", name).read_bytes()
    _assert_shell_headers(response)


@pytest.mark.parametrize("language", LANGUAGES)
def test_catalog_assets_are_the_exact_packaged_catalog_bytes(
    client: TestClient, language: Language
) -> None:
    response = client.get(f"/guide/assets/i18n-{language}.json")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    packaged = files("adlife.i18n").joinpath("catalogs", f"{language}.json").read_bytes()
    assert response.content == catalog_bytes(language) == packaged
    _assert_shell_headers(response)


@pytest.mark.parametrize(
    "name",
    [
        "nope.js",
        "GUIDE.CSS",
        "guide.css.map",
        "i18n-xx.json",
        "i18n-EN.json",
        "en.json",
        "base.html.j2",
        "workbench.js",
        "guide_web.py",
        "%2e%2e%2fworkbench.js",
    ],
)
def test_unknown_asset_names_are_not_found(client: TestClient, name: str) -> None:
    response = client.get(f"/guide/assets/{name}")
    assert response.status_code == 404
    if "%" not in name:
        assert response.json()["error"]["code"] == "not-found"


def test_an_oversized_asset_is_never_served(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guide_web, "MAX_GUIDE_ASSET_BYTES", 16)
    for name in ("i18n-en.json", "guide.css"):
        # The workbench middleware turns the refusal into its bounded, value-free 500.
        response = client.get(f"/guide/assets/{name}")
        assert response.status_code == 500
        assert response.headers["content-type"] == "application/json"
        assert len(response.content) < 512


# --- templates ---------------------------------------------------------------------------


def test_templates_are_allow_listed_lf_text_in_a_strict_autoescaped_environment() -> None:
    for name in GUIDE_TEMPLATES:
        source = guide_template_source(name)
        assert source is not None and "\r" not in source
        packaged = files("adlife.city").joinpath("templates", "guide", name).read_bytes()
        assert source == packaged.decode("utf-8").replace("\r\n", "\n")
    for name in ("nope.html.j2", "../static/workbench.html", "BASE.HTML.J2", ""):
        assert guide_template_source(name) is None
    environment = guide_environment()
    assert environment is guide_environment()
    assert environment.autoescape is True
    assert environment.undefined is StrictUndefined
    assert environment.keep_trailing_newline is True


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xef\xbb\xbf<!doctype html>", "byte order mark"),
        (b"<p>\xff</p>", "not valid UTF-8"),
        (b"x" * 65_537, "size limit"),
    ],
    ids=["bom", "invalid-utf8", "oversized"],
)
def test_a_damaged_packaged_template_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes, message: str
) -> None:
    folder = tmp_path / "templates" / "guide"
    folder.mkdir(parents=True)
    (folder / "base.html.j2").write_bytes(content)
    monkeypatch.setattr(guide_web, "files", lambda _package: tmp_path)
    with pytest.raises(RuntimeError, match=message):
        guide_template_source("base.html.j2")


def test_a_missing_packaged_template_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guide_web, "files", lambda _package: tmp_path)
    with pytest.raises(RuntimeError, match="unavailable"):
        guide_template_source("base.html.j2")


# --- story API ---------------------------------------------------------------------------


def test_the_story_api_returns_the_verified_story_json(
    client: TestClient, workspace: _Workspace
) -> None:
    response = client.get(f"/api/runs/{STORY_RUN}/story")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content == story_json_bytes(build_run_story(workspace.story))
    assert response.json()["run"]["run_id"] == STORY_RUN
    _assert_shell_headers(response)


@pytest.mark.parametrize(
    ("run_id", "status", "code"),
    [
        (MISSING_RUN, 404, "not-found"),
        ("Not-Portable", 404, "not-found"),
        ("con", 404, "not-found"),
        (OLDER_RUN, 404, "story-unavailable"),
    ],
)
def test_the_story_api_refuses_with_the_shared_error_envelope(
    client: TestClient, run_id: str, status: int, code: str
) -> None:
    response = client.get(f"/api/runs/{run_id}/story")
    assert response.status_code == status
    document = response.json()
    assert document["schema_version"] == 1 and document["error"]["code"] == code
    assert run_id not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("query", ["?lang=en", "?x=1", "?lang=fa&lang=fa"])
def test_the_story_api_takes_no_query(client: TestClient, query: str) -> None:
    response = client.get(f"/api/runs/{STORY_RUN}/story{query}")
    assert response.status_code == 422
    assert response.json() == INVALID_QUERY


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (GuideRunMissing(), 404, "not-found"),
        (GuideRunUnverifiable(), 409, "run-unavailable"),
        (CorruptRunArtifact("broken"), 409, "run-unavailable"),
        (TypeError("odd evidence"), 409, "run-unavailable"),
        (ValueError("odd evidence"), 409, "run-unavailable"),
        (RunStoryUnavailable("older"), 404, "story-unavailable"),
        (RunStoryTooLarge("huge"), 409, "story-too-large"),
    ],
)
def test_story_failures_map_to_value_free_refusals(
    error: Exception, status: int, code: str
) -> None:
    with TestClient(_bare_app(_refuse(error)), base_url=HOST) as client:
        response = client.get(f"/api/runs/{STORY_RUN}/story")
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert str(error) not in response.text or not str(error)
        page = client.get(f"/guide/runs/{STORY_RUN}/report")
        assert page.status_code == status
        assert page.headers["content-type"] == "text/html; charset=utf-8"


def test_an_oversized_story_is_refused_by_the_api_and_the_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = client.app.state.guide_story_cache  # type: ignore[attr-defined]
    cache.clear()
    monkeypatch.setattr(run_story_module, "MAX_STORY_BYTES", 1_024)
    try:
        response = client.get(f"/api/runs/{STORY_RUN}/story")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "story-too-large"
        page = client.get(f"/guide/runs/{STORY_RUN}/report?lang=de")
        assert page.status_code == 409
        assert _tr("de", "error.too_large") in page.text
    finally:
        cache.clear()


def test_a_report_over_its_size_cap_is_refused_not_truncated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_report_module, "MAX_RUN_REPORT_BYTES", 2_048)
    for path in REPORTS:
        response = client.get(f"{path}?lang=fa")
        assert response.status_code == 409
        assert _tr("fa", "error.too_large") in response.text
        assert "content-disposition" not in response.headers


# --- report ------------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_report_view_is_the_rendered_report_with_its_own_policy(
    client: TestClient, workspace: _Workspace, language: Language
) -> None:
    response = client.get(f"/guide/runs/{STORY_RUN}/report?lang={language}")
    rendered = render_run_report_html(
        build_run_story(workspace.story),
        language=language,
        links=_report_links(STORY_RUN, language),
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.content == rendered.content
    policy = response.headers["content-security-policy"]
    assert policy == rendered.content_security_policy + "; frame-ancestors 'none'"
    assert policy == _meta_policy(response.text) + "; frame-ancestors 'none'"
    assert "style-src 'self'" not in policy and "img-src 'self'" in policy
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in response.headers
    hrefs = {attrs.get("href") for attrs, _text in _anchors(response.text)}
    assert f"/guide/runs/{STORY_RUN}?lang={language}" in hrefs
    assert f"/guide/runs/{STORY_RUN}/report/download?lang={language}" in hrefs


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_report_download_is_a_standalone_attachment(
    client: TestClient, workspace: _Workspace, language: Language
) -> None:
    response = client.get(f"/guide/runs/{STORY_RUN}/report/download?lang={language}")
    rendered = render_run_report_html(
        build_run_story(workspace.story), language=language, links=None
    )
    assert response.status_code == 200
    assert response.content == rendered.content
    assert response.headers["content-disposition"] == (
        f'attachment; filename="adlife-report-{STORY_RUN}-{language}.html"'
    )
    policy = response.headers["content-security-policy"]
    assert policy == _meta_policy(response.text) + "; frame-ancestors 'none'"
    assert "img-src 'none'" in policy
    assert 'href="/' not in response.text
    assert all(
        (attrs.get("href") or "#").startswith("#") for attrs, _text in _anchors(response.text)
    )
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


# --- the verified story cache ------------------------------------------------------------


def test_the_story_cache_skips_reverification_until_a_byte_changes(
    tmp_path: Path, verifications: list[str]
) -> None:
    root = tmp_path / "workspace"
    saved_story_run(root, "mock", run_id="guide-tamper")
    app = create_city_workbench_app(prepare_workbench_workspace(root), csrf_token=TOKEN)
    artifact = root / "city-runs" / "guide-tamper" / "outputs" / "response-state.json"
    with TestClient(app, base_url=HOST) as client:
        first = client.get("/api/runs/guide-tamper/story")
        assert first.status_code == 200 and verifications == ["guide-tamper"]
        assert client.get("/api/runs/guide-tamper/story").content == first.content
        assert client.get("/guide/runs/guide-tamper/report?lang=fa").status_code == 200
        assert client.get("/guide/runs/guide-tamper/report/download").status_code == 200
        assert verifications == ["guide-tamper"]

        original = _tamper(artifact)
        refused = client.get("/api/runs/guide-tamper/story")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "run-unavailable"
        assert client.get("/guide/runs/guide-tamper/report").status_code == 409
        # A refusal is never cached: each request verifies the changed bytes again.
        assert len(verifications) == 3

        artifact.write_bytes(original)
        restored = client.get("/api/runs/guide-tamper/story")
        assert restored.status_code == 200 and restored.content == first.content
        assert client.get("/api/runs/guide-tamper/story").status_code == 200
        assert len(verifications) == 4


class _FakeRuns:
    def __init__(self) -> None:
        self.fingerprints: dict[str, str | None] = {}
        self.loads: list[str] = []

    def load(self, run_id: str) -> StoredCityRun:
        self.loads.append(run_id)
        return SimpleNamespace(run_id=run_id, load=len(self.loads))  # type: ignore[return-value]

    def fingerprint(self, run_id: str) -> str | None:
        return self.fingerprints.get(run_id)


@pytest.fixture
def fake_runs(monkeypatch: pytest.MonkeyPatch) -> _FakeRuns:
    monkeypatch.setattr(guide_web, "build_run_story", lambda stored: (stored.run_id, stored.load))
    return _FakeRuns()


def test_the_cache_hits_only_on_an_equal_known_fingerprint(fake_runs: _FakeRuns) -> None:
    cache = guide_web._StoryCache(fake_runs.load, fake_runs.fingerprint)
    fake_runs.fingerprints["a"] = "one"
    assert cache.get("a") == ("a", 1)
    assert cache.get("a") == ("a", 1)
    assert fake_runs.loads == ["a"]
    fake_runs.fingerprints["a"] = "two"
    assert cache.get("a") == ("a", 2)
    assert cache.get("a") == ("a", 2)
    fake_runs.fingerprints["a"] = None
    assert cache.get("a") == ("a", 3)
    assert cache.get("a") == ("a", 4)
    assert fake_runs.loads == ["a"] * 4
    cache.clear()
    fake_runs.fingerprints["a"] = "two"
    assert cache.get("a") == ("a", 5)


def test_the_cache_never_keeps_a_story_whose_bytes_moved_while_it_was_built(
    fake_runs: _FakeRuns,
) -> None:
    moving = iter(f"print-{index}" for index in range(100))
    cache = guide_web._StoryCache(fake_runs.load, lambda _run_id: next(moving))
    assert cache.get("a") == ("a", 1)
    assert cache.get("a") == ("a", 2)
    assert fake_runs.loads == ["a", "a"]


def test_the_cache_keeps_the_eight_most_recent_stories(fake_runs: _FakeRuns) -> None:
    cache = guide_web._StoryCache(fake_runs.load, fake_runs.fingerprint)
    names = [f"run-{index}" for index in range(9)]
    for name in names:
        fake_runs.fingerprints[name] = f"print-{name}"
    for name in names[:8]:
        cache.get(name)
    cache.get(names[0])  # most recently used again, so run-1 is now the oldest
    cache.get(names[8])
    assert len(fake_runs.loads) == 9
    cache.get(names[0])
    assert len(fake_runs.loads) == 9
    cache.get(names[1])
    assert fake_runs.loads[-1] == names[1] and len(fake_runs.loads) == 10


def test_the_cache_never_keeps_a_refusal(fake_runs: _FakeRuns) -> None:
    attempts: list[str] = []

    def refuse(run_id: str) -> StoredCityRun:
        attempts.append(run_id)
        raise GuideRunUnverifiable

    cache = guide_web._StoryCache(refuse, lambda _run_id: "same")
    for _attempt in range(2):
        with pytest.raises(GuideRunUnverifiable):
            cache.get("a")
    assert attempts == ["a", "a"]


# --- content fingerprint -----------------------------------------------------------------


def test_the_content_fingerprint_names_the_exact_bytes_of_one_run(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    saved_story_run(root, "mock", run_id="guide-print")
    repository = WorkbenchRunRepository(prepare_workbench_workspace(root))
    before = _snapshot(root)
    first = repository.content_fingerprint("guide-print")
    assert first is not None and re.fullmatch(r"[0-9a-f]{64}", first)
    assert repository.content_fingerprint("guide-print") == first
    assert _snapshot(root) == before

    artifact = root / "city-runs" / "guide-print" / "outputs" / "response-state.json"
    original = _tamper(artifact)
    changed = repository.content_fingerprint("guide-print")
    assert changed is not None and changed != first
    artifact.write_bytes(original)
    assert repository.content_fingerprint("guide-print") not in {None, changed}

    extra = root / "city-runs" / "guide-print" / "outputs" / "extra.json"
    extra.write_bytes(b"{}")
    assert repository.content_fingerprint("guide-print") not in {None, first, changed}


@pytest.mark.parametrize("run_id", ["absent-run", "Not-Portable", "../city-runs", "con", ""])
def test_the_content_fingerprint_is_none_for_anything_it_cannot_vouch_for(
    tmp_path: Path, run_id: str
) -> None:
    repository = WorkbenchRunRepository(prepare_workbench_workspace(tmp_path / "workspace"))
    assert repository.content_fingerprint(run_id) is None


# --- reads only --------------------------------------------------------------------------


def test_every_guide_read_leaves_the_workspace_unchanged(
    client: TestClient, workspace: _Workspace
) -> None:
    before = _snapshot(workspace.root)
    paths = [
        *PAGES,
        *REPORTS,
        f"/guide/runs/{OLDER_RUN}/report",
        f"/guide/runs/{MISSING_RUN}/report/download",
        "/guide/jobs/job-1",
    ]
    for language in LANGUAGES:
        for path in paths:
            client.get(f"{path}?lang={language}")
    for name in (*GUIDE_STATIC_ASSETS, *GUIDE_CATALOG_ASSETS, "nope.js"):
        client.get(f"/guide/assets/{name}")
    for run_id in (STORY_RUN, OLDER_RUN, MISSING_RUN):
        client.get(f"/api/runs/{run_id}/story")
    assert _snapshot(workspace.root) == before
    assert not list(workspace.root.rglob("*.html"))
