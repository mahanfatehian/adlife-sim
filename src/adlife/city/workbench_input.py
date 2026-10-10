"""Strict, frozen inputs for the local fictional city workbench."""

from __future__ import annotations

from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Final, Literal, Self

from pydantic import Field, field_serializer, field_validator, model_validator

from adlife.core.domain.city import TravelDirection, _validate_public_metadata
from adlife.core.domain.identifiers import validate_portable_run_identifier
from adlife.core.domain.person import DomainModel, contains_provider_secret_text
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    BillboardSide,
    SpatialActiveWindow,
    SpatialActivity,
)
from adlife.core.domain.spatial_response import SpatialResponseTraits

_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_ACTIVITY_ORDER = {"home": 0, "commute": 1, "work": 2, "leisure": 3}
CREATIVE_DISCLOSURE: Final = "Fictional creative for synthetic simulation only."
PublicText = Annotated[str, Field(min_length=1, max_length=80)]


def _safe_public_text(value: str) -> str:
    checked = _validate_public_metadata(value)
    if contains_provider_secret_text(checked):
        raise ValueError("workbench text contains credential-shaped content")
    return checked


def _safe_identifier(value: str) -> str:
    if contains_provider_secret_text(value):
        raise ValueError("workbench identifier contains credential-shaped content")
    return value


def _unique_text_members(value: object, *, label: str) -> object:
    if isinstance(value, (list, tuple, set, frozenset)) and all(
        isinstance(item, str) for item in value
    ):
        items = tuple(value)
        if len(items) != len(set(items)):
            raise ValueError(f"{label} must be unique")
        return frozenset(items)
    return value


def _safe_interests(value: frozenset[str], *, label: str) -> frozenset[str]:
    for item in value:
        _safe_public_text(item)
    return value


