from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.catalog import CityCatalogError, load_city_catalog, select_catalog_city
from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run
from adlife.city.web import create_city_app
from adlife.city.workbench_catalog import (
    PackagedCityCatalogUnavailable,
    WorkbenchCityRegistryUnavailable,
    WorkbenchCityResolver,
)
from adlife.city.workbench_city_registry import (
    MAX_CITY_PACK_UPLOAD_BYTES,
    CityPackPublicationError,
    CityPackRegistrationConflict,
    CityPackUploadTooLarge,
    InvalidCityPackUpload,
    MalformedCityPackUpload,
)
from adlife.city.workbench_input import WorkbenchRunDraft
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import UnsafeRunLocation
from tests.unit.city.test_workbench_input import (
    draft_data,
    draft_v2_data,
    draft_v3_data,
    persona_data,
)

TOKEN = "fixed-workbench-web-token"
ORIGIN = "http://127.0.0.1:8765"
UPLOAD_PATH = "/api/catalog/cities/upload"


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


def _secure_post(client: TestClient, path: str, payload: dict[str, Any]):
    return client.post(
        path,
        headers={
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "X-AdLife-CSRF": TOKEN,
        },
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _city_pack_payload(city_id: str = "operator-city") -> dict[str, Any]:
    payload = select_catalog_city("fictional-grid-v2").model_dump(mode="json")
    payload["city_id"] = city_id
    payload["name"] = f"Operator City {city_id}"
    return payload


def _city_pack_document(city_id: str = "operator-city", *, indent: int | None = None) -> bytes:
    return json.dumps(
        _city_pack_payload(city_id),
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
    ).encode("utf-8")


def _secure_upload(
    client: TestClient,
    document: bytes,
    *,
    path: str = UPLOAD_PATH,
    headers: dict[str, str] | None = None,
):
    request_headers = {
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "X-AdLife-CSRF": TOKEN,
    }
    request_headers.update(headers or {})
    return client.post(path, headers=request_headers, content=document)


def _valid_draft(*, phone: bool = True, roadside: bool = False) -> dict[str, Any]:
    payload = draft_data(phone=phone, roadside=roadside)
    payload["settings"]["seed"] = str(payload["settings"]["seed"])
    if roadside:
        payload["scenario"]["roadside"]["road_id"] = "middle-west"
    return payload


def _tiny_draft(*, run_id: str = "browser-run") -> dict[str, Any]:
    payload = _valid_draft()
    payload["settings"].update({"run_id": run_id, "agent_count": 1, "days": 1})
    payload["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 480, "end_minute": 540}
    ]
    return payload


def _persona_draft(*, run_id: str = "persona-browser-run") -> dict[str, Any]:
    payload = draft_v2_data(agent_count=2)
    payload["settings"].update({"seed": "9223372036854775807", "days": 1, "run_id": run_id})
    payload["population"]["personas"] = [
        persona_data(persona_id="persona-002"),
        persona_data(persona_id="persona-001"),
    ]
    payload["population"]["personas"][0]["traits"]["impulsivity"] = 0.8
    payload["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 480, "end_minute": 540}
    ]
    return payload


def test_persona_templates_endpoint_is_exact_stable_validated_and_query_free(
    tmp_path: Path,
) -> None:
    from adlife.city.workbench_personas import load_persona_template_catalog

    client, root = _client(tmp_path)
    before = _snapshot(root)
    with client:
        response = client.get("/api/persona-templates")
        repeated = client.get("/api/persona-templates")
        refused = client.get("/api/persona-templates?secret=never-reflect-query")
    assert response.status_code == 200
    assert response.json() == load_persona_template_catalog().model_dump(mode="json")
    assert [item["persona_id"] for item in response.json()["templates"]] == [
        "persona-001",
        "persona-002",
        "persona-003",
        "persona-004",
    ]
    assert response.content == repeated.content
    assert refused.status_code == 422
    assert "never-reflect-query" not in refused.text
    assert _snapshot(root) == before


def test_persona_catalog_failure_is_a_constant_value_free_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from adlife.city.workbench_personas import PersonaTemplateCatalogError

    secret = "sk-private-catalog-secret-1234567890123456"

    def broken():
        raise PersonaTemplateCatalogError(f"C:/private/persona_templates.json {secret}")

    monkeypatch.setattr(_module(), "load_persona_template_catalog", broken, raising=False)
    client, _ = _client(tmp_path, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR), client:
        response = client.get("/api/persona-templates")
    assert response.status_code == 500
    assert response.json() == {
        "schema_version": 1,
        "error": {
            "code": "persona-catalog-unavailable",
            "message": "The fictional persona catalog is unavailable.",
            "fields": {},
        },
    }
    assert secret not in response.text + caplog.text
    assert "C:/private" not in response.text + caplog.text


def test_schema2_validation_receipts_and_jobs_project_one_verified_v8_snapshot(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    payload = _persona_draft()
    with client:
        first = _post(client, payload)
        payload["population"]["personas"].reverse()
        repeated = _post(client, payload)
        assert first.status_code == repeated.status_code == 200
        assert first.content == repeated.content
        submitted = _secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202
        accepted = submitted.json()["job"]
        assert accepted["accepted_settings"]["run_schema_version"] == 8
        assert accepted["accepted_settings"]["workbench_input_schema_version"] == 2
        terminal = _await_job(client, submitted.json()["status_url"])
        assert terminal["phase"] == "completed"
        assert terminal["result"]["run_schema_version"] == 8
        listing = client.get("/api/runs")
        detail = client.get("/api/runs/persona-browser-run/meta")
        frozen = client.get("/api/runs/persona-browser-run/workbench-input")
        shell = client.get("/runs/persona-browser-run")
        assert (
            listing.status_code
            == detail.status_code
            == frozen.status_code
            == shell.status_code
            == 200
        )
        assert listing.json()["runs"][0] == terminal["result"]
        assert detail.json()["workbench_input_available"] is True
        assert detail.json()["workbench_input_sha256"] == first.json()["hashes"]["input_sha256"]
        document = frozen.json()
        assert document["workbench_input_sha256"] == first.json()["hashes"]["input_sha256"]
        assert document["settings"]["seed"] == "9223372036854775807"
        assert document["population"]["assignments"] == [
            {"agent_id": "person-001", "persona_id": "persona-001"},
            {"agent_id": "person-002", "persona_id": "persona-002"},
        ]
        view_token = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">', shell.text
        )
        assert view_token is not None
        sidecar = root / "city-runs" / "persona-browser-run" / "inputs" / "workbench.json"
        sidecar.write_bytes(sidecar.read_bytes() + b" ")
        pinned = client.get(
            "/api/runs/persona-browser-run/workbench-input",
            headers={"X-AdLife-View-Token": view_token.group(1)},
        )
        assert pinned.status_code == 200
        assert pinned.content == frozen.content
        assert client.get("/runs/persona-browser-run").status_code == 409
        assert client.get("/api/runs/persona-browser-run/workbench-input").status_code == 409


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("fictional", False),
        ("fictional", "true"),
        ("fictional", 1),
        ("persona_id", "persona-000"),
        ("count", True),
        ("count", 0),
        ("display_name", ""),
        ("age", True),
        ("occupation", 123),
        ("mbti", "WRONG"),
        ("background", "😀" * 700),
    ],
)
def test_persona_diagnostics_are_adjacent_value_free_and_pre_reservation(
    tmp_path: Path,
    path: str,
    field: str,
    value: object,
) -> None:
    client, root = _client(tmp_path)
    payload = _persona_draft()
    payload["population"]["personas"][0][field] = value
    before = _snapshot(root)
    with client:
        response = _secure_post(client, path, payload)
        assert response.status_code == 422
        expected_path = (
            "population.personas.0" if field == "background" else f"population.personas.0.{field}"
        )
        assert response.json()["error"]["fields"] == {expected_path: "This field is invalid."}
        assert client.get("/api/workbench").json()["active_job"] is None
    assert _snapshot(root) == before


