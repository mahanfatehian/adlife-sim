from typing import Literal, Self

from pydantic import Field, model_validator

from adlife.core.domain.campaign import BillboardPlacement, Campaign, PhonePlacement
from adlife.core.domain.person import DomainModel, PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Relationship, RoutineBlock, World


def _duplicates(values: tuple[str, ...]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return duplicates


class Scenario(DomainModel):
    schema_version: Literal[1] = 1
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=120)
    days: int = Field(ge=1, le=7)
    tick_minutes: Literal[15] = 15
    world: World
    population: tuple[PersonProfile, ...] = Field(min_length=1, max_length=30)
    initial_states: tuple[ConsumerState, ...] = Field(max_length=30)
    routine_blocks: tuple[RoutineBlock, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    campaigns: tuple[Campaign, ...] = ()
    social_enabled: bool = True

    @model_validator(mode="after")
    def validate_cross_references(self) -> Self:
        agent_ids = tuple(profile.agent_id for profile in self.population)
        if duplicate_agents := _duplicates(agent_ids):
            raise ValueError(f"duplicate agent_id: {sorted(duplicate_agents)[0]}")

        state_agent_ids = tuple(state.agent_id for state in self.initial_states)
        if duplicate_states := _duplicates(state_agent_ids):
            raise ValueError(f"duplicate initial state agent_id: {sorted(duplicate_states)[0]}")

        zone_ids = tuple(zone.zone_id for zone in self.world.zones)
        if duplicate_zones := _duplicates(zone_ids):
            raise ValueError(f"duplicate zone_id: {sorted(duplicate_zones)[0]}")

        route_ids = tuple(route.route_id for route in self.world.routes)
        if duplicate_routes := _duplicates(route_ids):
            raise ValueError(f"duplicate route_id: {sorted(duplicate_routes)[0]}")

        campaign_ids = tuple(campaign.campaign_id for campaign in self.campaigns)
        if duplicate_campaigns := _duplicates(campaign_ids):
            raise ValueError(f"duplicate campaign_id: {sorted(duplicate_campaigns)[0]}")

        agent_id_set = set(agent_ids)
        if set(state_agent_ids) != agent_id_set:
            raise ValueError("initial states must match population agent identifiers")

        zone_id_set = set(zone_ids)
        for profile in self.population:
            if profile.home_zone not in zone_id_set:
                raise ValueError(f"unknown home zone: {profile.home_zone}")
            if (
                profile.work_or_study_zone is not None
                and profile.work_or_study_zone not in zone_id_set
            ):
                raise ValueError(f"unknown work or study zone: {profile.work_or_study_zone}")

        for state in self.initial_states:
            if state.location not in zone_id_set:
                raise ValueError(f"unknown state location: {state.location}")

        for route in self.world.routes:
            referenced_zones = {route.source_zone, route.target_zone}
            if route.transit_zone is not None:
                referenced_zones.add(route.transit_zone)
            unknown_zones = referenced_zones - zone_id_set
            if unknown_zones:
                raise ValueError(f"unknown route zone: {sorted(unknown_zones)[0]}")

        route_id_set = set(route_ids)
        for state in self.initial_states:
            if state.current_route_id is not None and state.current_route_id not in route_id_set:
                raise ValueError(f"unknown state route: {state.current_route_id}")

        for block in self.routine_blocks:
            if block.zone_id not in zone_id_set:
                raise ValueError(f"unknown routine zone: {block.zone_id}")
            if block.route_id is not None and block.route_id not in route_id_set:
                raise ValueError(f"unknown routine route: {block.route_id}")

        for campaign in self.campaigns:
            for placement in campaign.placements:
                if isinstance(placement, PhonePlacement) and placement.zone not in zone_id_set:
                    raise ValueError(f"unknown placement zone: {placement.zone}")
                if (
                    isinstance(placement, BillboardPlacement)
                    and placement.route_id not in route_id_set
                ):
                    raise ValueError(f"unknown placement route: {placement.route_id}")

        edges: set[tuple[str, str]] = set()
        adjacency: dict[str, set[str]] = {agent_id: set() for agent_id in agent_ids}
        for relationship in self.relationships:
            if (
                relationship.source_id not in agent_id_set
                or relationship.target_id not in agent_id_set
            ):
                raise ValueError("relationship endpoint outside population")
            if relationship.source_id == relationship.target_id:
                raise ValueError("self-relationship is not allowed")
            edge = (
                min(relationship.source_id, relationship.target_id),
                max(relationship.source_id, relationship.target_id),
            )
            if edge in edges:
                raise ValueError(f"duplicate relationship: {edge[0]} and {edge[1]}")
            edges.add(edge)
            adjacency[relationship.source_id].add(relationship.target_id)
            adjacency[relationship.target_id].add(relationship.source_id)

        if len(agent_ids) == 2 and any(not neighbors for neighbors in adjacency.values()):
            raise ValueError("each agent must have a relationship when population exceeds one")
        if len(agent_ids) >= 3:
            visited: set[str] = set()
            pending = [agent_ids[0]]
            while pending:
                current = pending.pop()
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(adjacency[current] - visited)
            if visited != agent_id_set:
                raise ValueError("relationship graph must be connected")

        duration_minutes = self.days * 1440
        if any(campaign.end_minute > duration_minutes for campaign in self.campaigns):
            raise ValueError("campaign period is outside simulation duration")

        return self


__all__ = ["Scenario"]
