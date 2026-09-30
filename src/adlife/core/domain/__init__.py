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
from adlife.core.domain.city import (
    CityBounds,
    CityCoordinate,
    CityNode,
    CityPack,
    CityPackDocument,
    CityPackV2,
    CityRoad,
    CityRoadV2,
    CitySource,
    TravelDirection,
    parse_city_pack_json,
)
from adlife.core.domain.city_catalog import (
    CityCatalog,
    CityCatalogEntry,
    parse_city_catalog_json,
)
from adlife.core.domain.city_places import (
    CityPlace,
    CityPlaceProvenance,
    CityPlaceSet,
    parse_city_place_set_json,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, DomainModel, PersonProfile
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.spatial_campaign import (
    PhoneOpportunityPlacement,
    RoadsideBillboardPlacement,
    SpatialActiveWindow,
    SpatialCampaign,
    SpatialCampaignScenario,
    SpatialPlacement,
    parse_spatial_campaign_scenario_json,
)
from adlife.core.domain.state import ConsumerState, ExposureCount, Memory
from adlife.core.domain.world import Relationship, Route, RoutineBlock, World, Zone

__all__ = [
    "BillboardPlacement",
    "Campaign",
    "CityBounds",
    "CityCatalog",
    "CityCatalogEntry",
    "CityCoordinate",
    "CityNode",
    "CityPack",
    "CityPackDocument",
    "CityPackV2",
    "CityPlace",
    "CityPlaceProvenance",
    "CityPlaceSet",
    "CityRoad",
    "CityRoadV2",
    "CitySource",
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
    "PhoneOpportunityPlacement",
    "PhonePlacement",
    "Placement",
    "Price",
    "Relationship",
    "RoadsideBillboardPlacement",
    "Route",
    "RoutineBlock",
    "RunManifest",
    "Scenario",
    "SimulationResult",
    "SpatialActiveWindow",
    "SpatialCampaign",
    "SpatialCampaignScenario",
    "SpatialPlacement",
    "TimeWindow",
    "TravelDirection",
    "World",
    "Zone",
    "parse_city_catalog_json",
    "parse_city_pack_json",
    "parse_city_place_set_json",
    "parse_spatial_campaign_scenario_json",
]
