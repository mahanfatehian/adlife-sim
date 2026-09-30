"""Deterministic, road-constrained mobility preview; no advertising decisions."""

from __future__ import annotations

import heapq
import itertools
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Literal

from adlife.core.domain.city import (
    CityCoordinate,
    CityNode,
    CityPack,
    CityPackDocument,
    CityPackV2,
    RoadKind,
    TravelDirection,
)
from adlife.core.domain.city_places import CityPlace, CityPlaceSet
from adlife.core.simulation.rng import RandomOracle

Activity = Literal["home", "commute", "work", "leisure"]

# Illustrative free-flow speeds, not observed traffic or a calibrated transport model.
METERS_PER_MINUTE: dict[RoadKind, float] = {
    "motorway": 1_000.0,
    "trunk": 800.0,
    "primary": 600.0,
    "secondary": 500.0,
    "tertiary": 400.0,
    "residential": 300.0,
    "service": 200.0,
    "path": 80.0,
}


def _length_meters(start: CityNode | CityCoordinate, end: CityNode | CityCoordinate) -> float:
    lat1, lat2 = math.radians(start.latitude), math.radians(end.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(end.longitude - start.longitude)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 12_742_000.0 * math.asin(min(1.0, math.sqrt(value)))


@dataclass(frozen=True, slots=True)
class CityPath:
    node_ids: tuple[str, ...]
    road_ids: tuple[str, ...]
    duration_minutes: float
    distance_meters: float
    directions: tuple[TravelDirection, ...] = ()
    geometry: tuple[tuple[tuple[float, float], ...], ...] = ()


@dataclass(frozen=True, slots=True)
class _RoadEdge:
    target: str
    road_id: str
    duration: float
    distance: float
    direction: TravelDirection
    geometry: tuple[tuple[float, float], ...]


RoadGraph = dict[str, tuple[_RoadEdge, ...]]


@dataclass(frozen=True, slots=True)
class _RouteState:
    duration: float
    distance: float
    parent: str | None
    road_id: str | None
    direction: TravelDirection | None
    geometry: tuple[tuple[float, float], ...] | None
    version: int


def _coordinates(
    start: CityNode,
    shape: tuple[CityCoordinate, ...],
    end: CityNode,
) -> tuple[CityNode | CityCoordinate, ...]:
    return (start, *shape, end)


def _edge(
    *,
    target: str,
    road_id: str,
    kind: RoadKind,
    direction: TravelDirection,
    points: tuple[CityNode | CityCoordinate, ...],
) -> _RoadEdge:
    distance = sum(_length_meters(start, end) for start, end in pairwise(points))
    if distance <= 0:
        raise ValueError("road geometry must have positive length")
    geometry = tuple((point.longitude, point.latitude) for point in points)
    return _RoadEdge(
        target=target,
        road_id=road_id,
        duration=distance / METERS_PER_MINUTE[kind],
        distance=distance,
        direction=direction,
        geometry=geometry,
    )


def _reverse_edge(edge: _RoadEdge, *, target: str) -> _RoadEdge:
    return _RoadEdge(
        target=target,
        road_id=edge.road_id,
        duration=edge.duration,
        distance=edge.distance,
        direction="backward",
        geometry=tuple(reversed(edge.geometry)),
    )


def _build_road_graph(pack: CityPackDocument) -> RoadGraph:
    nodes = {node.node_id: node for node in pack.nodes}
    adjacency: dict[str, list[_RoadEdge]] = {node_id: [] for node_id in nodes}
    if isinstance(pack, CityPack):
        for road_v1 in pack.roads:
            points = _coordinates(nodes[road_v1.source_node], (), nodes[road_v1.target_node])
            forward = _edge(
                target=road_v1.target_node,
                road_id=road_v1.road_id,
                kind=road_v1.kind,
                direction="forward",
                points=points,
            )
            adjacency[road_v1.source_node].append(forward)
            if not road_v1.one_way:
                adjacency[road_v1.target_node].append(
                    _reverse_edge(forward, target=road_v1.source_node)
                )
    else:
        for road_v2 in pack.roads:
            points = _coordinates(
                nodes[road_v2.source_node], road_v2.shape, nodes[road_v2.target_node]
            )
            forward = _edge(
                target=road_v2.target_node,
                road_id=road_v2.road_id,
                kind=road_v2.kind,
                direction="forward",
                points=points,
            )
            if "forward" in road_v2.directions:
                adjacency[road_v2.source_node].append(forward)
            if "backward" in road_v2.directions:
                adjacency[road_v2.target_node].append(
                    _reverse_edge(forward, target=road_v2.source_node)
                )
    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge.road_id, edge.target, edge.direction))
    return {node_id: tuple(edges) for node_id, edges in adjacency.items()}


