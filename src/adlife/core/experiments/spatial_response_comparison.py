"""Honest response-metric deltas for matched synthetic city runs.

The comparison keeps opportunity and numeric response-assumption confounding
separate. Every scalar is treatment minus control; none is presented as a causal,
observed, population, purchase, or sales effect.
"""

from __future__ import annotations

from typing import Literal, Self, TypeAlias

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator

from adlife.core.domain.person import DomainModel, contains_secret_or_email_text
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseAggregateSeries,
    SpatialResponseCampaignSeries,
    SpatialResponseChannelSeries,
    SpatialResponseEventSeries,
    SpatialResponseMetrics,
    SpatialResponseStateReceipt,
)
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_response import MAX_SPATIAL_RESPONSES

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_EVENT_NAMES = (
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
)
SpatialOpportunityComparisonClassification: TypeAlias = Literal[
    "matched-opportunity-structure",
    "opportunity-confounded",
]
SpatialResponseAssumptionComparisonClassification: TypeAlias = Literal[
    "matched-response-assumptions",
    "response-assumption-confounded",
]


class SpatialResponseComparisonError(ValueError):
    """Raised when two valid response-metric documents are not comparable."""


class _ExactFloatDeltaModel(DomainModel):
    """Base for delta records whose public numeric leaves are exact floats."""

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def exact_float_delta(cls, value: object, info: ValidationInfo) -> object:
        if (
            info.field_name
            in {
                *_EVENT_NAMES,
                "initial_mean",
                "final_mean",
                "mean_change",
            }
            and type(value) is not float
        ):
            raise ValueError("spatial response comparison deltas must be floats")
        return value


class SpatialResponseEventDeltaSeries(_ExactFloatDeltaModel):
    """Treatment-minus-control values for one response-event metric slice."""

    schema_version: Literal[1] = 1
    response_count: float = Field(ge=-MAX_SPATIAL_RESPONSES, le=MAX_SPATIAL_RESPONSES)
    response_reach: float = Field(ge=-1, le=1)
    response_frequency: float = Field(
        ge=-MAX_SPATIAL_RESPONSES,
        le=MAX_SPATIAL_RESPONSES,
    )
    mean_rule_sentiment_delta: float = Field(ge=-2, le=2)
    mean_rule_recall_delta: float = Field(ge=-1, le=1)


class SpatialResponseStateDelta(_ExactFloatDeltaModel):
    """Treatment-minus-control values for one committed state metric."""

    schema_version: Literal[1] = 1
    initial_mean: float = Field(ge=-2, le=2)
    final_mean: float = Field(ge=-2, le=2)
    mean_change: float = Field(ge=-4, le=4)


class SpatialResponseAggregateDeltaSeries(SpatialResponseEventDeltaSeries):
    """Response-event and committed-state deltas for an aggregate slice."""

    brand_sentiment: SpatialResponseStateDelta
    recall_strength: SpatialResponseStateDelta
    purchase_intention_proxy: SpatialResponseStateDelta


class SpatialResponseChannelDeltaSeries(SpatialResponseEventDeltaSeries):
    """Response-event deltas for one channel; committed state is unattributed."""

    channel: Literal["roadside", "mobile"]


class SpatialResponseCampaignDeltaSeries(SpatialResponseAggregateDeltaSeries):
    """Response-event and committed-state deltas for one campaign."""

    model_config = ConfigDict(hide_input_in_errors=True)
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")

    @field_validator("campaign_id")
    @classmethod
    def safe_campaign_identifier(cls, value: str) -> str:
        if contains_secret_or_email_text(value):
            raise ValueError("campaign identifier has a forbidden credential-shaped value")
        return value


def _positive_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _event_deltas(
    control: SpatialResponseEventSeries,
    treatment: SpatialResponseEventSeries,
) -> dict[str, float]:
    return {
        name: _positive_zero(float(getattr(treatment, name).value - getattr(control, name).value))
        for name in _EVENT_NAMES
    }


