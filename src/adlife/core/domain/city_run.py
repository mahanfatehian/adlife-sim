"""Versioned integrity contract for a saved, synthetic city mobility trace."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import AfterValidator, ConfigDict, Field, field_validator, model_validator

from adlife.core.domain.city_cognition import (
    MAX_CITY_COGNITION_INPUT_BYTES,
    MAX_CITY_COGNITION_REQUESTS,
    MAX_CITY_COGNITION_STREAM_BYTES,
    MAX_CITY_COGNITION_USAGE_INTEGER,
    CityCognitionEvaluationV1,
    CityCognitionInputV1,
    CityCognitionOutcomeCountsV1,
    Hash,
)
from adlife.core.domain.city_cognition_v2 import (
    _CITY_COGNITION_TERMINAL_POLICY_HASHES_V2,
    _CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
    CityCognitionCoverageCountsV2,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
    CityCognitionModeV2,
    CityCognitionNeutralizationCountsV2,
    CityCognitionOutcomeCountsV2,
    CityCognitionReasonCountsV2,
    CityCognitionSummaryV2,
)
from adlife.core.domain.person import DomainModel, contains_secret_or_email_text
from adlife.core.domain.serialization import canonical_json

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_RESERVED_RUN_IDS = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}

# PEP 440's version grammar, without surrounding whitespace or normalization:
# https://packaging.python.org/en/latest/specifications/version-specifiers/
_PACKAGE_VERSION_PATTERN = re.compile(
    r"v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*"
    r"(?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?[0-9]*)?"
    r"(?:(?:-[0-9]+)|(?:[-_.]?(?:post|rev|r)[-_.]?[0-9]*))?"
    r"(?:[-_.]?dev[-_.]?[0-9]*)?"
    r"(?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?",
    re.IGNORECASE | re.ASCII,
)


def _validate_package_version(value: str) -> str:
    if contains_secret_or_email_text(value):
        raise ValueError("package version carries credential-shaped text")
    if _PACKAGE_VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError("package version must use safe PEP 440 text")
    return value


PackageVersion: TypeAlias = Annotated[
    str, Field(min_length=1, max_length=40), AfterValidator(_validate_package_version)
]


class CityTraceSummary(DomainModel):
    """Hashes and counts of every core frame and the generated agent assignments."""

    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    frame_count: int = Field(ge=1, le=10_080)
    position_count: int = Field(ge=1, le=302_400)

    @model_validator(mode="after")
    def count_positions(self) -> Self:
        if self.position_count < self.frame_count:
            raise ValueError("each frame must contain at least one position")
        return self


class CityRunManifest(DomainModel):
    """A complete saved mobility run; distinct from a zone advertising run."""

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    status: Literal["completed"] = "completed"
    model_id: Literal["illustrative-road-mobility-v1"] = "illustrative-road-mobility-v1"
    package_version: PackageVersion
    python_version: str = Field(pattern=r"^3\.(?:11|12|13)\.[0-9]+$")
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    frame_count: int = Field(ge=1, le=10_080)
    position_count: int = Field(ge=1, le=302_400)

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        if value in _RESERVED_RUN_IDS:
            raise ValueError("run identifier is reserved on Windows")
        return value

    @model_validator(mode="after")
    def complete_counts(self) -> Self:
        if self.frame_count != self.days * 1_440:
            raise ValueError("frame count does not match run duration")
        if self.position_count != self.frame_count * self.agent_count:
            raise ValueError("position count does not match run population")
        return self


class CityRunManifestV2(DomainModel):
    """A saved trace generated from a geometry-preserving v2 city pack."""

    schema_version: Literal[2] = 2
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    status: Literal["completed"] = "completed"
    model_id: Literal["illustrative-road-mobility-v2"] = "illustrative-road-mobility-v2"
    package_version: PackageVersion
    python_version: str = Field(pattern=r"^3\.(?:11|12|13)\.[0-9]+$")
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    frame_count: int = Field(ge=1, le=10_080)
    position_count: int = Field(ge=1, le=302_400)
    city_schema_version: Literal[2]

    @field_validator("city_schema_version", mode="before")
    @classmethod
    def exact_city_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("city_schema_version must be integer 2")
        return value

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        if value in _RESERVED_RUN_IDS:
            raise ValueError("run identifier is reserved on Windows")
        return value

    @model_validator(mode="after")
    def complete_counts(self) -> Self:
        if self.frame_count != self.days * 1_440:
            raise ValueError("frame count does not match run duration")
        if self.position_count != self.frame_count * self.agent_count:
            raise ValueError("position count does not match run population")
        return self


class CityRunManifestV3(DomainModel):
    """A saved trace with a frozen synthetic place set and assignments."""

    schema_version: Literal[3] = 3
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    status: Literal["completed"] = "completed"
    model_id: Literal["illustrative-road-mobility-v3"] = "illustrative-road-mobility-v3"
    package_version: PackageVersion
    python_version: str = Field(pattern=r"^3\.(?:11|12|13)\.[0-9]+$")
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    place_set_sha256: str = Field(pattern=_HASH_PATTERN)
    place_assignments_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    frame_count: int = Field(ge=1, le=10_080)
    position_count: int = Field(ge=1, le=302_400)
    city_schema_version: Literal[1, 2]
    place_schema_version: Literal[1]

    @field_validator("city_schema_version", mode="before")
    @classmethod
    def exact_city_schema_version(cls, value: object) -> object:
        if type(value) is not int or value not in {1, 2}:
            raise ValueError("city_schema_version must be integer 1 or 2")
        return value

    @field_validator("place_schema_version", mode="before")
    @classmethod
    def exact_place_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("place_schema_version must be integer 1")
        return value

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        if value in _RESERVED_RUN_IDS:
            raise ValueError("run identifier is reserved on Windows")
        return value

    @model_validator(mode="after")
    def complete_counts(self) -> Self:
        if self.frame_count != self.days * 1_440:
            raise ValueError("frame count does not match run duration")
        if self.position_count != self.frame_count * self.agent_count:
            raise ValueError("position count does not match run population")
        return self


class CityRunManifestV4(DomainModel):
    """A saved city trace with frozen spatial opportunity evidence."""

    schema_version: Literal[4] = 4
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    status: Literal["completed"] = "completed"
    model_id: Literal["illustrative-road-spatial-study-v1"] = "illustrative-road-spatial-study-v1"
    package_version: PackageVersion
    python_version: str = Field(pattern=r"^3\.(?:11|12|13)\.[0-9]+$")
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_summary_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_stream_bytes: int = Field(ge=0, le=536_870_912)
    opportunity_count: int = Field(ge=0, le=520_800)
    seed: int = Field(ge=0, le=2**63 - 1)
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    frame_count: int = Field(ge=1, le=10_080)
    position_count: int = Field(ge=1, le=302_400)
    city_schema_version: Literal[1, 2]
    spatial_scenario_schema_version: Literal[1]
    spatial_opportunity_schema_version: Literal[1]
    place_schema_version: Literal[1] | None = None
    place_set_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    place_assignments_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)

    @field_validator("city_schema_version", mode="before")
    @classmethod
    def exact_city_schema_version(cls, value: object) -> object:
        if type(value) is not int or value not in {1, 2}:
            raise ValueError("city_schema_version must be integer 1 or 2")
        return value

    @field_validator(
        "spatial_scenario_schema_version",
        "spatial_opportunity_schema_version",
        mode="before",
    )
    @classmethod
    def exact_spatial_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("spatial schema versions must be integer 1")
        return value

    @field_validator("place_schema_version", mode="before")
    @classmethod
    def exact_optional_place_schema_version(cls, value: object) -> object:
        if value is not None and (type(value) is not int or value != 1):
            raise ValueError("place_schema_version must be integer 1 when present")
        return value

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        if value in _RESERVED_RUN_IDS:
            raise ValueError("run identifier is reserved on Windows")
        return value

    @model_validator(mode="after")
    def complete_bindings(self) -> Self:
        if self.frame_count != self.days * 1_440:
            raise ValueError("frame count does not match run duration")
        if self.position_count != self.frame_count * self.agent_count:
            raise ValueError("position count does not match run population")
        place_values = (
            self.place_schema_version,
            self.place_set_sha256,
            self.place_assignments_sha256,
        )
        if any(value is None for value in place_values) and any(
            value is not None for value in place_values
        ):
            raise ValueError("spatial city run place binding must be complete when present")
        return self


class CityRunManifestV5(DomainModel):
    """A spatial city trace with frozen synthetic attention evidence."""

    schema_version: Literal[5] = 5
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    status: Literal["completed"] = "completed"
    model_id: Literal["illustrative-road-spatial-attention-study-v1"] = (
        "illustrative-road-spatial-attention-study-v1"
    )
    package_version: PackageVersion
    python_version: str = Field(pattern=r"^3\.(?:11|12|13)\.[0-9]+$")
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_summary_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_stream_bytes: int = Field(ge=0, le=536_870_912)
    opportunity_count: int = Field(ge=0, le=520_800)
    attention_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    attention_summary_sha256: str = Field(pattern=_HASH_PATTERN)
    attention_stream_bytes: int = Field(ge=0, le=1_073_741_824)
    impression_count: int = Field(ge=0, le=520_800)
    noticed_count: int = Field(ge=0, le=520_800)
    seed: int = Field(ge=0, le=2**63 - 1)
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    frame_count: int = Field(ge=1, le=10_080)
    position_count: int = Field(ge=1, le=302_400)
    city_schema_version: Literal[1, 2]
    spatial_scenario_schema_version: Literal[1]
    spatial_opportunity_schema_version: Literal[1]
    spatial_attention_schema_version: Literal[1]
    spatial_attention_model_id: Literal["spatial-attention-v1"]
    place_schema_version: Literal[1] | None = None
    place_set_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    place_assignments_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)

    @field_validator("city_schema_version", mode="before")
    @classmethod
    def exact_city_schema_version(cls, value: object) -> object:
        if type(value) is not int or value not in {1, 2}:
            raise ValueError("city_schema_version must be integer 1 or 2")
        return value

    @field_validator(
        "spatial_scenario_schema_version",
        "spatial_opportunity_schema_version",
        "spatial_attention_schema_version",
        mode="before",
    )
    @classmethod
    def exact_spatial_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("spatial schema versions must be integer 1")
        return value

    @field_validator("place_schema_version", mode="before")
    @classmethod
    def exact_optional_place_schema_version(cls, value: object) -> object:
        if value is not None and (type(value) is not int or value != 1):
            raise ValueError("place_schema_version must be integer 1 when present")
        return value

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        if value in _RESERVED_RUN_IDS:
            raise ValueError("run identifier is reserved on Windows")
        return value

    @model_validator(mode="after")
    def complete_bindings(self) -> Self:
        if self.frame_count != self.days * 1_440:
            raise ValueError("frame count does not match run duration")
        if self.position_count != self.frame_count * self.agent_count:
            raise ValueError("position count does not match run population")
        if self.impression_count != self.opportunity_count:
            raise ValueError("attention impressions must match spatial opportunities")
        if self.noticed_count > self.impression_count:
            raise ValueError("attention notices cannot exceed impressions")
        place_values = (
            self.place_schema_version,
            self.place_set_sha256,
            self.place_assignments_sha256,
        )
        if any(value is None for value in place_values) and any(
            value is not None for value in place_values
        ):
            raise ValueError("spatial city run place binding must be complete when present")
        return self


class _CityRunResponseManifestCommon(DomainModel):
    """Fields shared by response runs with and without workbench provenance."""

    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    status: Literal["completed"] = "completed"
    model_id: Literal["illustrative-road-spatial-response-study-v1"] = (
        "illustrative-road-spatial-response-study-v1"
    )
    package_version: PackageVersion
    python_version: str = Field(pattern=r"^3\.(?:11|12|13)\.[0-9]+$")
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    agents_sha256: str = Field(pattern=_HASH_PATTERN)
    trace_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_summary_sha256: str = Field(pattern=_HASH_PATTERN)
    opportunity_stream_bytes: int = Field(ge=0, le=536_870_912)
    opportunity_count: int = Field(ge=0, le=520_800)
    attention_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    attention_summary_sha256: str = Field(pattern=_HASH_PATTERN)
    attention_stream_bytes: int = Field(ge=0, le=1_073_741_824)
    impression_count: int = Field(ge=0, le=520_800)
    noticed_count: int = Field(ge=0, le=520_800)
    spatial_response_schema_version: int
    spatial_response_model_id: str
    response_input_sha256: str = Field(pattern=_HASH_PATTERN)
    response_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    response_state_sha256: str = Field(pattern=_HASH_PATTERN)
    response_summary_sha256: str = Field(pattern=_HASH_PATTERN)
    response_stream_bytes: int = Field(ge=0, le=2_147_483_648)
    response_count: int = Field(ge=0, le=520_800)
    state_update_count: int = Field(ge=0, le=520_800)
    response_campaign_count: int = Field(ge=1, le=20)
    final_state_count: int = Field(ge=1, le=600)
    seed: int = Field(ge=0, le=2**63 - 1)
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    frame_count: int = Field(ge=1, le=10_080)
    position_count: int = Field(ge=1, le=302_400)
    city_schema_version: Literal[1, 2]
    spatial_scenario_schema_version: Literal[1]
    spatial_opportunity_schema_version: Literal[1]
    spatial_attention_schema_version: Literal[1]
    spatial_attention_model_id: Literal["spatial-attention-v1"]
    place_schema_version: Literal[1] | None = None
    place_set_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    place_assignments_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)

    @field_validator("city_schema_version", mode="before")
    @classmethod
    def exact_city_schema_version(cls, value: object) -> object:
        if type(value) is not int or value not in {1, 2}:
            raise ValueError("city_schema_version must be integer 1 or 2")
        return value

    @field_validator(
        "spatial_scenario_schema_version",
        "spatial_opportunity_schema_version",
        "spatial_attention_schema_version",
        mode="before",
    )
    @classmethod
    def exact_spatial_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("spatial schema versions must be integer 1")
        return value

    @field_validator("place_schema_version", mode="before")
    @classmethod
    def exact_optional_place_schema_version(cls, value: object) -> object:
        if value is not None and (type(value) is not int or value != 1):
            raise ValueError("place_schema_version must be integer 1 when present")
        return value

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        if value in _RESERVED_RUN_IDS:
            raise ValueError("run identifier is reserved on Windows")
        return value

    @model_validator(mode="after")
    def complete_response_bindings(self) -> Self:
        if self.frame_count != self.days * 1_440:
            raise ValueError("frame count does not match run duration")
        if self.position_count != self.frame_count * self.agent_count:
            raise ValueError("position count does not match run population")
        if self.impression_count != self.opportunity_count:
            raise ValueError("attention impressions must match spatial opportunities")
        if self.noticed_count > self.impression_count:
            raise ValueError("attention notices cannot exceed impressions")
        if self.response_count != self.noticed_count:
            raise ValueError("response count must match spatial notices")
        if self.response_count == 0 and self.state_update_count != 0:
            raise ValueError("response state updates require responses")
        if self.response_count > 0 and not (1 <= self.state_update_count <= self.response_count):
            raise ValueError("response state update count does not match responses")
        if self.final_state_count != self.agent_count * self.response_campaign_count:
            raise ValueError("final response state count does not cover agents and campaigns")
        place_values = (
            self.place_schema_version,
            self.place_set_sha256,
            self.place_assignments_sha256,
        )
        if any(value is None for value in place_values) and any(
            value is not None for value in place_values
        ):
            raise ValueError("spatial city run place binding must be complete when present")
        return self


class _CityRunResponseManifestBase(_CityRunResponseManifestCommon):
    """Historical exact V1 response pins shared by manifests V6 through V9."""

    spatial_response_schema_version: Literal[1]
    spatial_response_model_id: Literal["spatial-response-v1"]

    @field_validator("spatial_response_schema_version", mode="before")
    @classmethod
    def exact_response_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("spatial schema versions must be integer 1")
        return value


class CityRunManifestV6(_CityRunResponseManifestBase):
    """A spatial city trace with frozen bounded response-state evidence."""

    schema_version: Literal[6] = 6


class CityRunManifestV7(_CityRunResponseManifestBase):
    """A response run bound to its complete normalized workbench input."""

    schema_version: Literal[7] = 7
    workbench_input_schema_version: Literal[1]
    workbench_input_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator("workbench_input_schema_version", mode="before")
    @classmethod
    def exact_workbench_input_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("workbench_input_schema_version must be integer 1")
        return value


class CityRunManifestV8(_CityRunResponseManifestBase):
    """A response run bound to normalized fictional-persona workbench input."""

    schema_version: Literal[8] = 8
    workbench_input_schema_version: Literal[2]
    workbench_input_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator("workbench_input_schema_version", mode="before")
    @classmethod
    def exact_workbench_input_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("workbench_input_schema_version must be integer 2")
        return value


class CityCognitionManifestReceiptV1(DomainModel):
    """Bounded hashes and counts for the complete frozen cognition sidecars."""

    cognition_input_sha256: str = Field(pattern=_HASH_PATTERN)
    cognition_input_bytes: int = Field(ge=1, le=MAX_CITY_COGNITION_INPUT_BYTES)
    request_schema_version: Literal[1]
    result_schema_version: Literal[1]
    explainer_id: Literal[
        "city-rules-explainer-v1",
        "city-mock-explainer-v1",
        "city-provider-explainer-v1",
    ]
    prompt_contract_version: Literal[1]
    prompt_contract_sha256: str = Field(pattern=_HASH_PATTERN)
    result_schema_contract_version: Literal[1]
    result_schema_contract_sha256: str = Field(pattern=_HASH_PATTERN)
    selection_policy_version: Literal[1]
    selection_policy_sha256: str = Field(pattern=_HASH_PATTERN)
    fallback_policy_version: Literal[1]
    fallback_policy_sha256: str = Field(pattern=_HASH_PATTERN)
    provider_profile_snapshot_sha256: str | None = Field(
        default=None,
        pattern=_HASH_PATTERN,
    )
    provider_revision_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    effective_settings_sha256: str = Field(pattern=_HASH_PATTERN)
    budget_allocation_sha256: str = Field(pattern=_HASH_PATTERN)
    selected_request_count: int = Field(ge=0, le=MAX_CITY_COGNITION_REQUESTS)
    outcome_counts: CityCognitionOutcomeCountsV1
    terminal_stream_sha256: str = Field(pattern=_HASH_PATTERN)
    terminal_stream_bytes: int = Field(ge=0, le=MAX_CITY_COGNITION_STREAM_BYTES)
    terminal_record_count: int = Field(ge=0, le=MAX_CITY_COGNITION_REQUESTS)
    summary_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator(
        "request_schema_version",
        "result_schema_version",
        "prompt_contract_version",
        "result_schema_contract_version",
        "selection_policy_version",
        "fallback_policy_version",
        mode="before",
    )
    @classmethod
    def exact_version_integers(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("city cognition receipt versions must be integer 1")
        return value

    @model_validator(mode="after")
    def complete_receipt(self) -> Self:
        if (
            self.selected_request_count != self.terminal_record_count
            or self.selected_request_count != self.outcome_counts.total
        ):
            raise ValueError("city cognition receipt counts do not match")
        profile_values = (
            self.provider_profile_snapshot_sha256,
            self.provider_revision_sha256,
        )
        if any(value is None for value in profile_values) and any(
            value is not None for value in profile_values
        ):
            raise ValueError("city cognition receipt provider binding must be complete")
        provider_outcomes = (
            self.outcome_counts.provider
            + self.outcome_counts.cache
            + self.outcome_counts.fallback
            + self.outcome_counts.budget_skipped
        )
        if provider_outcomes and self.provider_profile_snapshot_sha256 is None:
            raise ValueError("provider cognition outcomes require a frozen provider binding")
        if self.explainer_id == "city-rules-explainer-v1":
            coherent_explainer = (
                self.provider_profile_snapshot_sha256 is None
                and self.outcome_counts.rules == self.selected_request_count
            )
        elif self.explainer_id == "city-mock-explainer-v1":
            coherent_explainer = (
                self.provider_profile_snapshot_sha256 is None
                and self.outcome_counts.mock == self.selected_request_count
            )
        else:
            coherent_explainer = (
                self.provider_profile_snapshot_sha256 is not None
                and self.outcome_counts.rules == 0
                and self.outcome_counts.mock == 0
            )
        if not coherent_explainer:
            raise ValueError("city cognition receipt explainer, profile, and outcomes do not match")
        if self.terminal_record_count == 0:
            if (
                self.terminal_stream_bytes != 0
                or self.terminal_stream_sha256 != sha256(b"").hexdigest()
            ):
                raise ValueError("empty cognition stream receipt does not match empty bytes")
        elif self.terminal_stream_bytes == 0:
            raise ValueError("nonempty cognition stream requires stored bytes")
        return self


class CityRunManifestV9(_CityRunResponseManifestBase):
    """A schema-3 response run with complete frozen noncausal cognition evidence."""

    schema_version: Literal[9] = 9
    workbench_input_schema_version: Literal[3]
    workbench_input_sha256: str = Field(pattern=_HASH_PATTERN)
    cognition: CityCognitionManifestReceiptV1

    @field_validator("workbench_input_schema_version", mode="before")
    @classmethod
    def exact_workbench_input_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 3:
            raise ValueError("workbench_input_schema_version must be integer 3")
        return value


def build_city_cognition_manifest_receipt(
    cognition_input: CityCognitionInputV1,
    cognition_evaluation: CityCognitionEvaluationV1,
) -> CityCognitionManifestReceiptV1:
    """Derive the V9 receipt solely from validated, frozen cognition evidence."""
    if type(cognition_input) is not CityCognitionInputV1:
        raise TypeError("cognition_input must be a CityCognitionInputV1")
    if type(cognition_evaluation) is not CityCognitionEvaluationV1:
        raise TypeError("cognition_evaluation must be a CityCognitionEvaluationV1")
    try:
        checked_input = CityCognitionInputV1.model_validate_json(
            json.dumps(
                cognition_input.model_dump(mode="json", warnings="none"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        checked_evaluation = CityCognitionEvaluationV1.model_validate_json(
            json.dumps(
                cognition_evaluation.model_dump(mode="json", warnings="none"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
    except ValueError:
        raise ValueError("city cognition evidence failed receipt validation") from None
    if (
        checked_evaluation.cognition_input_sha256 != checked_input.fingerprint
        or checked_evaluation.run_input_sha256 != checked_input.run_input_sha256
        or checked_evaluation.mode != checked_input.mode
    ):
        raise ValueError("city cognition evaluation does not match its frozen input")

    input_bytes = (canonical_json(checked_input) + "\n").encode("utf-8")
    summary_bytes = (canonical_json(checked_evaluation.summary) + "\n").encode("utf-8")
    effective_settings_sha256 = (
        checked_input.effective_settings.fingerprint
        if checked_input.effective_settings is not None
        else sha256(canonical_json({"effective_settings": None}).encode("utf-8")).hexdigest()
    )
    budget_allocation_sha256 = sha256(
        canonical_json(
            {
                "aggregate_output_token_ceiling": (checked_input.aggregate_output_token_ceiling),
                "reservations": [
                    item.model_dump(mode="json", warnings="none")
                    for item in checked_input.reservations
                ],
            }
        ).encode("utf-8")
    ).hexdigest()
    profile = checked_input.provider_profile
    explainer_id: Literal[
        "city-rules-explainer-v1",
        "city-mock-explainer-v1",
        "city-provider-explainer-v1",
    ]
    if checked_input.mode == "rules":
        explainer_id = "city-rules-explainer-v1"
    elif checked_input.mode == "mock":
        explainer_id = "city-mock-explainer-v1"
    else:
        explainer_id = "city-provider-explainer-v1"
    if checked_input.explainer_id != explainer_id:
        raise ValueError("city cognition input explainer does not match its mode")
    return CityCognitionManifestReceiptV1(
        cognition_input_sha256=checked_input.fingerprint,
        cognition_input_bytes=len(input_bytes),
        request_schema_version=1,
        result_schema_version=1,
        explainer_id=explainer_id,
        prompt_contract_version=checked_input.prompt_contract_version,
        prompt_contract_sha256=checked_input.prompt_contract_sha256,
        result_schema_contract_version=checked_input.result_schema_version,
        result_schema_contract_sha256=checked_input.result_schema_contract_sha256,
        selection_policy_version=checked_input.selection_policy_version,
        selection_policy_sha256=checked_input.selection_policy_sha256,
        fallback_policy_version=checked_input.fallback_policy_version,
        fallback_policy_sha256=checked_input.fallback_policy_sha256,
        provider_profile_snapshot_sha256=None if profile is None else profile.fingerprint,
        provider_revision_sha256=None if profile is None else profile.revision_sha256,
        effective_settings_sha256=effective_settings_sha256,
        budget_allocation_sha256=budget_allocation_sha256,
        selected_request_count=checked_evaluation.summary.selected_request_count,
        outcome_counts=checked_evaluation.summary.outcome_counts,
        terminal_stream_sha256=checked_evaluation.summary.terminal_stream_sha256,
        terminal_stream_bytes=checked_evaluation.summary.terminal_stream_bytes,
        terminal_record_count=checked_evaluation.summary.terminal_record_count,
        summary_sha256=sha256(summary_bytes).hexdigest(),
    )


class CityCognitionManifestReceiptV2(DomainModel):
    """Bounded policy and terminal evidence for a causal cognition run."""

    model_config = ConfigDict(hide_input_in_errors=True, revalidate_instances="always")

    cognition_input_sha256: Hash
    cognition_input_bytes: int = Field(ge=1, le=131_073)
    causal_contract_sha256: Hash
    request_schema_version: Literal[2]
    result_schema_version: Literal[2]
    effect_scope: Literal["bounded-signals-v1"]
    mode: CityCognitionModeV2
    prompt_contract_version: Literal[2]
    prompt_contract_sha256: Hash
    result_schema_contract_version: Literal[2]
    result_schema_contract_sha256: Hash
    selection_policy_version: Literal[2]
    selection_policy_sha256: Hash
    fallback_policy_version: Literal[2]
    fallback_policy_sha256: Hash
    mock_policy_version: Literal[1]
    mock_policy_sha256: Hash
    influence_policy_version: Literal[1]
    influence_policy_sha256: Hash
    provider_profile_snapshot_sha256: Hash | None
    provider_revision_sha256: Hash | None
    effective_settings_sha256: Hash
    budget_allocation_sha256: Hash
    funded_response_limit: Literal[10, 20, 40, 60]
    coverage: CityCognitionCoverageCountsV2
    outcomes: CityCognitionOutcomeCountsV2
    reasons: CityCognitionReasonCountsV2
    neutralization: CityCognitionNeutralizationCountsV2
    attempt_count: int = Field(ge=0, le=240)
    prompt_tokens: int = Field(ge=0, le=60 * MAX_CITY_COGNITION_USAGE_INTEGER)
    completion_tokens: int = Field(ge=0, le=60 * MAX_CITY_COGNITION_USAGE_INTEGER)
    terminal_stream_sha256: Hash
    terminal_stream_bytes: int = Field(ge=0, le=1_048_576)
    terminal_record_count: int = Field(ge=0, le=60)
    summary_sha256: Hash

    @field_validator(
        "request_schema_version",
        "result_schema_version",
        "prompt_contract_version",
        "result_schema_contract_version",
        "selection_policy_version",
        "fallback_policy_version",
        mode="before",
    )
    @classmethod
    def exact_v2_versions(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("city cognition V2 receipt versions must be integer 2")
        return value

    @field_validator("mock_policy_version", "influence_policy_version", mode="before")
    @classmethod
    def exact_v1_policy_versions(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("city cognition receipt policy versions must be integer 1")
        return value

    @field_validator("funded_response_limit", mode="before")
    @classmethod
    def exact_funded_limit(cls, value: object) -> object:
        if type(value) is not int or value not in {10, 20, 40, 60}:
            raise ValueError("funded_response_limit must be integer 10, 20, 40, or 60")
        return value

    @model_validator(mode="after")
    def complete_receipt(self) -> Self:
        if any(
            getattr(self, field) != expected
            for field, expected in _CITY_COGNITION_TERMINAL_POLICY_HASHES_V2
        ):
            raise ValueError("city cognition receipt policies do not match the frozen V2 contract")
        summary_fields = set(CityCognitionSummaryV2.model_fields) - {"schema_version", "disclosure"}
        receipt_fields = self.model_dump(mode="json")
        summary = CityCognitionSummaryV2.model_validate_json(
            canonical_json(
                {name: receipt_fields[name] for name in summary_fields}
                | {
                    "schema_version": 2,
                    "disclosure": "synthetic-bounded-cognition-not-observed-behavior",
                }
            )
        )
        if (
            self.summary_sha256
            != sha256((canonical_json(summary) + "\n").encode("utf-8")).hexdigest()
        ):
            raise ValueError("city cognition receipt summary hash does not match its summary")
        offline = self.mode in {"rules", "mock"}
        if (
            offline
            and self.effective_settings_sha256
            != sha256(canonical_json({"effective_settings": None}).encode("utf-8")).hexdigest()
        ):
            raise ValueError("offline cognition receipt settings must match the null-settings hash")
        if (self.provider_profile_snapshot_sha256 is None) != offline or (
            self.provider_revision_sha256 is None
        ) != offline:
            raise ValueError(
                "city cognition receipt requires a complete mode-specific provider binding"
            )
        if offline and self.funded_response_limit != 60:
            raise ValueError("offline cognition requires the complete local funded limit")
        if self.coverage.funded_request_count != min(
            self.coverage.selected_request_count, self.funded_response_limit
        ):
            raise ValueError("receipt funding does not match the canonical prefix")
        if self.terminal_record_count == 0:
            # Summary cardinality proves zero selections and therefore no reservations.
            empty_allocation_sha256 = sha256(
                canonical_json(
                    {
                        "aggregate_output_token_ceiling": (
                            0
                            if offline
                            else _CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
                            * self.funded_response_limit
                        ),
                        "reservations": [],
                    }
                ).encode("utf-8")
            ).hexdigest()
            if self.budget_allocation_sha256 != empty_allocation_sha256:
                raise ValueError("empty cognition receipt budget allocation hash does not match")
            if self.terminal_stream_bytes or self.terminal_stream_sha256 != sha256(b"").hexdigest():
                raise ValueError("empty cognition receipt requires empty stream bytes")
        elif self.terminal_stream_bytes == 0:
            raise ValueError("nonempty cognition receipt requires stream bytes")
        return self


def build_city_cognition_manifest_receipt_v2(
    cognition_input: CityCognitionInputV2,
    cognition_evaluation: CityCognitionEvaluationV2,
) -> CityCognitionManifestReceiptV2:
    """Derive receipt facts only from deeply revalidated frozen V2 evidence."""
    if type(cognition_input) is not CityCognitionInputV2:
        raise TypeError("cognition_input must be a CityCognitionInputV2")
    if type(cognition_evaluation) is not CityCognitionEvaluationV2:
        raise TypeError("cognition_evaluation must be a CityCognitionEvaluationV2")
    try:
        checked_input = CityCognitionInputV2.model_validate_json(
            json.dumps(
                cognition_input.model_dump(mode="json", warnings="none"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        checked_evaluation = CityCognitionEvaluationV2.model_validate_json(
            json.dumps(
                cognition_evaluation.model_dump(mode="json", warnings="none"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
    except ValueError:
        raise ValueError("city cognition V2 evidence failed receipt validation") from None
    if (
        checked_evaluation.cognition_input_sha256 != checked_input.fingerprint
        or checked_evaluation.run_input_sha256 != checked_input.run_input_sha256
        or checked_evaluation.effect_scope != checked_input.effect_scope
        or checked_evaluation.mode != checked_input.mode
        or len(checked_evaluation.records) != len(checked_input.reservations)
    ):
        raise ValueError("city cognition V2 evaluation does not match its frozen input")
    profile, settings = checked_input.provider_profile, checked_input.effective_settings
    for record, reservation in zip(
        checked_evaluation.records, checked_input.reservations, strict=True
    ):
        if settings is not None and record.attempt_count > (
            1 + settings.transient_retries + settings.repair_attempts
        ):
            raise ValueError("city cognition terminal attempts exceed frozen effective settings")
        if (
            record.selection_index != reservation.selection_index
            or record.request_id != reservation.request_id
            or record.request_record_sha256 != reservation.request_record_sha256
            or (record.source == "budget-skipped") == reservation.funded
            or record.provider_profile_id != (None if profile is None else profile.profile_id)
            or record.provider_revision_sha256
            != (None if profile is None else profile.revision_sha256)
            or record.provider_profile_snapshot_sha256
            != (None if profile is None else profile.fingerprint)
            or record.provider_protocol != (None if profile is None else profile.protocol)
            or record.provider_effective_settings_sha256
            != (None if settings is None else settings.fingerprint)
        ):
            raise ValueError(
                "city cognition terminals do not match frozen funding and provider bindings"
            )
    contract, summary = checked_input.causal_contract, checked_evaluation.summary
    facts = {
        name: getattr(contract, name)
        for name in (
            "prompt_contract_version",
            "prompt_contract_sha256",
            "result_schema_contract_sha256",
            "selection_policy_version",
            "selection_policy_sha256",
            "fallback_policy_version",
            "fallback_policy_sha256",
            "mock_policy_version",
            "mock_policy_sha256",
            "influence_policy_version",
            "influence_policy_sha256",
        )
    }
    facts.update(
        {
            name: getattr(summary, name)
            for name in (
                "coverage",
                "outcomes",
                "reasons",
                "neutralization",
                "attempt_count",
                "prompt_tokens",
                "completion_tokens",
                "terminal_stream_sha256",
                "terminal_stream_bytes",
                "terminal_record_count",
            )
        }
    )
    facts.update(
        cognition_input_sha256=checked_input.fingerprint,
        cognition_input_bytes=len((canonical_json(checked_input) + "\n").encode("utf-8")),
        causal_contract_sha256=checked_input.causal_contract_sha256,
        request_schema_version=2,
        result_schema_version=2,
        effect_scope=checked_input.effect_scope,
        mode=checked_input.mode,
        result_schema_contract_version=contract.result_schema_version,
        provider_profile_snapshot_sha256=None if profile is None else profile.fingerprint,
        provider_revision_sha256=None if profile is None else profile.revision_sha256,
        effective_settings_sha256=(
            settings.fingerprint
            if settings is not None
            else sha256(canonical_json({"effective_settings": None}).encode("utf-8")).hexdigest()
        ),
        budget_allocation_sha256=sha256(
            canonical_json(
                {
                    "aggregate_output_token_ceiling": checked_input.aggregate_output_token_ceiling,
                    "reservations": [
                        item.model_dump(mode="json", warnings="none")
                        for item in checked_input.reservations
                    ],
                }
            ).encode("utf-8")
        ).hexdigest(),
        funded_response_limit=checked_input.funded_response_limit,
        summary_sha256=sha256((canonical_json(summary) + "\n").encode("utf-8")).hexdigest(),
    )
    return CityCognitionManifestReceiptV2.model_validate(facts)


class CityRunManifestV10(_CityRunResponseManifestCommon):
    """A schema-4 workbench run with frozen causal and dual-state evidence."""

    model_config = ConfigDict(hide_input_in_errors=True, revalidate_instances="always")

    schema_version: Literal[10]
    spatial_response_schema_version: Literal[2]
    spatial_response_model_id: Literal["spatial-response-v2"]
    workbench_input_schema_version: Literal[4]
    workbench_input_sha256: Hash
    baseline_response_count: int = Field(ge=0, le=520_800)
    applied_response_count: int = Field(ge=0, le=520_800)
    dual_state_update_count: int = Field(ge=0, le=520_800)
    final_cognition_state_count: int = Field(ge=1, le=600)
    rule_state_document_sha256: Hash
    cognition_state_document_sha256: Hash
    influence_policy_id: Literal["city-cognition-influence-v1"]
    influence_policy_sha256: Hash
    cognition: CityCognitionManifestReceiptV2

    @field_validator("spatial_response_schema_version", mode="before")
    @classmethod
    def exact_response_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("spatial_response_schema_version must be integer 2")
        return value

    @field_validator("workbench_input_schema_version", mode="before")
    @classmethod
    def exact_workbench_input_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 4:
            raise ValueError("workbench_input_schema_version must be integer 4")
        return value

    @model_validator(mode="after")
    def complete_dual_state_bindings(self) -> Self:
        if (
            self.response_count != self.baseline_response_count
            or self.response_count != self.applied_response_count
            or self.response_count != self.cognition.coverage.eligible_response_count
            or self.state_update_count != self.dual_state_update_count
            or self.final_state_count != self.final_cognition_state_count
            or self.influence_policy_sha256 != self.cognition.influence_policy_sha256
        ):
            raise ValueError("V10 response, dual-state, and cognition bindings do not match")
        return self


CityRunManifestDocument: TypeAlias = (
    CityRunManifest
    | CityRunManifestV2
    | CityRunManifestV3
    | CityRunManifestV4
    | CityRunManifestV5
    | CityRunManifestV6
    | CityRunManifestV7
    | CityRunManifestV8
    | CityRunManifestV9
    | CityRunManifestV10
)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("city run manifest JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"city run manifest JSON contains non-finite constant {value}")


def parse_city_run_manifest_json(document: str | bytes) -> CityRunManifestDocument:
    """Dispatch one strict manifest without coercing its schema version token."""
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("city run manifest is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("city run manifest must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int:
        raise ValueError("city run manifest schema_version must be an integer")
    normalized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if version == 1:
        return CityRunManifest.model_validate_json(normalized)
    if version == 2:
        return CityRunManifestV2.model_validate_json(normalized)
    if version == 3:
        return CityRunManifestV3.model_validate_json(normalized)
    if version == 4:
        return CityRunManifestV4.model_validate_json(normalized)
    if version == 5:
        return CityRunManifestV5.model_validate_json(normalized)
    if version == 6:
        return CityRunManifestV6.model_validate_json(normalized)
    if version == 7:
        return CityRunManifestV7.model_validate_json(normalized)
    if version == 8:
        return CityRunManifestV8.model_validate_json(normalized)
    if version == 9:
        return CityRunManifestV9.model_validate_json(normalized)
    if version == 10:
        return CityRunManifestV10.model_validate_json(normalized)
    raise ValueError("unsupported city run manifest schema_version")


__all__ = [
    "CityCognitionManifestReceiptV1",
    "CityCognitionManifestReceiptV2",
    "CityRunManifest",
    "CityRunManifestDocument",
    "CityRunManifestV2",
    "CityRunManifestV3",
    "CityRunManifestV4",
    "CityRunManifestV5",
    "CityRunManifestV6",
    "CityRunManifestV7",
    "CityRunManifestV8",
    "CityRunManifestV9",
    "CityRunManifestV10",
    "CityTraceSummary",
    "PackageVersion",
    "build_city_cognition_manifest_receipt",
    "build_city_cognition_manifest_receipt_v2",
    "parse_city_run_manifest_json",
]
