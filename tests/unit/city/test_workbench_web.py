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
        assert document["job"]["phase"] in {"queued", "evaluating"}

        completed = _await_job(client, document["status_url"])
        assert set(completed) == {
            "schema_version",
            "job_id",
            "run_id",
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
    assert "Bounded run foundation" in shell.text
    assert "Bounded run execution is available" in shell.text
    assert "Run execution is not enabled" not in shell.text
    assert "creates no job or run directory" not in shell.text
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
