"""Versioned integrity contract for a saved, synthetic city mobility trace."""

from __future__ import annotations

from typing import Literal, Self

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


__all__ = ["CityRunManifest", "CityTraceSummary"]
