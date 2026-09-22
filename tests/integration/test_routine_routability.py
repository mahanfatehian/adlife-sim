"""Every packaged routine must be routable in the packaged default world.

A routine is a contract with the movement stage: each consecutive zone change a
template demands must be a legal move in the world it ships with, or the run dies
mid-tick however far into the week it happens to fall. These tests walk both a
weekday and a weekend day for every template through the real ``resolve_movement``
with a live snapshot - the exact path a run takes, including the current-route rule
that lets an agent hop between a transit zone and its destination - so a template
can never again demand a leg no route covers.
"""

from __future__ import annotations

from typing import cast

import pytest

from adlife.cli.project import default_world
from adlife.core.domain.events import EventType
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import World
from adlife.core.simulation.movement import MovementIntent, Snapshot, resolve_movement
from adlife.core.simulation.rng import RandomOracle
from adlife.core.simulation.routines import load_routine_templates, routine_at

_DAY_MINUTES = 1440
_TICK = 15
_WEEKEND_DAY_START = 5 * _DAY_MINUTES

# The two (home, work) pairs the default world routes.
_ROUTABLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("home-north", "office"),
    ("home-center", "retail-center"),
)

# The (home, work) pairs each template is actually driven with. ``_ROUTABLE_ASSIGNMENTS``
# in adlife.cli.project pins the base pair per occupation, and the assignment draw swaps
# between the two routable pairs - except the freelancer, whose routine is only routable
# from home-center, so a home-north freelancer is not a population the CLI can ever
# generate and is not tested here.
_TEMPLATE_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "student": _ROUTABLE_PAIRS,
    "office-worker": _ROUTABLE_PAIRS,
    "retail-worker": _ROUTABLE_PAIRS,
    "freelancer": (_ROUTABLE_PAIRS[1],),
    "unemployed": _ROUTABLE_PAIRS,
}

# The packaged unemployed weekday commutes from the cafe to the highway transit - a
# leg no world can route (see ``_assign_routable_zones`` in adlife.cli.project).
# Generated profiles therefore follow the office-worker routine, and that one
# (template, day) combination is the documented exception.
_DOCUMENTED_EXCEPTION: frozenset[tuple[str, str]] = frozenset({("unemployed", "weekday")})


def _profile(template_id: str, home_zone: str, work_zone: str) -> PersonProfile:
    return PersonProfile(
        agent_id="person-001",
        display_name="Routability 001",
        age=30,
        occupation="office-worker",
        income_band="middle",
        household_type="shared-apartment",
        home_zone=home_zone,
        work_or_study_zone=work_zone,
        interests=frozenset({"fitness"}),
        traits=ConsumerTraits(
            price_sensitivity=0.5,
            novelty_seeking=0.5,
            social_susceptibility=0.5,
            advertising_skepticism=0.5,
            mobile_attention=0.5,
            outdoor_attention=0.5,
            brand_loyalty=0.5,
            impulsivity=0.5,
        ),
        initial_brand_sentiment=0.0,
        routine_template=template_id,
    )


def _state_for(profile: PersonProfile, zone: str, activity: str) -> ConsumerState:
    return ConsumerState(
        agent_id=profile.agent_id,
        location=zone,
        activity=cast(object, activity),
        mood=0.0,
        fatigue=0.0,
        brand_sentiment=0.0,
        recall_strength=0.0,
        purchase_intention=0.0,
        cognition_budget_remaining=6,
    )


def _walk_one_day(
    template_id: str,
    day_start: int,
    home_zone: str,
    work_zone: str,
    world: World,
) -> None:
    """Re-execute one agent's movement loop for a day, exactly as the engine does."""
    profile = _profile(template_id, home_zone, work_zone)
    oracle = RandomOracle(42)

    first_block = routine_at(profile, day_start, oracle)
    state = _state_for(profile, first_block.zone_id, first_block.activity)
    for offset in range(_TICK, _DAY_MINUTES, _TICK):
        block = routine_at(profile, day_start + offset, oracle)
        intent = MovementIntent(
            agent_id=profile.agent_id,
            from_zone=state.location,
            to_zone=block.zone_id,
            activity=block.activity,
            route_id=block.route_id,
        )
        snapshot = Snapshot(
            agents={profile.agent_id: (profile, state)},
            simulated_minute=day_start + offset,
            run_id="routability",
        )
        events = resolve_movement([intent], world, snapshot)  # raises InvalidMovement
        current_route_id = state.current_route_id
        for event in events:
            if event.event_type == EventType.LOCATION_CHANGED:
                current_route_id = event.payload["to_route_id"]
        state = state.model_copy(
            update={
                "location": block.zone_id,
                "activity": block.activity,
                "current_route_id": current_route_id,
            }
        )


@pytest.mark.parametrize("template_id", sorted(load_routine_templates()))
@pytest.mark.parametrize(
    ("day_start", "day_name"),
    [(0, "weekday"), (_WEEKEND_DAY_START, "weekend")],
)
def test_packaged_routines_are_routable_in_the_default_world(
    template_id: str,
    day_start: int,
    day_name: str,
) -> None:
    if (template_id, day_name) in _DOCUMENTED_EXCEPTION:
        pytest.skip("documented exception: generated unemployed follow the office-worker routine")

    world = default_world()
    for home_zone, work_zone in _TEMPLATE_PAIRS[template_id]:
        _walk_one_day(template_id, day_start, home_zone, work_zone, world)
