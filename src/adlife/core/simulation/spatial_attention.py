"""Deterministic synthetic attention evidence derived from spatial opportunities."""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, model_validator

from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.spatial_opportunity import (
    MAX_SPATIAL_OPPORTUNITIES,
    SpatialOpportunity,
    SpatialOpportunityEvaluation,
)

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_MODEL_ID: Literal["spatial-attention-v1"] = "spatial-attention-v1"
_CLAIM_SCOPE: Literal["synthetic-attention-not-observed-behavior"] = (
    "synthetic-attention-not-observed-behavior"
)
_NOTICE_PROBABILITY = 0.5
_MAX_SEED = 2**63 - 1
MAX_SPATIAL_ATTENTION_EVENTS = MAX_SPATIAL_OPPORTUNITIES * 2
MAX_SPATIAL_ATTENTION_STREAM_BYTES = 1_073_741_824


class _SpatialAttentionEvent(DomainModel):
    schema_version: Literal[1] = 1
    model_id: Literal["spatial-attention-v1"] = _MODEL_ID
    claim_scope: Literal["synthetic-attention-not-observed-behavior"] = _CLAIM_SCOPE
    event_id: str = Field(pattern=_HASH_PATTERN)
    caused_by: str = Field(pattern=_HASH_PATTERN)
    opportunity_id: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    campaign_id: str = Field(pattern=_ID_PATTERN)
    placement_id: str = Field(pattern=_ID_PATTERN)
    agent_id: str = Field(pattern=r"^person-[0-9]{3}$")
    channel: Literal["roadside-billboard", "mobile-feed"]
    day_index: int = Field(ge=0, le=6)
    model_minute: int = Field(ge=0, lt=10_080)
    millisecond_within_minute: int = Field(ge=0, lt=60_000)
    notice_probability: float = Field(default=_NOTICE_PROBABILITY, ge=0.5, le=0.5)
    notice_draw: float = Field(ge=0, lt=1)

    @model_validator(mode="after")
    def coherent_time(self) -> Self:
        if self.day_index != self.model_minute // 1_440:
            raise ValueError("attention event day does not match model minute")
        return self


class SpatialImpression(_SpatialAttentionEvent):
    """One model impression caused by one retained synthetic opportunity."""

    event_type: Literal["spatial.impression"] = "spatial.impression"
    noticed: bool

    @model_validator(mode="after")
    def coherent_notice_decision(self) -> Self:
        if self.noticed is not (self.notice_draw < self.notice_probability):
            raise ValueError("impression notice decision does not match its draw")
        return self


class SpatialNotice(_SpatialAttentionEvent):
    """One model notice caused by an impression whose neutral draw passed."""

    event_type: Literal["spatial.noticed"] = "spatial.noticed"

    @model_validator(mode="after")
    def passing_draw(self) -> Self:
        if self.notice_draw >= self.notice_probability:
            raise ValueError("notice event requires a passing notice draw")
        return self


SpatialAttentionEvent: TypeAlias = Annotated[
    SpatialImpression | SpatialNotice,
    Field(discriminator="event_type"),
]


class SpatialAttentionCounts(DomainModel):
    """Exact opportunity, impression and noticed totals split by channel."""

    schema_version: Literal[1] = 1
    opportunity_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    impression_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    noticed_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    roadside_impression_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    roadside_noticed_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    phone_impression_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)
    phone_noticed_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)

    @model_validator(mode="after")
    def coherent_funnel(self) -> Self:
        if self.impression_count != self.opportunity_count:
            raise ValueError("every spatial opportunity must produce one impression")
        if self.impression_count != (self.roadside_impression_count + self.phone_impression_count):
            raise ValueError("attention impression channel counts do not match total")
        if self.noticed_count != self.roadside_noticed_count + self.phone_noticed_count:
            raise ValueError("attention noticed channel counts do not match total")
        if self.noticed_count > self.impression_count:
            raise ValueError("noticed count cannot exceed impression count")
        if self.roadside_noticed_count > self.roadside_impression_count:
            raise ValueError("roadside noticed count cannot exceed impressions")
        if self.phone_noticed_count > self.phone_impression_count:
            raise ValueError("phone noticed count cannot exceed impressions")
        return self


