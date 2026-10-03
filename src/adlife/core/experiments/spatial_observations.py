"""Typed scalar observations shared by spatial comparisons and seed studies.

The observation seam deliberately carries only one auditable scalar and its source
artifacts.  It never exposes response inputs, raw events, campaign copy, or an adapter
type to the study layer.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Literal, Protocol, Self, TypeAlias, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.person import DomainModel, contains_secret_or_email_text
from adlife.core.experiments.spatial_metrics import (
    SpatialMetricReceipt,
    SpatialMetrics,
)
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseMetrics,
)
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_opportunity import MAX_SPATIAL_OPPORTUNITIES

_KEY_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"
_CAMPAIGN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_MAX_KEY_LENGTH = 200

SpatialStudyMetricArtifact: TypeAlias = Literal[
    "outputs/spatial-opportunities.jsonl",
    "outputs/spatial-attention.jsonl",
    "outputs/spatial-responses.jsonl",
    "inputs/spatial-response.json",
    "outputs/response-state.json",
]

_ATTENTION_METRICS = frozenset(
    {
        "opportunity_count",
        "impression_count",
        "noticed_count",
        "opportunity_reach",
        "impression_reach",
        "noticed_reach",
        "impression_frequency",
        "notice_rate",
    }
)
_RESPONSE_EVENT_METRICS = frozenset(
    {
        "response_count",
        "response_reach",
        "response_frequency",
        "mean_rule_sentiment_delta",
        "mean_rule_recall_delta",
    }
)
_STATE_METRICS = frozenset(
    {
        "brand_sentiment",
        "recall_strength",
        "purchase_intention_proxy",
    }
)
_STATE_AGGREGATES = frozenset({"initial_mean", "final_mean", "mean_change"})
_CHANNELS = frozenset({"roadside", "mobile"})

_OPPORTUNITY_ARTIFACTS: tuple[SpatialStudyMetricArtifact, ...] = (
    "outputs/spatial-opportunities.jsonl",
)
_ATTENTION_ARTIFACTS: tuple[SpatialStudyMetricArtifact, ...] = ("outputs/spatial-attention.jsonl",)
_RESPONSE_ARTIFACTS: tuple[SpatialStudyMetricArtifact, ...] = ("outputs/spatial-responses.jsonl",)
_STATE_ARTIFACTS: tuple[SpatialStudyMetricArtifact, ...] = (
    "inputs/spatial-response.json",
    "outputs/response-state.json",
)


class _ResponseEventReceipt(Protocol):
    value: float
    numerator: int | float
    denominator: int
    source_artifacts: tuple[SpatialStudyMetricArtifact, ...]


class _ResponseStateReceipt(Protocol):
    initial_total: float
    final_total: float
    change_total: float
    denominator: int
    initial_mean: float
    final_mean: float
    mean_change: float
    source_artifacts: tuple[SpatialStudyMetricArtifact, ...]


def _campaign_key_segment(value: str) -> bool:
    if _CAMPAIGN_ID_PATTERN.fullmatch(value) is None:
        return False
    if contains_secret_or_email_text(value):
        raise ValueError("spatial metric key carries credential-shaped text")
    return True


def _source_artifacts_for_key(
    key: str,
) -> tuple[SpatialStudyMetricArtifact, ...]:
    """Return the one canonical source tuple for a grammar-valid observation key."""
    parts = key.split(".")
    if len(parts) == 3 and parts[:2] == ["attention", "overall"]:
        metric = parts[2]
        if metric in _ATTENTION_METRICS:
            return (
                _OPPORTUNITY_ARTIFACTS
                if metric in {"opportunity_count", "opportunity_reach"}
                else _ATTENTION_ARTIFACTS
            )
    elif (
        len(parts) == 4
        and parts[:2] == ["attention", "channel"]
        and parts[2] in _CHANNELS
        and parts[3] in _ATTENTION_METRICS
    ):
        return (
            _OPPORTUNITY_ARTIFACTS
            if parts[3] in {"opportunity_count", "opportunity_reach"}
            else _ATTENTION_ARTIFACTS
        )
    elif len(parts) == 3 and parts[:2] == ["response", "overall"]:
        if parts[2] in _RESPONSE_EVENT_METRICS:
            return _RESPONSE_ARTIFACTS
    elif (
        len(parts) == 4
        and parts[:2] == ["response", "channel"]
        and parts[2] in _CHANNELS
        and parts[3] in _RESPONSE_EVENT_METRICS
    ) or (
        len(parts) == 4
        and parts[:2] == ["response", "campaign"]
        and _campaign_key_segment(parts[2])
        and parts[3] in _RESPONSE_EVENT_METRICS
    ):
        return _RESPONSE_ARTIFACTS
    elif (
        len(parts) == 5
        and parts[:3] == ["response", "overall", "state"]
        and parts[3] in _STATE_METRICS
        and parts[4] in _STATE_AGGREGATES
    ) or (
        len(parts) == 6
        and parts[:2] == ["response", "campaign"]
        and _campaign_key_segment(parts[2])
        and parts[3] == "state"
        and parts[4] in _STATE_METRICS
        and parts[5] in _STATE_AGGREGATES
    ):
        return _STATE_ARTIFACTS
    raise ValueError("spatial metric observation key does not match the versioned grammar")


class SpatialMetricObservation(DomainModel):
    """One finite scalar, its exact quotient receipt, and its source artifacts."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    key: str = Field(min_length=1, max_length=_MAX_KEY_LENGTH, pattern=_KEY_PATTERN)
    value: float = Field(ge=-MAX_SPATIAL_OPPORTUNITIES, le=MAX_SPATIAL_OPPORTUNITIES)
    numerator: float = Field(ge=-MAX_SPATIAL_OPPORTUNITIES, le=MAX_SPATIAL_OPPORTUNITIES)
    denominator: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    source_artifacts: tuple[SpatialStudyMetricArtifact, ...] = Field(
        min_length=1,
        max_length=2,
    )

    @field_validator("value", "numerator", mode="before")
    @classmethod
    def exact_float(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("spatial metric observation scalars must be floats")
        return value

    @field_validator("denominator", mode="before")
    @classmethod
    def exact_integer_denominator(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("spatial metric observation denominator must be an integer")
        return value

    @model_validator(mode="after")
    def coherent_observation(self) -> Self:
        expected_artifacts = _source_artifacts_for_key(self.key)
        if self.source_artifacts != expected_artifacts:
            raise ValueError("spatial metric observation sources do not match its key")
        expected_value = self.numerator / self.denominator if self.denominator else 0.0
        if self.value != expected_value:
            raise ValueError("spatial metric observation value must equal its receipt quotient")
        if self.value == 0.0 and math.copysign(1.0, self.value) < 0:
            raise ValueError("spatial metric observation zero must have a positive sign")
        if self.numerator == 0.0 and math.copysign(1.0, self.numerator) < 0:
            raise ValueError("spatial metric observation numerator zero must have a positive sign")
        return self


def _observation(
    key: str,
    *,
    value: float,
    numerator: int | float,
    denominator: int,
    source_artifacts: tuple[SpatialStudyMetricArtifact, ...],
) -> SpatialMetricObservation:
    return SpatialMetricObservation(
        key=key,
        value=float(value),
        numerator=float(numerator),
        denominator=denominator,
        source_artifacts=source_artifacts,
    )


def _attention_observations(
    metrics: SpatialMetrics,
) -> Iterable[SpatialMetricObservation]:
    for scope, series in (
        ("overall", metrics.overall),
        *((f"channel.{item.channel}", item) for item in metrics.channels),
    ):
        for name in sorted(_ATTENTION_METRICS):
            receipt = cast(SpatialMetricReceipt, getattr(series, name))
            yield _observation(
                f"attention.{scope}.{name}",
                value=receipt.value,
                numerator=receipt.numerator,
                denominator=receipt.denominator,
                source_artifacts=cast(
                    tuple[SpatialStudyMetricArtifact, ...],
                    receipt.source_artifacts,
                ),
            )


def _response_event_observations(
    metrics: SpatialResponseMetrics,
) -> Iterable[SpatialMetricObservation]:
    for scope, series in (
        ("overall", metrics.overall),
        *((f"channel.{item.channel}", item) for item in metrics.channels),
        *((f"campaign.{item.campaign_id}", item) for item in metrics.campaigns),
    ):
        for name in sorted(_RESPONSE_EVENT_METRICS):
            receipt = cast(_ResponseEventReceipt, getattr(series, name))
            yield _observation(
                f"response.{scope}.{name}",
                value=receipt.value,
                numerator=receipt.numerator,
                denominator=receipt.denominator,
                source_artifacts=receipt.source_artifacts,
            )


def _state_observation(
    scope: str,
    name: str,
    aggregate: str,
    receipt: _ResponseStateReceipt,
) -> SpatialMetricObservation:
    numerator_name = {
        "initial_mean": "initial_total",
        "final_mean": "final_total",
        "mean_change": "change_total",
    }[aggregate]
    return _observation(
        f"response.{scope}.state.{name}.{aggregate}",
        value=getattr(receipt, aggregate),
        numerator=getattr(receipt, numerator_name),
        denominator=receipt.denominator,
        source_artifacts=receipt.source_artifacts,
    )


def _response_state_observations(
    metrics: SpatialResponseMetrics,
) -> Iterable[SpatialMetricObservation]:
    # State is intentionally absent from channel series. Same-minute notices from two
    # channels may share one nonlinear committed state transition, so channel allocation
    # would invent evidence the response model did not record.
    for scope, series in (
        ("overall", metrics.overall),
        *((f"campaign.{item.campaign_id}", item) for item in metrics.campaigns),
    ):
        for name in sorted(_STATE_METRICS):
            receipt = cast(_ResponseStateReceipt, getattr(series, name))
            for aggregate in sorted(_STATE_AGGREGATES):
                yield _state_observation(scope, name, aggregate, receipt)


def _validate_matched_provenance(
    attention: SpatialMetrics,
    response: SpatialResponseMetrics,
) -> None:
    if attention.source_run_schema_version != 6 or response.source_run_schema_version != 6:
        raise ValueError("response observations require schema-v6 spatial metrics")
    checks = (
        (attention.opportunity_model_id, response.opportunity_model_id),
        (attention.attention_model_id, response.attention_model_id),
        (attention.scenario_sha256, response.scenario_sha256),
        (attention.city_sha256, response.city_sha256),
        (attention.agents_sha256, response.agents_sha256),
        (attention.trace_sha256, response.trace_sha256),
        (attention.opportunity_structure_sha256, response.opportunity_structure_sha256),
        (attention.seed, response.seed),
        (attention.population_size, response.population_size),
        (attention.days, response.days),
    )
    if any(left != right for left, right in checks):
        raise ValueError("attention and response metrics do not share exact provenance")


def spatial_metric_observations(
    attention: SpatialMetrics,
    response: SpatialResponseMetrics | None = None,
) -> tuple[SpatialMetricObservation, ...]:
    """Flatten validated attention and optional response metrics into canonical scalars."""
    checked_attention = revalidate_model(
        attention,
        SpatialMetrics,
        label="spatial attention metrics",
    )
    observations = list(_attention_observations(checked_attention))
    if response is not None:
        checked_response = revalidate_model(
            response,
            SpatialResponseMetrics,
            label="spatial response metrics",
        )
        _validate_matched_provenance(checked_attention, checked_response)
        observations.extend(_response_event_observations(checked_response))
        observations.extend(_response_state_observations(checked_response))

    ordered = tuple(sorted(observations, key=lambda item: item.key))
    keys = tuple(item.key for item in ordered)
    if len(keys) != len(set(keys)):
        raise ValueError("spatial metric observations contain a duplicate key")
    return ordered


__all__ = [
    "SpatialMetricObservation",
    "SpatialStudyMetricArtifact",
    "spatial_metric_observations",
]
