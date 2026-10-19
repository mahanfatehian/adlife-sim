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
from adlife.city.runs import create_city_run
from adlife.city.workbench_input import WorkbenchRunDraftV2
from adlife.city.workbench_security import MAX_WORKBENCH_BODY_BYTES
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.test_city_places_browser import _workbench_assumptions_viewer
from tests.browser.test_city_workbench_browser import (
    _installed_browser,
    _open_page,
    _serve,
    _set_audience_count,
    _workbench,
)
from tests.unit.city import test_workbench_web as web_helpers
from tests.unit.city.test_workbench_input import draft_v2_data


def test_schema4_compatibility_refusal_is_value_free_and_keeps_no_active_job(
    tmp_path,
    monkeypatch,
    caplog,
):
    from fastapi.testclient import TestClient

    from adlife.city.provider_compatibility import WorkbenchProviderCompatibilityRequired
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    private = "sk-private-v10-compatibility-marker"
    absolute_path = r"C:\private\v10-provider-marker.json"

    def refuse(*_args, **_kwargs):
        error = WorkbenchProviderCompatibilityRequired()
        error.add_note(f"{private} {absolute_path}")
        raise error

    monkeypatch.setattr(workbench_web.CityJobManager, "submit", refuse)
    app = create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"),
        csrf_token=web_helpers.TOKEN,
    )
    payload = draft_v4_data(mode="rules")
    payload["settings"].update(run_id="screened-v10-refusal", agent_count=1, days=2, seed="42")
    payload["population"]["personas"][0]["count"] = 1
    with caplog.at_level(logging.ERROR), TestClient(app, base_url=web_helpers.ORIGIN) as client:
        response = web_helpers._secure_post(client, "/api/jobs", payload)
        capabilities = client.get("/api/workbench")

    assert response.status_code == 409, response.text
    assert response.json()["error"] == {
        "code": "provider-compatibility-required",
        "message": "A successful bounded-cognition provider compatibility check is required.",
        "fields": {},
    }
    assert private not in response.text + caplog.text
    assert absolute_path not in response.text + caplog.text
    assert capabilities.json()["active_job"] is None


@pytest.mark.parametrize(
    "document,query",
    [
        (b"[]", ""),
        (b"true", ""),
        (b"null", ""),
        (b'{"effect_scope":true}', ""),
        (b'{"effect_scope":2}', ""),
        (b'{"effect_scope":"private-browser-marker"}', ""),
        (b'{"effect_scope":"bounded-signals-v1","schema_version":2}', ""),
        (b'{"effect_scope":"bounded-signals-v1","settings":{}}', ""),
        (
            b'{"effect_scope":"bounded-signals-v1","causal_contract_sha256":"private-browser-marker"}',
            "",
        ),
        (b'{"effect_scope":"bounded-signals-v1","effect_scope":"explanation-only"}', ""),
        (b'{"effect_scope":"bounded-signals-v1"}', "?effect_scope=bounded-signals-v1"),
    ],
)
def test_scoped_check_rejects_untrusted_body_before_controller(
    tmp_path, monkeypatch, document, query
):
    from fastapi.testclient import TestClient

    app = create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"), csrf_token=web_helpers.TOKEN
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("rejected body reached controller")

    monkeypatch.setattr(app.state.provider_controller, "check", forbidden)
    with TestClient(app, base_url=web_helpers.ORIGIN) as client:
        response = client.post(
            "/api/providers/local-ollama/revisions/" + "a" * 64 + "/check" + query,
            content=document,
            headers={
                "Origin": web_helpers.ORIGIN,
                "X-AdLife-CSRF": web_helpers.TOKEN,
                "Content-Type": "application/json",
            },
        )
    assert response.status_code in {400, 422}
    assert "private-browser-marker" not in response.text


