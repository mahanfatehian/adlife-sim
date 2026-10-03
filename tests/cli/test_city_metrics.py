from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.city.analysis import metrics_for_stored_city_run
from adlife.city.runs import create_city_run
from adlife.cli.app import app
from tests.unit.city.test_city_analysis import _artifact_bytes, _spatial_run
from tests.unit.city.test_city_pack import load_pack, pack_data


def test_city_metrics_emits_exact_machine_document_and_is_read_only(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "study")
    before = _artifact_bytes(stored.directory)
    expected = metrics_for_stored_city_run(stored).model_dump(mode="json")

    result = CliRunner().invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "study"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == expected
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_v6_json_preserves_schema_and_attention_only_receipts(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "response-study"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert set(document) == {
        "schema_version",
        "model_id",
        "claim_scope",
        "source_run_schema_version",
        "opportunity_model_id",
        "attention_model_id",
        "scenario_sha256",
        "city_sha256",
        "agents_sha256",
        "trace_sha256",
        "opportunity_structure_sha256",
        "seed",
        "population_size",
        "days",
        "overall",
        "channels",
    }
    assert document["source_run_schema_version"] == 6
    assert document["claim_scope"] == "synthetic-metrics-not-observed-outcomes"
    assert document["overall"]["opportunity_count"]["numerator"] == 4
    assert document["overall"]["impression_count"]["numerator"] == 4
    assert document["overall"]["noticed_count"]["numerator"] == 1

    metric_names = {
        "opportunity_count",
        "impression_count",
        "noticed_count",
        "opportunity_reach",
        "impression_reach",
        "noticed_reach",
        "impression_frequency",
        "notice_rate",
    }
    artifacts = {
        artifact
        for series in (document["overall"], *document["channels"])
        for name in metric_names
        for artifact in series[name]["source_artifacts"]
    }
    assert artifacts == {
        "outputs/spatial-opportunities.jsonl",
        "outputs/spatial-attention.jsonl",
    }
    assert all(
        set(series) == {"schema_version", "channel"} | metric_names
        for series in (document["overall"], *document["channels"])
    )
    assert stored.response_evaluation is not None
    assert "_lines" not in document
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_human_output_leads_with_claim_and_receipts(tmp_path: Path) -> None:
    _spatial_run(tmp_path, "study")

    result = CliRunner().invoke(app, ["city-metrics", str(tmp_path), "study"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "synthetic metrics, not observed outcomes: study",
        "overall: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
        "roadside: opportunities 0/1=0; impressions 0/1=0; noticed 0/1=0; "
        "opportunity reach 0/2=0; impression frequency 0/0=0; notice rate 0/0=0",
        "mobile: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
    ]
    assert result.stderr == ""


def test_city_metrics_v6_human_output_stays_attention_only_and_read_only(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(app, ["city-metrics", str(tmp_path), "response-study"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "synthetic metrics, not observed outcomes: response-study",
        "overall: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
        "roadside: opportunities 0/1=0; impressions 0/1=0; noticed 0/1=0; "
        "opportunity reach 0/2=0; impression frequency 0/0=0; notice rate 0/0=0",
        "mobile: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
    ]
    assert "response" not in result.stdout.lower().replace("response-study", "")
    assert "purchase" not in result.stdout.lower()
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_maps_invalid_missing_legacy_and_corrupt_runs(tmp_path: Path) -> None:
    runner = CliRunner()
    invalid = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "../escape"],
    )
    assert invalid.exit_code == 2
    assert json.loads(invalid.stdout)["error"]["exit_code"] == 2
    assert "Traceback" not in invalid.output

    missing = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "missing"],
    )
    assert missing.exit_code == 4
    assert json.loads(missing.stdout)["error"]["exit_code"] == 4

    create_city_run(
        load_pack(pack_data()),
        root=tmp_path,
        run_id="legacy",
        seed=42,
        agent_count=2,
        days=1,
    )
    legacy = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "legacy"],
    )
    assert legacy.exit_code == 4
    assert "schema-v5" in json.loads(legacy.stdout)["error"]["message"]

    stored = _spatial_run(tmp_path, "corrupt")
    (stored.directory / "run.json").write_text("{}", encoding="utf-8")
    corrupt = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "corrupt"],
    )
    assert corrupt.exit_code == 4
    assert json.loads(corrupt.stdout)["error"]["exit_code"] == 4
    assert "Traceback" not in corrupt.output
