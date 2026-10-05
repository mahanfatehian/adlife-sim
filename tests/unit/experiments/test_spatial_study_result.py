from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import BaseModel

from adlife.core.experiments import spatial_study
from adlife.core.simulation._validation import revalidate_model
from tests.integration.test_city_spatial_study import analyze, definition, make_runs


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("spatial-study-result")
    make_runs(root, response=True, same=False)
    return analyze(root, definition(response=True, same=False))


def validate(document):
    return spatial_study.SpatialStudyResult.model_validate(document)


def bypass_copy(model, **updates):
    # Deliberately skip DomainModel.model_copy's validation, as an untrusted caller can.
    return BaseModel.model_copy(model, update=updates)


def test_result_round_trip_and_identity_cached_validation(result):
    checked = revalidate_model(
        result, spatial_study.SpatialStudyResult, label="spatial study result"
    )
    assert checked is result
    assert spatial_study.SpatialStudyResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("study_definition_sha256", "0" * 64),
        ("seeds", (1, 0)),
        ("source_run_schema_version", 5),
        ("source_run_model_id", "invented-model"),
        ("package_version", "9.9.9"),
        ("python_version", "3.11.9"),
        ("days", 2),
        ("population_size", 3),
        ("control_scenario_sha256", "0" * 64),
        ("treatment_scenario_sha256", "0" * 64),
        ("control_response_input_sha256", "0" * 64),
        ("treatment_response_assumption_structure_sha256", "0" * 64),
        ("opportunity_matched_pair_count", 1),
        ("opportunity_confounded_pair_count", 1),
        ("response_assumption_matched_pair_count", 1),
        ("response_assumption_classification", "not-applicable"),
        ("opportunity_classification", "opportunity-confounded"),
        ("a_a_status", "not-applicable"),
        ("evidence_tier", "full-protocol-50-or-more-seeds"),
        ("bootstrap_resamples", 9999),
        ("bootstrap_confidence", 0.9),
        ("direction_threshold", 0.9),
        ("seed_protocol", "selected-seeds"),
    ],
)
def test_result_rejects_changed_root_provenance_and_verdicts(result, field, value):
    with pytest.raises(ValueError):
        revalidate_model(
            bypass_copy(result, **{field: value}), spatial_study.SpatialStudyResult, label="result"
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_sha256", "0" * 64),
        ("run_id", "changed-run"),
        ("scenario_sha256", "0" * 64),
        ("opportunity_stream_sha256", "0" * 64),
        ("opportunity_summary_sha256", "0" * 64),
        ("attention_stream_sha256", "0" * 64),
        ("attention_summary_sha256", "0" * 64),
        ("opportunity_stream_bytes", 1),
        ("attention_stream_bytes", 1),
        ("opportunity_count", 0),
        ("noticed_count", 0),
        ("opportunity_structure_sha256", "0" * 64),
        ("attention_metrics_sha256", "0" * 64),
    ],
)
def test_result_rejects_changed_arm_receipts(result, field, value):
    document = result.model_dump(mode="python")
    document["pairs"][0]["control"][field] = value
    with pytest.raises(ValueError):
        validate(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("response_input_sha256", "0" * 64),
        ("response_stream_sha256", "0" * 64),
        ("response_state_sha256", "0" * 64),
        ("response_summary_sha256", "0" * 64),
        ("response_stream_bytes", 1),
        ("response_count", 0),
        ("state_update_count", 0),
        ("response_campaign_count", 2),
        ("final_state_count", 3),
        ("response_assumption_structure_sha256", "0" * 64),
        ("response_metrics_sha256", "0" * 64),
        ("campaign_ids", ("changed-campaign",)),
    ],
)
def test_result_rejects_changed_response_receipts(result, field, value):
    document = result.model_dump(mode="python")
    document["pairs"][0]["control"]["response"][field] = value
    with pytest.raises(ValueError):
        validate(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", 1),
        ("agents_sha256", "0" * 64),
        ("trace_sha256", "0" * 64),
        ("place_assignments_sha256", "0" * 64),
        ("opportunity_classification", "opportunity-confounded"),
        ("response_assumption_classification", "response-assumption-confounded"),
    ],
)
def test_result_rejects_changed_pair_receipts(result, field, value):
    document = result.model_dump(mode="python")
    document["pairs"][0][field] = value
    with pytest.raises(ValueError):
        validate(document)


