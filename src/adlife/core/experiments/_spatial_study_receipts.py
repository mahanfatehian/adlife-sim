"""Compact, path-free receipts for the repeated-seed spatial study contract."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, HttpUrl, field_validator, model_validator

from adlife.core.domain.city import (
    CitySource,
    _validate_public_metadata,
    _validate_public_url,
)
from adlife.core.domain.identifiers import validate_portable_run_identifier
from adlife.core.domain.person import DomainModel, contains_secret_or_email_text
from adlife.core.experiments.spatial_observations import SpatialMetricObservation

StudyHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
OpportunityClassification = Literal["matched-opportunity-structure", "opportunity-confounded"]
ResponseClassification = Literal[
    "not-applicable", "matched-response-assumptions", "response-assumption-confounded"
]


class SpatialStudyCityProvenance(DomainModel):
    """Only public, bounded city-pack metadata; no road graph or provider bodies."""

    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal[1, 2]
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=120)
    city_sha256: StudyHash
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    source: CitySource | None = None
    source_url: HttpUrl | None = None
    license: str | None = Field(default=None, min_length=1, max_length=80)
    attribution: str | None = Field(default=None, min_length=1, max_length=240)
    known_omissions: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(
        default=(), max_length=16
    )

    @field_validator("city_id", "name", "license", "attribution")
    @classmethod
    def public_text(cls, value: str | None) -> str | None:
        return _validate_public_metadata(value) if value is not None else None

    @field_validator("source_url")
    @classmethod
    def public_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _validate_public_url(value) if value is not None else None

    @field_validator("known_omissions")
    @classmethod
    def public_omissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _validate_public_metadata(item)
        if value != tuple(sorted(set(value))):
            raise ValueError("study city omissions must be unique and canonical")
        return value

    @model_validator(mode="after")
    def versioned_provenance(self) -> Self:
        if self.schema_version == 1:
            if self.source is not None or self.time_zone is not None or self.known_omissions:
                raise ValueError("v1 study city provenance cannot invent v2 metadata")
            if any(item is None for item in (self.source_url, self.license, self.attribution)):
                raise ValueError("v1 study city provenance is incomplete")
        else:
            from adlife.core.domain.city import CityPackV2

            if self.source is None or self.time_zone is None or not self.known_omissions:
                raise ValueError("v2 study city provenance is incomplete")
            CityPackV2.known_time_zone(self.time_zone)
            if any(item is not None for item in (self.source_url, self.license, self.attribution)):
                raise ValueError("v2 study city provenance cannot carry legacy metadata")
        return self


class _StudyReceipt(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)


class SpatialStudyResponseReceipt(_StudyReceipt):
    """Schema-v6/v7 response evidence, retained even for attention-only A/A checks."""

    response_input_sha256: StudyHash
    response_stream_sha256: StudyHash
    response_state_sha256: StudyHash
    response_summary_sha256: StudyHash
    response_stream_bytes: int = Field(ge=0, le=2_147_483_648)
    response_count: int = Field(ge=0, le=520_800)
    state_update_count: int = Field(ge=0, le=520_800)
    response_campaign_count: int = Field(ge=1, le=20)
    final_state_count: int = Field(ge=1, le=600)
    response_assumption_structure_sha256: StudyHash
    response_metrics_sha256: StudyHash | None = None
    campaign_ids: tuple[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")], ...] = Field(
        min_length=1, max_length=20
    )

    @model_validator(mode="after")
    def canonical_campaigns(self) -> Self:
        if self.campaign_ids != tuple(sorted(set(self.campaign_ids))):
            raise ValueError("study response campaigns must be unique and canonical")
        if any(contains_secret_or_email_text(item) for item in self.campaign_ids):
            raise ValueError("study campaign identifier is invalid")
        if len(self.campaign_ids) != self.response_campaign_count:
            raise ValueError("study response campaign count does not match its identities")
        return self


class SpatialStudyArmReceipt(_StudyReceipt):
    """Arm-specific compact fields sufficient to reconstruct the canonical manifest."""

    run_id: str
    manifest_sha256: StudyHash
    scenario_sha256: StudyHash
    opportunity_stream_sha256: StudyHash
    opportunity_summary_sha256: StudyHash
    opportunity_stream_bytes: int = Field(ge=0, le=536_870_912)
    opportunity_count: int = Field(ge=0, le=520_800)
    attention_stream_sha256: StudyHash
    attention_summary_sha256: StudyHash
    attention_stream_bytes: int = Field(ge=0, le=1_073_741_824)
    impression_count: int = Field(ge=0, le=520_800)
    noticed_count: int = Field(ge=0, le=520_800)
    opportunity_structure_sha256: StudyHash
    attention_metrics_sha256: StudyHash
    response: SpatialStudyResponseReceipt | None = None
    workbench_input_schema_version: Literal[1] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    workbench_input_sha256: StudyHash | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_validator("run_id")
    @classmethod
    def portable_identifier(cls, value: str) -> str:
        return validate_portable_run_identifier(value)

    @field_validator("workbench_input_schema_version", mode="before")
    @classmethod
    def exact_optional_workbench_version(cls, value: object) -> object:
        if value is not None and (type(value) is not int or value != 1):
            raise ValueError("workbench input schema version must be integer 1 when present")
        return value

    @model_validator(mode="after")
    def complete_workbench_binding(self) -> Self:
        if (self.workbench_input_schema_version is None) != (self.workbench_input_sha256 is None):
            raise ValueError("study arm workbench input binding is incomplete")
        return self


class SpatialStudyScalarPair(_StudyReceipt):
    """Two complete scalar observations and their exact treatment-minus-control delta."""

    control: SpatialMetricObservation
    treatment: SpatialMetricObservation
    delta: float = Field(ge=-1_041_600, le=1_041_600)

    @field_validator("delta", mode="before")
    @classmethod
    def exact_delta_float(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("study scalar delta must be a float")
        return value

    @model_validator(mode="after")
    def exact_scalar_pair(self) -> Self:
        import math

        if self.control.key != self.treatment.key:
            raise ValueError("study scalar arms must have identical keys")
        if self.control.source_artifacts != self.treatment.source_artifacts:
            raise ValueError("study scalar arms must have identical sources")
        delta = self.treatment.value - self.control.value
        if self.delta != delta or (self.delta == 0.0 and math.copysign(1.0, self.delta) < 0):
            raise ValueError("study scalar delta does not match its observations")
        return self


class SpatialStudyPairReceipt(_StudyReceipt):
    """One seed's matched mobility, two arms, classifications and scalar receipts."""

    seed: int = Field(ge=0, le=99)
    agents_sha256: StudyHash
    trace_sha256: StudyHash
    place_assignments_sha256: StudyHash | None = None
    control: SpatialStudyArmReceipt
    treatment: SpatialStudyArmReceipt
    opportunity_classification: OpportunityClassification
    response_assumption_classification: ResponseClassification
    scalars: tuple[SpatialStudyScalarPair, ...] = Field(min_length=24, max_length=328)

    @model_validator(mode="after")
    def canonical_scalar_keys(self) -> Self:
        keys = tuple(item.control.key for item in self.scalars)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("study pair scalar keys must be unique and canonical")
        return self