@pytest.mark.parametrize(
    "document",
    [
        b'{"credential":"private-browser-marker",',
        b'{"credential":"private-browser-marker","extra":true}',
        b'{"credential":{"Authorization":"private-browser-marker"}}',
        b'{"credential":["api_key","private-browser-marker"]}',
        b'{"credential":true}',
        b'{"credential":"private-browser-marker\\n"}',
        b'{"credential":"private-browser-marker\\ud800"}',
        b'{"credential":"' + b"x" * 4097 + b'"}',
    ],
    ids=[
        "syntax",
        "extra",
        "nested-object",
        "nested-list",
        "boolean",
        "control",
        "surrogate",
        "key-size",
    ],
)
def test_credential_route_screens_invalid_documents_before_profile_lookup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    document: bytes,
) -> None:
    client, root = web_helpers._client(tmp_path)
    before = web_helpers._snapshot(root)
    path = "/api/providers/missing-profile/revisions/" + "a" * 64 + "/credential"
    with caplog.at_level(logging.DEBUG), client:
        response = client.post(
            path,
            content=document,
            headers={
                "Content-Type": "application/json",
                "Origin": web_helpers.ORIGIN,
                "X-AdLife-CSRF": web_helpers.TOKEN,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["fields"] == {}
        combined = response.text + caplog.text
        assert "private-browser-marker" not in combined
        assert "Authorization" not in response.text
        assert "api_key" not in response.text
        assert "JSONDecodeError" not in combined
        assert "ValidationError" not in combined
        assert response.headers["cache-control"] == "no-store"
    assert web_helpers._snapshot(root) == before


def test_credential_route_rejects_oversized_body_before_parse_or_profile_lookup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, root = web_helpers._client(tmp_path)
    before = web_helpers._snapshot(root)
    path = "/api/providers/missing-profile/revisions/" + "a" * 64 + "/credential"
    secret = "private-browser-oversize-marker"
    with caplog.at_level(logging.DEBUG), client:
        response = client.post(
            path,
            content=secret.encode() + b"x" * 8192,
            headers={
                "Content-Type": "application/json",
                "Origin": web_helpers.ORIGIN,
                "X-AdLife-CSRF": web_helpers.TOKEN,
            },
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request-too-large"
    assert secret not in response.text + caplog.text
    assert web_helpers._snapshot(root) == before


@pytest.mark.parametrize(
    "field",
    [
        "persona_id",
        "display_name",
        "occupation",
        "background",
        "mbti",
        "interests",
    ],
)
@pytest.mark.parametrize(
    "text",
    [
        "sk-" + "x" * 32,
        "Authorization: Bearer fictional-secret-1234567890",
        "fictional@example.invalid",
        "+98 912 123 4567",
        "national id: 1234567890",
        "فارسی\u202eپنهان",  # noqa: RUF001
        "فارسی\x00پنهان",  # noqa: RUF001
        "فارسی\u2066پنهان",  # noqa: RUF001
    ],
)
def test_every_persona_string_security_refusal_is_value_free_and_creates_nothing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    field: str,
    text: str,
) -> None:
    client, root = web_helpers._client(tmp_path)
    payload = web_helpers._persona_draft()
    payload["population"]["personas"][0][field] = [text] if field == "interests" else text
    before = web_helpers._snapshot(root)
    with caplog.at_level(logging.DEBUG), client:
        for path in ("/api/scenarios/validate", "/api/jobs"):
            response = web_helpers._secure_post(client, path, payload)
            assert response.status_code == 422
            assert text not in response.text + caplog.text
            assert "fictional-secret" not in response.text + caplog.text
            assert "example.invalid" not in response.text + caplog.text
        assert client.get("/api/workbench").json()["active_job"] is None
    assert web_helpers._snapshot(root) == before
    assert not (root / "city-runs").exists()


@pytest.mark.parametrize("member", ["api_key", "Authorization", "access_token", "password"])
def test_nested_persona_secret_member_names_are_never_reflected_or_reserved(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    member: str,
) -> None:
    client, root = web_helpers._client(tmp_path)
    payload = web_helpers._persona_draft()
    secret = "fake-private-nested-value-1728394056"
    payload["population"]["personas"][0]["traits"][member] = {"nested": secret}
    before = web_helpers._snapshot(root)
    with caplog.at_level(logging.DEBUG), client:
        for path in ("/api/scenarios/validate", "/api/jobs"):
            response = web_helpers._secure_post(client, path, payload)
            assert response.status_code == 422
            assert member not in response.text
            assert secret not in response.text + caplog.text
        assert client.get("/api/workbench").json()["active_job"] is None
    assert web_helpers._snapshot(root) == before


def test_schema2_ordinary_persian_is_valid_and_overflow_precedes_json_parsing(
    tmp_path: Path,
) -> None:
    client, root = web_helpers._client(tmp_path)
    payload = web_helpers._persona_draft()
    persona = payload["population"]["personas"][0]
    persona.update(
        {
            "display_name": "مینای خیالی",
            "occupation": "طراح خیالی",
            "background": "این شخصیت کاملاً خیالی است.",
        }
    )
    persona["interests"] = ["هنر", "فناوری"]
    before = web_helpers._artifact_snapshot(root)
    with client:
        assert web_helpers._post(client, payload).status_code == 200
        for path in ("/api/scenarios/validate", "/api/jobs"):
            for content, status in [(b'{"schema_version":2,', 400), (b"x" * 131073, 413)]:
                response = client.post(
                    path,
                    content=content,
                    headers={
                        "Content-Type": "application/json",
                        "Origin": web_helpers.ORIGIN,
                        "X-AdLife-CSRF": web_helpers.TOKEN,
                    },
                )
                assert response.status_code == status
        assert client.get("/api/workbench").json()["active_job"] is None
    assert web_helpers._artifact_snapshot(root) == before


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
        "workbench_input_schema_version": 3,
        "run_schema_version": 9,
        "city_id": "fictional-grid-v2",
        "scenario_id": "city-study",
        "campaign_id": "fictional-campaign",
        "seed": "42",
        "agent_count": 20,
        "days": 2,
        "response_mode": "deterministic-rules",
        "cognition_mode": "rules",
        "provider_profile_id": None,
        "provider_revision_sha256": None,
        "aggregate_output_token_ceiling": 0,
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
        city_pack_script = client.get("/assets/workbench-city-packs.js")
        persona_script = client.get("/assets/workbench-personas.js")
        persona_style = client.get("/assets/workbench-personas.css")
        provider_script = client.get("/assets/workbench-providers.js")
        provider_style = client.get("/assets/workbench-providers.css")
        icon = client.get("/assets/workbench-icon.svg")
        capabilities = client.get("/api/workbench")

    assert (
        shell.status_code
        == stylesheet.status_code
        == script.status_code
        == city_pack_script.status_code
        == 200
    )
    assert icon.status_code == capabilities.status_code == 200
    assert (
        persona_script.status_code
        == persona_style.status_code
        == provider_script.status_code
        == provider_style.status_code
        == 200
    )
    for asset in (
        city_pack_script,
        persona_script,
        persona_style,
        provider_script,
        provider_style,
    ):
        assert asset.headers["cache-control"] == "no-store"
        assert asset.headers["x-content-type-options"] == "nosniff"
        assert asset.headers["content-security-policy"] == shell.headers["content-security-policy"]
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
        "/assets/workbench-city-packs.js",
        "/assets/workbench-icon.svg",
        "/assets/workbench-personas.css",
        "/assets/workbench-personas.js",
        "/assets/workbench-providers.css",
        "/assets/workbench-providers.js",
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
        assert forbidden not in (
            script.text + city_pack_script.text + persona_script.text + provider_script.text
        )
    lowered_css = stylesheet.text.lower() + persona_style.text.lower() + provider_style.text.lower()
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


@pytest.mark.parametrize(
    "outcome",
    ["success", "server-refusal", "network-failure", "cancellation"],
)
def test_provider_credential_is_transient_and_cleared_on_every_submission_outcome(
    tmp_path: Path,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from adlife.adapters.cognition.city_credentials import (
        ProviderCredentialUnavailable,
        ResolvedCredential,
        SecretBuffer,
    )
    from adlife.city.provider_runtime import build_provider_runtime_policy

    marker = "-".join(("browser", "transient", "marker", str(1_234_567_890)))
    stored: dict[str, str] = {}

    class MemoryCredentialStore:
        def available(self, profile: Any) -> bool:
            return profile.revision_sha256 in stored

        def store(self, profile: Any, credential: SecretBuffer) -> None:
            stored[profile.revision_sha256] = credential.reveal_text()

        def resolve(self, profile: Any) -> ResolvedCredential:
            value = stored.get(profile.revision_sha256)
            if value is None:
                raise ProviderCredentialUnavailable("unavailable")
            return ResolvedCredential(
                source="keyring",
                secret=SecretBuffer(value),
                origin_approval_id=profile.origin_approval_id,
                credential_slot_id=profile.credential_slot_id,
            )

    root = tmp_path / "workspace"
    app = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="provider-browser-security-token",
        provider_runtime=build_provider_runtime_policy(
            provider_origins=("remote-acme=https://api.example.invalid/v1",),
        ),
        credential_store=MemoryCredentialStore(),
    )
    profile = {
        "schema_version": 1,
        "profile_id": "remote-acme",
        "display_label": "Remote Acme",
        "mode": "remote",
        "base_url": "https://api.example.invalid/v1",
        "model_id": "fictional-compatible-model",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "remote-acme",
    }

    with _serve(app) as base_url:
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        console_messages: list[str] = []
        page_errors: list[str] = []
        requests: list[tuple[str, str, str | None]] = []
        try:
            created = page.request.post(
                f"{base_url}/api/providers",
                headers={
                    "Content-Type": "application/json",
                    "Origin": base_url,
                    "X-AdLife-CSRF": "provider-browser-security-token",
                },
                data=profile,
            )
            assert created.status == 201, created.text()
            revision = created.json()["revision_sha256"]
            credential_url = f"{base_url}/api/providers/remote-acme/revisions/{revision}/credential"
            preload = page.request.get(f"{base_url}/api/providers")
            assert preload.status == 200, preload.text()

            page.on("console", lambda message: console_messages.append(message.text))
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "request",
                lambda request: requests.append((request.method, request.url, request.post_data)),
            )
            if outcome != "success":

                def reject_credential(route: Any) -> None:
                    if outcome == "server-refusal":
                        route.fulfill(
                            status=503,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "error": {
                                        "code": "credential-store-unavailable",
                                        "message": "Credential storage is unavailable.",
                                    }
                                }
                            ),
                        )
                    elif outcome == "cancellation":
                        route.abort("aborted")
                    else:
                        route.abort("connectionrefused")

                page.route("**/api/providers/**/credential", reject_credential)

            response = page.goto(base_url, wait_until="networkidle")
            assert response is not None and response.status == 200
            assert page_errors == [], page_errors
            configure = page.get_by_role("button", name="Configure campaign")
            expect(configure).to_be_enabled(timeout=10_000)
            configure.click()
            page.get_by_role("button", name="Review run").click()
            page.get_by_label("Source").select_option("remote")
            credential = page.locator("#provider-credential")
            save = page.locator("#provider-save-credential")
            expect(credential).to_be_visible()
            expect(save).to_be_enabled()
            credential.fill(marker)
            expect(credential).to_have_value(marker)
            assert credential.get_attribute("value") != marker

            save.click()
            expect(credential).to_have_value("")

            credential_requests = [
                item for item in requests if item[0] == "POST" and item[1] == credential_url
            ]
            assert len(credential_requests) == 1
            assert json.loads(credential_requests[0][2] or "null") == {"credential": marker}
            for _method, url, body in requests:
                assert marker not in url
                if url != credential_url:
                    assert marker not in (body or "")

            browser_state = page.evaluate(
                """async (marker) => {
                  const textMatches = [];
                  const walker = document.createTreeWalker(document, NodeFilter.SHOW_TEXT);
                  while (walker.nextNode()) {
                    if ((walker.currentNode.nodeValue || "").includes(marker)) {
                      textMatches.push(walker.currentNode.parentElement?.tagName || "text");
                    }
                  }
                  const attributeMatches = [];
                  for (const element of document.querySelectorAll("*")) {
                    for (const name of element.getAttributeNames()) {
                      if ((element.getAttribute(name) || "").includes(marker)) {
                        attributeMatches.push(`${element.tagName}:${name}`);
                      }
                    }
                  }
                  const globalMatches = [];
                  for (const name of Object.getOwnPropertyNames(window)) {
                    try {
                      const value = window[name];
                      if (typeof value === "string" && value.includes(marker)) {
                        globalMatches.push(name);
                      }
                    } catch (_error) {
                      // Cross-origin and browser-owned accessors may throw.
                    }
                  }
                  return {
                    textMatches,
                    attributeMatches,
                    globalMatches,
                    localStorage: Object.entries(localStorage),
                    sessionStorage: Object.entries(sessionStorage),
                    indexedDatabases: indexedDB.databases ?
                      (await indexedDB.databases()).map((item) => item.name) : [],
                    urls: performance.getEntriesByType("resource").map((item) => item.name),
                    pageUrl: location.href,
                  };
                }""",
                marker,
            )
            assert browser_state == {
                "textMatches": [],
                "attributeMatches": [],
                "globalMatches": [],
                "localStorage": [],
                "sessionStorage": [],
                "indexedDatabases": [],
                "urls": browser_state["urls"],
                "pageUrl": base_url + "/",
            }
            assert all(marker not in url for url in browser_state["urls"])
            assert marker not in page.content()
            assert marker not in "".join(console_messages + page_errors)
            assert marker not in caplog.text

            listing = page.request.get(f"{base_url}/api/providers")
            capabilities = page.request.get(f"{base_url}/api/workbench")
            assert listing.status == capabilities.status == 200
            assert marker not in listing.text() + capabilities.text()
            assert (outcome == "success") is bool(stored)
            for artifact in root.rglob("*"):
                if artifact.is_file():
                    assert marker.encode() not in artifact.read_bytes()
        finally:
            browser.close()
            playwright.stop()


