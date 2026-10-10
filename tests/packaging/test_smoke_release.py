"""Exercise smoke workspace lifetime without creating another environment."""

import copy
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adlife.city.analysis import response_metrics_for_stored_city_run
from adlife.city.run_store import CityRunStore
from adlife.city.studies import analyze_stored_spatial_study
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import SpatialStudyDefinition, SpatialStudyPair
from adlife.core.experiments.spatial_response_metrics import SpatialResponseMetrics
from adlife.core.experiments.spatial_study import SpatialStudyResult
from adlife.reporting.spatial_html import render_spatial_study_html
from scripts import smoke_release
from tests.unit.city.test_city_analysis import _spatial_run


def _expected_response_document(*, city_sha256: str, scenario_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "city_sha256": city_sha256,
        "scenario_sha256": scenario_sha256,
        "profiles": [
            {
                "agent_id": "person-001",
                "fictional": True,
                "interests": ["coffee", "outdoors"],
                "traits": {
                    "price_sensitivity": 0.8,
                    "novelty_seeking": 0.3,
                    "advertising_skepticism": 0.6,
                    "mobile_recall_encoding": 0.7,
                    "roadside_recall_encoding": 0.4,
                    "impulsivity": 0.2,
                },
            },
            {
                "agent_id": "person-002",
                "fictional": True,
                "interests": ["coffee", "work"],
                "traits": {
                    "price_sensitivity": 0.2,
                    "novelty_seeking": 0.9,
                    "advertising_skepticism": 0.1,
                    "mobile_recall_encoding": 0.8,
                    "roadside_recall_encoding": 0.5,
                    "impulsivity": 0.7,
                },
            },
        ],
        "campaigns": [
            {
                "campaign_id": "fictional-launch",
                "creative_sha256": (
                    "1b286665d9b12264f37cfcbb7b9938660fa2ad496de2fa86c2f78fe802b7f0ec"
                ),
                "target_interests": ["coffee", "outdoors"],
                "relative_price": 1.0,
            }
        ],
        "initial_states": [
            {
                "agent_id": agent_id,
                "campaign_id": "fictional-launch",
                "brand_sentiment": 0.0,
                "recall_strength": 0.0,
                "purchase_intention": 0.0,
            }
            for agent_id in ("person-001", "person-002")
        ],
    }


def test_smoke_generates_one_canonical_fictional_response_input(tmp_path: Path) -> None:
    path = tmp_path / "response.json"
    expected = _expected_response_document(city_sha256="a" * 64, scenario_sha256="e" * 64)

    smoke_release._write_fictional_spatial_response(
        path,
        city_sha256="a" * 64,
        scenario_sha256="e" * 64,
    )

    expected_bytes = (
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    assert path.read_bytes() == expected_bytes


def test_smoke_generates_one_canonical_two_seed_response_aa_study(tmp_path: Path) -> None:
    path = tmp_path / "study.json"
    expected = {
        "schema_version": 1,
        "study_id": "wheel-aa-study",
        "design": "a-a",
        "analysis_scope": "attention-and-response",
        "pairs": [
            {
                "seed": 0,
                "control_run_id": "catalog-seed-0",
                "treatment_run_id": "catalog-seed-0",
            },
            {
                "seed": 1,
                "control_run_id": "catalog-seed-1",
                "treatment_run_id": "catalog-seed-1",
            },
        ],
    }

    smoke_release._write_spatial_aa_study(path)

    expected_bytes = (
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    assert path.read_bytes() == expected_bytes


def _full_spatial_study_fixture(
    root: Path,
) -> tuple[
    SpatialStudyResult,
    dict[str, object],
    bytes,
    dict[int, Path],
    dict[int, dict[str, object]],
]:
    for seed in (0, 1):
        _spatial_run(root, f"catalog-seed-{seed}", seed=seed, response=True)
    definition = SpatialStudyDefinition(
        study_id="wheel-aa-study",
        design="a-a",
        analysis_scope="attention-and-response",
        pairs=tuple(
            SpatialStudyPair(
                seed=seed,
                control_run_id=f"catalog-seed-{seed}",
                treatment_run_id=f"catalog-seed-{seed}",
            )
            for seed in (0, 1)
        ),
    )
    result = analyze_stored_spatial_study(CityRunStore(root), definition)
    document = result.model_dump(mode="json")
    SpatialStudyResult.model_validate_json(json.dumps(document))
    definition_bytes = (canonical_json(definition) + "\n").encode("utf-8")
    run_directories = {seed: root / "city-runs" / f"catalog-seed-{seed}" for seed in (0, 1)}
    metrics = {
        seed: response_metrics_for_stored_city_run(
            CityRunStore(root).load(f"catalog-seed-{seed}")
        ).model_dump(mode="json")
        for seed in (0, 1)
    }
    for value in metrics.values():
        SpatialResponseMetrics.model_validate_json(json.dumps(value))
    return result, document, definition_bytes, run_directories, metrics


def _canonical_document_sha256(document: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _city_run_receipt(manifest: dict[str, object], run_directory: Path) -> dict[str, object]:
    summaries = {
        name: json.loads((run_directory / "outputs" / f"{name}-summary.json").read_text("utf-8"))
        for name in ("opportunity", "attention", "response")
    }
    opportunity = summaries["opportunity"]
    attention = summaries["attention"]
    response = summaries["response"]
    return {
        "run_id": manifest["run_id"],
        "city_id": "fictional-grid-v2",
        "city_sha256": manifest["city_sha256"],
        "trace_sha256": manifest["trace_sha256"],
        "frame_count": manifest["frame_count"],
        "position_count": manifest["position_count"],
        "run_schema_version": manifest["schema_version"],
        "directory": str(run_directory.resolve()),
        "place_set_sha256": manifest["place_set_sha256"],
        "place_assignments_sha256": manifest["place_assignments_sha256"],
        "scenario_sha256": manifest["scenario_sha256"],
        "opportunity_stream_sha256": manifest["opportunity_stream_sha256"],
        "opportunity_summary_sha256": manifest["opportunity_summary_sha256"],
        "opportunity_stream_bytes": manifest["opportunity_stream_bytes"],
        "opportunity_count": manifest["opportunity_count"],
        "opportunity_counts": opportunity["counts"],
        "opportunity_claim_scope": opportunity["claim_scope"],
        "attention_model_id": manifest["spatial_attention_model_id"],
        "attention_claim_scope": attention["claim_scope"],
        "attention_notice_probability": attention["notice_probability"],
        "attention_stream_sha256": manifest["attention_stream_sha256"],
        "attention_summary_sha256": manifest["attention_summary_sha256"],
        "attention_stream_bytes": manifest["attention_stream_bytes"],
        "impression_count": manifest["impression_count"],
        "noticed_count": manifest["noticed_count"],
        "attention_counts": attention["counts"],
        "response_model_id": manifest["spatial_response_model_id"],
        "response_claim_scope": response["claim_scope"],
        "response_input_sha256": manifest["response_input_sha256"],
        "response_stream_sha256": manifest["response_stream_sha256"],
        "response_state_sha256": manifest["response_state_sha256"],
        "response_summary_sha256": manifest["response_summary_sha256"],
        "response_stream_bytes": manifest["response_stream_bytes"],
        "response_count": manifest["response_count"],
        "state_update_count": manifest["state_update_count"],
        "response_campaign_count": manifest["response_campaign_count"],
        "final_state_count": manifest["final_state_count"],
        "response_counts": response["counts"],
    }


def _mutate_city_run_stage_count(document: dict[str, object]) -> None:
    counts = document["opportunity_counts"]
    assert isinstance(counts, dict)
    value = counts["roadside_matching_traversal_count"]
    assert isinstance(value, int)
    counts["roadside_matching_traversal_count"] = value + 1


def _verified_metrics_hashes(
    metrics: dict[int, dict[str, object]], run_directories: dict[int, Path]
) -> dict[int, str]:
    return {
        seed: smoke_release._verify_response_metrics(
            metrics[seed],
            run_directory=run_directories[seed],
            expected_campaign_ids=("fictional-launch",),
        )
        for seed in (0, 1)
    }


def _verify_study_fixture(
    document: dict[str, object],
    definition_bytes: bytes,
    run_directories: dict[int, Path],
    metrics: dict[int, dict[str, object]],
) -> str:
    return smoke_release._verify_spatial_aa_study(
        document,
        definition_bytes=definition_bytes,
        run_directories=run_directories,
        response_metrics=metrics,
        response_metrics_sha256=_verified_metrics_hashes(metrics, run_directories),
    )


def _pair_observations(pair: dict[str, object]) -> dict[str, dict[str, object]]:
    scalars = pair["scalars"]
    assert isinstance(scalars, list)
    observations: dict[str, dict[str, object]] = {}
    for scalar in scalars:
        assert isinstance(scalar, dict)
        observation = scalar["control"]
        assert isinstance(observation, dict)
        key = observation["key"]
        assert isinstance(key, str)
        observations[key] = observation
    return observations


def test_smoke_verifies_complete_response_metrics_against_exact_artifacts(tmp_path: Path) -> None:
    _result, _study, _definition, run_directories, metrics = _full_spatial_study_fixture(tmp_path)

    hashes = _verified_metrics_hashes(metrics, run_directories)

    assert hashes == {seed: _canonical_document_sha256(metrics[seed]) for seed in (0, 1)}


@pytest.mark.parametrize("evidence", ("event", "state"))
def test_smoke_refuses_coordinated_metrics_divergence_from_persisted_response(
    tmp_path: Path, evidence: str
) -> None:
    _result, _study, _definition, run_directories, metrics = _full_spatial_study_fixture(tmp_path)
    run_directory = run_directories[0]
    source_snapshot = smoke_release._artifact_hashes(run_directory)
    corrupted = copy.deepcopy(metrics[0])
    overall = corrupted["overall"]
    channels = corrupted["channels"]
    campaigns = corrupted["campaigns"]
    assert isinstance(overall, dict)
    assert isinstance(channels, list)
    assert isinstance(campaigns, list)
    if evidence == "event":
        for series in (overall, *channels, *campaigns):
            assert isinstance(series, dict)
            receipt = series["mean_rule_sentiment_delta"]
            assert isinstance(receipt, dict)
            receipt.update(numerator=0.0, value=0.0)
    else:
        for series in (overall, *campaigns):
            assert isinstance(series, dict)
            receipt = series["brand_sentiment"]
            assert isinstance(receipt, dict)
            receipt.update(
                initial_total=0.0,
                final_total=0.0,
                change_total=0.0,
                initial_mean=0.0,
                final_mean=0.0,
                mean_change=0.0,
            )

    with pytest.raises(SystemExit):
        smoke_release._verify_response_metrics(
            corrupted,
            run_directory=run_directory,
            expected_campaign_ids=("fictional-launch",),
        )

    assert smoke_release._artifact_hashes(run_directory) == source_snapshot


def test_smoke_v6_manifest_requires_exact_schema_types_and_a_campaign(tmp_path: Path) -> None:
    _result, _study, _definition, run_directories, _metrics = _full_spatial_study_fixture(tmp_path)
    manifest = json.loads((run_directories[0] / "run.json").read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    invalid_documents = []

    no_campaign = copy.deepcopy(manifest)
    no_campaign.update(response_campaign_count=0, final_state_count=0)
    invalid_documents.append(no_campaign)

    bool_city_schema = copy.deepcopy(manifest)
    bool_city_schema["city_schema_version"] = True
    invalid_documents.append(bool_city_schema)

    bool_place_schema = copy.deepcopy(manifest)
    bool_place_schema.update(
        place_schema_version=True,
        place_set_sha256="a" * 64,
        place_assignments_sha256="b" * 64,
    )
    invalid_documents.append(bool_place_schema)

    for document in invalid_documents:
        with pytest.raises(SystemExit):
            smoke_release._verify_v6_manifest(document, "schema-v6 manifest")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda city: city["source"].update({"source_sha256": "A" * 64}),  # type: ignore[union-attr]
        lambda city: city.update({"source_url": "https://example.org/legacy"}),
        lambda city: city.update({"time_zone": "America/New_York"}),
        lambda city: city["known_omissions"].append(  # type: ignore[union-attr]
            "Synthetic geometry; not a real city"
        ),
    ],
    ids=("uppercase-source-hash", "legacy-source-field", "wrong-time-zone", "duplicate-omission"),
)
def test_smoke_v2_city_provenance_is_the_exact_public_packaged_receipt(
    mutate: Callable[[dict[str, Any]], object],
) -> None:
    city = copy.deepcopy(smoke_release._FICTIONAL_GRID_V2_PROVENANCE)
    city["city_sha256"] = "a" * 64
    manifest: dict[str, object] = {"city_schema_version": 2, "city_sha256": "a" * 64}

    smoke_release._verify_study_city_provenance(city, manifest, "v2 city")
    mutate(city)

    with pytest.raises(SystemExit):
        smoke_release._verify_study_city_provenance(city, manifest, "v2 city")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document.pop("agents_sha256"),
        lambda document: document["channels"].reverse(),  # type: ignore[union-attr]
        lambda document: document["overall"]["response_frequency"].update(  # type: ignore[index,union-attr]
            {"denominator": 2, "value": 0.5}
        ),
    ],
    ids=("missing-provenance", "reversed-channels", "impossible-frequency"),
)
def test_smoke_refuses_structurally_invalid_direct_response_metrics(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], object]
) -> None:
    _result, _study, _definition, run_directories, metrics = _full_spatial_study_fixture(tmp_path)
    corrupted = copy.deepcopy(metrics[0])
    mutate(corrupted)

    with pytest.raises(SystemExit):
        smoke_release._verify_response_metrics(
            corrupted,
            run_directory=run_directories[0],
            expected_campaign_ids=("fictional-launch",),
        )


