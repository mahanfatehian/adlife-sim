"""Auditable metrics derived from synthetic spatial response artifacts.

The metrics in this module describe deterministic model output.  They are neither
observed outcomes nor purchase, sales, population, or causal-effect estimates.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Collection, Mapping, Sequence
from hashlib import sha256
from typing import Literal, Self, TypeAlias, TypedDict

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.person import (
    DomainModel,
    contains_secret_or_email_text,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.domain.spatial_response import (
    SpatialResponseInitialState,
    SpatialResponseInput,
    validate_spatial_response_input,
)
from adlife.core.experiments.spatial_metrics import (
    SpatialMetrics,
    derive_spatial_metrics,
)
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_attention import SpatialAttentionEvaluation
from adlife.core.simulation.spatial_opportunity import SpatialOpportunityEvaluation
from adlife.core.simulation.spatial_response import (
    MAX_SPATIAL_RESPONSES,
    SpatialResponseEvaluation,
    SpatialResponseState,
    SpatialRuleResponse,
    evaluate_spatial_responses,
    summarize_spatial_response_artifact,
)

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_MAX_CITY_AGENTS = 30
_MAX_CAMPAIGNS = 20
_MAX_STATE_ROWS = _MAX_CITY_AGENTS * _MAX_CAMPAIGNS

SpatialResponseMetricName: TypeAlias = Literal[
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
]
SpatialResponseStateMetricName: TypeAlias = Literal[
    "brand_sentiment",
    "recall_strength",
    "purchase_intention_proxy",
]
SpatialResponseMetricChannel: TypeAlias = Literal["roadside", "mobile"]
SpatialResponseMetricEventType: TypeAlias = Literal["spatial.response"]
SpatialResponseMetricArtifact: TypeAlias = Literal["outputs/spatial-responses.jsonl"]
SpatialResponseStateArtifact: TypeAlias = Literal[
    "inputs/spatial-response.json", "outputs/response-state.json"
]

_RESPONSE_ARTIFACT: tuple[SpatialResponseMetricArtifact, ...] = ("outputs/spatial-responses.jsonl",)
_STATE_ARTIFACTS: tuple[SpatialResponseStateArtifact, SpatialResponseStateArtifact] = (
    "inputs/spatial-response.json",
    "outputs/response-state.json",
)
_EVENT_NAMES: tuple[SpatialResponseMetricName, ...] = (
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
)
_STATE_NAMES: tuple[SpatialResponseStateMetricName, ...] = (
    "brand_sentiment",
    "recall_strength",
    "purchase_intention_proxy",
)


class _EventSeriesValues(TypedDict):
    response_count: SpatialResponseMetricReceipt
    response_reach: SpatialResponseMetricReceipt
    response_frequency: SpatialResponseMetricReceipt
    mean_rule_sentiment_delta: SpatialResponseMetricReceipt
    mean_rule_recall_delta: SpatialResponseMetricReceipt


def _positive_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _matches_partitioned_fsum(total: float, parts: Sequence[float]) -> bool:
    """Compare direct and partitioned ``fsum`` results within their rounding budget."""
    regrouped = math.fsum(parts)
    if total == regrouped:
        return True
    if len(parts) <= 1 or all(abs(part) < sys.float_info.min for part in parts):
        # A singleton performs no regrouping. A sum of only zero/subnormal binary64
        # values is exactly representable, so neither case has a rounding budget.
        return False
    # Each persisted partition and each of the two final sums may be rounded by at
    # most half an ulp.  This is the narrow error bound implied by regrouping the
    # same finite inputs; a general relative tolerance would admit material changes.
    rounding_budget = (
        math.fsum((math.ulp(total), math.ulp(regrouped), *(math.ulp(part) for part in parts))) / 2.0
    )
    return abs(total - regrouped) <= rounding_budget


class SpatialResponseMetricReceipt(DomainModel):
    """One event-derived response numerator/denominator calculation."""

    schema_version: Literal[1] = 1
    name: SpatialResponseMetricName
    numerator: int | float = Field(ge=-MAX_SPATIAL_RESPONSES, le=MAX_SPATIAL_RESPONSES)
    denominator: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    value: float = Field(ge=-MAX_SPATIAL_RESPONSES, le=MAX_SPATIAL_RESPONSES)
    source_event_types: tuple[SpatialResponseMetricEventType, ...] = ("spatial.response",)
    source_artifacts: tuple[SpatialResponseMetricArtifact, ...] = _RESPONSE_ARTIFACT

    @model_validator(mode="after")
    def coherent_receipt(self) -> Self:
        count_names = {"response_count", "response_reach", "response_frequency"}
        if self.name in count_names and type(self.numerator) is not int:
            raise ValueError("response count receipt numerator must be an integer")
        if self.name not in count_names and type(self.numerator) is not float:
            raise ValueError("response mean receipt numerator must be a float")
        expected = self.numerator / self.denominator if self.denominator else 0.0
        expected = _positive_zero(float(expected))
        if self.value != expected or (self.value == 0.0 and math.copysign(1.0, self.value) < 0):
            raise ValueError("response metric value must equal its numerator/denominator quotient")
        if self.source_event_types != ("spatial.response",) or (
            self.source_artifacts != _RESPONSE_ARTIFACT
        ):
            raise ValueError("response metric receipt sources do not match its metric name")
        return self


class SpatialResponseStateReceipt(DomainModel):
    """One complete-grid initial/final state calculation."""

    schema_version: Literal[1] = 1
    name: SpatialResponseStateMetricName
    initial_total: float = Field(ge=-_MAX_STATE_ROWS, le=_MAX_STATE_ROWS)
    final_total: float = Field(ge=-_MAX_STATE_ROWS, le=_MAX_STATE_ROWS)
    change_total: float = Field(ge=-(_MAX_STATE_ROWS * 2), le=_MAX_STATE_ROWS * 2)
    denominator: int = Field(ge=1, le=_MAX_STATE_ROWS)
    initial_mean: float = Field(ge=-1, le=1)
    final_mean: float = Field(ge=-1, le=1)
    mean_change: float = Field(ge=-2, le=2)
    source_artifacts: tuple[SpatialResponseStateArtifact, SpatialResponseStateArtifact] = (
        _STATE_ARTIFACTS
    )

    @model_validator(mode="after")
    def coherent_receipt(self) -> Self:
        change = _positive_zero(self.final_total - self.initial_total)
        if self.change_total != change:
            raise ValueError("response state change must equal final total minus initial total")
        if self.initial_mean != self.initial_total / self.denominator:
            raise ValueError("response initial mean must equal its total/denominator quotient")
        if self.final_mean != self.final_total / self.denominator:
            raise ValueError("response final mean must equal its total/denominator quotient")
        mean_change = _positive_zero(self.change_total / self.denominator)
        if self.mean_change != mean_change or (
            self.mean_change == 0.0 and math.copysign(1.0, self.mean_change) < 0
        ):
            raise ValueError("response mean change must equal its total/denominator quotient")
        if self.source_artifacts != _STATE_ARTIFACTS:
            raise ValueError("response state receipt sources do not match its metric name")
        if self.name != "brand_sentiment" and (self.initial_total < 0 or self.final_total < 0):
            raise ValueError("bounded response strength totals cannot be negative")
        return self


class SpatialResponseEventSeries(DomainModel):
    """The fixed response-event metric set for one population slice."""

    schema_version: Literal[1] = 1
    response_count: SpatialResponseMetricReceipt
    response_reach: SpatialResponseMetricReceipt
    response_frequency: SpatialResponseMetricReceipt
    mean_rule_sentiment_delta: SpatialResponseMetricReceipt
    mean_rule_recall_delta: SpatialResponseMetricReceipt

    @model_validator(mode="after")
    def coherent_series(self) -> Self:
        for name in _EVENT_NAMES:
            if getattr(self, name).name != name:
                raise ValueError("response metric receipt is stored under the wrong series field")
        if self.response_count.denominator != 1:
            raise ValueError("response count must use denominator one")
        if self.response_frequency.numerator != self.response_count.numerator:
            raise ValueError("response frequency numerator must equal response count")
        if self.response_frequency.denominator != self.response_reach.numerator:
            raise ValueError("response frequency denominator must equal reached agents")
        for receipt in (
            self.mean_rule_sentiment_delta,
            self.mean_rule_recall_delta,
        ):
            if receipt.denominator != self.response_count.numerator:
                raise ValueError("response mean denominator must equal response count")
        return self


class SpatialResponseAggregateSeries(SpatialResponseEventSeries):
    """Event and committed-state metrics for the overall population."""

    brand_sentiment: SpatialResponseStateReceipt
    recall_strength: SpatialResponseStateReceipt
    purchase_intention_proxy: SpatialResponseStateReceipt

    @model_validator(mode="after")
    def coherent_state_fields(self) -> Self:
        for name in _STATE_NAMES:
            if getattr(self, name).name != name:
                raise ValueError("response state receipt is stored under the wrong series field")
        denominators = {getattr(self, name).denominator for name in _STATE_NAMES}
        if len(denominators) != 1:
            raise ValueError("response state metrics must use one complete-grid denominator")
        return self


class SpatialResponseChannelSeries(SpatialResponseEventSeries):
    """Response event metrics for one channel; state is intentionally unattributed."""

    channel: SpatialResponseMetricChannel


class SpatialResponseCampaignSeries(SpatialResponseAggregateSeries):
    """Response event and committed-state metrics for one fictional campaign."""

    model_config = ConfigDict(hide_input_in_errors=True)
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")

    @field_validator("campaign_id")
    @classmethod
    def safe_campaign_identifier(cls, value: str) -> str:
        if contains_secret_or_email_text(value):
            raise ValueError("campaign identifier has a forbidden credential-shaped value")
        return value


class SpatialResponseMetrics(DomainModel):
    """Frozen provenance and deterministic metrics for a schema-v6/v7 response run."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    model_id: Literal["spatial-response-metrics-v1"] = "spatial-response-metrics-v1"
    claim_scope: Literal["synthetic-response-metrics-not-observed-outcomes"] = (
        "synthetic-response-metrics-not-observed-outcomes"
    )
    source_run_schema_version: Literal[6, 7] = 6
    opportunity_model_id: Literal["spatial-opportunity-v1"] = "spatial-opportunity-v1"
    attention_model_id: Literal["spatial-attention-v1"] = "spatial-attention-v1"
    response_model_id: Literal["spatial-response-v1"] = "spatial-response-v1"
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    response_assumption_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    response_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    response_state_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)
    population_size: int = Field(ge=1, le=_MAX_CITY_AGENTS)
    days: int = Field(ge=1, le=7)
    campaign_count: int = Field(ge=1, le=_MAX_CAMPAIGNS)
    overall: SpatialResponseAggregateSeries
    channels: tuple[SpatialResponseChannelSeries, SpatialResponseChannelSeries]
    campaigns: tuple[SpatialResponseCampaignSeries, ...] = Field(
        min_length=1,
        max_length=_MAX_CAMPAIGNS,
    )

    @field_validator("source_run_schema_version", mode="before")
    @classmethod
    def exact_source_schema(cls, value: object) -> object:
        if type(value) is not int or value not in {6, 7}:
            raise ValueError("source run schema version must be integer 6 or 7")
        return value

    @model_validator(mode="after")
    def coherent_document(self) -> Self:
        if tuple(item.channel for item in self.channels) != ("roadside", "mobile"):
            raise ValueError("response metric channels must be ordered roadside then mobile")
        campaign_ids = tuple(item.campaign_id for item in self.campaigns)
        if campaign_ids != tuple(sorted(campaign_ids)) or len(campaign_ids) != len(
            set(campaign_ids)
        ):
            raise ValueError("response metric campaigns must be unique and canonical")
        if self.campaign_count != len(self.campaigns):
            raise ValueError("response campaign count does not match campaign metrics")

        all_series: tuple[SpatialResponseEventSeries, ...] = (
            self.overall,
            *self.channels,
            *self.campaigns,
        )
        for series in all_series:
            if series.response_reach.denominator != self.population_size:
                raise ValueError("response reach denominator must equal population size")
            if series.response_reach.numerator > self.population_size:
                raise ValueError("response reach cannot exceed population size")

        roadside, mobile = self.channels
        if self.overall.response_count.numerator != (
            roadside.response_count.numerator + mobile.response_count.numerator
        ):
            raise ValueError("response channel counts do not add up to overall count")
        if self.overall.response_count.numerator != sum(
            item.response_count.numerator for item in self.campaigns
        ):
            raise ValueError("response campaign counts do not add up to overall count")
        for name in ("mean_rule_sentiment_delta", "mean_rule_recall_delta"):
            overall = getattr(self.overall, name)
            for slices in (self.channels, self.campaigns):
                numerators = tuple(float(getattr(item, name).numerator) for item in slices)
                if not _matches_partitioned_fsum(
                    float(overall.numerator), numerators
                ) or overall.denominator != sum(getattr(item, name).denominator for item in slices):
                    raise ValueError("response slice means do not add up to overall evidence")

        expected_overall_denominator = self.population_size * self.campaign_count
        for name in _STATE_NAMES:
            overall_state = getattr(self.overall, name)
            if overall_state.denominator != expected_overall_denominator:
                raise ValueError("overall state denominator must cover every agent/campaign pair")
            campaign_states = tuple(getattr(item, name) for item in self.campaigns)
            if any(item.denominator != self.population_size for item in campaign_states):
                raise ValueError("campaign state denominator must cover every agent")
            initial_totals = tuple(item.initial_total for item in campaign_states)
            final_totals = tuple(item.final_total for item in campaign_states)
            if not _matches_partitioned_fsum(
                overall_state.initial_total, initial_totals
            ) or not _matches_partitioned_fsum(overall_state.final_total, final_totals):
                raise ValueError("campaign state totals do not add up to overall state")
        return self


