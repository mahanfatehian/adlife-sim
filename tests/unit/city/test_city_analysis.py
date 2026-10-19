from __future__ import annotations

import json
import math
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


def _persona_run(root: Path, run_id: str = "persona-study", *, seed: int = 0, change=None):
    from adlife.city.workbench_input import WorkbenchRunDraftV2
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_validation import persona_draft

    data = persona_draft().model_dump(mode="json")
    data["settings"].update(run_id=run_id, seed=seed, days=1)
    data["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 480, "end_minute": 482}
    ]
    data["scenario"]["phone"]["opportunity_probability_per_minute"] = 1.0
    if change is not None:
        change(data)
    validated = construct_workbench_run(WorkbenchRunDraftV2.model_validate_json(json.dumps(data)))
    return create_city_run(
        validated.pack,
        root=root,
        run_id=run_id,
        seed=seed,
        agent_count=3,
        days=1,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )


def _cognition_receipt(source: str, *, salt: str) -> dict[str, object]:
    def digest(label: str) -> str:
        return sha256(f"{salt}:{label}".encode()).hexdigest()

    outcomes = dict.fromkeys(
        ("rules", "mock", "provider", "cache", "fallback", "budget_skipped"), 0
    )
    outcomes[source] = 1
    provider_bound = source in {"provider", "cache", "fallback", "budget_skipped"}
    return {
        "cognition_input_sha256": digest("input"),
        "cognition_input_bytes": 1,
        "request_schema_version": 1,
        "result_schema_version": 1,
        "explainer_id": (
            f"city-{source}-explainer-v1"
            if source in {"rules", "mock"}
            else "city-provider-explainer-v1"
        ),
        "prompt_contract_version": 1,
        "prompt_contract_sha256": digest("prompt"),
        "result_schema_contract_version": 1,
        "result_schema_contract_sha256": digest("result-schema"),
        "selection_policy_version": 1,
        "selection_policy_sha256": digest("selection"),
        "fallback_policy_version": 1,
        "fallback_policy_sha256": digest("fallback"),
        "provider_profile_snapshot_sha256": digest("profile") if provider_bound else None,
        "provider_revision_sha256": digest("revision") if provider_bound else None,
        "effective_settings_sha256": digest("settings"),
        "budget_allocation_sha256": digest("budget"),
        "selected_request_count": 1,
        "outcome_counts": {"schema_version": 1, **outcomes},
        "terminal_stream_sha256": digest("stream"),
        "terminal_stream_bytes": 1,
        "terminal_record_count": 1,
        "summary_sha256": digest("summary"),
    }


def _as_v9(stored, *, source: str, salt: str):
    from adlife.core.domain.city_run import CityRunManifestV9

    manifest = CityRunManifestV9.model_validate(
        stored.manifest.model_dump(mode="python")
        | {
            "schema_version": 9,
            "workbench_input_schema_version": 3,
            "workbench_input_sha256": sha256(f"{salt}:workbench".encode()).hexdigest(),
            "cognition": _cognition_receipt(source, salt=salt),
        }
    )
    return replace(stored, manifest=manifest)


def test_v9_projections_keep_cognition_out_of_numeric_metrics_and_aa_equality(tmp_path):
    from adlife.city.analysis import response_metrics_for_stored_city_run

    stored = _persona_run(tmp_path)
    rules = _as_v9(stored, source="rules", salt="rules")
    provider = _as_v9(stored, source="provider", salt="provider")

    attention = metrics_for_stored_city_run(rules)
    response = response_metrics_for_stored_city_run(provider)
    attention_comparison = compare_stored_city_runs(rules, provider)
    response_comparison = _compare_stored_city_response_runs(rules, provider)

    assert attention.source_run_schema_version == response.source_run_schema_version == 9
    assert attention_comparison.control == attention_comparison.treatment
    assert response_comparison.control == response_comparison.treatment
    assert rules.manifest.cognition != provider.manifest.cognition


def test_persisted_v8_analysis_preserves_exact_provenance_and_source_bytes(tmp_path):
    from adlife.city.analysis import response_metrics_for_stored_city_run
    from adlife.city.run_store import CityRunStore

    _persona_run(tmp_path)
    stored = CityRunStore(tmp_path).load("persona-study")
    before = _artifact_bytes(tmp_path)
    attention = metrics_for_stored_city_run(stored)
    response = response_metrics_for_stored_city_run(stored)
    assert attention.source_run_schema_version == response.source_run_schema_version == 8
    assert compare_stored_city_runs(stored, stored).control.source_run_schema_version == 8
    assert _compare_stored_city_response_runs(stored, stored).control.source_run_schema_version == 8
    assert _artifact_bytes(tmp_path) == before


