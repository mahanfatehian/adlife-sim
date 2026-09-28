import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.city.runs import create_city_run
from adlife.cli.app import app
from tests.unit.city.test_city_pack import load_pack, pack_data


def test_city_replay_reports_verified_trace_in_json(tmp_path: Path) -> None:
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="study", seed=42, agent_count=2, days=1
    )
    result = CliRunner().invoke(app, ["--format", "json", "city-replay", str(tmp_path), "study"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["identical"] is True
    assert document["trace_sha256"] == stored.manifest.trace_sha256
    assert "_lines" not in document


def test_city_replay_refuses_missing_or_corrupt_source_cleanly(tmp_path: Path) -> None:
    missing = CliRunner().invoke(app, ["--format", "json", "city-replay", str(tmp_path), "missing"])
    assert missing.exit_code == 4
    assert json.loads(missing.stdout)["error"]["exit_code"] == 4
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="study", seed=42, agent_count=2, days=1
    )
    (stored.directory / "run.json").write_text("{}", encoding="utf-8")
    corrupt = CliRunner().invoke(app, ["--format", "json", "city-replay", str(tmp_path), "study"])
    assert corrupt.exit_code == 4
    assert json.loads(corrupt.stdout)["error"]["exit_code"] == 4
    assert "Traceback" not in corrupt.output


def test_city_replay_refuses_invalid_identifier_as_input_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["--format", "json", "city-replay", str(tmp_path), "../escape"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["exit_code"] == 2
