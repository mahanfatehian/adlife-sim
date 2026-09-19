from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, TypeAlias

from pydantic import Field

from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import DomainModel, PersonProfile
from adlife.core.domain.state import Activity, ConsumerState
from adlife.core.domain.world import Route, World
from adlife.core.simulation.engine import UnboundRun, canonical_sha256, stable_event_id

AgentStatePair: TypeAlias = tuple[PersonProfile, ConsumerState]

if TYPE_CHECKING:
    from adlife.core.ports.cognition import CognitionRequest
    from adlife.core.simulation.exposure import AttentionDecision

_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ZONE_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"


class InvalidMovement(ValueError):
    """Raised when an intent cannot be resolved through one world route."""


class InvalidTickPlan(ValueError):
    """Raised when a tick plan does not cover its snapshot exactly once."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    agents: Mapping[str, AgentStatePair]
    simulated_minute: int = 0
    run_id: str | None = None
    next_event_sequence: int = 0
    version: int = 0
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        for name, value in (
            ("simulated_minute", self.simulated_minute),
            ("next_event_sequence", self.next_event_sequence),
            ("version", self.version),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.run_id is not None and _RUN_ID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("run_id must be a lowercase slug of at most 40 characters")
        if not self.agents:
            raise ValueError("snapshot must contain at least one agent")

        ordered: dict[str, AgentStatePair] = {}
        for agent_id in sorted(self.agents):
            pair = self.agents[agent_id]
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError("snapshot agent values must be profile/state tuples")
            profile, state = pair
            if not isinstance(profile, PersonProfile) or not isinstance(state, ConsumerState):
                raise TypeError("snapshot agent values must contain domain profile and state")
            if agent_id != profile.agent_id or agent_id != state.agent_id:
                raise ValueError("snapshot key must match profile and state agent_id")
            ordered[agent_id] = (profile, state)

        object.__setattr__(self, "agents", MappingProxyType(ordered))
        fingerprint_input: Mapping[str, object] = {
            "agents": {
                agent_id: {
                    "profile": profile,
                    "state": state,
                }
                for agent_id, (profile, state) in ordered.items()
            },
            "simulated_minute": self.simulated_minute,
            "run_id": self.run_id,
            "next_event_sequence": self.next_event_sequence,
            "version": self.version,
        }
        object.__setattr__(self, "fingerprint", canonical_sha256(fingerprint_input))


class MovementIntent(DomainModel):
    schema_version: Literal[1] = 1
    agent_id: str = Field(pattern=r"^person-[0-9]{3}$")
    from_zone: str = Field(pattern=_ZONE_PATTERN)
    to_zone: str = Field(pattern=_ZONE_PATTERN)
    activity: Activity
    route_id: str | None = Field(default=None, pattern=_ZONE_PATTERN)


def _revalidate_intent(intent: MovementIntent) -> MovementIntent:
    field_data: dict[str, object] = {}
    for field_name in MovementIntent.model_fields:
        try:
            field_data[field_name] = getattr(intent, field_name)
        except AttributeError:
            continue
    return MovementIntent.model_validate(field_data)


@dataclass(frozen=True, slots=True)
class _ResolvedMovement:
    agent_id: str
    from_zone: str
    to_zone: str
    from_route_id: str | None
    to_route_id: str | None
    from_activity: Activity | None
    to_activity: Activity


@dataclass(frozen=True, slots=True)
class _MovementResolution:
    transitions: tuple[_ResolvedMovement, ...]
    events: tuple[DomainEvent, ...]


@dataclass(frozen=True, slots=True)
class TickPlan:
    snapshot: Snapshot
    intents: tuple[MovementIntent, ...]
    movement_events: tuple[DomainEvent, ...] = ()
    attention: tuple[AttentionDecision, ...] = ()
    requests: tuple[CognitionRequest, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Snapshot):
            raise TypeError("TickPlan.snapshot must be a Snapshot")

        intents = tuple(self.intents)
        if any(not isinstance(intent, MovementIntent) for intent in intents):
            raise TypeError("TickPlan.intents must contain only MovementIntent values")
        validated = tuple(_revalidate_intent(intent) for intent in intents)
        ordered = tuple(sorted(validated, key=lambda intent: intent.agent_id))
        intent_agent_ids = tuple(intent.agent_id for intent in ordered)
        if len(intent_agent_ids) != len(set(intent_agent_ids)):
            raise InvalidTickPlan("duplicate movement intent for agent")
        if intent_agent_ids != tuple(self.snapshot.agents):
            raise InvalidTickPlan("intent agent IDs must exactly match snapshot agent IDs")
        object.__setattr__(self, "intents", ordered)

        movement_events = tuple(self.movement_events)
        if any(not isinstance(event, DomainEvent) for event in movement_events):
            raise TypeError("TickPlan.movement_events must contain only DomainEvent values")
        object.__setattr__(
            self,
            "movement_events",
            tuple(sorted(movement_events, key=lambda event: (event.sequence, event.event_id))),
        )
        # The exposure and cognition stages import this module, so their types are only
        # checked lazily: a fully planned tick carries AttentionDecision and
        # CognitionRequest values, and a movement-only plan carries empty tuples.
        from adlife.core.ports.cognition import CognitionRequest as _CognitionRequest
        from adlife.core.simulation.exposure import AttentionDecision as _AttentionDecision

        attention = tuple(self.attention)
        if any(not isinstance(item, _AttentionDecision) for item in attention):
            raise TypeError("TickPlan.attention must contain only AttentionDecision values")
        object.__setattr__(self, "attention", attention)
        requests = tuple(self.requests)
        if any(not isinstance(item, _CognitionRequest) for item in requests):
            raise TypeError("TickPlan.requests must contain only CognitionRequest values")
        object.__setattr__(self, "requests", requests)
        if not attention and requests:
            raise InvalidTickPlan("a plan cannot request cognition without attention decisions")


@dataclass(frozen=True, slots=True)
class TickOutcome:
    snapshot: Snapshot
    events: tuple[DomainEvent, ...]
    ends_simulated_day: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Snapshot):
            raise TypeError("TickOutcome.snapshot must be a Snapshot")
        if not isinstance(self.ends_simulated_day, bool):
            raise TypeError("TickOutcome.ends_simulated_day must be a bool")

        events = tuple(self.events)
        if any(not isinstance(event, DomainEvent) for event in events):
            raise TypeError("TickOutcome.events must contain only DomainEvent values")
        object.__setattr__(
            self,
            "events",
            tuple(sorted(events, key=lambda event: (event.sequence, event.event_id))),
        )


VIRTUAL_ZONES: frozenset[str] = frozenset({"online"})
"""Zones an agent occupies without travelling a physical route.