def _validated_response_sources(
    response_input: SpatialResponseInput,
    scenario: SpatialCampaignScenario,
) -> tuple[SpatialResponseInput, SpatialCampaignScenario]:
    validated_input = revalidate_model(
        response_input,
        SpatialResponseInput,
        label="spatial response input",
    )
    validated_scenario = revalidate_model(
        scenario,
        SpatialCampaignScenario,
        label="spatial response scenario",
    )
    validate_spatial_response_input(
        validated_input,
        validated_scenario,
        agent_ids=tuple(profile.agent_id for profile in validated_input.profiles),
    )
    for campaign in validated_input.campaigns:
        if contains_secret_or_email_text(campaign.campaign_id):
            raise ValueError("campaign identifier has a forbidden credential-shaped value")
    return validated_input, validated_scenario


def spatial_response_assumption_structure_sha256(
    response_input: SpatialResponseInput,
    scenario: SpatialCampaignScenario,
) -> str:
    """Hash only the numeric response-rule assumptions and placement rule bindings."""
    validated_input, validated_scenario = _validated_response_sources(response_input, scenario)
    document: Mapping[str, object] = {
        "model_id": "spatial-response-assumption-structure-v1",
        "profiles": [
            {
                "agent_id": profile.agent_id,
                "fictional": profile.fictional,
                "interests": sorted(profile.interests),
                "traits": profile.traits.model_dump(mode="json"),
            }
            for profile in validated_input.profiles
        ],
        "campaigns": [
            {
                "campaign_id": campaign.campaign_id,
                "target_interests": sorted(campaign.target_interests),
                "relative_price": campaign.relative_price,
            }
            for campaign in validated_input.campaigns
        ],
        "placements": [
            {
                "placement_id": placement.placement_id,
                "campaign_id": placement.campaign_id,
                "channel": placement.channel,
                "frequency_cap_per_agent_per_day": (placement.frequency_cap_per_agent_per_day),
            }
            for placement in validated_scenario.placements
        ],
        "initial_states": [
            state.model_dump(mode="json") for state in validated_input.initial_states
        ],
    }
    return sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _event_receipt(
    name: SpatialResponseMetricName,
    numerator: int | float,
    denominator: int,
) -> SpatialResponseMetricReceipt:
    value = numerator / denominator if denominator else 0.0
    return SpatialResponseMetricReceipt(
        name=name,
        numerator=numerator,
        denominator=denominator,
        value=_positive_zero(float(value)),
    )


