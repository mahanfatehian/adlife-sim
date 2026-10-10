from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.catalog import CityCatalogError, load_city_catalog, select_catalog_city
from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run
from adlife.city.web import create_city_app
from adlife.city.workbench_input import WorkbenchRunDraft
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.ports.run_store import UnsafeRunLocation
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
            "city": "Bundled fictional city geometry; not a live city or traffic feed.",
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
        manifest = root / "city-runs" / "tamper-run" / "run.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        second = client.get("/api/runs/tamper-run/meta")
        page = client.get("/runs/tamper-run")

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

    assert scoped.status_code == direct.status_code == 422
    assert scoped.json() == direct.json() == {"detail": "request validation failed"}
    assert secret not in scoped.text + direct.text


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
        "request": "Unknown field.",
        "settings.agent_count": "This field is invalid.",
    }
    assert secret not in response.text + caplog.text
    assert capabilities.json()["active_job"] is None
    assert not (root / "city-runs").exists()


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
    assert document["city_sha256"] == entry.pack_sha256 == pack.fingerprint
    assert document["pack"] == pack.model_dump(mode="json")
    assert document["coverage"] == entry.coverage.model_dump(mode="json")
    assert document["source"] == entry.source.model_dump(mode="json")
    assert document["known_omissions"] == list(entry.known_omissions)
    assert "resource_name" not in response.text
    assert entry.resource_name not in response.text


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
    assert _snapshot(root) == before == ()


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
    assert set(fields) == {"request", "settings.agent_count"}
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
    assert 'id="completed-runs"' in shell.text
    assert "SYNTHETIC" in shell.text
    assert "not real residents" in shell.text
    assert "Provider and OAuth settings are not installed" in shell.text
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
