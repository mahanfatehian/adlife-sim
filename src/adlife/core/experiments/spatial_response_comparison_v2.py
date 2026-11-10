"""V10 response comparison: rule-baseline deltas, cognition provenance only.

Treatment-minus-control scalars exist only for the rule-owned baseline. Provider
signals are carried as labelled snapshots and a provenance classification. They are
never subtracted, and they are never a purchase, lift, or campaign-effect quantity.
"""

from __future__ import annotations

from typing import Literal, Self, TypeAlias

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.person import DomainModel
from adlife.core.experiments.spatial_response_comparison import (
    SpatialOpportunityComparisonClassification,
    SpatialResponseAggregateDeltaSeries,
    SpatialResponseAssumptionComparisonClassification,
    SpatialResponseCampaignDeltaSeries,
    SpatialResponseChannelDeltaSeries,
    SpatialResponseComparisonError,
    SpatialResponseStateDelta,
)
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseCampaignSeries,
    SpatialResponseChannelSeries,
    SpatialResponseStateReceipt,
)
from adlife.core.experiments.spatial_response_metrics_v2 import (
    SpatialProviderDependentCognitionMetricsV2,
    SpatialResponseMetricsV2,
    SpatialRuleBaselineMetricsV2,
)
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id
from adlife.core.simulation._validation import revalidate_model

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_EVENT_NAMES = (
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
)
_PROVENANCE_FIELDS = (
    "causal_contract_sha256",
    "prompt_contract_sha256",
    "result_schema_contract_sha256",
    "influence_policy_sha256",
    "provider_revision_sha256",
    "effective_settings_sha256",
)
CognitionProvenanceClassification: TypeAlias = Literal[
    "matched-provider-dependent-exploratory",
    "incompatible-provider-provenance",
]


def _exact_schema_version(value: object) -> object:
    if type(value) is not int or value != 2:
        raise ValueError("comparison schema version must be integer 2")
    return value


