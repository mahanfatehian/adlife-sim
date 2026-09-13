from collections.abc import Mapping
from typing import Literal, Self, cast

from pydantic import Field, field_serializer, field_validator, model_validator

from adlife.core.domain.json_values import (
    FrozenJsonMapping,
    freeze_json_mapping,
    thaw_json_mapping,
)
from adlife.core.domain.person import DomainModel


def _empty_metrics() -> Mapping[str, float]:
    return cast(Mapping[str, float], FrozenJsonMapping())


class RunManifest(DomainModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    scenario_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(ge=0, le=2**63 - 1)
    package_version: str = Field(min_length=1, max_length=40)
    git_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|uncommitted)$")
    lockfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["rules", "mock", "local", "remote", "replay"]
    model_id: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=80)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    platform: str = Field(min_length=1, max_length=200)


class SimulationResult(DomainModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    status: Literal["completed", "failed", "interrupted"]
    final_minute: int = Field(ge=0, le=10080)
    event_count: int = Field(ge=0)
    metrics: Mapping[str, float] = Field(default_factory=_empty_metrics)
    failure_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("metrics", mode="before")
    @classmethod
    def validate_metrics_json(cls, value: object) -> FrozenJsonMapping:
        return freeze_json_mapping(value)

    @field_validator("metrics")
    @classmethod
    def freeze_metrics(cls, value: Mapping[str, float]) -> Mapping[str, float]:
        return cast(Mapping[str, float], freeze_json_mapping(value))

    @field_serializer("metrics")
    def serialize_metrics(self, value: Mapping[str, float]) -> dict[str, float]:
        return cast(dict[str, float], thaw_json_mapping(value))

    @model_validator(mode="after")
    def require_failure_reason(self) -> Self:
        if self.status == "failed" and self.failure_reason is None:
            raise ValueError("failed result requires failure_reason")
        return self


__all__ = ["RunManifest", "SimulationResult"]
