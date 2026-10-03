"""Pure artifact-derived metrics for synthetic spatial city studies.

These values summarize model evidence. They are not observed advertising outcomes and
do not introduce a response, purchase, or causal-effect model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
from typing import Literal, Self, TypeAlias

from pydantic import Field, model_validator

from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionEvaluation,
    SpatialImpression,
    SpatialNotice,
)
from adlife.core.simulation.spatial_opportunity import (
    MAX_SPATIAL_OPPORTUNITIES,
    PhoneOpportunity,
    RoadsideOpportunity,
    SpatialOpportunity,
    SpatialOpportunityEvaluation,
)

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_AGENT_PATTERN = re.compile(r"^person-[0-9]{3}$")
_MAX_CITY_AGENTS = 30
_MAX_DAYS = 7

SpatialMetricName: TypeAlias = Literal[
    "opportunity_count",
    "impression_count",
    "noticed_count",
    "opportunity_reach",
    "impression_reach",
    "noticed_reach",
    "impression_frequency",
    "notice_rate",
]
SpatialMetricChannel: TypeAlias = Literal["overall", "roadside", "mobile"]
SpatialMetricEventType: TypeAlias = Literal[
    "spatial.opportunity", "spatial.impression", "spatial.noticed"
]
SpatialMetricArtifact: TypeAlias = Literal[
    "outputs/spatial-opportunities.jsonl", "outputs/spatial-attention.jsonl"
]

_OPPORTUNITY_ARTIFACT: tuple[SpatialMetricArtifact, ...] = ("outputs/spatial-opportunities.jsonl",)
_ATTENTION_ARTIFACT: tuple[SpatialMetricArtifact, ...] = ("outputs/spatial-attention.jsonl",)
_SOURCES: dict[
    SpatialMetricName,
    tuple[tuple[SpatialMetricEventType, ...], tuple[SpatialMetricArtifact, ...]],
] = {
    "opportunity_count": (("spatial.opportunity",), _OPPORTUNITY_ARTIFACT),
    "impression_count": (("spatial.impression",), _ATTENTION_ARTIFACT),
    "noticed_count": (("spatial.noticed",), _ATTENTION_ARTIFACT),
    "opportunity_reach": (("spatial.opportunity",), _OPPORTUNITY_ARTIFACT),
    "impression_reach": (("spatial.impression",), _ATTENTION_ARTIFACT),
    "noticed_reach": (("spatial.noticed",), _ATTENTION_ARTIFACT),
    "impression_frequency": (("spatial.impression",), _ATTENTION_ARTIFACT),
    "notice_rate": (
        ("spatial.impression", "spatial.noticed"),
        _ATTENTION_ARTIFACT,
    ),
}
_METRIC_NAMES: tuple[SpatialMetricName, ...] = (
    "opportunity_count",
    "impression_count",
    "noticed_count",
    "opportunity_reach",
    "impression_reach",
    "noticed_reach",
    "impression_frequency",
    "notice_rate",
)


class SpatialMetricReceipt(DomainModel):
    """One auditable numerator/denominator calculation and its source records."""

    schema_version: Literal[1] = 1
    name: SpatialMetricName
    numerator: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    denominator: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    value: float = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    source_event_types: tuple[SpatialMetricEventType, ...] = Field(min_length=1, max_length=2)
    source_artifacts: tuple[SpatialMetricArtifact, ...] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def coherent_receipt(self) -> Self:
        expected_value = self.numerator / self.denominator if self.denominator else 0.0
        if self.value != expected_value:
            raise ValueError("metric value must equal its numerator/denominator quotient")
        expected_events, expected_artifacts = _SOURCES[self.name]
        if (
            self.source_event_types != expected_events
            or self.source_artifacts != expected_artifacts
        ):
            raise ValueError("metric receipt sources do not match its metric name")
        return self


class SpatialMetricSeries(DomainModel):
    """The fixed metric set for all evidence or for one channel."""

    schema_version: Literal[1] = 1
    channel: SpatialMetricChannel
    opportunity_count: SpatialMetricReceipt
    impression_count: SpatialMetricReceipt
    noticed_count: SpatialMetricReceipt
    opportunity_reach: SpatialMetricReceipt
    impression_reach: SpatialMetricReceipt
    noticed_reach: SpatialMetricReceipt
    impression_frequency: SpatialMetricReceipt
    notice_rate: SpatialMetricReceipt

    @model_validator(mode="after")
    def coherent_series(self) -> Self:
        for name in _METRIC_NAMES:
            receipt = getattr(self, name)
            if receipt.name != name:
                raise ValueError("metric receipt is stored under the wrong series field")
        for count in (self.opportunity_count, self.impression_count, self.noticed_count):
            if count.denominator != 1:
                raise ValueError("metric count receipts must use denominator one")
        if self.impression_count.numerator != self.opportunity_count.numerator:
            raise ValueError("every retained opportunity must have one impression")
        if self.noticed_count.numerator > self.impression_count.numerator:
            raise ValueError("noticed count cannot exceed impression count")
        if self.impression_frequency.numerator != self.impression_count.numerator:
            raise ValueError("impression frequency numerator must be the impression count")
        if self.impression_frequency.denominator != self.impression_reach.numerator:
            raise ValueError("impression frequency denominator must be reached agents")
        if self.notice_rate.numerator != self.noticed_count.numerator:
            raise ValueError("notice rate numerator must be the noticed count")
        if self.notice_rate.denominator != self.impression_count.numerator:
            raise ValueError("notice rate denominator must be the impression count")
        return self


class SpatialMetrics(DomainModel):
    """Frozen schema-v5 study provenance plus deterministic synthetic metrics."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-metrics-v1"] = "spatial-metrics-v1"
    claim_scope: Literal["synthetic-metrics-not-observed-outcomes"] = (
        "synthetic-metrics-not-observed-outcomes"
    )
    source_run_schema_version: Literal[5] = 5
    opportunity_model_id: Literal["spatial-opportunity-v1"] = "spatial-opportunity-v1"
    attention_model_id: Literal["spatial-attention-v1"] = "spatial-attention-v1"
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)
    population_size: int = Field(ge=1, le=_MAX_CITY_AGENTS)
    days: int = Field(ge=1, le=_MAX_DAYS)
    overall: SpatialMetricSeries
    channels: tuple[SpatialMetricSeries, SpatialMetricSeries]

    @model_validator(mode="after")
    def coherent_document(self) -> Self:
        if tuple(item.channel for item in self.channels) != ("roadside", "mobile"):
            raise ValueError("metric channels must be ordered roadside then mobile")
        if self.overall.channel != "overall":
            raise ValueError("overall metrics must use the overall channel")
        for series in (self.overall, *self.channels):
            for reach in (
                series.opportunity_reach,
                series.impression_reach,
                series.noticed_reach,
            ):
                if reach.denominator != self.population_size:
                    raise ValueError("reach denominator must equal the declared population")
                if reach.numerator > self.population_size:
                    raise ValueError("reached agents cannot exceed the declared population")
        roadside, mobile = self.channels
        for name in ("opportunity_count", "impression_count", "noticed_count"):
            if getattr(self.overall, name).numerator != (
                getattr(roadside, name).numerator + getattr(mobile, name).numerator
            ):
                raise ValueError("channel metric counts do not add up to the overall count")
        return self