def test_v8_single_pair_compare_retains_population_assumption_confounding(tmp_path):
    def change(data):
        data["population"]["personas"][0]["traits"]["novelty_seeking"] = 0.2

    control = _persona_run(tmp_path, "control")
    treatment = _persona_run(tmp_path, "treatment", change=change)
    before = _artifact_bytes(tmp_path)
    result = _compare_stored_city_response_runs(control, treatment)
    assert (
        result.control.source_run_schema_version == result.treatment.source_run_schema_version == 8
    )
    assert result.response_assumption_classification == "response-assumption-confounded"
    assert _artifact_bytes(tmp_path) == before


def test_descriptive_only_v8_edit_changes_provenance_without_changing_response_input(tmp_path):
    def change(data):
        data["population"]["personas"][0].update(
            display_name="Another fictional name",
            age=54,
            occupation="Artist",
            background="Fictional biography marker",
            mbti="INTJ",
        )

    base = _persona_run(tmp_path / "base", "same")
    edited = _persona_run(tmp_path / "edited", "same", change=change)
    assert base.manifest.workbench_input_sha256 != edited.manifest.workbench_input_sha256
    assert base.response_input == edited.response_input
    assert base.manifest.response_input_sha256 == edited.manifest.response_input_sha256


@pytest.mark.parametrize("version", [8, 9, True, 8.0, "8"])
def test_v8_view_context_accepts_only_exact_saved_version_and_verified_metric_pair(
    tmp_path, version
):
    from adlife.city.analysis import response_metrics_for_stored_city_run
    from adlife.city.web import create_city_view_context

    stored = _persona_run(tmp_path)
    arguments = {
        "run_id": stored.manifest.run_id,
        "run_schema_version": version,
        "spatial_scenario": stored.spatial_scenario,
        "opportunity_evaluation": stored.opportunity_evaluation,
        "attention_evaluation": stored.attention_evaluation,
        "spatial_metrics": metrics_for_stored_city_run(stored),
        "response_input": stored.response_input,
        "response_evaluation": stored.response_evaluation,
        "spatial_response_metrics": response_metrics_for_stored_city_run(stored),
        "workbench_input_sha256": stored.manifest.workbench_input_sha256,
    }
    if type(version) is int and version == 8:
        context = create_city_view_context(stored.mobility, **arguments)
        assert context.run_schema_version == 8
        assert context.workbench_input_sha256 == stored.workbench_input.fingerprint
        arguments["spatial_metrics"] = arguments["spatial_metrics"].model_copy(
            update={"source_run_schema_version": 7}
        )
        with pytest.raises(ValueError, match="do not match"):
            create_city_view_context(stored.mobility, **arguments)
    elif version == 9:
        with pytest.raises(ValueError, match="do not match"):
            create_city_view_context(stored.mobility, **arguments)
    else:
        with pytest.raises(ValueError, match="unsupported city run schema version"):
            create_city_view_context(stored.mobility, **arguments)


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


def _stored_v10(root: Path, **options):
    from adlife.city.run_store import CityRunStore
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _, prepared, directory = _saved_v10(root, **options)
    return CityRunStore(root).load(prepared.manifest.run_id), directory


def _direct_v10_metrics(stored):
    from adlife.core.experiments.spatial_metrics import derive_spatial_metrics
    from adlife.core.experiments.spatial_response_metrics_v2 import (
        derive_spatial_response_metrics_v2,
    )

    agent_ids = tuple(agent.agent_id for agent in stored.mobility.agents)
    attention = derive_spatial_metrics(
        stored.opportunity_evaluation,
        stored.attention_evaluation,
        agent_ids=agent_ids,
        agents_sha256=stored.manifest.agents_sha256,
        trace_sha256=stored.manifest.trace_sha256,
        days=stored.manifest.days,
        source_run_schema_version=10,
    )
    return attention, derive_spatial_response_metrics_v2(
        stored.response_input,
        stored.response_evaluation,
        scenario=stored.spatial_scenario,
        opportunities=stored.opportunity_evaluation,
        attention=stored.attention_evaluation,
        agent_ids=agent_ids,
        attention_metrics=attention,
        cognition_input=stored.cognition_input,
        cognition=stored.cognition_evaluation,
    )