def test_workbench_city_disclosure_covers_uploaded_sources_without_claiming_review(
    tmp_path: Path,
) -> None:
    client, _root = web_helpers._client(tmp_path)
    with client:
        response = client.get("/api/workbench")

    assert response.status_code == 200
    assert response.json()["disclosure"]["city"] == (
        "City-pack geometry; no live map or traffic feed. "
        "Uploaded sources are operator-declared and unreviewed."
    )


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


def test_city_pack_upload_never_reflects_secret_filename_path_or_raw_body(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, root = web_helpers._client(tmp_path)
    secret = "Authorization: Bearer sk-city-upload-secret-1234567890"
    client_filename = r"C:\private\customer-city-secret.json"
    payload = web_helpers._city_pack_payload("credential-bearing-city")
    payload["api_key"] = secret
    payload["filename"] = client_filename
    document = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    before = web_helpers._snapshot(root)

    with caplog.at_level(logging.DEBUG), client:
        response = client.post(
            web_helpers.UPLOAD_PATH,
            headers={
                "Content-Type": "application/json",
                "Origin": web_helpers.ORIGIN,
                "X-AdLife-CSRF": web_helpers.TOKEN,
                "Authorization": secret,
                "X-File-Name": client_filename,
            },
            content=document,
        )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid-city-pack",
        "message": "The city pack is not a valid CityPack V2 document.",
        "fields": {},
    }
    exposed = response.text + caplog.text
    for marker in (
        secret,
        client_filename,
        str(root),
        "credential-bearing-city",
        "city-pack-staging",
        ".city-pack-registry.lock",
    ):
        assert marker not in exposed
    assert web_helpers._snapshot(root) == before
    assert not (root / "city-packs").exists()
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
    before = web_helpers._artifact_snapshot(root)
    with client:
        response = client.get(path)

    assert response.status_code == expected_status
    assert "sk-query-secret" not in response.text
    assert ".." not in response.text
    assert "workbench.html" not in response.text
    assert web_helpers._artifact_snapshot(root) == before


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

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "prepare_scientific",
        fail_prepare,
    )
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
            _set_audience_count(page, 1)
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


