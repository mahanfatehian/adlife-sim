from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city.run_store import CityRunStore
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import SpatialStudyDefinition, SpatialStudyPair
from adlife.core.ports.run_store import CorruptRunArtifact, SchemaVersionMismatch
from tests.unit.city.test_city_analysis import _artifact_bytes, _spatial_run


def definition(*, response: bool = False, contrast: bool = False, same: bool = True, n: int = 2):
    return SpatialStudyDefinition(
        study_id="repeated-study",
        design="paired-contrast" if contrast else "a-a",
        analysis_scope="attention-and-response" if response else "attention",
        pairs=tuple(
            SpatialStudyPair(
                seed=seed,
                control_run_id=f"control-{seed}",
                treatment_run_id=f"control-{seed}" if same else f"treatment-{seed}",
            )
            for seed in range(n)
        ),
    )


def analyze(root: Path, study: SpatialStudyDefinition):
    from adlife.city import studies

    return studies.analyze_stored_spatial_study(CityRunStore(root), study)


def make_runs(root: Path, *, response: bool = False, contrast: bool = False, same: bool = True):
    for seed in range(2):
        _spatial_run(root, f"control-{seed}", seed=seed, response=response)
        if not same:
            _spatial_run(
                root,
                f"treatment-{seed}",
                seed=seed,
                response=response,
                end=3 if contrast else 2,
            )


@pytest.mark.parametrize(
    "schema,scope", [(5, "attention"), (6, "attention"), (6, "attention-and-response")]
)
@pytest.mark.parametrize("same", [False, True])
def test_verified_aa_study_has_exact_receipts_and_preserves_sources(tmp_path, schema, scope, same):
    make_runs(tmp_path, response=schema == 6, same=same)
    before = _artifact_bytes(tmp_path)
    result = analyze(tmp_path, definition(response=scope != "attention", same=same))
    assert result.model_id == "spatial-paired-study-v1"
    assert result.claim_scope == "synthetic-study-not-observed-or-causal-effect"
    assert result.source_run_schema_version == schema
    assert result.seeds == (0, 1)
    assert result.evidence_tier == "exploratory-under-50-seeds"
    assert result.a_a_status == "exact-zero-verified"
    assert result.opportunity_matched_pair_count == 2
    assert result.opportunity_confounded_pair_count == 0
    assert len(result.statistics) == (62 if scope != "attention" else 24)
    for pair in result.pairs:
        for arm in (pair.control, pair.treatment):
            raw = (tmp_path / "city-runs" / arm.run_id / "run.json").read_bytes()
            assert arm.manifest_sha256 == sha256(raw).hexdigest()
            assert (arm.response is not None) == (schema == 6)
        assert all(row.delta == 0.0 and row.control == row.treatment for row in pair.scalars)
    assert all(stat.direction == "stable-null" for stat in result.statistics)
    assert _artifact_bytes(tmp_path) == before
    public = canonical_json(result)
    for forbidden in (
        str(tmp_path),
        "traits",
        "interests",
        "target_interests",
        'opportunities":',
        'frames":',
    ):
        assert forbidden not in public


def test_contrast_classifies_opportunity_and_response_assumptions_independently(tmp_path):
    make_runs(tmp_path, response=True, contrast=True, same=False)
    result = analyze(tmp_path, definition(response=True, contrast=True, same=False))
    assert result.opportunity_classification == "opportunity-confounded"
    assert result.opportunity_confounded_pair_count == 2
    # The fixture's changed frequency cap is a numeric response assumption.
    assert result.response_assumption_classification == "response-assumption-confounded"
    assert result.response_assumption_confounded_pair_count == 2
    assert result.a_a_status == "not-applicable"
    count = next(
        s for s in result.statistics if s.metric_key == "attention.overall.opportunity_count"
    )
    assert count.mean_paired_difference == 2.0
    assert count.sample_standard_deviation == 0.0
    assert count.paired_standardized_difference is None


def test_identical_contrast_and_mixed_schemas_are_refused(tmp_path):
    make_runs(tmp_path, same=False)
    with pytest.raises(ValueError):
        analyze(tmp_path, definition(contrast=True, same=False))
    # A study cannot mix complete, independently valid v5 and v6 runs.
    mixed = tmp_path / "mixed"
    _spatial_run(mixed, "control-0", seed=0)
    _spatial_run(mixed, "control-1", seed=1, response=True)
    with pytest.raises(ValueError):
        analyze(mixed, definition())


