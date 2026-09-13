from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Literal, Self, cast

import yaml
from pydantic import Field, model_validator

from adlife.core.domain.person import DomainModel, PersonProfile
from adlife.core.domain.world import RoutineBlock
from adlife.core.simulation.rng import RandomOracle

DayType = Literal["weekday", "weekend"]

_EXPECTED_TEMPLATE_IDS = frozenset(
    {"student", "office-worker", "retail-worker", "freelancer", "unemployed"}
)
_JITTER_ACTIVITIES = frozenset({"commute", "leisure"})
_JITTER_MINUTES = (-30, -15, 0, 15, 30)


class RoutineTemplate(DomainModel):
    schema_version: Literal[1] = 1
    template_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    weekday: tuple[RoutineBlock, ...] = Field(min_length=1)
    weekend: tuple[RoutineBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_complete_days(self) -> Self:
        self._validate_day("weekday", self.weekday)
        self._validate_day("weekend", self.weekend)
        return self

    def _validate_day(self, day_type: DayType, blocks: tuple[RoutineBlock, ...]) -> None:
        expected_start = 0
        for block in blocks:
            if block.template_id != self.template_id:
                raise ValueError("routine block template_id does not match its template")
            if block.day_type != day_type:
                raise ValueError("routine block day_type does not match its schedule")
            if block.start_minute_of_day > expected_start:
                raise ValueError(
                    f"{day_type} routine has a gap before minute {block.start_minute_of_day}"
                )
            if block.start_minute_of_day < expected_start:
                raise ValueError(
                    f"{day_type} routine has an overlap at minute {block.start_minute_of_day}"
                )
            expected_start = block.end_minute_of_day
        if expected_start < 1440:
            raise ValueError(f"{day_type} routine has a gap after minute {expected_start}")


def _mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be a mapping with string keys")
    return cast(Mapping[str, object], value)


def _require_keys(
    value: Mapping[str, object],
    expected: set[str],
    location: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{location} has invalid keys; missing={missing}, extra={extra}")


def _parse_day(
    template_id: str,
    day_type: DayType,
    value: object,
) -> tuple[RoutineBlock, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{template_id}.{day_type} must be a non-empty list")

    blocks: list[RoutineBlock] = []
    for index, item in enumerate(value):
        block = _mapping(item, f"{template_id}.{day_type}[{index}]")
        required = {"start", "end", "activity", "zone_id"}
        allowed = required | {"route_id"}
        if not required <= set(block) or not set(block) <= allowed:
            missing = sorted(required - set(block))
            extra = sorted(set(block) - allowed)
            raise ValueError(
                f"{template_id}.{day_type}[{index}] has invalid keys; "
                f"missing={missing}, extra={extra}"
            )
        blocks.append(
            RoutineBlock.model_validate(
                {
                    "template_id": template_id,
                    "day_type": day_type,
                    "start_minute_of_day": block["start"],
                    "end_minute_of_day": block["end"],
                    "activity": block["activity"],
                    "zone_id": block["zone_id"],
                    "route_id": block.get("route_id"),
                }
            )
        )
    return tuple(blocks)


@lru_cache(maxsize=1)
def load_routine_templates() -> Mapping[str, RoutineTemplate]:
    """Load and validate the built-in routine YAML through package resources."""
    resource = files("adlife.resources.routines").joinpath("default.yaml")
    root = _mapping(yaml.safe_load(resource.read_text(encoding="utf-8")), "routine resource")
    _require_keys(root, {"schema_version", "templates"}, "routine resource")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ValueError("routine resource schema_version must be 1")

    template_values = _mapping(root["templates"], "routine resource templates")
    if set(template_values) != _EXPECTED_TEMPLATE_IDS:
        missing = sorted(_EXPECTED_TEMPLATE_IDS - set(template_values))
        extra = sorted(set(template_values) - _EXPECTED_TEMPLATE_IDS)
        raise ValueError(f"routine resource templates differ; missing={missing}, extra={extra}")

    templates: dict[str, RoutineTemplate] = {}
    for template_id in sorted(template_values):
        value = _mapping(template_values[template_id], f"template {template_id}")
        _require_keys(value, {"weekday", "weekend"}, f"template {template_id}")
        templates[template_id] = RoutineTemplate(
            template_id=template_id,
            weekday=_parse_day(template_id, "weekday", value["weekday"]),
            weekend=_parse_day(template_id, "weekend", value["weekend"]),
        )
    return MappingProxyType(templates)


def _commute_route(profile: PersonProfile) -> str:
    return "highway-north" if profile.home_zone == "home-north" else "highway-center"


def _resolve_zone(block: RoutineBlock, profile: PersonProfile, commute_route: str) -> str:
    if block.zone_id == "home":
        return profile.home_zone
    if block.zone_id == "work-or-study":
        return profile.work_or_study_zone or profile.home_zone
    if block.zone_id == "commute":
        return commute_route
    return block.zone_id


def _jittered_blocks(
    blocks: tuple[RoutineBlock, ...],
    profile: PersonProfile,
    day_index: int,
    day_type: DayType,
    oracle: RandomOracle,
) -> tuple[RoutineBlock, ...]:
    boundaries = [blocks[0].start_minute_of_day, *(block.end_minute_of_day for block in blocks)]
    for index, block in enumerate(blocks[:-1]):
        if block.activity not in _JITTER_ACTIVITIES:
            continue
        draw = oracle.uniform(
            f"routine:jitter:{day_type}:{block.activity}:{index}",
            profile.agent_id,
            day_index,
            0,
        )
        jitter = _JITTER_MINUTES[min(int(draw * len(_JITTER_MINUTES)), len(_JITTER_MINUTES) - 1)]
        boundary_index = index + 1
        proposed = boundaries[boundary_index] + jitter
        boundaries[boundary_index] = max(
            boundaries[boundary_index - 1] + 15,
            min(boundaries[boundary_index + 1] - 15, proposed),
        )

    commute_route = _commute_route(profile)
    return tuple(
        RoutineBlock(
            template_id=block.template_id,
            day_type=day_type,
            start_minute_of_day=boundaries[index],
            end_minute_of_day=boundaries[index + 1],
            activity=block.activity,
            zone_id=_resolve_zone(block, profile, commute_route),
            route_id=commute_route if block.route_id == "commute" else block.route_id,
        )
        for index, block in enumerate(blocks)
    )


def routine_at(
    profile: PersonProfile,
    simulated_minute: int,
    oracle: RandomOracle,
) -> RoutineBlock:
    """Return the profile-specific routine block active at a simulated minute."""
    if isinstance(simulated_minute, bool) or not isinstance(simulated_minute, int):
        raise ValueError("simulated_minute must be nonnegative")
    if simulated_minute < 0:
        raise ValueError("simulated_minute must be nonnegative")

    templates = load_routine_templates()
    try:
        template = templates[profile.routine_template]
    except KeyError as error:
        raise ValueError(f"unknown routine template: {profile.routine_template}") from error

    day_index = simulated_minute // 1440
    day_type: DayType = "weekend" if day_index % 7 in {5, 6} else "weekday"
    base_blocks = template.weekend if day_type == "weekend" else template.weekday
    blocks = _jittered_blocks(base_blocks, profile, day_index, day_type, oracle)
    minute_of_day = simulated_minute % 1440
    for block in blocks:
        if block.start_minute_of_day <= minute_of_day < block.end_minute_of_day:
            return block
    raise RuntimeError("validated routine did not cover the requested minute")


__all__ = ["RoutineTemplate", "load_routine_templates", "routine_at"]
