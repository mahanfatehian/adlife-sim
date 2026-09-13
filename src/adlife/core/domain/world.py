from typing import Literal, Self

from pydantic import Field, model_validator

from adlife.core.domain.person import DomainModel
from adlife.core.domain.state import Activity


class Zone(DomainModel):
    schema_version: Literal[1] = 1
    zone_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["home", "education", "work", "retail", "social", "highway", "online"]


class Route(DomainModel):
    schema_version: Literal[1] = 1
    route_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    source_zone: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    target_zone: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    transit_zone: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,79}$",
    )
    travel_minutes: int = Field(ge=15, le=240, multiple_of=15)


class RoutineBlock(DomainModel):
    schema_version: Literal[1] = 1
    template_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    day_type: Literal["weekday", "weekend"]
    start_minute_of_day: int = Field(ge=0, lt=1440)
    end_minute_of_day: int = Field(gt=0, le=1440)
    activity: Activity
    zone_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    route_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,79}$",
    )

    @model_validator(mode="after")
    def require_forward_window(self) -> Self:
        if self.end_minute_of_day <= self.start_minute_of_day:
            raise ValueError("end_minute_of_day must be after start_minute_of_day")
        return self


class Relationship(DomainModel):
    schema_version: Literal[1] = 1
    source_id: str = Field(pattern=r"^person-[0-9]{3}$")
    target_id: str = Field(pattern=r"^person-[0-9]{3}$")
    kind: Literal["friend", "colleague", "family", "online"]
    strength: float = Field(ge=0, le=1)


class World(DomainModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    zones: tuple[Zone, ...] = Field(min_length=1, max_length=50)
    routes: tuple[Route, ...] = Field(default=(), max_length=100)


__all__ = ["Relationship", "Route", "RoutineBlock", "World", "Zone"]