def _await_job(client: TestClient, status_url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    while time.monotonic() < deadline:
        response = client.get(status_url)
        assert response.status_code == 200
        job = response.json()
        if job["phase"] in {"completed", "failed", "cancelled"}:
            return job
        pause.wait(0.005)
    raise AssertionError(f"job at {status_url} did not terminate")


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


def _artifact_snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Exclude the registry's advisory coordination file from artifact assertions."""
    return tuple(entry for entry in _snapshot(root) if entry[0] != ".city-pack-registry.lock")


def _saved_workbench_run(
    root: Path,
    *,
    run_id: str,
    schema_version: int,
    seed: int = 42,
):
    payload = _tiny_draft(run_id=run_id)
    payload["settings"]["seed"] = seed
    draft = WorkbenchRunDraft.model_validate_json(json.dumps(payload, separators=(",", ":")))
    validated = construct_workbench_run(draft)
    settings = validated.workbench_input.draft.settings
    return create_city_run(
        validated.pack,
        root=root,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input if schema_version == 7 else None,
    )


def _legacy_saved_run_app(stored):
    return create_city_app(
        stored.mobility,
        run_id=stored.manifest.run_id,
        run_schema_version=stored.manifest.schema_version,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        spatial_metrics=metrics_for_stored_city_run(stored),
        response_input=stored.response_input,
        response_evaluation=stored.response_evaluation,
        spatial_response_metrics=response_metrics_for_stored_city_run(stored),
    )


def test_workbench_capabilities_are_versioned_bounded_and_honest(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.get("/api/workbench")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "phase": "bounded-jobs",
        "capabilities": {
            "scenario_validation": True,
            "jobs": True,
            "run_execution": True,
            "run_inspection": True,
            "provider_configuration": False,
            "oauth": False,
            "response_modes": ["deterministic-rules"],
        },
        "active_job": None,
        "limits": {
            "request_body_bytes": 131072,
            "agent_count": {"minimum": 1, "maximum": 30},
            "days": {"minimum": 1, "maximum": 7},
            "seed": {"minimum": "0", "maximum": "9223372036854775807"},
            "placements": ["mobile-feed", "roadside-billboard"],
        },
        "disclosure": {
            "population": "Synthetic cohort assumptions; not real residents.",
            "city": (
                "City-pack geometry; no live map or traffic feed. "
                "Uploaded sources are operator-declared and unreviewed."
            ),
            "outcomes": "Modeled responses are not observed behavior, sales, or forecasts.",
        },
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("schema_version", [6, 7])
def test_run_scoped_reads_match_the_legacy_verified_view_contract(
    tmp_path: Path,
    schema_version: int,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = f"parity-v{schema_version}"
    stored = _saved_workbench_run(root, run_id=run_id, schema_version=schema_version)
    before = _snapshot(root)
    workbench = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    legacy = _legacy_saved_run_app(stored)
    suffixes = (
        "meta",
        "city",
        "agents",
        "places",
        "place-assignments",
        "opportunity-summary",
        "opportunities?minute=480&limit=1",
        "attention-summary",
        "spatial-metrics",
        "spatial-response-metrics",
        "attention-events?minute=480&limit=1",
        "response-summary",
        "response-events?minute=480&limit=1",
        "response-state?agent_id=person-001",
        "frame?minute=480&agent_id=person-001",
        (
            "timeline?minute=480&agent_id=person-001&opportunity_offset=0"
            "&attention_offset=0&response_offset=0&response_state_offset=0&limit=1"
        ),
    )
    refusal_suffixes = (
        "opportunities?minute=1440",
        "attention-events?minute=480&agent_id=person-999",
        "response-events?minute=-1",
        "response-state?limit=101",
        "frame?minute=1440&agent_id=person-001",
    )

    with (
        TestClient(workbench, base_url=ORIGIN) as scoped_client,
        TestClient(legacy, base_url=ORIGIN) as legacy_client,
    ):
        for suffix in (*suffixes, *refusal_suffixes):
            legacy_response = legacy_client.get(f"/api/{suffix}")
            scoped_response = scoped_client.get(f"/api/runs/{run_id}/{suffix}")
            assert scoped_response.status_code == legacy_response.status_code, suffix
            expected = legacy_response.json()
            if schema_version == 7 and suffix == "meta":
                assert stored.workbench_input is not None
                expected = {
                    **expected,
                    "workbench_input_available": True,
                    "workbench_input_sha256": stored.workbench_input.fingerprint,
                }
            assert scoped_response.json() == expected, suffix
            assert (
                scoped_response.headers["content-type"] == legacy_response.headers["content-type"]
            )
            assert (
                scoped_response.headers["cache-control"]
                == legacy_response.headers["cache-control"]
                == "no-store"
            )
            assert (
                scoped_response.headers["x-content-type-options"]
                == legacy_response.headers["x-content-type-options"]
                == "nosniff"
            )

    assert _snapshot(root) == before


def test_scoped_timeline_projection_verifies_the_run_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="single-load-timeline", schema_version=7)
    module = _module()
    original = module.WorkbenchRunRepository.load
    loaded: list[str] = []

    def counted_load(repository: object, run_id: str):
        loaded.append(run_id)
        return original(repository, run_id)

    monkeypatch.setattr(module.WorkbenchRunRepository, "load", counted_load)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(
            "/api/runs/single-load-timeline/timeline",
            params={"minute": 480, "agent_id": "person-001", "limit": 1},
        )

    assert response.status_code == 200
    assert loaded == ["single-load-timeline"]
    assert set(response.json()) == {
        "schema_version",
        "minute",
        "agent_id",
        "frame",
        "opportunity_page",
        "attention_page",
        "response_page",
        "response_state_page",
    }


def test_inspector_snapshot_reuses_one_verified_run_across_browser_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = "snapshot-view"
    _saved_workbench_run(root, run_id=run_id, schema_version=7)
    module = _module()
    original = module.WorkbenchRunRepository.load
    loaded: list[str] = []

    def counted_load(repository: object, candidate: str):
        loaded.append(candidate)
        return original(repository, candidate)

    monkeypatch.setattr(module.WorkbenchRunRepository, "load", counted_load)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get(f"/runs/{run_id}")
        token_match = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
            page.text,
        )
        assert token_match is not None
        headers = {"X-AdLife-View-Token": token_match.group(1)}
        responses = (
            client.get(f"/api/runs/{run_id}/meta", headers=headers),
            client.get(f"/api/runs/{run_id}/city", headers=headers),
            client.get(f"/api/runs/{run_id}/agents", headers=headers),
            client.get(f"/api/runs/{run_id}/workbench-input", headers=headers),
            client.get(
                f"/api/runs/{run_id}/timeline",
                headers=headers,
                params={"minute": 480, "agent_id": "person-001", "limit": 1},
            ),
        )

    assert page.status_code == 200
    assert all(response.status_code == 200 for response in responses)
    assert loaded == [run_id]


def test_inspector_snapshot_token_is_bound_to_one_run_and_invalid_tokens_are_refused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="snapshot-a", schema_version=7)
    _saved_workbench_run(root, run_id="snapshot-b", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get("/runs/snapshot-a")
        token_match = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
            page.text,
        )
        assert token_match is not None
        token = token_match.group(1)
        wrong_run = client.get(
            "/api/runs/snapshot-b/meta",
            headers={"X-AdLife-View-Token": token},
        )
        malformed = client.get(
            "/api/runs/snapshot-a/meta",
            headers={"X-AdLife-View-Token": "not-a-valid-token"},
        )
        unknown = client.get(
            "/api/runs/snapshot-a/meta",
            headers={"X-AdLife-View-Token": "A" * 43},
        )

    expected = {
        "code": "run-unavailable",
        "message": "The saved run could not be verified.",
        "fields": {},
    }
    assert wrong_run.status_code == malformed.status_code == unknown.status_code == 409
    assert wrong_run.json()["error"] == malformed.json()["error"] == unknown.json()["error"]
    assert unknown.json()["error"] == expected
    assert token not in wrong_run.text + malformed.text + unknown.text


def test_repeated_inspector_pages_share_one_verified_snapshot_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = "shared-snapshot"
    _saved_workbench_run(root, run_id=run_id, schema_version=7)
    module = _module()
    original_load = module.WorkbenchRunRepository.load
    loaded: list[str] = []
    resolved_snapshot_ids: list[int] = []

    def counted_load(repository: object, candidate: str):
        loaded.append(candidate)
        return original_load(repository, candidate)

    original_resolve = module._InspectorSnapshots.resolve

    def observed_resolve(cache: object, token: str, *, run_id: str):
        snapshot = original_resolve(cache, token, run_id=run_id)
        if snapshot is not None:
            resolved_snapshot_ids.append(id(snapshot))
        return snapshot

    monkeypatch.setattr(module.WorkbenchRunRepository, "load", counted_load)
    monkeypatch.setattr(module._InspectorSnapshots, "resolve", observed_resolve)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        pages = (client.get(f"/runs/{run_id}"), client.get(f"/runs/{run_id}"))
        tokens = []
        for page in pages:
            token_match = re.search(
                r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
                page.text,
            )
            assert token_match is not None
            tokens.append(token_match.group(1))
        responses = tuple(
            client.get(
                f"/api/runs/{run_id}/meta",
                headers={"X-AdLife-View-Token": token},
            )
            for token in tokens
        )

    assert all(page.status_code == 200 for page in pages)
    assert all(response.status_code == 200 for response in responses)
    assert loaded == [run_id, run_id]
    assert len(tokens) == len(set(tokens)) == 2
    assert len(set(resolved_snapshot_ids)) == 1


def test_open_inspector_snapshot_stays_atomic_and_reload_reverifies_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = "atomic-snapshot"
    _saved_workbench_run(root, run_id=run_id, schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get(f"/runs/{run_id}")
        token_match = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
            page.text,
        )
        assert token_match is not None
        headers = {"X-AdLife-View-Token": token_match.group(1)}
        before = client.get(f"/api/runs/{run_id}/meta", headers=headers)
        manifest = root / "city-runs" / run_id / "run.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        same_page = client.get(f"/api/runs/{run_id}/meta", headers=headers)
        direct = client.get(f"/api/runs/{run_id}/meta")
        reload = client.get(f"/runs/{run_id}")

    assert page.status_code == before.status_code == same_page.status_code == 200
    assert same_page.json() == before.json()
    assert direct.status_code == reload.status_code == 409
    assert (
        direct.json()["error"]
        == reload.json()["error"]
        == {
            "code": "run-unavailable",
            "message": "The saved run could not be verified.",
            "fields": {},
        }
    )


