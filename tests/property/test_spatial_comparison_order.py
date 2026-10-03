from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from adlife.core.experiments.spatial_comparison import compare_spatial_metrics
from tests.unit.experiments.test_spatial_comparison import METRIC_NAMES, _variant_metrics


@given(start=st.integers(min_value=0, max_value=8))
def test_swapping_comparison_arms_negates_every_delta(start: int) -> None:
    control = _variant_metrics(start=start, end=start + 1)
    treatment = _variant_metrics(start=start, end=start + 2)

    forward = compare_spatial_metrics(
        control,
        treatment,
        control_run_id="control",
        treatment_run_id="treatment",
    )
    reverse = compare_spatial_metrics(
        treatment,
        control,
        control_run_id="treatment",
        treatment_run_id="control",
    )

    assert forward.classification == reverse.classification
    for forward_series, reverse_series in zip(
        (forward.overall, *forward.channels),
        (reverse.overall, *reverse.channels),
        strict=True,
    ):
        assert forward_series.channel == reverse_series.channel
        for name in METRIC_NAMES:
            assert getattr(forward_series, name) == -getattr(reverse_series, name)
