from typing import Annotated, Literal, Self

from pydantic import Field, field_serializer, model_validator

from adlife.core.domain.person import DomainModel

ShortText = Annotated[str, Field(min_length=1, max_length=120)]
CampaignId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]


class Price(DomainModel):
    amount: float = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class CreativeFeatures(DomainModel):
    schema_version: Literal[1] = 1
    description: str = Field(min_length=1, max_length=1000)
    visual_style: str | None = Field(default=None, min_length=1, max_length=120)
    dominant_colors: tuple[ShortText, ...] = Field(default=(), max_length=8)
    visible_text: tuple[ShortText, ...] = Field(default=(), max_length=12)
    contains_people: bool = False


class TimeWindow(DomainModel):
    start_minute_of_day: int = Field(ge=0, lt=1440)
    end_minute_of_day: int = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def require_forward_window(self) -> Self:
        if self.end_minute_of_day <= self.start_minute_of_day:
            raise ValueError("end_minute_of_day must be after start_minute_of_day")
        return self


class PhonePlacement(DomainModel):
    schema_version: Literal[1] = 1
    channel: Literal["mobile-feed"]
    zone: Literal["online"] = "online"
    active_windows: tuple[TimeWindow, ...] = Field(min_length=1)
    frequency_cap: int = Field(ge=1, le=7)
    visibility: float = Field(ge=0, le=1)


class BillboardPlacement(DomainModel):
    schema_version: Literal[1] = 1
    channel: Literal["highway-billboard"]
    route_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    active_windows: tuple[TimeWindow, ...] = Field(min_length=1)
    frequency_cap: int = Field(ge=1, le=14)
    visibility: float = Field(ge=0, le=1)


Placement = Annotated[
    PhonePlacement | BillboardPlacement,
    Field(discriminator="channel"),
]


class Campaign(DomainModel):
    schema_version: Literal[1] = 1
    campaign_id: CampaignId
    name: ShortText
    product_name: ShortText
    product_category: CampaignId
    message: str = Field(min_length=1, max_length=1000)
    call_to_action: str = Field(min_length=1, max_length=240)
    price: Price
    category_reference_price: float = Field(
        gt=0,
        description="Amount denominated in price.currency.",
    )
    target_interests: frozenset[ShortText] = Field(min_length=1, max_length=12)
    start_minute: int = Field(ge=0, lt=10080)
    end_minute: int = Field(gt=0, le=10080)
    placements: tuple[Placement, ...] = Field(min_length=1)
    asset_path: str | None = Field(default=None, min_length=1, max_length=500)
    asset_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    creative_features: CreativeFeatures

    @field_serializer("target_interests", when_used="json")
    def serialize_target_interests(self, value: frozenset[str]) -> list[str]:
        """Serialize targeting in a stable order so persisted campaigns stay byte-identical."""
        return sorted(value)

    @model_validator(mode="after")
    def validate_campaign(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be after start_minute")
        placement_keys = tuple(placement.model_dump_json() for placement in self.placements)
        if len(placement_keys) != len(set(placement_keys)):
            raise ValueError("placements must be unique")
        if self.asset_path is not None and self.asset_sha256 is None:
            raise ValueError("asset_sha256 is required when asset_path is present")
        return self


__all__ = [
    "BillboardPlacement",
    "Campaign",
    "CreativeFeatures",
    "PhonePlacement",
    "Placement",
    "Price",
    "TimeWindow",
]
