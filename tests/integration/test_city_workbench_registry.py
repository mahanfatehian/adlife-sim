from __future__ import annotations

import json
import multiprocessing
import threading
import time
from pathlib import Path
from queue import Empty
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adlife.city.catalog import select_catalog_city
from adlife.city.run_store import CityRunStore
from adlife.city.runs import replay_city_run
from adlife.city.workbench_city_registry import (
    CityPackRegistrationConflict,
    WorkspaceCityRegistry,
)
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.unit.city.test_workbench_input import draft_data, draft_v2_data

_TOKEN = "fixed-registry-integration-token"
_ORIGIN = "http://127.0.0.1:8765"


def _document(city_id: str) -> bytes:
    payload = select_catalog_city("fictional-grid-v2").model_dump(mode="json")
    payload["city_id"] = city_id
    payload["name"] = f"Operator {city_id}"
    return json.dumps(payload, ensure_ascii=False).encode()


def test_uploaded_city_v10_storage_fresh_load_needs_no_live_registry(tmp_path, monkeypatch):
    from adlife.city.workbench_catalog import WorkbenchCityResolver
    from adlife.city.workbench_input import WorkbenchRunDraftV4
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10, _save_prepared
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    workspace = prepare_workbench_workspace(tmp_path)
    resolved = WorkbenchCityResolver(workspace).register_city(_document("v10-uploaded-city"))
    document = draft_v4_data(mode="rules")
    document["city_id"] = resolved.pack.city_id
    compiled = construct_workbench_run(
        WorkbenchRunDraftV4.model_validate_json(json.dumps(document)),
        selected_pack=resolved.pack,
    )
    _, prepared = _prepared_v10(tmp_path, validated=compiled)
    _save_prepared(tmp_path, prepared)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("saved V10 consulted a live city registry")

    monkeypatch.setattr(WorkbenchCityResolver, "__init__", forbidden)
    monkeypatch.setattr(WorkspaceCityRegistry, "__init__", forbidden)
    stored = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert stored.pack == resolved.pack
    assert stored.manifest.city_sha256 == resolved.pack.fingerprint
    assert stored.workbench_input.draft.city_id == resolved.pack.city_id
    assert stored.cognition_input.run_input_sha256 == stored.workbench_input.fingerprint


def _post_json(client: TestClient, path: str, payload: dict[str, Any]):
    return client.post(
        path,
        headers={
            "Content-Type": "application/json",
            "Origin": _ORIGIN,
            "X-AdLife-CSRF": _TOKEN,
        },
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _upload(client: TestClient, document: bytes):
    return client.post(
        "/api/catalog/cities/upload",
        headers={
            "Content-Type": "application/json",
            "Origin": _ORIGIN,
            "X-AdLife-CSRF": _TOKEN,
        },
        content=document,
    )


def _await_job(client: TestClient, status_url: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    pause = threading.Event()
    while time.monotonic() < deadline:
        response = client.get(status_url)
        assert response.status_code == 200
        job = response.json()
        if job["phase"] in {"completed", "failed", "cancelled"}:
            return job
        pause.wait(0.005)
    raise AssertionError(f"job at {status_url} did not terminate")


def _roadside_draft(
    input_schema_version: int,
    *,
    city_id: str,
    run_id: str,
) -> dict[str, Any]:
    if input_schema_version == 1:
        payload = draft_data(phone=False, roadside=True)
    else:
        payload = draft_v2_data(agent_count=1)
        payload["scenario"] = draft_data(phone=False, roadside=True)["scenario"]
    payload["city_id"] = city_id
    payload["settings"].update(
        {
            "run_id": run_id,
            "agent_count": 1,
            "days": 1,
            "seed": "17",
        }
    )
    roadside = payload["scenario"]["roadside"]
    assert isinstance(roadside, dict)
    roadside["road_id"] = "middle-west"
    roadside["active_windows"] = [{"day": 1, "start_minute": 420, "end_minute": 480}]
    return payload


def _register_process(
    workspace_path: str,
    city_id: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue[str],
) -> None:
    registry = WorkspaceCityRegistry(prepare_workbench_workspace(Path(workspace_path)))
    start.wait(timeout=20)
    try:
        registry.register(_document(city_id), forbidden_city_ids=frozenset())
    except CityPackRegistrationConflict:
        results.put("conflict")
    except Exception as error:  # pragma: no cover - reported to parent for diagnosis
        results.put(f"unexpected:{type(error).__name__}")
    else:
        results.put("accepted")


def _race(tmp_path: Path, city_ids: tuple[str, str]) -> tuple[list[str], WorkspaceCityRegistry]:
    workspace_path = tmp_path / "workspace"
    WorkspaceCityRegistry(prepare_workbench_workspace(workspace_path)).discover()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_register_process,
            args=(str(workspace_path), city_id, start, results),
        )
        for city_id in city_ids
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)
        assert not process.is_alive()
        assert process.exitcode == 0
    answers: list[str] = []
    for _process in processes:
        try:
            answers.append(results.get(timeout=5))
        except Empty:
            pytest.fail("registry race worker did not report a result")
    return answers, WorkspaceCityRegistry(prepare_workbench_workspace(workspace_path))


