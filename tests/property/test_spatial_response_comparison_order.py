from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_response_comparison import (
    compare_spatial_response_metrics,
)
from adlife.core.experiments.spatial_response_metrics import SpatialResponseMetrics
from tests.unit.experiments.test_spatial_response_comparison import _response_metrics

EVENT_NAMES = (
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
)
STATE_NAMES = ("brand_sentiment", "recall_strength", "purchase_intention_proxy")
STATE_AGGREGATES = ("initial_mean", "final_mean", "mean_change")
SENTIMENTS = st.sampled_from((-1.0, -0.5, 0.25, 1.0))


def _scalars(document: Any, *, receipts: bool = False) -> dict[str, float]:
    """Select the public scalar contract, excluding IDs, provenance and versions."""
    slices = [("overall", document.overall)]
    slices.extend((f"channel.{series.channel}", series) for series in document.channels)
    slices.extend((f"campaign.{series.campaign_id}", series) for series in document.campaigns)
    values = {}
    for key, series in slices:
        for name in EVENT_NAMES:
            value = getattr(series, name)
            values[f"{key}.{name}"] = float(value.value if receipts else value)
        if not key.startswith("channel."):
            for name in STATE_NAMES:
                state = getattr(series, name)
                for aggregate in STATE_AGGREGATES:
                    values[f"{key}.state.{name}.{aggregate}"] = getattr(state, aggregate)
    return values


def _reorder_mappings(value: Any, offset: int, reverse: bool) -> Any:
    if isinstance(value, dict):
        keys = list(value)
        keys = keys[offset % len(keys) :] + keys[: offset % len(keys)] if keys else keys
        if reverse:
            keys.reverse()
        return {key: _reorder_mappings(value[key], offset + 1, reverse) for key in keys}
    if isinstance(value, list):
        return [_reorder_mappings(item, offset, reverse) for item in value]
    if isinstance(value, tuple):
        return tuple(_reorder_mappings(item, offset, reverse) for item in value)
    return value


@settings(max_examples=12, deadline=None)
@given(offset=st.integers(min_value=1, max_value=40), reverse=st.booleans())
def test_comparison_bytes_are_invariant_to_nested_input_mapping_order(
    offset: int,
    reverse: bool,
) -> None:
    control = _response_metrics(initial_sentiment=-0.5, phone_end_minute=1)
    treatment = _response_metrics(initial_sentiment=0.25, phone_end_minute=3)
    expected = compare_spatial_response_metrics(
        control,
        treatment,
        control_run_id="control",
        treatment_run_id="treatment",
    )
    permuted_control = SpatialResponseMetrics.model_validate(
        _reorder_mappings(control.model_dump(mode="python"), offset, reverse)
    )
    permuted_treatment = SpatialResponseMetrics.model_validate(
        _reorder_mappings(treatment.model_dump(mode="python"), offset + 2, not reverse)
    )

    actual = compare_spatial_response_metrics(
        permuted_control,
        permuted_treatment,
        control_run_id="control",
        treatment_run_id="treatment",
    )

    assert canonical_json(actual) == canonical_json(expected)
    assert actual.overall.brand_sentiment.initial_mean == 0.75
    assert tuple(series.channel for series in actual.channels) == ("roadside", "mobile")
    assert tuple(series.campaign_id for series in actual.campaigns) == (
        "fictional-launch",
        "phone-campaign",
    )


@settings(max_examples=20, deadline=None)
@given(
    control_sentiment=SENTIMENTS,
    treatment_sentiment=SENTIMENTS,
    phone_end_minute=st.integers(min_value=1, max_value=4),
    phone_cap=st.integers(min_value=1, max_value=4),
)
def test_swapping_generated_arms_exactly_negates_every_response_scalar(
    control_sentiment: float,
    treatment_sentiment: float,
    phone_end_minute: int,
    phone_cap: int,
) -> None:
    control = _response_metrics(initial_sentiment=control_sentiment)
    treatment = _response_metrics(
        initial_sentiment=treatment_sentiment,
        phone_end_minute=phone_end_minute,
        phone_cap=phone_cap,
    )
    forward = compare_spatial_response_metrics(
        control,
        treatment,
        control_run_id="control",
        treatment_run_id="treatment",
    )
    reverse = compare_spatial_response_metrics(
        treatment,
        control,
        control_run_id="treatment",
        treatment_run_id="control",
    )

    control_values = _scalars(control, receipts=True)
    treatment_values = _scalars(treatment, receipts=True)
    # Source receipt subtraction is the public contract, independent of delta builders.
    expected = {key: treatment_values[key] - value for key, value in control_values.items()}
    assert len(expected) == 52
    assert _scalars(forward) == expected
    assert _scalars(reverse) == {key: -value for key, value in expected.items()}
    assert forward.overall.brand_sentiment.initial_mean == treatment_sentiment - control_sentiment
    assert forward.opportunity_classification == reverse.opportunity_classification
    assert forward.response_assumption_classification == reverse.response_assumption_classification
    assert reverse.control == forward.treatment
    assert reverse.treatment == forward.control


@settings(max_examples=16, deadline=None)
@given(
    sentiment=SENTIMENTS,
    phone_end_minute=st.integers(min_value=1, max_value=4),
    phone_cap=st.integers(min_value=1, max_value=4),
)
def test_generated_aa_comparisons_have_exact_positive_zero_for_every_scalar(
    sentiment: float,
    phone_end_minute: int,
    phone_cap: int,
) -> None:
    metrics = _response_metrics(
        initial_sentiment=sentiment,
        phone_end_minute=phone_end_minute,
        phone_cap=phone_cap,
    )
    independently_loaded = SpatialResponseMetrics.model_validate_json(canonical_json(metrics))

    result = compare_spatial_response_metrics(
        metrics,
        independently_loaded,
        control_run_id="control",
        treatment_run_id="treatment",
    )

    values = _scalars(result)
    assert len(values) == 52
    assert set(values.values()) == {0.0}
    assert all(math.copysign(1.0, value) == 1.0 for value in values.values())
    assert result.opportunity_classification == "matched-opportunity-structure"
    assert result.response_assumption_classification == "matched-response-assumptions"


def test_response_comparison_bytes_do_not_depend_on_python_hash_seed() -> None:
    script = """
from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_response_comparison import compare_spatial_response_metrics
from adlife.core.experiments.spatial_response_metrics import SpatialResponseMetrics
from tests.unit.experiments.test_spatial_response_comparison import _response_metrics

def metrics(sentiment, end_minute):
    source = _response_metrics(initial_sentiment=sentiment, phone_end_minute=end_minute)
    document = source.model_dump(mode='python')
    # Set iteration deliberately varies mapping construction across fresh processes.
    unordered = {key: document[key] for key in set(document)}
    return SpatialResponseMetrics.model_validate(unordered)

result = compare_spatial_response_metrics(
    metrics(-0.5, 1), metrics(0.25, 3), control_run_id='control', treatment_run_id='treatment'
)
print(canonical_json(result))
"""
    outputs = []
    for hash_seed in ("0", "12345"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert completed.stderr == ""
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    document = json.loads(outputs[0])
    assert document["model_id"] == "spatial-response-metrics-comparison-v1"
    assert document["overall"]["brand_sentiment"]["initial_mean"] == 0.75
    assert [series["channel"] for series in document["channels"]] == ["roadside", "mobile"]
    assert [series["campaign_id"] for series in document["campaigns"]] == [
        "fictional-launch",
        "phone-campaign",
    ]
