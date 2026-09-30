"""Deterministic synthetic opportunity evidence; never an impression or outcome."""

from __future__ import annotations

import math
from collections.abc import Iterator
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
_UINT64_MASK = (1 << 64) - 1
_SPLITMIX_GAMMA = 0x9E3779B97F4A7C15
_SPLITMIX_MIX_1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MIX_2 = 0x94D049BB133111EB
MAX_SPATIAL_OPPORTUNITIES = 520_800
MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES = 536_870_912


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
    opportunity_count: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITIES)

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
    opportunities: tuple[SpatialOpportunity, ...] = Field(max_length=MAX_SPATIAL_OPPORTUNITIES)

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


class SpatialOpportunityArtifactSummary(DomainModel):
    """Exact hashes and denominators for one canonical opportunity JSONL stream."""

    schema_version: Literal[1] = 1
    model_id: Literal["spatial-opportunity-artifact-v1"] = "spatial-opportunity-artifact-v1"
    claim_scope: Literal["synthetic-opportunity-not-impression"] = _CLAIM_SCOPE
    opportunity_model_id: Literal["spatial-opportunity-v1"] = _MODEL_ID
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    stream_sha256: str = Field(pattern=_HASH_PATTERN)
    stream_bytes: int = Field(ge=0, le=MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES)
    counts: SpatialOpportunityCounts


def spatial_opportunity_lines(
    evaluation: SpatialOpportunityEvaluation,
) -> Iterator[bytes]:
    """Yield one canonical UTF-8 JSONL record for every opportunity, in core order."""
    if not isinstance(evaluation, SpatialOpportunityEvaluation):
        raise TypeError("evaluation must be SpatialOpportunityEvaluation")
    for opportunity in evaluation.opportunities:
        yield (canonical_json(opportunity) + "\n").encode("utf-8")


def summarize_spatial_opportunity_artifact(
    evaluation: SpatialOpportunityEvaluation,
) -> SpatialOpportunityArtifactSummary:
    """Hash canonical stream bytes without joining the full artifact in memory."""
    digest = sha256()
    stream_bytes = 0
    for line in spatial_opportunity_lines(evaluation):
        stream_bytes += len(line)
        if stream_bytes > MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES:
            raise ValueError("spatial opportunity stream exceeds its size limit")
        digest.update(line)
    return SpatialOpportunityArtifactSummary(
        scenario_sha256=evaluation.scenario_sha256,
        city_sha256=evaluation.city_sha256,
        stream_sha256=digest.hexdigest(),
        stream_bytes=stream_bytes,
        counts=evaluation.counts,
    )


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


@dataclass(frozen=True, slots=True)
class _PhoneCandidate:
    placement: PhoneOpportunityPlacement
    agent_id: str
    day_index: int
    minute: int
    activity: SpatialActivity
    draw: float

    @property
    def at_millisecond(self) -> int:
        return self.minute * 60_000


_OpportunityCandidate: TypeAlias = _RoadsideCandidate | _PhoneCandidate


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


def _candidate_agent_id(candidate: _OpportunityCandidate) -> str:
    if isinstance(candidate, _RoadsideCandidate):
        return candidate.traversal.agent_id
    return candidate.agent_id


def _candidate_day_index(candidate: _OpportunityCandidate) -> int:
    if isinstance(candidate, _RoadsideCandidate):
        return candidate.traversal.day_index
    return candidate.day_index