def _road_sequence(best: Mapping[str, _RouteState], node_id: str) -> tuple[str, ...]:
    sequence = []
    current = node_id
    while best[current].parent is not None:
        state = best[current]
        assert state.road_id is not None
        sequence.append(state.road_id)
        assert state.parent is not None
        current = state.parent
    return tuple(reversed(sequence))


def _shortest_path(graph: RoadGraph, start_id: str, end_id: str) -> CityPath:
    if start_id not in graph or end_id not in graph:
        raise ValueError("route endpoint is not a city node")
    if start_id == end_id:
        return CityPath((start_id,), (), 0.0, 0.0)
    # Positive edge times settle each predecessor before its child. Full identifier
    # sequences are reconstructed only on exact time ties, not copied on every edge.
    serial = itertools.count(1)
    frontier: list[tuple[float, int, str]] = [(0.0, 0, start_id)]
    best = {start_id: _RouteState(0.0, 0.0, None, None, None, None, 0)}
    while frontier:
        duration, version, current = heapq.heappop(frontier)
        state = best[current]
        if duration != state.duration or version != state.version:
            continue
        if current == end_id:
            node_ids = [current]
            road_ids = []
            directions: list[TravelDirection] = []
            geometry: list[tuple[tuple[float, float], ...]] = []
            while best[current].parent is not None:
                path_state = best[current]
                assert path_state.road_id is not None
                assert path_state.direction is not None
                assert path_state.geometry is not None
                assert path_state.parent is not None
                road_ids.append(path_state.road_id)
                directions.append(path_state.direction)
                geometry.append(path_state.geometry)
                current = path_state.parent
                node_ids.append(current)
            return CityPath(
                tuple(reversed(node_ids)),
                tuple(reversed(road_ids)),
                state.duration,
                state.distance,
                tuple(reversed(directions)),
                tuple(reversed(geometry)),
            )
        for edge in graph[current]:
            candidate_duration = duration + edge.duration
            previous = best.get(edge.target)
            if previous is not None:
                if candidate_duration > previous.duration:
                    continue
                if candidate_duration == previous.duration and (
                    (*_road_sequence(best, current), edge.road_id)
                    >= _road_sequence(best, edge.target)
                ):
                    continue
            next_version = next(serial)
            best[edge.target] = _RouteState(
                candidate_duration,
                state.distance + edge.distance,
                current,
                edge.road_id,
                edge.direction,
                edge.geometry,
                next_version,
            )
            heapq.heappush(frontier, (candidate_duration, next_version, edge.target))
    raise ValueError("no directed road path between city nodes")


def shortest_path(pack: CityPackDocument, start_id: str, end_id: str) -> CityPath:
    """Find a directed, minimum-travel-time path with canonical tie breaking."""
    if start_id not in {node.node_id for node in pack.nodes} or end_id not in {
        node.node_id for node in pack.nodes
    }:
        raise ValueError("route endpoint is not a city node")
    return _shortest_path(_build_road_graph(pack), start_id, end_id)


