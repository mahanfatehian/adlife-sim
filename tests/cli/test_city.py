import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app
from tests.unit.city.test_city_pack import pack_data


def test_city_command_starts_loopback_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    calls: list[dict[str, object]] = []

    def fake_run(application: object, **kwargs: object) -> None:
        assert application is not None
        calls.append(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(app, ["city", "--agents", "2", "--days", "1", "--port", "8765"])
    assert result.exit_code == 0, result.output
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8765


def test_city_refuses_invalid_agent_count() -> None:
    result = CliRunner().invoke(app, ["city", "--agents", "0"])
    assert result.exit_code == 2


def test_city_refuses_malformed_pack_without_echoing_it(tmp_path: Path) -> None:
    pack = tmp_path / "city.json"
    pack.write_text('{"api_key":"topsecret123"', encoding="utf-8")
    result = CliRunner().invoke(app, ["city", "--pack", str(pack)])
    assert result.exit_code == 2
    assert "topsecret123" not in result.output
    assert "Traceback" not in result.output


def test_city_refuses_json_mode_cleanly() -> None:
    result = CliRunner().invoke(app, ["--format", "json", "city"])
    assert result.exit_code == 2
    assert '"exit_code": 2' in result.stdout


def test_city_refuses_unschedulable_route_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    data = pack_data()
    data["nodes"] = [
        {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
        {"node_id": "b", "longitude": 0.0, "latitude": 2.0},
    ]
    data["roads"] = [
        {"road_id": "ab", "source_node": "a", "target_node": "b", "kind": "residential"}
    ]
    pack = tmp_path / "city.json"
    pack.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    result = CliRunner().invoke(app, ["city", "--pack", str(pack), "--agents", "1"])
    assert result.exit_code == 2
    assert "trip cannot finish before midnight" in result.output
    assert "Traceback" not in result.output


def test_city_can_select_verified_offline_catalog_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    import uvicorn

    calls: list[object] = []

    def fake_run(application: object, **kwargs: object) -> None:
        calls.append(application)

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(socket, "create_connection", blocked)
    result = CliRunner().invoke(
        app, ["city", "--city-id", "fictional-grid-v2", "--agents", "2", "--days", "1"]
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert "Fictional Grid City V2" in result.output


def test_city_refuses_ambiguous_or_unknown_catalog_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    pack = tmp_path / "city.json"
    pack.write_text(json.dumps(pack_data()), encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(args))
    both = CliRunner().invoke(app, ["city", "--pack", str(pack), "--city-id", "fictional-grid-v2"])
    assert both.exit_code == 2
    unknown = CliRunner().invoke(app, ["city", "--city-id", "unknown-city"])
    assert unknown.exit_code == 2
    assert "Traceback" not in both.output + unknown.output
    assert calls == []
