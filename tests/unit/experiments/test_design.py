"""Paired sign stability follows the registered seed-count rule, not effect magnitude."""

from typing import Literal

import pytest

from adlife.core.experiments.design import paired_statistics
from adlife.core.experiments.metrics import METRIC_NAMES


@pytest.mark.parametrize(
    "values,agreement,direction,mean,median",
    [
        pytest.param(
            (0.0,) * 8 + (1.0, -1.0), 0.1, "unstable", 0.0, 0.0, id="zero-heavy-cancellation"
        ),
        pytest.param((1.0, -1.0), 0.5, "unstable", 0.0, 0.0, id="symmetric-signs"),
        pytest.param((1.0,) * 8 + (-100.0,) * 2, 0.8, "stable", -19.2, 1.0, id="negative-outliers"),
        pytest.param((-1.0,) * 8 + (100.0,) * 2, 0.8, "stable", 19.2, -1.0, id="positive-outliers"),
        pytest.param(
            (1.0,) * 8 + (-4.0,) * 2, 0.8, "stable", 0.0, 1.0, id="majority-with-zero-mean"
        ),
        pytest.param(
            (1.0,) * 8 + (0.0,) * 2, 0.8, "stable", 0.8, 1.0, id="exact-positive-threshold"
        ),
        pytest.param(
            (-1.0,) * 8 + (0.0,) * 2, 0.8, "stable", -0.8, -1.0, id="exact-negative-threshold"
        ),
        pytest.param(
            (1.0,) * 7 + (0.0,) * 3, 0.7, "unstable", 0.7, 1.0, id="zeros-stay-in-denominator"
        ),
        pytest.param((0.0,) * 10, 1.0, "stable", 0.0, 0.0, id="all-zero-null-consistency"),
    ],
)
def test_directional_stability_counts_the_largest_nonzero_sign_group(
    values: tuple[float, ...],
    agreement: float,
    direction: Literal["stable", "unstable"],
    mean: float,
    median: float,
) -> None:
    differences: dict[str, tuple[float, ...]] = {name: (0.0,) for name in METRIC_NAMES}
    differences["recall"] = values

    result = paired_statistics(differences)["recall"]

    assert result.agreement_fraction == agreement
    assert result.direction == direction
    assert result.n_seeds == len(values)
    assert result.mean_paired_difference == pytest.approx(mean)
    assert result.median_paired_difference == median