def _event_series(
    responses: Sequence[SpatialRuleResponse],
    *,
    population_size: int,
) -> _EventSeriesValues:
    count = len(responses)
    reach = len({item.agent_id for item in responses})
    return {
        "response_count": _event_receipt("response_count", count, 1),
        "response_reach": _event_receipt("response_reach", reach, population_size),
        "response_frequency": _event_receipt("response_frequency", count, reach),
        "mean_rule_sentiment_delta": _event_receipt(
            "mean_rule_sentiment_delta",
            _positive_zero(math.fsum(item.sentiment_delta for item in responses)),
            count,
        ),
        "mean_rule_recall_delta": _event_receipt(
            "mean_rule_recall_delta",
            _positive_zero(math.fsum(item.recall_delta for item in responses)),
            count,
        ),
    }


def _state_value(
    state: SpatialResponseInitialState | SpatialResponseState,
    name: SpatialResponseStateMetricName,
) -> float:
    if name == "purchase_intention_proxy":
        return state.purchase_intention
    return float(getattr(state, name))


def _state_receipt(
    name: SpatialResponseStateMetricName,
    initial_states: Sequence[SpatialResponseInitialState],
    final_states: Sequence[SpatialResponseState],
) -> SpatialResponseStateReceipt:
    if len(initial_states) != len(final_states) or not initial_states:
        raise ValueError("response state metric requires matching complete state grids")
    initial_total = math.fsum(_state_value(item, name) for item in initial_states)
    final_total = math.fsum(_state_value(item, name) for item in final_states)
    change_total = _positive_zero(final_total - initial_total)
    denominator = len(initial_states)
    return SpatialResponseStateReceipt(
        name=name,
        initial_total=initial_total,
        final_total=final_total,
        change_total=change_total,
        denominator=denominator,
        initial_mean=initial_total / denominator,
        final_mean=final_total / denominator,
        mean_change=_positive_zero(change_total / denominator),
    )