@dataclass(frozen=True, slots=True)
class CityAgent:
    agent_id: str
    home_node: str
    work_node: str
    leisure_node: str


@dataclass(frozen=True, slots=True)
class CityPlaceAssignment:
    """Auditable place IDs and route nodes selected for one synthetic agent."""

    agent_id: str
    home_place_id: str
    work_place_id: str
    leisure_place_id: str
    home_node: str
    work_node: str
    leisure_node: str


@dataclass(frozen=True, slots=True)
class CityPosition:
    agent_id: str
    minute: int
    longitude: float
    latitude: float
    activity: Activity
    road_id: str | None

    @property
    def coordinate_label(self) -> str:
        latitude_hemisphere = "N" if self.latitude >= 0 else "S"
        longitude_hemisphere = "E" if self.longitude >= 0 else "W"
        return (
            f"{abs(self.latitude):.5f}° {latitude_hemisphere} / "
            f"{abs(self.longitude):.5f}° {longitude_hemisphere}"
        )


@dataclass(frozen=True, slots=True)
class CityRoadTraversal:
    """One continuous directed road interval in an agent's immutable daily route."""

    agent_id: str
    day_index: int
    leg: Literal["outbound", "return"]
    road_sequence: int
    road_id: str
    travel_direction: TravelDirection
    start_minute: float
    end_minute: float
    distance_meters: float

    @property
    def duration_minutes(self) -> float:
        return self.end_minute - self.start_minute