@pytest.mark.parametrize(
    "mutation",
    [
        "value",
        "numerator",
        "denominator",
        "source",
        "delta",
        "coherent-count",
        "coherent-state",
        "order",
        "duplicate",
        "missing",
    ],
)
def test_bypass_result_must_recompute_entire_scalar_graph(result, mutation):
    pair = result.pairs[0]
    rows = list(pair.scalars)
    row = next(item for item in rows if item.control.key == "attention.overall.opportunity_count")
    changed = row.control
    if mutation == "value":
        changed = bypass_copy(changed, value=changed.value + 1.0)
    elif mutation == "numerator":
        changed = bypass_copy(changed, numerator=changed.numerator + 1.0)
    elif mutation == "denominator":
        changed = bypass_copy(changed, denominator=2)
    elif mutation == "source":
        changed = bypass_copy(changed, source_artifacts=("outputs/spatial-attention.jsonl",))
    elif mutation == "delta":
        row = bypass_copy(row, delta=1.0)
    elif mutation == "coherent-count":
        changed = bypass_copy(changed, numerator=6.0, value=6.0)
    elif mutation == "coherent-state":
        row = next(
            item
            for item in rows
            if item.control.key == "response.overall.state.recall_strength.initial_mean"
        )
        changed = bypass_copy(row.control, numerator=0.5, value=0.25)
    if mutation not in {"delta", "order", "duplicate", "missing"}:
        row = bypass_copy(row, control=changed)
    rows[rows.index(next(item for item in rows if item.control.key == row.control.key))] = row
    if mutation == "order":
        rows.reverse()
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "missing":
        rows.pop()
    changed_pair = bypass_copy(pair, scalars=tuple(rows))
    bypass = bypass_copy(result, pairs=(changed_pair, *result.pairs[1:]))
    with pytest.raises(ValueError):
        revalidate_model(bypass, spatial_study.SpatialStudyResult, label="result")


@pytest.mark.parametrize(
    "field,value",
    [
        ("mean_paired_difference", 0.1),
        ("sample_standard_deviation", 0.1),
        ("median_paired_difference", 0.1),
        ("bootstrap_ci_low", -0.1),
        ("bootstrap_ci_high", 0.1),
        ("paired_standardized_difference", 1.0),
        ("positive_count", 2),
        ("agreement_fraction", 0.0),
        ("direction", "stable-positive"),
    ],
)
def test_result_recomputes_statistics_from_seed_deltas(result, field, value):
    statistic = bypass_copy(result.statistics[0], **{field: value})
    bypass = bypass_copy(result, statistics=(statistic, *result.statistics[1:]))
    with pytest.raises(ValueError):
        revalidate_model(bypass, spatial_study.SpatialStudyResult, label="result")


def test_result_refuses_order_duplicates_and_altered_definition(result):
    document = result.model_dump(mode="python")
    changes = [
        {"pairs": tuple(reversed(document["pairs"]))},
        {"pairs": (document["pairs"][0], document["pairs"][0])},
        {"statistics": tuple(reversed(document["statistics"]))},
        {"statistics": (document["statistics"][0], *document["statistics"])},
        {"definition": {**document["definition"], "study_id": "different-study"}},
        {"city": {**document["city"], "city_sha256": "0" * 64}},
    ]
    for change in changes:
        with pytest.raises(ValueError):
            validate({**copy.deepcopy(document), **change})


def test_canonical_result_ceiling_is_checked_in_utf8_bytes(result, monkeypatch):
    from adlife.core.domain.serialization import canonical_json

    size = len(canonical_json(result).encode("utf-8"))
    document = result.model_dump(mode="python")
    monkeypatch.setattr(spatial_study, "MAX_SPATIAL_STUDY_RESULT_BYTES", size)
    assert validate(document) == result
    monkeypatch.setattr(spatial_study, "MAX_SPATIAL_STUDY_RESULT_BYTES", size - 1)
    with pytest.raises(ValueError, match="canonical JSON size limit"):
        validate(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "Altered city"),
        ("city_id", "different-city"),
        ("license", "Different license"),
        ("attribution", "Different attribution"),
        ("source_url", "https://example.org/different-source"),
    ],
)
def test_result_binds_compact_public_city_provenance(result, field, value):
    city = result.city.model_copy(update={field: value})
    with pytest.raises(ValueError):
        revalidate_model(
            bypass_copy(result, city=city), spatial_study.SpatialStudyResult, label="result"
        )


@pytest.mark.parametrize("receipt", ["arm", "response"])
def test_standalone_receipt_identifier_refusal_does_not_echo_sensitive_input(result, receipt):
    secret = "sk-live-" + "abcdefghij"
    if receipt == "arm":
        model = spatial_study.SpatialStudyArmReceipt
        document = result.pairs[0].control.model_dump(mode="python")
        document["run_id"] = secret
    else:
        model = spatial_study.SpatialStudyResponseReceipt
        document = result.pairs[0].control.response.model_dump(mode="python")
        document["campaign_ids"] = (secret,)
    with pytest.raises(ValueError) as caught:
        model.model_validate(document)
    assert secret not in str(caught.value)


def test_direct_result_validation_refuses_bypass_statistic_numeric_equality(result):
    # Python False == 0.0 must not substitute for validating the nested statistic.
    changed = bypass_copy(result.statistics[0], mean_paired_difference=False)
    document = result.model_dump(mode="python")
    document["statistics"] = (changed, *result.statistics[1:])
    with pytest.raises(ValueError):
        validate(document)
