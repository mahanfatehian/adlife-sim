"""Deterministic, road-constrained mobility preview; no advertising decisions."""

from __future__ import annotations

import heapq
import itertools
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from adlife.core.domain.city import CityNode, CityPack, RoadKind
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


def _length_meters(start: CityNode, end: CityNode) -> float:
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


RoadEdge = tuple[str, str, float, float]
RoadGraph = dict[str, tuple[RoadEdge, ...]]


@dataclass(frozen=True, slots=True)
class _RouteState:
    duration: float
    distance: float
    parent: str | None
    road_id: str | None
    version: int


def _build_road_graph(pack: CityPack) -> RoadGraph:
    nodes = {node.node_id: node for node in pack.nodes}
    adjacency: dict[str, list[RoadEdge]] = {node_id: [] for node_id in nodes}
    for road in pack.roads:
        distance = _length_meters(nodes[road.source_node], nodes[road.target_node])
        if distance <= 0:
            raise ValueError("road endpoints must have different coordinates")
        duration = distance / METERS_PER_MINUTE[road.kind]
        adjacency[road.source_node].append((road.target_node, road.road_id, duration, distance))
        if not road.one_way:
            adjacency[road.target_node].append((road.source_node, road.road_id, duration, distance))
    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge[1], edge[0]))
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
    best = {start_id: _RouteState(0.0, 0.0, None, None, 0)}
    while frontier:
        duration, version, current = heapq.heappop(frontier)
        state = best[current]
        if duration != state.duration or version != state.version:
            continue
        if current == end_id:
            node_ids = [current]
            road_ids = []
            while best[current].parent is not None:
                path_state = best[current]
                assert path_state.road_id is not None
                assert path_state.parent is not None
                road_ids.append(path_state.road_id)
                current = path_state.parent
                node_ids.append(current)
            return CityPath(
                tuple(reversed(node_ids)),
                tuple(reversed(road_ids)),
                state.duration,
                state.distance,
            )
        for target, road_id, edge_duration, edge_distance in graph[current]:
            candidate_duration = duration + edge_duration
            previous = best.get(target)
            if previous is not None:
                if candidate_duration > previous.duration:
                    continue
                if candidate_duration == previous.duration and (
                    (*_road_sequence(best, current), road_id) >= _road_sequence(best, target)
                ):
                    continue
            next_version = next(serial)
            best[target] = _RouteState(
                candidate_duration,
                state.distance + edge_distance,
                current,
                road_id,
                next_version,
            )
            heapq.heappush(frontier, (candidate_duration, next_version, target))
    raise ValueError("no directed road path between city nodes")


def shortest_path(pack: CityPack, start_id: str, end_id: str) -> CityPath:
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


class CityMobility:
    """Immutable configuration; every frame is computed from seed and simulated minute."""

    def __init__(self, pack: CityPack, *, seed: int, agent_count: int, days: int) -> None:
        if not isinstance(pack, CityPack):
            raise TypeError("pack must be a CityPack")
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if type(agent_count) is not int or not 1 <= agent_count <= 250:
            raise ValueError("agent_count must be between 1 and 250")
        if type(days) is not int or not 1 <= days <= 31:
            raise ValueError("days must be between 1 and 31")
        self.pack = pack
        self.seed = seed
        self.days = days
        self._nodes = {node.node_id: node for node in pack.nodes}
        road_graph = _build_road_graph(pack)
        self._edge_minutes = {
            (source, target, road_id): duration
            for source, edges in road_graph.items()
            for target, road_id, duration, _ in edges
        }
        oracle = RandomOracle(seed)
        node_ids = tuple(self._nodes)
        agents: list[CityAgent] = []
        self._paths: dict[tuple[str, str], CityPath] = {}
        for index in range(agent_count):
            agent_id = f"person-{index + 1:03d}"
            home = _choose(node_ids, oracle, "city-home", agent_id, 0)
            other_nodes = tuple(node_id for node_id in node_ids if node_id != home)
            work = _choose(other_nodes, oracle, "city-work", agent_id, 0)
            leisure = _choose(other_nodes, oracle, "city-leisure", agent_id, 0)
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

    def metadata(self) -> dict[str, object]:
        return {
            "city_id": self.pack.city_id,
            "city_name": self.pack.name,
            "city_sha256": self.pack.fingerprint,
            "source_url": str(self.pack.source_url),
            "license": self.pack.license,
            "attribution": self.pack.attribution,
            "seed": self.seed,
            "agent_count": len(self.agents),
            "days": self.days,
            "minutes_per_day": 1440,
            "model": "illustrative-road-mobility-v1",
            "disclosure": (
                "Synthetic agents and illustrative travel speeds; not measured traffic "
                "or a prediction of real residents or advertising outcomes."
            ),
        }

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
            segment_minutes = self._edge_minutes[start.node_id, end.node_id, road_id]
            if remaining < segment_minutes or index == len(path.road_ids) - 1:
                fraction = min(1.0, remaining / segment_minutes)
                return CityPosition(
                    agent_id,
                    minute,
                    start.longitude + (end.longitude - start.longitude) * fraction,
                    start.latitude + (end.latitude - start.latitude) * fraction,
                    "commute",
                    road_id,
                )
            remaining -= segment_minutes
        raise AssertionError("a nonempty path must contain a road")


def _choose(
    choices: tuple[str, ...], oracle: RandomOracle, namespace: str, agent_id: str, index: int
) -> str:
    return choices[int(oracle.uniform(namespace, agent_id, 0, index) * len(choices))]


__all__ = ["CityAgent", "CityMobility", "CityPath", "CityPosition", "shortest_path"]