``online`` is where a phone-check happens: it is not a place, so a routine that moves an
agent online names no route and needs none. The documented world lists it among its ten
zones and its routes connect physical zones only - a phone-check that needed a highway
route would never happen. A caller that DOES name a route into a virtual zone is
honoured: the explicit route wins, and only the unrouted movement is virtual.
"""


def _route_zones(route: Route) -> frozenset[str]:
    return frozenset(
        zone_id
        for zone_id in (route.source_zone, route.transit_zone, route.target_zone)
        if zone_id is not None
    )


def _route_for_intent(
    intent: MovementIntent,
    routes_by_id: Mapping[str, Route],
    *,
    current_route: Route | None = None,
) -> Route | None:
    if intent.from_zone in VIRTUAL_ZONES:
        return None
    if intent.to_zone in VIRTUAL_ZONES and intent.route_id is None:
        return None
    if intent.from_zone == intent.to_zone:
        if intent.route_id is None:
            return None
        try:
            route = routes_by_id[intent.route_id]
        except KeyError as error:
            raise InvalidMovement(f"unknown route: {intent.route_id}") from error
        if intent.from_zone not in _route_zones(route):
            raise InvalidMovement(
                f"no route from {intent.from_zone} to {intent.to_zone} for {intent.agent_id}"
            )
        return route

    if intent.route_id is not None:
        try:
            route = routes_by_id[intent.route_id]
        except KeyError as error:
            raise InvalidMovement(f"unknown route: {intent.route_id}") from error
        if {intent.from_zone, intent.to_zone} <= _route_zones(route):
            return route
        raise InvalidMovement(
            f"no route from {intent.from_zone} to {intent.to_zone} for {intent.agent_id}"
        )

    # An agent standing on a route it is already travelling continues on that route
    # before any other is considered: the built-in routines hop between the transit zone
    # and their destination without naming a route, and the transit zone is shared by
    # the morning and evening roads of the documented world.
    if (
        intent.route_id is None
        and current_route is not None
        and intent.from_zone != intent.to_zone
        and {intent.from_zone, intent.to_zone} <= _route_zones(current_route)
    ):
        return current_route

    matches = tuple(
        route
        for route in routes_by_id.values()
        if {intent.from_zone, intent.to_zone} <= _route_zones(route)
    )
    if not matches:
        raise InvalidMovement(
            f"no route from {intent.from_zone} to {intent.to_zone} for {intent.agent_id}"
        )
    if len(matches) > 1:
        raise InvalidMovement(
            f"multiple routes from {intent.from_zone} to {intent.to_zone} for {intent.agent_id}"
        )
    return matches[0]


def _resolve_movement(
    intents: Sequence[MovementIntent],
    world: World,
    snapshot: Snapshot | None = None,
) -> _MovementResolution:
    ordered_intents = tuple(sorted(intents, key=lambda intent: intent.agent_id))
    agent_ids = tuple(intent.agent_id for intent in ordered_intents)
    if len(agent_ids) != len(set(agent_ids)):
        raise InvalidMovement("each agent may submit only one movement intent per tick")

    zone_ids = {zone.zone_id for zone in world.zones}
    routes_by_id: dict[str, Route] = {}
    for world_route in world.routes:
        if world_route.route_id in routes_by_id:
            raise InvalidMovement(f"duplicate route_id: {world_route.route_id}")
        routes_by_id[world_route.route_id] = world_route

    transitions: list[_ResolvedMovement] = []
    for intent in ordered_intents:
        unknown_zones = {intent.from_zone, intent.to_zone} - zone_ids
        if unknown_zones:
            raise InvalidMovement(f"unknown zone: {sorted(unknown_zones)[0]}")

        state: ConsumerState | None = None
        if snapshot is not None:
            try:
                _, state = snapshot.agents[intent.agent_id]
            except KeyError as error:
                raise InvalidMovement(
                    f"agent {intent.agent_id} is missing from snapshot"
                ) from error
            if state.location != intent.from_zone:
                raise InvalidMovement(
                    f"snapshot location for {intent.agent_id} is {state.location}, "
                    f"not {intent.from_zone}"
                )

        route = _route_for_intent(
            intent,
            routes_by_id,
            current_route=(
                routes_by_id.get(state.current_route_id)
                if state is not None and state.current_route_id is not None
                else None
            ),
        )
        transitions.append(
            _ResolvedMovement(
                agent_id=intent.agent_id,
                from_zone=intent.from_zone,
                to_zone=intent.to_zone,
                from_route_id=state.current_route_id if state is not None else None,
                to_route_id=route.route_id if route is not None else None,
                from_activity=state.activity if state is not None else None,
                to_activity=intent.activity,
            )
        )

    candidates: list[tuple[int, str, EventType, Mapping[str, object]]] = []
    for transition in transitions:
        if transition.from_activity is None or transition.from_activity != transition.to_activity:
            activity_payload: dict[str, object] = {"to_activity": transition.to_activity}
            if transition.from_activity is not None:
                activity_payload["from_activity"] = transition.from_activity
            candidates.append(
                (
                    0,
                    transition.agent_id,
                    EventType.ACTIVITY_CHANGED,
                    activity_payload,
                )
            )
        if (
            transition.from_zone != transition.to_zone
            or transition.from_route_id != transition.to_route_id
        ):
            candidates.append(
                (
                    1,
                    transition.agent_id,
                    EventType.LOCATION_CHANGED,
                    {
                        "from_zone": transition.from_zone,
                        "to_zone": transition.to_zone,
                        "route_id": transition.to_route_id,
                        "from_route_id": transition.from_route_id,
                        "to_route_id": transition.to_route_id,
                    },
                )
            )

    if snapshot is None:
        run_id = "movement"
        simulated_minute = 0
        sequence_start = 0
    else:
        snapshot_run_id = snapshot.run_id
        if snapshot_run_id is None:
            raise UnboundRun("snapshot event stream is unbound; call bind_run before resolution")
        run_id = snapshot_run_id
        simulated_minute = snapshot.simulated_minute
        sequence_start = snapshot.next_event_sequence
    events: list[DomainEvent] = []
    for offset, (_, agent_id, event_type, payload) in enumerate(
        sorted(candidates, key=lambda item: (item[0], item[1]))
    ):
        sequence = sequence_start + offset
        events.append(
            DomainEvent(
                event_id=stable_event_id(run_id, sequence),
                run_id=run_id,
                simulated_minute=simulated_minute,
                sequence=sequence,
                event_type=event_type,
                agent_id=agent_id,
                payload=payload,
                source=EventSource.RULE,
            )
        )
    return _MovementResolution(transitions=tuple(transitions), events=tuple(events))


def resolve_movement(
    intents: Sequence[MovementIntent],
    world: World,
    snapshot: Snapshot | None = None,
) -> tuple[DomainEvent, ...]:
    """Emit canonical events; the two-argument form is standalone and non-persisted."""
    return _resolve_movement(intents, world, snapshot).events


__all__ = [
    "AgentStatePair",
    "InvalidMovement",
    "InvalidTickPlan",
    "MovementIntent",
    "Snapshot",
    "TickOutcome",
    "TickPlan",
    "resolve_movement",
]