def test_completed_inspector_url_serves_the_existing_dashboard_with_a_scoped_api_base(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(root, run_id="inspectable-run", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get("/runs/inspectable-run")
        script = client.get("/static/app.js")
        style = client.get("/static/app.css")
        listing = client.get("/api/runs")

    assert page.status_code == script.status_code == style.status_code == 200
    assert page.text.count('name="adlife-api-base"') == 1
    assert 'name="adlife-api-base" content="/api/runs/inspectable-run"' in page.text
    assert page.text.count('name="adlife-view-token"') == 1
    assert re.search(
        r'<meta name="adlife-view-token" content="[A-Za-z0-9_-]{43}">',
        page.text,
    )
    assert 'src="/static/app.js"' in page.text
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert page.headers["cache-control"] == "no-store"
    assert script.headers["content-type"].startswith("text/javascript")
    assert style.headers["content-type"].startswith("text/css")
    assert listing.json()["runs"][0]["inspector_url"] == "/runs/inspectable-run"
    assert stored.manifest.run_id in page.text or "saved-run-label" in page.text


def test_v7_workbench_input_endpoint_returns_only_the_verified_frozen_projection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(
        root,
        run_id="maximum-input-view",
        schema_version=7,
        seed=2**63 - 1,
    )
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/runs/maximum-input-view/workbench-input")
        metadata = client.get("/api/runs/maximum-input-view/meta")
        refused_query = client.get(
            "/api/runs/maximum-input-view/workbench-input",
            params={"unexpected": "sk-never-echo-this-value-1234567890"},
        )
        refused_path = client.get("/api/runs/maximum-input-view/workbench-input/private")

    assert response.status_code == metadata.status_code == 200
    document = response.json()
    assert set(document) == {
        "run_id",
        "run_schema_version",
        "workbench_input_schema_version",
        "manifest_sha256",
        "workbench_input_sha256",
        "city_sha256",
        "scenario_sha256",
        "creative_sha256",
        "scenario",
        "cohort",
        "settings",
        "creative_template",
    }
    assert document["settings"]["seed"] == "9223372036854775807"
    assert (
        document["manifest_sha256"]
        == hashlib.sha256((stored.directory / "run.json").read_bytes()).hexdigest()
    )
    assert stored.workbench_input is not None
    assert document["workbench_input_sha256"] == stored.workbench_input.fingerprint
    assert metadata.json()["workbench_input_available"] is True
    assert metadata.json()["workbench_input_sha256"] == stored.workbench_input.fingerprint
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["permissions-policy"] == ("camera=(), microphone=(), geolocation=()")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "directory" not in response.text
    assert str(stored.directory) not in response.text
    assert refused_query.status_code == 422
    assert refused_query.json()["error"]["code"] == "invalid-query"
    assert "sk-never" not in refused_query.text
    assert refused_path.status_code == 404


def _save_legacy_run(root: Path, schema_version: int) -> str:
    from tests.integration.test_city_run_store import spatial_specimen
    from tests.unit.city.test_city_mobility import mobility_place_set
    from tests.unit.city.test_city_pack import (
        load_pack,
        load_pack_v2,
        pack_data,
        pack_v2_data,
    )

    run_id = f"legacy-input-v{schema_version}"
    if schema_version == 1:
        create_city_run(
            load_pack(pack_data()),
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
        )
    elif schema_version == 2:
        create_city_run(
            load_pack_v2(pack_v2_data()),
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
        )
    elif schema_version == 3:
        create_city_run(
            load_pack_v2(pack_v2_data()),
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
            places=mobility_place_set(),
        )
    elif schema_version == 4:
        manifest, mobility, scenario, opportunities = spatial_specimen()
        manifest = manifest.model_copy(update={"run_id": run_id})
        CityRunStore(root).save(
            manifest,
            mobility.pack,
            mobility.agents,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
        )
    elif schema_version == 5:
        payload = _tiny_draft(run_id=run_id)
        payload["settings"]["seed"] = 42
        validated = construct_workbench_run(
            WorkbenchRunDraft.model_validate_json(json.dumps(payload, separators=(",", ":")))
        )
        create_city_run(
            validated.pack,
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
            spatial_scenario=validated.scenario,
        )
    else:
        _saved_workbench_run(root, run_id=run_id, schema_version=6)
    return run_id


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4, 5, 6])
def test_verified_legacy_runs_report_workbench_input_as_unavailable_without_claiming_it(
    tmp_path: Path,
    schema_version: int,
) -> None:
    root = tmp_path / f"workspace-v{schema_version}"
    prepare_workbench_workspace(root)
    run_id = _save_legacy_run(root, schema_version)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(f"/api/runs/{run_id}/workbench-input")
        metadata = client.get(f"/api/runs/{run_id}/meta")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "workbench-input-unavailable",
        "message": "This saved run has no browser workbench input.",
        "fields": {},
    }
    assert metadata.status_code == 200
    assert metadata.json()["run_schema_version"] == schema_version
    assert "workbench_input_available" not in metadata.json()
    assert "workbench_input_sha256" not in metadata.json()


def test_workbench_input_endpoint_reverifies_and_refuses_sidecar_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(root, run_id="input-tamper", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/input-tamper/workbench-input")
        sidecar = stored.directory / "inputs" / "workbench.json"
        sidecar.write_bytes(sidecar.read_bytes() + b"\n")
        second = client.get("/api/runs/input-tamper/workbench-input")

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"] == {
        "code": "run-unavailable",
        "message": "The saved run could not be verified.",
        "fields": {},
    }
    assert str(sidecar) not in second.text


@pytest.mark.parametrize(
    "run_id",
    ["missing-run", "CON", "sk-live-abcdefghij", "x" * 41],
)
def test_direct_inspection_screens_invalid_or_unknown_run_identifiers(
    tmp_path: Path,
    run_id: str,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        api = client.get(f"/api/runs/{run_id}/meta")
        workbench_input = client.get(f"/api/runs/{run_id}/workbench-input")
        page = client.get(f"/runs/{run_id}")

    assert api.status_code == workbench_input.status_code == page.status_code == 404
    assert (
        api.json()["error"]["code"]
        == workbench_input.json()["error"]["code"]
        == page.json()["error"]["code"]
        == "not-found"
    )
    assert run_id not in api.text
    assert run_id not in workbench_input.text
    assert run_id not in page.text


def test_run_scoped_reads_reverify_after_a_prior_success_and_refuse_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="tamper-run", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/tamper-run/meta")
        first_timeline = client.get("/api/runs/tamper-run/timeline?minute=480&agent_id=person-001")
        manifest = root / "city-runs" / "tamper-run" / "run.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        second = client.get("/api/runs/tamper-run/meta")
        second_timeline = client.get("/api/runs/tamper-run/timeline?minute=480&agent_id=person-001")
        page = client.get("/runs/tamper-run")

    assert first.status_code == first_timeline.status_code == 200
    assert second.status_code == second_timeline.status_code == page.status_code == 409
    assert (
        second.json()["error"]
        == second_timeline.json()["error"]
        == page.json()["error"]
        == {
            "code": "run-unavailable",
            "message": "The saved run could not be verified.",
            "fields": {},
        }
    )
    assert str(manifest) not in second.text + page.text


def test_run_scoped_reads_refuse_a_workspace_replaced_between_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="workspace-swap", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/workspace-swap/meta")

        runs_module = importlib.import_module("adlife.city.workbench_runs")

        def replaced(_workspace):
            raise runs_module.UnsafeWorkbenchWorkspace("replaced")

        monkeypatch.setattr(runs_module, "verify_workbench_workspace", replaced)
        second = client.get("/api/runs/workspace-swap/meta")
        workbench_input = client.get("/api/runs/workspace-swap/workbench-input")
        page = client.get("/runs/workspace-swap")

    assert first.status_code == 200
    assert second.status_code == workbench_input.status_code == page.status_code == 409
    assert (
        second.json()["error"]
        == workbench_input.json()["error"]
        == page.json()["error"]
        == {
            "code": "workspace-unavailable",
            "message": "The workbench workspace is unavailable.",
            "fields": {},
        }
    )


def test_run_scoped_reads_report_a_valid_run_with_an_unsafe_location_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="unsafe-location", schema_version=7)
    module = _module()
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    unsafe_detail = "unsafe run location must never be returned"

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/unsafe-location/meta")

        def unsafe_load(_repository: object, _run_id: str) -> None:
            raise UnsafeRunLocation(unsafe_detail)

        monkeypatch.setattr(module.WorkbenchRunRepository, "load", unsafe_load)
        second = client.get("/api/runs/unsafe-location/meta")
        page = client.get("/runs/unsafe-location")

    assert first.status_code == 200
    assert second.status_code == page.status_code == 409
    assert (
        second.json()["error"]
        == page.json()["error"]
        == {
            "code": "run-unavailable",
            "message": "The saved run could not be verified.",
            "fields": {},
        }
    )
    assert unsafe_detail not in second.text + page.text