def test_v8_saved_persona_markup_stays_inert_and_read_only_under_csp(tmp_path: Path) -> None:
    with _workbench_assumptions_viewer(tmp_path, schema_version=8) as (base_url, root, expected):
        before = web_helpers._snapshot(root)
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        requests: list[tuple[str, str]] = []
        errors: list[str] = []
        dialogs: list[str] = []
        try:
            page.on("request", lambda request: requests.append((request.method, request.url)))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            response = page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")
            assert response is not None
            assert "script-src 'self'" in response.headers["content-security-policy"]
            expect(page.locator("#selected-name")).to_have_text("مینا <img src=x onerror=alert(1)>")
            page.locator("#identity-context summary").click()
            expect(page.locator("#identity-background")).to_have_text(
                "زندگی خیالی <script>alert(1)</script>"
            )
            assert (
                page.locator(
                    "#identity-context img, #identity-context script, #people-list img"
                ).count()
                == 0
            )
            assert page.locator("#identity-context bdi[dir=auto]").count() == 4
            assert errors == dialogs == []
            assert all(method == "GET" and url.startswith(base_url) for method, url in requests)
            assert not any(
                "persona-templates" in url or "workbench.json" in url for _, url in requests
            )
            assert web_helpers._snapshot(root) == before
        finally:
            browser.close()
            playwright.stop()