def test_response_scope_requires_v6_and_corruption_is_not_scientific_incompatibility(tmp_path):
    make_runs(tmp_path)
    with pytest.raises(SchemaVersionMismatch):
        analyze(tmp_path, definition(response=True))
    path = tmp_path / "city-runs" / "control-0" / "outputs" / "spatial-attention.jsonl"
    path.write_bytes(path.read_bytes() + b"{}\n")
    with pytest.raises(CorruptRunArtifact):
        analyze(tmp_path, definition())


def test_verified_aa_difference_is_an_artifact_failure(tmp_path):
    make_runs(tmp_path, contrast=True, same=False)
    with pytest.raises(CorruptRunArtifact):
        analyze(tmp_path, definition(same=False))


@pytest.mark.parametrize(
    "field,value", [("package_version", "0.2.0"), ("python_version", "3.11.9")]
)
@pytest.mark.parametrize("run_id", ["treatment-0", "control-1", "treatment-1"])
def test_analysis_seam_refuses_mismatched_runtime_receipts(
    tmp_path, monkeypatch, field, value, run_id
):
    make_runs(tmp_path, same=False)
    store = CityRunStore(tmp_path)
    real_load = store.load

    def load(identifier):
        stored = real_load(identifier)
        if identifier == run_id:
            return replace(stored, manifest=stored.manifest.model_copy(update={field: value}))
        return stored

    # Current CityRunStore rejects another runtime itself. Inject at its verified
    # return seam to protect analysis compatibility for future verified backends.
    monkeypatch.setattr(store, "load", load)
    with pytest.raises(ValueError):
        from adlife.city.studies import analyze_stored_spatial_study

        analyze_stored_spatial_study(store, definition(same=False))


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": 8},
        {"city_sha256": "0" * 64},
        {"city_schema_version": 2},
        {"agents_sha256": "0" * 64},
        {"trace_sha256": "0" * 64},
        {"days": 2, "frame_count": 2880, "position_count": 5760},
        {"agent_count": 3, "position_count": 4320},
        {
            "place_schema_version": 1,
            "place_set_sha256": "0" * 64,
            "place_assignments_sha256": "1" * 64,
        },
    ],
)
@pytest.mark.parametrize("run_id", ["treatment-0", "treatment-1"])
def test_analysis_seam_refuses_each_unmatched_provenance_field(
    tmp_path, monkeypatch, changes, run_id
):
    from adlife.city.studies import analyze_stored_spatial_study

    make_runs(tmp_path, same=False)
    store = CityRunStore(tmp_path)
    real_load = store.load

    def load(identifier):
        stored = real_load(identifier)
        if identifier == run_id:
            return replace(stored, manifest=stored.manifest.model_copy(update=changes))
        return stored

    monkeypatch.setattr(store, "load", load)
    with pytest.raises(ValueError):
        analyze_stored_spatial_study(store, definition(same=False))


def test_cross_seed_scenario_identity_cannot_change(tmp_path):
    _spatial_run(tmp_path, "control-0", seed=0)
    _spatial_run(tmp_path, "control-1", seed=1, end=3)
    with pytest.raises(ValueError):
        analyze(tmp_path, definition())


@pytest.mark.parametrize("change", ["creative", "assumption"])
def test_response_contrast_has_independent_opportunity_and_assumption_verdicts(tmp_path, change):
    from adlife.city.runs import create_city_run

    for seed in range(2):
        stored = _spatial_run(tmp_path, f"control-{seed}", seed=seed, response=True)
        scenario = stored.spatial_scenario
        response_input = stored.response_input
        if change == "creative":
            scenario = scenario.model_copy(
                update={
                    "campaigns": tuple(
                        campaign.model_copy(update={"creative_sha256": "c" * 64})
                        for campaign in scenario.campaigns
                    )
                }
            )
            response_input = response_input.model_copy(
                update={
                    "scenario_sha256": scenario.fingerprint,
                    "campaigns": tuple(
                        campaign.model_copy(update={"creative_sha256": "c" * 64})
                        for campaign in response_input.campaigns
                    ),
                }
            )
        else:
            response_input = response_input.model_copy(
                update={
                    "campaigns": tuple(
                        campaign.model_copy(update={"relative_price": 0.5})
                        for campaign in response_input.campaigns
                    )
                }
            )
        create_city_run(
            stored.pack,
            root=tmp_path,
            run_id=f"treatment-{seed}",
            seed=seed,
            agent_count=2,
            days=1,
            spatial_scenario=scenario,
            spatial_response=response_input,
        )
    result = analyze(tmp_path, definition(response=True, contrast=True, same=False))
    assert result.opportunity_classification == "matched-opportunity-structure"
    assert result.response_assumption_classification == (
        "matched-response-assumptions" if change == "creative" else "response-assumption-confounded"
    )
    if change == "creative":
        assert all(statistic.direction == "stable-null" for statistic in result.statistics)