def test_run_scoped_validation_errors_never_echo_query_values(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(root, run_id="screened-query", schema_version=7)
    workbench = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    legacy = _legacy_saved_run_app(stored)
    secret = "Authorization: Bearer sk-never-echo-scoped-query-1234567890"

    with (
        TestClient(workbench, base_url=ORIGIN) as scoped_client,
        TestClient(legacy, base_url=ORIGIN) as legacy_client,
    ):
        scoped = scoped_client.get(
            "/api/runs/screened-query/frame",
            params={"minute": secret},
        )
        direct = legacy_client.get("/api/frame", params={"minute": secret})
        scoped_timeline = scoped_client.get(
            "/api/runs/screened-query/timeline",
            params={"minute": secret, "agent_id": "person-001"},
        )
        direct_timeline = legacy_client.get(
            "/api/timeline",
            params={"minute": secret, "agent_id": "person-001"},
        )

    assert (
        scoped.status_code
        == direct.status_code
        == scoped_timeline.status_code
        == direct_timeline.status_code
        == 422
    )
    assert (
        scoped.json()
        == direct.json()
        == scoped_timeline.json()
        == direct_timeline.json()
        == {"detail": "request validation failed"}
    )
    assert secret not in scoped.text + direct.text + scoped_timeline.text + direct_timeline.text


def test_submit_is_async_then_polls_to_verified_completion_and_restart_discovery(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    with client:
        accepted = _secure_post(client, "/api/jobs", _tiny_draft())

        assert accepted.status_code == 202
        document = accepted.json()
        assert set(document) == {"schema_version", "job", "status_url"}
        assert document["schema_version"] == 1
        assert document["status_url"] == f"/api/jobs/{document['job']['job_id']}"
        assert document["job"]["run_id"] == "browser-run"
        assert document["job"]["accepted_settings"]["seed"] == "42"
        assert document["job"]["phase"] in {"queued", "evaluating"}

        completed = _await_job(client, document["status_url"])
        assert set(completed) == {
            "schema_version",
            "job_id",
            "run_id",
            "accepted_settings",
            "phase",
            "created_at",
            "updated_at",
            "cancellation_requested",
            "can_cancel",
            "error",
            "result",
        }
        assert completed["phase"] == "completed"
        assert completed["error"] is None
        assert completed["result"]["run_id"] == "browser-run"
        assert completed["result"]["run_schema_version"] == 7
        assert completed["result"]["seed"] == "42"

        page = client.get("/api/runs?offset=0&limit=1")
        assert page.status_code == 200
        assert page.json()["returned_count"] == 1
        assert page.json()["runs"] == [completed["result"]]

    assert (root / "city-runs" / "browser-run" / "inputs" / "workbench.json").is_file()

    restarted = _module().create_city_workbench_app(
        prepare_workbench_workspace(root), csrf_token=TOKEN
    )
    with TestClient(restarted, base_url=ORIGIN) as restarted_client:
        page = restarted_client.get("/api/runs")
    assert page.status_code == 200
    assert [item["run_id"] for item in page.json()["runs"]] == ["browser-run"]


def test_submit_returns_while_evaluation_is_blocked_then_busy_job_can_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    entered = threading.Event()
    release = threading.Event()
    original_prepare = module.WorkbenchRunRepository.prepare

    def blocked_prepare(repository: Any, validated: Any):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test evaluation gate timed out")
        return original_prepare(repository, validated)

    monkeypatch.setattr(module.WorkbenchRunRepository, "prepare", blocked_prepare)
    client, root = _client(tmp_path)
    try:
        with client:
            accepted = _secure_post(client, "/api/jobs", _tiny_draft(run_id="blocked-run"))
            assert accepted.status_code == 202
            assert entered.wait(5)

            busy = _secure_post(client, "/api/jobs", _tiny_draft(run_id="other-run"))
            assert busy.status_code == 409
            assert busy.json()["error"]["code"] == "job-busy"
            capabilities = client.get("/api/workbench")
            assert capabilities.status_code == 200
            assert capabilities.json()["active_job"]["job_id"] == accepted.json()["job"]["job_id"]
            assert capabilities.json()["active_job"]["run_id"] == "blocked-run"
            assert capabilities.json()["active_job"]["phase"] == "evaluating"

            cancelled = _secure_post(
                client,
                accepted.json()["status_url"] + "/cancel",
                {},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["cancellation_requested"] is True
            assert cancelled.json()["can_cancel"] is False
            release.set()
            terminal = _await_job(client, accepted.json()["status_url"])
            assert terminal["phase"] == "cancelled"
    finally:
        release.set()

    assert not (root / "city-runs" / "blocked-run").exists()
    assert not (root / "city-runs" / "other-run").exists()


def test_concurrent_submissions_cannot_both_be_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    submission_barrier = threading.Barrier(2)
    evaluation_entered = threading.Event()
    release_evaluation = threading.Event()
    original_prepare = module.WorkbenchRunRepository.prepare

    def blocked_prepare(repository: Any, validated: Any):
        evaluation_entered.set()
        if not release_evaluation.wait(10):
            raise RuntimeError("test evaluation gate timed out")
        return original_prepare(repository, validated)

    monkeypatch.setattr(module.WorkbenchRunRepository, "prepare", blocked_prepare)
    client, root = _client(tmp_path)
    responses: list[Any] = []
    response_lock = threading.Lock()

    def submit(run_id: str) -> None:
        submission_barrier.wait(5)
        response = _secure_post(client, "/api/jobs", _tiny_draft(run_id=run_id))
        with response_lock:
            responses.append(response)

    first = threading.Thread(target=submit, args=("race-first",))
    second = threading.Thread(target=submit, args=("race-second",))
    try:
        with client:
            first.start()
            second.start()
            first.join(10)
            second.join(10)
            assert not first.is_alive()
            assert not second.is_alive()
            assert sorted(response.status_code for response in responses) == [202, 409]
            accepted = next(response for response in responses if response.status_code == 202)
            refused = next(response for response in responses if response.status_code == 409)
            assert refused.json()["error"]["code"] == "job-busy"
            assert evaluation_entered.wait(5)
            cancelled = _secure_post(client, accepted.json()["status_url"] + "/cancel", {})
            assert cancelled.status_code == 200
            release_evaluation.set()
            assert _await_job(client, accepted.json()["status_url"])["phase"] == "cancelled"
    finally:
        release_evaluation.set()
        first.join(10)
        second.join(10)

    assert not (root / "city-runs").exists()


def test_application_lifespan_closes_its_owned_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    closed = threading.Event()
    original_close = module.CityJobManager.close

    def observed_close(manager: Any) -> None:
        original_close(manager)
        closed.set()

    monkeypatch.setattr(module.CityJobManager, "close", observed_close)
    client, _ = _client(tmp_path)
    with client:
        assert not closed.is_set()
        assert client.get("/api/workbench").status_code == 200

    assert closed.is_set()


def test_duplicate_unknown_job_late_cancel_and_cancel_body_are_stable_refusals(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        accepted = _secure_post(client, "/api/jobs", _tiny_draft(run_id="conflict-run"))
        completed = _await_job(client, accepted.json()["status_url"])
        assert completed["phase"] == "completed"

        duplicate = _secure_post(client, "/api/jobs", _tiny_draft(run_id="conflict-run"))
        missing = client.get("/api/jobs/job-00000000000000000000000000000000")
        malformed = client.get("/api/jobs/not-a-job")
        late = _secure_post(client, accepted.json()["status_url"] + "/cancel", {})
        nonempty = _secure_post(
            client,
            accepted.json()["status_url"] + "/cancel",
            {"unexpected": "value-never-echoed"},
        )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "run-conflict"
    assert missing.status_code == malformed.status_code == 404
    assert missing.json()["error"]["code"] == "job-not-found"
    assert malformed.json()["error"]["code"] == "job-not-found"
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "job-state-conflict"
    assert nonempty.status_code == 422
    assert nonempty.json()["error"]["fields"] == {"request": "Unknown field."}
    assert "value-never-echoed" not in nonempty.text


@pytest.mark.parametrize(
    "query",
    [
        "offset=-1",
        "offset=1.0",
        "offset=true",
        "offset=01",
        "offset=10001",
        "limit=0",
        "limit=101",
        "limit=1.0",
        "limit=true",
        "limit=01",
        "offset=0&offset=1",
        "limit=1&limit=2",
        "unknown=1",
        pytest.param("offset=" + "9" * 5_000, id="very-large-offset"),
    ],
)
def test_run_pagination_refuses_noncanonical_duplicate_or_unknown_query(
    tmp_path: Path,
    query: str,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.get(f"/api/runs?{query}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-query"
    assert "true" not in response.text
    assert "unknown" not in response.text


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/workbench?unexpected=true", None),
        ("get", "/api/catalog/cities?unexpected=true", None),
        (
            "get",
            "/api/catalog/cities/fictional-grid-v2?unexpected=true",
            None,
        ),
        ("get", "/api/creative-templates?unexpected=true", None),
        ("post", "/api/scenarios/validate?dry_run=true", _tiny_draft()),
        ("post", "/api/jobs?dry_run=true", _tiny_draft()),
        ("get", "/api/jobs/job-00000000000000000000000000000000?x=1", None),
        ("post", "/api/jobs/job-00000000000000000000000000000000/cancel?x=1", {}),
    ],
)
def test_fixed_control_routes_refuse_every_query_parameter_without_side_effects(
    tmp_path: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    client, root = _client(tmp_path)
    with client:
        response = (
            client.get(path) if method == "get" else _secure_post(client, path, payload or {})
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-query"
    assert "dry_run" not in response.text
    assert not (root / "city-runs").exists()


@pytest.mark.parametrize(
    "document",
    [
        b'{"settings":',
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b"[]",
    ],
)
def test_invalid_job_documents_create_neither_job_nor_artifact(
    tmp_path: Path,
    document: bytes,
) -> None:
    client, root = _client(tmp_path)
    with client:
        response = client.post(
            "/api/jobs",
            headers={
                "Content-Type": "application/json",
                "Origin": ORIGIN,
                "X-AdLife-CSRF": TOKEN,
            },
            content=document,
        )
        capabilities = client.get("/api/workbench")
        page = client.get("/api/runs")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed-json"
    assert capabilities.json()["active_job"] is None
    assert page.json()["verified_total"] == 0
    assert not (root / "city-runs").exists()


def test_structurally_invalid_job_draft_is_screened_and_never_registered(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, root = _client(tmp_path)
    secret = "sk-never-return-invalid-job-draft-1234567890"
    payload = _tiny_draft(run_id="invalid-draft")
    payload["settings"]["agent_count"] = True
    payload["api_key"] = secret

    with caplog.at_level(logging.ERROR), client:
        response = _secure_post(client, "/api/jobs", payload)
        capabilities = client.get("/api/workbench")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert response.json()["error"]["fields"] == {
        "request": "Request data cannot contain credential fields.",
    }
    assert secret not in response.text + caplog.text
    assert capabilities.json()["active_job"] is None
    assert not (root / "city-runs").exists()


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
def test_well_formed_structural_secret_is_a_semantic_refusal_without_job_or_files(
    tmp_path: Path,
    path: str,
) -> None:
    client, root = _client(tmp_path)
    payload = _tiny_draft()
    # Each string is individually allowed by V1, but the pair is structurally forbidden.
    payload["cohort"]["interests"] = ["api_key", "opaque-private-marker"]
    before = _snapshot(root)
    with client:
        response = _secure_post(client, path, payload)
        capabilities = client.get("/api/workbench")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert "opaque-private-marker" not in response.text
    assert "api_key" not in response.text
    assert capabilities.json()["active_job"] is None
    assert _snapshot(root) == before


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
@pytest.mark.parametrize(
    ("case", "field", "message"),
    [
        ("secret", "request", "Request data cannot contain credential fields."),
        ("future-version", "schema_version", "Use workbench schema version 1, 2, or 3."),
        ("malformed-version", "schema_version", "Use workbench schema version 1, 2, or 3."),
    ],
)
def test_write_endpoints_return_value_free_typed_transport_refusals_before_mutation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    path: str,
    case: str,
    field: str,
    message: str,
) -> None:
    client, root = _client(tmp_path)
    marker = "opaque-private-transport-marker"
    payload = draft_v3_data()
    payload["settings"]["seed"] = "42"
    if case == "secret":
        payload["credentials"] = {"api_key": marker}
    elif case == "future-version":
        payload["schema_version"] = 4
    else:
        payload["schema_version"] = marker
    before = _snapshot(root)

    with caplog.at_level(logging.ERROR), client:
        response = _secure_post(client, path, payload)
        capabilities = client.get("/api/workbench")
        runs = client.get("/api/runs")

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid-fields",
        "message": "One or more request fields are invalid.",
        "fields": {field: message},
    }
    assert marker not in response.text
    assert caplog.text == ""
    assert "internal-error" not in response.text
    assert capabilities.json()["active_job"] is None
    assert runs.json()["verified_total"] == 0
    assert _snapshot(root) == before


def test_secret_content_on_cancel_is_not_diagnosed_as_a_run_draft(tmp_path: Path) -> None:
    client, root = _client(tmp_path)
    before = _snapshot(root)
    with client:
        response = _secure_post(
            client, "/api/jobs/job-" + "a" * 32 + "/cancel", {"api_key": "opaque-private-marker"}
        )
    assert response.status_code == 422
    assert set(response.json()["error"]["fields"]) == {"request"}
    assert "opaque-private-marker" not in response.text
    assert _snapshot(root) == before


def test_worker_submission_refusal_is_constant_and_value_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    secret = "Authorization: Bearer sk-never-return-submission-1234567890"
    absolute_path = r"C:\private\workspace\executor.txt"

    def refuse_submission(*_args: object, **_kwargs: object) -> None:
        raise module.WorkbenchJobSubmissionError(f"{secret} at {absolute_path}")

    monkeypatch.setattr(module.CityJobManager, "submit", refuse_submission)
    client, root = _client(tmp_path)
    with caplog.at_level(logging.ERROR), client:
        response = _secure_post(client, "/api/jobs", _tiny_draft(run_id="submit-failure"))
        capabilities = client.get("/api/workbench")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "worker-unavailable",
        "message": "The workbench worker is not accepting jobs.",
        "fields": {},
    }
    assert secret not in response.text + caplog.text
    assert absolute_path not in response.text + caplog.text
    assert capabilities.json()["active_job"] is None
    assert not (root / "city-runs").exists()


def test_schema3_job_is_safely_refused_until_manifest_v9_without_reserving_a_run(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    payload = draft_v3_data()
    payload["settings"]["seed"] = "42"

    with client:
        response = _secure_post(client, "/api/jobs", payload)
        capabilities = client.get("/api/workbench")
        runs = client.get("/api/runs")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "worker-unavailable",
        "message": "The workbench worker is not accepting jobs.",
        "fields": {},
    }
    assert capabilities.json()["active_job"] is None
    assert runs.json()["verified_total"] == 0
    assert not (root / "city-runs").exists()


@pytest.mark.parametrize("phase", ["prepare", "publish", "verified_summary"])
def test_worker_failures_expose_only_screened_terminal_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    phase: str,
) -> None:
    module = _module()
    secret = "Authorization: Bearer sk-never-expose-worker-failure-1234567890"
    absolute_path = r"C:\private\workspace\job.json"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"{secret} at {absolute_path}")

    monkeypatch.setattr(module.WorkbenchRunRepository, phase, fail)
    client, root = _client(tmp_path)
    with caplog.at_level(logging.ERROR), client:
        accepted = _secure_post(
            client,
            "/api/jobs",
            _tiny_draft(run_id=f"fail-{phase.replace('_', '-')}"),
        )
        assert accepted.status_code == 202
        terminal = _await_job(client, accepted.json()["status_url"])

    assert terminal["phase"] == "failed"
    assert (
        terminal["error"]["code"]
        == {
            "prepare": "evaluation-failed",
            "publish": "persistence-failed",
            "verified_summary": "verification-failed",
        }[phase]
    )
    combined = json.dumps(terminal) + caplog.text
    assert secret not in combined
    assert absolute_path not in combined
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert secret.encode() not in content
            assert absolute_path.encode() not in content


def test_city_pack_upload_returns_one_exact_path_free_row_and_canonical_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    calls: list[bytes] = []
    original = module.WorkbenchCityResolver.register_city

    def counted_register(resolver: WorkbenchCityResolver, document: bytes):
        calls.append(document)
        return original(resolver, document)

    monkeypatch.setattr(module.WorkbenchCityResolver, "register_city", counted_register)
    client, root = _client(tmp_path)
    document = _city_pack_document(indent=2)

    with client:
        response = _secure_upload(client, document)

    assert response.status_code == 201
    assert calls == [document]
    workspace = prepare_workbench_workspace(root)
    resolved = WorkbenchCityResolver(workspace).resolve_city("operator-city")
    expected = module.build_workbench_city_catalog_row(resolved).model_dump(mode="json")
    assert response.json() == expected
    assert set(response.json()) == {
        "city_id",
        "display_name",
        "pack_schema_version",
        "storage_origin",
        "data_origin",
        "qualification",
        "reviewer_role",
        "reviewed_on",
        "coverage",
        "time_zone",
        "source",
        "known_omissions",
        "pack_sha256",
    }
    assert response.json()["storage_origin"] == "workspace-upload"
    assert response.json()["data_origin"] == "operator-supplied"
    assert response.json()["qualification"] == "operator-supplied-unreviewed"
    assert str(root) not in response.text
    assert "resource_name" not in response.text
    stored = root / "city-packs" / "operator-city.json"
    assert stored.read_bytes() == canonical_json(resolved.pack).encode("utf-8") + b"\n"
    assert list((root / "city-pack-staging").iterdir()) == []
    assert not (root / "city-runs").exists()


def test_upload_route_coexists_with_a_city_whose_identifier_is_upload(tmp_path: Path) -> None:
    client, root = _client(tmp_path)
    document = _city_pack_document("upload")

    with client:
        missing = client.get(UPLOAD_PATH)
        registered = _secure_upload(client, document)
        detail = client.get(UPLOAD_PATH)
        duplicate = _secure_upload(client, document)

    assert missing.status_code == 404
    assert registered.status_code == 201
    assert registered.json()["city_id"] == "upload"
    assert detail.status_code == 200
    assert detail.json()["city_id"] == "upload"
    expected_pack = CityPackV2.model_validate_json(_city_pack_document("upload"))
    assert detail.json()["pack"] == expected_pack.model_dump(mode="json")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "city-pack-conflict"
    assert (root / "city-packs" / "upload.json").read_bytes() == (
        canonical_json(expected_pack).encode("utf-8") + b"\n"
    )


def test_city_pack_upload_rejects_query_and_security_failures_before_registration(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    document = _city_pack_document()
    before = _snapshot(root)

    with client:
        responses = [
            _secure_upload(
                client,
                document,
                path=f"{UPLOAD_PATH}?api_key=sk-query-never-echo-1234567890",
            ),
            _secure_upload(client, document, headers={"Content-Type": "text/plain"}),
            _secure_upload(client, document, headers={"Origin": "http://example.test"}),
            _secure_upload(client, document, headers={"X-AdLife-CSRF": "wrong-token"}),
            _secure_upload(
                client,
                document,
                headers={"Host": "example.test", "Origin": "http://example.test"},
            ),
        ]
        for method in ("PUT", "PATCH", "DELETE"):
            responses.append(
                client.request(
                    method,
                    UPLOAD_PATH,
                    headers={
                        "Content-Type": "application/json",
                        "Origin": ORIGIN,
                        "X-AdLife-CSRF": TOKEN,
                    },
                    content=document,
                )
            )

    assert [(response.status_code, response.json()["error"]["code"]) for response in responses] == [
        (400, "invalid-upload-request"),
        (400, "invalid-content-type"),
        (403, "invalid-origin"),
        (403, "invalid-csrf"),
        (400, "invalid-host"),
        (405, "method-not-allowed"),
        (405, "method-not-allowed"),
        (405, "method-not-allowed"),
    ]
    assert "sk-query-never-echo" not in "".join(response.text for response in responses)
    assert _snapshot(root) == before
    assert not (root / "city-packs").exists()


@pytest.mark.parametrize(
    "document",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        "{}".encode("utf-16"),
        b'{"schema_version":2,"schema_version":2}',
        b'{"value":NaN}',
        b'{"value":1e999}',
        b"[]",
        ('{"value":' + "[" * 40 + "0" + "]" * 40 + "}").encode(),
    ],
)
def test_city_pack_upload_maps_malformed_or_ambiguous_json_to_400(
    tmp_path: Path,
    document: bytes,
) -> None:
    client, root = _client(tmp_path)
    before = _snapshot(root)

    with client:
        response = _secure_upload(client, document)

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "malformed-city-pack",
        "message": "The city pack document is not valid JSON.",
        "fields": {},
    }
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": 1},
        {"schema_version": True},
        {**_city_pack_payload(), "unexpected": "value-never-echoed"},
    ],
)
def test_city_pack_upload_maps_well_formed_invalid_v2_to_422(
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    client, root = _client(tmp_path)
    before = _snapshot(root)
    document = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    with client:
        response = _secure_upload(client, document)

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid-city-pack",
        "message": "The city pack is not a valid CityPack V2 document.",
        "fields": {},
    }
    assert "unexpected" not in response.text
    assert "value-never-echoed" not in response.text
    assert _snapshot(root) == before


