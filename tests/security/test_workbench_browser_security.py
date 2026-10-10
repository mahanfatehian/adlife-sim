from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

import adlife.city.workbench_web as workbench_web
from adlife.city.workbench_security import MAX_WORKBENCH_BODY_BYTES
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.test_city_workbench_browser import (
    _installed_browser,
    _open_page,
    _serve,
    _workbench,
)
from tests.unit.city import test_workbench_web as web_helpers


class _AssetReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []
        self.inline_handlers: list[str] = []
        self.inline_scripts = 0
        self._script_without_source = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        for name in ("href", "src"):
            value = attributes.get(name)
            if value is not None:
                self.references.append(value)
        self.inline_handlers.extend(name for name, _value in attrs if name.startswith("on"))
        if tag == "script" and "src" not in attributes:
            self._script_without_source = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_without_source:
            self.inline_scripts += 1
            self._script_without_source = False


def _job_documents() -> tuple[dict[str, object], dict[str, object]]:
    job_id = "job-" + "a" * 32
    settings = {
        "schema_version": 1,
        "city_id": "fictional-grid-v2",
        "scenario_id": "city-study",
        "campaign_id": "fictional-campaign",
        "seed": "42",
        "agent_count": 20,
        "days": 2,
        "response_mode": "deterministic-rules",
    }
    queued: dict[str, object] = {
        "schema_version": 1,
        "job_id": job_id,
        "run_id": "city-study-001",
        "accepted_settings": settings,
        "phase": "queued",
        "created_at": "2026-10-10T00:00:00.000000Z",
        "updated_at": "2026-10-10T00:00:00.000000Z",
        "cancellation_requested": False,
        "can_cancel": True,
        "error": None,
        "result": None,
    }
    return settings, queued


def test_served_package_assets_expose_no_external_or_persistent_execution_surface(
    tmp_path: Path,
) -> None:
    client, _root = web_helpers._client(tmp_path)
    with client:
        shell = client.get("/")
        stylesheet = client.get("/assets/workbench.css")
        script = client.get("/assets/workbench.js")
        icon = client.get("/assets/workbench-icon.svg")
        capabilities = client.get("/api/workbench")

    assert shell.status_code == stylesheet.status_code == script.status_code == 200
    assert icon.status_code == capabilities.status_code == 200
    policy = shell.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "script-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "base-uri 'none'" in policy
    assert "form-action 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "'unsafe-inline'" not in policy
    assert "'unsafe-eval'" not in policy

    parser = _AssetReferenceParser()
    parser.feed(shell.text)
    assert parser.inline_handlers == []
    assert parser.inline_scripts == 0
    assert sorted(parser.references) == [
        "#main-content",
        "/",
        "/",
        "/assets/workbench-icon.svg",
        "/assets/workbench.css",
        "/assets/workbench.js",
    ]
    assert all(reference.startswith(("/", "#")) for reference in parser.references)

    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "document.writeln",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "serviceWorker",
        "Worker(",
        "SharedWorker(",
        "caches.",
        "importScripts(",
        "WebSocket(",
        "EventSource(",
        "http://",
        "https://",
        "//outside",
    ):
        assert forbidden not in script.text
    lowered_css = stylesheet.text.lower()
    for forbidden in ("@import", "@font-face", "url(", "expression("):
        assert forbidden not in lowered_css
    assert re.search(r"(?m)^\s*behavior\s*:", lowered_css) is None
    lowered_icon = icon.text.lower()
    for forbidden in ("<script", "<foreignobject", "href="):
        assert forbidden not in lowered_icon

    for response in (shell, stylesheet, script, icon, capabilities):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in response.headers
        assert "server" not in response.headers


