import pytest
from hypothesis import given
from hypothesis import strategies as st

from adlife.core.domain.events import EventType
from adlife.core.domain.world import Route, World, Zone
from adlife.core.simulation import movement


def _linear_world() -> World:
    return World(
        world_id="linear-world",
        zones=(
            Zone(zone_id="zone-a", name="Zone A", kind="home"),
            Zone(zone_id="zone-b", name="Zone B", kind="work"),
            Zone(zone_id="zone-c", name="Zone C", kind="retail"),
        ),
        routes=(
            Route(
                route_id="route-ab",
                source_zone="zone-a",
                target_zone="zone-b",
                travel_minutes=15,
            ),
            Route(
                route_id="route-bc",
                source_zone="zone-b",
                target_zone="zone-c",
                travel_minutes=15,
            ),
        ),
    )


def _fan_world() -> World:
    return World(
        world_id="fan-world",
        zones=(
            Zone(zone_id="zone-a", name="Zone A", kind="home"),
            Zone(zone_id="zone-b", name="Zone B", kind="work"),
            Zone(zone_id="zone-c", name="Zone C", kind="retail"),
            Zone(zone_id="zone-d", name="Zone D", kind="social"),
        ),
        routes=(
            Route(
                route_id="route-ab",
                source_zone="zone-a",
                target_zone="zone-b",
                travel_minutes=15,
            ),
            Route(
                route_id="route-ac",
                source_zone="zone-a",
                target_zone="zone-c",
                travel_minutes=15,
            ),
            Route(
                route_id="route-ad",
                source_zone="zone-a",
                target_zone="zone-d",
                travel_minutes=15,
            ),
        ),
    )


@given(reverse=st.booleans())
def test_connected_path_never_allows_a_two_route_teleport(reverse: bool) -> None:
    source, target = ("zone-c", "zone-a") if reverse else ("zone-a", "zone-c")
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone=source,
        to_zone=target,
        activity="shopping",
    )

    with pytest.raises(movement.InvalidMovement, match="no route"):
        movement.resolve_movement((intent,), _linear_world())


@given(
    zone=st.sampled_from(("zone-a", "zone-b", "zone-c")),
    activity=st.sampled_from(("sleep", "work", "shopping", "leisure")),
)
def test_remaining_in_any_zone_never_emits_a_location_change(
    zone: str,
    activity: str,
) -> None:
    intent = movement.MovementIntent(
        agent_id="person-001",
        from_zone=zone,
        to_zone=zone,
        activity=activity,
    )

    events = movement.resolve_movement((intent,), _linear_world())

    assert all(event.event_type != EventType.LOCATION_CHANGED for event in events)


@given(order=st.permutations((0, 1, 2)))
def test_every_intent_permutation_produces_identical_events(
    order: list[int],
) -> None:
    intents = (
        movement.MovementIntent(
            agent_id="person-003",
            from_zone="zone-a",
            to_zone="zone-d",
            activity="socializing",
        ),
        movement.MovementIntent(
            agent_id="person-001",
            from_zone="zone-a",
            to_zone="zone-b",
            activity="work",
        ),
        movement.MovementIntent(
            agent_id="person-002",
            from_zone="zone-a",
            to_zone="zone-c",
            activity="shopping",
        ),
    )
    permuted = tuple(intents[index] for index in order)

    events = movement.resolve_movement(permuted, _fan_world())
    canonical = movement.resolve_movement(intents, _fan_world())

    assert events == canonical
    assert [(event.event_type, event.agent_id) for event in events] == [
        (EventType.ACTIVITY_CHANGED, "person-001"),
        (EventType.ACTIVITY_CHANGED, "person-002"),
        (EventType.ACTIVITY_CHANGED, "person-003"),
        (EventType.LOCATION_CHANGED, "person-001"),
        (EventType.LOCATION_CHANGED, "person-002"),
        (EventType.LOCATION_CHANGED, "person-003"),
    ]