def test_city_pack_upload_has_an_inclusive_four_mib_raw_boundary(tmp_path: Path) -> None:
    client, root = _client(tmp_path)
    exact = b"{}" + b" " * (MAX_CITY_PACK_UPLOAD_BYTES - 2)

    with client:
        accepted_by_boundary = _secure_upload(client, exact)
        oversized = _secure_upload(client, exact + b" ")

    assert accepted_by_boundary.status_code == 422
    assert accepted_by_boundary.json()["error"]["code"] == "invalid-city-pack"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request-too-large"
    assert not (root / "city-packs").exists()


def test_canonical_city_pack_overflow_maps_to_413_before_registry_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_module = importlib.import_module("adlife.city.workbench_city_registry")

    def canonical_overflow(_pack: CityPackV2) -> bytes:
        raise CityPackUploadTooLarge()

    monkeypatch.setattr(registry_module, "_canonical_pack_bytes", canonical_overflow)
    client, root = _client(tmp_path)
    before = _snapshot(root)

    with client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "city-pack-too-large"
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (
            MalformedCityPackUpload(),
            400,
            "malformed-city-pack",
            "The city pack document is not valid JSON.",
        ),
        (
            InvalidCityPackUpload(),
            422,
            "invalid-city-pack",
            "The city pack is not a valid CityPack V2 document.",
        ),
        (
            CityPackUploadTooLarge(),
            413,
            "city-pack-too-large",
            "The city pack exceeds the upload size limit.",
        ),
        (
            CityPackRegistrationConflict(),
            409,
            "city-pack-conflict",
            "The city pack conflicts with the workspace catalog.",
        ),
        (
            WorkbenchCityRegistryUnavailable("sk-registry-error-never-echo-1234567890"),
            409,
            "city-registry-unavailable",
            "The workspace city registry is unavailable.",
        ),
        (
            PackagedCityCatalogUnavailable("sk-catalog-error-never-echo-1234567890"),
            500,
            "catalog-unavailable",
            "The verified city catalog is unavailable.",
        ),
        (
            CityPackPublicationError(False),
            500,
            "city-pack-publication-failed",
            "The city pack could not be published.",
        ),
        (
            CityPackPublicationError(True),
            500,
            "city-pack-publication-uncertain",
            "The city pack may already be registered. Reload the city catalog before retrying.",
        ),
    ],
)
def test_city_pack_upload_maps_typed_failures_without_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    status: int,
    code: str,
    message: str,
) -> None:
    module = _module()

    def refuse(_resolver: WorkbenchCityResolver, _document: bytes) -> None:
        raise error

    monkeypatch.setattr(module.WorkbenchCityResolver, "register_city", refuse)
    client, root = _client(tmp_path, raise_server_exceptions=False)
    before = _snapshot(root)

    with caplog.at_level(logging.ERROR), client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == status
    assert response.json()["error"] == {"code": code, "message": message, "fields": {}}
    assert "sk-registry-error" not in response.text + caplog.text
    assert "sk-catalog-error" not in response.text + caplog.text
    assert _snapshot(root) == before