def _aggregate_series(
    responses: Sequence[SpatialRuleResponse],
    *,
    initial_states: Sequence[SpatialResponseInitialState],
    final_states: Sequence[SpatialResponseState],
    population_size: int,
) -> SpatialResponseAggregateSeries:
    events = _event_series(responses, population_size=population_size)
    return SpatialResponseAggregateSeries(
        **events,
        brand_sentiment=_state_receipt("brand_sentiment", initial_states, final_states),
        recall_strength=_state_receipt("recall_strength", initial_states, final_states),
        purchase_intention_proxy=_state_receipt(
            "purchase_intention_proxy", initial_states, final_states
        ),
    )


def _validated_population(agent_ids: Collection[str]) -> tuple[str, ...]:
    if isinstance(agent_ids, (str, bytes)) or not isinstance(agent_ids, Collection):
        raise TypeError("agent_ids must be a collection of agent identifiers")
    identifiers = tuple(agent_ids)
    if not identifiers or len(identifiers) > _MAX_CITY_AGENTS:
        raise ValueError("agent_ids must contain between 1 and 30 identifiers")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("agent_ids must not contain duplicate identifiers")
    if any(
        type(identifier) is not str
        or len(identifier) != 10
        or not identifier.startswith("person-")
        or not identifier[7:].isdigit()
        for identifier in identifiers
    ):
        raise ValueError("every agent ID must match person-NNN")
    return tuple(sorted(identifiers))