def _positive_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _event_deltas(control: object, treatment: object) -> dict[str, float]:
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
    control: SpatialRuleBaselineMetricsV2 | SpatialResponseCampaignSeries,
    treatment: SpatialRuleBaselineMetricsV2 | SpatialResponseCampaignSeries,
) -> SpatialResponseAggregateDeltaSeries:
    events = _event_deltas(control, treatment)
    return SpatialResponseAggregateDeltaSeries(
        response_count=events["response_count"],
        response_reach=events["response_reach"],
        response_frequency=events["response_frequency"],
        mean_rule_sentiment_delta=events["mean_rule_sentiment_delta"],
        mean_rule_recall_delta=events["mean_rule_recall_delta"],
        brand_sentiment=_state_delta(control.brand_sentiment, treatment.brand_sentiment),
        recall_strength=_state_delta(control.recall_strength, treatment.recall_strength),
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


def _campaign_ids(metrics: SpatialResponseMetricsV2) -> tuple[str, ...]:
    return tuple(item.campaign_id for item in metrics.rule_baseline.campaigns)


def _validate_matched_provenance(
    control: SpatialResponseMetricsV2,
    treatment: SpatialResponseMetricsV2,
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
    control: SpatialResponseMetricsV2,
    treatment: SpatialResponseMetricsV2,
) -> SpatialOpportunityComparisonClassification:
    if control.opportunity_structure_sha256 == treatment.opportunity_structure_sha256:
        return "matched-opportunity-structure"
    return "opportunity-confounded"


def _assumption_classification(
    control: SpatialResponseMetricsV2,
    treatment: SpatialResponseMetricsV2,
) -> SpatialResponseAssumptionComparisonClassification:
    if (
        control.response_assumption_structure_sha256
        == treatment.response_assumption_structure_sha256
    ):
        return "matched-response-assumptions"
    return "response-assumption-confounded"


def _cognition_provenance_classification(
    control: SpatialProviderDependentCognitionMetricsV2,
    treatment: SpatialProviderDependentCognitionMetricsV2,
) -> CognitionProvenanceClassification:
    if all(getattr(control, name) == getattr(treatment, name) for name in _PROVENANCE_FIELDS):
        return "matched-provider-dependent-exploratory"
    return "incompatible-provider-provenance"


def _baseline_comparison(
    control: SpatialRuleBaselineMetricsV2,
    treatment: SpatialRuleBaselineMetricsV2,
) -> SpatialRuleBaselineMetricsComparisonV2:
    if tuple(item.channel for item in control.channels) != tuple(
        item.channel for item in treatment.channels
    ):
        raise SpatialResponseComparisonError("response channel order differs between runs")
    channels = tuple(
        _channel_delta(control_series, treatment_series)
        for control_series, treatment_series in zip(
            control.channels,
            treatment.channels,
            strict=True,
        )
    )
    campaigns = tuple(
        _campaign_delta(control_series, treatment_series)
        for control_series, treatment_series in zip(
            control.campaigns,
            treatment.campaigns,
            strict=True,
        )
    )
    return SpatialRuleBaselineMetricsComparisonV2(
        overall=_aggregate_delta(control, treatment),
        channels=(channels[0], channels[1]),
        campaigns=campaigns,
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


class SpatialRuleBaselineMetricsComparisonV2(DomainModel):
    """Treatment-minus-control rule baseline. Purchase stays on this family only."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    claim_scope: Literal["rule-owned-synthetic-baseline-not-observed-outcomes"] = (
        "rule-owned-synthetic-baseline-not-observed-outcomes"
    )
    purchase_intention_proxy_scope: Literal["uncalibrated-rule-owned-proxy"] = (
        "uncalibrated-rule-owned-proxy"
    )
    overall: SpatialResponseAggregateDeltaSeries
    channels: tuple[SpatialResponseChannelDeltaSeries, SpatialResponseChannelDeltaSeries]
    campaigns: tuple[SpatialResponseCampaignDeltaSeries, ...] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        return _exact_schema_version(value)


class SpatialCognitionProvenanceComparisonV2(DomainModel):
    """Label both cognition snapshots. Do not subtract them."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    claim_scope: Literal["provider-dependent-exploratory-not-campaign-effect"] = (
        "provider-dependent-exploratory-not-campaign-effect"
    )
    disclosure: Literal["synthetic-bounded-cognition-not-observed-behavior"] = (
        "synthetic-bounded-cognition-not-observed-behavior"
    )
    classification: CognitionProvenanceClassification
    control: SpatialProviderDependentCognitionMetricsV2
    treatment: SpatialProviderDependentCognitionMetricsV2

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        return _exact_schema_version(value)


class SpatialResponseMetricsComparisonV2(DomainModel):
    """Matched V10 snapshots, baseline deltas, and classified cognition provenance."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    model_id: Literal["spatial-response-metrics-comparison-v2"] = (
        "spatial-response-metrics-comparison-v2"
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
    control: SpatialResponseMetricsV2
    treatment: SpatialResponseMetricsV2
    rule_baseline: SpatialRuleBaselineMetricsComparisonV2
    cognition_provenance: SpatialCognitionProvenanceComparisonV2

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        return _exact_schema_version(value)

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
            SpatialResponseMetricsV2,
            label="control spatial response metrics",
        )
        treatment = revalidate_model(
            self.treatment,
            SpatialResponseMetricsV2,
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
            "control_opportunity_structure_sha256": control.opportunity_structure_sha256,
            "treatment_opportunity_structure_sha256": treatment.opportunity_structure_sha256,
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
            control, treatment
        ):
            raise ValueError("response comparison assumption classification is incoherent")
        expected_baseline = _baseline_comparison(control.rule_baseline, treatment.rule_baseline)
        if self.rule_baseline != expected_baseline:
            raise ValueError("response comparison baseline deltas do not match source snapshots")
        expected_classification = _cognition_provenance_classification(
            control.provider_dependent_cognition,
            treatment.provider_dependent_cognition,
        )
        provenance = self.cognition_provenance
        if (
            provenance.classification != expected_classification
            or provenance.control != control.provider_dependent_cognition
            or provenance.treatment != treatment.provider_dependent_cognition
        ):
            raise ValueError("cognition provenance does not match the source snapshots")
        return self


def compare_spatial_response_metrics_v2(
    control: SpatialResponseMetricsV2,
    treatment: SpatialResponseMetricsV2,
    *,
    control_run_id: str,
    treatment_run_id: str,
) -> SpatialResponseMetricsComparisonV2:
    """Compare matched V10 metrics without turning cognition into an effect."""
    validated_control = revalidate_model(
        control,
        SpatialResponseMetricsV2,
        label="control spatial response metrics",
    )
    validated_treatment = revalidate_model(
        treatment,
        SpatialResponseMetricsV2,
        label="treatment spatial response metrics",
    )
    _validate_matched_provenance(validated_control, validated_treatment)
    control_cognition = validated_control.provider_dependent_cognition
    treatment_cognition = validated_treatment.provider_dependent_cognition
    return SpatialResponseMetricsComparisonV2(
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
        control_opportunity_structure_sha256=validated_control.opportunity_structure_sha256,
        treatment_opportunity_structure_sha256=validated_treatment.opportunity_structure_sha256,
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
        rule_baseline=_baseline_comparison(
            validated_control.rule_baseline,
            validated_treatment.rule_baseline,
        ),
        cognition_provenance=SpatialCognitionProvenanceComparisonV2(
            classification=_cognition_provenance_classification(
                control_cognition,
                treatment_cognition,
            ),
            control=control_cognition,
            treatment=treatment_cognition,
        ),
    )


__all__ = [
    "CognitionProvenanceClassification",
    "SpatialCognitionProvenanceComparisonV2",
    "SpatialResponseMetricsComparisonV2",
    "SpatialRuleBaselineMetricsComparisonV2",
    "compare_spatial_response_metrics_v2",
]