def test_smoke_refuses_noncanonical_response_summary(tmp_path: Path) -> None:
    _result, _study, _definition, run_directories, metrics = _full_spatial_study_fixture(tmp_path)
    run_directory = run_directories[0]
    summary_path = run_directory / "outputs" / "response-summary.json"
    summary_path.write_bytes(b"not-json")
    manifest_path = run_directory / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["response_summary_sha256"] = sha256(b"not-json").hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(SystemExit):
        smoke_release._verify_response_metrics(
            metrics[0],
            run_directory=run_directory,
            expected_campaign_ids=("fictional-launch",),
        )


@pytest.mark.parametrize(
    "relative",
    ("outputs/spatial-responses.jsonl", "outputs/response-state.json"),
)
def test_smoke_recomputes_direct_response_artifact_hashes(tmp_path: Path, relative: str) -> None:
    _result, _study, _definition, run_directories, metrics = _full_spatial_study_fixture(tmp_path)
    path = run_directories[0] / relative
    path.write_bytes(path.read_bytes() + b"tampered\n")

    with pytest.raises(SystemExit):
        smoke_release._verify_response_metrics(
            metrics[0],
            run_directory=run_directories[0],
            expected_campaign_ids=("fictional-launch",),
        )


def test_smoke_refuses_response_reach_larger_than_the_event_count(tmp_path: Path) -> None:
    _result, _study, _definition, run_directories, metrics = _full_spatial_study_fixture(tmp_path)
    corrupted = copy.deepcopy(metrics[0])
    overall = corrupted["overall"]
    assert isinstance(overall, dict)
    reach = overall["response_reach"]
    frequency = overall["response_frequency"]
    assert isinstance(reach, dict)
    assert isinstance(frequency, dict)
    reach.update(numerator=2, value=1.0)
    frequency.update(denominator=2, value=0.5)

    with pytest.raises(SystemExit):
        smoke_release._verify_response_metrics(
            corrupted,
            run_directory=run_directories[0],
            expected_campaign_ids=("fictional-launch",),
        )


def test_smoke_refuses_response_state_totals_outside_domain_bounds(tmp_path: Path) -> None:
    _result, _study, _definition, run_directories, metrics = _full_spatial_study_fixture(tmp_path)
    corrupted = copy.deepcopy(metrics[0])
    overall = corrupted["overall"]
    campaigns = corrupted["campaigns"]
    assert isinstance(overall, dict)
    assert isinstance(campaigns, list)
    for series in (overall, campaigns[0]):
        receipt = series["purchase_intention_proxy"]
        assert isinstance(receipt, dict)
        initial = receipt["initial_total"]
        denominator = receipt["denominator"]
        assert isinstance(initial, float)
        assert isinstance(denominator, int)
        final = float(denominator) + 0.5
        change = final - initial
        receipt.update(
            final_total=final,
            change_total=change,
            final_mean=final / denominator,
            mean_change=change / denominator,
        )

    with pytest.raises(SystemExit):
        smoke_release._verify_response_metrics(
            corrupted,
            run_directory=run_directories[0],
            expected_campaign_ids=("fictional-launch",),
        )


def test_smoke_refuses_zero_response_rule_numerators_and_state_changes(tmp_path: Path) -> None:
    _result, _study, _definition, _runs, metrics = _full_spatial_study_fixture(tmp_path)
    channels = metrics[0]["channels"]
    campaigns = metrics[0]["campaigns"]
    assert isinstance(channels, list)
    assert isinstance(campaigns, list)

    zero_channel = copy.deepcopy(channels[0])
    sentiment = zero_channel["mean_rule_sentiment_delta"]
    assert isinstance(sentiment, dict)
    sentiment["numerator"] = 0.1
    with pytest.raises(SystemExit):
        smoke_release._response_series(
            zero_channel,
            identity_key="channel",
            identity="roadside",
            population_size=2,
            include_state=False,
            label="zero response channel",
        )

    zero_campaign = copy.deepcopy(campaigns[0])
    for name in smoke_release._RESPONSE_EVENT_NAMES:
        receipt = zero_campaign[name]
        assert isinstance(receipt, dict)
        receipt["numerator"] = 0 if name in smoke_release._RESPONSE_EVENT_NAMES[:3] else 0.0
        receipt["denominator"] = (
            1 if name == "response_count" else 2 if name == "response_reach" else 0
        )
        receipt["value"] = 0.0
    state = zero_campaign["brand_sentiment"]
    assert isinstance(state, dict)
    initial = state["initial_total"]
    denominator = state["denominator"]
    assert isinstance(initial, float)
    assert isinstance(denominator, int)
    state.update(
        final_total=initial + 0.1,
        change_total=0.1,
        final_mean=(initial + 0.1) / denominator,
        mean_change=0.1 / denominator,
    )
    with pytest.raises(SystemExit):
        smoke_release._response_series(
            zero_campaign,
            identity_key="campaign_id",
            identity="fictional-launch",
            population_size=2,
            include_state=True,
            label="zero response campaign",
        )


def test_smoke_refuses_impossible_attention_hierarchy(tmp_path: Path) -> None:
    _result, document, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path)
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    pair = pairs[0]
    assert isinstance(pair, dict)
    control = pair["control"]
    assert isinstance(control, dict)
    observations = _pair_observations(pair)
    noticed = observations["attention.channel.mobile.noticed_count"]
    notice_rate = observations["attention.channel.mobile.notice_rate"]
    noticed.update(numerator=5.0, value=5.0)
    notice_rate.update(numerator=5.0, value=1.25)

    with pytest.raises(SystemExit):
        smoke_release._attention_metrics_from_observations(
            document,
            pair,
            control,
            observations,
            label="attention hierarchy",
        )


def test_smoke_refuses_response_receipts_not_aligned_with_notices(tmp_path: Path) -> None:
    _result, document, _definition, _runs, metrics = _full_spatial_study_fixture(tmp_path)
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    pair = pairs[0]
    assert isinstance(pair, dict)
    control = pair["control"]
    assert isinstance(control, dict)
    attention = smoke_release._attention_metrics_from_observations(
        document,
        pair,
        control,
        _pair_observations(pair),
        label="notice alignment",
    )
    response = copy.deepcopy(metrics[0])
    overall = response["overall"]
    assert isinstance(overall, dict)
    count = overall["response_count"]
    assert isinstance(count, dict)
    count.update(numerator=2, value=2.0)

    with pytest.raises(SystemExit):
        smoke_release._verify_notice_response_alignment(attention, response, "notice alignment")


def test_smoke_verifies_complete_exact_zero_study_and_canonical_manifests(
    tmp_path: Path,
) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )

    result_sha256 = _verify_study_fixture(document, definition_bytes, run_directories, metrics)

    assert result_sha256 == _canonical_document_sha256(document)


