"""Deterministic synthetic opportunity evidence; never an impression or outcome."""

from __future__ import annotations

import math
from dataclasses import dataclass
from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, model_validator

from adlife.core.domain.city import CityPack, CityPackDocument, TravelDirection
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import (
    BillboardSide,
    PhoneOpportunityPlacement,
    RoadsideBillboardPlacement,
    SpatialActivity,
    SpatialCampaignScenario,
    validate_spatial_scenario_against_city,
)
from adlife.core.simulation.city_mobility import CityMobility, CityRoadTraversal

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_MODEL_ID: Literal["spatial-opportunity-v1"] = "spatial-opportunity-v1"
_CLAIM_SCOPE: Literal["synthetic-opportunity-not-impression"] = (
    "synthetic-opportunity-not-impression"
)


class _SpatialOpportunity(DomainModel):
    schema_version: Literal[1] = 1
    model_id: Literal["spatial-opportunity-v1"] = _MODEL_ID
    claim_scope: Literal["synthetic-opportunity-not-impression"] = _CLAIM_SCOPE
    opportunity_id: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    campaign_id: str = Field(pattern=_ID_PATTERN)
    placement_id: str = Field(pattern=_ID_PATTERN)
    agent_id: str = Field(pattern=r"^person-[0-9]{3}$")
    channel: str = Field(min_length=1, max_length=40)
    day_index: int = Field(ge=0, le=6)
    model_minute: int = Field(ge=0, lt=10_080)
    millisecond_within_minute: int = Field(ge=0, lt=60_000)
    ordinal_for_agent_placement_day: int = Field(ge=1, le=100)


class RoadsideOpportunity(_SpatialOpportunity):
    """A synthetic directional passage that satisfied the v1 facing heuristic."""

    channel: Literal["roadside-billboard"] = "roadside-billboard"
    basis: Literal["directional-road-passage-v1"] = "directional-road-passage-v1"
    road_id: str = Field(pattern=_ID_PATTERN)
    travel_direction: TravelDirection
    road_fraction: float = Field(gt=0, lt=1)
    side: BillboardSide
    minimum_distance_meters: float = Field(ge=0, le=1)
    approach_distance_meters: float = Field(gt=0, le=1_000)
    view_angle_degrees: float = Field(ge=0, le=90)


class PhoneOpportunity(_SpatialOpportunity):
    """A future-compatible typed phone record; generation is added in C2 task 4."""

    channel: Literal["mobile-feed"] = "mobile-feed"
    basis: Literal["keyed-activity-minute-v1"] = "keyed-activity-minute-v1"
    activity: SpatialActivity
    eligibility_draw: float = Field(ge=0, lt=1)
    opportunity_probability_per_minute: float = Field(ge=0, le=1)


SpatialOpportunity: TypeAlias = Annotated[
    RoadsideOpportunity | PhoneOpportunity,
    Field(discriminator="channel"),
]


class SpatialOpportunityCounts(DomainModel):
    """Explicit model-process denominators before and after each opportunity filter."""

    schema_version: Literal[1] = 1
    roadside_matching_traversal_count: int = Field(ge=0)
    roadside_active_crossing_count: int = Field(ge=0)
    roadside_proximity_passage_count: int = Field(ge=0)
    roadside_approximately_visible_count: int = Field(ge=0)
    phone_eligible_agent_minute_count: int = Field(ge=0)
    phone_successful_draw_count: int = Field(ge=0)
    frequency_capped_candidate_count: int = Field(ge=0)
    roadside_opportunity_count: int = Field(ge=0)
    phone_opportunity_count: int = Field(ge=0)
    opportunity_count: int = Field(ge=0)

    @model_validator(mode="after")
    def coherent_funnel(self) -> Self:
        if not (
            self.roadside_approximately_visible_count
            <= self.roadside_proximity_passage_count
            <= self.roadside_active_crossing_count
            <= self.roadside_matching_traversal_count
        ):
            raise ValueError("roadside opportunity stage counts are inconsistent")
        if self.phone_successful_draw_count > self.phone_eligible_agent_minute_count:
            raise ValueError("phone opportunity stage counts are inconsistent")
        if self.opportunity_count != (
            self.roadside_opportunity_count + self.phone_opportunity_count
        ):
            raise ValueError("opportunity channel counts do not match total")
        candidates = self.roadside_approximately_visible_count + self.phone_successful_draw_count
        if candidates != self.opportunity_count + self.frequency_capped_candidate_count:
            raise ValueError("opportunity candidate counts do not match cap outcomes")
        return self