def test_real_process_duplicate_registration_has_one_winner(tmp_path: Path) -> None:
    answers, registry = _race(tmp_path, ("race-city", "race-city"))

    assert sorted(answers) == ["accepted", "conflict"]
    assert tuple(pack.city_id for pack in registry.discover()) == ("race-city",)


def test_real_process_capacity_race_cannot_publish_a_fifty_first_pack(tmp_path: Path) -> None:
    registry = WorkspaceCityRegistry(prepare_workbench_workspace(tmp_path / "workspace"))
    for number in range(49):
        registry.register(_document(f"seed-{number:02d}"), forbidden_city_ids=frozenset())

    answers, restarted = _race(tmp_path, ("candidate-a", "candidate-b"))

    assert sorted(answers) == ["accepted", "conflict"]
    assert len(restarted.discover()) == 50


def test_reader_never_observes_a_partial_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import adlife.city.workbench_city_registry as registry_module

    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    writer = WorkspaceCityRegistry(workspace)
    reader = WorkspaceCityRegistry(workspace)
    linked = threading.Event()
    release = threading.Event()
    real_link = registry_module._link_stage

    def pausing_link(*args: object, **kwargs: object) -> None:
        real_link(*args, **kwargs)
        linked.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(registry_module, "_link_stage", pausing_link)
    write_failure: list[BaseException] = []
    read_result: list[tuple[str, ...]] = []

    def publish() -> None:
        try:
            writer.register(_document("atomic-city"), forbidden_city_ids=frozenset())
        except BaseException as error:  # pragma: no cover - asserted below
            write_failure.append(error)

    def discover() -> None:
        read_result.append(tuple(pack.city_id for pack in reader.discover()))

    writer_thread = threading.Thread(target=publish)
    writer_thread.start()
    assert linked.wait(timeout=10)
    reader_thread = threading.Thread(target=discover)
    reader_thread.start()
    assert reader_thread.is_alive()
    release.set()
    writer_thread.join(timeout=10)
    reader_thread.join(timeout=10)

    assert write_failure == []
    assert read_result == [("atomic-city",)]