def _candidate_sort_key(candidate: _OpportunityCandidate) -> tuple[object, ...]:
    return (
        candidate.at_millisecond,
        _candidate_agent_id(candidate),
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


def _phone_stream_key(
    seed: int,
    placement: PhoneOpportunityPlacement,
    agent_id: str,
) -> int:
    material = (
        f"{seed}|spatial-phone-opportunity-v1:"
        f"{placement.campaign_id}:{placement.placement_id}|{agent_id}|0"
    )
    return int.from_bytes(sha256(material.encode("utf-8")).digest()[:8], "big")


def _keyed_phone_draw(stream_key: int, minute: int) -> float:
    mixed = (stream_key + minute * _SPLITMIX_GAMMA) & _UINT64_MASK
    mixed = ((mixed ^ (mixed >> 30)) * _SPLITMIX_MIX_1) & _UINT64_MASK
    mixed = ((mixed ^ (mixed >> 27)) * _SPLITMIX_MIX_2) & _UINT64_MASK
    mixed ^= mixed >> 31
    return (mixed >> 11) / (1 << 53)


def evaluate_spatial_opportunities(
    mobility: CityMobility,
    scenario: SpatialCampaignScenario,
) -> SpatialOpportunityEvaluation:
    """Evaluate typed synthetic opportunities from immutable validated inputs."""
    if not isinstance(mobility, CityMobility):
        raise TypeError("mobility must be CityMobility")
    if not isinstance(scenario, SpatialCampaignScenario):
        raise TypeError("scenario must be SpatialCampaignScenario")
    validate_spatial_scenario_against_city(scenario, mobility.pack)
    if scenario.days != mobility.days:
        raise ValueError("spatial scenario duration does not match mobility duration")

    roadside = tuple(
        item for item in scenario.placements if isinstance(item, RoadsideBillboardPlacement)
    )
    phone = tuple(
        item for item in scenario.placements if isinstance(item, PhoneOpportunityPlacement)
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
    candidates: list[_OpportunityCandidate] = []
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

    phone_eligible = 0
    phone_successful = 0
    phone_pre_capped = 0
    phone_candidate_counts: dict[tuple[str, str, int], int] = {}
    phone_stream_keys = {
        (placement.placement_id, agent.agent_id): _phone_stream_key(
            mobility.seed,
            placement,
            agent.agent_id,
        )
        for placement in phone
        for agent in mobility.agents
    }
    for minute in range(mobility.days * 1_440):
        active_phone = tuple(
            phone_placement
            for phone_placement in phone
            if any(
                window.start_minute <= minute < window.end_minute
                for window in phone_placement.active_windows
            )
        )
        if not active_phone:
            continue
        day_index = minute // 1_440
        for position in mobility.frame(minute):
            for phone_placement in active_phone:
                if position.activity not in phone_placement.eligible_activities:
                    continue
                phone_eligible += 1
                draw = _keyed_phone_draw(
                    phone_stream_keys[(phone_placement.placement_id, position.agent_id)],
                    minute,
                )
                if draw >= phone_placement.opportunity_probability_per_minute:
                    continue
                phone_successful += 1
                phone_cap_key = (
                    phone_placement.placement_id,
                    position.agent_id,
                    day_index,
                )
                retained = phone_candidate_counts.get(phone_cap_key, 0)
                if retained >= phone_placement.frequency_cap_per_agent_per_day:
                    phone_pre_capped += 1
                    continue
                phone_candidate_counts[phone_cap_key] = retained + 1
                candidates.append(
                    _PhoneCandidate(
                        placement=phone_placement,
                        agent_id=position.agent_id,
                        day_index=day_index,
                        minute=minute,
                        activity=position.activity,
                        draw=draw,
                    )
                )

    cap_counts: dict[tuple[str, str, int], int] = {}
    opportunities: list[RoadsideOpportunity | PhoneOpportunity] = []
    capped = phone_pre_capped
    roadside_emitted = 0
    phone_emitted = 0
    for candidate in sorted(candidates, key=_candidate_sort_key):
        candidate_placement = candidate.placement
        agent_id = _candidate_agent_id(candidate)
        day_index = _candidate_day_index(candidate)
        cap_key = (candidate_placement.placement_id, agent_id, day_index)
        prior = cap_counts.get(cap_key, 0)
        if prior >= candidate_placement.frequency_cap_per_agent_per_day:
            capped += 1
            continue
        ordinal = prior + 1
        cap_counts[cap_key] = ordinal
        model_minute, millisecond = divmod(candidate.at_millisecond, 60_000)
        opportunity_id = _opportunity_id(
            scenario_sha256=scenario.fingerprint,
            city_sha256=mobility.pack.fingerprint,
            campaign_id=candidate_placement.campaign_id,
            placement_id=candidate_placement.placement_id,
            agent_id=agent_id,
            channel=candidate_placement.channel,
            at_millisecond=candidate.at_millisecond,
        )
        if isinstance(candidate, _RoadsideCandidate):
            roadside_placement = candidate.placement
            roadside_emitted += 1
            opportunities.append(
                RoadsideOpportunity(
                    opportunity_id=opportunity_id,
                    scenario_sha256=scenario.fingerprint,
                    city_sha256=mobility.pack.fingerprint,
                    campaign_id=roadside_placement.campaign_id,
                    placement_id=roadside_placement.placement_id,
                    agent_id=agent_id,
                    day_index=day_index,
                    model_minute=model_minute,
                    millisecond_within_minute=millisecond,
                    ordinal_for_agent_placement_day=ordinal,
                    road_id=roadside_placement.road_id,
                    travel_direction=roadside_placement.travel_direction,
                    road_fraction=roadside_placement.road_fraction,
                    side=roadside_placement.side,
                    minimum_distance_meters=candidate.geometry.minimum_distance_meters,
                    approach_distance_meters=candidate.geometry.approach_distance_meters,
                    view_angle_degrees=candidate.geometry.view_angle_degrees,
                )
            )
        else:
            phone_placement = candidate.placement
            phone_emitted += 1
            opportunities.append(
                PhoneOpportunity(
                    opportunity_id=opportunity_id,
                    scenario_sha256=scenario.fingerprint,
                    city_sha256=mobility.pack.fingerprint,
                    campaign_id=phone_placement.campaign_id,
                    placement_id=phone_placement.placement_id,
                    agent_id=agent_id,
                    day_index=day_index,
                    model_minute=model_minute,
                    millisecond_within_minute=millisecond,
                    ordinal_for_agent_placement_day=ordinal,
                    activity=candidate.activity,
                    eligibility_draw=candidate.draw,
                    opportunity_probability_per_minute=(
                        phone_placement.opportunity_probability_per_minute
                    ),
                )
            )

    counts = SpatialOpportunityCounts(
        roadside_matching_traversal_count=matching,
        roadside_active_crossing_count=active,
        roadside_proximity_passage_count=proximity,
        roadside_approximately_visible_count=approximately_visible,
        phone_eligible_agent_minute_count=phone_eligible,
        phone_successful_draw_count=phone_successful,
        frequency_capped_candidate_count=capped,
        roadside_opportunity_count=roadside_emitted,
        phone_opportunity_count=phone_emitted,
        opportunity_count=len(opportunities),
    )
    return SpatialOpportunityEvaluation(
        scenario_sha256=scenario.fingerprint,
        city_sha256=mobility.pack.fingerprint,
        counts=counts,
        opportunities=tuple(opportunities),
    )


__all__ = [
    "MAX_SPATIAL_OPPORTUNITIES",
    "MAX_SPATIAL_OPPORTUNITY_STREAM_BYTES",
    "PhoneOpportunity",
    "RoadsideOpportunity",
    "SpatialOpportunity",
    "SpatialOpportunityArtifactSummary",
    "SpatialOpportunityCounts",
    "SpatialOpportunityEvaluation",
    "evaluate_spatial_opportunities",
    "spatial_opportunity_lines",
    "summarize_spatial_opportunity_artifact",
]
