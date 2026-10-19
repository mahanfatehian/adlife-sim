"""Auditable V10 metrics with separate rule-baseline and bounded-cognition authority.

The rule baseline is re-derived only from the deterministic ``spatial.response`` evidence
embedded in every applied V10 response and from the committed rule states, so it is
numerically identical to the V1 rule model for the same scientific inputs. Values that
a provider signal can influence are reported only as explicitly exploratory synthetic
cognition measurements: they never enter the uncalibrated purchase-intention proxy, a
rule metric, or any campaign-effect estimate.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from hashlib import sha256
from typing import Literal, Self, TypeAlias, get_args

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.city_cognition import Hash, _safe_text
from adlife.core.domain.city_cognition_v2 import (
    _CITY_COGNITION_TERMINAL_POLICY_HASHES_V2,
    CityCognitionAffectV2,
    CityCognitionCoverageCountsV2,
    CityCognitionCredibilityV2,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
    CityCognitionModeV2,
    CityCognitionNeutralizationCountsV2,
    CityCognitionOutcomeCountsV2,
    CityCognitionReasonCountsV2,
    CityCognitionRelevanceV2,
    CityCognitionSalienceV2,
    CityCognitionTerminalRecordV2,
)
from adlife.core.domain.city_run import build_city_cognition_manifest_receipt_v2
from adlife.core.domain.person import DomainModel, contains_secret_or_email_text
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.domain.spatial_response import (
    SpatialResponseInitialState,
    SpatialResponseInput,
    validate_spatial_response_input,
)
from adlife.core.experiments.spatial_metrics import SpatialMetrics, derive_spatial_metrics
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseCampaignSeries,
    SpatialResponseChannelSeries,
    SpatialResponseMetricReceipt,
    SpatialResponseStateReceipt,
    _aggregate_series,
    _event_series,
    _matches_partitioned_fsum,
    _validated_population,
    spatial_response_assumption_structure_sha256,
)
from adlife.core.simulation._validation import revalidate_model
from adlife.core.simulation.spatial_attention import SpatialAttentionEvaluation
from adlife.core.simulation.spatial_opportunity import SpatialOpportunityEvaluation
from adlife.core.simulation.spatial_response import (
    MAX_SPATIAL_RESPONSES,
    SpatialResponseState,
    SpatialRuleResponse,
    evaluate_spatial_responses,
)
from adlife.core.simulation.spatial_response_v2 import (
    SpatialAppliedResponseV2,
    SpatialCognitionStateV2,
    SpatialResponseEvaluationV2,
    apply_spatial_cognition_v2,
    summarize_spatial_response_artifact_v2,
)

_MAX_CITY_AGENTS = 30
_MAX_CAMPAIGNS = 20
_MAX_STATE_ROWS = _MAX_CITY_AGENTS * _MAX_CAMPAIGNS
_MAX_SELECTED_REQUESTS = 60
_MAX_SELECTED_REQUESTS_PER_AGENT = 4
_FUNDED_RESPONSE_LIMITS = frozenset({10, 20, 40, 60})
_OFFLINE_MODES = frozenset({"rules", "mock"})
# The manifest-V10 receipt binds offline runs to this exact null-settings digest.
_NULL_EFFECTIVE_SETTINGS_SHA256 = sha256(
    canonical_json({"effective_settings": None}).encode("utf-8")
).hexdigest()

SpatialCognitionMetricNameV2: TypeAlias = Literal["mean_applied_sentiment", "mean_applied_recall"]
SpatialCognitionStateMetricNameV2: TypeAlias = Literal["synthetic_sentiment", "synthetic_recall"]
SpatialCognitionMetricEventType: TypeAlias = Literal["spatial.applied-response"]
SpatialCognitionMetricArtifact: TypeAlias = Literal["outputs/spatial-responses.jsonl"]
SpatialCognitionCoverageArtifact: TypeAlias = Literal["outputs/cognition-summary.json"]
SpatialCognitionStateArtifact: TypeAlias = Literal[
    "inputs/spatial-response.json", "outputs/response-state.json"
]

_APPLIED_EVENT_TYPES: tuple[SpatialCognitionMetricEventType, ...] = ("spatial.applied-response",)
_APPLIED_ARTIFACTS: tuple[SpatialCognitionMetricArtifact, ...] = (
    "outputs/spatial-responses.jsonl",
)
_COVERAGE_ARTIFACTS: tuple[SpatialCognitionCoverageArtifact, ...] = (
    "outputs/cognition-summary.json",
)
_STATE_ARTIFACTS: tuple[SpatialCognitionStateArtifact, SpatialCognitionStateArtifact] = (
    "inputs/spatial-response.json",
    "outputs/response-state.json",
)
_RULE_EVENT_FIELDS = (
    "response_count",
    "response_reach",
    "response_frequency",
    "mean_rule_sentiment_delta",
    "mean_rule_recall_delta",
)
_RULE_STATE_FIELDS = ("brand_sentiment", "recall_strength", "purchase_intention_proxy")
_RULE_MEAN_FIELDS = ("mean_rule_sentiment_delta", "mean_rule_recall_delta")
_CHANNEL_SPECS: tuple[
    tuple[Literal["roadside"], Literal["roadside-billboard"]],
    tuple[Literal["mobile"], Literal["mobile-feed"]],
] = (
    ("roadside", "roadside-billboard"),
    ("mobile", "mobile-feed"),
)


def _positive_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _negative_zero(value: float) -> bool:
    return value == 0.0 and math.copysign(1.0, value) < 0


def _exact_float(value: object) -> object:
    # Strict pydantic still converts integers to floats. Persisted metric scalars are
    # floats by contract, so an integer token is a noncanonical document.
    if type(value) is not float:
        raise ValueError("cognition metric scalars must be exact floats")
    return value


def _lf_sha256(value: DomainModel) -> str:
    return sha256((canonical_json(value) + "\n").encode("utf-8")).hexdigest()


class _SpatialResponseMetricsModelV2(DomainModel):
    # Nested V2 metric instances are re-proven whenever a parent document is built.
    model_config = ConfigDict(hide_input_in_errors=True, revalidate_instances="always")


class SpatialCognitionMetricReceiptV2(_SpatialResponseMetricsModelV2):
    """One mean applied cognition step over every eligible (applied) response."""

    schema_version: Literal[2] = 2
    name: SpatialCognitionMetricNameV2
    numerator: float = Field(ge=-MAX_SPATIAL_RESPONSES, le=MAX_SPATIAL_RESPONSES)
    denominator: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    value: float = Field(ge=-1, le=1)
    source_event_types: tuple[SpatialCognitionMetricEventType, ...] = _APPLIED_EVENT_TYPES
    source_artifacts: tuple[SpatialCognitionMetricArtifact, ...] = _APPLIED_ARTIFACTS

    @field_validator("numerator", "value", mode="before")
    @classmethod
    def exact_float_scalars(cls, value: object) -> object:
        return _exact_float(value)

    @model_validator(mode="after")
    def coherent_receipt(self) -> Self:
        if self.denominator == 0 and self.numerator != 0.0:
            raise ValueError("an empty applied-response set must have a zero numerator")
        expected = _positive_zero(self.numerator / self.denominator) if self.denominator else 0.0
        if self.value != expected or _negative_zero(self.value) or _negative_zero(self.numerator):
            raise ValueError("cognition metric value must equal its positive quotient")
        if self.name == "mean_applied_recall" and (self.numerator < 0 or self.value < 0):
            raise ValueError("applied recall cannot be negative")
        if (
            self.source_event_types != _APPLIED_EVENT_TYPES
            or self.source_artifacts != _APPLIED_ARTIFACTS
        ):
            raise ValueError("cognition metric receipt sources are not canonical")
        return self


class SpatialCognitionCoverageReceiptV2(_SpatialResponseMetricsModelV2):
    """Funded cognition requests divided by every eligible baseline response."""

    schema_version: Literal[2] = 2
    name: Literal["funded_coverage"] = "funded_coverage"
    numerator: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    denominator: int = Field(ge=0, le=MAX_SPATIAL_RESPONSES)
    value: float = Field(ge=0, le=1)
    source_artifacts: tuple[SpatialCognitionCoverageArtifact, ...] = _COVERAGE_ARTIFACTS

    @field_validator("value", mode="before")
    @classmethod
    def exact_float_value(cls, value: object) -> object:
        return _exact_float(value)

    @model_validator(mode="after")
    def coherent_receipt(self) -> Self:
        expected = _positive_zero(self.numerator / self.denominator) if self.denominator else 0.0
        if (
            self.numerator > self.denominator
            or self.value != expected
            or _negative_zero(self.value)
        ):
            raise ValueError("funded coverage must equal funded requests over eligible responses")
        if self.source_artifacts != _COVERAGE_ARTIFACTS:
            raise ValueError("funded coverage receipt sources are not canonical")
        return self


class SpatialCognitionStateReceiptV2(_SpatialResponseMetricsModelV2):
    """One complete-grid initial/final synthetic cognition-state calculation."""

    schema_version: Literal[2] = 2
    name: SpatialCognitionStateMetricNameV2
    initial_total: float = Field(ge=-_MAX_STATE_ROWS, le=_MAX_STATE_ROWS)
    final_total: float = Field(ge=-_MAX_STATE_ROWS, le=_MAX_STATE_ROWS)
    change_total: float = Field(ge=-(_MAX_STATE_ROWS * 2), le=_MAX_STATE_ROWS * 2)
    denominator: int = Field(ge=1, le=_MAX_STATE_ROWS)
    initial_mean: float = Field(ge=-1, le=1)
    final_mean: float = Field(ge=-1, le=1)
    mean_change: float = Field(ge=-2, le=2)
    source_artifacts: tuple[SpatialCognitionStateArtifact, SpatialCognitionStateArtifact] = (
        _STATE_ARTIFACTS
    )

    @field_validator(
        "initial_total",
        "final_total",
        "change_total",
        "initial_mean",
        "final_mean",
        "mean_change",
        mode="before",
    )
    @classmethod
    def exact_float_totals(cls, value: object) -> object:
        return _exact_float(value)

    @model_validator(mode="after")
    def coherent_receipt(self) -> Self:
        if (
            self.change_total != _positive_zero(self.final_total - self.initial_total)
            or self.initial_mean != self.initial_total / self.denominator
            or self.final_mean != self.final_total / self.denominator
            or self.mean_change != _positive_zero(self.change_total / self.denominator)
        ):
            raise ValueError("cognition state receipt does not match its complete-grid totals")
        if any(
            _negative_zero(value)
            for value in (
                self.initial_total,
                self.final_total,
                self.change_total,
                self.initial_mean,
                self.final_mean,
                self.mean_change,
            )
        ):
            raise ValueError("cognition state receipt zeros must be positive")
        if self.name == "synthetic_recall" and (self.initial_total < 0 or self.final_total < 0):
            raise ValueError("synthetic recall totals cannot be negative")
        if self.source_artifacts != _STATE_ARTIFACTS:
            raise ValueError("cognition state receipt sources are not canonical")
        return self


class SpatialCognitionAffectCountsV2(_SpatialResponseMetricsModelV2):
    """Selected terminal results by affect, in exact result-contract order."""

    annoyed: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    skeptical: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    neutral: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    curious: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    positive: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    uncertain: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)

    @property
    def total(self) -> int:
        return (
            self.annoyed
            + self.skeptical
            + self.neutral
            + self.curious
            + self.positive
            + self.uncertain
        )


class _SpatialCognitionScaleCountsV2(_SpatialResponseMetricsModelV2):
    none: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    low: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    medium: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    high: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    uncertain: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)

    @property
    def total(self) -> int:
        return self.none + self.low + self.medium + self.high + self.uncertain


class SpatialCognitionRelevanceCountsV2(_SpatialCognitionScaleCountsV2):
    """Selected terminal results by relevance, in exact result-contract order."""


class SpatialCognitionCredibilityCountsV2(_SpatialResponseMetricsModelV2):
    """Selected terminal results by credibility, in exact result-contract order."""

    low: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    medium: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    high: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)
    uncertain: int = Field(ge=0, le=_MAX_SELECTED_REQUESTS)

    @property
    def total(self) -> int:
        return self.low + self.medium + self.high + self.uncertain


class SpatialCognitionSalienceCountsV2(_SpatialCognitionScaleCountsV2):
    """Selected terminal results by salience, in exact result-contract order."""


class SpatialCognitionCategoryCountsV2(_SpatialResponseMetricsModelV2):
    """Zero-inclusive categories of every selected terminal result.

    The counts come from ``outputs/cognition.jsonl`` and include the exact neutral
    vectors of rules, fallback, and budget-skipped terminals; the source counts say
    which terminals a provider or cache actually supplied.
    """

    schema_version: Literal[2] = 2
    affect: SpatialCognitionAffectCountsV2
    relevance: SpatialCognitionRelevanceCountsV2
    credibility: SpatialCognitionCredibilityCountsV2
    salience: SpatialCognitionSalienceCountsV2

    @property
    def total(self) -> int:
        return self.affect.total

    @model_validator(mode="after")
    def one_terminal_population(self) -> Self:
        if len({self.affect.total, self.relevance.total, self.credibility.total}) != 1 or (
            self.salience.total != self.affect.total
        ):
            raise ValueError("every cognition category family must count the same terminals")
        return self


# The count families are the result contract itself, never a parallel vocabulary.
for _family, _categories in (
    (SpatialCognitionAffectCountsV2, CityCognitionAffectV2),
    (SpatialCognitionRelevanceCountsV2, CityCognitionRelevanceV2),
    (SpatialCognitionCredibilityCountsV2, CityCognitionCredibilityV2),
    (SpatialCognitionSalienceCountsV2, CityCognitionSalienceV2),
):
    if tuple(_family.model_fields) != get_args(_categories):  # pragma: no cover - import guard
        raise RuntimeError("cognition category counts must follow the result contract order")


class SpatialRuleBaselineMetricsV2(_SpatialResponseMetricsModelV2):
    """Rule-owned V10 receipts, numerically identical to the V1 rule response model.

    The overall event and committed-state receipts sit directly on this document; the
    channel and campaign slices reuse the exact V1 receipt shapes because their source
    events (``spatial.response`` baselines) and state artifacts keep V1 semantics.
    """

    schema_version: Literal[2] = 2
    claim_scope: Literal["rule-owned-synthetic-baseline-not-observed-outcomes"] = (
        "rule-owned-synthetic-baseline-not-observed-outcomes"
    )
    purchase_intention_proxy_scope: Literal["uncalibrated-rule-owned-proxy"] = (
        "uncalibrated-rule-owned-proxy"
    )
    response_count: SpatialResponseMetricReceipt
    response_reach: SpatialResponseMetricReceipt
    response_frequency: SpatialResponseMetricReceipt
    mean_rule_sentiment_delta: SpatialResponseMetricReceipt
    mean_rule_recall_delta: SpatialResponseMetricReceipt
    brand_sentiment: SpatialResponseStateReceipt
    recall_strength: SpatialResponseStateReceipt
    purchase_intention_proxy: SpatialResponseStateReceipt
    channels: tuple[SpatialResponseChannelSeries, SpatialResponseChannelSeries]
    campaigns: tuple[SpatialResponseCampaignSeries, ...] = Field(
        min_length=1,
        max_length=_MAX_CAMPAIGNS,
    )

    @model_validator(mode="after")
    def coherent_rule_baseline(self) -> Self:
        # V1 receipt models accept supplied instances without revalidation. Prove every
        # embedded receipt here; a before-validator would also strip JSON-mode tuple
        # leniency from the nested strict documents.
        for name in _RULE_EVENT_FIELDS:
            revalidate_model(
                getattr(self, name), SpatialResponseMetricReceipt, label="rule baseline receipt"
            )
        for name in _RULE_STATE_FIELDS:
            revalidate_model(
                getattr(self, name), SpatialResponseStateReceipt, label="rule baseline state"
            )
        for channel in self.channels:
            revalidate_model(
                channel, SpatialResponseChannelSeries, label="rule baseline channel series"
            )
        for campaign in self.campaigns:
            revalidate_model(
                campaign, SpatialResponseCampaignSeries, label="rule baseline campaign series"
            )
        for name in (*_RULE_EVENT_FIELDS, *_RULE_STATE_FIELDS):
            if getattr(self, name).name != name:
                raise ValueError("rule baseline receipt is stored under the wrong field")
        if self.response_count.denominator != 1:
            raise ValueError("rule baseline response count must use denominator one")
        if (
            self.response_frequency.numerator != self.response_count.numerator
            or self.response_frequency.denominator != self.response_reach.numerator
        ):
            raise ValueError("rule baseline frequency must divide responses by reached agents")
        if any(
            getattr(self, name).denominator != self.response_count.numerator
            for name in _RULE_MEAN_FIELDS
        ):
            raise ValueError("rule baseline means must cover every baseline response")
        if tuple(item.channel for item in self.channels) != ("roadside", "mobile"):
            raise ValueError("rule baseline channels must be ordered roadside then mobile")
        campaign_ids = tuple(item.campaign_id for item in self.campaigns)
        if campaign_ids != tuple(sorted(set(campaign_ids))):
            raise ValueError("rule baseline campaigns must be unique and canonical")

        population = self.response_reach.denominator
        series: tuple[
            SpatialRuleBaselineMetricsV2
            | SpatialResponseChannelSeries
            | SpatialResponseCampaignSeries,
            ...,
        ] = (self, *self.channels, *self.campaigns)
        if population < 1 or any(
            item.response_reach.denominator != population
            or item.response_reach.numerator > population
            for item in series
        ):
            raise ValueError("rule baseline reach must use one complete population denominator")
        total = self.response_count.numerator
        if total != sum(item.response_count.numerator for item in self.channels) or total != sum(
            item.response_count.numerator for item in self.campaigns
        ):
            raise ValueError("rule baseline slice counts do not add up to the overall count")
        for name in _RULE_MEAN_FIELDS:
            overall = getattr(self, name)
            for slices in (self.channels, self.campaigns):
                if not _matches_partitioned_fsum(
                    float(overall.numerator),
                    tuple(float(getattr(item, name).numerator) for item in slices),
                ) or overall.denominator != sum(getattr(item, name).denominator for item in slices):
                    raise ValueError("rule baseline slice means do not add up to overall evidence")
        for name in _RULE_STATE_FIELDS:
            overall_state = getattr(self, name)
            campaign_states = tuple(getattr(item, name) for item in self.campaigns)
            if overall_state.denominator != population * len(self.campaigns) or any(
                item.denominator != population for item in campaign_states
            ):
                raise ValueError("rule baseline state must cover every agent/campaign pair")
            if not _matches_partitioned_fsum(
                overall_state.initial_total, tuple(item.initial_total for item in campaign_states)
            ) or not _matches_partitioned_fsum(
                overall_state.final_total, tuple(item.final_total for item in campaign_states)
            ):
                raise ValueError("rule baseline campaign states do not add up to overall state")
        return self


class SpatialProviderDependentCognitionMetricsV2(_SpatialResponseMetricsModelV2):
    """Exploratory synthetic cognition measurements with exact non-secret provenance.

    Nothing here is a purchase, rule, or campaign-effect quantity. Provider transport
    facts (endpoint, credential slot, display label, raw bodies, usage) are omitted.
    """

    schema_version: Literal[2] = 2
    claim_scope: Literal["provider-dependent-exploratory-not-campaign-effect"] = (
        "provider-dependent-exploratory-not-campaign-effect"
    )
    disclosure: Literal["synthetic-bounded-cognition-not-observed-behavior"] = (
        "synthetic-bounded-cognition-not-observed-behavior"
    )
    mode: CityCognitionModeV2
    cognition_input_sha256: Hash
    cognition_terminal_stream_sha256: Hash
    cognition_summary_sha256: Hash
    causal_contract_sha256: Hash
    prompt_contract_sha256: Hash
    result_schema_contract_sha256: Hash
    selection_policy_sha256: Hash
    fallback_policy_sha256: Hash
    mock_policy_sha256: Hash
    influence_policy_id: Literal["city-cognition-influence-v1"] = "city-cognition-influence-v1"
    influence_policy_sha256: Hash
    provider_profile_id: str | None = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    provider_model_id: str | None = Field(min_length=1, max_length=120)
    provider_revision_sha256: Hash | None
    provider_profile_snapshot_sha256: Hash | None
    effective_settings_sha256: Hash
    funded_response_limit: Literal[10, 20, 40, 60]
    coverage: CityCognitionCoverageCountsV2
    source_counts: CityCognitionOutcomeCountsV2
    fallback_counts: CityCognitionReasonCountsV2
    neutralization_counts: CityCognitionNeutralizationCountsV2
    category_counts: SpatialCognitionCategoryCountsV2 = Field(
        description=(
            "Categories of every selected terminal result, including the fixed neutral "
            "vectors of rules, fallback, and budget-skipped terminals; source_counts tallies "
            "the same terminals by source, so only provider, cache, and mock terminals can "
            "carry a non-neutral category."
        )
    )
    funded_coverage: SpatialCognitionCoverageReceiptV2
    mean_applied_sentiment: SpatialCognitionMetricReceiptV2
    mean_applied_recall: SpatialCognitionMetricReceiptV2
    synthetic_sentiment: SpatialCognitionStateReceiptV2
    synthetic_recall: SpatialCognitionStateReceiptV2

    @field_validator("funded_response_limit", mode="before")
    @classmethod
    def exact_funded_limit(cls, value: object) -> object:
        if type(value) is not int or value not in _FUNDED_RESPONSE_LIMITS:
            raise ValueError("funded response limit must be integer 10, 20, 40 or 60")
        return value

    @field_validator("provider_profile_id")
    @classmethod
    def safe_provider_profile(cls, value: str | None) -> str | None:
        if value is not None and contains_secret_or_email_text(value):
            raise ValueError("provider profile identifier contains credential-shaped text")
        return value

    @field_validator("provider_model_id")
    @classmethod
    def safe_provider_model(cls, value: str | None) -> str | None:
        if value is not None:
            _safe_text(value)
            if contains_secret_or_email_text(value):
                raise ValueError("provider model identifier contains credential-shaped text")
        return value

    @model_validator(mode="after")
    def coherent_cognition_family(self) -> Self:
        if any(
            getattr(self, field) != expected
            for field, expected in _CITY_COGNITION_TERMINAL_POLICY_HASHES_V2
        ):
            raise ValueError("cognition metrics do not bind the frozen V2 causal contract")
        coverage, sources, reasons = self.coverage, self.source_counts, self.fallback_counts
        selected = coverage.selected_request_count
        if (
            sources.total != selected
            or sources.budget_skipped != coverage.budget_skipped_count
            or reasons.budget_skipped != sources.budget_skipped
            or reasons.total - reasons.budget_skipped != sources.fallback
        ):
            raise ValueError("cognition source, fallback, and coverage counts do not match")
        if coverage.funded_request_count != min(selected, self.funded_response_limit):
            raise ValueError("cognition funding does not match the canonical funded prefix")

        provider_fields = (
            self.provider_profile_id,
            self.provider_model_id,
            self.provider_revision_sha256,
            self.provider_profile_snapshot_sha256,
        )
        if self.mode in _OFFLINE_MODES:
            if (
                any(value is not None for value in provider_fields)
                or self.effective_settings_sha256 != _NULL_EFFECTIVE_SETTINGS_SHA256
                or self.funded_response_limit != _MAX_SELECTED_REQUESTS
                or getattr(sources, self.mode) != selected
            ):
                raise ValueError(
                    "offline cognition metrics must resolve locally without a provider"
                )
        elif any(value is None for value in provider_fields) or sources.rules or sources.mock:
            raise ValueError("provider cognition metrics require complete non-secret provenance")

        # Rules, fallback, and budget-skipped terminals carry the exact neutral vector;
        # mock vectors are never uncertain. Only provider/cache results may be uncertain.
        categories = self.category_counts
        identity_terminals = sources.rules + sources.fallback + sources.budget_skipped
        signal_capacity = sources.provider + sources.cache
        family_uncertain = (
            categories.affect.uncertain,
            categories.relevance.uncertain,
            categories.credibility.uncertain,
            categories.salience.uncertain,
        )
        neutralization = self.neutralization_counts
        uncertain = neutralization.uncertain_result_count
        safety = neutralization.safety_neutralized_result_count
        if categories.total != selected:
            raise ValueError("each cognition category family must count every selected request")
        if (
            categories.affect.neutral < identity_terminals
            or categories.relevance.medium < identity_terminals
            or categories.credibility.medium < identity_terminals
            or categories.salience.medium < identity_terminals
            or not max(family_uncertain) <= uncertain <= min(sum(family_uncertain), signal_capacity)
        ):
            raise ValueError("cognition categories do not match their terminal sources")
        always_neutral = coverage.unselected_response_count + identity_terminals
        if safety > signal_capacity or not always_neutral + max(
            uncertain, safety
        ) <= neutralization.neutralized_response_count <= always_neutral + min(
            uncertain + safety, signal_capacity
        ):
            raise ValueError("cognition neutralization counts cannot describe the terminals")

        eligible = coverage.eligible_response_count
        if (
            self.funded_coverage.numerator != coverage.funded_request_count
            or self.funded_coverage.denominator != eligible
        ):
            raise ValueError("funded coverage receipt does not match cognition coverage")
        for name in ("mean_applied_sentiment", "mean_applied_recall"):
            receipt = getattr(self, name)
            if receipt.name != name or receipt.denominator != eligible:
                raise ValueError("applied cognition means must cover every eligible response")
        for name in ("synthetic_sentiment", "synthetic_recall"):
            if getattr(self, name).name != name:
                raise ValueError("cognition state receipt is stored under the wrong field")
        if self.synthetic_sentiment.denominator != self.synthetic_recall.denominator:
            raise ValueError("final cognition states must use one complete-grid denominator")
        return self


class SpatialResponseMetricsV2(_SpatialResponseMetricsModelV2):
    """Complete V10 receipt binding the rule baseline and exploratory cognition families."""

    schema_version: Literal[2] = 2
    model_id: Literal["spatial-response-metrics-v2"] = "spatial-response-metrics-v2"
    claim_scope: Literal["synthetic-dual-state-metrics-not-observed-outcomes"] = (
        "synthetic-dual-state-metrics-not-observed-outcomes"
    )
    source_run_schema_version: Literal[10] = 10
    opportunity_model_id: Literal["spatial-opportunity-v1"] = "spatial-opportunity-v1"
    attention_model_id: Literal["spatial-attention-v1"] = "spatial-attention-v1"
    response_model_id: Literal["spatial-response-v2"] = "spatial-response-v2"
    scenario_sha256: Hash
    city_sha256: Hash
    agents_sha256: Hash
    trace_sha256: Hash
    opportunity_structure_sha256: Hash
    response_input_sha256: Hash
    response_assumption_structure_sha256: Hash
    response_stream_sha256: Hash
    response_state_sha256: Hash
    response_summary_sha256: Hash
    rule_state_document_sha256: Hash
    cognition_state_document_sha256: Hash
    cognition_input_sha256: Hash
    cognition_terminal_stream_sha256: Hash
    cognition_summary_sha256: Hash
    influence_policy_sha256: Hash
    seed: int = Field(ge=0, le=2**63 - 1)
    population_size: int = Field(ge=1, le=_MAX_CITY_AGENTS)
    days: int = Field(ge=1, le=7)
    campaign_count: int = Field(ge=1, le=_MAX_CAMPAIGNS)
    rule_baseline: SpatialRuleBaselineMetricsV2
    provider_dependent_cognition: SpatialProviderDependentCognitionMetricsV2

    @field_validator("source_run_schema_version", mode="before")
    @classmethod
    def exact_source_schema(cls, value: object) -> object:
        if type(value) is not int or value != 10:
            raise ValueError("source run schema version must be integer 10")
        return value

    @model_validator(mode="after")
    def coherent_document(self) -> Self:
        baseline = self.rule_baseline
        cognition = self.provider_dependent_cognition
        grid = self.population_size * self.campaign_count
        if (
            self.campaign_count != len(baseline.campaigns)
            or baseline.response_reach.denominator != self.population_size
            or cognition.synthetic_sentiment.denominator != grid
        ):
            raise ValueError("V10 metric families do not cover the population and campaigns")
        if (
            self.cognition_input_sha256 != cognition.cognition_input_sha256
            or self.cognition_terminal_stream_sha256 != cognition.cognition_terminal_stream_sha256
            or self.cognition_summary_sha256 != cognition.cognition_summary_sha256
            or self.influence_policy_sha256 != cognition.influence_policy_sha256
        ):
            raise ValueError("V10 outer provenance does not match the cognition metrics")
        coverage = cognition.coverage
        if (
            coverage.eligible_response_count != baseline.response_count.numerator
            or coverage.selected_request_count
            > _MAX_SELECTED_REQUESTS_PER_AGENT * self.population_size
        ):
            raise ValueError("cognition coverage does not match the rule baseline responses")
        # Both tracks start from the same declared state.
        if (
            cognition.synthetic_sentiment.initial_total != baseline.brand_sentiment.initial_total
            or cognition.synthetic_recall.initial_total != baseline.recall_strength.initial_total
        ):
            raise ValueError("rule and cognition tracks must share one initial state")
        # Every neutralized response preserves its exact baseline step, so a fully
        # neutralized run has a cognition track identical to its rule track.
        if cognition.neutralization_counts.neutralized_response_count == (
            coverage.eligible_response_count
        ) and (
            cognition.mean_applied_sentiment.numerator
            != baseline.mean_rule_sentiment_delta.numerator
            or cognition.mean_applied_recall.numerator != baseline.mean_rule_recall_delta.numerator
            or cognition.synthetic_sentiment.final_total != baseline.brand_sentiment.final_total
            or cognition.synthetic_recall.final_total != baseline.recall_strength.final_total
        ):
            raise ValueError("neutral cognition must be an exact identity over the rule baseline")
        return self


def _rule_baseline_metrics(
    responses: Sequence[SpatialRuleResponse],
    *,
    initial_states: Sequence[SpatialResponseInitialState],
    final_states: Sequence[SpatialResponseState],
    campaign_ids: Sequence[str],
    population_size: int,
) -> SpatialRuleBaselineMetricsV2:
    overall = _aggregate_series(
        responses,
        initial_states=initial_states,
        final_states=final_states,
        population_size=population_size,
    )
    roadside, mobile = (
        SpatialResponseChannelSeries(
            channel=label,
            **_event_series(
                tuple(item for item in responses if item.channel == source_channel),
                population_size=population_size,
            ),
        )
        for label, source_channel in _CHANNEL_SPECS
    )
    campaigns = tuple(
        SpatialResponseCampaignSeries(
            campaign_id=campaign_id,
            **_aggregate_series(
                tuple(item for item in responses if item.campaign_id == campaign_id),
                initial_states=tuple(
                    item for item in initial_states if item.campaign_id == campaign_id
                ),
                final_states=tuple(
                    item for item in final_states if item.campaign_id == campaign_id
                ),
                population_size=population_size,
            ).model_dump(mode="python"),
        )
        for campaign_id in campaign_ids
    )
    return SpatialRuleBaselineMetricsV2(
        response_count=overall.response_count,
        response_reach=overall.response_reach,
        response_frequency=overall.response_frequency,
        mean_rule_sentiment_delta=overall.mean_rule_sentiment_delta,
        mean_rule_recall_delta=overall.mean_rule_recall_delta,
        brand_sentiment=overall.brand_sentiment,
        recall_strength=overall.recall_strength,
        purchase_intention_proxy=overall.purchase_intention_proxy,
        channels=(roadside, mobile),
        campaigns=campaigns,
    )


def _applied_mean(
    name: SpatialCognitionMetricNameV2,
    values: Sequence[float],
) -> SpatialCognitionMetricReceiptV2:
    numerator = _positive_zero(math.fsum(values))
    denominator = len(values)
    return SpatialCognitionMetricReceiptV2(
        name=name,
        numerator=numerator,
        denominator=denominator,
        value=_positive_zero(numerator / denominator) if denominator else 0.0,
    )


def _funded_coverage(coverage: CityCognitionCoverageCountsV2) -> SpatialCognitionCoverageReceiptV2:
    funded = coverage.funded_request_count
    eligible = coverage.eligible_response_count
    return SpatialCognitionCoverageReceiptV2(
        numerator=funded,
        denominator=eligible,
        value=_positive_zero(funded / eligible) if eligible else 0.0,
    )


def _cognition_state_receipt(
    name: SpatialCognitionStateMetricNameV2,
    initial_states: Sequence[SpatialResponseInitialState],
    final_states: Sequence[SpatialCognitionStateV2],
) -> SpatialCognitionStateReceiptV2:
    if not initial_states or tuple(
        (item.agent_id, item.campaign_id) for item in initial_states
    ) != tuple((item.agent_id, item.campaign_id) for item in final_states):
        raise ValueError("cognition metrics require matching complete state grids")
    if name == "synthetic_sentiment":
        initial = tuple(item.brand_sentiment for item in initial_states)
        final = tuple(item.synthetic_sentiment for item in final_states)
    else:
        initial = tuple(item.recall_strength for item in initial_states)
        final = tuple(item.synthetic_recall for item in final_states)
    initial_total = _positive_zero(math.fsum(initial))
    final_total = _positive_zero(math.fsum(final))
    change_total = _positive_zero(final_total - initial_total)
    denominator = len(initial_states)
    return SpatialCognitionStateReceiptV2(
        name=name,
        initial_total=initial_total,
        final_total=final_total,
        change_total=change_total,
        denominator=denominator,
        initial_mean=initial_total / denominator,
        final_mean=final_total / denominator,
        mean_change=_positive_zero(change_total / denominator),
    )


def _canonical_final_states(evaluation: SpatialResponseEvaluationV2) -> tuple[str, ...]:
    return tuple(
        canonical_json(state)
        for state in (*evaluation.final_rule_states, *evaluation.final_cognition_states)
    )


def _category_counts(
    records: Sequence[CityCognitionTerminalRecordV2],
) -> SpatialCognitionCategoryCountsV2:
    def family(categories: tuple[str, ...], observed: Sequence[str]) -> Mapping[str, int]:
        counts = Counter(observed)
        return {category: counts[category] for category in categories}

    return SpatialCognitionCategoryCountsV2.model_validate(
        {
            "affect": family(
                get_args(CityCognitionAffectV2), tuple(item.result.affect for item in records)
            ),
            "relevance": family(
                get_args(CityCognitionRelevanceV2),
                tuple(item.result.relevance for item in records),
            ),
            "credibility": family(
                get_args(CityCognitionCredibilityV2),
                tuple(item.result.credibility for item in records),
            ),
            "salience": family(
                get_args(CityCognitionSalienceV2), tuple(item.result.salience for item in records)
            ),
        }
    )


def derive_spatial_response_metrics_v2(
    response_input: SpatialResponseInput,
    response: SpatialResponseEvaluationV2,
    *,
    scenario: SpatialCampaignScenario,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    agent_ids: Collection[str],
    attention_metrics: SpatialMetrics,
    cognition_input: CityCognitionInputV2,
    cognition: CityCognitionEvaluationV2,
) -> SpatialResponseMetricsV2:
    """Re-derive one complete V10 run and project two noninterfering metric families.

    Every input is revalidated and the dual-state stream is regenerated from the
    deterministic baseline plus the frozen terminal signals before any metric is read.
    """
    validated_input = revalidate_model(
        response_input, SpatialResponseInput, label="spatial response input"
    )
    validated_scenario = revalidate_model(
        scenario, SpatialCampaignScenario, label="spatial response scenario"
    )
    validated_opportunities = revalidate_model(
        opportunities, SpatialOpportunityEvaluation, label="spatial opportunity evaluation"
    )
    validated_attention = revalidate_model(
        attention, SpatialAttentionEvaluation, label="spatial attention evaluation"
    )
    validated_response = revalidate_model(
        response, SpatialResponseEvaluationV2, label="V2 spatial response evaluation"
    )
    validated_attention_metrics = revalidate_model(
        attention_metrics, SpatialMetrics, label="spatial attention metrics"
    )
    validated_cognition_input = revalidate_model(
        cognition_input, CityCognitionInputV2, label="V2 city cognition input"
    )
    validated_cognition = revalidate_model(
        cognition, CityCognitionEvaluationV2, label="V2 city cognition evaluation"
    )
    if validated_attention_metrics.source_run_schema_version != 10:
        raise ValueError("V2 spatial response metrics require source run schema 10")
    population = _validated_population(agent_ids)
    assumption_structure_sha256 = spatial_response_assumption_structure_sha256(
        validated_input, validated_scenario
    )
    validate_spatial_response_input(validated_input, validated_scenario, agent_ids=population)

    expected_attention_metrics = derive_spatial_metrics(
        validated_opportunities,
        validated_attention,
        agent_ids=population,
        agents_sha256=validated_attention_metrics.agents_sha256,
        trace_sha256=validated_attention_metrics.trace_sha256,
        days=validated_attention_metrics.days,
        source_run_schema_version=10,
    )
    if expected_attention_metrics != validated_attention_metrics:
        raise ValueError("spatial attention metrics do not match their V10 source evidence")
    # The receipt builder proves the terminal evidence belongs to this exact frozen
    # input: reservations, funding prefix, attempts, and provider/settings bindings.
    receipt = build_city_cognition_manifest_receipt_v2(
        validated_cognition_input, validated_cognition
    )
    if (
        validated_response.attention_seed != validated_attention.seed
        or validated_response.response_input_sha256 != validated_input.fingerprint
        or validated_response.cognition_input_sha256 != receipt.cognition_input_sha256
    ):
        raise ValueError("V2 response evidence is bound to different science or cognition")

    baseline = evaluate_spatial_responses(
        validated_input,
        validated_scenario,
        validated_opportunities,
        validated_attention,
        agent_ids=population,
    )
    expected_response = apply_spatial_cognition_v2(
        baseline, validated_cognition_input, validated_cognition
    )
    # Records are content addressed; final states are compared canonically because
    # model equality cannot distinguish a signed zero that changes the state hash.
    if expected_response != validated_response or _canonical_final_states(
        expected_response
    ) != _canonical_final_states(validated_response):
        raise ValueError("V2 response evidence does not match its baseline and frozen signals")
    summary = summarize_spatial_response_artifact_v2(validated_response)

    applied = tuple(
        record
        for record in validated_response.records
        if isinstance(record, SpatialAppliedResponseV2)
    )
    rule_baseline = _rule_baseline_metrics(
        tuple(record.baseline for record in applied),
        initial_states=validated_input.initial_states,
        final_states=validated_response.final_rule_states,
        campaign_ids=tuple(item.campaign_id for item in validated_input.campaigns),
        population_size=len(population),
    )
    profile = validated_cognition_input.provider_profile
    cognition_metrics = SpatialProviderDependentCognitionMetricsV2(
        mode=receipt.mode,
        cognition_input_sha256=receipt.cognition_input_sha256,
        cognition_terminal_stream_sha256=receipt.terminal_stream_sha256,
        cognition_summary_sha256=receipt.summary_sha256,
        causal_contract_sha256=receipt.causal_contract_sha256,
        prompt_contract_sha256=receipt.prompt_contract_sha256,
        result_schema_contract_sha256=receipt.result_schema_contract_sha256,
        selection_policy_sha256=receipt.selection_policy_sha256,
        fallback_policy_sha256=receipt.fallback_policy_sha256,
        mock_policy_sha256=receipt.mock_policy_sha256,
        influence_policy_id=validated_cognition_input.causal_contract.influence_policy.policy_id,
        influence_policy_sha256=receipt.influence_policy_sha256,
        provider_profile_id=None if profile is None else profile.profile_id,
        provider_model_id=None if profile is None else profile.model_id,
        provider_revision_sha256=receipt.provider_revision_sha256,
        provider_profile_snapshot_sha256=receipt.provider_profile_snapshot_sha256,
        effective_settings_sha256=receipt.effective_settings_sha256,
        funded_response_limit=receipt.funded_response_limit,
        coverage=receipt.coverage,
        source_counts=receipt.outcomes,
        fallback_counts=receipt.reasons,
        neutralization_counts=receipt.neutralization,
        category_counts=_category_counts(validated_cognition.records),
        funded_coverage=_funded_coverage(receipt.coverage),
        mean_applied_sentiment=_applied_mean(
            "mean_applied_sentiment", tuple(record.applied_sentiment_delta for record in applied)
        ),
        mean_applied_recall=_applied_mean(
            "mean_applied_recall", tuple(record.applied_recall_delta for record in applied)
        ),
        synthetic_sentiment=_cognition_state_receipt(
            "synthetic_sentiment",
            validated_input.initial_states,
            validated_response.final_cognition_states,
        ),
        synthetic_recall=_cognition_state_receipt(
            "synthetic_recall",
            validated_input.initial_states,
            validated_response.final_cognition_states,
        ),
    )
    return SpatialResponseMetricsV2(
        scenario_sha256=validated_response.scenario_sha256,
        city_sha256=validated_response.city_sha256,
        agents_sha256=validated_attention_metrics.agents_sha256,
        trace_sha256=validated_attention_metrics.trace_sha256,
        opportunity_structure_sha256=validated_attention_metrics.opportunity_structure_sha256,
        response_input_sha256=validated_response.response_input_sha256,
        response_assumption_structure_sha256=assumption_structure_sha256,
        response_stream_sha256=summary.stream_sha256,
        response_state_sha256=summary.state_document_sha256,
        response_summary_sha256=_lf_sha256(summary),
        rule_state_document_sha256=summary.rule_state_document_sha256,
        cognition_state_document_sha256=summary.cognition_state_document_sha256,
        cognition_input_sha256=receipt.cognition_input_sha256,
        cognition_terminal_stream_sha256=receipt.terminal_stream_sha256,
        cognition_summary_sha256=receipt.summary_sha256,
        influence_policy_sha256=validated_response.influence_policy_sha256,
        seed=validated_response.attention_seed,
        population_size=len(population),
        days=validated_attention_metrics.days,
        campaign_count=len(validated_input.campaigns),
        rule_baseline=rule_baseline,
        provider_dependent_cognition=cognition_metrics,
    )


__all__ = [
    "SpatialCognitionAffectCountsV2",
    "SpatialCognitionCategoryCountsV2",
    "SpatialCognitionCoverageReceiptV2",
    "SpatialCognitionCredibilityCountsV2",
    "SpatialCognitionMetricReceiptV2",
    "SpatialCognitionRelevanceCountsV2",
    "SpatialCognitionSalienceCountsV2",
    "SpatialCognitionStateReceiptV2",
    "SpatialProviderDependentCognitionMetricsV2",
    "SpatialResponseMetricsV2",
    "SpatialRuleBaselineMetricsV2",
    "derive_spatial_response_metrics_v2",
]
