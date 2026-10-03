"""Deterministic paired-seed statistics for verified spatial-study evidence."""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import islice
from typing import Literal, Self, TypeVar, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.experiments.spatial_observations import (
    SpatialStudyMetricArtifact,
    _source_artifacts_for_key,
)

_MASK_64 = (1 << 64) - 1
_UINT64_RANGE = 1 << 64
_SPLITMIX_INCREMENT = 0x9E3779B97F4A7C15
_SPLITMIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MULTIPLIER_2 = 0x94D049BB133111EB
_BOOTSTRAP_MODEL_ID = "spatial-paired-bootstrap-v1"
_BOOTSTRAP_RESAMPLES = 10_000
_MIN_METRICS = 1
_MAX_METRICS = 328
_MIN_SEEDS = 2
_MAX_SEEDS = 100
_DIRECTION_THRESHOLD = 0.8

SpatialStudyDirection = Literal[
    "stable-positive",
    "stable-negative",
    "stable-null",
    "unstable",
]

_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")


def _positive_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _direction(
    *, positive: int, negative: int, zero: int, sample_size: int
) -> SpatialStudyDirection:
    if zero == sample_size:
        return "stable-null"
    if positive / sample_size >= _DIRECTION_THRESHOLD:
        return "stable-positive"
    if negative / sample_size >= _DIRECTION_THRESHOLD:
        return "stable-negative"
    return "unstable"


