from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from adlife.core.domain.person import DomainModel

Identifier = Annotated[str, Field(min_length=1, max_length=160)]
CampaignId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]
Channel = Literal["mobile-feed", "highway-billboard"]
Activity = Literal[
    "sleep",
    "breakfast",
    "commute",
    "study",
    "work",
    "shopping",
    "leisure",
    "socializing",
    "phone-check",
    "reflection",
]


class Memory(DomainModel):
    schema_version: Literal[1] = 1
    memory_id: Identifier
    created_minute: int = Field(ge=0, le=10080)
    kind: Literal["advertising", "social", "purchase-proxy", "reflection"]
    summary: str = Field(min_length=1, max_length=280)
    salience: float = Field(ge=0, le=1)
    campaign_id: CampaignId | None = None
    caused_by_event_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def require_unique_causes(self) -> Self:
        if len(self.caused_by_event_ids) != len(set(self.caused_by_event_ids)):
            raise ValueError("causal event identifiers must be unique")
        return self


class ExposureCount(DomainModel):
    schema_version: Literal[1] = 1
    campaign_id: CampaignId
    channel: Channel
    count: int = Field(ge=0, le=14)


class ConsumerState(DomainModel):
    schema_version: Literal[1] = 1
    agent_id: str = Field(pattern=r"^person-[0-9]{3}$")
    location: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    current_route_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,79}$",
    )
    activity: Activity
    mood: float = Field(ge=-1, le=1)
    fatigue: float = Field(ge=0, le=1)
    brand_sentiment: float = Field(ge=-1, le=1)
    recall_strength: float = Field(ge=0, le=1)
    purchase_intention: float = Field(ge=0, le=1)
    exposure_counts: tuple[ExposureCount, ...] = Field(default=(), max_length=60)
    memories: tuple[Memory, ...] = Field(default=(), max_length=5)
    daily_reflection: str | None = Field(default=None, min_length=1, max_length=1000)
    cognition_budget_remaining: int = Field(ge=0, le=6)
    ad_fatigue: float = Field(default=0, ge=0, le=1)
    daily_reinforcement: float = Field(default=0, ge=0, le=0.2)
    social_proof: float = Field(default=0, ge=-1, le=1)
    aware_campaign_ids: frozenset[CampaignId] = Field(default_factory=frozenset)
    active_need: bool = False
    disposable_budget: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_unique_nested_records(self) -> Self:
        exposure_keys = tuple(
            (exposure.campaign_id, exposure.channel) for exposure in self.exposure_counts
        )
        if len(exposure_keys) != len(set(exposure_keys)):
            raise ValueError("duplicate exposure count for campaign and channel")
        memory_ids = tuple(memory.memory_id for memory in self.memories)
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("duplicate memory_id")
        return self

    def exposure_count(self, campaign_id: str, channel: str) -> int:
        return next(
            (
                exposure.count
                for exposure in self.exposure_counts
                if exposure.campaign_id == campaign_id and exposure.channel == channel
            ),
            0,
        )


__all__ = ["Activity", "ConsumerState", "ExposureCount", "Memory"]
