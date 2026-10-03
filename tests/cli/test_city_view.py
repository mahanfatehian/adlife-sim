"""The saved-city viewer opens only a validated artifact on loopback."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from typer.testing import CliRunner

from adlife.city.runs import create_city_run
from adlife.cli.app import app
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario


@pytest.mark.asyncio
async def test_city_view_loads_saved_run_before_binding_and_serves_exact_core_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="saved-study", seed=42, agent_count=2, days=1
    )
    before = {
        path.name: path.read_bytes() for path in stored.directory.rglob("*") if path.is_file()
    }
    calls: list[tuple[FastAPI, dict[str, object]]] = []

    def fake_run(application: FastAPI, **kwargs: object) -> None:
        calls.append((application, kwargs))

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(app, ["city-view", str(tmp_path), "saved-study", "--port", "8766"])
    assert result.exit_code == 0, result.output
    application, options = calls[0]
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 8766
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://city.test"
    ) as web:
        metadata = await web.get("/api/meta")
        frame = await web.get("/api/frame?minute=480")
        other = await web.get("/api/run?run_id=another")
        path = await web.get("/api/frame?minute=480&path=../run.json")
    assert metadata.json()["run_id"] == "saved-study"
    assert metadata.json()["saved"] is True
    assert frame.json() == stored.mobility.frame_document(480)
    assert other.status_code == 404
    assert path.json() == frame.json()
    assert before == {
        path.name: path.read_bytes() for path in stored.directory.rglob("*") if path.is_file()
    }


def test_city_view_refuses_corruption_before_server_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="saved-study", seed=42, agent_count=2, days=1
    )
    (stored.directory / "run.json").write_text("{}", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(args))
    result = CliRunner().invoke(app, ["city-view", str(tmp_path), "saved-study"])
    assert result.exit_code == 4
    assert calls == []
    assert "Traceback" not in result.output


def test_city_view_refuses_machine_mode(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["--format", "json", "city-view", str(tmp_path), "study"])
    assert result.exit_code == 2
    assert '"exit_code": 2' in result.stdout


@pytest.mark.asyncio
async def test_city_view_serves_saved_v3_places_and_exact_manifest_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = create_city_run(
        load_pack_v2(pack_v2_data()),
        root=tmp_path,
        run_id="place-study",
        seed=42,
        agent_count=2,
        days=7,
        places=mobility_place_set(),
    )
    before = {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }
    calls: list[FastAPI] = []
    monkeypatch.setattr("uvicorn.run", lambda application, **kwargs: calls.append(application))
    result = CliRunner().invoke(app, ["city-view", str(tmp_path), "place-study"])
    assert result.exit_code == 0, result.output
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=calls[0]), base_url="http://city.test"
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        places = await web.get("/api/places")
        assignments = await web.get("/api/place-assignments")
    assert metadata["run_schema_version"] == 3
    assert places.status_code == assignments.status_code == 200
    assert assignments.json() == stored.mobility.place_assignment_document()
    assert before == {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.asyncio
async def test_city_view_serves_verified_v5_opportunity_evidence_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=2,
            )
        ],
    )
    stored = create_city_run(
        pack,
        root=tmp_path,
        run_id="spatial-study",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
    )
    before = {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }
    calls: list[FastAPI] = []
    monkeypatch.setattr("uvicorn.run", lambda application, **kwargs: calls.append(application))

    result = CliRunner().invoke(app, ["city-view", str(tmp_path), "spatial-study"])

    assert result.exit_code == 0, result.output
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=calls[0]), base_url="http://city.test"
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        summary = (await web.get("/api/opportunity-summary")).json()
        opportunities = (await web.get("/api/opportunities?minute=0")).json()
        attention_summary = (await web.get("/api/attention-summary")).json()
        attention_events = (await web.get("/api/attention-events?minute=0")).json()
        metrics = (await web.get("/api/spatial-metrics")).json()
    assert metadata["run_schema_version"] == 5
    assert metadata["opportunity_count"] == 4
    assert metadata["spatial_attention"] is True
    assert summary["scenario_sha256"] == scenario.fingerprint
    assert summary["counts"]["opportunity_count"] == 4
    assert opportunities["total"] == 2
    assert [item["agent_id"] for item in opportunities["items"]] == [
        "person-001",
        "person-002",
    ]
    assert stored.attention_evaluation is not None
    assert attention_summary["attention_model_id"] == "spatial-attention-v1"
    assert attention_summary["counts"] == stored.attention_evaluation.counts.model_dump(mode="json")
    assert attention_events["items"] == [
        event.model_dump(mode="json")
        for event in stored.attention_evaluation.events
        if event.model_minute == 0
    ]
    assert metrics["claim_scope"] == "synthetic-metrics-not-observed-outcomes"
    assert metrics["overall"]["opportunity_count"]["value"] == 4.0
    assert before == {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }
