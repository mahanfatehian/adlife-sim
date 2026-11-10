"""Schema-v10 studies statistic the rule baseline and only classify cognition."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from adlife.city.run_store import CityRunStore
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import SpatialStudyDefinition, SpatialStudyPair
from adlife.core.ports.run_store import SchemaVersionMismatch


def _module():
    return importlib.import_module("adlife.core.experiments.spatial_study_v2")


def _save_pair(root: Path, seed: int, mode: str, run_id: str) -> None:
    import shutil

    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    def mutation(document, *, seed=seed, run_id=run_id):
        document["settings"].update(seed=seed, run_id=run_id)

    stage = root / f"stage-{run_id}" if mode == "local" else root
    options = {"mode": mode, "agents": 2, "days": 2, "run_id": run_id, "mutation": mutation}
    if mode == "local":
        options["source"] = "provider"
    _saved_v10(stage, **options)
    if mode == "local":
        destination = root / "city-runs" / run_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage / "city-runs" / run_id), str(destination))


def _definition() -> SpatialStudyDefinition:
    return SpatialStudyDefinition(
        study_id="v10-aa",
        design="a-a",
        analysis_scope="attention-and-response",
        pairs=(
            SpatialStudyPair(seed=0, control_run_id="rules-0", treatment_run_id="provider-0"),
            SpatialStudyPair(seed=1, control_run_id="rules-1", treatment_run_id="provider-1"),
        ),
    )


def test_v10_study_statistics_use_only_rule_baseline(tmp_path: Path) -> None:
    from adlife.city.studies import analyze_stored_spatial_study

    for seed in (0, 1):
        _save_pair(tmp_path, seed, "rules", f"rules-{seed}")
        _save_pair(tmp_path, seed, "local", f"provider-{seed}")

    result = analyze_stored_spatial_study(CityRunStore(tmp_path), _definition())

    assert result.model_id == "spatial-paired-study-v2"
    assert result.a_a_status == "rule-baseline-exact-zero-verified"
    assert all(item.mean_paired_difference == 0.0 for item in result.statistics)
    assert all("cognition" not in item.metric_key for item in result.statistics)
    assert result.pairs[0].cognition_provenance_classification == (
        "incompatible-provider-provenance"
    )
    dumped = result.model_dump(mode="json")
    assert "delta" not in dumped["pairs"][0]["cognition_provenance_classification"]
    assert "purchase" not in canonical_json(result.pairs[0].control.cognition)

    from typer.testing import CliRunner

    from adlife.cli.app import app

    study_path = tmp_path / "study.json"
    study_path.write_text(canonical_json(_definition()), encoding="utf-8")
    human = CliRunner().invoke(app, ["city-study", str(tmp_path), str(study_path)])
    assert human.exit_code == 0, human.output
    text = human.stdout.lower()
    assert "not a campaign effect" in text
    assert "rule baseline statistics" in text
    assert "traceback" not in text
    machine = CliRunner().invoke(
        app, ["--format", "json", "city-study", str(tmp_path), str(study_path)]
    )
    assert machine.exit_code == 0, machine.output
    assert machine.stdout.count("\n") == 1


def test_v10_study_refuses_a_mixed_schema_before_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adlife.city import studies
    from tests.unit.city.test_city_analysis import _spatial_run

    _save_pair(tmp_path, 0, "rules", "rules-0")
    _spatial_run(tmp_path, "legacy-1", seed=1, response=True)
    definition = SpatialStudyDefinition(
        study_id="mixed",
        design="a-a",
        analysis_scope="attention-and-response",
        pairs=(
            SpatialStudyPair(seed=0, control_run_id="rules-0", treatment_run_id="rules-0"),
            SpatialStudyPair(seed=1, control_run_id="legacy-1", treatment_run_id="legacy-1"),
        ),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a mixed study projected a run")

    monkeypatch.setattr(studies, "_load_v10_projection", forbidden)
    monkeypatch.setattr(studies, "_load_projection", forbidden)
    with pytest.raises(SchemaVersionMismatch, match="cannot mix a schema-v10"):
        studies.analyze_stored_spatial_study(CityRunStore(tmp_path), definition)