@pytest.mark.parametrize(
    "relative,manifest_key",
    [
        ("outputs/opportunity-summary.json", "opportunity_summary_sha256"),
        ("outputs/attention-summary.json", "attention_summary_sha256"),
    ],
)
def test_smoke_refuses_noncanonical_attention_source_summary(
    tmp_path: Path, relative: str, manifest_key: str
) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    run_directory = run_directories[0]
    summary_path = run_directory / relative
    summary_path.write_bytes(b"not-json")
    digest = sha256(b"not-json").hexdigest()
    manifest_path = run_directory / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[manifest_key] = digest
    manifest_content = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    manifest_path.write_bytes(manifest_content)
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    pair = pairs[0]
    assert isinstance(pair, dict)
    for arm_name in ("control", "treatment"):
        arm = pair[arm_name]
        assert isinstance(arm, dict)
        arm[manifest_key] = digest
        arm["manifest_sha256"] = sha256(manifest_content).hexdigest()

    with pytest.raises(SystemExit):
        _verify_study_fixture(document, definition_bytes, run_directories, metrics)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document["pairs"][0]["scalars"].pop(),  # type: ignore[index,union-attr]
        lambda document: document.pop("opportunity_model_id"),
        lambda document: document["pairs"][0]["control"].update(  # type: ignore[index,union-attr]
            {"opportunity_count": 3}
        ),
    ],
    ids=("missing-observation", "missing-provenance", "manifest-receipt-mismatch"),
)
def test_smoke_refuses_incomplete_or_cross_boundary_study_evidence(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], object]
) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    corrupted = copy.deepcopy(document)
    mutate(corrupted)

    with pytest.raises(SystemExit):
        _verify_study_fixture(corrupted, definition_bytes, run_directories, metrics)


def test_smoke_refuses_study_response_metrics_hash_not_seen_directly(tmp_path: Path) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    verified_hashes = _verified_metrics_hashes(metrics, run_directories)
    verified_hashes[0] = "0" * 64

    with pytest.raises(SystemExit):
        smoke_release._verify_spatial_aa_study(
            document,
            definition_bytes=definition_bytes,
            run_directories=run_directories,
            response_metrics=metrics,
            response_metrics_sha256=verified_hashes,
        )


def test_smoke_refuses_cross_seed_response_assumption_drift(tmp_path: Path) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    changed_assumptions = "f" * 64
    metrics[1]["response_assumption_structure_sha256"] = changed_assumptions
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    pair = pairs[1]
    assert isinstance(pair, dict)
    for arm_name in ("control", "treatment"):
        arm = pair[arm_name]
        assert isinstance(arm, dict)
        response = arm["response"]
        assert isinstance(response, dict)
        response["response_assumption_structure_sha256"] = changed_assumptions
        response["response_metrics_sha256"] = _canonical_document_sha256(metrics[1])

    with pytest.raises(SystemExit):
        _verify_study_fixture(document, definition_bytes, run_directories, metrics)


@pytest.mark.parametrize("field", ("value", "numerator"))
def test_smoke_refuses_negative_zero_in_aa_observations(tmp_path: Path, field: str) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    pair = pairs[0]
    assert isinstance(pair, dict)
    scalars = pair["scalars"]
    assert isinstance(scalars, list)
    scalar = next(
        row
        for row in scalars
        if isinstance(row, dict)
        and isinstance(row.get("control"), dict)
        and row["control"].get("value") == 0.0
        and row["control"].get("numerator") == 0.0
    )
    for arm in ("control", "treatment"):
        observation = scalar[arm]
        assert isinstance(observation, dict)
        observation[field] = -0.0

    with pytest.raises(SystemExit):
        _verify_study_fixture(document, definition_bytes, run_directories, metrics)


@pytest.mark.parametrize(
    "target,field",
    [
        ("scalar", "delta"),
        ("statistic", "mean_paired_difference"),
        ("statistic", "sample_standard_deviation"),
        ("statistic", "median_paired_difference"),
        ("statistic", "bootstrap_ci_low"),
        ("statistic", "bootstrap_ci_high"),
    ],
)
def test_smoke_refuses_negative_zero_in_every_exact_zero_aa_result(
    tmp_path: Path, target: str, field: str
) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    if target == "scalar":
        pairs = document["pairs"]
        assert isinstance(pairs, list)
        scalars = pairs[0]["scalars"]
        assert isinstance(scalars, list)
        scalars[0][field] = -0.0
    else:
        statistics = document["statistics"]
        assert isinstance(statistics, list)
        statistics[0][field] = -0.0

    with pytest.raises(SystemExit):
        _verify_study_fixture(document, definition_bytes, run_directories, metrics)


def test_smoke_refuses_nonzero_aa_scalar(tmp_path: Path) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    scalars = pairs[0]["scalars"]
    assert isinstance(scalars, list)
    scalars[0]["delta"] = 0.25

    with pytest.raises(SystemExit):
        _verify_study_fixture(document, definition_bytes, run_directories, metrics)


def test_smoke_refuses_scalar_source_not_declared_by_its_key(tmp_path: Path) -> None:
    _result, document, definition_bytes, run_directories, metrics = _full_spatial_study_fixture(
        tmp_path
    )
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    scalars = pairs[0]["scalars"]
    assert isinstance(scalars, list)
    scalar = scalars[0]
    assert isinstance(scalar, dict)
    for arm in ("control", "treatment"):
        observation = scalar[arm]
        assert isinstance(observation, dict)
        observation["source_artifacts"] = ["outputs/spatial-responses.jsonl"]

    with pytest.raises(SystemExit):
        _verify_study_fixture(document, definition_bytes, run_directories, metrics)


def _spatial_report_receipt(content: bytes, study: dict[str, object]) -> dict[str, object]:
    definition_sha256 = study["study_definition_sha256"]
    assert isinstance(definition_sha256, str)
    return {
        "schema_version": 1,
        "format_id": "spatial-study-html-v1",
        "claim_scope": "synthetic-study-not-observed-or-causal-effect",
        "study_id": "wheel-aa-study",
        "study_definition_sha256": definition_sha256,
        "study_result_sha256": _canonical_document_sha256(study),
        "report_path": "city-reports/wheel-aa-study.html",
        "report_sha256": sha256(content).hexdigest(),
        "report_bytes": len(content),
    }


def _write_spatial_report(root: Path, content: bytes) -> dict[str, object]:
    report = root / "city-reports" / "wheel-aa-study.html"
    report.parent.mkdir(parents=True)
    report.write_bytes(content)
    return {"report": report}


def _verify_report_fixture(root: Path, content: bytes, study: dict[str, object]) -> bytes:
    _write_spatial_report(root, content)
    receipt = _spatial_report_receipt(content, study)
    return smoke_release._verify_spatial_report(
        root,
        receipt,
        study_definition_sha256=receipt["study_definition_sha256"],  # type: ignore[arg-type]
        study_result_sha256=receipt["study_result_sha256"],  # type: ignore[arg-type]
        study=study,
    )


def test_smoke_verifies_contract_complete_static_report(tmp_path: Path) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)

    verified = _verify_report_fixture(tmp_path / "publication", content, study)

    assert verified == content


@pytest.mark.parametrize(
    "needle,replacement",
    [
        (b"</main>", b"</main><script>alert(1)</script>"),
        (
            b'<meta name="viewport"',
            b'<meta http-equiv="refresh" content="0;url=https://example.invalid">\n'
            b'<meta name="viewport"',
        ),
        (
            b"Synthetic / exploratory / unobserved / non-causal / not sales",
            b"Study result",
        ),
        (b"<tbody><tr>", b"<tbody>"),
        (b'<article class="pair"', b'<article class="removed-pair"'),
        (b"</footer>", b"private-provider-key person-001 raw_response</footer>"),
        (
            b"</footer>",
            b"coffee outdoors work price_sensitivity Price sensitivity</footer>",
        ),
        (
            b'<main id="main" tabindex="-1">',
            b'<main id="main" tabindex="-1" data-private="private-provider-key person-001">',
        ),
        (
            b'<a class="skip-link" href="#main"',
            b'<a class="skip-link" href="https://example.invalid" href="#main"',
        ),
        (
            b'<p class="scope">',
            b'<p class="scope" hidden aria-hidden="true">',
        ),
        (
            b'<main id="main" tabindex="-1">',
            b'<main id="main" tabindex="-1" popover>',
        ),
        (b'<dl class="ledger">', b'<dl class="ledger skip-link">'),
    ],
    ids=(
        "executable-element",
        "second-http-equiv",
        "missing-prominent-disclosure",
        "missing-statistic-row",
        "missing-pair-provenance",
        "private-markers",
        "raw-response-profile-markers",
        "private-attribute-markers",
        "duplicate-link-attribute",
        "hidden-disclosure",
        "popover-main",
        "offscreen-ledger",
    ),
)
def test_smoke_refuses_insecure_or_semantically_incomplete_report(
    tmp_path: Path, needle: bytes, replacement: bytes
) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    assert needle in content
    corrupted = content.replace(needle, replacement, 1)

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


@pytest.mark.parametrize(
    "needle,replacement",
    [
        (
            b'<main id="main" tabindex="-1">',
            b'<main id="main" tabindex="-1" hidden>',
        ),
        (
            b'<section aria-labelledby="protocol-heading">',
            b'<section aria-labelledby="protocol-heading"><template>',
        ),
        (
            b'<section aria-labelledby="protocol-heading">',
            b'<section class="skip-link" aria-labelledby="protocol-heading">',
        ),
    ],
    ids=("hidden-main", "template-wrapped-protocol", "offscreen-protocol"),
)
def test_smoke_refuses_required_report_evidence_hidden_from_browsers(
    tmp_path: Path, needle: bytes, replacement: bytes
) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    assert needle in content
    corrupted = content.replace(needle, replacement, 1)
    if b"<template>" in replacement:
        section_end = corrupted.index(b"</section>\n", corrupted.index(replacement))
        corrupted = corrupted[:section_end] + b"</template>" + corrupted[section_end:]

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


def test_smoke_refuses_required_report_evidence_inside_a_replaced_element(
    tmp_path: Path,
) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    marker = b'<section aria-labelledby="protocol-heading">'
    start = content.index(marker) + len(marker)
    end = content.index(b"</section>\n", start)
    corrupted = content[:start] + b"<meter>" + content[start:end] + b"</meter>" + content[end:]

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


