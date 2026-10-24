"""Guide pages keep the workbench security posture: one module script, no inline code.

Every rendered guide page, the error pages included, is parsed rather than grepped: it
must load exactly its own module script and stylesheets from ``/guide/assets/``, carry
no inline script, no ``style`` attribute or element, no event handler attribute and no
off-origin URL, and be served with exactly the workbench shell headers. Saved text from
a hostile workspace reaches the guide only as JSON data or as escaped report text.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adlife.city.guide_web import GUIDE_STATIC_ASSETS
from adlife.city.workbench_web import _SHELL_HEADERS, create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.i18n import load_catalog
from tests.unit.city.run_story_fixtures import (
    HOSTILE_CAMPAIGN,
    HOSTILE_PERSONA_NAME,
    HOSTILE_SCENARIO,
    saved_story_run,
)

HOST = "http://127.0.0.1:8765"
TOKEN = "guide-pages-security-token"
STORY_RUN = "guide-secure"
MIXED_RUN = "guide-hostile-mixed"
OLDER_RUN = "guide-older-v9"
JOB_ID = "job-" + "fedcba9876543210" * 2
SETUP_CSS = ("/guide/assets/guide.css", "/guide/assets/guide-setup.css")
STORY_CSS = ("/guide/assets/guide.css", "/guide/assets/guide-story.css")
INTERACTIVE_PAGES = {
    "/guide": (SETUP_CSS, "/guide/assets/guide-start.js"),
    "/guide/new": (SETUP_CSS, "/guide/assets/guide-setup.js"),
    f"/guide/jobs/{JOB_ID}": (SETUP_CSS, "/guide/assets/guide-progress.js"),
    f"/guide/runs/{STORY_RUN}": (STORY_CSS, "/guide/assets/guide-story.js"),
}
ERROR_PAGES = {
    "/guide/jobs/job-not-a-job": 404,
    "/guide/runs/Not-Portable": 404,
    "/guide/runs/nobody-saved-this/report": 404,
    "/guide/runs/nobody-saved-this/report/download": 404,
}
_URL_ATTRIBUTES = frozenset({"href", "src", "action", "formaction", "poster", "data", "srcset"})
_FORBIDDEN_ELEMENTS = frozenset(
    {"style", "iframe", "frame", "object", "embed", "base", "form", "applet", "template"}
)


@dataclass
class _Audit:
    scripts: list[tuple[dict[str, str | None], str]] = field(default_factory=list)
    stylesheets: list[str | None] = field(default_factory=list)
    links: list[dict[str, str | None]] = field(default_factory=list)
    elements: list[str] = field(default_factory=list)
    attributes: list[tuple[str, str, str | None]] = field(default_factory=list)


class _Auditor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.audit = _Audit()
        self._script: tuple[dict[str, str | None], list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.audit.elements.append(tag)
        self.audit.attributes.extend((tag, name, value) for name, value in attrs)
        if tag == "script":
            self._script = (attributes, [])
        elif tag == "link":
            self.audit.links.append(attributes)
            if attributes.get("rel") == "stylesheet":
                self.audit.stylesheets.append(attributes.get("href"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag == "script":
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            attributes, body = self._script
            self.audit.scripts.append((attributes, "".join(body)))
            self._script = None


class _Controls(HTMLParser):
    """The text of every link and button, the only things a visitor can activate."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self._open: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"a", "button"}:
            self._open.append([])

    def handle_data(self, data: str) -> None:
        for text in self._open:
            text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "button"} and self._open:
            self.texts.append(" ".join("".join(self._open.pop()).split()))


def _control_texts(document: str) -> list[str]:
    parser = _Controls()
    parser.feed(document)
    parser.close()
    return parser.texts


def _audit(document: str) -> _Audit:
    auditor = _Auditor()
    auditor.feed(document)
    auditor.close()
    return auditor.audit


def _assert_inert_markup(audit: _Audit) -> None:
    for tag, name, value in audit.attributes:
        assert not name.startswith("on"), (tag, name)
        assert name not in {"style", "srcdoc"}, (tag, name)
        if name in _URL_ATTRIBUTES:
            url = (value or "").strip()
            assert url.startswith(("/", "#", "?")) and not url.startswith("//"), (tag, url)
        if tag == "meta" and name == "http-equiv":
            assert (value or "").lower() != "refresh"
    assert not _FORBIDDEN_ELEMENTS.intersection(audit.elements)
    for attributes, body in audit.scripts:
        assert attributes.get("src"), "inline script"
        assert body.strip() == "", "inline script body"


