"""Literal conformance goldens for versioned paired seed statistics.

These expectations were calculated independently of production code. In particular,
the fifty-seed vector has zero mean but an 80% positive direction: an aggregate mean
must never substitute for the seed-sign rule.
"""

from __future__ import annotations

import math
from typing import Literal

import pytest

from adlife.core.experiments.spatial_study import (
    SpatialPairedStatistic,
    _bootstrap_seed,
    _SplitMix64,
    spatial_paired_statistics,
)

METRIC_KEY = "attention.overall.notice_rate"
SOURCES = ("outputs/spatial-attention.jsonl",)


@pytest.mark.parametrize(
    "values,expected",
    [
        pytest.param(
            (-1.0, 3.0),
            {
                "schema_version": 1,
                "metric_key": "attention.overall.notice_rate",
                "source_artifacts": ["outputs/spatial-attention.jsonl"],
                "n_seeds": 2,
                "mean_paired_difference": 1.0,
                "sample_standard_deviation": 2.8284271247461903,
                "median_paired_difference": 1.0,
                "bootstrap_ci_low": -1.0,
                "bootstrap_ci_high": 3.0,
                "paired_standardized_difference": 0.35355339059327373,
                "positive_count": 1,
                "negative_count": 1,
                "zero_count": 0,
                "agreement_fraction": 0.5,
                "direction": "unstable",
            },
            id="two-seed-even-median",
        ),
        pytest.param(
            (1.0,) * 16 + (0.0,) * 4,
            {
                "schema_version": 1,
                "metric_key": "attention.overall.notice_rate",
                "source_artifacts": ["outputs/spatial-attention.jsonl"],
                "n_seeds": 20,
                "mean_paired_difference": 0.8,
                "sample_standard_deviation": 0.41039134083406165,
                "median_paired_difference": 1.0,
                "bootstrap_ci_low": 0.6,
                "bootstrap_ci_high": 0.95,
                "paired_standardized_difference": 1.949358868961793,
                "positive_count": 16,
                "negative_count": 0,
                "zero_count": 4,
                "agreement_fraction": 0.8,
                "direction": "stable-positive",
            },
            id="twenty-seed-exact-threshold",
        ),
        pytest.param(
            (1.0,) * 40 + (-4.0,) * 10,
            {
                "schema_version": 1,
                "metric_key": "attention.overall.notice_rate",
                "source_artifacts": ["outputs/spatial-attention.jsonl"],
                "n_seeds": 50,
                "mean_paired_difference": 0.0,
                "sample_standard_deviation": 2.0203050891044216,
                "median_paired_difference": 1.0,
                "bootstrap_ci_low": -0.6,
                "bootstrap_ci_high": 0.5,
                "paired_standardized_difference": 0.0,
                "positive_count": 40,
                "negative_count": 10,
                "zero_count": 0,
                "agreement_fraction": 0.8,
                "direction": "stable-positive",
            },
            id="fifty-seed-positive-direction-zero-mean",
        ),
    ],
)
def test_paired_statistics_complete_literal_documents(
    values: tuple[float, ...], expected: dict[str, object]
) -> None:
    result = spatial_paired_statistics({METRIC_KEY: values}, {METRIC_KEY: SOURCES})

    assert isinstance(result, tuple)
    assert len(result) == 1
    assert isinstance(result[0], SpatialPairedStatistic)
    assert result[0].model_dump(mode="json") == expected


