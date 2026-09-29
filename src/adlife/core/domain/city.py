"""Validated, immutable street graph for the opt-in geographic pilot."""

from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Any, Literal, Self, TypeAlias
from unicodedata import bidirectional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, HttpUrl, field_validator, model_validator

from adlife.core.domain.person import DomainModel, contains_secret_or_email_text
from adlife.core.domain.serialization import canonical_json

RoadKind = Literal[
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "residential",
    "service",
    "path",
]
TravelDirection = Literal["forward", "backward"]
CityOmission = Annotated[str, Field(min_length=1, max_length=200)]

_BIDI_CONTROLS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"})


def _validate_public_metadata(value: str) -> str:
    if not value.strip():
        raise ValueError("city metadata cannot be blank")
    if any(
        ord(character) < 32
        or 127 <= ord(character) < 160
        or bidirectional(character) in _BIDI_CONTROLS
        for character in value
    ):
        raise ValueError("city metadata contains a display control character")
    if contains_secret_or_email_text(value):
        raise ValueError("city metadata contains a credential or private identifier")
    return value


def _validate_public_url(value: HttpUrl) -> HttpUrl:
    if value.username or value.password or value.query or value.fragment:
        raise ValueError("source_url cannot contain credentials, query, or fragment")
    if contains_secret_or_email_text(str(value)):
        raise ValueError("source_url contains a credential or private identifier")
    return value


class CityNode(DomainModel):
    node_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class CityRoad(DomainModel):
    road_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    source_node: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    target_node: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    kind: RoadKind
    one_way: bool = False

    @model_validator(mode="after")
    def distinct_endpoints(self) -> Self:
        if self.source_node == self.target_node:
            raise ValueError("a road must connect distinct nodes")
        return self


class CityPack(DomainModel):
    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=120)
    source_url: HttpUrl
    license: str = Field(min_length=1, max_length=80)
    attribution: str = Field(min_length=1, max_length=240)
    nodes: tuple[CityNode, ...] = Field(min_length=2, max_length=10_000)
    roads: tuple[CityRoad, ...] = Field(min_length=1, max_length=20_000)

    @field_validator("name", "license", "attribution")
    @classmethod
    def public_metadata_only(cls, value: str) -> str:
        return _validate_public_metadata(value)

    @field_validator("source_url")
    @classmethod
    def public_source_url(cls, value: HttpUrl) -> HttpUrl:
        return _validate_public_url(value)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        nodes = tuple(sorted(self.nodes, key=lambda node: node.node_id))
        roads = tuple(sorted(self.roads, key=lambda road: road.road_id))
        node_ids = tuple(node.node_id for node in nodes)
        road_ids = tuple(road.road_id for road in roads)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate city node identifier")
        if len(road_ids) != len(set(road_ids)):
            raise ValueError("duplicate city road identifier")
        known_nodes = set(node_ids)
        coordinates = {node.node_id: (node.longitude, node.latitude) for node in nodes}
        outgoing: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        incoming: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        for road in roads:
            if road.source_node not in known_nodes or road.target_node not in known_nodes:
                raise ValueError("unknown road node")
            if coordinates[road.source_node] == coordinates[road.target_node]:
                raise ValueError("road endpoints must have distinct coordinates")
            if abs(coordinates[road.source_node][0] - coordinates[road.target_node][0]) > 180:
                raise ValueError("roads crossing the date line are not supported by this map")
            outgoing[road.source_node].add(road.target_node)
            incoming[road.target_node].add(road.source_node)
            if not road.one_way:
                outgoing[road.target_node].add(road.source_node)
                incoming[road.source_node].add(road.target_node)
        if not _reaches_all(node_ids[0], outgoing) or not _reaches_all(node_ids[0], incoming):
            raise ValueError("city roads must form a strongly connected directed graph")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "roads", roads)
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


class CityCoordinate(DomainModel):
    """One bounded WGS84 point used only as intermediate road geometry."""

    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85, le=85)


class CityBounds(DomainModel):
    """Exact non-wrapping bounds for every node and intermediate shape point."""

    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-85, le=85)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85, le=85)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("city bounds must have positive non-wrapping area")
        return self


class CitySource(DomainModel):
    """Public, non-secret provenance frozen into a v2 city pack."""

    provider: str = Field(min_length=1, max_length=120)
    dataset: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    published_on: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_url: HttpUrl
    license: str = Field(min_length=1, max_length=80)
    attribution: str = Field(min_length=1, max_length=240)

    @field_validator("provider", "dataset", "version", "license", "attribution")
    @classmethod
    def public_metadata_only(cls, value: str) -> str:
        return _validate_public_metadata(value)

    @field_validator("published_on")
    @classmethod
    def real_source_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("source published_on must be a calendar date") from None
        return value

    @field_validator("source_url")
    @classmethod
    def public_source_url(cls, value: HttpUrl) -> HttpUrl:
        return _validate_public_url(value)