@pytest.mark.parametrize("schema_version", [7, 8])
def test_read_only_browser_and_api_inspection_preserve_every_source_artifact_byte(
    tmp_path: Path,
    schema_version: int,
) -> None:
    browser_path = _installed_browser()
    root = tmp_path / "workspace"
    workspace = prepare_workbench_workspace(root)
    if schema_version == 8:
        payload = draft_v2_data(agent_count=1)
        payload["settings"].update(run_id="security-read-only", days=1)
        payload["scenario"]["phone"]["active_windows"] = [
            {"day": 1, "start_minute": 480, "end_minute": 481}
        ]
        payload["scenario"]["roadside"] = None
        validated = construct_workbench_run(
            WorkbenchRunDraftV2.model_validate_json(json.dumps(payload))
        )
        settings = validated.workbench_input.draft.settings
        stored = create_city_run(
            validated.pack,
            root=root,
            run_id=settings.run_id,
            seed=settings.seed,
            agent_count=settings.agent_count,
            days=settings.days,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
        )
        assert stored.manifest.schema_version == 8
    else:
        stored = web_helpers._saved_workbench_run(
            root, run_id="security-read-only", schema_version=7
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
            expect(page.locator("#assumptions-status")).to_have_text("Verified")
            if schema_version == 8:
                expect(page.locator("#selected-name")).to_be_visible()
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


_HOSTILE_NAME = "مینا <img src=x onerror=alert(1)>"
_HOSTILE_BACKGROUND = "زندگی خیالی </script><script>alert(1)</script>"


def _hostile_persona(document: dict[str, Any]) -> None:
    persona = document["population"]["personas"][0]
    persona["display_name"] = _HOSTILE_NAME
    persona["background"] = _HOSTILE_BACKGROUND


def _hostile_v10_workspace(root: Path, mode: str = "mock") -> Path:
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    workspace = root / "workspace"
    prepare_workbench_workspace(workspace)
    _saved_v10(workspace, mode=mode, run_id="hostile-v10", mutation=_hostile_persona)
    return workspace


@pytest.mark.parametrize("mode", ["mock", "local"])
def test_v10_saved_run_api_keeps_hostile_text_as_inert_json_data(
    tmp_path: Path,
    mode: str,
) -> None:
    from fastapi.testclient import TestClient

    root = _hostile_v10_workspace(tmp_path, mode)
    application = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="hostile-v10-token",
    )
    with TestClient(application, base_url="http://127.0.0.1:8765") as client:
        shell = client.get("/runs/hostile-v10")
        token = re.search(r'adlife-view-token" content="([A-Za-z0-9_-]{43})"', shell.text)
        assert token is not None
        headers = {"X-AdLife-View-Token": token.group(1)}
        documents = {
            suffix: client.get(f"/api/runs/hostile-v10/{suffix}", headers=headers)
            for suffix in (
                "meta",
                "workbench-input",
                "cognition-summary",
                "cognition-records",
                "response-events?minute=0",
                "response-state",
                "spatial-response-metrics",
            )
        }

    assert shell.status_code == 200
    assert "script-src 'self'" in shell.headers["content-security-policy"]
    # The inspector shell embeds no saved evidence; hostile text arrives only as JSON.
    for marker in ("مینا", "<img src=x", "alert(1)"):
        assert marker not in shell.text
    for suffix, response in documents.items():
        assert response.status_code == 200, suffix
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["x-content-type-options"] == "nosniff"
    frozen = documents["workbench-input"]
    assert frozen.headers["content-security-policy"].startswith("default-src 'none'")
    personas = frozen.json()["population"]["personas"]
    assert (personas[0]["display_name"], personas[0]["background"]) == (
        _HOSTILE_NAME,
        _HOSTILE_BACKGROUND,
    )
    # Descriptive persona text has no causal role: no cognition or response document
    # carries it, and no projection exposes provider transport or local paths.
    for suffix, response in documents.items():
        if suffix != "workbench-input":
            assert "مینا" not in response.text and "alert(1)" not in response.text, suffix
        assert str(root) not in response.text
        assert "11434" not in response.text and "credential_slot" not in response.text
        assert re.search(r"(?i)authorization|bearer|api[_-]?key", response.text) is None


