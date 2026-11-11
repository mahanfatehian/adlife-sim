"""Baseline-only paired studies over schema-v10 runs.

Statistics are computed from attention and rule-baseline observations. Cognition
provenance is classified and counted. It is never a statistic, a delta, or a
campaign effect.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.city_cognition import Hash
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionCoverageCountsV2,
    CityCognitionModeV2,
    CityCognitionNeutralizationCountsV2,
    CityCognitionOutcomeCountsV2,
    CityCognitionReasonCountsV2,
)
from adlife.core.domain.city_run import PackageVersion
from adlife.core.domain.person import DomainModel, contains_secret_or_email_text
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_study import SpatialStudyDefinition
from adlife.core.experiments.spatial_response_comparison import (
    SpatialOpportunityComparisonClassification,
    SpatialResponseAssumptionComparisonClassification,
)
from adlife.core.experiments.spatial_response_comparison_v2 import CognitionProvenanceClassification
from adlife.core.experiments.spatial_response_metrics_v2 import SpatialCognitionCategoryCountsV2
from adlife.core.experiments.spatial_study import (
    MAX_SPATIAL_STUDY_RESULT_BYTES,
    SpatialPairedStatistic,
    SpatialStudyCityProvenance,
    SpatialStudyScalarPair,
    spatial_paired_statistics,
)
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id

_PROVENANCE_FIELDS = (
    "causal_contract_sha256",
    "prompt_contract_sha256",
    "result_schema_contract_sha256",
    "influence_policy_sha256",
    "provider_revision_sha256",
    "effective_settings_sha256",
)


def cognition_measurement_key(key: str) -> bool:
    """True for a cognition measurement, not a campaign id that happens to say cognition."""
    parts = key.split(".")
    if not parts or parts == [""]:
        return False
    if parts[0] == "cognition":
        return True
    if len(parts) >= 4 and parts[:2] == ["response", "campaign"]:
        return "cognition" in parts[3:]
    return "cognition" in parts


def classify_cognition_provenance(
    control: CityCognitionStudyReceiptV2,
    treatment: CityCognitionStudyReceiptV2,
) -> CognitionProvenanceClassification:
    """Match the frozen contract fields. Do not subtract category counts."""
    if all(getattr(control, name) == getattr(treatment, name) for name in _PROVENANCE_FIELDS):
        return "matched-provider-dependent-exploratory"
    return "incompatible-provider-provenance"


class CityCognitionStudyReceiptV2(DomainModel):
    """Non-secret schema-v10 cognition binding for one saved run."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    mode: CityCognitionModeV2
    cognition_input_sha256: Hash
    terminal_stream_sha256: Hash
    summary_sha256: Hash
    causal_contract_sha256: Hash
    prompt_contract_sha256: Hash
    result_schema_contract_sha256: Hash
    influence_policy_sha256: Hash
    provider_revision_sha256: Hash | None
    effective_settings_sha256: Hash
    funded_response_limit: Literal[10, 20, 40, 60, 120, 250, 500, 1000]
    coverage: CityCognitionCoverageCountsV2
    outcomes: CityCognitionOutcomeCountsV2
    reasons: CityCognitionReasonCountsV2
    neutralization: CityCognitionNeutralizationCountsV2
    category_counts: SpatialCognitionCategoryCountsV2

    @field_validator("schema_version", "funded_response_limit", mode="before")
    @classmethod
    def exact_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("cognition study receipt integers must be exact integers")
        return value


class SpatialStudyResponseReceiptV2(DomainModel):
    """Schema-v10 response hashes and campaign identities. No raw response stream."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    response_model_id: Literal["spatial-response-v2"] = "spatial-response-v2"
    response_input_sha256: Hash
    response_stream_sha256: Hash
    response_state_sha256: Hash
    response_summary_sha256: Hash
    rule_state_document_sha256: Hash
    cognition_state_document_sha256: Hash
    influence_policy_sha256: Hash
    response_assumption_structure_sha256: Hash
    response_metrics_sha256: Hash
    response_count: int = Field(ge=0, le=520_800)
    response_campaign_count: int = Field(ge=1, le=20)
    campaign_ids: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("schema_version", "response_count", "response_campaign_count", mode="before")
    @classmethod
    def exact_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("response study receipt integers must be exact integers")
        return value

    @model_validator(mode="after")
    def canonical_campaigns(self) -> Self:
        if self.campaign_ids != tuple(sorted(set(self.campaign_ids))):
            raise ValueError("study response campaigns must be unique and canonical")
        if any(contains_secret_or_email_text(item) for item in self.campaign_ids):
            raise ValueError("study campaign identifier is invalid")
        if len(self.campaign_ids) != self.response_campaign_count:
            raise ValueError("study response campaign count does not match its identities")
        return self


class SpatialStudyArmReceiptV2(DomainModel):
    """One schema-v10 arm: workbench binding, response hashes, cognition receipt."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    run_id: str
    manifest_sha256: Hash
    scenario_sha256: Hash
    workbench_input_schema_version: Literal[4] = 4
    workbench_input_sha256: Hash
    opportunity_structure_sha256: Hash
    attention_metrics_sha256: Hash
    response: SpatialStudyResponseReceiptV2
    cognition: CityCognitionStudyReceiptV2

    @field_validator("schema_version", "workbench_input_schema_version", mode="before")
    @classmethod
    def exact_integers(cls, value: object) -> object:
        if type(value) is not int or value not in {2, 4}:
            raise ValueError("study arm versions must be exact integers")
        return value

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        try:
            return validate_run_id(value)
        except UnsafeRunLocation:
            raise ValueError("study arm run identifier is invalid") from None

    @model_validator(mode="after")
    def bound_workbench_and_policy(self) -> Self:
        if self.workbench_input_schema_version != 4:
            raise ValueError("schema-v10 study arms require workbench input schema 4")
        if self.response.influence_policy_sha256 != self.cognition.influence_policy_sha256:
            raise ValueError("study arm influence policy does not match its cognition receipt")
        return self