def _state_delta(
    control: SpatialResponseStateReceipt,
    treatment: SpatialResponseStateReceipt,
) -> SpatialResponseStateDelta:
    if control.name != treatment.name:
        raise SpatialResponseComparisonError("response state metric order differs between runs")
    return SpatialResponseStateDelta(
        initial_mean=_positive_zero(treatment.initial_mean - control.initial_mean),
        final_mean=_positive_zero(treatment.final_mean - control.final_mean),
        mean_change=_positive_zero(treatment.mean_change - control.mean_change),
    )


def _aggregate_delta(
    control: SpatialResponseAggregateSeries,
    treatment: SpatialResponseAggregateSeries,
) -> SpatialResponseAggregateDeltaSeries:
    events = _event_deltas(control, treatment)
    return SpatialResponseAggregateDeltaSeries(
        response_count=events["response_count"],
        response_reach=events["response_reach"],
        response_frequency=events["response_frequency"],
        mean_rule_sentiment_delta=events["mean_rule_sentiment_delta"],
        mean_rule_recall_delta=events["mean_rule_recall_delta"],
        brand_sentiment=_state_delta(
            control.brand_sentiment,
            treatment.brand_sentiment,
        ),
        recall_strength=_state_delta(
            control.recall_strength,
            treatment.recall_strength,
        ),
        purchase_intention_proxy=_state_delta(
            control.purchase_intention_proxy,
            treatment.purchase_intention_proxy,
        ),
    )


def _channel_delta(
    control: SpatialResponseChannelSeries,
    treatment: SpatialResponseChannelSeries,
) -> SpatialResponseChannelDeltaSeries:
    if control.channel != treatment.channel:
        raise SpatialResponseComparisonError("response channel order differs between runs")
    events = _event_deltas(control, treatment)
    return SpatialResponseChannelDeltaSeries(
        channel=control.channel,
        response_count=events["response_count"],
        response_reach=events["response_reach"],
        response_frequency=events["response_frequency"],
        mean_rule_sentiment_delta=events["mean_rule_sentiment_delta"],
        mean_rule_recall_delta=events["mean_rule_recall_delta"],
    )


def _campaign_delta(
    control: SpatialResponseCampaignSeries,
    treatment: SpatialResponseCampaignSeries,
) -> SpatialResponseCampaignDeltaSeries:
    if control.campaign_id != treatment.campaign_id:
        raise SpatialResponseComparisonError("campaign set differs between the spatial runs")
    aggregate = _aggregate_delta(control, treatment)
    return SpatialResponseCampaignDeltaSeries(
        campaign_id=control.campaign_id,
        **aggregate.model_dump(mode="python"),
    )


def _campaign_ids(metrics: SpatialResponseMetrics) -> tuple[str, ...]:
    return tuple(item.campaign_id for item in metrics.campaigns)


def _validate_matched_provenance(
    control: SpatialResponseMetrics,
    treatment: SpatialResponseMetrics,
) -> None:
    checks = (
        (
            "source run schema",
            control.source_run_schema_version,
            treatment.source_run_schema_version,
        ),
        ("opportunity model", control.opportunity_model_id, treatment.opportunity_model_id),
        ("attention model", control.attention_model_id, treatment.attention_model_id),
        ("response model", control.response_model_id, treatment.response_model_id),
        ("city fingerprint", control.city_sha256, treatment.city_sha256),
        ("mobility trace", control.trace_sha256, treatment.trace_sha256),
        ("agent assignment", control.agents_sha256, treatment.agents_sha256),
        ("seed", control.seed, treatment.seed),
        ("duration", control.days, treatment.days),
        ("population", control.population_size, treatment.population_size),
    )
    for label, control_value, treatment_value in checks:
        if control_value != treatment_value:
            raise SpatialResponseComparisonError(
                f"{label} differs between the spatial response runs"
            )
    if _campaign_ids(control) != _campaign_ids(treatment):
        raise SpatialResponseComparisonError("campaign set differs between the spatial runs")


