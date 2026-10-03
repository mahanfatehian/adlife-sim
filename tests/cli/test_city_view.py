"""The saved-city viewer opens only a validated artifact on loopback."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from typer.testing import CliRunner

from adlife.city import analysis as city_analysis
from adlife.city.runs import create_city_run
from adlife.cli.app import app
from adlife.core.domain.city_run import CityRunManifestV6
from adlife.core.simulation.spatial_response import summarize_spatial_response_artifact
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario
from tests.unit.city.test_spatial_response import _response_input


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
        response_metrics = await web.get("/api/spatial-response-metrics")
        other = await web.get("/api/run?run_id=another")
        path = await web.get("/api/frame?minute=480&path=../run.json")
    assert metadata.json()["run_id"] == "saved-study"
    assert metadata.json()["saved"] is True
    assert frame.json() == stored.mobility.frame_document(480)
    assert response_metrics.status_code == 404
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


def test_city_view_does_not_announce_url_before_application_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_city_run(
        load_pack(pack_data()),
        root=tmp_path,
        run_id="saved-study",
        seed=42,
        agent_count=2,
        days=1,
    )
    calls: list[object] = []

    def reject_application(*args: object, **kwargs: object) -> FastAPI:
        raise ValueError("viewer evidence failed validation")

    monkeypatch.setattr(
        "adlife.cli.commands.city_view.create_city_app",
        reject_application,
    )
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(args))

    result = CliRunner().invoke(app, ["city-view", str(tmp_path), "saved-study"])

    assert result.exit_code == 2
    assert "http://127.0.0.1" not in result.output
    assert "viewer evidence failed validation" in result.output
    assert calls == []


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
        response_metrics = await web.get("/api/spatial-response-metrics")
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
    assert response_metrics.status_code == 404
    assert before == {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.asyncio
async def test_city_view_passes_verified_v6_response_evidence_and_metrics_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    response_input = _response_input(
        scenario,
        agent_ids=("person-001", "person-002"),
    )
    stored = create_city_run(
        pack,
        root=tmp_path,
        run_id="response-study",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
        spatial_response=response_input,
    )
    assert isinstance(stored.manifest, CityRunManifestV6)
    assert stored.response_evaluation is not None
    response_metrics_helper = getattr(
        city_analysis,
        "response_metrics_for_stored_city_run",
        None,
    )
    assert callable(response_metrics_helper), (
        "stored schema-v6 response metric projection is not implemented"
    )
    expected_response_metrics = response_metrics_helper(stored).model_dump(mode="json")
    before = {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }
    calls: list[tuple[FastAPI, dict[str, object]]] = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda application, **kwargs: calls.append((application, kwargs)),
    )

    result = CliRunner().invoke(
        app,
        ["city-view", str(tmp_path), "response-study", "--port", "8767"],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    application, options = calls[0]
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 8767
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://city.test",
    ) as web:
        metadata = (await web.get("/api/meta")).json()
        summary = await web.get("/api/response-summary")
        events = await web.get("/api/response-events?minute=0")
        final_state = await web.get("/api/response-state")
        metrics = await web.get("/api/spatial-metrics")
        response_metrics = await web.get("/api/spatial-response-metrics")
        response_metrics_write = await web.post("/api/spatial-response-metrics", json={})

    evaluation = stored.response_evaluation
    assert metadata["run_schema_version"] == stored.manifest.schema_version == 6
    assert metadata["spatial_response"] is True
    assert metadata["response_model_id"] == "spatial-response-v1"
    assert metadata["response_claim_scope"] == "synthetic-response-not-observed-behavior"
    assert metadata["response_count"] == stored.manifest.response_count == 1
    assert metadata["state_update_count"] == stored.manifest.state_update_count == 1
    assert metadata["final_state_count"] == stored.manifest.final_state_count == 2
    assert summary.status_code == 200
    assert summary.json() == summarize_spatial_response_artifact(evaluation).model_dump(mode="json")
    assert events.status_code == 200
    assert events.json()["response_model_id"] == "spatial-response-v1"
    assert events.json()["claim_scope"] == "synthetic-response-not-observed-behavior"
    assert events.json()["total"] == 2
    assert events.json()["event_type_counts"] == {
        "spatial.response": 1,
        "spatial.state-updated": 1,
    }
    assert events.json()["agent_counts"] == {"person-002": 2}
    assert events.json()["channel_counts"] == {
        "roadside-billboard": 0,
        "mobile-feed": 1,
    }
    assert events.json()["items"] == [
        record.model_dump(mode="json") for record in evaluation.records if record.model_minute == 0
    ]
    assert final_state.status_code == 200
    assert final_state.json()["model_id"] == "spatial-response-state-v1"
    assert final_state.json()["state_scope"] == "final-end-of-run-not-scrubbed-minute"
    assert final_state.json()["total"] == 2
    assert final_state.json()["items"] == [
        state.model_dump(mode="json") for state in evaluation.final_states
    ]
    assert metrics.status_code == 200
    assert metrics.json()["source_run_schema_version"] == 6
    assert metrics.json()["claim_scope"] == "synthetic-metrics-not-observed-outcomes"
    assert response_metrics.status_code == 200
    assert response_metrics.json() == expected_response_metrics
    assert response_metrics_write.status_code == 405
    assert "Traceback" not in result.output
    assert before == {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }


def test_city_view_refuses_corrupt_v6_response_before_server_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        run_id="response-study",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
        spatial_response=_response_input(
            scenario,
            agent_ids=("person-001", "person-002"),
        ),
    )
    summary_path = stored.directory / "outputs" / "response-summary.json"
    summary_path.write_bytes(summary_path.read_bytes() + b" ")
    before_attempt = {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }
    calls: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(args))

    result = CliRunner().invoke(app, ["city-view", str(tmp_path), "response-study"])

    assert result.exit_code == 4
    assert calls == []
    assert "http://127.0.0.1" not in result.output
    assert "Traceback" not in result.output
    assert before_attempt == {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }
