"""Strict fictional response assumptions bound to one spatial campaign scenario."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from hashlib import sha256
from typing import Any, Literal, Self

from pydantic import Field, field_serializer, field_validator, model_validator

from adlife.core.domain.city import _validate_public_metadata
from adlife.core.domain.person import (
    DomainModel,
    ShortText,
    contains_sensitive_text,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_AGENT_PATTERN = r"^person-[0-9]{3}$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_AGENT_RE = re.compile(_AGENT_PATTERN)


def _unique_text_members(value: object, *, label: str) -> object:
    if isinstance(value, (list, tuple, set, frozenset)) and all(
        isinstance(item, str) for item in value
    ):
        items = tuple(value)
        if len(items) != len(set(items)):
            raise ValueError(f"{label} must be unique")
        return frozenset(items)
    return value


def _safe_interest_members(value: frozenset[str], *, label: str) -> frozenset[str]:
    for item in value:
        _validate_public_metadata(item)
        if contains_sensitive_text(item):
            raise ValueError(f"{label} contains sensitive, private, credential, or control text")
    return value


class SpatialResponseTraits(DomainModel):
    """Transparent bounded assumptions used only after a synthetic notice."""

    price_sensitivity: float = Field(ge=0, le=1)
    novelty_seeking: float = Field(ge=0, le=1)
    advertising_skepticism: float = Field(ge=0, le=1)
    mobile_recall_encoding: float = Field(ge=0, le=1)
    roadside_recall_encoding: float = Field(ge=0, le=1)
    impulsivity: float = Field(ge=0, le=1)


class SpatialResponseProfile(DomainModel):
    """One deliberately fictional response profile without resident identity data."""

    agent_id: str = Field(pattern=_AGENT_PATTERN)
    fictional: Literal[True] = True
    interests: frozenset[ShortText] = Field(min_length=1, max_length=12)
    traits: SpatialResponseTraits

    @field_validator("interests", mode="before")
    @classmethod
    def unique_interests(cls, value: object) -> object:
        return _unique_text_members(value, label="profile interests")

    @field_validator("interests")
    @classmethod
    def safe_interests(cls, value: frozenset[str]) -> frozenset[str]:
        return _safe_interest_members(value, label="profile interests")

    @field_serializer("interests", when_used="json")
    def serialize_interests(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class SpatialCampaignResponseInput(DomainModel):
    """Numeric campaign assumptions; campaign copy is deliberately absent."""

    campaign_id: str = Field(pattern=_ID_PATTERN)
    creative_sha256: str = Field(pattern=_HASH_PATTERN)
    target_interests: frozenset[ShortText] = Field(min_length=1, max_length=12)
    relative_price: float = Field(gt=0, le=100)

    @field_validator("target_interests", mode="before")
    @classmethod
    def unique_target_interests(cls, value: object) -> object:
        return _unique_text_members(value, label="campaign target interests")

    @field_validator("target_interests")
    @classmethod
    def safe_target_interests(cls, value: frozenset[str]) -> frozenset[str]:
        return _safe_interest_members(value, label="campaign target interests")

    @field_serializer("target_interests", when_used="json")
    def serialize_target_interests(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class SpatialResponseInitialState(DomainModel):
    """One bounded initial state for one fictional agent and one campaign."""

    agent_id: str = Field(pattern=_AGENT_PATTERN)
    campaign_id: str = Field(pattern=_ID_PATTERN)
    brand_sentiment: float = Field(ge=-1, le=1)
    recall_strength: float = Field(ge=0, le=1)
    purchase_intention: float = Field(ge=0, le=1)


class SpatialResponseInput(DomainModel):
    """Canonical response assumptions for an exact scenario and fictional cohort."""

    schema_version: Literal[1] = 1
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    profiles: tuple[SpatialResponseProfile, ...] = Field(min_length=1, max_length=30)
    campaigns: tuple[SpatialCampaignResponseInput, ...] = Field(min_length=1, max_length=20)
    initial_states: tuple[SpatialResponseInitialState, ...] = Field(
        min_length=1,
        max_length=600,
    )

    @model_validator(mode="after")
    def canonical_complete_input(self) -> Self:
        profiles = tuple(sorted(self.profiles, key=lambda item: item.agent_id))
        campaigns = tuple(sorted(self.campaigns, key=lambda item: item.campaign_id))
        states = tuple(
            sorted(self.initial_states, key=lambda item: (item.agent_id, item.campaign_id))
        )
        profile_ids = tuple(item.agent_id for item in profiles)
        campaign_ids = tuple(item.campaign_id for item in campaigns)
        state_keys = tuple((item.agent_id, item.campaign_id) for item in states)
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("duplicate spatial response profile identifier")
        if len(campaign_ids) != len(set(campaign_ids)):
            raise ValueError("duplicate spatial response campaign identifier")
        if len(state_keys) != len(set(state_keys)):
            raise ValueError("duplicate spatial response initial state")
        expected_state_keys = {
            (agent_id, campaign_id) for agent_id in profile_ids for campaign_id in campaign_ids
        }
        if set(state_keys) != expected_state_keys:
            raise ValueError(
                "spatial response initial states must cover every profile and campaign"
            )
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "campaigns", campaigns)
        object.__setattr__(self, "initial_states", states)
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


def validate_spatial_response_input(
    response_input: SpatialResponseInput,
    scenario: SpatialCampaignScenario,
    *,
    agent_ids: Collection[str],
) -> None:
    """Bind one response document to exact scenario campaigns and cohort identifiers."""
    if not isinstance(response_input, SpatialResponseInput):
        raise TypeError("response_input must be SpatialResponseInput")
    if not isinstance(scenario, SpatialCampaignScenario):
        raise TypeError("scenario must be SpatialCampaignScenario")
    if isinstance(agent_ids, (str, bytes)) or not isinstance(agent_ids, Collection):
        raise TypeError("agent_ids must be a collection of agent identifiers")
    supplied_agent_ids = tuple(agent_ids)
    if len(supplied_agent_ids) != len(set(supplied_agent_ids)):
        raise ValueError("city agent identifiers must be unique")
    if any(
        not isinstance(agent_id, str) or _AGENT_RE.fullmatch(agent_id) is None
        for agent_id in supplied_agent_ids
    ):
        raise ValueError("city agent identifier is invalid")
    if response_input.city_sha256 != scenario.city_sha256:
        raise ValueError("spatial response city fingerprint does not match scenario")
    if response_input.scenario_sha256 != scenario.fingerprint:
        raise ValueError("spatial response scenario fingerprint does not match scenario")
    profile_ids = {profile.agent_id for profile in response_input.profiles}
    if profile_ids != set(supplied_agent_ids):
        raise ValueError("spatial response agent identifiers do not match city population")
    expected_campaigns = {
        campaign.campaign_id: campaign.creative_sha256 for campaign in scenario.campaigns
    }
    actual_campaigns = {
        campaign.campaign_id: campaign.creative_sha256 for campaign in response_input.campaigns
    }
    if set(actual_campaigns) != set(expected_campaigns):
        raise ValueError("spatial response campaign identifiers do not match scenario")
    if actual_campaigns != expected_campaigns:
        raise ValueError("spatial response creative fingerprints do not match scenario")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("spatial response JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"spatial response JSON contains non-finite constant {value}")


def parse_spatial_response_input_json(document: str | bytes) -> SpatialResponseInput:
    """Parse one response document without coercing version tokens or duplicate keys."""
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("spatial response input is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("spatial response input must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int:
        raise ValueError("spatial response input schema_version must be an integer")
    if version != 1:
        raise ValueError("unsupported spatial response input schema_version")
    normalized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return SpatialResponseInput.model_validate_json(normalized)


__all__ = [
    "SpatialCampaignResponseInput",
    "SpatialResponseInitialState",
    "SpatialResponseInput",
    "SpatialResponseProfile",
    "SpatialResponseTraits",
    "parse_spatial_response_input_json",
    "validate_spatial_response_input",
]
