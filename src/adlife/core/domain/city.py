"""Validated, immutable street graph for the opt-in geographic pilot."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal, Self
from unicodedata import bidirectional

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

_BIDI_CONTROLS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"})


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

    @field_validator("source_url")
    @classmethod
    def public_source_url(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("source_url cannot contain credentials, query, or fragment")
        if contains_secret_or_email_text(str(value)):
            raise ValueError("source_url contains a credential or private identifier")
        return value

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


__all__ = ["CityNode", "CityPack", "CityRoad", "RoadKind"]