def _opportunity_classification(
    control: SpatialResponseMetrics,
    treatment: SpatialResponseMetrics,
) -> SpatialOpportunityComparisonClassification:
    if control.opportunity_structure_sha256 == treatment.opportunity_structure_sha256:
        return "matched-opportunity-structure"
    return "opportunity-confounded"


def _assumption_classification(
    control: SpatialResponseMetrics,
    treatment: SpatialResponseMetrics,
) -> SpatialResponseAssumptionComparisonClassification:
    if (
        control.response_assumption_structure_sha256
        == treatment.response_assumption_structure_sha256
    ):
        return "matched-response-assumptions"
    return "response-assumption-confounded"


class SpatialResponseMetricsComparison(DomainModel):
    """Full response snapshots and auditable raw deltas for one matched run pair."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    model_id: Literal["spatial-response-metrics-comparison-v1"] = (
        "spatial-response-metrics-comparison-v1"
    )
    claim_scope: Literal["synthetic-response-comparison-not-causal-or-observed-effect"] = (
        "synthetic-response-comparison-not-causal-or-observed-effect"
    )
    opportunity_classification: SpatialOpportunityComparisonClassification
    response_assumption_classification: SpatialResponseAssumptionComparisonClassification
    control_run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    treatment_run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    control_scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    treatment_scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)
    population_size: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    campaign_count: int = Field(ge=1, le=20)
    control_opportunity_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    treatment_opportunity_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    control_response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    treatment_response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    control_response_assumption_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    treatment_response_assumption_structure_sha256: str = Field(pattern=_HASH_PATTERN)
    control: SpatialResponseMetrics
    treatment: SpatialResponseMetrics
    overall: SpatialResponseAggregateDeltaSeries
    channels: tuple[
        SpatialResponseChannelDeltaSeries,
        SpatialResponseChannelDeltaSeries,
    ]
    campaigns: tuple[SpatialResponseCampaignDeltaSeries, ...] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("control_run_id", "treatment_run_id")
    @classmethod
    def safe_run_identifier(cls, value: str) -> str:
        try:
            return validate_run_id(value)
        except UnsafeRunLocation:
            raise ValueError("spatial response comparison run identifier is invalid") from None

    @model_validator(mode="after")
    def coherent_comparison(self) -> Self:
        control = revalidate_model(
            self.control,
            SpatialResponseMetrics,
            label="control spatial response metrics",
        )
        treatment = revalidate_model(
            self.treatment,
            SpatialResponseMetrics,
            label="treatment spatial response metrics",
        )
        _validate_matched_provenance(control, treatment)

        expected_fields = {
            "control_scenario_sha256": control.scenario_sha256,
            "treatment_scenario_sha256": treatment.scenario_sha256,
            "city_sha256": control.city_sha256,
            "agents_sha256": control.agents_sha256,
            "trace_sha256": control.trace_sha256,
            "seed": control.seed,
            "population_size": control.population_size,
            "days": control.days,
            "campaign_count": control.campaign_count,
            "control_opportunity_structure_sha256": (control.opportunity_structure_sha256),
            "treatment_opportunity_structure_sha256": (treatment.opportunity_structure_sha256),
            "control_response_input_sha256": control.response_input_sha256,
            "treatment_response_input_sha256": treatment.response_input_sha256,
            "control_response_assumption_structure_sha256": (
                control.response_assumption_structure_sha256
            ),
            "treatment_response_assumption_structure_sha256": (
                treatment.response_assumption_structure_sha256
            ),
        }
        if any(getattr(self, name) != value for name, value in expected_fields.items()):
            raise ValueError("response comparison provenance does not match its source snapshots")

        if self.opportunity_classification != _opportunity_classification(control, treatment):
            raise ValueError("response comparison opportunity classification is incoherent")
        if self.response_assumption_classification != _assumption_classification(
            control,
            treatment,
        ):
            raise ValueError("response comparison assumption classification is incoherent")

        expected_channels = tuple(
            _channel_delta(control_series, treatment_series)
            for control_series, treatment_series in zip(
                control.channels,
                treatment.channels,
                strict=True,
            )
        )
        expected_campaigns = tuple(
            _campaign_delta(control_series, treatment_series)
            for control_series, treatment_series in zip(
                control.campaigns,
                treatment.campaigns,
                strict=True,
            )
        )
        if self.overall != _aggregate_delta(control.overall, treatment.overall):
            raise ValueError("response comparison overall deltas do not match source snapshots")
        if self.channels != expected_channels:
            raise ValueError("response comparison channel deltas do not match source snapshots")
        if self.campaigns != expected_campaigns:
            raise ValueError("response comparison campaign deltas do not match source snapshots")
        return self


def compare_spatial_response_metrics(
    control: SpatialResponseMetrics,
    treatment: SpatialResponseMetrics,
    *,
    control_run_id: str,
    treatment_run_id: str,
) -> SpatialResponseMetricsComparison:
    """Compare matched response metrics without calling their raw deltas causal."""
    validated_control = revalidate_model(
        control,
        SpatialResponseMetrics,
        label="control spatial response metrics",
    )
    validated_treatment = revalidate_model(
        treatment,
        SpatialResponseMetrics,
        label="treatment spatial response metrics",
    )
    _validate_matched_provenance(validated_control, validated_treatment)

    channel_deltas = tuple(
        _channel_delta(control_series, treatment_series)
        for control_series, treatment_series in zip(
            validated_control.channels,
            validated_treatment.channels,
            strict=True,
        )
    )
    campaign_deltas = tuple(
        _campaign_delta(control_series, treatment_series)
        for control_series, treatment_series in zip(
            validated_control.campaigns,
            validated_treatment.campaigns,
            strict=True,
        )
    )
    return SpatialResponseMetricsComparison(
        opportunity_classification=_opportunity_classification(
            validated_control,
            validated_treatment,
        ),
        response_assumption_classification=_assumption_classification(
            validated_control,
            validated_treatment,
        ),
        control_run_id=control_run_id,
        treatment_run_id=treatment_run_id,
        control_scenario_sha256=validated_control.scenario_sha256,
        treatment_scenario_sha256=validated_treatment.scenario_sha256,
        city_sha256=validated_control.city_sha256,
        agents_sha256=validated_control.agents_sha256,
        trace_sha256=validated_control.trace_sha256,
        seed=validated_control.seed,
        population_size=validated_control.population_size,
        days=validated_control.days,
        campaign_count=validated_control.campaign_count,
        control_opportunity_structure_sha256=(validated_control.opportunity_structure_sha256),
        treatment_opportunity_structure_sha256=(validated_treatment.opportunity_structure_sha256),
        control_response_input_sha256=validated_control.response_input_sha256,
        treatment_response_input_sha256=validated_treatment.response_input_sha256,
        control_response_assumption_structure_sha256=(
            validated_control.response_assumption_structure_sha256
        ),
        treatment_response_assumption_structure_sha256=(
            validated_treatment.response_assumption_structure_sha256
        ),
        control=validated_control,
        treatment=validated_treatment,
        overall=_aggregate_delta(validated_control.overall, validated_treatment.overall),
        channels=(channel_deltas[0], channel_deltas[1]),
        campaigns=campaign_deltas,
    )


__all__ = [
    "SpatialOpportunityComparisonClassification",
    "SpatialResponseAggregateDeltaSeries",
    "SpatialResponseAssumptionComparisonClassification",
    "SpatialResponseCampaignDeltaSeries",
    "SpatialResponseChannelDeltaSeries",
    "SpatialResponseComparisonError",
    "SpatialResponseEventDeltaSeries",
    "SpatialResponseMetricsComparison",
    "SpatialResponseStateDelta",
    "compare_spatial_response_metrics",
]