@pytest.mark.parametrize(
    ("input_schema_version", "run_schema_version"),
    [(1, 7), (2, 8)],
)
def test_uploaded_city_completes_the_workbench_flow_after_restart(
    tmp_path: Path,
    input_schema_version: int,
    run_schema_version: int,
) -> None:
    root = tmp_path / f"workspace-v{input_schema_version}"
    city_id = f"operator-flow-v{input_schema_version}"
    run_id = f"uploaded-flow-v{run_schema_version}"
    draft = _roadside_draft(
        input_schema_version,
        city_id=city_id,
        run_id=run_id,
    )
    app = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=_TOKEN,
    )

    with TestClient(app, base_url=_ORIGIN) as client:
        upload = _upload(client, _document(city_id))
        assert upload.status_code == 201
        uploaded = upload.json()
        assert uploaded["city_id"] == city_id
        assert (
            uploaded["storage_origin"],
            uploaded["data_origin"],
            uploaded["qualification"],
        ) == (
            "workspace-upload",
            "operator-supplied",
            "operator-supplied-unreviewed",
        )

        listing = client.get("/api/catalog/cities")
        detail = client.get(f"/api/catalog/cities/{city_id}")
        assert listing.status_code == detail.status_code == 200
        assert city_id in [row["city_id"] for row in listing.json()["cities"]]
        city = detail.json()
        assert city["city_sha256"] == uploaded["pack_sha256"]
        assert city["pack"]["city_id"] == city_id

        validation = _post_json(client, "/api/scenarios/validate", draft)
        assert validation.status_code == 200
        receipt = validation.json()
        assert receipt["city"] == {
            "city_id": city_id,
            "city_sha256": uploaded["pack_sha256"],
        }
        assert receipt["scenario"]["placement_count"] == 1
        assert receipt["scenario"]["channels"] == ["roadside-billboard"]
        assert receipt["scenario"]["placements"] == [
            {
                "placement_id": "roadside-placement",
                "channel": "roadside-billboard",
            }
        ]

        accepted = _post_json(client, "/api/jobs", draft)
        assert accepted.status_code == 202
        terminal = _await_job(client, accepted.json()["status_url"])
        assert terminal["phase"] == "completed"
        assert terminal["error"] is None
        assert terminal["result"]["run_id"] == run_id
        assert terminal["result"]["run_schema_version"] == run_schema_version
        assert terminal["result"]["city_id"] == city_id
        assert terminal["result"]["city_sha256"] == uploaded["pack_sha256"]
        assert terminal["result"]["channels"] == ["roadside-billboard"]

    restarted = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=_TOKEN,
    )
    with TestClient(restarted, base_url=_ORIGIN) as client:
        catalog_detail = client.get(f"/api/catalog/cities/{city_id}")
        runs = client.get("/api/runs")
        inspector = client.get(f"/runs/{run_id}")
        metadata = client.get(f"/api/runs/{run_id}/meta")
        frozen_city = client.get(f"/api/runs/{run_id}/city")
        frozen_input = client.get(f"/api/runs/{run_id}/workbench-input")
        frame = client.get(f"/api/runs/{run_id}/frame?minute=0")

    assert all(
        response.status_code == 200
        for response in (
            catalog_detail,
            runs,
            inspector,
            metadata,
            frozen_city,
            frozen_input,
            frame,
        )
    )
    assert catalog_detail.json()["city_sha256"] == uploaded["pack_sha256"]
    assert [row["run_id"] for row in runs.json()["runs"]] == [run_id]
    assert metadata.json()["run_schema_version"] == run_schema_version
    assert metadata.json()["city_sha256"] == uploaded["pack_sha256"]
    assert frozen_city.json()["city_id"] == city_id
    assert frozen_input.json()["run_schema_version"] == run_schema_version
    assert frozen_input.json()["workbench_input_schema_version"] == input_schema_version
    assert frozen_input.json()["city_sha256"] == uploaded["pack_sha256"]

    stored = CityRunStore(root).load(run_id)
    replay = replay_city_run(stored)
    assert replay.identical is True
    assert replay.city_sha256 == stored.manifest.city_sha256 == uploaded["pack_sha256"]
    assert replay.trace_sha256 == stored.manifest.trace_sha256
    assert replay.scenario_sha256 == stored.manifest.scenario_sha256
    assert replay.response_input_sha256 == stored.manifest.response_input_sha256