def test_attention_only_v6_aa_requires_exact_response_artifact_identity(tmp_path):
    from adlife.city.runs import create_city_run

    for seed in range(2):
        stored = _spatial_run(tmp_path, f"control-{seed}", seed=seed, response=True)
        response_input = stored.response_input.model_copy(
            update={
                "campaigns": tuple(
                    campaign.model_copy(update={"relative_price": 0.5})
                    for campaign in stored.response_input.campaigns
                )
            }
        )
        create_city_run(
            stored.pack,
            root=tmp_path,
            run_id=f"treatment-{seed}",
            seed=seed,
            agent_count=2,
            days=1,
            spatial_scenario=stored.spatial_scenario,
            spatial_response=response_input,
        )
    with pytest.raises(CorruptRunArtifact):
        analyze(tmp_path, definition(same=False))


def test_v2_city_and_real_place_provenance_is_compact_and_reconstructable(tmp_path):
    from adlife.city.runs import create_city_run
    from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data
    from tests.unit.city.test_spatial_opportunity import _mobility, _phone, _scenario

    pack = load_pack_v2(pack_v2_data())
    places = _mobility(pack, agent_count=2).places
    scenario = _scenario(
        pack, [_phone(windows=[{"start_minute": 0, "end_minute": 2}], probability=1.0, cap=2)]
    )
    for seed in range(2):
        create_city_run(
            pack,
            root=tmp_path,
            run_id=f"control-{seed}",
            seed=seed,
            agent_count=2,
            days=1,
            places=places,
            spatial_scenario=scenario,
        )
    result = analyze(tmp_path, definition())
    assert result.city.schema_version == 2
    assert result.city.source == pack.source
    assert result.city.time_zone == pack.time_zone
    assert result.city.known_omissions == pack.known_omissions
    assert result.place_schema_version == 1
    assert result.place_set_sha256 == places.fingerprint
    assert all(pair.place_assignments_sha256 is not None for pair in result.pairs)
    text = canonical_json(result)
    assert '"nodes"' not in text and '"roads"' not in text and '"places"' not in text


@pytest.mark.parametrize("response_scope", [False, True])
def test_cross_seed_response_inputs_are_constant_only_for_response_scope(tmp_path, response_scope):
    from adlife.city.runs import create_city_run
    from tests.unit.city.test_city_pack import load_pack, pack_data
    from tests.unit.city.test_spatial_opportunity import _phone, _scenario
    from tests.unit.city.test_spatial_response import _response_input

    pack = load_pack(pack_data())
    scenario = _scenario(
        pack, [_phone(windows=[{"start_minute": 0, "end_minute": 2}], probability=1.0, cap=2)]
    )
    for seed in range(2):
        response_input = _response_input(scenario, agent_ids=("person-001", "person-002"))
        if seed == 1:
            response_input = response_input.model_copy(
                update={
                    "campaigns": tuple(
                        campaign.model_copy(update={"relative_price": 0.5})
                        for campaign in response_input.campaigns
                    )
                }
            )
        create_city_run(
            pack,
            root=tmp_path,
            run_id=f"control-{seed}",
            seed=seed,
            agent_count=2,
            days=1,
            spatial_scenario=scenario,
            spatial_response=response_input,
        )
    if response_scope:
        with pytest.raises(ValueError):
            analyze(tmp_path, definition(response=True))
    else:
        result = analyze(tmp_path, definition())
        assert result.a_a_status == "exact-zero-verified"
        assert result.response_assumption_classification == "not-applicable"