def test_v10_read_only_browser_and_api_inspection_preserve_every_source_artifact_byte(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    root = _hostile_v10_workspace(tmp_path)
    application = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="read-only-v10-token",
    )
    before = web_helpers._snapshot(root)
    prefix = "/api/runs/hostile-v10"
    paths = [
        f"{prefix}/{suffix}"
        for suffix in (
            "meta",
            "city",
            "agents",
            "opportunity-summary",
            "opportunities?minute=0",
            "attention-summary",
            "attention-events?minute=0",
            "spatial-metrics",
            "response-summary",
            "response-events?minute=0",
            "response-state",
            "spatial-response-metrics",
            "frame?minute=0",
            "timeline?minute=0&agent_id=person-001",
            "workbench-input",
            "cognition-summary",
            "cognition-records",
        )
    ]

    with _serve(application) as base_url:
        playwright, browser, page = _open_page(browser_path, base_url)
        requests: list[tuple[str, str]] = []
        dialogs: list[str] = []
        try:
            page.on("request", lambda request: requests.append((request.method, request.url)))
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            page.goto(f"{base_url}/runs/hostile-v10", wait_until="networkidle")
            statuses = page.evaluate(
                """async (paths) => {
                  const token = document.querySelector('meta[name="adlife-view-token"]').content;
                  return Promise.all(paths.map(async (path) => {
                    const response = await fetch(path, {
                      cache: "no-store",
                      credentials: "same-origin",
                      redirect: "error",
                      headers: {"X-AdLife-View-Token": token},
                    });
                    await response.arrayBuffer();
                    return [path, response.status];
                  }));
                }""",
                paths,
            )
            stored_keys = page.evaluate(
                "() => [window.localStorage.length, window.sessionStorage.length]"
            )
        finally:
            browser.close()
            playwright.stop()

    assert statuses == [[path, 200] for path in paths]
    assert all(method == "GET" and url.startswith(base_url) for method, url in requests)
    assert not any("hostile" in url and "alert" in url for _, url in requests)
    assert stored_keys == [0, 0]
    assert dialogs == []
    assert web_helpers._snapshot(root) == before
