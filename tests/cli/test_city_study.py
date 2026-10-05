from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from adlife.cli.app import app
from adlife.core.domain.serialization import canonical_json
from tests.integration.test_city_spatial_study import analyze, definition, make_runs
from tests.unit.city.test_city_analysis import _artifact_bytes


def study_file(root: Path, *, response: bool = False) -> Path:
    path = root / "study.json"
    path.write_text(canonical_json(definition(response=response)), encoding="utf-8")
    return path


@pytest.mark.parametrize("response", [False, True])
def test_city_study_exact_json_document_and_read_only_sources(tmp_path, response):
    make_runs(tmp_path, response=response)
    path = study_file(tmp_path, response=response)
    before = _artifact_bytes(tmp_path)
    expected = analyze(tmp_path, definition(response=response)).model_dump(mode="json")
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-study", str(tmp_path), str(path)]
    )
    assert invocation.exit_code == 0, invocation.output
    assert json.loads(invocation.stdout) == expected
    assert invocation.stdout.count("\n") == 1
    assert invocation.stderr == ""
    assert _artifact_bytes(tmp_path) == before


def test_city_study_exact_human_disclosures_precede_all_statistics(tmp_path):
    make_runs(tmp_path)
    path = study_file(tmp_path)
    invocation = CliRunner().invoke(app, ["city-study", str(tmp_path), str(path)])
    assert invocation.exit_code == 0, invocation.output
    names = [
        "impression_count",
        "impression_frequency",
        "impression_reach",
        "notice_rate",
        "noticed_count",
        "noticed_reach",
        "opportunity_count",
        "opportunity_reach",
    ]
    keys = [
        f"attention.{scope}.{name}"
        for scope in ("channel.mobile", "channel.roadside", "overall")
        for name in names
    ]
    assert (
        invocation.stdout
        == "\n".join(
            [
                "synthetic spatial study, not observed or causal effects: repeated-study",
                "scope: attention; seeds: 2; tier: exploratory-under-50-seeds",
                "opportunity: matched-opportunity-structure (matched 2, confounded 0)",
                "response assumptions: not-applicable (matched 0, confounded 0); "
                "A/A: exact-zero-verified",
                "Intervals describe deterministic simulator seed variation; "
                "not population confidence or sales.",
                *(
                    f"{key}: mean +0; sample SD 0; median +0; 95% bootstrap [+0, +0]; "
                    "standardized difference null; agreement 1; stable-null"
                    for key in keys
                ),
            ]
        )
        + "\n"
    )
    assert invocation.stderr == ""


@pytest.mark.parametrize(
    "kind,code,message",
    [
        ("definition", 2, "spatial study definition is invalid"),
        ("scientific", 2, "spatial study inputs are incompatible"),
        ("artifact", 4, "city run artifacts could not be verified"),
        ("defect", 1, "spatial study analysis failed"),
        ("interrupt", 130, "run interrupted"),
    ],
)
def test_city_study_safe_exit_contract(tmp_path, monkeypatch, kind, code, message):
    from adlife.cli.commands import city_study
    from adlife.core.experiments.spatial_study import SpatialStudyCompatibilityError
    from adlife.core.ports.run_store import CorruptRunArtifact

    path = tmp_path / "study.json"
    path.write_text(
        "{}" if kind == "definition" else canonical_json(definition()), encoding="utf-8"
    )
    failures = {
        "scientific": SpatialStudyCompatibilityError,
        "artifact": CorruptRunArtifact,
        "defect": RuntimeError,
        "interrupt": KeyboardInterrupt,
    }
    if kind != "definition":

        def fail(*args, **kwargs):
            raise failures[kind](f"private-source-content at {tmp_path}")

        monkeypatch.setattr(city_study, "analyze_stored_spatial_study", fail)
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-study", str(tmp_path), str(path)]
    )
    assert invocation.exit_code == code
    assert json.loads(invocation.stdout) == {
        "error": {
            "exit_code": code,
            "type": "KeyboardInterrupt" if kind == "interrupt" else "CommandError",
            "message": message,
        }
    }
    assert invocation.stderr == f"error: {message}\n"
    assert "private-source-content" not in invocation.output
    assert str(tmp_path) not in invocation.output


def test_city_study_unexpected_definition_loader_failure_is_generic(tmp_path, monkeypatch):
    from adlife.cli.commands import city_study

    def fail(path):
        raise RuntimeError(f"private-loader-content at {tmp_path}")

    monkeypatch.setattr(city_study, "load_spatial_study_definition", fail)
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-study", str(tmp_path), "study.json"]
    )
    assert invocation.exit_code == 1
    assert json.loads(invocation.stdout) == {
        "error": {
            "exit_code": 1,
            "type": "CommandError",
            "message": "spatial study analysis failed",
        }
    }
    assert invocation.stderr == "error: spatial study analysis failed\n"
    assert "private-loader-content" not in invocation.output


@pytest.mark.parametrize("kind", ["missing", "corrupt", "unsupported", "aa-invariant"])
def test_city_study_missing_corrupt_and_unsupported_runs_exit_four(tmp_path, kind):
    response = kind == "unsupported"
    if kind in {"corrupt", "unsupported"}:
        make_runs(tmp_path)
    if kind == "corrupt":
        path = tmp_path / "city-runs" / "control-0" / "run.json"
        path.write_bytes(b"{}")
    if kind == "aa-invariant":
        make_runs(tmp_path, contrast=True, same=False)
        path = tmp_path / "study.json"
        path.write_text(canonical_json(definition(same=False)), encoding="utf-8")
    else:
        path = study_file(tmp_path, response=response)
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-study", str(tmp_path), str(path)]
    )
    assert invocation.exit_code == 4
    assert (
        json.loads(invocation.stdout)["error"]["message"]
        == "city run artifacts could not be verified"
    )


def test_response_human_output_carries_rule_state_and_proxy_disclosures(tmp_path):
    make_runs(tmp_path, response=True)
    path = study_file(tmp_path, response=True)
    invocation = CliRunner().invoke(app, ["city-study", str(tmp_path), str(path)])
    assert invocation.exit_code == 0
    text = invocation.stdout
    assert "Every notice mechanically produces one rule response" in text
    assert "purchase intention is an uncalibrated internal proxy" in text
    assert "directional stability is not a clean creative effect" in text
    assert text.index("A/A: exact-zero-verified") < text.index("attention.channel.")
    assert "response.overall.state.purchase_intention_proxy.mean_change: mean +0" in text


def test_city_study_refuses_tampered_analyzer_return_before_output(tmp_path, monkeypatch):
    from adlife.cli.commands import city_study

    make_runs(tmp_path)
    path = study_file(tmp_path)
    result = analyze(tmp_path, definition())
    bad = BaseModel.model_copy(result, update={"opportunity_matched_pair_count": 0})
    monkeypatch.setattr(city_study, "analyze_stored_spatial_study", lambda *args: bad)
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-study", str(tmp_path), str(path)]
    )
    assert invocation.exit_code == 2
    assert "statistics" not in json.loads(invocation.stdout)


@pytest.mark.parametrize("option", ["--output", "--discover", "--force"])
def test_city_study_accepts_no_discovery_or_output_option(tmp_path, option):
    invocation = CliRunner().invoke(
        app, ["--format", "json", "city-study", str(tmp_path), "study.json", option]
    )
    assert invocation.exit_code == 2
    assert json.loads(invocation.stdout)["error"]["exit_code"] == 2
    assert "No such option" in json.loads(invocation.stdout)["error"]["message"]