def test_smoke_refuses_orphan_definition_term_hidden_from_semantic_capture(
    tmp_path: Path,
) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    needle = b"<dt>schema_version</dt>"
    assert needle in content
    corrupted = content.replace(
        needle,
        b"<dt>observed_sales_increase</dt>" + needle,
        1,
    )

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


@pytest.mark.parametrize(
    "needle,replacement",
    [
        (b"<dt>control</dt>", b"<dt>false_claim_control</dt>"),
        (
            b'<h2 id="protocol-heading">Study and protocol receipts</h2>',
            b'<h2 id="protocol-heading">Observed sales results</h2>',
        ),
        (
            b'<h2 id="protocol-heading">',
            b'<h2 id="missing-protocol-heading">',
        ),
    ],
    ids=("nested-ledger-label", "misleading-heading", "broken-heading-reference"),
)
def test_smoke_refuses_mislabelled_report_semantics(
    tmp_path: Path, needle: bytes, replacement: bytes
) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    assert needle in content
    corrupted = content.replace(needle, replacement, 1)

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


def test_smoke_refuses_csp_moved_after_style_despite_spoofing_comment(tmp_path: Path) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    marker = b'<meta http-equiv="Content-Security-Policy"'
    start = content.index(marker)
    end = content.index(b">\n", start) + 2
    csp = content[start:end]
    corrupted = content[:start] + content[end:]
    corrupted = corrupted.replace(b"<style>", b"<!-- Content-Security-Policy -->\n<style>", 1)
    corrupted = corrupted.replace(b"</style>\n", b"</style>\n" + csp, 1)

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


def test_smoke_refuses_csp_and_style_outside_the_document_head(tmp_path: Path) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    csp_start = content.index(b'<meta http-equiv="Content-Security-Policy"')
    csp_end = content.index(b">\n", csp_start) + 2
    csp = content[csp_start:csp_end]
    style_start = content.index(b"<style>")
    style_end = content.index(b"</style>\n", style_start) + len(b"</style>\n")
    style = content[style_start:style_end]
    corrupted = content[:csp_start] + content[csp_end:style_start] + content[style_end:]
    corrupted = corrupted.replace(b"<body>\n", b"<body>\n" + csp + style, 1)

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


@pytest.mark.parametrize("section_id", ("protocol-heading", "city-heading", "reproduction-heading"))
def test_smoke_refuses_missing_public_evidence_section(tmp_path: Path, section_id: str) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    start = content.index(f'<section aria-labelledby="{section_id}">'.encode())
    end = content.index(b"</section>\n", start) + len(b"</section>\n")
    corrupted = content[:start] + content[end:]

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


def test_smoke_refuses_extra_statistic_source_not_in_study(tmp_path: Path) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    needle = b"</bdi></li></ul></th>\n<td>2</td>"
    replacement = (
        b'</bdi></li><li><bdi dir="auto">outputs/bogus.jsonl</bdi></li></ul></th>\n<td>2</td>'
    )
    assert needle in content
    corrupted = content.replace(needle, replacement, 1)

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


def test_smoke_refuses_duplicate_pair_article_even_with_identical_receipts(tmp_path: Path) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    start = content.index(b'<article class="pair"')
    end = content.index(b"</article>", start) + len(b"</article>")
    article = content[start:end]
    corrupted = content[:end] + article + content[end:]

    with pytest.raises(SystemExit):
        _verify_report_fixture(tmp_path / "publication", corrupted, study)


def test_smoke_refuses_report_directory_symlink(tmp_path: Path) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    root = tmp_path / "publication"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "wheel-aa-study.html").write_bytes(content)
    try:
        (root / "city-reports").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")
    receipt = _spatial_report_receipt(content, study)

    with pytest.raises(SystemExit):
        smoke_release._verify_spatial_report(
            root,
            receipt,
            study_definition_sha256=study["study_definition_sha256"],  # type: ignore[arg-type]
            study_result_sha256=_canonical_document_sha256(study),
            study=study,
        )


def test_smoke_refuses_report_directory_windows_reparse_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, study, _definition, _runs, _metrics = _full_spatial_study_fixture(tmp_path / "sources")
    content = render_spatial_study_html(result)
    root = tmp_path / "publication"
    _write_spatial_report(root, content)
    receipt = _spatial_report_receipt(content, study)
    report_directory = root / "city-reports"
    real_lstat = Path.lstat

    def reparse_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        status = real_lstat(path)
        if path == report_directory:
            return SimpleNamespace(
                st_mode=status.st_mode,
                st_dev=status.st_dev,
                st_ino=status.st_ino,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            )
        return status

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    with pytest.raises(SystemExit):
        smoke_release._verify_spatial_report(
            root,
            receipt,
            study_definition_sha256=study["study_definition_sha256"],  # type: ignore[arg-type]
            study_result_sha256=_canonical_document_sha256(study),
            study=study,
        )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda document: document.update(trace_sha256="f" * 64),
        lambda document: document.update(raw_response="private-provider-material"),
        _mutate_city_run_stage_count,
    ),
    ids=("divergent-field", "unexpected-private-field", "divergent-count-summary"),
)
def test_smoke_refuses_invalid_city_run_stdout_at_the_manifest_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, object]], object],
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    root = scratch / "city-output"
    _result, study, _definition, run_directories, _metrics = _full_spatial_study_fixture(root)
    manifests = {
        seed: json.loads((directory / "run.json").read_text(encoding="utf-8"))
        for seed, directory in run_directories.items()
    }
    city = study["city"]
    assert isinstance(city, dict)

    def run_child(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        arguments = command[1:]
        if arguments[:3] != ["--format", "json", "city-run"]:
            raise AssertionError("invalid city-run receipt was not refused at its boundary")
        seed = int(arguments[arguments.index("--seed") + 1])
        manifest = manifests[seed]
        document = _city_run_receipt(manifest, run_directories[seed])
        if seed == 0:
            mutate(document)
        return subprocess.CompletedProcess(command, 0, json.dumps(document) + "\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)
    manifest = manifests[0]

    with pytest.raises(SystemExit):
        smoke_release._certify_spatial_study(
            Path("adlife"),
            scratch=scratch,
            places_path=scratch / "places.json",
            spatial_path=scratch / "campaign.json",
            response_path=scratch / "response.json",
            catalog_sha256=manifest["city_sha256"],
            place_set_sha256=None,
            scenario_sha256=manifest["scenario_sha256"],
            response_input_sha256=manifest["response_input_sha256"],
        )


def test_smoke_certifies_two_seed_response_study_report_and_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    root = scratch / "city-output"
    result, study, _definition, run_directories, metrics = _full_spatial_study_fixture(root)
    manifests = {
        seed: json.loads((directory / "run.json").read_text(encoding="utf-8"))
        for seed, directory in run_directories.items()
    }
    city = study["city"]
    assert isinstance(city, dict)
    commands: list[tuple[list[str], dict[str, object]]] = []
    report_verifications = 0

    real_verify_report = smoke_release._verify_spatial_report

    def verify_report(
        report_root: Path,
        receipt: dict[str, object],
        *,
        study_definition_sha256: str,
        study_result_sha256: str,
        study: dict[str, object],
    ) -> bytes:
        nonlocal report_verifications
        report_verifications += 1
        return real_verify_report(
            report_root,
            receipt,
            study_definition_sha256=study_definition_sha256,
            study_result_sha256=study_result_sha256,
            study=study,
        )

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        arguments = command[1:]
        commands.append((arguments, kwargs))
        if arguments[:3] == ["--format", "json", "city-run"]:
            seed = int(arguments[arguments.index("--seed") + 1])
            manifest = manifests[seed]
            document = _city_run_receipt(manifest, run_directories[seed])
            return subprocess.CompletedProcess(command, 0, json.dumps(document) + "\n", "")
        if "city-metrics" in arguments:
            run_id = arguments[arguments.index("city-metrics") + 2]
            seed = int(run_id.rsplit("-", 1)[1])
            return subprocess.CompletedProcess(command, 0, json.dumps(metrics[seed]) + "\n", "")
        if "city-study" in arguments:
            return subprocess.CompletedProcess(command, 0, json.dumps(study) + "\n", "")
        if "city-report" in arguments:
            report = root / "city-reports" / "wheel-aa-study.html"
            if report.exists():
                error = {
                    "error": {
                        "exit_code": 3,
                        "type": "CommandError",
                        "message": "report destination already exists",
                    }
                }
                assert kwargs.get("expected_exit") == 3
                return subprocess.CompletedProcess(
                    command,
                    3,
                    json.dumps(error, separators=(",", ":")) + "\n",
                    "error: report destination already exists\n",
                )
            content = render_spatial_study_html(result)
            report.parent.mkdir()
            report.write_bytes(content)
            receipt = _spatial_report_receipt(content, study)
            return subprocess.CompletedProcess(command, 0, json.dumps(receipt) + "\n", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(smoke_release, "_run", run_child)
    monkeypatch.setattr(smoke_release, "_verify_spatial_report", verify_report)
    manifest = manifests[0]

    smoke_release._certify_spatial_study(
        Path("adlife"),
        scratch=scratch,
        places_path=scratch / "places.json",
        spatial_path=scratch / "campaign.json",
        response_path=scratch / "response.json",
        catalog_sha256=manifest["city_sha256"],
        place_set_sha256=None,
        scenario_sha256=manifest["scenario_sha256"],
        response_input_sha256=manifest["response_input_sha256"],
    )

    arguments = [item[0] for item in commands]
    assert sum("city-run" in command for command in arguments) == 2
    assert sum("city-metrics" in command for command in arguments) == 2
    assert sum("city-study" in command for command in arguments) == 1
    assert sum("city-report" in command for command in arguments) == 2
    assert report_verifications == 2
    assert all(
        command[-2:] == ["--layer", "response"]
        for command in arguments
        if "city-metrics" in command
    )
    assert {kwargs.get("network_guard_token") for _args, kwargs in commands} == {
        "spatial-run-0",
        "spatial-run-1",
        "spatial-metrics-0",
        "spatial-metrics-1",
        "spatial-study",
        "spatial-report",
        "spatial-report-conflict",
    }


def test_smoke_subprocess_environment_refuses_source_checkout_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "untrusted-checkout/src")
    monkeypatch.setenv("PYTHONHOME", "untrusted-python-home")
    monkeypatch.setenv("PYTHONUSERBASE", "untrusted-user-base")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "untrusted-launcher")
    monkeypatch.setenv("VIRTUAL_ENV", "untrusted-active-environment")
    monkeypatch.setenv("CONDA_PREFIX", "untrusted-conda")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted-proxy.invalid")
    monkeypatch.setenv("ADLIFE_API_KEY", "secret-value")
    monkeypatch.setenv("ADLIFE_DEBUG", "1")
    monkeypatch.setenv("LD_PRELOAD", "untrusted-library")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "untrusted-library")
    monkeypatch.setenv("UV_OFFLINE", "1")

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs.get("env")
        assert isinstance(environment, dict)
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "strict"
        assert "PYTHONPATH" not in environment
        assert "PYTHONHOME" not in environment
        assert "PYTHONUSERBASE" not in environment
        assert "__PYVENV_LAUNCHER__" not in environment
        assert "VIRTUAL_ENV" not in environment
        assert "CONDA_PREFIX" not in environment
        assert "HTTPS_PROXY" not in environment
        assert "ADLIFE_API_KEY" not in environment
        assert "ADLIFE_DEBUG" not in environment
        assert "LD_PRELOAD" not in environment
        assert "DYLD_INSERT_LIBRARIES" not in environment
        assert environment["PYTHONNOUSERSITE"] == "1"
        assert environment["PYTHONSAFEPATH"] == "1"
        assert environment["PYTHONUTF8"] == "1"
        assert environment["PYTHONIOENCODING"] == "utf-8"
        assert environment["UV_OFFLINE"] == "1"
        return subprocess.CompletedProcess(command, 0, "isolated", "")

    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)

    assert smoke_release._run(["child-python", "--version"]).stdout == "isolated"