@pytest.mark.parametrize(
    ("mode", "source"), [("rules", None), ("mock", None), ("local", "provider")]
)
def test_v10_metrics_project_verified_dual_state_run_without_mutating_artifacts(
    tmp_path: Path, mode: str, source: str | None
) -> None:
    from adlife.city.analysis import response_metrics_for_stored_city_run
    from adlife.core.domain.city_run import CityRunManifestV10
    from adlife.core.experiments.spatial_response_metrics_v2 import SpatialResponseMetricsV2

    options = {"mode": mode} if source is None else {"mode": mode, "source": source}
    stored, directory = _stored_v10(tmp_path, **options)
    before = _artifact_bytes(directory)

    attention = metrics_for_stored_city_run(stored)
    response = response_metrics_for_stored_city_run(stored)

    assert type(stored.manifest) is CityRunManifestV10
    assert attention.source_run_schema_version == 10
    assert type(response) is SpatialResponseMetricsV2
    assert response.source_run_schema_version == 10
    assert response.rule_baseline.purchase_intention_proxy.name == "purchase_intention_proxy"
    assert response.cognition_input_sha256 == stored.manifest.cognition.cognition_input_sha256
    assert (attention, response) == _direct_v10_metrics(stored)
    assert response.response_stream_sha256 == stored.manifest.response_stream_sha256
    assert response.provider_dependent_cognition.mode == mode
    assert response.provider_dependent_cognition.coverage == stored.manifest.cognition.coverage
    assert _artifact_bytes(directory) == before


@pytest.mark.parametrize(
    "missing",
    [
        "cognition_evaluation",
        "cognition_input",
        "response_evaluation",
        "response_input",
        "spatial_scenario",
        "opportunity_evaluation",
        "attention_evaluation",
    ],
)
def test_v10_response_metrics_require_complete_exact_v2_evidence(
    tmp_path: Path, missing: str
) -> None:
    from adlife.city.analysis import response_metrics_for_stored_city_run

    stored, _ = _stored_v10(tmp_path, mode="rules")
    incomplete = replace(stored, **{missing: None})

    with pytest.raises(CorruptRunArtifact, match="incomplete"):
        response_metrics_for_stored_city_run(incomplete)


def test_v10_and_v9_response_metrics_dispatch_by_manifest_class_without_trial_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adlife.city.analysis import response_metrics_for_stored_city_run
    from tests.integration.test_city_run_replay import _create_v9_run

    v10, _ = _stored_v10(tmp_path / "v10", mode="mock")
    v9 = _create_v9_run(tmp_path / "v9")
    assert v9.manifest.schema_version == 9
    v1_response = _spatial_run(tmp_path / "v6", "response-study", response=True)

    # A manifest version selects exactly one evidence family; the other is corruption.
    for crossed in (
        replace(v10, response_evaluation=v1_response.response_evaluation),
        replace(v10, cognition_input=v9.cognition_input),
        replace(v10, cognition_evaluation=v9.cognition_evaluation),
        replace(v9, response_evaluation=v10.response_evaluation),
    ):
        with pytest.raises(CorruptRunArtifact, match="incomplete"):
            response_metrics_for_stored_city_run(crossed)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("response metrics entered the other version's derivation")

    monkeypatch.setattr(city_analysis, "derive_spatial_response_metrics", forbidden)
    assert response_metrics_for_stored_city_run(v10).source_run_schema_version == 10
    monkeypatch.undo()
    monkeypatch.setattr(city_analysis, "derive_spatial_response_metrics_v2", forbidden)
    assert response_metrics_for_stored_city_run(v9).source_run_schema_version == 9


def test_v10_response_metrics_refuse_a_manifest_from_another_verified_run(
    tmp_path: Path,
) -> None:
    from adlife.city.analysis import response_metrics_for_stored_city_run

    rules, _ = _stored_v10(tmp_path / "rules", mode="rules", run_id="rules-run")
    mock, _ = _stored_v10(tmp_path / "mock", mode="mock", run_id="mock-run")

    with pytest.raises(CorruptRunArtifact, match="manifest"):
        response_metrics_for_stored_city_run(replace(rules, manifest=mock.manifest))


def test_v10_response_projection_maps_inconsistent_evidence_to_artifact_corruption(
    tmp_path: Path,
) -> None:
    from adlife.city.analysis import response_metrics_for_stored_city_run

    rules, _ = _stored_v10(tmp_path / "rules", mode="rules")
    mock, _ = _stored_v10(tmp_path / "mock", mode="mock")

    for forged in (
        replace(rules, cognition_evaluation=mock.cognition_evaluation),
        replace(rules, response_evaluation=mock.response_evaluation),
        replace(mock, cognition_input=rules.cognition_input),
    ):
        with pytest.raises(CorruptRunArtifact, match="could not be projected") as error:
            response_metrics_for_stored_city_run(forged)
        assert error.value.__cause__ is None
        assert error.value.__suppress_context__