@pytest.mark.parametrize(
    "values,mean,median,low,high,positive,negative,zeros,agreement,direction",
    [
        pytest.param(
            (0.0,) * 20,
            0.0,
            0.0,
            0.0,
            0.0,
            0,
            0,
            20,
            1.0,
            "stable-null",
            id="all-zero-null-consistency",
        ),
        pytest.param(
            (2.5,) * 20,
            2.5,
            2.5,
            2.5,
            2.5,
            20,
            0,
            0,
            1.0,
            "stable-positive",
            id="constant-positive-is-not-zero-effect",
        ),
        pytest.param(
            (-2.5,) * 20,
            -2.5,
            -2.5,
            -2.5,
            -2.5,
            0,
            20,
            0,
            1.0,
            "stable-negative",
            id="constant-negative-is-not-zero-effect",
        ),
    ],
)
def test_zero_deviation_has_nullable_effect_and_exact_interval(
    values: tuple[float, ...],
    mean: float,
    median: float,
    low: float,
    high: float,
    positive: int,
    negative: int,
    zeros: int,
    agreement: float,
    direction: Literal["stable-null", "stable-positive", "stable-negative"],
) -> None:
    (result,) = spatial_paired_statistics({METRIC_KEY: values}, {METRIC_KEY: SOURCES})

    assert result.mean_paired_difference == mean
    assert result.sample_standard_deviation == 0.0
    assert result.median_paired_difference == median
    assert result.bootstrap_ci_low == low
    assert result.bootstrap_ci_high == high
    assert result.paired_standardized_difference is None
    assert (result.positive_count, result.negative_count, result.zero_count) == (
        positive,
        negative,
        zeros,
    )
    assert result.agreement_fraction == agreement
    assert result.direction == direction


def test_negative_mirror_has_literal_negative_interval_and_direction() -> None:
    (result,) = spatial_paired_statistics(
        {METRIC_KEY: (-1.0,) * 16 + (0.0,) * 4}, {METRIC_KEY: SOURCES}
    )

    assert result.mean_paired_difference == -0.8
    assert result.sample_standard_deviation == 0.41039134083406165
    assert result.median_paired_difference == -1.0
    assert result.bootstrap_ci_low == -0.95
    assert result.bootstrap_ci_high == -0.6
    assert result.paired_standardized_difference == -1.949358868961793
    assert (result.positive_count, result.negative_count, result.zero_count) == (0, 16, 4)
    assert result.agreement_fraction == 0.8
    assert result.direction == "stable-negative"


@pytest.mark.parametrize(
    "values,mean,median,counts,agreement,direction",
    [
        pytest.param(
            (0.0,) * 8 + (1.0, -1.0),
            0.0,
            0.0,
            (1, 1, 8),
            0.1,
            "unstable",
            id="zero-heavy-agreement",
        ),
        pytest.param(
            (1.0,) * 7 + (0.0,) * 3,
            0.7,
            1.0,
            (7, 0, 3),
            0.7,
            "unstable",
            id="zeros-stay-in-denominator",
        ),
        pytest.param(
            (1.0,) * 8 + (-100.0,) * 2,
            -19.2,
            1.0,
            (8, 2, 0),
            0.8,
            "stable-positive",
            id="direction-opposes-mean",
        ),
        pytest.param(
            (-1.0,) * 8 + (100.0,) * 2,
            19.2,
            -1.0,
            (2, 8, 0),
            0.8,
            "stable-negative",
            id="negative-direction-opposes-mean",
        ),
        pytest.param(
            (1.0, 2.0, 7.0, 10.0),
            5.0,
            4.5,
            (4, 0, 0),
            1.0,
            "stable-positive",
            id="even-median-two-central-values",
        ),
        pytest.param(
            (1e-12, 1e-12),
            1e-12,
            1e-12,
            (2, 0, 0),
            1.0,
            "stable-positive",
            id="no-post-hoc-epsilon",
        ),
    ],
)
def test_sign_counts_and_even_median_follow_seed_values(
    values: tuple[float, ...],
    mean: float,
    median: float,
    counts: tuple[int, int, int],
    agreement: float,
    direction: str,
) -> None:
    (result,) = spatial_paired_statistics({METRIC_KEY: values}, {METRIC_KEY: SOURCES})

    assert result.mean_paired_difference == mean
    assert result.median_paired_difference == median
    assert (result.positive_count, result.negative_count, result.zero_count) == counts
    assert result.agreement_fraction == agreement
    assert result.direction == direction


@pytest.mark.parametrize("values", [(-0.0, -0.0), (-1.0, 1.0), (1.0,) * 40 + (-4.0,) * 10])
def test_every_numeric_zero_in_the_document_has_a_positive_sign(
    values: tuple[float, ...],
) -> None:
    (result,) = spatial_paired_statistics({METRIC_KEY: values}, {METRIC_KEY: SOURCES})

    for value in result.model_dump(mode="json").values():
        if type(value) is float and value == 0.0:
            assert math.copysign(1.0, value) == 1.0
    assert "-0.0" not in result.model_dump_json()