def test_smoke_subprocess_environment_scrubs_case_insensitive_names_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_release.os,
        "environ",
        {
            "PythonPath": "shadow-checkout",
            "PythonHome": "shadow-runtime",
            "PythonOptimize": "2",
            "service_TOKEN": "token",
            "service_SECRET": "secret",
            "service_PASSWORD": "password",
            "service_CREDENTIAL": "credential",
            "API_KEY_21ST": "provider-key",
            "nested_vendor_api_key_value": "nested-provider-key",
            "AWS_ACCESS_KEY_ID": "cloud-access-key",
            "AWS_SECRET_ACCESS_KEY": "cloud-secret-key",
            "vendor_client_secret_nested": "nested-client-secret",
            "GOOGLE_APPLICATION_CREDENTIALS": "credential-file.json",
            "https_proxy": "http://proxy.invalid",
            "UV_INDEX_URL": "https://user:password@registry.invalid/simple",
            "uv_extra_index_url": "https://extra.invalid/simple",
            "PIP_INDEX_URL": "https://user:password@registry.invalid/simple",
            "pip_extra_index_url": "https://extra.invalid/simple",
            "SSH_AUTH_SOCK": "agent.sock",
            "GIT_ASKPASS": "credential-helper",
            "UV_KEYRING_PROVIDER": "subprocess",
            "UV_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "SAFE_SETTING": "preserved",
        },
    )

    environment = smoke_release._subprocess_environment()

    assert (
        not {
            "PythonPath",
            "PythonHome",
            "PythonOptimize",
            "service_TOKEN",
            "service_SECRET",
            "service_PASSWORD",
            "service_CREDENTIAL",
            "API_KEY_21ST",
            "nested_vendor_api_key_value",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "vendor_client_secret_nested",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "https_proxy",
            "UV_INDEX_URL",
            "uv_extra_index_url",
            "PIP_INDEX_URL",
            "pip_extra_index_url",
            "SSH_AUTH_SOCK",
            "GIT_ASKPASS",
            "UV_KEYRING_PROVIDER",
        }
        & environment.keys()
    )
    assert environment["UV_OFFLINE"] == "1"
    assert environment["TOKENIZERS_PARALLELISM"] == "false"
    assert environment["SAFE_SETTING"] == "preserved"


def test_smoke_installer_environment_preserves_only_package_registry_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_release.os,
        "environ",
        {
            "PythonPath": "shadow-checkout",
            "HTTPS_PROXY": "http://registry-proxy.invalid",
            "no_proxy": "127.0.0.1,localhost",
            "UV_INDEX_URL": "https://registry.invalid/simple",
            "UV_EXTRA_INDEX_URL": "https://extra.invalid/simple",
            "UV_INDEX_PRIVATE_USERNAME": "registry-user",
            "UV_INDEX_PRIVATE_PASSWORD": "registry-password",
            "PIP_INDEX_URL": "https://registry.invalid/simple",
            "PIP_EXTRA_INDEX_URL": "https://extra.invalid/simple",
            "PIP_TRUSTED_HOST": "registry.invalid",
            "REGISTRY_TOKEN": "registry-token",
            "ADLIFE_API_KEY": "provider-key",
            "API_KEY_21ST": "provider-key",
            "AWS_ACCESS_KEY_ID": "cloud-access-key",
            "AWS_SECRET_ACCESS_KEY": "cloud-secret-key",
            "SSH_AUTH_SOCK": "agent.sock",
            "GIT_ASKPASS": "credential-helper",
            "UV_KEYRING_PROVIDER": "subprocess",
            "SAFE_SETTING": "preserved",
        },
    )

    environment = smoke_release._subprocess_environment(allow_network_environment=True)

    assert "PythonPath" not in environment
    assert environment["HTTPS_PROXY"] == "http://registry-proxy.invalid"
    assert environment["no_proxy"] == "127.0.0.1,localhost"
    assert environment["UV_INDEX_URL"] == "https://registry.invalid/simple"
    assert environment["UV_EXTRA_INDEX_URL"] == "https://extra.invalid/simple"
    assert environment["UV_INDEX_PRIVATE_USERNAME"] == "registry-user"
    assert environment["UV_INDEX_PRIVATE_PASSWORD"] == "registry-password"
    assert environment["PIP_INDEX_URL"] == "https://registry.invalid/simple"
    assert environment["PIP_EXTRA_INDEX_URL"] == "https://extra.invalid/simple"
    assert environment["PIP_TRUSTED_HOST"] == "registry.invalid"
    assert environment["SAFE_SETTING"] == "preserved"
    assert (
        not {
            "REGISTRY_TOKEN",
            "ADLIFE_API_KEY",
            "API_KEY_21ST",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "SSH_AUTH_SOCK",
            "GIT_ASKPASS",
            "UV_KEYRING_PROVIDER",
        }
        & environment.keys()
    )


def test_smoke_json_contract_refuses_success_diagnostics() -> None:
    completed = subprocess.CompletedProcess(["adlife"], 0, "{}\n", "unsafe warning\n")

    with pytest.raises(SystemExit):
        smoke_release._expect_json(completed, "clean JSON")


def test_smoke_json_contract_refuses_extra_stdout_lines() -> None:
    completed = subprocess.CompletedProcess(["adlife"], 0, "{}\n\n", "")

    with pytest.raises(SystemExit):
        smoke_release._expect_json(completed, "one JSON line")


def test_smoke_offline_doctor_requires_every_successful_public_check() -> None:
    document: dict[str, object] = {
        "checks": [
            {"name": name, "ok": True, "detail": f"{name} ok"}
            for name in (
                "package-version",
                "python-version",
                "resources",
                "writable",
                "sqlite-json1",
                "utf-8",
                "providers",
                "offline",
            )
        ],
        "all_ok": True,
    }
    smoke_release._verify_offline_doctor(document)
    document["all_ok"] = False

    with pytest.raises(SystemExit):
        smoke_release._verify_offline_doctor(document)


@pytest.mark.parametrize("digest", ("A" * 64, "g" * 64, "0" * 63))
def test_smoke_sha256_contract_requires_exact_lowercase_hex(digest: str) -> None:
    with pytest.raises(SystemExit):
        smoke_release._expect_sha256({"digest": digest}, "digest", "hash receipt")


@pytest.mark.parametrize("value", (-0.0, 0, False, float("nan"), float("inf")))
def test_smoke_exact_zero_contract_refuses_negative_zero_and_non_floats(value: object) -> None:
    with pytest.raises(SystemExit):
        smoke_release._finite_float(value, "A/A delta")


def test_smoke_network_guard_blocks_socket_operations(
    tmp_path: Path,
) -> None:
    venv = tmp_path / "venv"
    created = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    python = venv / scripts / ("python.exe" if sys.platform == "win32" else "python")

    guard = smoke_release._install_network_guard(python, cwd=tmp_path)
    probe = (
        "import builtins,sys;"
        "assert builtins.__adlife_smoke_network_guard__;"
        "assert builtins.__adlife_smoke_network_guard_self_test__;"
        "\nsys.audit('socket.connect', None, ('127.0.0.1', 1))"
        "\nsys.audit('socket.sendto', None, ('::1', 1))"
        "\nsys.audit('socket.sendmsg', None, ('127.0.0.1', 1))"
        "\ntry:\n sys.audit('socket.connect', None, ('203.0.113.1', 443))"
        "\nexcept RuntimeError:\n pass"
        "\nelse:\n raise AssertionError('external connection was not blocked')"
        "\ntry:\n sys.audit('socket.getaddrinfo', 'example.invalid', 443, 0, 0, 0)"
        "\nexcept RuntimeError:\n pass"
        "\nelse:\n raise AssertionError('DNS lookup was not blocked')"
        "\nfor event,args in ("
        "('socket.gethostbyname',('example.invalid',)),"
        "('socket.gethostbyname_ex',('example.invalid',)),"
        "('socket.gethostbyaddr',('203.0.113.1',)),"
        "('socket.getnameinfo',(('203.0.113.1',443),0)),"
        "('socket.sendmsg',(None,('203.0.113.1',443)))):"
        "\n try:\n  sys.audit(event,*args)"
        "\n except RuntimeError:\n  pass"
        "\n else:\n  raise AssertionError(f'{event} was not blocked')"
        "\nprint('blocked')"
    )
    completed = smoke_release._run(
        [str(python), "-I", "-c", probe],
        cwd=tmp_path,
        network_guard_token="guard-probe",
    )

    assert completed.stdout == "blocked\n"
    assert guard.read_text(encoding="utf-8").endswith("\n")
    log = guard.with_name("adlife-smoke-network-guard.log").read_text(encoding="utf-8")
    assert "guard-probe\t" in log