def test_v10_response_comparison_refuses_v1_contract_before_projecting_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_city_run_replay import _create_v9_run

    v10, directory = _stored_v10(tmp_path / "v10", mode="mock")
    v9 = _create_v9_run(tmp_path / "v9")
    before = _artifact_bytes(directory)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a refused comparison projected response metrics")

    monkeypatch.setattr(city_analysis, "response_metrics_for_stored_city_run", forbidden)
    for control, treatment in ((v10, v10), (v9, v10), (v10, v9)):
        with pytest.raises(SchemaVersionMismatch, match="schema-v6, v7, v8 or v9"):
            _compare_stored_city_response_runs(control, treatment)
    assert _artifact_bytes(directory) == before


def test_v10_attention_comparison_keeps_exact_source_schema_and_aa_zero(
    tmp_path: Path,
) -> None:
    stored, directory = _stored_v10(tmp_path, mode="mock")
    before = _artifact_bytes(directory)

    result = compare_stored_city_runs(stored, stored)

    assert result.control.source_run_schema_version == 10
    assert result.treatment.source_run_schema_version == 10
    assert result.classification == "matched-opportunity-structure"
    assert set(result.overall.model_dump(exclude={"schema_version", "channel"}).values()) == {0.0}
    assert _artifact_bytes(directory) == before


@pytest.mark.parametrize("v10_arm", ["treatment", "control"])
def test_mixed_v9_and_v10_attention_comparison_keeps_truthful_per_arm_schemas(
    tmp_path: Path, v10_arm: str
) -> None:
    # Bounded cognition cannot reach movement, opportunities, or attention. A matched
    # V9/V10 pair therefore takes the existing cross-version attention-snapshot path in
    # either order, while the V1 response comparison refuses the same pair outright.
    from tests.integration.test_city_run_replay import _create_v9_run

    v9 = _create_v9_run(tmp_path / "v9")

    def same_seed(document) -> None:
        document["settings"].update(seed=v9.manifest.seed)

    v10, directory = _stored_v10(
        tmp_path / "v10",
        mode="mock",
        agents=v9.manifest.agent_count,
        days=v9.manifest.days,
        mutation=same_seed,
    )
    provenance = ("agents_sha256", "trace_sha256", "city_sha256", "seed", "agent_count", "days")
    assert [getattr(v10.manifest, name) for name in provenance] == [
        getattr(v9.manifest, name) for name in provenance
    ]
    control, treatment = (v9, v10) if v10_arm == "treatment" else (v10, v9)
    before = (_artifact_bytes(v9.directory), _artifact_bytes(directory))

    result = compare_stored_city_runs(control, treatment)

    assert (result.control_run_id, result.treatment_run_id) == (
        control.manifest.run_id,
        treatment.manifest.run_id,
    )
    assert (
        result.control.source_run_schema_version,
        result.treatment.source_run_schema_version,
    ) == (
        control.manifest.schema_version,
        treatment.manifest.schema_version,
    )
    assert {control.manifest.schema_version, treatment.manifest.schema_version} == {9, 10}
    assert result.classification == "matched-opportunity-structure"
    assert result.control.model_dump(exclude={"source_run_schema_version"}) == (
        result.treatment.model_dump(exclude={"source_run_schema_version"})
    )
    deltas = [
        value
        for series in (result.overall, *result.channels)
        for value in series.model_dump(exclude={"schema_version", "channel"}).values()
    ]
    assert deltas and all(value == 0.0 and math.copysign(1.0, value) == 1.0 for value in deltas)
    with pytest.raises(SchemaVersionMismatch, match="schema-v6, v7, v8 or v9"):
        _compare_stored_city_response_runs(control, treatment)
    assert (_artifact_bytes(v9.directory), _artifact_bytes(directory)) == before


def test_legacy_refusals_name_every_supported_metric_schema(tmp_path: Path) -> None:
    from adlife.city.analysis import response_metrics_for_stored_city_run

    pack = load_pack(pack_data())
    legacy = create_city_run(pack, root=tmp_path, run_id="legacy", seed=42, agent_count=2, days=1)
    attention_only = _spatial_run(tmp_path, "attention-study")

    with pytest.raises(SchemaVersionMismatch, match=r"schema-v5, v6, v7, v8, v9 or v10"):
        metrics_for_stored_city_run(legacy)
    with pytest.raises(SchemaVersionMismatch, match=r"schema-v6, v7, v8, v9 or v10"):
        response_metrics_for_stored_city_run(attention_only)


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