class SpatialStudyPairReceiptV2(DomainModel):
    """One seed pair. Scalars are baseline or attention observations only."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    seed: int = Field(ge=0, le=99)
    control: SpatialStudyArmReceiptV2
    treatment: SpatialStudyArmReceiptV2
    opportunity_classification: SpatialOpportunityComparisonClassification
    response_assumption_classification: SpatialResponseAssumptionComparisonClassification
    cognition_provenance_classification: CognitionProvenanceClassification
    scalars: tuple[SpatialStudyScalarPair, ...] = Field(min_length=24, max_length=328)

    @field_validator("schema_version", "seed", mode="before")
    @classmethod
    def exact_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("study pair integers must be exact integers")
        return value

    @model_validator(mode="after")
    def classified_without_cognition_scalars(self) -> Self:
        keys = tuple(item.control.key for item in self.scalars)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("study pair scalar keys must be unique and canonical")
        if any(cognition_measurement_key(key) for key in keys):
            raise ValueError("study pair scalars must not include cognition measurements")
        if self.cognition_provenance_classification != classify_cognition_provenance(
            self.control.cognition,
            self.treatment.cognition,
        ):
            raise ValueError("cognition provenance classification does not match the arm receipts")
        return self


class SpatialStudyResultV2(DomainModel):
    """Seed-paired rule-baseline statistics plus classified cognition receipts."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[2] = 2
    model_id: Literal["spatial-paired-study-v2"] = "spatial-paired-study-v2"
    claim_scope: Literal["synthetic-study-not-observed-or-causal-effect"] = (
        "synthetic-study-not-observed-or-causal-effect"
    )
    definition: SpatialStudyDefinition
    study_definition_sha256: Hash
    seeds: tuple[int, ...] = Field(min_length=2, max_length=100)
    evidence_tier: Literal["exploratory-under-50-seeds", "full-protocol-50-or-more-seeds"]
    source_run_schema_version: Literal[10] = 10
    source_run_model_id: Literal["illustrative-road-spatial-response-study-v1"] = (
        "illustrative-road-spatial-response-study-v1"
    )
    response_model_id: Literal["spatial-response-v2"] = "spatial-response-v2"
    package_version: PackageVersion
    python_version: str = Field(pattern=r"^3\.(?:11|12|13)\.[0-9]+$")
    city: SpatialStudyCityProvenance
    city_provenance_sha256: Hash
    days: int = Field(ge=1, le=7)
    population_size: int = Field(ge=1, le=30)
    control_scenario_sha256: Hash
    treatment_scenario_sha256: Hash
    campaign_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    opportunity_classification: SpatialOpportunityComparisonClassification
    response_assumption_classification: SpatialResponseAssumptionComparisonClassification
    a_a_status: Literal["rule-baseline-exact-zero-verified", "not-applicable"]
    cognition_provenance_matched_pair_count: int = Field(ge=0, le=100)
    cognition_provenance_incompatible_pair_count: int = Field(ge=0, le=100)
    pairs: tuple[SpatialStudyPairReceiptV2, ...] = Field(min_length=2, max_length=100)
    statistics: tuple[SpatialPairedStatistic, ...] = Field(min_length=24, max_length=328)

    @field_validator(
        "schema_version",
        "source_run_schema_version",
        "days",
        "population_size",
        mode="before",
    )
    @classmethod
    def exact_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("study protocol integers must be exact integers")
        return value

    @model_validator(mode="after")
    def baseline_statistics_only(self) -> Self:
        from adlife.core.experiments._spatial_study_validation import validate_study_result_v2

        validate_study_result_v2(self)
        if len(canonical_json(self).encode("utf-8")) > MAX_SPATIAL_STUDY_RESULT_BYTES:
            raise ValueError("spatial study result exceeds the canonical JSON size limit")
        return self


def statistics_for_v2_pairs(
    pairs: tuple[SpatialStudyPairReceiptV2, ...],
) -> tuple[SpatialPairedStatistic, ...]:
    """Reuse the fixed 10,000-resample bootstrap over baseline observation deltas."""
    keys = tuple(row.control.key for row in pairs[0].scalars)
    expected = set(keys)
    differences: dict[str, list[float]] = {key: [] for key in keys}
    sources = {row.control.key: row.control.source_artifacts for row in pairs[0].scalars}
    for pair in pairs:
        by_key = {row.control.key: row for row in pair.scalars}
        if set(by_key) != expected:
            raise ValueError("study pair scalar keys do not match")
        for key in keys:
            differences[key].append(by_key[key].delta)
    return spatial_paired_statistics(
        {key: tuple(values) for key, values in differences.items()},
        sources,
    )


__all__ = [
    "CityCognitionStudyReceiptV2",
    "SpatialStudyArmReceiptV2",
    "SpatialStudyPairReceiptV2",
    "SpatialStudyResponseReceiptV2",
    "SpatialStudyResultV2",
    "classify_cognition_provenance",
    "cognition_measurement_key",
    "statistics_for_v2_pairs",
]
