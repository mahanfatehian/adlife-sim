from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city import analysis as city_analysis
from adlife.city.analysis import compare_stored_city_runs, metrics_for_stored_city_run
from adlife.city.runs import create_city_run
from adlife.core.domain.city_run import CityRunManifestV5, CityRunManifestV6, CityRunManifestV7
from adlife.core.experiments.spatial_comparison import SpatialComparisonError
from adlife.core.ports.run_store import CorruptRunArtifact, SchemaVersionMismatch
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario
from tests.unit.city.test_spatial_response import _response_input


def _spatial_run(
    root: Path,
    run_id: str,
    *,
    start: int = 0,
    end: int = 2,
    seed: int = 42,
    response: bool = False,
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
    response_input = (
        _response_input(
            scenario,
            agent_ids=("person-001", "person-002"),
        )
        if response
        else None
    )
    return create_city_run(
        pack,
        root=root,
        run_id=run_id,
        seed=seed,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
        spatial_response=response_input,
    )


def _artifact_bytes(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _response_metrics_for_stored_city_run(stored):
    helper = getattr(city_analysis, "response_metrics_for_stored_city_run", None)
    assert callable(helper), "stored schema-v6 response metric projection is not implemented"
    return helper(stored)


def _compare_stored_city_response_runs(control, treatment):
    helper = getattr(city_analysis, "compare_stored_city_response_runs", None)
    assert callable(helper), "stored schema-v6 response metric comparison is not implemented"
    return helper(control, treatment)


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


def test_metrics_project_verified_v6_attention_only_without_mutating_artifacts(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    metrics = metrics_for_stored_city_run(stored)

    assert isinstance(stored.manifest, CityRunManifestV6)
    assert metrics.source_run_schema_version == 6
    assert metrics.overall.opportunity_count.value == 4.0
    assert metrics.overall.impression_count.value == 4.0
    assert all(
        "response" not in artifact
        for series in (metrics.overall, *metrics.channels)
        for receipt in (
            series.opportunity_count,
            series.impression_count,
            series.noticed_count,
            series.opportunity_reach,
            series.impression_reach,
            series.noticed_reach,
            series.impression_frequency,
            series.notice_rate,
        )
        for artifact in receipt.source_artifacts
    )
    assert _artifact_bytes(stored.directory) == before


def test_response_metrics_project_verified_v6_run_without_mutating_artifacts(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    metrics = _response_metrics_for_stored_city_run(stored)

    assert isinstance(stored.manifest, CityRunManifestV6)
    assert metrics.model_id == "spatial-response-metrics-v1"
    assert metrics.claim_scope == "synthetic-response-metrics-not-observed-outcomes"
    assert metrics.source_run_schema_version == 6
    assert metrics.scenario_sha256 == stored.manifest.scenario_sha256
    assert metrics.city_sha256 == stored.manifest.city_sha256
    assert metrics.agents_sha256 == stored.manifest.agents_sha256
    assert metrics.trace_sha256 == stored.manifest.trace_sha256
    assert metrics.response_input_sha256 == stored.manifest.response_input_sha256
    assert metrics.response_stream_sha256 == stored.manifest.response_stream_sha256
    assert metrics.response_state_sha256 == stored.manifest.response_state_sha256
    assert metrics.population_size == stored.manifest.agent_count == 2
    assert metrics.days == stored.manifest.days
    assert metrics.campaign_count == stored.manifest.response_campaign_count
    assert metrics.overall.response_count.numerator == stored.manifest.response_count == 1
    assert metrics.overall.purchase_intention_proxy.denominator == stored.manifest.final_state_count
    assert _artifact_bytes(stored.directory) == before


def test_v7_projections_and_comparisons_preserve_exact_source_schema(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "v7-response-study", response=True)
    manifest = CityRunManifestV7.model_validate(
        stored.manifest.model_dump(mode="python")
        | {
            "schema_version": 7,
            "workbench_input_schema_version": 1,
            "workbench_input_sha256": sha256(b"v7-response-study").hexdigest(),
        }
    )
    v7 = replace(stored, manifest=manifest)

    attention = metrics_for_stored_city_run(v7)
    response = _response_metrics_for_stored_city_run(v7)
    attention_comparison = compare_stored_city_runs(v7, v7)
    response_comparison = _compare_stored_city_response_runs(v7, v7)

    assert attention.source_run_schema_version == 7
    assert response.source_run_schema_version == 7
    assert attention_comparison.control.source_run_schema_version == 7
    assert attention_comparison.treatment.source_run_schema_version == 7
    assert response_comparison.control.source_run_schema_version == 7
    assert response_comparison.treatment.source_run_schema_version == 7


def test_response_metrics_refuse_legacy_and_incomplete_v6_evidence(tmp_path: Path) -> None:
    legacy = _spatial_run(tmp_path, "attention-study")
    with pytest.raises(SchemaVersionMismatch, match="schema-v6"):
        _response_metrics_for_stored_city_run(legacy)

    stored = _spatial_run(tmp_path, "response-study", response=True)
    incomplete = type(stored)(
        stored.manifest,
        stored.pack,
        stored.mobility,
        stored.directory,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        response_input=stored.response_input,
        response_evaluation=None,
    )
    with pytest.raises(CorruptRunArtifact, match="incomplete"):
        _response_metrics_for_stored_city_run(incomplete)


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


def test_compare_v6_run_with_itself_is_exact_aa_zero_and_read_only(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = compare_stored_city_runs(stored, stored)

    assert result.control.source_run_schema_version == 6
    assert result.treatment.source_run_schema_version == 6
    assert result.classification == "matched-opportunity-structure"
    assert result.overall.model_dump(exclude={"schema_version", "channel"}) == {
        "opportunity_count": 0.0,
        "impression_count": 0.0,
        "noticed_count": 0.0,
        "opportunity_reach": 0.0,
        "impression_reach": 0.0,
        "noticed_reach": 0.0,
        "impression_frequency": 0.0,
        "notice_rate": 0.0,
    }
    assert _artifact_bytes(stored.directory) == before


def test_compare_stored_v6_response_runs_is_exact_aa_zero_and_read_only(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = _compare_stored_city_response_runs(stored, stored)

    assert result.model_id == "spatial-response-metrics-comparison-v1"
    assert result.claim_scope == "synthetic-response-comparison-not-causal-or-observed-effect"
    assert result.control_run_id == result.treatment_run_id == "response-study"
    assert result.opportunity_classification == "matched-opportunity-structure"
    assert result.response_assumption_classification == "matched-response-assumptions"
    assert result.control == result.treatment
    assert _artifact_bytes(stored.directory) == before


def test_compare_refuses_valid_runs_with_different_mobility_provenance(tmp_path: Path) -> None:
    control = _spatial_run(tmp_path, "control", seed=42)
    treatment = _spatial_run(tmp_path, "treatment", seed=43)

    with pytest.raises(SpatialComparisonError, match=r"mobility trace|seed"):
        compare_stored_city_runs(control, treatment)
