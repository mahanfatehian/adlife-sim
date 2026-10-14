"""Versioned integrity contract for a saved, synthetic city mobility trace."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import AfterValidator, Field, field_validator, model_validator

from adlife.core.domain.city_cognition import (
    MAX_CITY_COGNITION_INPUT_BYTES,
    MAX_CITY_COGNITION_REQUESTS,
    MAX_CITY_COGNITION_STREAM_BYTES,
    CityCognitionEvaluationV1,
    CityCognitionInputV1,
    CityCognitionOutcomeCountsV1,
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


class _CityRunResponseManifestBase(DomainModel):
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
    spatial_response_schema_version: Literal[1]
    spatial_response_model_id: Literal["spatial-response-v1"]
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
        "spatial_response_schema_version",
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
    raise ValueError("unsupported city run manifest schema_version")


__all__ = [
    "CityCognitionManifestReceiptV1",
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
    "CityTraceSummary",
    "PackageVersion",
    "build_city_cognition_manifest_receipt",
    "parse_city_run_manifest_json",
]
