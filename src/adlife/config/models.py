from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SimulationSettings(StrictModel):
    days: int = Field(default=3, ge=1, le=7)
    tick_minutes: Literal[15] = 15
    population_size: int = Field(default=20, ge=1, le=30)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    cognition_mode: Literal["rules", "hybrid", "replay"] = "rules"
    max_cognition_per_agent: int = Field(default=6, ge=0, le=6)
    max_cognition_total: int = Field(default=180, ge=0, le=180)


class ProviderSettings(StrictModel):
    mode: Literal["mock", "local", "remote"] = "mock"
    base_url: HttpUrl | None = None
    model: str = Field(default="mock-v1", min_length=1, max_length=120)
    timeout_seconds: float = Field(default=60, ge=1, le=120)
    retries: int = Field(default=2, ge=0, le=2)

    @model_validator(mode="before")
    @classmethod
    def apply_and_validate_url(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("mode") == "local" and data.get("base_url") is None:
            data["base_url"] = "http://127.0.0.1:11434/v1"
        if data.get("mode") == "remote" and data.get("base_url") is None:
            raise ValueError("remote provider requires base_url")
        return data


class AppConfig(StrictModel):
    schema_version: Literal[1]
    simulation: SimulationSettings
    provider: ProviderSettings
