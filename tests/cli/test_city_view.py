"""The saved-city viewer opens only a validated artifact on loopback."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from typer.testing import CliRunner

from adlife.city.runs import create_city_run
from adlife.cli.app import app
from tests.unit.city.test_city_pack import load_pack, pack_data


@pytest.mark.asyncio
async def test_city_view_loads_saved_run_before_binding_and_serves_exact_core_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="saved-study", seed=42, agent_count=2, days=1
    )
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