def _assert_shell_headers(response: Any) -> None:
    for name, value in _SHELL_HEADERS.items():
        assert response.headers[name] == value, name
    assert response.headers["cache-control"] == "no-store"
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "unsafe-inline" not in policy


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("guide-pages") / "workspace"
    saved_story_run(root, "rules", run_id=STORY_RUN)
    saved_story_run(root, "mixed", run_id=MIXED_RUN)
    saved_story_run(root, "v9", run_id=OLDER_RUN)
    return root


@pytest.fixture(scope="module")
def client(workspace: Path) -> Iterator[TestClient]:
    app = create_city_workbench_app(prepare_workbench_workspace(workspace), csrf_token=TOKEN)
    with TestClient(app, base_url=HOST) as test_client:
        yield test_client


@pytest.mark.parametrize("language", ["en", "de", "fa"])
@pytest.mark.parametrize("path", sorted(INTERACTIVE_PAGES))
def test_each_interactive_page_loads_exactly_its_own_module_and_stylesheets(
    client: TestClient, path: str, language: str
) -> None:
    response = client.get(f"{path}?lang={language}")
    assert response.status_code == 200
    _assert_shell_headers(response)
    audit = _audit(response.text)
    stylesheets, script = INTERACTIVE_PAGES[path]
    assert tuple(audit.stylesheets) == stylesheets
    assert [attributes for attributes, _body in audit.scripts] == [
        {"type": "module", "src": script}
    ]
    assert [link.get("rel") for link in audit.links] == ["icon", "stylesheet", "stylesheet"]
    _assert_inert_markup(audit)
    for url in (*stylesheets, script):
        asset = client.get(url)
        assert asset.status_code == 200, url
        assert asset.headers["content-type"] == GUIDE_STATIC_ASSETS[url.rsplit("/", 1)[1]]


@pytest.mark.parametrize("language", ["en", "fa"])
@pytest.mark.parametrize(("path", "status"), sorted(ERROR_PAGES.items()))
def test_error_pages_load_no_script_and_only_the_base_stylesheet(
    client: TestClient, path: str, status: int, language: str
) -> None:
    response = client.get(f"{path}?lang={language}")
    assert response.status_code == status
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    _assert_shell_headers(response)
    audit = _audit(response.text)
    assert audit.scripts == []
    assert tuple(audit.stylesheets) == ("/guide/assets/guide.css",)
    _assert_inert_markup(audit)
    assert "adlife-csrf-token" not in response.text


def test_assets_and_the_story_api_carry_the_shell_headers(client: TestClient) -> None:
    for name in (*GUIDE_STATIC_ASSETS, "i18n-en.json", "i18n-fa.json"):
        response = client.get(f"/guide/assets/{name}")
        assert response.status_code == 200, name
        _assert_shell_headers(response)
        assert response.headers["x-content-type-options"] == "nosniff"
    story = client.get(f"/api/runs/{STORY_RUN}/story")
    assert story.status_code == 200
    assert story.headers["content-type"] == "application/json"
    _assert_shell_headers(story)


def test_the_report_policy_replaces_the_shell_policy_and_forbids_script(
    client: TestClient,
) -> None:
    for path in (
        f"/guide/runs/{STORY_RUN}/report?lang=fa",
        f"/guide/runs/{STORY_RUN}/report/download",
    ):
        response = client.get(path)
        assert response.status_code == 200
        policy = response.headers["content-security-policy"]
        assert policy != _SHELL_HEADERS["Content-Security-Policy"]
        assert "script-src 'none'" in policy and policy.endswith("frame-ancestors 'none'")
        assert "unsafe-inline" not in policy
        audit = _audit(response.text)
        assert audit.scripts == []
        assert audit.elements.count("style") == 1
        for tag, name, _value in audit.attributes:
            assert not name.startswith("on") and name != "style", (tag, name)


@pytest.mark.parametrize("language", ["en", "fa"])
@pytest.mark.parametrize(
    "path",
    [
        *sorted(INTERACTIVE_PAGES),
        *sorted(ERROR_PAGES),
        f"/guide/runs/{STORY_RUN}/report",
        f"/guide/runs/{STORY_RUN}/report/download",
        # An older run once offered the removed inspector from its report refusal.
        f"/guide/runs/{OLDER_RUN}",
        f"/guide/runs/{OLDER_RUN}/report",
    ],
)
def test_no_guide_page_or_report_links_to_the_removed_technical_view(
    client: TestClient, path: str, language: str
) -> None:
    response = client.get(f"{path}?lang={language}")
    assert response.status_code in {200, 404}, response.status_code
    catalog = load_catalog(language)
    labels = {"Technical view"}
    if catalog.has("common.technical_view"):  # the key itself is due to be deleted
        labels.add(catalog.text("common.technical_view"))
    # Prose may still mention the old view until its wording changes; no control names it.
    controls = _control_texts(response.text)
    assert not [text for text in controls if any(label in text for label in labels)], controls
    audit = _audit(response.text)
    # The report's own "technical details" section stays; no technical control remains.
    assert not [
        value
        for _tag, name, value in audit.attributes
        if name == "id" and (value or "").endswith("-technical")
    ]
    hrefs = [value or "" for _tag, name, value in audit.attributes if name == "href"]
    assert hrefs, "every page links somewhere"
    for href in hrefs:
        # Only the guide's own pages, its assets, the shared icon and in-page anchors.
        assert href.startswith(("/guide", "#", "?", "/assets/workbench-icon.svg")), href
        assert not href.startswith(("/runs/", "/static/")) and href != "/", href
    assert 'target="_blank"' not in response.text


@pytest.mark.parametrize("mode", ["mock", "local"])
def test_a_hostile_v10_workspace_reaches_the_guide_only_as_data(tmp_path: Path, mode: str) -> None:
    from tests.security.test_workbench_api_security import (
        _HOSTILE_NAME,
        _hostile_v10_workspace,
    )

    root = _hostile_v10_workspace(tmp_path, mode)
    app = create_city_workbench_app(prepare_workbench_workspace(root), csrf_token=TOKEN)
    with TestClient(app, base_url=HOST) as client:
        story = client.get("/api/runs/hostile-v10/story")
        page = client.get("/guide/runs/hostile-v10?lang=fa")
        report = client.get("/guide/runs/hostile-v10/report?lang=fa")
        download = client.get("/guide/runs/hostile-v10/report/download?lang=fa")

    assert story.status_code == 200
    assert story.headers["content-type"] == "application/json"
    _assert_shell_headers(story)
    names = [persona["display_name"] for persona in story.json()["personas"]]
    assert _HOSTILE_NAME in names
    # The page shell embeds no saved evidence; the page script fetches it as JSON.
    assert page.status_code == 200
    for marker in ("<img src=x", "onerror", "alert(1)"):
        assert marker not in page.text
    _assert_inert_markup(_audit(page.text))
    for response in (report, download):
        assert response.status_code == 200
        assert "<img src=x" not in response.text and "onerror=alert" in response.text
        audit = _audit(response.text)
        assert "img" not in audit.elements and audit.scripts == []
        assert not [name for _tag, name, _value in audit.attributes if name.startswith("on")]


def test_hostile_scenario_campaign_and_persona_names_stay_data(client: TestClient) -> None:
    story = client.get(f"/api/runs/{MIXED_RUN}/story")
    assert story.status_code == 200
    document = story.json()
    assert document["run"]["scenario"]["name"] == HOSTILE_SCENARIO
    assert document["run"]["campaign"]["name"] == HOSTILE_CAMPAIGN
    text = json.dumps(document, ensure_ascii=False)
    page = client.get(f"/guide/runs/{MIXED_RUN}?lang=fa")
    for marker in ("window.pwned", "<svg onload", "</bdi><script>"):
        assert marker in text
        assert marker not in page.text
    report = client.get(f"/guide/runs/{MIXED_RUN}/report?lang=fa")
    assert report.status_code == 200
    for marker in ("<svg onload", "</bdi><script>", "<script>window", "<img src=x"):
        assert marker not in report.text
    audit = _audit(report.text)
    assert audit.scripts == [] and "svg" not in audit.elements
    assert not [name for _tag, name, _value in audit.attributes if name.startswith("on")]
    assert HOSTILE_PERSONA_NAME not in report.text
