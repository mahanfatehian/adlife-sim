from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.city import analysis as city_analysis
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


def test_city_metrics_default_and_explicit_attention_layers_have_identical_output(
    tmp_path: Path,
) -> None:
    _spatial_run(tmp_path, "response-study", response=True)
    runner = CliRunner()

    default = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "response-study"],
    )
    explicit = runner.invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "response-study",
            "--layer",
            "attention",
        ],
    )

    assert default.exit_code == explicit.exit_code == 0
    assert (explicit.stdout, explicit.stderr) == (default.stdout, default.stderr)


def test_city_metrics_response_layer_emits_exact_machine_document_and_is_read_only(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)
    helper = getattr(city_analysis, "response_metrics_for_stored_city_run", None)
    assert callable(helper), "stored schema-v6 response metric projection is not implemented"
    expected = helper(stored).model_dump(mode="json")

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "response-study",
            "--layer",
            "response",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document == expected
    assert document["model_id"] == "spatial-response-metrics-v1"
    assert document["claim_scope"] == "synthetic-response-metrics-not-observed-outcomes"
    assert document["source_run_schema_version"] == 6
    assert document["overall"]["response_count"]["numerator"] == 1
    assert document["overall"]["purchase_intention_proxy"]["denominator"] == 2
    assert document["channels"][0]["channel"] == "roadside"
    assert document["channels"][1]["channel"] == "mobile"
    assert document["campaigns"][0]["campaign_id"] == "fictional-launch"
    assert "_lines" not in document
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_response_layer_human_output_exposes_event_and_state_metrics(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        ["city-metrics", str(tmp_path), "response-study", "--layer", "response"],
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "synthetic response metrics, not observed outcomes: response-study"
    assert any(line.startswith("overall:") for line in lines)
    assert any(line.startswith("roadside:") for line in lines)
    assert any(line.startswith("mobile:") for line in lines)
    assert any(line.startswith("fictional-launch:") for line in lines)
    normalized = result.stdout.lower()
    for label in (
        "responses",
        "response reach",
        "response frequency",
        "rule sentiment delta",
        "rule recall delta",
        "brand sentiment",
        "recall strength",
        "purchase intention proxy",
    ):
        assert label in normalized
    assert "purchase probability" not in normalized
    assert "sales" not in normalized
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_response_layer_requires_schema_v6(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "attention-study")
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "attention-study",
            "--layer",
            "response",
        ],
    )

    assert result.exit_code == 4
    assert "schema-v6" in json.loads(result.stdout)["error"]["message"]
    assert "Traceback" not in result.output
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