def test_smoke_network_guard_log_requires_each_declared_child_once(tmp_path: Path) -> None:
    guard = tmp_path / "sitecustomize.py"
    guard.write_text("# guard\n", encoding="utf-8")
    adlife = tmp_path / "adlife.exe"
    viewer = tmp_path / "viewer.py"
    adlife.write_text("", encoding="utf-8")
    viewer.write_text("", encoding="utf-8")
    log = guard.with_name("adlife-smoke-network-guard.log")
    log.write_text(
        f"first\t{adlife}\nsecond\t{adlife}\nviewer\t{viewer}\n",
        encoding="utf-8",
    )

    smoke_release._verify_network_guard_log(
        guard,
        adlife=adlife,
        viewer_probe=viewer,
        required_tokens=("first", "second", "viewer"),
        viewer_token="viewer",
    )

    log.write_text(
        f"first\t{adlife}\nfirst\t{adlife}\nviewer\t{viewer}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="unique"):
        smoke_release._verify_network_guard_log(
            guard,
            adlife=adlife,
            viewer_probe=viewer,
            required_tokens=("first", "second", "viewer"),
            viewer_token="viewer",
        )

    log.write_text(
        f"first\t{viewer}\nsecond\t{adlife}\nviewer\t{viewer}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="wrong child"):
        smoke_release._verify_network_guard_log(
            guard,
            adlife=adlife,
            viewer_probe=viewer,
            required_tokens=("first", "second", "viewer"),
            viewer_token="viewer",
        )

    log.write_text(
        f"first\t{adlife}\nsecond\t{adlife}\nviewer\t{viewer}\n\t{adlife}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="token"):
        smoke_release._verify_network_guard_log(
            guard,
            adlife=adlife,
            viewer_probe=viewer,
            required_tokens=("first", "second", "viewer"),
            viewer_token="viewer",
        )


def test_smoke_response_funnel_refuses_zero_updates_for_positive_responses() -> None:
    with pytest.raises(SystemExit):
        smoke_release._expect_response_funnel(
            response_count=1,
            state_update_count=0,
            noticed_count=1,
            label="city-run --city-id",
        )


def test_smoke_viewer_probe_checks_guard_before_installed_imports_and_metrics() -> None:
    source = smoke_release._VIEWER_PROBE_SOURCE

    assert source.index("\n_verify_guard()\n\nimport adlife") < source.index(
        "from adlife.city.analysis"
    )
    assert 'metrics_payload = await _json(client, "/api/spatial-metrics")' in source
    assert 'assert metrics_payload == metrics.model_dump(mode="json")' in source
    assert 'assert \'<meta name="adlife-api-base" content="/api">\' in index.text' in source
    assert 'assert "function validatedApiBase()" in javascript.text' in source
    assert 'assert "function apiPath(relative)" in javascript.text' in source
    assert "assert 'endpoint: \"/response-events\"' in javascript.text" in source


def test_smoke_viewer_probe_exercises_the_installed_workbench_lifecycle() -> None:
    source = smoke_release._VIEWER_PROBE_SOURCE

    for required in (
        "from adlife.city.workbench_web import create_city_workbench_app",
        "accepted = await workbench_client.post(",
        '"/api/jobs",',
        "await workbench_client.get(status_url)",
        'await workbench_client.get("/api/runs?offset=0&limit=100")',
        'await workbench_client.get("/assets/workbench.js")',
        'await workbench_client.get("/api/catalog/cities/fictional-grid-v2")',
        'await workbench_client.get("/runs/wheel-workbench")',
        'await workbench_client.get("/api/runs/wheel-workbench/meta")',
        '"/api/runs/wheel-workbench/workbench-input"',
        '"/api/runs/wheel-workbench/frame?minute=0"',
        'await workbench_client.get("/static/app.js")',
        'assert workbench_input.json()["settings"]["seed"] == "17"',
        'root / "city-runs" / "wheel-workbench" / "inputs" / "workbench.json"',
        '"seed": "17",',
        'accepted_settings = accepted_document["job"]["accepted_settings"]',
        'assert completed_job["accepted_settings"] == accepted_settings',
        '"workbench_run_schema_version": 7',
        '"workbench_input_view_verified": True',
        '"workbench_scoped_frame_verified": True',
        '"workbench_browser_asset_verified": True',
    ):
        assert required in source


def test_smoke_network_guard_refuses_to_replace_sitecustomize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_site = tmp_path / "venv" / "Lib" / "site-packages"
    child_site.mkdir(parents=True)
    existing = child_site / "sitecustomize.py"
    existing.write_text("# dependency-owned\n", encoding="utf-8")
    monkeypatch.setattr(
        smoke_release,
        "_child_site_packages",
        lambda python, **kwargs: child_site,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="sitecustomize"):
        smoke_release._install_network_guard(tmp_path / "venv" / "python")

    assert existing.read_text(encoding="utf-8") == "# dependency-owned\n"


def test_smoke_discovers_child_purelib_with_the_child_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "venv"
    python = environment / "Scripts" / "python.exe"
    purelib = environment / "Lib" / "site-packages"
    purelib.mkdir(parents=True)

    def run_child(
        command: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert command == [
            str(python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ]
        assert cwd == tmp_path
        return subprocess.CompletedProcess(command, 0, f"{purelib}\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)

    assert smoke_release._child_site_packages(python, cwd=tmp_path) == purelib.resolve()


@pytest.mark.parametrize(
    "boundary",
    ("city-metrics", "city-compare", "city-replay", "installed city viewer"),
)
def test_smoke_refuses_source_mutation_after_each_read_only_boundary(
    boundary: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_directory = tmp_path / "city-runs" / "study"
    run_directory.mkdir(parents=True)
    artifact = run_directory / "run.json"
    artifact.write_bytes(b"before\n")
    source_hashes = smoke_release._artifact_hashes(run_directory)
    artifact.write_bytes(b"after\n")

    with pytest.raises(SystemExit):
        smoke_release._expect_artifacts_unchanged(run_directory, source_hashes, boundary)

    assert boundary in capsys.readouterr().err


def test_smoke_artifact_snapshot_detects_an_added_empty_directory(tmp_path: Path) -> None:
    run_directory = tmp_path / "city-runs" / "study"
    run_directory.mkdir(parents=True)
    (run_directory / "run.json").write_bytes(b"before\n")
    source_hashes = smoke_release._artifact_hashes(run_directory)
    (run_directory / "unexpected-empty").mkdir()

    with pytest.raises(SystemExit):
        smoke_release._expect_artifacts_unchanged(
            run_directory, source_hashes, "empty-directory mutation"
        )


def test_smoke_artifact_snapshot_refuses_a_non_file_entry(tmp_path: Path) -> None:
    run_directory = tmp_path / "city-runs" / "study"
    run_directory.mkdir(parents=True)
    (run_directory / "run.json").write_bytes(b"before\n")
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (run_directory / "unsafe-link").symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    with pytest.raises(SystemExit):
        smoke_release._artifact_hashes(run_directory)


def test_smoke_subprocesses_do_not_create_console_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        expected = 0x08000000 if sys.platform == "win32" else 0
        assert kwargs.get("creationflags", 0) == expected
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(command, 0, "captured", "")

    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)
    assert smoke_release._run(["adlife", "--version"]).stdout == "captured"


def test_smoke_expected_exit_preserves_subprocess_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "untrusted-checkout/src")
    monkeypatch.setenv("ADLIFE_API_KEY", "private-provider-key")
    monkeypatch.setenv("ADLIFE_DEBUG", "1")
    monkeypatch.setenv("ADLIFE_SMOKE_GUARD_INVOCATION", "host-controlled")

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs.get("env")
        assert isinstance(environment, dict)
        assert "PYTHONPATH" not in environment
        assert "ADLIFE_API_KEY" not in environment
        assert "ADLIFE_DEBUG" not in environment
        assert environment["ADLIFE_SMOKE_GUARD_INVOCATION"] == "expected-refusal"
        assert kwargs["capture_output"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "strict"
        assert kwargs["timeout"] == 300
        expected_flag = 0x08000000 if sys.platform == "win32" else 0
        assert kwargs.get("creationflags", 0) == expected_flag
        return subprocess.CompletedProcess(
            command,
            3,
            '{"error":{"exit_code":3,"message":"already exists","type":"CommandError"}}\n',
            "error: already exists\n",
        )

    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)

    completed = smoke_release._run(
        ["adlife", "--format", "json", "city-report"],
        expected_exit=3,
        network_guard_token="expected-refusal",
    )

    assert completed.returncode == 3


def test_smoke_expected_error_json_is_not_accepted_by_success_parser() -> None:
    completed = subprocess.CompletedProcess(
        ["adlife"],
        3,
        '{"error":{"exit_code":3,"message":"already exists","type":"CommandError"}}\n',
        "error: already exists\n",
    )

    with pytest.raises(SystemExit):
        smoke_release._expect_json(completed, "city-report conflict")

    document = smoke_release._expect_error_json(
        completed,
        "city-report conflict",
        exit_code=3,
        message="already exists",
    )

    assert document["error"]["type"] == "CommandError"


def test_smoke_subprocess_flag_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("creationflags") == 0
        return subprocess.CompletedProcess(command, 0, "captured", "")

    monkeypatch.delattr(smoke_release.subprocess, "CREATE_NO_WINDOW", raising=False)
    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)

    assert smoke_release._run(["adlife", "--version"]).stdout == "captured"


def test_smoke_uses_the_running_supported_interpreter_without_fetching_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def record_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(smoke_release, "_uv_executable", lambda: "uv")
    monkeypatch.setattr(smoke_release, "_run", record_command)
    smoke_release._create_interpreter(tmp_path)
    assert commands == [["uv", "venv", "--python", sys.executable, str(tmp_path / "venv")]]


def test_smoke_cleans_workspace_and_returns_no_dead_artifact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    # Environment provisioning is external; run all CLI/report checks for real
    # with the already installed environment so this regression stays offline.
    monkeypatch.setattr(
        smoke_release, "_create_interpreter", lambda path: (Path(sys.executable), None)
    )
    monkeypatch.setattr(smoke_release, "_install_wheel", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        smoke_release,
        "_install_network_guard",
        lambda *args, **kwargs: workspace / "sitecustomize.py",
        raising=False,
    )
    monkeypatch.setattr(
        smoke_release,
        "_verify_installed_viewer",
        lambda *args, **kwargs: workspace / "verify-installed-city-viewer.py",
        raising=False,
    )
    monkeypatch.setattr(
        smoke_release,
        "_verify_network_guard_log",
        lambda *args, **kwargs: None,
    )

    result = smoke_release.smoke(tmp_path / "unused.whl")

    assert not workspace.exists()
    assert result is None


def test_smoke_cleans_workspace_when_installation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))

    def fail_provisioning(path: Path) -> None:
        raise RuntimeError("environment unavailable")

    monkeypatch.setattr(smoke_release, "_create_interpreter", fail_provisioning)
    with pytest.raises(RuntimeError, match="environment unavailable"):
        smoke_release.smoke(tmp_path / "unused.whl")
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("platform_name", "launcher_name"),
    (("linux", "adlife"), ("win32", "adlife.exe")),
)
def test_smoke_exercises_verified_catalog_run_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    launcher_name: str,
) -> None:
    """Catch a wheel smoke that omits any packaged-catalog consumer boundary."""
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    scratch = workspace / "scratch"
    calls: list[tuple[list[str], Path | None]] = []
    city_sha256 = "a" * 64
    place_sha256 = "c" * 64
    assignments_sha256 = "d" * 64
    scenario_sha256 = "e" * 64
    trace_sha256 = "b" * 64
    opportunity_stream_sha256 = "f" * 64
    opportunity_summary_sha256 = "1" * 64
    attention_stream_sha256 = "2" * 64
    attention_summary_sha256 = "3" * 64
    structure_sha256 = "4" * 64
    response_stream = b'{"event_type":"spatial.response"}\n'
    response_state = b'{"model_id":"spatial-response-state-v1"}\n'
    response_summary = b'{"model_id":"spatial-response-artifact-v1"}\n'
    response_stream_sha256 = sha256(response_stream).hexdigest()
    response_state_sha256 = sha256(response_state).hexdigest()
    response_summary_sha256 = sha256(response_summary).hexdigest()
    response_input_document = _expected_response_document(
        city_sha256=city_sha256,
        scenario_sha256=scenario_sha256,
    )
    response_input_canonical = json.dumps(
        response_input_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    response_input_sha256 = sha256(response_input_canonical).hexdigest()
    opportunity_counts = {
        "opportunity_count": 4,
        "roadside_billboard_count": 0,
        "mobile_feed_count": 4,
    }
    attention_counts = {
        "opportunity_count": 4,
        "impression_count": 4,
        "noticed_count": 2,
        "roadside_impression_count": 0,
        "roadside_noticed_count": 0,
        "phone_impression_count": 4,
        "phone_noticed_count": 2,
    }
    opportunity_receipt = {
        "numerator": 4,
        "denominator": 1,
        "value": 4.0,
        "source_artifacts": ["outputs/spatial-opportunities.jsonl"],
    }
    impression_receipt = {
        "numerator": 4,
        "denominator": 1,
        "value": 4.0,
        "source_artifacts": ["outputs/spatial-attention.jsonl"],
    }
    noticed_receipt = {
        "numerator": 2,
        "denominator": 1,
        "value": 2.0,
        "source_artifacts": ["outputs/spatial-attention.jsonl"],
    }
    metrics_document: dict[str, object] = {
        "model_id": "spatial-metrics-v1",
        "claim_scope": "synthetic-metrics-not-observed-outcomes",
        "source_run_schema_version": 6,
        "scenario_sha256": scenario_sha256,
        "city_sha256": city_sha256,
        "trace_sha256": trace_sha256,
        "opportunity_structure_sha256": structure_sha256,
        "overall": {
            "opportunity_count": opportunity_receipt,
            "impression_count": impression_receipt,
            "noticed_count": noticed_receipt,
        },
    }
    response_counts = {
        "schema_version": 1,
        "response_count": 2,
        "state_update_count": 2,
        "roadside_response_count": 0,
        "phone_response_count": 2,
        "campaign_count": 1,
        "final_state_count": 2,
    }

    monkeypatch.setattr(smoke_release.sys, "platform", platform_name)
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    monkeypatch.setattr(
        smoke_release,
        "_create_interpreter",
        lambda path: (workspace / "venv" / "Scripts" / "python.exe", None),
    )
    monkeypatch.setattr(smoke_release, "_install_wheel", lambda *args, **kwargs: None)
    study_certifications: list[dict[str, object]] = []

    def certify_study(adlife: Path, **kwargs: object) -> int:
        study_certifications.append({"adlife": adlife, **kwargs})
        guard_log = child_site / "adlife-smoke-network-guard.log"
        with guard_log.open("a", encoding="utf-8") as target:
            for token in smoke_release._SPATIAL_GUARD_TOKENS:
                target.write(f"{token}\t{adlife}\n")
        return 12_345

    monkeypatch.setattr(smoke_release, "_certify_spatial_study", certify_study)
    child_site = workspace / "venv" / "Lib" / "site-packages"
    child_site.mkdir(parents=True)
    installed_package = child_site / "adlife"
    installed_package.mkdir()
    installed_module = installed_package / "__init__.py"
    installed_module.write_text("", encoding="utf-8")
    artifact_hash_call_positions: list[int] = []
    real_artifact_hashes = smoke_release._artifact_hashes

    def record_artifact_hashes(directory: Path) -> dict[str, str]:
        artifact_hash_call_positions.append(len(calls))
        return real_artifact_hashes(directory)

    monkeypatch.setattr(smoke_release, "_artifact_hashes", record_artifact_hashes)

    def run_child(
        command: list[str], *, cwd: Path | None = None, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command[1:], cwd))
        arguments = command[1:]
        document: dict[str, object] = {}
        guard_log = child_site / "adlife-smoke-network-guard.log"
        if (child_site / "sitecustomize.py").is_file():
            entry = arguments[1] if len(arguments) > 1 and arguments[0] == "-I" else command[0]
            token = kwargs.get("network_guard_token", "")
            assert isinstance(token, str)
            with guard_log.open("a", encoding="utf-8") as target:
                target.write(f"{token}\t{entry}\n")
        if arguments[:2] == ["-I", "-c"]:
            return subprocess.CompletedProcess(command, 0, f"{child_site}\n", "")
        if arguments and arguments[0] == "-I":
            assert arguments == [
                "-I",
                str(scratch / "verify-installed-city-viewer.py"),
                str(scratch / "city-output"),
                "catalog-smoke",
                str(child_site),
            ]
            assert (child_site / "sitecustomize.py").is_file()
            document = {
                "run_schema_version": 6,
                "response_model_id": "spatial-response-v1",
                "claim_scope": "synthetic-response-not-observed-behavior",
                "summary_model_id": "spatial-response-artifact-v1",
                "state_model_id": "spatial-response-state-v1",
                "state_scope": "final-end-of-run-not-scrubbed-minute",
                "response_count": 2,
                "final_state_count": 2,
                "adlife_module_path": str(installed_module),
                "child_site_path": str(child_site),
                "module_origin_verified": True,
                "network_guard_verified": True,
                "ui_verified": True,
                "workbench_run_schema_version": 7,
                "workbench_sidecar_verified": True,
                "workbench_discovery_verified": True,
                "workbench_inspector_verified": True,
                "workbench_input_view_verified": True,
                "workbench_scoped_frame_verified": True,
                "workbench_browser_asset_verified": True,
            }
        elif arguments == ["--format", "json", "doctor", "--offline"]:
            document = {
                "checks": [
                    {"name": name, "ok": True, "detail": f"{name} ok"}
                    for name in (
                        "package-version",
                        "python-version",
                        "resources",
                        "writable",
                        "sqlite-json1",
                        "utf-8",
                        "providers",
                        "offline",
                    )
                ],
                "all_ok": True,
            }
        elif arguments[:3] == ["--format", "json", "run"]:
            document = {"status": "completed"}
        elif arguments[:3] == ["--format", "json", "report"]:
            report = scratch / "smoke-study" / "reports" / "smoke.html"
            report.parent.mkdir(parents=True)
            report.write_text("x" * 10_001, encoding="utf-8")
        elif arguments == ["--format", "json", "city-catalog", "list"]:
            document = {
                "cities": [
                    {
                        "city_id": "fictional-grid-v2",
                        "qualification": "fictional-fixture",
                        "pack_schema_version": 2,
                    }
                ]
            }
        elif arguments == [
            "--format",
            "json",
            "city-catalog",
            "show",
            "fictional-grid-v2",
        ]:
            document = {
                "city_id": "fictional-grid-v2",
                "qualification": "fictional-fixture",
                "pack_schema_version": 2,
                "pack_sha256": city_sha256,
            }
        elif arguments[:4] == ["--format", "json", "city-places", "validate"]:
            place_path = Path(arguments[4])
            place_document = json.loads(place_path.read_text(encoding="utf-8"))
            assert place_document["city_id"] == "fictional-grid-v2"
            assert place_document["city_sha256"] == city_sha256
            assert {place["kind"] for place in place_document["places"]} == {
                "home",
                "workplace",
                "leisure",
            }
            assert all(
                place["provenance"]["method"] == "operator-authored-fictional"
                for place in place_document["places"]
            )
            document = {
                "valid": True,
                "city_id": "fictional-grid-v2",
                "place_set_sha256": place_sha256,
            }
        elif arguments[:4] == ["--format", "json", "city-campaign", "validate"]:
            scenario_path = Path(arguments[4])
            scenario_document = json.loads(scenario_path.read_text(encoding="utf-8"))
            assert scenario_document["scenario_id"] == "clean-room-spatial"
            assert scenario_document["city_id"] == "fictional-grid-v2"
            assert scenario_document["city_sha256"] == city_sha256
            assert {item["channel"] for item in scenario_document["placements"]} == {
                "roadside-billboard",
                "mobile-feed",
            }
            document = {
                "valid": True,
                "scenario_id": "clean-room-spatial",
                "scenario_sha256": scenario_sha256,
                "city_id": "fictional-grid-v2",
                "city_sha256": city_sha256,
                "campaign_count": 1,
                "placement_count": 2,
                "billboard_count": 1,
                "phone_count": 1,
                "max_billboard_binding_error_meters": 0.0,
            }
        elif arguments[:3] == ["--format", "json", "city-run"]:
            response_path = Path(arguments[arguments.index("--spatial-response") + 1])
            assert json.loads(response_path.read_text(encoding="utf-8")) == response_input_document
            run_directory = scratch / "city-output" / "city-runs" / "catalog-smoke"
            (run_directory / "inputs").mkdir(parents=True)
            (run_directory / "outputs").mkdir()
            (run_directory / "run.json").write_text("manifest", encoding="utf-8")
            (run_directory / "inputs" / "city.json").write_text("city", encoding="utf-8")
            (run_directory / "outputs" / "spatial-attention.jsonl").write_text(
                "attention", encoding="utf-8"
            )
            (run_directory / "inputs" / "spatial-response.json").write_bytes(
                response_input_canonical + b"\n"
            )
            (run_directory / "outputs" / "spatial-responses.jsonl").write_bytes(response_stream)
            (run_directory / "outputs" / "response-state.json").write_bytes(response_state)
            (run_directory / "outputs" / "response-summary.json").write_bytes(response_summary)
            document = {
                "run_id": "catalog-smoke",
                "run_schema_version": 6,
                "city_id": "fictional-grid-v2",
                "city_sha256": city_sha256,
                "place_set_sha256": place_sha256,
                "place_assignments_sha256": assignments_sha256,
                "trace_sha256": trace_sha256,
                "scenario_sha256": scenario_sha256,
                "opportunity_stream_sha256": opportunity_stream_sha256,
                "opportunity_summary_sha256": opportunity_summary_sha256,
                "opportunity_stream_bytes": 1_234,
                "opportunity_count": 4,
                "opportunity_counts": opportunity_counts,
                "opportunity_claim_scope": "synthetic-opportunity-not-impression",
                "attention_model_id": "spatial-attention-v1",
                "attention_claim_scope": "synthetic-attention-not-observed-behavior",
                "attention_notice_probability": 0.5,
                "attention_stream_sha256": attention_stream_sha256,
                "attention_summary_sha256": attention_summary_sha256,
                "attention_stream_bytes": 2_468,
                "impression_count": 4,
                "noticed_count": 2,
                "attention_counts": attention_counts,
                "response_model_id": "spatial-response-v1",
                "response_claim_scope": "synthetic-response-not-observed-behavior",
                "response_input_sha256": response_input_sha256,
                "response_stream_sha256": response_stream_sha256,
                "response_state_sha256": response_state_sha256,
                "response_summary_sha256": response_summary_sha256,
                "response_stream_bytes": len(response_stream),
                "response_count": 2,
                "state_update_count": 2,
                "response_campaign_count": 1,
                "final_state_count": 2,
                "response_counts": response_counts,
                "frame_count": 1_440,
                "position_count": 2_880,
                "directory": str(scratch / "city-output" / "city-runs" / "catalog-smoke"),
            }
        elif arguments == [
            "--format",
            "json",
            "city-metrics",
            "city-output",
            "catalog-smoke",
        ]:
            document = metrics_document
        elif arguments == [
            "--format",
            "json",
            "city-compare",
            "city-output",
            "catalog-smoke",
            "catalog-smoke",
        ]:
            zero_series = {
                "opportunity_count": 0.0,
                "impression_count": 0.0,
                "noticed_count": 0.0,
                "opportunity_reach": 0.0,
                "impression_reach": 0.0,
                "noticed_reach": 0.0,
                "impression_frequency": 0.0,
                "notice_rate": 0.0,
            }
            document = {
                "model_id": "spatial-metrics-comparison-v1",
                "claim_scope": "synthetic-comparison-not-causal-or-observed-effect",
                "classification": "matched-opportunity-structure",
                "control_run_id": "catalog-smoke",
                "treatment_run_id": "catalog-smoke",
                "control_opportunity_structure_sha256": structure_sha256,
                "treatment_opportunity_structure_sha256": structure_sha256,
                "control": metrics_document,
                "treatment": metrics_document,
                "overall": zero_series,
                "channels": [
                    {"channel": "roadside", **zero_series},
                    {"channel": "mobile", **zero_series},
                ],
            }
        elif arguments == [
            "--format",
            "json",
            "city-replay",
            "city-output",
            "catalog-smoke",
        ]:
            document = {
                "run_id": "catalog-smoke",
                "identical": True,
                "city_sha256": city_sha256,
                "place_set_sha256": place_sha256,
                "place_assignments_sha256": assignments_sha256,
                "trace_sha256": trace_sha256,
                "scenario_sha256": scenario_sha256,
                "opportunity_stream_sha256": opportunity_stream_sha256,
                "opportunity_summary_sha256": opportunity_summary_sha256,
                "opportunity_stream_bytes": 1_234,
                "opportunity_count": 4,
                "opportunity_counts": opportunity_counts,
                "opportunity_claim_scope": "synthetic-opportunity-not-impression",
                "attention_model_id": "spatial-attention-v1",
                "attention_claim_scope": "synthetic-attention-not-observed-behavior",
                "attention_notice_probability": 0.5,
                "attention_stream_sha256": attention_stream_sha256,
                "attention_summary_sha256": attention_summary_sha256,
                "attention_stream_bytes": 2_468,
                "impression_count": 4,
                "noticed_count": 2,
                "attention_counts": attention_counts,
                "response_model_id": "spatial-response-v1",
                "response_claim_scope": "synthetic-response-not-observed-behavior",
                "response_input_sha256": response_input_sha256,
                "response_stream_sha256": response_stream_sha256,
                "response_state_sha256": response_state_sha256,
                "response_summary_sha256": response_summary_sha256,
                "response_stream_bytes": len(response_stream),
                "response_count": 2,
                "state_update_count": 2,
                "response_campaign_count": 1,
                "final_state_count": 2,
                "response_counts": response_counts,
                "frame_count": 1_440,
                "position_count": 2_880,
            }
        return subprocess.CompletedProcess(command, 0, json.dumps(document) + "\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)

    smoke_release.smoke(tmp_path / "unused.whl")

    invoked = [arguments for arguments, _cwd in calls]
    assert ["--format", "json", "city-catalog", "list"] in invoked
    assert [
        "--format",
        "json",
        "city-catalog",
        "show",
        "fictional-grid-v2",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-places",
        "validate",
        str(scratch / "fictional-grid-v2-places.json"),
        "--city-id",
        "fictional-grid-v2",
        "--agents",
        "2",
        "--days",
        "1",
        "--seed",
        "42",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-campaign",
        "validate",
        str(scratch / "fictional-grid-v2-spatial-campaign.json"),
        "--city-id",
        "fictional-grid-v2",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-run",
        "--city-id",
        "fictional-grid-v2",
        "--places",
        str(scratch / "fictional-grid-v2-places.json"),
        "--spatial-campaign",
        str(scratch / "fictional-grid-v2-spatial-campaign.json"),
        "--spatial-response",
        str(scratch / "fictional-grid-v2-spatial-response.json"),
        "--output-root",
        "city-output",
        "--run-id",
        "catalog-smoke",
        "--agents",
        "2",
        "--days",
        "1",
        "--seed",
        "42",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-replay",
        "city-output",
        "catalog-smoke",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-metrics",
        "city-output",
        "catalog-smoke",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-compare",
        "city-output",
        "catalog-smoke",
        "catalog-smoke",
    ] in invoked
    viewer_call_position = next(
        index
        for index, (arguments, _cwd) in enumerate(calls)
        if len(arguments) > 1 and arguments[0] == "-I" and arguments[1] != "-c"
    )
    boundary_positions = [
        next(index for index, (arguments, _cwd) in enumerate(calls) if marker in arguments) + 1
        for marker in ("city-metrics", "city-compare", "city-replay")
    ]
    boundary_positions.append(viewer_call_position + 1)
    assert len(artifact_hash_call_positions) == 5
    assert artifact_hash_call_positions[1:] == boundary_positions
    assert study_certifications == [
        {
            "adlife": workspace / "venv" / "Scripts" / launcher_name,
            "scratch": scratch,
            "places_path": scratch / "fictional-grid-v2-places.json",
            "spatial_path": scratch / "fictional-grid-v2-spatial-campaign.json",
            "response_path": scratch / "fictional-grid-v2-spatial-response.json",
            "catalog_sha256": city_sha256,
            "place_set_sha256": place_sha256,
            "scenario_sha256": scenario_sha256,
            "response_input_sha256": response_input_sha256,
        }
    ]
    assert all(cwd == scratch for _arguments, cwd in calls)
    assert not workspace.exists()


def test_offline_smoke_installs_exact_wheel_without_resolving_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def record_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(smoke_release, "_run", record_command)

    smoke_release._install_wheel(Path("child-python"), "uv", Path("adlife.whl"), no_deps=True)

    assert calls == [
        [
            "uv",
            "pip",
            "install",
            "--python",
            "child-python",
            "--quiet",
            "--no-deps",
            "adlife.whl",
        ]
    ]


def test_offline_smoke_exposes_only_locked_host_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    python = venv / "Scripts" / "python.exe"
    child_site = venv / "Lib" / "site-packages"
    child_site.mkdir(parents=True)
    host_site = tmp_path / "host" / "site-packages"
    host_site.mkdir(parents=True)

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, f"{child_site}\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)
    monkeypatch.setattr(smoke_release.site, "getsitepackages", lambda: [str(host_site)])

    smoke_release._expose_locked_dependencies(python)

    assert (child_site / "adlife-smoke-locked-dependencies.pth").read_text(
        encoding="utf-8"
    ) == f"{host_site.resolve()}\n"


def test_offline_smoke_refuses_child_site_outside_the_created_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    python = venv / "Scripts" / "python.exe"
    outside = tmp_path / "outside" / "site-packages"
    outside.mkdir(parents=True)

    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, f"{outside}\n", "")

    monkeypatch.setattr(smoke_release, "_run", run_child)

    with pytest.raises(RuntimeError, match="outside the smoke environment"):
        smoke_release._expose_locked_dependencies(python)