def derive_spatial_response_metrics(
    response_input: SpatialResponseInput,
    response: SpatialResponseEvaluation,
    *,
    scenario: SpatialCampaignScenario,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    agent_ids: Collection[str],
    attention_metrics: SpatialMetrics,
) -> SpatialResponseMetrics:
    """Re-derive and summarize one complete schema-v6/v7 spatial response run."""
    validated_input, validated_scenario = _validated_response_sources(response_input, scenario)
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
    validated_response = revalidate_model(
        response,
        SpatialResponseEvaluation,
        label="spatial response evaluation",
    )
    validated_attention_metrics = revalidate_model(
        attention_metrics,
        SpatialMetrics,
        label="spatial attention metrics",
    )
    response_source_version: Literal[6, 7]
    if validated_attention_metrics.source_run_schema_version == 6:
        response_source_version = 6
    elif validated_attention_metrics.source_run_schema_version == 7:
        response_source_version = 7
    else:
        raise ValueError("spatial response metrics require source run schema 6 or 7")
    population = _validated_population(agent_ids)
    validate_spatial_response_input(
        validated_input,
        validated_scenario,
        agent_ids=population,
    )

    expected_attention_metrics = derive_spatial_metrics(
        validated_opportunities,
        validated_attention,
        agent_ids=population,
        agents_sha256=validated_attention_metrics.agents_sha256,
        trace_sha256=validated_attention_metrics.trace_sha256,
        days=validated_attention_metrics.days,
        source_run_schema_version=response_source_version,
    )
    if expected_attention_metrics != validated_attention_metrics:
        raise ValueError("spatial attention metrics do not match their source evidence")
    if validated_response.attention_seed != validated_attention.seed:
        raise ValueError("spatial response attention seed does not match attention evidence")
    expected_response = evaluate_spatial_responses(
        validated_input,
        validated_scenario,
        validated_opportunities,
        validated_attention,
        agent_ids=population,
    )
    if expected_response != validated_response:
        raise ValueError("spatial response evidence does not match deterministic response evidence")

    responses = tuple(
        item for item in validated_response.records if isinstance(item, SpatialRuleResponse)
    )
    summary = summarize_spatial_response_artifact(validated_response)
    initial_states = validated_input.initial_states
    final_states = validated_response.final_states

    channels: list[SpatialResponseChannelSeries] = []
    channel_specs: tuple[
        tuple[Literal["roadside"], Literal["roadside-billboard"]],
        tuple[Literal["mobile"], Literal["mobile-feed"]],
    ] = (
        ("roadside", "roadside-billboard"),
        ("mobile", "mobile-feed"),
    )
    for label, source_channel in channel_specs:
        channel_responses = tuple(item for item in responses if item.channel == source_channel)
        channels.append(
            SpatialResponseChannelSeries(
                channel=label,
                **_event_series(channel_responses, population_size=len(population)),
            )
        )

    campaigns: list[SpatialResponseCampaignSeries] = []
    for campaign in validated_input.campaigns:
        campaign_id = campaign.campaign_id
        campaign_responses = tuple(item for item in responses if item.campaign_id == campaign_id)
        campaign_initial = tuple(item for item in initial_states if item.campaign_id == campaign_id)
        campaign_final = tuple(item for item in final_states if item.campaign_id == campaign_id)
        aggregate = _aggregate_series(
            campaign_responses,
            initial_states=campaign_initial,
            final_states=campaign_final,
            population_size=len(population),
        )
        campaigns.append(
            SpatialResponseCampaignSeries(
                campaign_id=campaign_id,
                **aggregate.model_dump(mode="python"),
            )
        )

    return SpatialResponseMetrics(
        source_run_schema_version=response_source_version,
        scenario_sha256=validated_response.scenario_sha256,
        city_sha256=validated_response.city_sha256,
        agents_sha256=validated_attention_metrics.agents_sha256,
        trace_sha256=validated_attention_metrics.trace_sha256,
        opportunity_structure_sha256=(validated_attention_metrics.opportunity_structure_sha256),
        response_input_sha256=validated_response.response_input_sha256,
        response_assumption_structure_sha256=(
            spatial_response_assumption_structure_sha256(
                validated_input,
                validated_scenario,
            )
        ),
        response_stream_sha256=summary.stream_sha256,
        response_state_sha256=summary.state_document_sha256,
        seed=validated_response.attention_seed,
        population_size=len(population),
        days=validated_attention_metrics.days,
        campaign_count=len(validated_input.campaigns),
        overall=_aggregate_series(
            responses,
            initial_states=initial_states,
            final_states=final_states,
            population_size=len(population),
        ),
        channels=(channels[0], channels[1]),
        campaigns=tuple(campaigns),
    )


__all__ = [
    "SpatialResponseAggregateSeries",
    "SpatialResponseCampaignSeries",
    "SpatialResponseChannelSeries",
    "SpatialResponseEventSeries",
    "SpatialResponseMetricReceipt",
    "SpatialResponseMetrics",
    "SpatialResponseStateReceipt",
    "derive_spatial_response_metrics",
    "spatial_response_assumption_structure_sha256",
]