class SpatialPairedStatistic(DomainModel):
    """One auditable statistic over seed-paired treatment-minus-control values."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    metric_key: str = Field(min_length=1, max_length=200)
    source_artifacts: tuple[SpatialStudyMetricArtifact, ...] = Field(
        min_length=1,
        max_length=2,
    )
    n_seeds: int = Field(ge=_MIN_SEEDS, le=_MAX_SEEDS)
    mean_paired_difference: float
    sample_standard_deviation: float = Field(ge=0.0)
    median_paired_difference: float
    bootstrap_ci_low: float
    bootstrap_ci_high: float
    paired_standardized_difference: float | None
    positive_count: int = Field(ge=0, le=_MAX_SEEDS)
    negative_count: int = Field(ge=0, le=_MAX_SEEDS)
    zero_count: int = Field(ge=0, le=_MAX_SEEDS)
    agreement_fraction: float = Field(ge=0.0, le=1.0)
    direction: SpatialStudyDirection

    @field_validator(
        "mean_paired_difference",
        "sample_standard_deviation",
        "median_paired_difference",
        "bootstrap_ci_low",
        "bootstrap_ci_high",
        "agreement_fraction",
        mode="before",
    )
    @classmethod
    def exact_float(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("spatial paired statistic scalars must be floats")
        return value

    @field_validator("paired_standardized_difference", mode="before")
    @classmethod
    def exact_nullable_float(cls, value: object) -> object:
        if value is not None and type(value) is not float:
            raise ValueError("spatial paired standardized difference must be a float or null")
        return value

    @field_validator(
        "n_seeds",
        "positive_count",
        "negative_count",
        "zero_count",
        mode="before",
    )
    @classmethod
    def exact_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("spatial paired statistic counts must be integers")
        return value

    @model_validator(mode="after")
    def coherent_statistic(self) -> Self:
        try:
            expected_sources = _source_artifacts_for_key(self.metric_key)
        except ValueError:
            raise ValueError("spatial paired statistic key is invalid") from None
        if self.source_artifacts != expected_sources:
            raise ValueError("spatial paired statistic sources do not match its key")
        if self.positive_count + self.negative_count + self.zero_count != self.n_seeds:
            raise ValueError("spatial paired statistic sign counts do not match its sample size")
        expected_agreement = (
            1.0
            if self.zero_count == self.n_seeds
            else max(self.positive_count, self.negative_count) / self.n_seeds
        )
        if self.agreement_fraction != expected_agreement:
            raise ValueError("spatial paired statistic agreement does not match its sign counts")
        expected_direction = _direction(
            positive=self.positive_count,
            negative=self.negative_count,
            zero=self.zero_count,
            sample_size=self.n_seeds,
        )
        if self.direction != expected_direction:
            raise ValueError("spatial paired statistic direction does not match its sign counts")
        if self.bootstrap_ci_low > self.bootstrap_ci_high:
            raise ValueError("spatial paired statistic interval endpoints are reversed")
        if self.sample_standard_deviation == 0.0:
            if self.paired_standardized_difference is not None:
                raise ValueError(
                    "zero-deviation statistic must have a null standardized difference"
                )
        else:
            expected_effect = _positive_zero(
                self.mean_paired_difference / self.sample_standard_deviation
            )
            if self.paired_standardized_difference != expected_effect:
                raise ValueError("paired standardized difference does not match mean/deviation")

        for value in (
            self.mean_paired_difference,
            self.sample_standard_deviation,
            self.median_paired_difference,
            self.bootstrap_ci_low,
            self.bootstrap_ci_high,
            self.paired_standardized_difference,
            self.agreement_fraction,
        ):
            if value == 0.0 and math.copysign(1.0, value) < 0:
                raise ValueError("spatial paired statistic zero must have a positive sign")
        return self


class _SplitMix64:
    """Version-pinned unsigned SplitMix64 stream used only for paired bootstrap indices."""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        if type(seed) is not int or not 0 <= seed <= _MASK_64:
            raise ValueError("SplitMix64 seed must be an unsigned 64-bit integer")
        self._state = seed

    def next_u64(self) -> int:
        self._state = (self._state + _SPLITMIX_INCREMENT) & _MASK_64
        value = self._state
        value = ((value ^ (value >> 30)) * _SPLITMIX_MULTIPLIER_1) & _MASK_64
        value = ((value ^ (value >> 27)) * _SPLITMIX_MULTIPLIER_2) & _MASK_64
        return (value ^ (value >> 31)) & _MASK_64

    def index(self, sample_size: int) -> int:
        if type(sample_size) is not int or not 1 <= sample_size <= _MAX_SEEDS:
            raise ValueError("bootstrap sample size is outside the supported range")
        limit = _UINT64_RANGE - (_UINT64_RANGE % sample_size)
        while True:
            word = self.next_u64()
            if word < limit:
                return word % sample_size


def _bootstrap_seed(metric_key: str, sample_size: int) -> int:
    document = canonical_json(
        {
            "metric_key": metric_key,
            "model_id": _BOOTSTRAP_MODEL_ID,
            "sample_size": sample_size,
        }
    )
    return int.from_bytes(sha256(document.encode("utf-8")).digest()[:8], "big")


def _bootstrap_interval(metric_key: str, values: tuple[float, ...]) -> tuple[float, float]:
    if all(value == values[0] for value in values[1:]):
        constant = _positive_zero(values[0])
        return constant, constant

    sample_size = len(values)
    generator = _SplitMix64(_bootstrap_seed(metric_key, sample_size))
    index = generator.index
    bootstrap_means: list[float] = []
    for _ in range(_BOOTSTRAP_RESAMPLES):
        sample_total = math.fsum(values[index(sample_size)] for _ in range(sample_size))
        bootstrap_means.append(_positive_zero(sample_total / sample_size))
    bootstrap_means.sort()
    return bootstrap_means[249], bootstrap_means[9749]


@dataclass(frozen=True, slots=True)
class _PreparedStatistic:
    source_artifacts: tuple[SpatialStudyMetricArtifact, ...]
    values: tuple[float, ...]
    mean: float
    sample_standard_deviation: float
    median: float
    positive_count: int
    negative_count: int
    zero_count: int


def _mapping_keys(mapping: Mapping[_KeyT, _ValueT]) -> tuple[str, ...]:
    try:
        declared_length = len(mapping)
        if not _MIN_METRICS <= declared_length <= _MAX_METRICS:
            raise ValueError
        raw_keys = tuple(islice(iter(mapping), _MAX_METRICS + 1))
    except Exception as error:
        raise ValueError("spatial paired statistic mapping is invalid") from error
    if len(raw_keys) != declared_length or any(type(key) is not str for key in raw_keys):
        raise ValueError("spatial paired statistic mapping keys are invalid")
    keys = cast(tuple[str, ...], raw_keys)
    if len(keys) != len(set(keys)):
        raise ValueError("spatial paired statistic mapping contains duplicate keys")
    return keys


def _prepare_values(
    values: object,
    *,
    source_artifacts: tuple[SpatialStudyMetricArtifact, ...],
) -> _PreparedStatistic:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError("spatial paired differences must be a finite float sequence")
    sample_size = len(values)
    if not _MIN_SEEDS <= sample_size <= _MAX_SEEDS:
        raise ValueError("spatial paired difference vector length is outside the supported range")
    try:
        raw_values = tuple(islice(iter(values), sample_size + 1))
    except Exception as error:
        raise ValueError("spatial paired differences must be a finite float sequence") from error
    if len(raw_values) != sample_size:
        raise ValueError("spatial paired difference sequence length is inconsistent")
    checked: list[float] = []
    for value in raw_values:
        if type(value) is not float or not math.isfinite(value):
            raise ValueError("spatial paired differences must contain exact finite floats")
        checked.append(value)
    vector = tuple(checked)

    # A bootstrap sample may select the largest magnitude value for every draw. Refuse
    # vectors whose valid finite members could therefore overflow during resampling.
    if max(abs(value) for value in vector) > sys.float_info.max / sample_size:
        raise ValueError("spatial paired differences exceed the supported arithmetic range")
    if all(value == vector[0] for value in vector[1:]):
        # An empirically constant vector has exactly zero seed variation. Computing
        # deviations from a separately rounded fsum/n mean can otherwise manufacture
        # variance for values such as 0.1 and violate the nullable-effect contract.
        mean = median = _positive_zero(vector[0])
        deviation = 0.0
    else:
        try:
            mean = _positive_zero(math.fsum(vector) / sample_size)
            squared_deviations = tuple((value - mean) ** 2 for value in vector)
            if any(not math.isfinite(value) for value in squared_deviations):
                raise ValueError
            variance = math.fsum(squared_deviations) / (sample_size - 1)
            deviation = _positive_zero(math.sqrt(variance))
            ordered = sorted(vector)
            midpoint = sample_size // 2
            median = (
                ordered[midpoint]
                if sample_size % 2
                else math.fsum((ordered[midpoint - 1], ordered[midpoint])) / 2.0
            )
            median = _positive_zero(median)
            if deviation and not math.isfinite(mean / deviation):
                raise ValueError
        except (ArithmeticError, ValueError):
            raise ValueError(
                "spatial paired differences exceed the supported arithmetic range"
            ) from None

    return _PreparedStatistic(
        source_artifacts=source_artifacts,
        values=vector,
        mean=mean,
        sample_standard_deviation=deviation,
        median=median,
        positive_count=sum(value > 0.0 for value in vector),
        negative_count=sum(value < 0.0 for value in vector),
        zero_count=sum(value == 0.0 for value in vector),
    )


def _preflight(
    differences: Mapping[str, Sequence[float]],
    sources: Mapping[str, tuple[SpatialStudyMetricArtifact, ...]],
) -> tuple[tuple[str, ...], dict[str, _PreparedStatistic]]:
    difference_keys = _mapping_keys(differences)
    source_keys = _mapping_keys(sources)
    if not _MIN_METRICS <= len(difference_keys) <= _MAX_METRICS:
        raise ValueError("spatial paired statistic metric count is outside the supported range")
    if set(difference_keys) != set(source_keys):
        raise ValueError("spatial paired statistic sources do not match metric keys")

    prepared: dict[str, _PreparedStatistic] = {}
    sample_size: int | None = None
    for key in sorted(difference_keys):
        try:
            expected_sources = _source_artifacts_for_key(key)
            actual_sources = sources[key]
            raw_values = differences[key]
        except (KeyError, TypeError, ValueError):
            raise ValueError("spatial paired statistic input is invalid") from None
        if type(actual_sources) is not tuple or actual_sources != expected_sources:
            raise ValueError("spatial paired statistic sources do not match metric keys")
        statistic = _prepare_values(raw_values, source_artifacts=expected_sources)
        if sample_size is None:
            sample_size = len(statistic.values)
        elif len(statistic.values) != sample_size:
            raise ValueError("spatial paired difference vectors must have equal lengths")
        prepared[key] = statistic
    return tuple(sorted(difference_keys)), prepared


def spatial_paired_statistics(
    differences: Mapping[str, Sequence[float]],
    sources: Mapping[str, tuple[SpatialStudyMetricArtifact, ...]],
) -> tuple[SpatialPairedStatistic, ...]:
    """Summarize canonical seed-paired difference vectors with independently keyed streams."""
    keys, prepared = _preflight(differences, sources)
    results: list[SpatialPairedStatistic] = []
    for key in keys:
        values = prepared[key]
        low, high = _bootstrap_interval(key, values.values)
        effect = (
            None
            if values.sample_standard_deviation == 0.0
            else _positive_zero(values.mean / values.sample_standard_deviation)
        )
        sample_size = len(values.values)
        agreement = (
            1.0
            if values.zero_count == sample_size
            else max(values.positive_count, values.negative_count) / sample_size
        )
        results.append(
            SpatialPairedStatistic(
                metric_key=key,
                source_artifacts=values.source_artifacts,
                n_seeds=sample_size,
                mean_paired_difference=values.mean,
                sample_standard_deviation=values.sample_standard_deviation,
                median_paired_difference=values.median,
                bootstrap_ci_low=_positive_zero(low),
                bootstrap_ci_high=_positive_zero(high),
                paired_standardized_difference=effect,
                positive_count=values.positive_count,
                negative_count=values.negative_count,
                zero_count=values.zero_count,
                agreement_fraction=_positive_zero(agreement),
                direction=_direction(
                    positive=values.positive_count,
                    negative=values.negative_count,
                    zero=values.zero_count,
                    sample_size=sample_size,
                ),
            )
        )
    return tuple(results)


__all__ = [
    "SpatialPairedStatistic",
    "SpatialStudyDirection",
    "spatial_paired_statistics",
]
