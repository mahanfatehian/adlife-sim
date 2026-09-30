"""Versioned integrity contract for a saved, synthetic city mobility trace."""

from __future__ import annotations

import json
from typing import Any, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from adlife.core.domain.person import DomainModel

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_RESERVED_RUN_IDS = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


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
    package_version: str = Field(min_length=1, max_length=40)
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
    package_version: str = Field(min_length=1, max_length=40)
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
    package_version: str = Field(min_length=1, max_length=40)
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


CityRunManifestDocument: TypeAlias = CityRunManifest | CityRunManifestV2 | CityRunManifestV3


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
    raise ValueError("unsupported city run manifest schema_version")


__all__ = [
    "CityRunManifest",
    "CityRunManifestDocument",
    "CityRunManifestV2",
    "CityRunManifestV3",
    "CityTraceSummary",
    "parse_city_run_manifest_json",
]
