import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.city.runs import create_city_run
from adlife.cli.app import app
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario


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


def test_city_replay_reports_verified_spatial_provenance(tmp_path: Path) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=1,
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

    result = CliRunner().invoke(
        app,
        ["--format", "json", "city-replay", str(tmp_path), "spatial-study"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["scenario_sha256"] == scenario.fingerprint
    assert document["opportunity_stream_sha256"] == (stored.manifest.opportunity_stream_sha256)
    assert document["opportunity_summary_sha256"] == (stored.manifest.opportunity_summary_sha256)
    assert document["opportunity_stream_bytes"] == stored.manifest.opportunity_stream_bytes
    assert document["opportunity_count"] == stored.manifest.opportunity_count == 2
    assert document["opportunity_counts"]["phone_eligible_agent_minute_count"] == 4
    assert document["claim_scope"] == "synthetic-opportunity-not-impression"