class WorkbenchDailyWindow(DomainModel):
    """One half-open interval within a numbered synthetic model day."""

    day: int = Field(ge=1, le=7)
    start_minute: int = Field(ge=0, le=1_439)
    end_minute: int = Field(ge=1, le=1_440)

    @model_validator(mode="after")
    def forward_window(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("workbench active window end must be after start")
        return self


def _canonical_daily_windows(
    value: tuple[WorkbenchDailyWindow, ...],
) -> tuple[WorkbenchDailyWindow, ...]:
    windows = tuple(sorted(value, key=lambda item: (item.day, item.start_minute, item.end_minute)))
    for previous, current in pairwise(windows):
        if current.day == previous.day and current.start_minute < previous.end_minute:
            raise ValueError("workbench active windows cannot overlap")
    return windows


def _canonical_absolute_windows(
    value: tuple[SpatialActiveWindow, ...],
) -> tuple[SpatialActiveWindow, ...]:
    windows = tuple(sorted(value, key=lambda item: (item.start_minute, item.end_minute)))
    for previous, current in pairwise(windows):
        if current.start_minute < previous.end_minute:
            raise ValueError("workbench active windows cannot overlap")
    return windows


class WorkbenchCampaignDraft(DomainModel):
    campaign_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    creative_template_id: str = Field(pattern=_ID_PATTERN)
    target_interests: frozenset[PublicText] = Field(min_length=1, max_length=12)
    relative_price: float = Field(gt=0, le=100)

    @field_validator("campaign_id", "creative_template_id")
    @classmethod
    def safe_identifiers(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        return _safe_public_text(value)

    @field_validator("target_interests", mode="before")
    @classmethod
    def unique_target_interests(cls, value: object) -> object:
        return _unique_text_members(value, label="campaign target interests")

    @field_validator("target_interests")
    @classmethod
    def safe_target_interests(cls, value: frozenset[str]) -> frozenset[str]:
        return _safe_interests(value, label="campaign target interests")

    @field_serializer("target_interests", when_used="json")
    def serialize_target_interests(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class _WorkbenchPlacementDraft(DomainModel):
    active_windows: tuple[WorkbenchDailyWindow, ...] = Field(min_length=1, max_length=14)
    frequency_cap_per_agent_per_day: int = Field(ge=1, le=100)

    @field_validator("active_windows")
    @classmethod
    def canonical_windows(
        cls, value: tuple[WorkbenchDailyWindow, ...]
    ) -> tuple[WorkbenchDailyWindow, ...]:
        return _canonical_daily_windows(value)


class WorkbenchPhonePlacementDraft(_WorkbenchPlacementDraft):
    eligible_activities: tuple[SpatialActivity, ...] = Field(min_length=1, max_length=4)
    opportunity_probability_per_minute: float = Field(ge=0, le=1)

    @field_validator("eligible_activities", mode="before")
    @classmethod
    def unique_activities(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            items = tuple(value)
            if not all(isinstance(item, str) for item in items):
                return value
            if len(items) != len(set(items)):
                raise ValueError("eligible phone activities must be unique")
            return items
        return value

    @field_validator("eligible_activities")
    @classmethod
    def canonical_activities(
        cls, value: tuple[SpatialActivity, ...]
    ) -> tuple[SpatialActivity, ...]:
        return tuple(sorted(value, key=_ACTIVITY_ORDER.__getitem__))


class WorkbenchRoadsidePlacementDraft(_WorkbenchPlacementDraft):
    road_id: str = Field(pattern=_ID_PATTERN)
    travel_direction: TravelDirection
    road_fraction: float = Field(gt=0, lt=1)
    side: BillboardSide
    orientation_degrees: float = Field(ge=0, lt=360)
    max_view_distance_meters: float = Field(gt=0, le=1_000)

    @field_validator("road_id")
    @classmethod
    def safe_road_id(cls, value: str) -> str:
        return _safe_identifier(value)


class WorkbenchScenarioDraft(DomainModel):
    scenario_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    campaign: WorkbenchCampaignDraft
    phone: WorkbenchPhonePlacementDraft | None = None
    roadside: WorkbenchRoadsidePlacementDraft | None = None

    @field_validator("scenario_id")
    @classmethod
    def safe_scenario_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        return _safe_public_text(value)

    @model_validator(mode="after")
    def at_least_one_placement(self) -> Self:
        if self.phone is None and self.roadside is None:
            raise ValueError("workbench scenario requires at least one placement")
        return self


class WorkbenchInitialState(DomainModel):
    brand_sentiment: float = Field(ge=-1, le=1)
    recall_strength: float = Field(ge=0, le=1)
    purchase_intention: float = Field(ge=0, le=1)


class WorkbenchCohortDraft(DomainModel):
    interests: frozenset[PublicText] = Field(min_length=1, max_length=12)
    traits: SpatialResponseTraits
    initial_state: WorkbenchInitialState

    @field_validator("interests", mode="before")
    @classmethod
    def unique_interests(cls, value: object) -> object:
        return _unique_text_members(value, label="cohort interests")

    @field_validator("interests")
    @classmethod
    def safe_interests(cls, value: frozenset[str]) -> frozenset[str]:
        return _safe_interests(value, label="cohort interests")

    @field_serializer("interests", when_used="json")
    def serialize_interests(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class WorkbenchRunSettings(DomainModel):
    run_id: str
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    seed: int = Field(ge=0, le=2**63 - 1)
    response_mode: Literal["deterministic-rules"] = "deterministic-rules"

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        return _safe_identifier(validate_portable_run_identifier(value))


class WorkbenchRunDraft(DomainModel):
    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: WorkbenchScenarioDraft
    cohort: WorkbenchCohortDraft
    settings: WorkbenchRunSettings

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)


class _NormalizedPlacementDraft(DomainModel):
    active_windows: tuple[SpatialActiveWindow, ...] = Field(min_length=1, max_length=14)
    frequency_cap_per_agent_per_day: int = Field(ge=1, le=100)

    @field_validator("active_windows")
    @classmethod
    def canonical_windows(
        cls, value: tuple[SpatialActiveWindow, ...]
    ) -> tuple[SpatialActiveWindow, ...]:
        return _canonical_absolute_windows(value)


class NormalizedPhonePlacementDraft(_NormalizedPlacementDraft):
    placement_id: Literal["phone-placement"] = "phone-placement"
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


class NormalizedRoadsidePlacementDraft(_NormalizedPlacementDraft):
    placement_id: Literal["roadside-placement"] = "roadside-placement"
    road_id: str = Field(pattern=_ID_PATTERN)
    travel_direction: TravelDirection
    road_fraction: float = Field(gt=0, lt=1)
    side: BillboardSide
    orientation_degrees: float = Field(ge=0, lt=360)
    max_view_distance_meters: float = Field(gt=0, le=1_000)

    @field_validator("road_id")
    @classmethod
    def safe_road_id(cls, value: str) -> str:
        return _safe_identifier(value)


class NormalizedWorkbenchScenarioDraft(DomainModel):
    scenario_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    campaign: WorkbenchCampaignDraft
    phone: NormalizedPhonePlacementDraft | None = None
    roadside: NormalizedRoadsidePlacementDraft | None = None

    @field_validator("scenario_id")
    @classmethod
    def safe_scenario_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        return _safe_public_text(value)

    @model_validator(mode="after")
    def at_least_one_placement(self) -> Self:
        if self.phone is None and self.roadside is None:
            raise ValueError("workbench scenario requires at least one placement")
        return self


class NormalizedWorkbenchRunDraft(DomainModel):
    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=_ID_PATTERN)
    scenario: NormalizedWorkbenchScenarioDraft
    cohort: WorkbenchCohortDraft
    settings: WorkbenchRunSettings

    @field_validator("city_id")
    @classmethod
    def safe_city_id(cls, value: str) -> str:
        return _safe_identifier(value)


class CreativeTemplate(DomainModel):
    template_id: str = Field(pattern=_ID_PATTERN)
    template_version: Literal[1] = 1
    product_name: str = Field(min_length=1, max_length=80)
    product_category: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=240)
    call_to_action: str = Field(min_length=1, max_length=120)
    disclosure: Literal["Fictional creative for synthetic simulation only."] = CREATIVE_DISCLOSURE

    @field_validator("template_id")
    @classmethod
    def safe_template_id(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("template_version", mode="before")
    @classmethod
    def exact_template_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("creative template_version must be integer 1")
        return value

    @field_validator(
        "product_name",
        "product_category",
        "message",
        "call_to_action",
        "disclosure",
    )
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_public_text(value)

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


class CreativeTemplateCatalog(DomainModel):
    schema_version: Literal[1] = 1
    templates: tuple[CreativeTemplate, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_sorted_templates(self) -> Self:
        templates = tuple(sorted(self.templates, key=lambda item: item.template_id))
        identifiers = tuple(item.template_id for item in templates)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate creative template identifier")
        object.__setattr__(self, "templates", templates)
        return self


class WorkbenchRunInput(DomainModel):
    schema_version: Literal[1] = 1
    draft: NormalizedWorkbenchRunDraft
    creative_template: CreativeTemplate

    @model_validator(mode="after")
    def creative_matches_draft(self) -> Self:
        if self.creative_template.template_id != self.draft.scenario.campaign.creative_template_id:
            raise ValueError("creative template does not match workbench campaign")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


__all__ = [
    "CREATIVE_DISCLOSURE",
    "CreativeTemplate",
    "CreativeTemplateCatalog",
    "NormalizedPhonePlacementDraft",
    "NormalizedRoadsidePlacementDraft",
    "NormalizedWorkbenchRunDraft",
    "NormalizedWorkbenchScenarioDraft",
    "WorkbenchCampaignDraft",
    "WorkbenchCohortDraft",
    "WorkbenchDailyWindow",
    "WorkbenchInitialState",
    "WorkbenchPhonePlacementDraft",
    "WorkbenchRoadsidePlacementDraft",
    "WorkbenchRunDraft",
    "WorkbenchRunInput",
    "WorkbenchRunSettings",
    "WorkbenchScenarioDraft",
]