class SpatialOpportunityEvaluation(DomainModel):
    """Canonical immutable opportunity records and their scientific denominators."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-opportunity-v1"] = _MODEL_ID
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    counts: SpatialOpportunityCounts
    opportunities: tuple[SpatialOpportunity, ...]

    @model_validator(mode="after")
    def coherent_records(self) -> Self:
        if len(self.opportunities) != self.counts.opportunity_count:
            raise ValueError("opportunity records do not match count")
        if len({item.opportunity_id for item in self.opportunities}) != len(self.opportunities):
            raise ValueError("opportunity identifiers must be unique")
        if any(
            item.scenario_sha256 != self.scenario_sha256 or item.city_sha256 != self.city_sha256
            for item in self.opportunities
        ):
            raise ValueError("opportunity record fingerprints do not match evaluation")
        keys = tuple(_opportunity_sort_key(item) for item in self.opportunities)
        if keys != tuple(sorted(keys)):
            raise ValueError("opportunity records must be in canonical order")
        return self


@dataclass(frozen=True, slots=True)
class _RoadsideGeometryEvidence:
    minimum_distance_meters: float
    approach_distance_meters: float
    view_angle_degrees: float


@dataclass(frozen=True, slots=True)
class _RoadsideCandidate:
    placement: RoadsideBillboardPlacement
    traversal: CityRoadTraversal
    at_millisecond: int
    geometry: _RoadsideGeometryEvidence


def _distance_meters(start: tuple[float, float], end: tuple[float, float]) -> float:
    longitude1, latitude1 = start
    longitude2, latitude2 = end
    lat1, lat2 = math.radians(latitude1), math.radians(latitude2)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(longitude2 - longitude1)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 12_742_000.0 * math.asin(min(1.0, math.sqrt(value)))


def _physical_road_geometry(
    pack: CityPackDocument, road_id: str
) -> tuple[tuple[float, float], ...]:
    nodes = {node.node_id: node for node in pack.nodes}
    if isinstance(pack, CityPack):
        road = next(item for item in pack.roads if item.road_id == road_id)
        start = nodes[road.source_node]
        end = nodes[road.target_node]
        shape: tuple[tuple[float, float], ...] = ()
    else:
        road_v2 = next(item for item in pack.roads if item.road_id == road_id)
        start = nodes[road_v2.source_node]
        end = nodes[road_v2.target_node]
        shape = tuple((point.longitude, point.latitude) for point in road_v2.shape)
    return (
        (start.longitude, start.latitude),
        *shape,
        (end.longitude, end.latitude),
    )


def _point_at_distance(
    points: tuple[tuple[float, float], ...], target_meters: float
) -> tuple[float, float]:
    lengths = tuple(_distance_meters(start, end) for start, end in pairwise(points))
    total = sum(lengths)
    target = min(total, max(0.0, target_meters))
    remaining = target
    for index, ((start, end), length) in enumerate(zip(pairwise(points), lengths, strict=True)):
        if remaining <= length or index == len(lengths) - 1:
            fraction = min(1.0, remaining / length)
            return (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            )
        remaining -= length
    raise AssertionError("validated road geometry must contain a segment")


def _bearing_degrees(origin: tuple[float, float], target: tuple[float, float]) -> float:
    longitude1, latitude1 = map(math.radians, origin)
    longitude2, latitude2 = map(math.radians, target)
    delta_lon = longitude2 - longitude1
    x = math.sin(delta_lon) * math.cos(latitude2)
    y = math.cos(latitude1) * math.sin(latitude2) - math.sin(latitude1) * math.cos(
        latitude2
    ) * math.cos(delta_lon)
    return math.degrees(math.atan2(x, y)) % 360


def _angle_difference(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


def _roadside_geometry(
    pack: CityPackDocument,
    placement: RoadsideBillboardPlacement,
) -> _RoadsideGeometryEvidence:
    points = _physical_road_geometry(pack, placement.road_id)
    lengths = tuple(_distance_meters(start, end) for start, end in pairwise(points))
    total = sum(lengths)
    placement_distance = total * placement.road_fraction
    road_point = _point_at_distance(points, placement_distance)
    if placement.travel_direction == "forward":
        approach_position = max(0.0, placement_distance - placement.max_view_distance_meters)
    else:
        approach_position = min(total, placement_distance + placement.max_view_distance_meters)
    approach_point = _point_at_distance(points, approach_position)
    approach_distance = abs(placement_distance - approach_position)
    placement_point = (placement.longitude, placement.latitude)
    return _RoadsideGeometryEvidence(
        minimum_distance_meters=_distance_meters(road_point, placement_point),
        approach_distance_meters=approach_distance,
        view_angle_degrees=_angle_difference(
            placement.orientation_degrees,
            _bearing_degrees(placement_point, approach_point),
        ),
    )


def _at_millisecond(traversal: CityRoadTraversal, road_fraction: float) -> int:
    progress = road_fraction if traversal.travel_direction == "forward" else 1 - road_fraction
    crossing = traversal.start_minute + traversal.duration_minutes * progress
    return math.floor(crossing * 60_000 + 0.5)


def _is_active(placement: RoadsideBillboardPlacement, at_millisecond: int) -> bool:
    return any(
        window.start_minute * 60_000 <= at_millisecond < window.end_minute * 60_000
        for window in placement.active_windows
    )


def _candidate_sort_key(candidate: _RoadsideCandidate) -> tuple[object, ...]:
    return (
        candidate.at_millisecond,
        candidate.traversal.agent_id,
        candidate.placement.campaign_id,
        candidate.placement.placement_id,
        candidate.placement.channel,
    )


def _opportunity_sort_key(opportunity: _SpatialOpportunity) -> tuple[object, ...]:
    return (
        opportunity.model_minute,
        opportunity.millisecond_within_minute,
        opportunity.agent_id,
        opportunity.campaign_id,
        opportunity.placement_id,
        opportunity.channel,
    )


def _opportunity_id(
    *,
    scenario_sha256: str,
    city_sha256: str,
    campaign_id: str,
    placement_id: str,
    agent_id: str,
    channel: str,
    at_millisecond: int,
) -> str:
    identity = {
        "model_id": _MODEL_ID,
        "scenario_sha256": scenario_sha256,
        "city_sha256": city_sha256,
        "campaign_id": campaign_id,
        "placement_id": placement_id,
        "agent_id": agent_id,
        "channel": channel,
        "at_millisecond": at_millisecond,
    }
    return sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def evaluate_spatial_opportunities(
    mobility: CityMobility,
    scenario: SpatialCampaignScenario,
) -> SpatialOpportunityEvaluation:
    """Evaluate synthetic roadside opportunities from immutable validated inputs."""
    if not isinstance(mobility, CityMobility):
        raise TypeError("mobility must be CityMobility")
    if not isinstance(scenario, SpatialCampaignScenario):
        raise TypeError("scenario must be SpatialCampaignScenario")
    validate_spatial_scenario_against_city(scenario, mobility.pack)
    if scenario.days != mobility.days:
        raise ValueError("spatial scenario duration does not match mobility duration")
    if any(isinstance(item, PhoneOpportunityPlacement) for item in scenario.placements):
        raise ValueError("phone opportunity evaluation is not available in this model phase")

    roadside = tuple(
        item for item in scenario.placements if isinstance(item, RoadsideBillboardPlacement)
    )
    by_road_direction: dict[
        tuple[str, TravelDirection], tuple[RoadsideBillboardPlacement, ...]
    ] = {}
    for placement in roadside:
        key = (placement.road_id, placement.travel_direction)
        by_road_direction[key] = (*by_road_direction.get(key, ()), placement)
    geometry = {
        placement.placement_id: _roadside_geometry(mobility.pack, placement)
        for placement in roadside
    }

    matching = 0
    active = 0
    proximity = 0
    approximately_visible = 0
    candidates: list[_RoadsideCandidate] = []
    for day_index in range(mobility.days):
        for traversal in mobility.road_traversals(day_index):
            placements = by_road_direction.get((traversal.road_id, traversal.travel_direction), ())
            for placement in placements:
                matching += 1
                at_millisecond = _at_millisecond(traversal, placement.road_fraction)
                if not _is_active(placement, at_millisecond):
                    continue
                active += 1
                proximity += 1
                evidence = geometry[placement.placement_id]
                if evidence.view_angle_degrees > 90:
                    continue
                approximately_visible += 1
                candidates.append(
                    _RoadsideCandidate(placement, traversal, at_millisecond, evidence)
                )

    cap_counts: dict[tuple[str, str, int], int] = {}
    opportunities: list[RoadsideOpportunity] = []
    capped = 0
    for candidate in sorted(candidates, key=_candidate_sort_key):
        placement = candidate.placement
        traversal = candidate.traversal
        cap_key = (placement.placement_id, traversal.agent_id, traversal.day_index)
        prior = cap_counts.get(cap_key, 0)
        if prior >= placement.frequency_cap_per_agent_per_day:
            capped += 1
            continue
        ordinal = prior + 1
        cap_counts[cap_key] = ordinal
        model_minute, millisecond = divmod(candidate.at_millisecond, 60_000)
        opportunities.append(
            RoadsideOpportunity(
                opportunity_id=_opportunity_id(
                    scenario_sha256=scenario.fingerprint,
                    city_sha256=mobility.pack.fingerprint,
                    campaign_id=placement.campaign_id,
                    placement_id=placement.placement_id,
                    agent_id=traversal.agent_id,
                    channel=placement.channel,
                    at_millisecond=candidate.at_millisecond,
                ),
                scenario_sha256=scenario.fingerprint,
                city_sha256=mobility.pack.fingerprint,
                campaign_id=placement.campaign_id,
                placement_id=placement.placement_id,
                agent_id=traversal.agent_id,
                day_index=traversal.day_index,
                model_minute=model_minute,
                millisecond_within_minute=millisecond,
                ordinal_for_agent_placement_day=ordinal,
                road_id=placement.road_id,
                travel_direction=placement.travel_direction,
                road_fraction=placement.road_fraction,
                side=placement.side,
                minimum_distance_meters=candidate.geometry.minimum_distance_meters,
                approach_distance_meters=candidate.geometry.approach_distance_meters,
                view_angle_degrees=candidate.geometry.view_angle_degrees,
            )
        )

    counts = SpatialOpportunityCounts(
        roadside_matching_traversal_count=matching,
        roadside_active_crossing_count=active,
        roadside_proximity_passage_count=proximity,
        roadside_approximately_visible_count=approximately_visible,
        phone_eligible_agent_minute_count=0,
        phone_successful_draw_count=0,
        frequency_capped_candidate_count=capped,
        roadside_opportunity_count=len(opportunities),
        phone_opportunity_count=0,
        opportunity_count=len(opportunities),
    )
    return SpatialOpportunityEvaluation(
        scenario_sha256=scenario.fingerprint,
        city_sha256=mobility.pack.fingerprint,
        counts=counts,
        opportunities=tuple(opportunities),
    )


__all__ = [
    "PhoneOpportunity",
    "RoadsideOpportunity",
    "SpatialOpportunity",
    "SpatialOpportunityCounts",
    "SpatialOpportunityEvaluation",
    "evaluate_spatial_opportunities",
]