def test_corrupt_registry_upload_refusal_preserves_existing_bytes_and_creates_no_record(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    records = root / "city-packs"
    records.mkdir()
    corrupt = records / "unexpected.txt"
    corrupt.write_bytes(b"corrupt-registry-marker")

    with client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "city-registry-unavailable"
    assert corrupt.read_bytes() == b"corrupt-registry-marker"
    assert not (records / "operator-city.json").exists()
    staging = root / "city-pack-staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_precommit_publication_failure_is_generic_and_leaves_no_live_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_module = importlib.import_module("adlife.city.workbench_city_registry")

    def fail_before_commit(*_args: object, **_kwargs: object) -> None:
        raise OSError("private publication path")

    monkeypatch.setattr(registry_module, "_link_stage", fail_before_commit)
    client, root = _client(tmp_path)

    with client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "city-pack-publication-failed",
        "message": "The city pack could not be published.",
        "fields": {},
    }
    assert "private publication path" not in response.text
    assert not (root / "city-packs" / "operator-city.json").exists()
    assert list((root / "city-pack-staging").iterdir()) == []


@pytest.mark.asyncio
async def test_response_send_failure_after_201_keeps_one_discoverable_immutable_record(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)
    document = _city_pack_document("send-failure-city", indent=2)
    pending: list[Message] = [{"type": "http.request", "body": document, "more_body": False}]
    sent: list[Message] = []
    secret = "sk-send-failure-never-echo-1234567890"
    private_path = r"C:\private\response.json"
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": UPLOAD_PATH,
        "raw_path": UPLOAD_PATH.encode("ascii"),
        "query_string": b"",
        "headers": [
            (b"host", b"127.0.0.1:8765"),
            (b"content-type", b"application/json"),
            (b"origin", ORIGIN.encode("ascii")),
            (b"x-adlife-csrf", TOKEN.encode("ascii")),
        ],
        "client": ("127.0.0.1", 40_000),
        "server": ("127.0.0.1", 8_765),
    }

    async def receive() -> Message:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body":
            raise ConnectionError(f"{secret} at {private_path}")

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert [message["status"] for message in starts] == [201]
    assert secret not in caplog.text
    assert private_path not in caplog.text
    resolved = WorkbenchCityResolver(workspace).resolve_city("send-failure-city")
    assert resolved.pack == CityPackV2.model_validate_json(_city_pack_document("send-failure-city"))
    stored = workspace.root / "city-packs" / "send-failure-city.json"
    assert stored.read_bytes() == canonical_json(resolved.pack).encode("utf-8") + b"\n"
    assert list((workspace.root / "city-pack-staging").iterdir()) == []

    retry_app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)
    with TestClient(retry_app, base_url=ORIGIN) as client:
        retry = _secure_upload(client, document)
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "city-pack-conflict"
    assert stored.read_bytes() == canonical_json(resolved.pack).encode("utf-8") + b"\n"


