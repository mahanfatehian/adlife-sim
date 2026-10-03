from __future__ import annotations

from pathlib import Path

import pytest

from adlife.city.analysis import compare_stored_city_runs, metrics_for_stored_city_run
from adlife.city.runs import create_city_run
from adlife.core.domain.city_run import CityRunManifestV5
from adlife.core.experiments.spatial_comparison import SpatialComparisonError
from adlife.core.ports.run_store import CorruptRunArtifact, SchemaVersionMismatch
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario


def _spatial_run(
    root: Path,
    run_id: str,
    *,
    start: int = 0,
    end: int = 2,
    seed: int = 42,
):
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": start, "end_minute": end}],
                probability=1.0,
                cap=end - start,
            )
        ],
    )
    return create_city_run(
        pack,
        root=root,
        run_id=run_id,
        seed=seed,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
    )


def _artifact_bytes(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def test_metrics_project_verified_v5_run_without_mutating_artifacts(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "study")
    before = _artifact_bytes(stored.directory)

    metrics = metrics_for_stored_city_run(stored)

    assert isinstance(stored.manifest, CityRunManifestV5)
    assert metrics.source_run_schema_version == 5
    assert metrics.scenario_sha256 == stored.manifest.scenario_sha256
    assert metrics.city_sha256 == stored.manifest.city_sha256
    assert metrics.agents_sha256 == stored.manifest.agents_sha256
    assert metrics.trace_sha256 == stored.manifest.trace_sha256
    assert metrics.seed == stored.manifest.seed
    assert metrics.population_size == stored.manifest.agent_count == 2
    assert metrics.days == stored.manifest.days
    assert metrics.overall.opportunity_count.value == 4.0
    assert _artifact_bytes(stored.directory) == before


def test_metrics_refuse_legacy_run_and_incomplete_v5_evidence(tmp_path: Path) -> None:
    pack = load_pack(pack_data())
    legacy = create_city_run(
        pack,
        root=tmp_path,
        run_id="legacy",
        seed=42,
        agent_count=2,
        days=1,
    )
    with pytest.raises(SchemaVersionMismatch, match="schema-v5"):
        metrics_for_stored_city_run(legacy)

    stored = _spatial_run(tmp_path, "study")
    incomplete = type(stored)(
        stored.manifest,
        stored.pack,
        stored.mobility,
        stored.directory,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=None,
    )
    with pytest.raises(CorruptRunArtifact, match="incomplete"):
        metrics_for_stored_city_run(incomplete)


def test_compare_projects_both_runs_without_mutating_either_source(tmp_path: Path) -> None:
    control = _spatial_run(tmp_path, "control", end=2)
    treatment = _spatial_run(tmp_path, "treatment", end=3)
    before_control = _artifact_bytes(control.directory)
    before_treatment = _artifact_bytes(treatment.directory)

    result = compare_stored_city_runs(control, treatment)

    assert result.control_run_id == "control"
    assert result.treatment_run_id == "treatment"
    assert result.classification == "opportunity-confounded"
    assert result.overall.opportunity_count == 2.0
    assert _artifact_bytes(control.directory) == before_control
    assert _artifact_bytes(treatment.directory) == before_treatment


def test_compare_refuses_valid_runs_with_different_mobility_provenance(tmp_path: Path) -> None:
    control = _spatial_run(tmp_path, "control", seed=42)
    treatment = _spatial_run(tmp_path, "treatment", seed=43)

    with pytest.raises(SpatialComparisonError, match=r"mobility trace|seed"):
        compare_stored_city_runs(control, treatment)
