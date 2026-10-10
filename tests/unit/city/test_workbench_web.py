from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from adlife.city.run_store import CityRunStore
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.unit.city.test_workbench_input import draft_data

TOKEN = "fixed-workbench-web-token"
ORIGIN = "http://127.0.0.1:8765"


def _module():
    spec = importlib.util.find_spec("adlife.city.workbench_web")
    assert spec is not None, "city workbench web application is not implemented"
    return importlib.import_module("adlife.city.workbench_web")


def _client(
    tmp_path: Path,
    *,
    token: str = TOKEN,
    raise_server_exceptions: bool = True,
) -> tuple[TestClient, Path]:
    root = tmp_path / "workspace"
    workspace = prepare_workbench_workspace(root)
    app = _module().create_city_workbench_app(workspace, csrf_token=token)
    return (
        TestClient(
            app,
            base_url=ORIGIN,
            raise_server_exceptions=raise_server_exceptions,
        ),
        root,
    )


def _post(client: TestClient, payload: dict[str, Any]):
    return client.post(
        "/api/scenarios/validate",
        headers={
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "X-AdLife-CSRF": TOKEN,
        },
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _valid_draft(*, phone: bool = True, roadside: bool = False) -> dict[str, Any]:
    payload = draft_data(phone=phone, roadside=roadside)
    if roadside:
        payload["scenario"]["roadside"]["road_id"] = "middle-west"
    return payload


def _snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((relative, "file", digest))
        else:
            entries.append((relative, "directory", ""))
    return tuple(entries)


def test_workbench_capabilities_are_versioned_bounded_and_honest(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.get("/api/workbench")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "phase": "validation-foundation",
        "capabilities": {
            "scenario_validation": True,
            "jobs": False,
            "run_execution": False,
            "run_inspection": False,
            "provider_configuration": False,
            "oauth": False,
            "response_modes": ["deterministic-rules"],
        },
        "active_job": None,
        "limits": {
            "request_body_bytes": 131072,
            "agent_count": {"minimum": 1, "maximum": 30},
            "days": {"minimum": 1, "maximum": 7},
            "placements": ["mobile-feed", "roadside-billboard"],
        },
        "disclosure": {
            "population": "Synthetic cohort assumptions; not real residents.",
            "city": "Bundled fictional city geometry; not a live city or traffic feed.",
            "outcomes": "Modeled responses are not observed behavior, sales, or forecasts.",
        },
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_catalog_and_creative_endpoints_are_complete_and_stably_ordered(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        cities_response = client.get("/api/catalog/cities")
        creatives_response = client.get("/api/creative-templates")

    assert cities_response.status_code == 200
    cities = cities_response.json()
    assert cities["schema_version"] == 1
    assert [item["city_id"] for item in cities["cities"]] == sorted(
        item["city_id"] for item in cities["cities"]
    )
    assert len(cities["cities"]) == 1
    city = cities["cities"][0]
    assert city["city_id"] == "fictional-grid-v2"
    assert city["data_origin"] == "fictional"
    assert city["qualification"] == "fictional-fixture"
    assert city["pack_schema_version"] == 2
    assert len(city["pack_sha256"]) == 64
    assert city["known_omissions"]
    assert "resource_name" not in city

    assert creatives_response.status_code == 200
    creatives = creatives_response.json()
    assert creatives["schema_version"] == 1
    assert [item["template_id"] for item in creatives["templates"]] == [
        "fictional-crispy-meal-v1",
        "fictional-device-launch-v1",
    ]
    assert all(len(item["creative_sha256"]) == 64 for item in creatives["templates"])
    assert all(
        item["disclosure"] == "Fictional creative for synthetic simulation only."
        for item in creatives["templates"]
    )


@pytest.mark.parametrize(
    ("phone", "roadside", "channels"),
    [
        (True, False, ["mobile-feed"]),
        (False, True, ["roadside-billboard"]),
        (True, True, ["mobile-feed", "roadside-billboard"]),
    ],
)
def test_validation_endpoint_returns_only_bound_summaries_and_stable_hashes(
    tmp_path: Path,
    phone: bool,
    roadside: bool,
    channels: list[str],
) -> None:
    client, root = _client(tmp_path)
    payload = _valid_draft(phone=phone, roadside=roadside)
    before = _snapshot(root)
    with client:
        first = _post(client, payload)
        second = _post(client, payload)

    assert first.status_code == 200
    assert first.json() == second.json()
    document = first.json()
    assert document["schema_version"] == 1
    assert document["valid"] is True
    assert document["run"] == {
        "run_id": "launch-run",
        "agent_count": 20,
        "days": 2,
        "seed": 42,
        "response_mode": "deterministic-rules",
    }
    assert document["city"]["city_id"] == "fictional-grid-v2"
    assert document["scenario"]["scenario_id"] == "launch-study"
    assert document["scenario"]["campaign_id"] == "fictional-launch"
    assert document["scenario"]["placement_count"] == len(channels)
    assert document["scenario"]["channels"] == channels
    assert [item["channel"] for item in document["scenario"]["placements"]] == channels
    assert document["response"]["profile_count"] == 20
    assert document["response"]["campaign_count"] == 1
    assert document["response"]["initial_state_count"] == 20
    assert document["creative"]["template_id"] == "fictional-device-launch-v1"
    for value in document["hashes"].values():
        assert len(value) == 64
    assert set(document) == {
        "schema_version",
        "valid",
        "run",
        "city",
        "scenario",
        "creative",
        "response",
        "hashes",
        "disclosure",
    }
    assert _snapshot(root) == before == ()


@pytest.mark.parametrize(
    ("mutation", "status", "code", "field"),
    [
        ("unknown-city", 404, "unknown-city", "city_id"),
        (
            "unknown-template",
            404,
            "unknown-creative-template",
            "scenario.campaign.creative_template_id",
        ),
        ("unknown-road", 400, "invalid-road-placement", "scenario.roadside.road_id"),
        ("invalid-day", 400, "invalid-schedule", "scenario.phone.active_windows"),
    ],
)
def test_validation_maps_known_domain_refusals_without_echoing_values(
    tmp_path: Path,
    mutation: str,
    status: int,
    code: str,
    field: str,
) -> None:
    client, root = _client(tmp_path)
    payload = _valid_draft(phone=True, roadside=True)
    if mutation == "unknown-city":
        payload["city_id"] = "missing-city"
    elif mutation == "unknown-template":
        payload["scenario"]["campaign"]["creative_template_id"] = "missing-template"
    elif mutation == "unknown-road":
        payload["scenario"]["roadside"]["road_id"] = "missing-road"
    else:
        payload["settings"]["days"] = 1
    before = _snapshot(root)

    with client:
        response = _post(client, payload)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()["error"]["fields"]) == {field}
    assert "missing" not in response.text
    assert _snapshot(root) == before == ()


def test_validation_maps_strict_json_and_pydantic_failures_without_input_echo(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    secret = "Authorization: Bearer sk-never-return-this-1234567890"
    absolute_path = r"C:\private\workspace\run.json"
    payload = _valid_draft()
    payload["settings"]["agent_count"] = True
    payload["scenario"]["name"] = absolute_path
    payload["api_key"] = secret
    headers = {
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "X-AdLife-CSRF": TOKEN,
    }
    before = _snapshot(root)
    with client:
        malformed = client.post(
            "/api/scenarios/validate",
            headers=headers,
            content=b'{"api_key":"sk-do-not-echo",',
        )
        oversized = client.post(
            "/api/scenarios/validate",
            headers=headers,
            content=b"x" * 131_073,
        )
        invalid = _post(client, payload)
        unknown = client.get("/api/not-a-route")

    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "malformed-json"
    assert "sk-do-not-echo" not in malformed.text
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request-too-large"
    assert invalid.status_code == 422
    fields = invalid.json()["error"]["fields"]
    assert set(fields) == {"api_key", "settings.agent_count"}
    assert secret not in invalid.text
    assert absolute_path not in invalid.text
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not-found"
    assert _snapshot(root) == before == ()


def test_request_validation_and_unexpected_failures_are_screened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    client, root = _client(tmp_path, raise_server_exceptions=False)
    secret = "sk-unexpected-secret-value-1234567890"
    absolute_path = r"C:\private\workspace\run.json"
    before = _snapshot(root)

    def request_failure(_draft: object) -> None:
        raise RequestValidationError(
            [
                {
                    "type": "missing",
                    "loc": ("body", "settings", "seed"),
                    "msg": "Field required",
                    "input": secret,
                }
            ]
        )

    monkeypatch.setattr(module, "construct_workbench_run", request_failure)
    with client:
        request_response = _post(client, _valid_draft())
    assert request_response.status_code == 422
    assert request_response.json()["error"]["fields"] == {
        "settings.seed": "This field is required."
    }
    assert secret not in request_response.text

    def unexpected_failure(_draft: object) -> None:
        raise RuntimeError(f"{secret} at {absolute_path}")

    monkeypatch.setattr(module, "construct_workbench_run", unexpected_failure)
    with caplog.at_level(logging.ERROR), client:
        unexpected_response = _post(client, _valid_draft())
    assert unexpected_response.status_code == 500
    assert unexpected_response.json()["error"] == {
        "code": "internal-error",
        "message": "The workbench could not complete the request.",
        "fields": {},
    }
    combined = unexpected_response.text + caplog.text
    assert secret not in combined
    assert absolute_path not in combined
    assert "Traceback" not in combined
    assert _snapshot(root) == before == ()


def test_validation_reaches_no_network_store_worker_or_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root = _client(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forbidden validation side effect")

    before = _snapshot(root)
    with client:
        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(CityRunStore, "__init__", refuse)
        response = _post(client, _valid_draft(phone=True, roadside=True))

    assert response.status_code == 200
    assert _snapshot(root) == before == ()
    assert client.app.router.on_startup == []
    assert client.app.router.on_shutdown == []
    assert not (root / "city-runs").exists()


def test_shell_assets_are_local_escaped_honest_and_csp_restricted(tmp_path: Path) -> None:
    hostile_token = 'token"><script>alert("x")</script>'
    client, _ = _client(tmp_path, token=hostile_token)
    with client:
        shell = client.get("/")
        css = client.get("/assets/workbench.css")
        icon = client.get("/assets/workbench-icon.svg")
        hostile_host = client.get("/", headers={"Host": "example.test"})

    assert shell.status_code == 200
    assert shell.headers["content-type"].startswith("text/html")
    assert shell.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    assert hostile_token not in shell.text
    assert "&quot;&gt;&lt;script&gt;" in shell.text
    assert "<script" not in shell.text.lower()
    assert '<link rel="stylesheet" href="/assets/workbench.css">' in shell.text
    assert '<link rel="icon" href="/assets/workbench-icon.svg" type="image/svg+xml">' in shell.text
    assert "SYNTHETIC" in shell.text
    assert "not real residents" in shell.text
    assert "Validation foundation" in shell.text
    assert "Run execution is not enabled" in shell.text
    assert "Provider configuration is not enabled" in shell.text
    for control in ("<form", "<input", "<select", "<button"):
        assert control not in shell.text.lower()
    assert "http://" not in shell.text
    assert "https://" not in shell.text

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "@import" not in css.text.lower()
    assert "@font-face" not in css.text.lower()
    assert "http://" not in css.text
    assert "https://" not in css.text
    assert "url(" not in css.text.lower()

    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert icon.text.startswith("<svg")
    assert "<script" not in icon.text.lower()
    assert "<foreignobject" not in icon.text.lower()
    assert "href=" not in icon.text.lower()
    assert hostile_host.status_code == 400
    for response in (shell, css, icon, hostile_host):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in response.headers
        assert "server" not in response.headers
