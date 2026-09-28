"""Deterministic conversion of a bounded, locally supplied Overpass JSON extract."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from pydantic import HttpUrl, ValidationError

from adlife.core.domain.city import CityNode, CityPack, CityRoad, RoadKind
from adlife.core.domain.serialization import canonical_json

MAX_OSM_INPUT_BYTES = 16_777_216
MAX_OSM_NODE_ELEMENTS = 50_000
MAX_OSM_ROAD_SEGMENTS = 20_000
_MAX_PACK_BYTES = 4_194_304
_ROAD_CLASSES: dict[str, RoadKind] = {
    "motorway": "motorway",
    "trunk": "trunk",
    "primary": "primary",
    "secondary": "secondary",
    "tertiary": "tertiary",
    "residential": "residential",
    "service": "service",
}
_ROAD_CLASSES.update({f"{kind}_link": value for kind, value in tuple(_ROAD_CLASSES.items())})
_ALLOWED_CAR_ACCESS = {"yes", "designated", "permissive"}


class OSMImportError(ValueError):
    """An extract cannot be converted; messages never include source contents."""


@dataclass(frozen=True, slots=True)
class OSMImportResult:
    pack: CityPack
    dropped_nodes: int = 0
    dropped_roads: int = 0


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OSMImportError("OSM JSON contains a duplicate object key")
        result[key] = value
    return result


def _nonstandard_constant(value: str) -> None:
    raise OSMImportError("OSM JSON contains a non-finite value")


def _positive_id(value: object) -> int:
    if type(value) is not int or not 0 < value <= 9_223_372_036_854_775_807:
        raise OSMImportError("OSM element has an invalid identifier")
    return value


def _coordinate(value: object, *, latitude: bool) -> float:
    limit = 90 if latitude else 180
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OSMImportError("OSM node has an invalid coordinate")
    if not -limit <= value <= limit or not math.isfinite(value):
        raise OSMImportError("OSM node has an invalid coordinate")
    return float(value)


def _direction(tags: dict[str, str], highway: str) -> tuple[bool, bool]:
    restrictions = ("oneway", "access", "vehicle", "motor_vehicle", "motorcar")
    if any(key.endswith(":conditional") for key in tags if key.startswith(restrictions)):
        raise OSMImportError("OSM road has a conditional restriction this importer cannot model")
    if any(
        f"{mode}:{direction}" in tags
        for mode in ("access", "vehicle", "motor_vehicle", "motorcar")
        for direction in ("forward", "backward")
    ):
        raise OSMImportError("OSM road has directional access this importer cannot model")
    value = next(
        (
            tags[key]
            for key in ("oneway:motorcar", "oneway:motor_vehicle", "oneway:vehicle", "oneway")
            if key in tags
        ),
        None,
    )
    if value is None:
        return highway == "motorway" or tags.get("junction") == "roundabout", False
    if value in {"yes", "true", "1"}:
        return True, False
    if value == "-1":
        return True, True
    if value in {"no", "false", "0"}:
        return False, False
    raise OSMImportError("OSM road has an unsupported one-way direction")


def _allows_car(tags: dict[str, str]) -> bool:
    for key in ("motorcar", "motor_vehicle", "vehicle", "access"):
        value = tags.get(key)
        if value is not None:
            return value in _ALLOWED_CAR_ACCESS
    return True


def _strong_components(roads: list[CityRoad], node_ids: set[str]) -> list[set[str]]:
    outgoing: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    incoming: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for road in roads:
        outgoing[road.source_node].add(road.target_node)
        incoming[road.target_node].add(road.source_node)
        if not road.one_way:
            outgoing[road.target_node].add(road.source_node)
            incoming[road.source_node].add(road.target_node)

    visited: set[str] = set()
    finish_order: list[str] = []
    for start in sorted(node_ids):
        if start in visited:
            continue
        visited.add(start)
        pending = [(start, iter(sorted(outgoing[start])))]
        while pending:
            current, neighbors = pending[-1]
            neighbor = next(neighbors, None)
            if neighbor is None:
                finish_order.append(current)
                pending.pop()
            elif neighbor not in visited:
                visited.add(neighbor)
                pending.append((neighbor, iter(sorted(outgoing[neighbor]))))

    components: list[set[str]] = []
    visited.clear()
    for start in reversed(finish_order):
        if start in visited:
            continue
        component: set[str] = set()
        pending_nodes = [start]
        visited.add(start)
        while pending_nodes:
            current = pending_nodes.pop()
            component.add(current)
            for neighbor in sorted(incoming[current]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending_nodes.append(neighbor)
        components.append(component)
    return components


def convert_overpass_json(
    data: bytes, *, city_id: str, name: str, largest_component: bool = False
) -> OSMImportResult:
    """Convert complete local Overpass geometry into the existing city-pack schema."""
    if len(data) > MAX_OSM_INPUT_BYTES:
        raise OSMImportError("OSM extract is too large")
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_nonstandard_constant,
        )
    except OSMImportError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise OSMImportError("OSM input must be UTF-8 JSON") from None
    if not isinstance(document, dict) or not isinstance(document.get("elements"), list):
        raise OSMImportError("OSM JSON needs an elements array")
    if "remark" in document:
        raise OSMImportError("OSM response reports an incomplete or failed query")
    nodes: dict[int, CityNode] = {}
    ways: dict[int, dict[str, Any]] = {}
    for element in document["elements"]:
        if not isinstance(element, dict):
            raise OSMImportError("OSM element must be an object")
        kind = element.get("type")
        if not isinstance(kind, str):
            raise OSMImportError("OSM element has an invalid type")
        if kind not in {"node", "way"}:
            continue
        identifier = _positive_id(element.get("id"))
        if kind == "node":
            if identifier in nodes:
                raise OSMImportError("OSM extract has a duplicate node identifier")
            if len(nodes) >= MAX_OSM_NODE_ELEMENTS:
                raise OSMImportError("OSM extract has too many node elements")
            latitude = _coordinate(element.get("lat"), latitude=True)
            longitude = _coordinate(element.get("lon"), latitude=False)
            nodes[identifier] = CityNode(
                node_id=f"osm-node-{identifier}", latitude=latitude, longitude=longitude
            )
        else:
            if identifier in ways:
                raise OSMImportError("OSM extract has a duplicate way identifier")
            ways[identifier] = element
    roads: list[CityRoad] = []
    used_nodes: set[int] = set()
    for way_id, way in sorted(ways.items()):
        members = way.get("nodes")
        if not isinstance(members, list) or len(members) < 2:
            raise OSMImportError("OSM road needs at least two member nodes")
        node_ids = [_positive_id(member) for member in members]
        if any(member not in nodes for member in node_ids):
            raise OSMImportError("OSM road references a missing node")
        tags = way.get("tags", {})
        if not isinstance(tags, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in tags.items()
        ):
            raise OSMImportError("OSM way has invalid tags")
        highway = tags.get("highway")
        if highway not in _ROAD_CLASSES:
            continue
        if not _allows_car(tags):
            continue
        if len(members) - 1 > MAX_OSM_ROAD_SEGMENTS - len(roads):
            raise OSMImportError("OSM extract has too many road segments")
        one_way, reverse = _direction(tags, highway)
        for index, (source, target) in enumerate(pairwise(node_ids)):
            if reverse:
                source, target = target, source
            try:
                road = CityRoad(
                    road_id=f"osm-way-{way_id}-{index}",
                    source_node=f"osm-node-{source}",
                    target_node=f"osm-node-{target}",
                    kind=_ROAD_CLASSES[highway],
                    one_way=one_way,
                )
            except ValidationError:
                raise OSMImportError("OSM road has invalid geometry") from None
            roads.append(road)
            used_nodes.update((source, target))
    if not roads:
        raise OSMImportError("OSM extract has no supported motor-vehicle roads")
    components = _strong_components(roads, {f"osm-node-{identifier}" for identifier in used_nodes})
    dropped_nodes = 0
    dropped_roads = 0
    if len(components) != 1:
        if not largest_component:
            raise OSMImportError(
                "OSM road graph is disconnected; use --largest-component explicitly"
            )
        selected = min(components, key=lambda component: (-len(component), min(component)))
        if len(selected) < 2:
            raise OSMImportError("OSM extract has no connected road component")
        retained_roads = [
            road for road in roads if road.source_node in selected and road.target_node in selected
        ]
        if not retained_roads:
            raise OSMImportError("OSM extract has no connected road component")
        dropped_nodes = len(used_nodes) - len(selected)
        dropped_roads = len(roads) - len(retained_roads)
        roads = retained_roads
        used_nodes = {int(node_id.removeprefix("osm-node-")) for node_id in selected}
    try:
        pack = CityPack(
            city_id=city_id,
            name=name,
            source_url=HttpUrl("https://www.openstreetmap.org/copyright"),
            license="ODbL-1.0",
            attribution="© OpenStreetMap contributors",
            nodes=tuple(nodes[identifier] for identifier in sorted(used_nodes)),
            roads=tuple(roads),
        )
    except ValidationError:
        raise OSMImportError("converted OSM streets fail city-pack validation") from None
    if len((canonical_json(pack) + "\n").encode("utf-8")) > _MAX_PACK_BYTES:
        raise OSMImportError("converted city pack is too large")
    return OSMImportResult(pack, dropped_nodes, dropped_roads)


__all__ = [
    "MAX_OSM_INPUT_BYTES",
    "MAX_OSM_NODE_ELEMENTS",
    "MAX_OSM_ROAD_SEGMENTS",
    "OSMImportError",
    "OSMImportResult",
    "convert_overpass_json",
]