class CityMobility:
    """Immutable configuration; every frame is computed from seed and simulated minute."""

    def __init__(
        self,
        pack: CityPackDocument,
        *,
        seed: int,
        agent_count: int,
        days: int,
        places: CityPlaceSet | None = None,
    ) -> None:
        if not isinstance(pack, (CityPack, CityPackV2)):
            raise TypeError("pack must be a supported CityPack")
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if type(agent_count) is not int or not 1 <= agent_count <= 250:
            raise ValueError("agent_count must be between 1 and 250")
        if type(days) is not int or not 1 <= days <= 31:
            raise ValueError("days must be between 1 and 31")
        if places is not None and not isinstance(places, CityPlaceSet):
            raise TypeError("places must be a CityPlaceSet")
        self.pack = pack
        self.seed = seed
        self.days = days
        self.places = places
        self._nodes = {node.node_id: node for node in pack.nodes}
        road_graph = _build_road_graph(pack)
        self._edges = {
            (source, edge.target, edge.road_id): edge
            for source, edges in road_graph.items()
            for edge in edges
        }
        oracle = RandomOracle(seed)
        node_ids = tuple(self._nodes)
        agents: list[CityAgent] = []
        place_assignments: list[CityPlaceAssignment] = []
        self._paths: dict[tuple[str, str], CityPath] = {}
        homes: list[CityPlace] = []
        workplaces: tuple[CityPlace, ...] = ()
        leisure_places: tuple[CityPlace, ...] = ()
        if places is not None:
            if places.city_id != pack.city_id:
                raise ValueError("city place set does not match the city identifier")
            if places.city_sha256 != pack.fingerprint:
                raise ValueError("city place set does not match the city fingerprint")
            if any(place.node_id not in self._nodes for place in places.places):
                raise ValueError("city place set references an unknown city node")
            homes = [place for place in places.places if place.kind == "home"]
            workplaces = tuple(place for place in places.places if place.kind == "workplace")
            leisure_places = tuple(place for place in places.places if place.kind == "leisure")
            if len(homes) < agent_count:
                raise ValueError("city place set requires one distinct home place per agent")
        for index in range(agent_count):
            agent_id = f"person-{index + 1:03d}"
            if places is None:
                home = _choose(node_ids, oracle, "city-home", agent_id, 0)
                other_nodes = tuple(node_id for node_id in node_ids if node_id != home)
                work = _choose(other_nodes, oracle, "city-work", agent_id, 0)
                leisure = _choose(other_nodes, oracle, "city-leisure", agent_id, 0)
            else:
                home_place = homes.pop(
                    _choice_index(len(homes), oracle, "city-place-home", agent_id)
                )
                available_workplaces = tuple(
                    place for place in workplaces if place.node_id != home_place.node_id
                )
                if not available_workplaces:
                    raise ValueError("each agent requires a workplace different from home")
                available_leisure = tuple(
                    place for place in leisure_places if place.node_id != home_place.node_id
                )
                if not available_leisure:
                    raise ValueError("each agent requires a leisure place different from home")
                work_place = available_workplaces[
                    _choice_index(len(available_workplaces), oracle, "city-place-work", agent_id)
                ]
                leisure_place = available_leisure[
                    _choice_index(len(available_leisure), oracle, "city-place-leisure", agent_id)
                ]
                home = home_place.node_id
                work = work_place.node_id
                leisure = leisure_place.node_id
                place_assignments.append(
                    CityPlaceAssignment(
                        agent_id=agent_id,
                        home_place_id=home_place.place_id,
                        work_place_id=work_place.place_id,
                        leisure_place_id=leisure_place.place_id,
                        home_node=home,
                        work_node=work,
                        leisure_node=leisure,
                    )
                )
            agents.append(CityAgent(agent_id, home, work, leisure))
            for source, target in (
                (home, work),
                (work, home),
                (home, leisure),
                (leisure, home),
            ):
                if (source, target) not in self._paths:
                    self._paths[source, target] = _shortest_path(road_graph, source, target)
            for destination, departure, return_time, day_kind in (
                (work, 8 * 60, 17 * 60, "weekday"),
                (leisure, 11 * 60, 16 * 60, "weekend"),
            ):
                if day_kind == "weekend" and days < 6:
                    continue
                outbound = self._paths[home, destination]
                inbound = self._paths[destination, home]
                return_departure = max(return_time, departure + outbound.duration_minutes)
                if return_departure + inbound.duration_minutes > 1440:
                    raise ValueError(
                        f"trip cannot finish before midnight for {agent_id} on {day_kind}"
                    )
        self.agents = tuple(agents)
        self.place_assignments = tuple(place_assignments)

    def metadata(self) -> dict[str, object]:
        document: dict[str, object] = {
            "city_id": self.pack.city_id,
            "city_name": self.pack.name,
            "city_sha256": self.pack.fingerprint,
            "seed": self.seed,
            "agent_count": len(self.agents),
            "days": self.days,
            "minutes_per_day": 1440,
            "model": (
                "illustrative-road-mobility-v3"
                if self.places is not None
                else (
                    "illustrative-road-mobility-v1"
                    if isinstance(self.pack, CityPack)
                    else "illustrative-road-mobility-v2"
                )
            ),
            "disclosure": (
                "Synthetic agents and illustrative travel speeds; not measured traffic "
                "or a prediction of real residents or advertising outcomes."
            ),
        }
        if isinstance(self.pack, CityPack):
            document.update(
                source_url=str(self.pack.source_url),
                license=self.pack.license,
                attribution=self.pack.attribution,
            )
        else:
            document.update(
                city_schema_version=2,
                time_zone=self.pack.time_zone,
                source=self.pack.source.model_dump(mode="json"),
                source_url=str(self.pack.source.source_url),
                license=self.pack.source.license,
                attribution=self.pack.source.attribution,
                bounds=self.pack.bounds.model_dump(mode="json"),
                known_omissions=list(self.pack.known_omissions),
            )
        if self.places is not None:
            document.update(
                place_schema_version=self.places.schema_version,
                place_set_name=self.places.name,
                place_set_sha256=self.places.fingerprint,
                place_assignment_count=len(self.place_assignments),
            )
        return document

    def place_assignment_document(self) -> dict[str, object]:
        """Return the canonical JSON-shaped place assignments for persistence and UI."""
        return {"assignments": [asdict(assignment) for assignment in self.place_assignments]}

    def frame(self, minute: int) -> tuple[CityPosition, ...]:
        if type(minute) is not int or not 0 <= minute < self.days * 1440:
            raise ValueError("minute is outside the simulated period")
        day, clock = divmod(minute, 1440)
        weekend = day % 7 in {5, 6}
        positions = []
        for agent in self.agents:
            destination = agent.leisure_node if weekend else agent.work_node
            outbound = self._paths[agent.home_node, destination]
            inbound = self._paths[destination, agent.home_node]
            departure = 11 * 60 if weekend else 8 * 60
            return_time = 16 * 60 if weekend else 17 * 60
            return_departure = max(return_time, departure + outbound.duration_minutes)
            if clock < departure:
                positions.append(self._at_node(agent.agent_id, minute, agent.home_node, "home"))
            elif clock < departure + outbound.duration_minutes:
                positions.append(self._on_path(agent.agent_id, minute, outbound, clock - departure))
            elif clock < return_departure:
                activity: Activity = "leisure" if weekend else "work"
                positions.append(self._at_node(agent.agent_id, minute, destination, activity))
            elif clock < return_departure + inbound.duration_minutes:
                positions.append(
                    self._on_path(agent.agent_id, minute, inbound, clock - return_departure)
                )
            else:
                positions.append(self._at_node(agent.agent_id, minute, agent.home_node, "home"))
        return tuple(positions)

    def road_traversals(self, day_index: int) -> tuple[CityRoadTraversal, ...]:
        """Return the continuous directed road schedule without changing minute frames."""
        if type(day_index) is not int or not 0 <= day_index < self.days:
            raise ValueError("day index is outside the simulated period")
        day_start = day_index * 1_440
        weekend = day_index % 7 in {5, 6}
        departure = 11 * 60 if weekend else 8 * 60
        return_time = 16 * 60 if weekend else 17 * 60
        traversals: list[CityRoadTraversal] = []
        for agent in self.agents:
            destination = agent.leisure_node if weekend else agent.work_node
            outbound = self._paths[agent.home_node, destination]
            inbound = self._paths[destination, agent.home_node]
            return_departure = max(return_time, departure + outbound.duration_minutes)
            traversals.extend(
                self._path_traversals(
                    agent.agent_id,
                    day_index,
                    "outbound",
                    day_start + departure,
                    outbound,
                )
            )
            traversals.extend(
                self._path_traversals(
                    agent.agent_id,
                    day_index,
                    "return",
                    day_start + return_departure,
                    inbound,
                )
            )
        return tuple(traversals)

    def frame_document(
        self, minute: int, *, selected_agent_id: str | None = None
    ) -> dict[str, object]:
        document: dict[str, object] = {
            "minute": minute,
            "positions": [
                asdict(position) | {"coordinate_label": position.coordinate_label}
                for position in self.frame(minute)
            ],
        }
        if selected_agent_id is not None:
            document["route"] = self.route_document(selected_agent_id, minute)
        return document

    def route_document(self, agent_id: str, minute: int) -> dict[str, object]:
        """Describe the selected day's planned directed leg without UI-side routing."""
        if type(minute) is not int or not 0 <= minute < self.days * 1440:
            raise ValueError("minute is outside the simulated period")
        agent = next((item for item in self.agents if item.agent_id == agent_id), None)
        if agent is None:
            raise ValueError("unknown city agent")
        day, clock = divmod(minute, 1440)
        weekend = day % 7 in {5, 6}
        destination = agent.leisure_node if weekend else agent.work_node
        outbound = self._paths[agent.home_node, destination]
        inbound = self._paths[destination, agent.home_node]
        departure = 11 * 60 if weekend else 8 * 60
        return_time = 16 * 60 if weekend else 17 * 60
        returning = clock >= max(return_time, departure + outbound.duration_minutes)
        path = inbound if returning else outbound
        return {
            "agent_id": agent_id,
            "direction": "return" if returning else "outbound",
            "destination_activity": "leisure" if weekend else "work",
            "node_ids": list(path.node_ids),
            "road_ids": list(path.road_ids),
            "directions": list(path.directions),
            "geometry": [
                [{"longitude": longitude, "latitude": latitude} for longitude, latitude in line]
                for line in path.geometry
            ],
            "duration_minutes": path.duration_minutes,
            "distance_meters": path.distance_meters,
        }

    def _at_node(
        self, agent_id: str, minute: int, node_id: str, activity: Activity
    ) -> CityPosition:
        node = self._nodes[node_id]
        return CityPosition(agent_id, minute, node.longitude, node.latitude, activity, None)

    def _on_path(self, agent_id: str, minute: int, path: CityPath, elapsed: float) -> CityPosition:
        remaining = elapsed
        for index, road_id in enumerate(path.road_ids):
            start = self._nodes[path.node_ids[index]]
            end = self._nodes[path.node_ids[index + 1]]
            edge = self._edges[start.node_id, end.node_id, road_id]
            if remaining < edge.duration or index == len(path.road_ids) - 1:
                fraction = min(1.0, remaining / edge.duration)
                longitude, latitude = _interpolate_geometry(edge, fraction)
                return CityPosition(agent_id, minute, longitude, latitude, "commute", road_id)
            remaining -= edge.duration
        raise AssertionError("a nonempty path must contain a road")

    def _path_traversals(
        self,
        agent_id: str,
        day_index: int,
        leg: Literal["outbound", "return"],
        start_minute: float,
        path: CityPath,
    ) -> tuple[CityRoadTraversal, ...]:
        traversals: list[CityRoadTraversal] = []
        current = start_minute
        for index, road_id in enumerate(path.road_ids):
            source = path.node_ids[index]
            target = path.node_ids[index + 1]
            edge = self._edges[source, target, road_id]
            end = current + edge.duration
            traversals.append(
                CityRoadTraversal(
                    agent_id=agent_id,
                    day_index=day_index,
                    leg=leg,
                    road_sequence=index,
                    road_id=road_id,
                    travel_direction=edge.direction,
                    start_minute=current,
                    end_minute=end,
                    distance_meters=edge.distance,
                )
            )
            current = end
        return tuple(traversals)