def test_same_origin_write_boundary_refuses_credential_bearing_bad_requests_without_artifacts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, root = web_helpers._client(tmp_path)
    secret = "Authorization: Bearer sk-refused-write-secret-1234567890"
    document = json.dumps({"api_key": secret}).encode()
    before = web_helpers._snapshot(root)

    with caplog.at_level(logging.ERROR), client:
        missing_csrf = client.post(
            "/api/jobs",
            headers={"Content-Type": "application/json", "Origin": web_helpers.ORIGIN},
            content=document,
        )
        hostile_origin = client.post(
            "/api/jobs",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://outside.invalid",
                "X-AdLife-CSRF": web_helpers.TOKEN,
            },
            content=document,
        )
        wrong_type = client.post(
            "/api/jobs",
            headers={
                "Content-Type": "text/plain",
                "Origin": web_helpers.ORIGIN,
                "X-AdLife-CSRF": web_helpers.TOKEN,
            },
            content=document,
        )
        too_large = client.post(
            "/api/jobs",
            headers={
                "Content-Type": "application/json",
                "Origin": web_helpers.ORIGIN,
                "X-AdLife-CSRF": web_helpers.TOKEN,
            },
            content=(b"x" * (MAX_WORKBENCH_BODY_BYTES + 1)),
        )

    assert [
        (missing_csrf.status_code, missing_csrf.json()["error"]["code"]),
        (hostile_origin.status_code, hostile_origin.json()["error"]["code"]),
        (wrong_type.status_code, wrong_type.json()["error"]["code"]),
        (too_large.status_code, too_large.json()["error"]["code"]),
    ] == [
        (403, "invalid-csrf"),
        (403, "invalid-origin"),
        (400, "invalid-content-type"),
        (413, "request-too-large"),
    ]
    combined = (
        "".join(response.text for response in (missing_csrf, hostile_origin, wrong_type, too_large))
        + caplog.text
    )
    assert secret not in combined
    assert "outside.invalid" not in combined
    assert web_helpers._snapshot(root) == before
    assert not (root / "city-runs").exists()


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/api/catalog/cities/%2e%2e", 404),
        ("/api/catalog/cities/%2e%2e%2fworkbench.html", 404),
        ("/api/runs/%2e%2e/workbench-input", 404),
        ("/runs/%2e%2e", 404),
        ("/assets/%2e%2e/workbench.html", 404),
        ("/static/%2e%2e/workbench.js", 404),
        ("/api/workbench?api_key=sk-query-secret-1234567890", 422),
        ("/api/runs?offset=..%2f..%2fprivate", 422),
        ("/api/runs?offset=0&limit=20&limit=21", 422),
        ("/api/runs/valid-run/frame?minute=sk-query-secret-1234567890", 422),
    ],
)
def test_traversal_and_query_abuse_are_value_free_refusals(
    tmp_path: Path,
    path: str,
    expected_status: int,
) -> None:
    client, root = web_helpers._client(tmp_path)
    before = web_helpers._snapshot(root)
    with client:
        response = client.get(path)

    assert response.status_code == expected_status
    assert "sk-query-secret" not in response.text
    assert ".." not in response.text
    assert "workbench.html" not in response.text
    assert web_helpers._snapshot(root) == before