class SpatialAttentionEvaluation(DomainModel):
    """Canonical immutable attention events and their transparent funnel counts."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-attention-v1"] = _MODEL_ID
    claim_scope: Literal["synthetic-attention-not-observed-behavior"] = _CLAIM_SCOPE
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=_MAX_SEED)
    notice_probability: float = Field(default=_NOTICE_PROBABILITY, ge=0.5, le=0.5)
    counts: SpatialAttentionCounts
    events: tuple[SpatialAttentionEvent, ...] = Field(max_length=MAX_SPATIAL_ATTENTION_EVENTS)

    @model_validator(mode="after")
    def coherent_records(self) -> Self:
        if len(self.events) != self.counts.impression_count + self.counts.noticed_count:
            raise ValueError("attention records do not match counts")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("attention event identifiers must be unique")
        if any(
            event.scenario_sha256 != self.scenario_sha256
            or event.city_sha256 != self.city_sha256
            or event.notice_probability != self.notice_probability
            for event in self.events
        ):
            raise ValueError("attention event evidence does not match evaluation")

        keys = tuple(_attention_sort_key(event) for event in self.events)
        if keys != tuple(sorted(keys)):
            raise ValueError("attention records must be in canonical order")

        impressions: dict[str, SpatialImpression] = {}
        notices: dict[str, SpatialNotice] = {}
        for event in self.events:
            expected_id = spatial_attention_event_id(
                event_type=event.event_type,
                caused_by=event.caused_by,
            )
            if event.event_id != expected_id:
                raise ValueError("attention event causal identity does not match event ID")
            if isinstance(event, SpatialImpression):
                expected_draw = spatial_notice_draw(
                    agent_id=event.agent_id,
                    at_millisecond=(event.model_minute * 60_000 + event.millisecond_within_minute),
                    channel=event.channel,
                    placement_id=event.placement_id,
                    seed=self.seed,
                )
                if event.notice_draw != expected_draw:
                    raise ValueError("attention notice draw does not match its keyed draw")
                if event.caused_by != event.opportunity_id:
                    raise ValueError("impression causal predecessor must be its opportunity")
                if event.opportunity_id in impressions:
                    raise ValueError("an opportunity cannot produce multiple impressions")
                impressions[event.opportunity_id] = event
            else:
                if event.caused_by in notices:
                    raise ValueError("an impression cannot produce multiple notice events")
                notices[event.caused_by] = event

        if len(impressions) != self.counts.opportunity_count:
            raise ValueError("attention impression records do not match opportunity count")
        for impression in impressions.values():
            notice = notices.get(impression.event_id)
            if impression.noticed is not (notice is not None):
                raise ValueError("attention records do not match impression notice decisions")
            if notice is not None and _shared_event_evidence(notice) != _shared_event_evidence(
                impression
            ):
                raise ValueError("notice evidence does not match its causal impression")
        impression_event_ids = {item.event_id for item in impressions.values()}
        if len(notices) != self.counts.noticed_count or any(
            cause not in impression_event_ids for cause in notices
        ):
            raise ValueError("notice records do not match their causal impressions")

        actual = _counts_for_events(self.events)
        if actual != self.counts:
            raise ValueError("attention records do not match counts")
        return self


class SpatialAttentionArtifactSummary(DomainModel):
    """Exact hashes and counts for one canonical attention JSONL stream."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-attention-artifact-v1"] = "spatial-attention-artifact-v1"
    claim_scope: Literal["synthetic-attention-not-observed-behavior"] = _CLAIM_SCOPE
    attention_model_id: Literal["spatial-attention-v1"] = _MODEL_ID
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    notice_probability: float = Field(default=_NOTICE_PROBABILITY, ge=0.5, le=0.5)
    stream_sha256: str = Field(pattern=_HASH_PATTERN)
    stream_bytes: int = Field(ge=0, le=MAX_SPATIAL_ATTENTION_STREAM_BYTES)
    counts: SpatialAttentionCounts