def _interpolate_geometry(edge: _RoadEdge, fraction: float) -> tuple[float, float]:
    if len(edge.geometry) == 2:
        start, end = edge.geometry
        return (
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
        )
    remaining = edge.distance * fraction
    for index, (start, end) in enumerate(pairwise(edge.geometry)):
        start_point = CityCoordinate(longitude=start[0], latitude=start[1])
        end_point = CityCoordinate(longitude=end[0], latitude=end[1])
        segment_distance = _length_meters(start_point, end_point)
        if remaining < segment_distance or index == len(edge.geometry) - 2:
            segment_fraction = min(1.0, remaining / segment_distance)
            return (
                start[0] + (end[0] - start[0]) * segment_fraction,
                start[1] + (end[1] - start[1]) * segment_fraction,
            )
        remaining -= segment_distance
    raise AssertionError("validated road geometry must contain a segment")


def _choose(
    choices: tuple[str, ...], oracle: RandomOracle, namespace: str, agent_id: str, index: int
) -> str:
    return choices[int(oracle.uniform(namespace, agent_id, 0, index) * len(choices))]


def _choice_index(length: int, oracle: RandomOracle, namespace: str, agent_id: str) -> int:
    return int(oracle.uniform(namespace, agent_id, 0, 0) * length)


__all__ = [
    "CityAgent",
    "CityMobility",
    "CityPath",
    "CityPlaceAssignment",
    "CityPosition",
    "CityRoadTraversal",
    "shortest_path",
]