@pytest.mark.parametrize(
    "metric_key,sample_size,expected_seed",
    [
        ("attention.overall.notice_rate", 2, 5997478027145000229),
        ("attention.overall.notice_rate", 20, 16836650139495647654),
        ("attention.overall.notice_rate", 50, 5922531097556278820),
        ("attention.overall.impression_count", 2, 9298917175350191284),
    ],
)
def test_bootstrap_seed_uses_canonical_json_and_first_eight_big_endian_digest_bytes(
    metric_key: str, sample_size: int, expected_seed: int
) -> None:
    assert _bootstrap_seed(metric_key, sample_size) == expected_seed


@pytest.mark.parametrize(
    "seed,expected_draws",
    [
        (
            0,
            (
                16294208416658607535,
                7960286522194355700,
                487617019471545679,
                17909611376780542444,
                1961750202426094747,
            ),
        ),
        (
            18446744073709551615,
            (
                16490336266968443936,
                16834447057089888969,
                4048727598324417001,
                7862637804313477842,
                13015481187462834606,
            ),
        ),
        (
            16836650139495647654,
            (
                4773355777288863084,
                13261657236280187130,
                17377957849125376874,
                13351718731700359749,
                13493468087957290934,
            ),
        ),
    ],
)
def test_splitmix64_draws_pin_unsigned_wrapping_and_versioned_mix(
    seed: int, expected_draws: tuple[int, ...]
) -> None:
    generator = _SplitMix64(seed)

    assert tuple(generator.next_u64() for _ in range(5)) == expected_draws


@pytest.mark.parametrize(
    "seed,sample_size,expected_indices",
    [
        (5997478027145000229, 2, (1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0)),
        (16836650139495647654, 20, (4, 10, 14, 9, 14, 19, 7, 6, 15, 10, 9, 17)),
        (5922531097556278820, 50, (37, 33, 2, 23, 39, 32, 21, 30, 5, 49, 29, 48)),
    ],
)
def test_bootstrap_indices_have_literal_seed_keyed_sequences(
    seed: int, sample_size: int, expected_indices: tuple[int, ...]
) -> None:
    generator = _SplitMix64(seed)

    assert tuple(generator.index(sample_size) for _ in range(12)) == expected_indices


@pytest.mark.parametrize(
    "sample_size,draws,expected_index",
    [
        (3, (18446744073709551615, 18446744073709551614, 8), 2),
        (20, (18446744073709551600, 18446744073709551615, 18446744073709551599, 8), 19),
        (50, (18446744073709551600, 18446744073709551615, 18446744073709551599, 8), 49),
        (2, (18446744073709551615, 8), 1),
    ],
)
def test_index_rejects_cutoff_inclusively_before_modulo(
    monkeypatch: pytest.MonkeyPatch,
    sample_size: int,
    draws: tuple[int, ...],
    expected_index: int,
) -> None:
    # The injected raw words make the vanishingly rare rejection boundary observable;
    # index selection and its rejection loop remain the real production behavior.
    words = iter(draws)
    monkeypatch.setattr(_SplitMix64, "next_u64", lambda self: next(words))
    generator = _SplitMix64(0)

    assert generator.index(sample_size) == expected_index
    assert generator.next_u64() == 8


def test_constant_decimal_vector_preserves_exact_value_and_null_effect() -> None:
    (result,) = spatial_paired_statistics({METRIC_KEY: (0.1, 0.1, 0.1)}, {METRIC_KEY: SOURCES})

    assert (
        result.mean_paired_difference,
        result.median_paired_difference,
        result.bootstrap_ci_low,
        result.bootstrap_ci_high,
        result.sample_standard_deviation,
        result.paired_standardized_difference,
        result.direction,
    ) == (0.1, 0.1, 0.1, 0.1, 0.0, None, "stable-positive")
    assert result.n_seeds == 3
    assert (result.positive_count, result.negative_count, result.zero_count) == (3, 0, 0)
