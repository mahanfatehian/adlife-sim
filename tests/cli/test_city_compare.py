from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.city.analysis import compare_stored_city_runs
from adlife.city.runs import create_city_run
from adlife.cli.app import app
from tests.unit.city.test_city_analysis import _artifact_bytes, _spatial_run
from tests.unit.city.test_city_pack import load_pack, pack_data


def test_city_compare_json_is_exact_confounded_and_read_only(tmp_path: Path) -> None:
    control = _spatial_run(tmp_path, "control", end=2)
    treatment = _spatial_run(tmp_path, "treatment", end=3)
    before_control = _artifact_bytes(control.directory)
    before_treatment = _artifact_bytes(treatment.directory)
    expected = compare_stored_city_runs(control, treatment).model_dump(mode="json")

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-compare",
            str(tmp_path),
            "control",
            "treatment",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document == expected
    assert document["claim_scope"] == "synthetic-comparison-not-causal-or-observed-effect"
    assert document["classification"] == "opportunity-confounded"
    assert document["overall"]["opportunity_count"] == 2.0
    assert document["control"]["overall"]["opportunity_count"]["value"] == 4.0
    assert document["treatment"]["overall"]["opportunity_count"]["value"] == 6.0
    assert "_lines" not in document
    assert result.stderr == ""
    assert _artifact_bytes(control.directory) == before_control
    assert _artifact_bytes(treatment.directory) == before_treatment


def test_city_compare_v6_json_is_exact_aa_zero_attention_only_and_read_only(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-compare",
            str(tmp_path),
            "response-study",
            "response-study",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["claim_scope"] == "synthetic-comparison-not-causal-or-observed-effect"
    assert document["classification"] == "matched-opportunity-structure"
    assert document["control"]["source_run_schema_version"] == 6
    assert document["treatment"]["source_run_schema_version"] == 6
    zero_deltas = {
        "opportunity_count": 0.0,
        "impression_count": 0.0,
        "noticed_count": 0.0,
        "opportunity_reach": 0.0,
        "impression_reach": 0.0,
        "noticed_reach": 0.0,
        "impression_frequency": 0.0,
        "notice_rate": 0.0,
    }
    assert document["overall"] == {
        "schema_version": 1,
        "channel": "overall",
        **zero_deltas,
    }
    assert document["channels"] == [
        {"schema_version": 1, "channel": "roadside", **zero_deltas},
        {"schema_version": 1, "channel": "mobile", **zero_deltas},
    ]

    metric_names = set(zero_deltas)
    for arm in (document["control"], document["treatment"]):
        assert all(
            set(series) == {"schema_version", "channel"} | metric_names
            for series in (arm["overall"], *arm["channels"])
        )
        artifacts = {
            artifact
            for series in (arm["overall"], *arm["channels"])
            for name in metric_names
            for artifact in series[name]["source_artifacts"]
        }
        assert artifacts == {
            "outputs/spatial-opportunities.jsonl",
            "outputs/spatial-attention.jsonl",
        }
    assert "_lines" not in document
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_compare_same_run_is_exact_aa_zero_in_human_output(tmp_path: Path) -> None:
    _spatial_run(tmp_path, "study")

    result = CliRunner().invoke(
        app,
        ["city-compare", str(tmp_path), "study", "study"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "synthetic comparison, not a causal or observed-world result: study -> study",
        "classification: matched-opportunity-structure",
        "Normalized model opportunity structure is identical; deltas remain synthetic "
        "and non-causal.",
        "overall deltas: opportunities +0; impressions +0; noticed +0; "
        "opportunity reach +0; impression frequency +0; notice rate +0",
        "roadside deltas: opportunities +0; impressions +0; noticed +0; "
        "opportunity reach +0; impression frequency +0; notice rate +0",
        "mobile deltas: opportunities +0; impressions +0; noticed +0; "
        "opportunity reach +0; impression frequency +0; notice rate +0",
    ]


def test_city_compare_v6_human_aa_output_is_clean_and_read_only(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        ["city-compare", str(tmp_path), "response-study", "response-study"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "synthetic comparison, not a causal or observed-world result: "
        "response-study -> response-study",
        "classification: matched-opportunity-structure",
        "Normalized model opportunity structure is identical; deltas remain synthetic "
        "and non-causal.",
        "overall deltas: opportunities +0; impressions +0; noticed +0; "
        "opportunity reach +0; impression frequency +0; notice rate +0",
        "roadside deltas: opportunities +0; impressions +0; noticed +0; "
        "opportunity reach +0; impression frequency +0; notice rate +0",
        "mobile deltas: opportunities +0; impressions +0; noticed +0; "
        "opportunity reach +0; impression frequency +0; notice rate +0",
    ]
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_compare_maps_incompatible_pair_to_input_error(tmp_path: Path) -> None:
    _spatial_run(tmp_path, "control", seed=42)
    _spatial_run(tmp_path, "treatment", seed=43)

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-compare",
            str(tmp_path),
            "control",
            "treatment",
        ],
    )

    assert result.exit_code == 2
    error = json.loads(result.stdout)["error"]
    assert error["exit_code"] == 2
    assert "mobility trace" in error["message"] or "seed" in error["message"]
    assert "Traceback" not in result.output


def test_city_compare_maps_invalid_missing_legacy_and_corrupt_runs(tmp_path: Path) -> None:
    runner = CliRunner()
    invalid = runner.invoke(
        app,
        [
            "--format",
            "json",
            "city-compare",
            str(tmp_path),
            "../escape",
            "safe",
        ],
    )
    assert invalid.exit_code == 2

    missing = runner.invoke(
        app,
        [
            "--format",
            "json",
            "city-compare",
            str(tmp_path),
            "missing",
            "also-missing",
        ],
    )
    assert missing.exit_code == 4

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
        ["--format", "json", "city-compare", str(tmp_path), "legacy", "legacy"],
    )
    assert legacy.exit_code == 4

    stored = _spatial_run(tmp_path, "corrupt")
    (stored.directory / "run.json").write_text("{}", encoding="utf-8")
    corrupt = runner.invoke(
        app,
        ["--format", "json", "city-compare", str(tmp_path), "corrupt", "corrupt"],
    )
    assert corrupt.exit_code == 4
    assert "Traceback" not in corrupt.output
