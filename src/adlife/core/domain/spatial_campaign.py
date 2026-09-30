"""Versioned geographic campaign inputs bound to one immutable city pack."""

from __future__ import annotations

import json
from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from adlife.core.domain.city import TravelDirection, _validate_public_metadata
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_ACTIVITY_ORDER = {"home": 0, "commute": 1, "work": 2, "leisure": 3}

SpatialActivity = Literal["home", "commute", "work", "leisure"]
BillboardSide = Literal["left", "right"]


class SpatialActiveWindow(DomainModel):
    """One half-open absolute model-time interval."""

    start_minute: int = Field(ge=0, lt=10_080)
    end_minute: int = Field(gt=0, le=10_080)

    @model_validator(mode="after")
    def forward_window(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("spatial active window end must be after start")
        return self


class SpatialCampaign(DomainModel):
    """Fictional campaign identity plus exact creative content hash."""

    campaign_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    creative_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator("name")
    @classmethod
    def public_name(cls, value: str) -> str:
        return _validate_public_metadata(value)


class _SpatialPlacement(DomainModel):
    placement_id: str = Field(pattern=_ID_PATTERN)
    campaign_id: str = Field(pattern=_ID_PATTERN)
    active_windows: tuple[SpatialActiveWindow, ...] = Field(min_length=1, max_length=14)
    frequency_cap_per_agent_per_day: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def canonical_non_overlapping_windows(self) -> Self:
        windows = tuple(
            sorted(
                self.active_windows,
                key=lambda item: (item.start_minute, item.end_minute),
            )
        )
        for previous, current in pairwise(windows):
            if current.start_minute < previous.end_minute:
                raise ValueError("spatial active windows cannot overlap")
        object.__setattr__(self, "active_windows", windows)
        return self


class RoadsideBillboardPlacement(_SpatialPlacement):
    """One explicit point on one physical road, without visibility semantics."""

    channel: Literal["roadside-billboard"]
    road_id: str = Field(pattern=_ID_PATTERN)
    travel_direction: TravelDirection
    road_fraction: float = Field(gt=0, lt=1)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85, le=85)
    side: BillboardSide
    orientation_degrees: float = Field(ge=0, lt=360)
    max_view_distance_meters: float = Field(gt=0, le=1_000)


class PhoneOpportunityPlacement(_SpatialPlacement):
    """Declared synthetic device-use opportunity policy; not observed behavior."""

    channel: Literal["mobile-feed"]
    opportunity_model: Literal["keyed-activity-minute-v1"]
    eligible_activities: tuple[SpatialActivity, ...] = Field(min_length=1, max_length=4)
    opportunity_probability_per_minute: float = Field(ge=0, le=1)

    @field_validator("eligible_activities")
    @classmethod
    def canonical_activities(
        cls, value: tuple[SpatialActivity, ...]
    ) -> tuple[SpatialActivity, ...]:
        if len(value) != len(set(value)):
            raise ValueError("eligible phone activities must be unique")
        return tuple(sorted(value, key=_ACTIVITY_ORDER.__getitem__))


SpatialPlacement: TypeAlias = Annotated[
    RoadsideBillboardPlacement | PhoneOpportunityPlacement,
    Field(discriminator="channel"),
]


class SpatialCampaignScenario(DomainModel):
    """Canonical campaign and placement inputs for one exact city graph."""

    schema_version: Literal[1] = 1
    scenario_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    days: int = Field(ge=1, le=7)
    city_id: str = Field(pattern=_ID_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    campaigns: tuple[SpatialCampaign, ...] = Field(min_length=1, max_length=20)
    placements: tuple[SpatialPlacement, ...] = Field(min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def public_name(cls, value: str) -> str:
        return _validate_public_metadata(value)

    @model_validator(mode="after")
    def canonical_scenario(self) -> Self:
        campaigns = tuple(sorted(self.campaigns, key=lambda item: item.campaign_id))
        placements = tuple(sorted(self.placements, key=lambda item: item.placement_id))

        campaign_ids = tuple(item.campaign_id for item in campaigns)
        if len(campaign_ids) != len(set(campaign_ids)):
            raise ValueError("duplicate spatial campaign identifier")
        placement_ids = tuple(item.placement_id for item in placements)
        if len(placement_ids) != len(set(placement_ids)):
            raise ValueError("duplicate spatial placement identifier")

        known_campaigns = set(campaign_ids)
        referenced_campaigns = {item.campaign_id for item in placements}
        if not referenced_campaigns <= known_campaigns:
            raise ValueError("spatial placement references an unknown campaign")
        if known_campaigns - referenced_campaigns:
            raise ValueError("spatial campaign exists without a placement")

        duration = self.days * 1_440
        if any(
            window.end_minute > duration
            for placement in placements
            for window in placement.active_windows
        ):
            raise ValueError("spatial active window exceeds scenario duration")

        billboard_keys = [
            (
                item.road_id,
                item.travel_direction,
                item.road_fraction,
                item.side,
            )
            for item in placements
            if isinstance(item, RoadsideBillboardPlacement)
        ]
        if len(billboard_keys) != len(set(billboard_keys)):
            raise ValueError("duplicate physical billboard placement")

        phone_keys = [
            (
                item.campaign_id,
                item.opportunity_model,
                item.eligible_activities,
                item.opportunity_probability_per_minute,
                item.frequency_cap_per_agent_per_day,
                tuple((window.start_minute, window.end_minute) for window in item.active_windows),
            )
            for item in placements
            if isinstance(item, PhoneOpportunityPlacement)
        ]
        if len(phone_keys) != len(set(phone_keys)):
            raise ValueError("duplicate phone opportunity policy")

        object.__setattr__(self, "campaigns", campaigns)
        object.__setattr__(self, "placements", placements)
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("spatial campaign JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"spatial campaign JSON contains non-finite constant {value}")


def parse_spatial_campaign_scenario_json(document: str | bytes) -> SpatialCampaignScenario:
    """Parse one strict scenario document without version-token coercion."""
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("spatial campaign scenario is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("spatial campaign scenario must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int:
        raise ValueError("spatial campaign scenario schema_version must be an integer")
    if version != 1:
        raise ValueError("unsupported spatial campaign scenario schema_version")
    normalized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return SpatialCampaignScenario.model_validate_json(normalized)


__all__ = [
    "BillboardSide",
    "PhoneOpportunityPlacement",
    "RoadsideBillboardPlacement",
    "SpatialActiveWindow",
    "SpatialActivity",
    "SpatialCampaign",
    "SpatialCampaignScenario",
    "SpatialPlacement",
    "parse_spatial_campaign_scenario_json",
]