def spatial_attention_event_id(*, event_type: str, caused_by: str) -> str:
    identity = {
        "caused_by": caused_by,
        "event_type": event_type,
        "model_id": _MODEL_ID,
    }
    return sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def spatial_notice_draw(
    *,
    agent_id: str,
    at_millisecond: int,
    channel: str,
    placement_id: str,
    seed: int,
) -> float:
    material = {
        "agent_id": agent_id,
        "at_millisecond": at_millisecond,
        "channel": channel,
        "model_id": _MODEL_ID,
        "placement_id": placement_id,
        "seed": seed,
    }
    digest = sha256(canonical_json(material).encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") >> 11) / (1 << 53)


def _notice_draw(opportunity: SpatialOpportunity, *, seed: int) -> float:
    return spatial_notice_draw(
        agent_id=opportunity.agent_id,
        at_millisecond=(opportunity.model_minute * 60_000 + opportunity.millisecond_within_minute),
        channel=opportunity.channel,
        placement_id=opportunity.placement_id,
        seed=seed,
    )


def _shared_event_evidence(event: _SpatialAttentionEvent) -> tuple[object, ...]:
    return (
        event.opportunity_id,
        event.scenario_sha256,
        event.city_sha256,
        event.campaign_id,
        event.placement_id,
        event.agent_id,
        event.channel,
        event.day_index,
        event.model_minute,
        event.millisecond_within_minute,
        event.notice_probability,
        event.notice_draw,
    )


def _attention_sort_key(event: _SpatialAttentionEvent) -> tuple[object, ...]:
    return (
        event.model_minute,
        event.millisecond_within_minute,
        event.agent_id,
        event.campaign_id,
        event.placement_id,
        event.channel,
        0 if isinstance(event, SpatialImpression) else 1,
    )


def _counts_for_events(
    events: tuple[SpatialAttentionEvent, ...],
) -> SpatialAttentionCounts:
    impressions = tuple(event for event in events if isinstance(event, SpatialImpression))
    notices = tuple(event for event in events if isinstance(event, SpatialNotice))
    return SpatialAttentionCounts(
        opportunity_count=len(impressions),
        impression_count=len(impressions),
        noticed_count=len(notices),
        roadside_impression_count=sum(
            event.channel == "roadside-billboard" for event in impressions
        ),
        roadside_noticed_count=sum(event.channel == "roadside-billboard" for event in notices),
        phone_impression_count=sum(event.channel == "mobile-feed" for event in impressions),
        phone_noticed_count=sum(event.channel == "mobile-feed" for event in notices),
    )


def _event_evidence(opportunity: SpatialOpportunity, *, notice_draw: float) -> dict[str, object]:
    return {
        "opportunity_id": opportunity.opportunity_id,
        "scenario_sha256": opportunity.scenario_sha256,
        "city_sha256": opportunity.city_sha256,
        "campaign_id": opportunity.campaign_id,
        "placement_id": opportunity.placement_id,
        "agent_id": opportunity.agent_id,
        "channel": opportunity.channel,
        "day_index": opportunity.day_index,
        "model_minute": opportunity.model_minute,
        "millisecond_within_minute": opportunity.millisecond_within_minute,
        "notice_draw": notice_draw,
    }


def evaluate_spatial_attention(
    opportunities: SpatialOpportunityEvaluation,
    *,
    seed: int,
) -> SpatialAttentionEvaluation:
    """Create deterministic synthetic impression and notice evidence."""
    if not isinstance(opportunities, SpatialOpportunityEvaluation):
        raise TypeError("opportunities must be SpatialOpportunityEvaluation")
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    if not 0 <= seed <= _MAX_SEED:
        raise ValueError(f"seed must be between 0 and {_MAX_SEED}")

    events: list[SpatialImpression | SpatialNotice] = []
    for opportunity in opportunities.opportunities:
        draw = _notice_draw(opportunity, seed=seed)
        noticed = draw < _NOTICE_PROBABILITY
        impression_id = spatial_attention_event_id(
            event_type="spatial.impression",
            caused_by=opportunity.opportunity_id,
        )
        evidence = _event_evidence(opportunity, notice_draw=draw)
        events.append(
            SpatialImpression.model_validate(
                {
                    "event_id": impression_id,
                    "event_type": "spatial.impression",
                    "caused_by": opportunity.opportunity_id,
                    "noticed": noticed,
                    **evidence,
                }
            )
        )
        if noticed:
            events.append(
                SpatialNotice.model_validate(
                    {
                        "event_id": spatial_attention_event_id(
                            event_type="spatial.noticed",
                            caused_by=impression_id,
                        ),
                        "event_type": "spatial.noticed",
                        "caused_by": impression_id,
                        **evidence,
                    }
                )
            )

    event_tuple: tuple[SpatialAttentionEvent, ...] = tuple(events)
    return SpatialAttentionEvaluation(
        scenario_sha256=opportunities.scenario_sha256,
        city_sha256=opportunities.city_sha256,
        seed=seed,
        counts=_counts_for_events(event_tuple),
        events=event_tuple,
    )


def spatial_attention_lines(evaluation: SpatialAttentionEvaluation) -> Iterator[bytes]:
    """Yield one canonical UTF-8 JSONL record for every attention event."""
    if not isinstance(evaluation, SpatialAttentionEvaluation):
        raise TypeError("evaluation must be SpatialAttentionEvaluation")
    for event in evaluation.events:
        yield (canonical_json(event) + "\n").encode("utf-8")


def summarize_spatial_attention_artifact(
    evaluation: SpatialAttentionEvaluation,
) -> SpatialAttentionArtifactSummary:
    """Hash canonical stream bytes without joining the full artifact in memory."""
    digest = sha256()
    stream_bytes = 0
    for line in spatial_attention_lines(evaluation):
        stream_bytes += len(line)
        if stream_bytes > MAX_SPATIAL_ATTENTION_STREAM_BYTES:
            raise ValueError("spatial attention stream exceeds its size limit")
        digest.update(line)
    return SpatialAttentionArtifactSummary(
        scenario_sha256=evaluation.scenario_sha256,
        city_sha256=evaluation.city_sha256,
        notice_probability=evaluation.notice_probability,
        stream_sha256=digest.hexdigest(),
        stream_bytes=stream_bytes,
        counts=evaluation.counts,
    )


__all__ = [
    "MAX_SPATIAL_ATTENTION_EVENTS",
    "MAX_SPATIAL_ATTENTION_STREAM_BYTES",
    "SpatialAttentionArtifactSummary",
    "SpatialAttentionCounts",
    "SpatialAttentionEvaluation",
    "SpatialAttentionEvent",
    "SpatialImpression",
    "SpatialNotice",
    "evaluate_spatial_attention",
    "spatial_attention_event_id",
    "spatial_attention_lines",
    "spatial_notice_draw",
    "summarize_spatial_attention_artifact",
]
