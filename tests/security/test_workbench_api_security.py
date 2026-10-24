"""Security boundary of the workbench's local JSON API and its read-only saved runs.

The browser workbench page is gone; the guided pages are the only browser interface.
These refusals and read-only guarantees are properties of the ``/api/*`` routes the guide
and the scripts use, so they are tested here directly, plus one read-only browser pass
through the guided pages.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect, sync_playwright

import adlife.city.workbench_web as workbench_web
from adlife.city.runs import create_city_run
from adlife.city.workbench_input import WorkbenchRunDraftV2
from adlife.city.workbench_security import MAX_WORKBENCH_BODY_BYTES
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.browser_support import _installed_browser, _serve
from tests.unit.city import test_workbench_web as web_helpers
from tests.unit.city.test_workbench_input import draft_v2_data


def _saved_bytes(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Every saved entry; the guide's start page may create a registry lock file."""
    return tuple(entry for entry in web_helpers._snapshot(root) if not entry[0].endswith(".lock"))


def _open_page(browser_path: Path, base_url: str, *, width: int = 1440):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        executable_path=str(browser_path),
        headless=True,
        args=["--no-first-run", "--disable-background-networking"],
    )
    page = browser.new_page(viewport={"width": width, "height": 1000})
    return playwright, browser, page


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
    before = _saved_bytes(root)
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
            # The guide's start page lists the saved run; it is the only browser surface.
            page.goto(f"{base_url}/guide", wait_until="networkidle")
            expect(
                page.locator(f'.setup-run[data-run-id="{stored.manifest.run_id}"]')
            ).to_be_visible()
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
    assert _saved_bytes(root) == before


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
        documents = {
            suffix: client.get(f"/api/runs/hostile-v10/{suffix}")
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

    # Hostile saved text arrives only as JSON data.
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
            "story",
        )
    ]

    with _serve(application) as base_url:
        playwright, browser, page = _open_page(browser_path, base_url)
        requests: list[tuple[str, str]] = []
        dialogs: list[str] = []
        try:
            page.on("request", lambda request: requests.append((request.method, request.url)))
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            # The guided story of the run is the only browser view of it.
            page.goto(f"{base_url}/guide/runs/hostile-v10", wait_until="networkidle")
            expect(page.locator("#story-app")).to_be_visible()
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
