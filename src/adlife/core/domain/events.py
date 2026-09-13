from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, field_serializer, field_validator, model_validator

from adlife.core.domain.json_values import (
    FrozenJsonMapping,
    freeze_json_mapping,
    thaw_json_mapping,
)
from adlife.core.domain.person import DomainModel

Identifier = Annotated[str, Field(min_length=1, max_length=160)]


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    ACTIVITY_CHANGED = "agent.activity_changed"
    LOCATION_CHANGED = "agent.location_changed"
    CAMPAIGN_ELIGIBLE = "campaign.eligible"
    CAMPAIGN_IMPRESSION = "campaign.impression"
    CAMPAIGN_NOTICED = "campaign.noticed"
    CAMPAIGN_IGNORED = "campaign.ignored"
    COGNITION_REQUESTED = "cognition.requested"
    COGNITION_COMPLETED = "cognition.completed"
    COGNITION_FALLBACK = "cognition.fallback"
    MEMORY_CREATED = "memory.created"
    SOCIAL_SHARED = "social.shared"
    SOCIAL_RECEIVED = "social.received"
    STATE_UPDATED = "agent.state_updated"
    DAY_REFLECTED = "day.reflected"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class EventSource(StrEnum):
    RULE = "rule"
    MOCK = "mock"
    LOCAL_LLM = "local-llm"
    REMOTE_LLM = "remote-llm"
    REPLAY = "replay"
    FALLBACK = "fallback"


class DomainEvent(DomainModel):
    schema_version: Literal[1] = 1
    event_id: Identifier
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    simulated_minute: int = Field(ge=0)
    sequence: int = Field(ge=0)
    event_type: EventType
    agent_id: str | None = Field(default=None, pattern=r"^person-[0-9]{3}$")
    campaign_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,79}$",
    )
    channel: str | None = Field(default=None, min_length=1, max_length=80)
    payload: Mapping[str, object] = Field(default_factory=FrozenJsonMapping)
    source: EventSource
    model_id: str | None = Field(default=None, min_length=1, max_length=120)
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    caused_by_event_ids: tuple[Identifier, ...] = Field(default=(), max_length=32)

    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload_json(cls, value: object) -> FrozenJsonMapping:
        return freeze_json_mapping(value)

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: Mapping[str, object]) -> FrozenJsonMapping:
        return freeze_json_mapping(value)

    @field_serializer("payload")
    def serialize_payload(self, value: Mapping[str, object]) -> dict[str, object]:
        return thaw_json_mapping(value)

    @model_validator(mode="after")
    def validate_causality(self) -> Self:
        if len(self.caused_by_event_ids) != len(set(self.caused_by_event_ids)):
            raise ValueError("causal event identifiers must be unique")
        if self.event_id in self.caused_by_event_ids:
            raise ValueError("an event cannot cause itself")
        return self


__all__ = ["DomainEvent", "EventSource", "EventType"]