def test_catalog_and_creative_endpoints_are_complete_and_stably_ordered(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        cities_response = client.get("/api/catalog/cities")
        creatives_response = client.get("/api/creative-templates")

    assert cities_response.status_code == 200
    cities = cities_response.json()
    assert set(cities) == {"schema_version", "cities"}
    assert cities["schema_version"] == 1
    assert [item["city_id"] for item in cities["cities"]] == sorted(
        item["city_id"] for item in cities["cities"]
    )
    assert [item["city_id"] for item in cities["cities"]] == [
        "fictional-grid-v2",
        "fictional-harbor-loop-v2",
        "fictional-river-junction-v2",
    ]
    city = next(item for item in cities["cities"] if item["city_id"] == "fictional-grid-v2")
    assert set(city) == {
        "city_id",
        "display_name",
        "pack_schema_version",
        "storage_origin",
        "data_origin",
        "qualification",
        "reviewer_role",
        "reviewed_on",
        "coverage",
        "time_zone",
        "source",
        "known_omissions",
        "pack_sha256",
    }
    assert city["city_id"] == "fictional-grid-v2"
    assert city["storage_origin"] == "package"
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
        "fictional-coffee-pass-v1",
        "fictional-crispy-meal-v1",
        "fictional-device-launch-v1",
        "fictional-home-delivery-v1",
    ]
    assert all(len(item["creative_sha256"]) == 64 for item in creatives["templates"])
    assert all(
        item["disclosure"] == "Fictional creative for synthetic simulation only."
        for item in creatives["templates"]
    )


def test_city_detail_returns_the_exact_verified_pack_without_resource_paths(
    tmp_path: Path,
) -> None:
    catalog = load_city_catalog()
    entry = catalog.entries[0]
    pack = select_catalog_city(entry.city_id)
    client, _ = _client(tmp_path)

    with client:
        response = client.get(f"/api/catalog/cities/{entry.city_id}")

    assert response.status_code == 200
    document = response.json()
    assert document["schema_version"] == 1
    assert document["city_id"] == entry.city_id
    assert document["storage_origin"] == "package"
    assert document["city_sha256"] == entry.pack_sha256 == pack.fingerprint
    assert document["pack"] == pack.model_dump(mode="json")
    assert document["coverage"] == entry.coverage.model_dump(mode="json")
    assert document["source"] == entry.source.model_dump(mode="json")
    assert document["known_omissions"] == list(entry.known_omissions)
    assert "resource_name" not in response.text
    assert entry.resource_name not in response.text


def test_catalog_routes_are_lazy_and_load_one_verified_snapshot_per_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    catalog_module = importlib.import_module("adlife.city.workbench_catalog")
    original = catalog_module.load_packaged_workbench_cities
    calls = 0

    def counted_snapshot():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(catalog_module, "load_packaged_workbench_cities", counted_snapshot)
    root = tmp_path / "lazy-workspace"
    workspace = prepare_workbench_workspace(root)
    app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)
    assert calls == 0

    with TestClient(app, base_url=ORIGIN) as client:
        listed = client.get("/api/catalog/cities")
        assert calls == 1
        detailed = client.get("/api/catalog/cities/fictional-grid-v2")
        assert calls == 2

    assert listed.status_code == 200
    assert detailed.status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/api/catalog/cities", "/api/catalog/cities/fictional-grid-v2"],
)
def test_catalog_routes_screen_packaged_snapshot_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    catalog_module = importlib.import_module("adlife.city.workbench_catalog")
    secret = "sk-packaged-city-catalog-secret-1234567890"

    def unavailable() -> None:
        raise PackagedCityCatalogUnavailable(secret)

    monkeypatch.setattr(catalog_module, "load_packaged_workbench_cities", unavailable)
    client, _ = _client(tmp_path, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR), client:
        response = client.get(path)

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "catalog-unavailable",
        "message": "The verified city catalog is unavailable.",
        "fields": {},
    }
    assert secret not in response.text + caplog.text


def test_app_owns_one_lazy_resolver_and_resolves_each_accepted_draft_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.city.workbench_catalog import ResolvedWorkbenchCity

    module = _module()
    selected_pack = select_catalog_city("fictional-grid-v2")
    resolved = ResolvedWorkbenchCity(
        pack=selected_pack,
        storage_origin="package",
        data_origin="fictional",
        qualification="fictional-fixture",
        reviewer_role=None,
        reviewed_on=None,
    )
    instances: list[Any] = []

    class RecordingResolver:
        def __init__(self, workspace: Any) -> None:
            self.workspace = workspace
            self.list_calls = 0
            self.resolve_calls: list[str] = []
            instances.append(self)

        def list_cities(self):
            self.list_calls += 1
            return (resolved,)

        def resolve_city(self, city_id: str):
            self.resolve_calls.append(city_id)
            return resolved

    parse_calls = 0
    construct_calls = 0
    real_parse = module.parse_workbench_http_draft
    real_construct = module.construct_workbench_run

    def counted_parse(payload: Any):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(payload)

    def counted_construct(draft: Any, *, selected_pack: Any = None):
        nonlocal construct_calls
        construct_calls += 1
        assert selected_pack is selected_pack_reference
        return real_construct(draft, selected_pack=selected_pack)

    selected_pack_reference = selected_pack
    monkeypatch.setattr(module, "WorkbenchCityResolver", RecordingResolver)
    monkeypatch.setattr(module, "parse_workbench_http_draft", counted_parse)
    monkeypatch.setattr(module, "construct_workbench_run", counted_construct)
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)

    assert len(instances) == 1
    assert instances[0].list_calls == 0
    assert instances[0].resolve_calls == []

    with TestClient(app, base_url=ORIGIN) as client:
        listed = client.get("/api/catalog/cities")
        detailed = client.get("/api/catalog/cities/fictional-grid-v2")
        validated = _secure_post(client, "/api/scenarios/validate", _tiny_draft())
        submitted = _secure_post(
            client,
            "/api/jobs",
            _tiny_draft(run_id="one-resolve-job"),
        )
        if submitted.status_code == 202:
            _await_job(client, submitted.json()["status_url"])

    assert listed.status_code == 200
    assert detailed.status_code == 200
    assert validated.status_code == 200
    assert submitted.status_code == 202
    assert len(instances) == 1
    assert instances[0].list_calls == 1
    assert instances[0].resolve_calls == [
        "fictional-grid-v2",
        "fictional-grid-v2",
        "fictional-grid-v2",
    ]
    assert parse_calls == 2
    assert construct_calls == 2


def test_corrupt_registry_blocks_new_city_operations_but_not_frozen_runs(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="frozen-run", schema_version=7)
    records = root / "city-packs"
    records.mkdir()
    (records / "unexpected.txt").write_bytes(b"corrupt registry state")

    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        listing = client.get("/api/runs")
        inspector = client.get("/runs/frozen-run")
        metadata = client.get("/api/runs/frozen-run/meta")
        replay_frame = client.get("/api/runs/frozen-run/frame?minute=0")
        blocked = [
            client.get("/api/catalog/cities"),
            client.get("/api/catalog/cities/fictional-grid-v2"),
            client.get("/api/catalog/cities/unknown-city"),
            _secure_post(client, "/api/scenarios/validate", _tiny_draft()),
            _secure_post(client, "/api/jobs", _tiny_draft(run_id="blocked-new-run")),
        ]

    assert listing.status_code == 200
    assert listing.json()["runs"][0]["run_id"] == "frozen-run"
    assert inspector.status_code == 200
    assert metadata.status_code == 200
    assert metadata.json()["run_id"] == "frozen-run"
    assert replay_frame.status_code == 200
    assert all(response.status_code == 409 for response in blocked)
    assert all(
        response.json()["error"]
        == {
            "code": "city-registry-unavailable",
            "message": "The workspace city registry is unavailable.",
            "fields": {},
        }
        for response in blocked
    )
    assert not (root / "city-runs" / "blocked-new-run").exists()


@pytest.mark.parametrize(
    "operation",
    ["list", "detail", "validate", "submit"],
)
def test_packaged_catalog_corruption_blocks_only_new_city_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    module = _module()
    catalog_module = importlib.import_module("adlife.city.workbench_catalog")

    def unavailable() -> None:
        raise PackagedCityCatalogUnavailable("private provider detail")

    monkeypatch.setattr(catalog_module, "load_packaged_workbench_cities", unavailable)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"),
        csrf_token=TOKEN,
    )
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        if operation == "list":
            response = client.get("/api/catalog/cities")
        elif operation == "detail":
            response = client.get("/api/catalog/cities/fictional-grid-v2")
        elif operation == "validate":
            response = _secure_post(client, "/api/scenarios/validate", _tiny_draft())
        else:
            response = _secure_post(
                client,
                "/api/jobs",
                _tiny_draft(run_id="catalog-blocked-run"),
            )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "catalog-unavailable",
        "message": "The verified city catalog is unavailable.",
        "fields": {},
    }