def _structure_record(opportunity: SpatialOpportunity) -> Mapping[str, object]:
    common: dict[str, object] = {
        "placement_id": opportunity.placement_id,
        "channel": opportunity.channel,
        "agent_id": opportunity.agent_id,
        "day_index": opportunity.day_index,
        "model_minute": opportunity.model_minute,
        "millisecond_within_minute": opportunity.millisecond_within_minute,
    }
    if isinstance(opportunity, RoadsideOpportunity):
        return common | {
            "road_id": opportunity.road_id,
            "travel_direction": opportunity.travel_direction,
            "road_fraction": opportunity.road_fraction,
            "side": opportunity.side,
            "minimum_distance_meters": opportunity.minimum_distance_meters,
            "approach_distance_meters": opportunity.approach_distance_meters,
            "view_angle_degrees": opportunity.view_angle_degrees,
        }
    if isinstance(opportunity, PhoneOpportunity):
        return common | {
            "activity": opportunity.activity,
            "eligibility_draw": opportunity.eligibility_draw,
            "opportunity_probability_per_minute": (opportunity.opportunity_probability_per_minute),
        }
    raise TypeError("unsupported spatial opportunity type")


def spatial_opportunity_structure_sha256(
    evaluation: SpatialOpportunityEvaluation,
) -> str:
    """Hash normalized model structure while excluding campaign/scenario identities."""
    validated = revalidate_model(
        evaluation,
        SpatialOpportunityEvaluation,
        label="spatial opportunity evaluation",
    )
    document = {
        "model_id": "spatial-opportunity-structure-v1",
        "opportunities": [_structure_record(item) for item in validated.opportunities],
    }
    return sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _validate_population(agent_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(agent_ids, (str, bytes)) or not isinstance(agent_ids, Sequence):
        raise TypeError("agent_ids must be a sequence of agent ID strings")
    identifiers = tuple(agent_ids)
    if not identifiers:
        raise ValueError("agent_ids must contain at least one agent")
    if len(identifiers) > _MAX_CITY_AGENTS:
        raise ValueError(f"agent_ids cannot contain more than {_MAX_CITY_AGENTS} agents")
    if any(
        type(identifier) is not str or _AGENT_PATTERN.fullmatch(identifier) is None
        for identifier in identifiers
    ):
        raise ValueError("every agent ID must match person-NNN")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("agent_ids must not contain duplicate identifiers")
    return tuple(sorted(identifiers))


def _receipt(name: SpatialMetricName, numerator: int, denominator: int) -> SpatialMetricReceipt:
    events, artifacts = _SOURCES[name]
    return SpatialMetricReceipt(
        name=name,
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else 0.0,
        source_event_types=events,
        source_artifacts=artifacts,
    )


def _series(
    channel: SpatialMetricChannel,
    *,
    opportunities: Sequence[SpatialOpportunity],
    impressions: Sequence[SpatialImpression],
    notices: Sequence[SpatialNotice],
    population_size: int,
) -> SpatialMetricSeries:
    opportunity_agents = len({item.agent_id for item in opportunities})
    impression_agents = len({item.agent_id for item in impressions})
    noticed_agents = len({item.agent_id for item in notices})
    return SpatialMetricSeries(
        channel=channel,
        opportunity_count=_receipt("opportunity_count", len(opportunities), 1),
        impression_count=_receipt("impression_count", len(impressions), 1),
        noticed_count=_receipt("noticed_count", len(notices), 1),
        opportunity_reach=_receipt("opportunity_reach", opportunity_agents, population_size),
        impression_reach=_receipt("impression_reach", impression_agents, population_size),
        noticed_reach=_receipt("noticed_reach", noticed_agents, population_size),
        impression_frequency=_receipt("impression_frequency", len(impressions), impression_agents),
        notice_rate=_receipt("notice_rate", len(notices), len(impressions)),
    )


def _validate_source_pair(
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
) -> None:
    if (
        opportunities.scenario_sha256 != attention.scenario_sha256
        or opportunities.city_sha256 != attention.city_sha256
    ):
        raise ValueError("opportunities and attention must describe the same scenario and city")
    if opportunities.counts.opportunity_count != attention.counts.opportunity_count:
        raise ValueError("opportunity and attention counts do not match")
    by_id = {item.opportunity_id: item for item in opportunities.opportunities}
    impressions = [event for event in attention.events if isinstance(event, SpatialImpression)]
    for impression in impressions:
        opportunity = by_id.get(impression.opportunity_id)
        if opportunity is None:
            raise ValueError("attention references an unknown spatial opportunity")
        shared = (
            "scenario_sha256",
            "city_sha256",
            "campaign_id",
            "placement_id",
            "agent_id",
            "channel",
            "day_index",
            "model_minute",
            "millisecond_within_minute",
        )
        if any(getattr(opportunity, name) != getattr(impression, name) for name in shared):
            raise ValueError("attention evidence does not match its spatial opportunity")


def _agents_in_evidence(
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
) -> set[str]:
    return {
        *(item.agent_id for item in opportunities.opportunities),
        *(event.agent_id for event in attention.events),
    }


def _ensure_within_duration(
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    *,
    days: int,
) -> None:
    duration_minutes = days * 1_440
    if any(item.model_minute >= duration_minutes for item in opportunities.opportunities) or any(
        event.model_minute >= duration_minutes for event in attention.events
    ):
        raise ValueError("spatial evidence falls outside the declared study duration")


def derive_spatial_metrics(
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    *,
    agent_ids: Sequence[str],
    agents_sha256: str,
    trace_sha256: str,
    days: int,
) -> SpatialMetrics:
    """Derive finite synthetic metrics from a validated opportunity/attention pair."""
    validated_opportunities = revalidate_model(
        opportunities,
        SpatialOpportunityEvaluation,
        label="spatial opportunity evaluation",
    )
    validated_attention = revalidate_model(
        attention,
        SpatialAttentionEvaluation,
        label="spatial attention evaluation",
    )
    _validate_source_pair(validated_opportunities, validated_attention)
    population = _validate_population(agent_ids)
    if type(days) is not int or not 1 <= days <= _MAX_DAYS:
        raise ValueError(f"days must be an integer between 1 and {_MAX_DAYS}")
    _ensure_within_duration(validated_opportunities, validated_attention, days=days)
    unknown = _agents_in_evidence(validated_opportunities, validated_attention) - set(population)
    if unknown:
        raise ValueError(
            "spatial evidence contains an agent absent from the declared population: "
            + ", ".join(sorted(unknown))
        )

    all_opportunities = validated_opportunities.opportunities
    all_impressions = tuple(
        event for event in validated_attention.events if isinstance(event, SpatialImpression)
    )
    all_notices = tuple(
        event for event in validated_attention.events if isinstance(event, SpatialNotice)
    )

    def channel_items(
        items: Iterable[SpatialOpportunity], channel: str
    ) -> tuple[SpatialOpportunity, ...]:
        return tuple(item for item in items if item.channel == channel)

    def channel_impressions(
        items: Iterable[SpatialImpression], channel: str
    ) -> tuple[SpatialImpression, ...]:
        return tuple(item for item in items if item.channel == channel)

    def channel_notices(items: Iterable[SpatialNotice], channel: str) -> tuple[SpatialNotice, ...]:
        return tuple(item for item in items if item.channel == channel)

    roadside = _series(
        "roadside",
        opportunities=channel_items(all_opportunities, "roadside-billboard"),
        impressions=channel_impressions(all_impressions, "roadside-billboard"),
        notices=channel_notices(all_notices, "roadside-billboard"),
        population_size=len(population),
    )
    mobile = _series(
        "mobile",
        opportunities=channel_items(all_opportunities, "mobile-feed"),
        impressions=channel_impressions(all_impressions, "mobile-feed"),
        notices=channel_notices(all_notices, "mobile-feed"),
        population_size=len(population),
    )
    return SpatialMetrics(
        scenario_sha256=validated_opportunities.scenario_sha256,
        city_sha256=validated_opportunities.city_sha256,
        agents_sha256=agents_sha256,
        trace_sha256=trace_sha256,
        opportunity_structure_sha256=spatial_opportunity_structure_sha256(validated_opportunities),
        seed=validated_attention.seed,
        population_size=len(population),
        days=days,
        overall=_series(
            "overall",
            opportunities=all_opportunities,
            impressions=all_impressions,
            notices=all_notices,
            population_size=len(population),
        ),
        channels=(roadside, mobile),
    )


__all__ = [
    "SpatialMetricReceipt",
    "SpatialMetricSeries",
    "SpatialMetrics",
    "derive_spatial_metrics",
    "spatial_opportunity_structure_sha256",
]