class CityRoadV2(DomainModel):
    """Stable physical road identity with explicit traversal directions and shape."""

    road_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    source_node: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    target_node: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    kind: RoadKind
    directions: tuple[TravelDirection, ...] = Field(min_length=1, max_length=2)
    shape: tuple[CityCoordinate, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def canonical_road(self) -> Self:
        if self.source_node == self.target_node:
            raise ValueError("a road must connect distinct nodes")
        if len(set(self.directions)) != len(self.directions):
            raise ValueError("road directions must be unique")
        directions = tuple(
            direction for direction in ("forward", "backward") if direction in self.directions
        )
        object.__setattr__(self, "directions", directions)
        return self


class CityPackV2(DomainModel):
    """Geometry- and provenance-preserving city graph; v1 remains a separate contract."""

    schema_version: Literal[2] = 2
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=120)
    time_zone: str = Field(min_length=1, max_length=64)
    source: CitySource
    bounds: CityBounds
    known_omissions: tuple[CityOmission, ...] = Field(min_length=1, max_length=16)
    nodes: tuple[CityNode, ...] = Field(min_length=2, max_length=10_000)
    roads: tuple[CityRoadV2, ...] = Field(min_length=1, max_length=20_000)

    @field_validator("name")
    @classmethod
    def public_name(cls, value: str) -> str:
        return _validate_public_metadata(value)

    @field_validator("known_omissions")
    @classmethod
    def canonical_omissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_validate_public_metadata(item) for item in value)
        if len(set(checked)) != len(checked):
            raise ValueError("known omissions must be unique")
        return tuple(sorted(checked))

    @field_validator("time_zone")
    @classmethod
    def known_time_zone(cls, value: str) -> str:
        if not value.isascii() or any(character.isspace() for character in value):
            raise ValueError("city time zone must be an IANA identifier")
        try:
            zone = ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("city time zone must be an installed IANA identifier") from None
        if zone.key != value:
            raise ValueError("city time zone must be an IANA identifier")
        return value

    @model_validator(mode="after")
    def validate_graph_and_geometry(self) -> Self:
        nodes = tuple(sorted(self.nodes, key=lambda node: node.node_id))
        roads = tuple(sorted(self.roads, key=lambda road: road.road_id))
        node_ids = tuple(node.node_id for node in nodes)
        road_ids = tuple(road.road_id for road in roads)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate city node identifier")
        if len(road_ids) != len(set(road_ids)):
            raise ValueError("duplicate city road identifier")
        if any(abs(node.latitude) > 85 for node in nodes):
            raise ValueError("city node latitude is outside supported v2 map bounds")

        known_nodes = set(node_ids)
        coordinates = {node.node_id: (node.longitude, node.latitude) for node in nodes}
        outgoing: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        incoming: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        all_coordinates = list(coordinates.values())
        shape_point_count = 0
        for road in roads:
            if road.source_node not in known_nodes or road.target_node not in known_nodes:
                raise ValueError("unknown road node")
            shape_point_count += len(road.shape)
            if shape_point_count > 100_000:
                raise ValueError("city roads contain too many intermediate shape points")
            line = (
                coordinates[road.source_node],
                *((point.longitude, point.latitude) for point in road.shape),
                coordinates[road.target_node],
            )
            for start, end in pairwise(line):
                if start == end:
                    raise ValueError("road geometry must contain distinct consecutive coordinates")
                if abs(start[0] - end[0]) > 180:
                    raise ValueError("roads crossing the date line are not supported by this map")
            all_coordinates.extend(line[1:-1])
            if "forward" in road.directions:
                outgoing[road.source_node].add(road.target_node)
                incoming[road.target_node].add(road.source_node)
            if "backward" in road.directions:
                outgoing[road.target_node].add(road.source_node)
                incoming[road.source_node].add(road.target_node)

        expected_bounds = (
            min(point[0] for point in all_coordinates),
            min(point[1] for point in all_coordinates),
            max(point[0] for point in all_coordinates),
            max(point[1] for point in all_coordinates),
        )
        actual_bounds = (self.bounds.west, self.bounds.south, self.bounds.east, self.bounds.north)
        if actual_bounds != expected_bounds:
            raise ValueError("city bounds must exactly enclose nodes and road geometry")
        if not _reaches_all(node_ids[0], outgoing) or not _reaches_all(node_ids[0], incoming):
            raise ValueError("city roads must form a strongly connected directed graph")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "roads", roads)
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


CityPackDocument: TypeAlias = CityPack | CityPackV2


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("city pack JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"city pack JSON contains non-finite constant {value}")


def parse_city_pack_json(document: str | bytes) -> CityPackDocument:
    """Dispatch one strict JSON document without coercing its version token."""
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("city pack is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("city pack must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int:
        raise ValueError("city pack schema_version must be an integer")
    normalized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if version == 1:
        return CityPack.model_validate_json(normalized)
    if version == 2:
        return CityPackV2.model_validate_json(normalized)
    raise ValueError("unsupported city pack schema_version")


def _reaches_all(start: str, adjacency: dict[str, set[str]]) -> bool:
    visited: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency[current] - visited)
    return len(visited) == len(adjacency)


__all__ = [
    "CityBounds",
    "CityCoordinate",
    "CityNode",
    "CityPack",
    "CityPackDocument",
    "CityPackV2",
    "CityRoad",
    "CityRoadV2",
    "CitySource",
    "RoadKind",
    "TravelDirection",
    "parse_city_pack_json",
]
