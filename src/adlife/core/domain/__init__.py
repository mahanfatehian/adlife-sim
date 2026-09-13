"""Versioned, adapter-free domain contracts."""

from adlife.core.domain.campaign import (
    BillboardPlacement,
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Placement,
    Price,
    TimeWindow,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, DomainModel, PersonProfile
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState, ExposureCount, Memory
from adlife.core.domain.world import Relationship, Route, RoutineBlock, World, Zone

__all__ = [
    "BillboardPlacement",
    "Campaign",
    "ConsumerState",
    "ConsumerTraits",
    "CreativeFeatures",
    "DomainEvent",
    "DomainModel",
    "EventSource",
    "EventType",
    "ExposureCount",
    "Memory",
    "PersonProfile",
    "PhonePlacement",
    "Placement",
    "Price",
    "Relationship",
    "Route",
    "RoutineBlock",
    "RunManifest",
    "Scenario",
    "SimulationResult",
    "TimeWindow",
    "World",
    "Zone",
]