@pytest.mark.parametrize("city_id", ["unknown-city", "INVALID", "..%2Fprivate"])
def test_city_detail_unknown_or_malformed_identifiers_are_screened(
    tmp_path: Path,
    city_id: str,
) -> None:
    client, _ = _client(tmp_path)

    with client:
        response = client.get(f"/api/catalog/cities/{city_id}")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not-found",
        "message": "The requested resource was not found.",
        "fields": {},
    }
    assert city_id not in response.text


def test_city_detail_catalog_integrity_failure_is_screened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    client, _ = _client(tmp_path, raise_server_exceptions=False)
    secret = "sk-catalog-integrity-secret-1234567890"

    def unavailable(_city_id: str) -> None:
        raise CityCatalogError(secret)

    monkeypatch.setattr(module, "build_workbench_city_detail", unavailable)
    with caplog.at_level(logging.ERROR), client:
        response = client.get("/api/catalog/cities/fictional-grid-v2")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "catalog-unavailable",
        "message": "The verified city catalog is unavailable.",
        "fields": {},
    }
    assert secret not in response.text + caplog.text


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
        "seed": "42",
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
    assert _artifact_snapshot(root) == before == ()


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
@pytest.mark.parametrize(
    "seed",
    [42, True, "01", "+42", " 42", "42.0", "9223372036854775808"],
)
def test_write_endpoints_require_a_canonical_decimal_seed_string(
    tmp_path: Path,
    path: str,
    seed: object,
) -> None:
    client, root = _client(tmp_path)
    payload = _tiny_draft(run_id="invalid-seed")
    payload["settings"]["seed"] = seed

    with client:
        response = _secure_post(client, path, payload)

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid-fields",
        "message": "One or more request fields are invalid.",
        "fields": {
            "settings.seed": ("Enter a whole-number seed from 0 through 9223372036854775807.")
        },
    }
    assert _snapshot(root) == ()


def test_maximum_seed_remains_an_exact_string_in_validation_jobs_and_limits(
    tmp_path: Path,
) -> None:
    maximum = "9223372036854775807"
    client, _ = _client(tmp_path)
    validation_payload = _tiny_draft(run_id="maximum-seed-validation")
    validation_payload["settings"]["seed"] = maximum
    job_payload = _tiny_draft(run_id="maximum-seed-job")
    job_payload["settings"]["seed"] = maximum

    with client:
        capabilities = client.get("/api/workbench")
        validation = _post(client, validation_payload)
        accepted = _secure_post(client, "/api/jobs", job_payload)
        completed = _await_job(client, accepted.json()["status_url"])

    assert capabilities.status_code == validation.status_code == 200
    assert capabilities.json()["limits"]["seed"] == {
        "minimum": "0",
        "maximum": maximum,
    }
    assert validation.json()["run"]["seed"] == maximum
    assert accepted.status_code == 202
    assert accepted.json()["job"]["accepted_settings"]["seed"] == maximum
    assert completed["accepted_settings"]["seed"] == maximum
    assert completed["result"]["seed"] == maximum


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
    assert _artifact_snapshot(root) == before == ()


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
    assert fields == {"request": "Request data cannot contain credential fields."}
    assert secret not in invalid.text
    assert absolute_path not in invalid.text
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not-found"
    assert _snapshot(root) == before == ()


@pytest.mark.parametrize(
    "path",
    [
        ("scenario", "scenario_id"),
        ("scenario", "campaign", "campaign_id"),
    ],
)
def test_validation_refuses_secret_identifiers_without_echo(
    tmp_path: Path,
    path: tuple[str, ...],
) -> None:
    client, root = _client(tmp_path)
    secret = "sk-" + "a" * 32
    payload = _valid_draft()
    target: Any = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = secret

    with client:
        response = _post(client, payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert secret not in response.text
    assert _snapshot(root) == ()


@pytest.mark.parametrize("unknown_key", ["Unknown", "sk-" + "a" * 32])
def test_validation_maps_unknown_keys_to_constant_field_without_echo(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    unknown_key: str,
) -> None:
    client, root = _client(tmp_path)
    secret_value = "Authorization: Bearer sk-never-log-this-1234567890"
    payload = _valid_draft()
    payload[unknown_key] = secret_value

    with caplog.at_level(logging.ERROR), client:
        response = _post(client, payload)

    assert response.status_code == 422
    assert response.json()["error"]["fields"] == {"request": "Unknown field."}
    combined = response.text + caplog.text
    assert unknown_key not in response.json()["error"]["fields"]
    assert unknown_key not in caplog.text
    if unknown_key.startswith("sk-"):
        assert unknown_key not in response.text
    assert secret_value not in combined
    assert "Traceback" not in combined
    assert _snapshot(root) == ()


@pytest.mark.parametrize("activities", [[{}], [[]]])
def test_validation_maps_malformed_activity_members_to_422(
    tmp_path: Path,
    activities: list[object],
) -> None:
    client, root = _client(tmp_path)
    payload = _valid_draft()
    payload["scenario"]["phone"]["eligible_activities"] = activities

    with client:
        response = _post(client, payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert _snapshot(root) == ()


def test_request_validation_and_unexpected_failures_are_screened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    client, root = _client(tmp_path)
    secret = "sk-unexpected-secret-value-1234567890"
    absolute_path = r"C:\private\workspace\run.json"
    before = _snapshot(root)

    def request_failure(_draft: object, *, selected_pack: object) -> None:
        del selected_pack
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

    def unexpected_failure(_draft: object, *, selected_pack: object) -> None:
        del selected_pack
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RuntimeError(f"at {absolute_path}") from cause

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
    assert _artifact_snapshot(root) == before == ()


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
    assert _artifact_snapshot(root) == before == ()
    assert client.app.router.on_startup == []
    assert client.app.router.on_shutdown == []
    assert not (root / "city-runs").exists()


def test_shell_assets_are_local_escaped_honest_and_csp_restricted(tmp_path: Path) -> None:
    hostile_token = 'token"><script>alert("x")</script>'
    client, _ = _client(tmp_path, token=hostile_token)
    with client:
        shell = client.get("/")
        css = client.get("/assets/workbench.css")
        javascript = client.get("/assets/workbench.js")
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
    assert '<link rel="stylesheet" href="/assets/workbench.css">' in shell.text
    assert '<link rel="icon" href="/assets/workbench-icon.svg" type="image/svg+xml">' in shell.text
    assert shell.text.count('<script defer src="/assets/workbench.js"></script>') == 1
    assert "<script>" not in shell.text.lower()
    assert "onclick=" not in shell.text.lower()
    assert 'class="skip-link"' in shell.text
    assert 'id="synthetic-disclosure"' in shell.text
    assert '<h1 id="city-title">Choose a city</h1>' in shell.text
    assert ">Synthetic model</strong>" in shell.text
    assert "Fictional people and modeled responses" in shell.text
    assert 'aria-label="City study stages"' in shell.text
    assert [
        shell.text.count(f'class="stage-name">{stage}</b>')
        for stage in (
            "CITY",
            "CAMPAIGN",
            "RUN",
            "INSPECT",
        )
    ] == [1, 1, 1, 1]
    assert 'id="city-map"' in shell.text
    assert 'id="city-map-alternative"' in shell.text
    assert ">Map details</summary>" in shell.text
    assert "<summary>Data &amp; integrity</summary>" in shell.text
    assert 'id="completed-runs"' in shell.text
    assert "not observed behavior, real sales" in shell.text
    assert "This city workflow does not call an AI provider" in shell.text
    assert "CAUSAL THREAD" not in shell.text
    assert "Compose bounded campaign assumptions" not in shell.text
    for control in ("<input", "<select", "<button"):
        assert control in shell.text.lower()
    assert "http://" not in shell.text
    assert "https://" not in shell.text

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "@import" not in css.text.lower()
    assert "@font-face" not in css.text.lower()
    assert "http://" not in css.text
    assert "https://" not in css.text
    assert "url(" not in css.text.lower()

    assert javascript.status_code == 200
    assert javascript.headers["content-type"].startswith("text/javascript")
    assert "innerHTML" not in javascript.text
    assert "insertAdjacentHTML" not in javascript.text
    assert "localStorage" not in javascript.text
    assert "sessionStorage" not in javascript.text
    assert "http://" not in javascript.text
    assert "https://" not in javascript.text

    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert icon.text.startswith("<svg")
    assert "<script" not in icon.text.lower()
    assert "<foreignobject" not in icon.text.lower()
    assert "href=" not in icon.text.lower()
    assert hostile_host.status_code == 400
    for response in (shell, css, javascript, icon, hostile_host):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in response.headers
        assert "server" not in response.headers