def test_credential_shaped_invalid_draft_is_not_echoed_into_browser_or_artifacts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    browser_path = _installed_browser()
    secret = "Authorization: Bearer sk-invalid-browser-draft-1234567890"

    with caplog.at_level(logging.ERROR), _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-name").fill(secret)
            page.locator("#campaign-continue").click()
            with page.expect_response("**/api/scenarios/validate") as response_info:
                page.locator("#validate-run").click()
            response = response_info.value

            expect(page.locator("#campaign-name-error")).to_contain_text("invalid")
            assert secret not in response.text()
            assert secret not in page.locator("body").inner_text()
            assert secret not in caplog.text
            assert not (root / "city-runs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_tampered_worker_failure_cannot_render_credential_shaped_text(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    secret = "Authorization: Bearer sk-browser-worker-secret-1234567890"
    _settings, queued = _job_documents()
    job_id = str(queued["job_id"])
    failed = {
        **queued,
        "phase": "failed",
        "can_cancel": False,
        "error": {"code": "evaluation-failed", "message": secret},
    }

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.route(
                "**/api/jobs",
                lambda route: route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "schema_version": 1,
                            "job": queued,
                            "status_url": f"/api/jobs/{job_id}",
                        }
                    ),
                ),
            )
            page.route(
                f"**/api/jobs/{job_id}",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(failed),
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()

            expect(page.locator("#error-banner")).to_contain_text(
                "incompatible failure record",
                timeout=10_000,
            )
            assert secret not in page.locator("body").inner_text()
            expect(page.locator("#job-inspect-link")).to_be_hidden()
            assert not (root / "city-runs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_non_failed_worker_record_cannot_smuggle_a_failure_into_the_dom(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    secret = "Authorization: Bearer sk-active-worker-secret-1234567890"
    _settings, queued = _job_documents()
    job_id = str(queued["job_id"])
    poisoned = {
        **queued,
        "error": {"code": "evaluation-failed", "message": secret},
    }

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.route(
                "**/api/jobs",
                lambda route: route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "schema_version": 1,
                            "job": poisoned,
                            "status_url": f"/api/jobs/{job_id}",
                        }
                    ),
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()

            expect(page.locator("#error-banner")).to_contain_text(
                "non-failed worker record carried a failure",
                timeout=10_000,
            )
            assert secret not in page.locator("body").inner_text()
            expect(page.locator("#job-inspect-link")).to_be_hidden()
            assert not (root / "city-runs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_real_worker_exception_is_screened_from_dom_response_log_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    browser_path = _installed_browser()
    secret = "Authorization: Bearer sk-real-worker-secret-1234567890"
    private_path = r"C:\private\provider\response.json"

    def fail_prepare(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"{secret} at {private_path}")

    monkeypatch.setattr(workbench_web.WorkbenchRunRepository, "prepare", fail_prepare)
    with caplog.at_level(logging.ERROR), _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        job_urls: list[str] = []
        try:
            page.on(
                "request",
                lambda request: (
                    job_urls.append(request.url)
                    if re.fullmatch(
                        rf"{re.escape(base_url)}/api/jobs/job-[0-9a-f]{{32}}",
                        request.url,
                    )
                    else None
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("screened-worker-failure")
            page.locator("#agent-count").fill("1")
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()

            expect(page.locator("#job-phase")).to_have_text("FAILED", timeout=15_000)
            expect(page.locator("#job-message")).to_have_text(
                "The deterministic city evaluation failed."
            )
            assert job_urls
            terminal_response = page.request.get(job_urls[-1])
            assert terminal_response.status == 200
            exposed = page.locator("body").inner_text() + terminal_response.text() + caplog.text
            assert secret not in exposed
            assert private_path not in exposed
            assert not (root / "city-runs" / "screened-worker-failure").exists()
        finally:
            browser.close()
            playwright.stop()


def test_external_inspector_url_from_a_tampered_library_is_inert(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    external_url = "https://outside.invalid/runs/stolen"
    summary: dict[str, Any] = {
        "schema_version": 1,
        "run_id": "tampered-library-run",
        "run_schema_version": 7,
        "city_id": "fictional-grid-v2",
        "city_schema_version": 2,
        "city_sha256": "a" * 64,
        "scenario_id": "city-study",
        "scenario_sha256": "b" * 64,
        "campaign_ids": ["fictional-campaign"],
        "channels": ["mobile-feed"],
        "seed": "42",
        "days": 2,
        "agent_count": 20,
        "frame_count": 2880,
        "opportunity_count": 0,
        "impression_count": 0,
        "noticed_count": 0,
        "response_count": 0,
        "inspector_url": external_url,
    }
    page_document = {
        "schema_version": 1,
        "offset": 0,
        "limit": 20,
        "returned_count": 1,
        "verified_total": 1,
        "scanned_count": 1,
        "corrupt_count": 0,
        "truncated": False,
        "runs": [summary],
    }

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        external_requests: list[str] = []
        try:
            page.route(
                re.compile(r".*/api/runs\?offset=0&limit=20$"),
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(page_document),
                ),
            )
            page.on(
                "request",
                lambda request: (
                    external_requests.append(request.url)
                    if not request.url.startswith(base_url)
                    else None
                ),
            )
            page.goto(base_url, wait_until="networkidle")

            expect(page.locator("#error-banner")).to_contain_text("run summary is incompatible")
            assert page.locator(f'a[href="{external_url}"]').count() == 0
            assert external_url not in page.locator("body").inner_text()
            assert external_requests == []
            assert not (root / "city-runs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_read_only_browser_and_api_inspection_preserve_every_source_artifact_byte(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    root = tmp_path / "workspace"
    workspace = prepare_workbench_workspace(root)
    stored = web_helpers._saved_workbench_run(
        root,
        run_id="security-read-only",
        schema_version=7,
    )
    application = create_city_workbench_app(
        workspace,
        csrf_token="read-only-security-token",
    )
    before = web_helpers._snapshot(root)
    prefix = "/api/runs/security-read-only"
    paths = [
        f"{prefix}/meta",
        f"{prefix}/city",
        f"{prefix}/agents",
        f"{prefix}/places",
        f"{prefix}/place-assignments",
        f"{prefix}/opportunity-summary",
        f"{prefix}/opportunities?minute=0",
        f"{prefix}/attention-summary",
        f"{prefix}/attention-events?minute=0",
        f"{prefix}/spatial-metrics",
        f"{prefix}/response-summary",
        f"{prefix}/response-events?minute=0",
        f"{prefix}/response-state",
        f"{prefix}/spatial-response-metrics",
        f"{prefix}/frame?minute=0",
        f"{prefix}/workbench-input",
    ]

    with _serve(application) as base_url:
        playwright, browser, page = _open_page(browser_path, base_url)
        external_requests: list[str] = []
        try:
            page.on(
                "request",
                lambda request: (
                    external_requests.append(request.url)
                    if not request.url.startswith(base_url)
                    else None
                ),
            )
            page.goto(f"{base_url}/runs/{stored.manifest.run_id}", wait_until="networkidle")
            statuses = page.evaluate(
                """async (paths) => Promise.all(paths.map(async (path) => {
                  const response = await fetch(path, {
                    cache: "no-store",
                    credentials: "same-origin",
                    redirect: "error",
                  });
                  await response.arrayBuffer();
                  return [path, response.status];
                }))""",
                paths,
            )
        finally:
            browser.close()
            playwright.stop()

    expected_statuses = {
        f"{prefix}/places": 404,
        f"{prefix}/place-assignments": 404,
    }
    assert statuses == [[path, expected_statuses.get(path, 200)] for path in paths]
    assert external_requests == []
    assert web_helpers._snapshot(root) == before
