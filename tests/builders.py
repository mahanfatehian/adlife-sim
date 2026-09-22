"""Shared scenario builders for whole-run tests.

Every Task 12 suite builds its scenarios from the single-agent contract fixture, so a
change to a domain bound shows up once here and not in four files. Builders are pure
functions over a :class:`~adlife.core.domain.scenario.Scenario`.
"""

from __future__ import annotations

from itertools import pairwise

from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Relationship, Route, Zone


def _chain(ids: tuple[str, ...]) -> tuple[Relationship, ...]:
    """The minimal connected graph over the given ordered agent identifiers."""
    return tuple(
        Relationship(source_id=left, target_id=right, kind="friend", strength=0.8)
        for left, right in pairwise(ids)
    )


def small_three_agent_scenario(valid_scenario: Scenario) -> Scenario:
    """The contract scenario scaled to three agents over the documented ten-zone world.

    ``retail-center`` and ``cafe`` are the two zones the built-in office-worker routine
    names beyond the contract fixture's four, and the three routes close the day loop
    back to ``home-north``. Population is three so a relationship graph is optional.
    """
    zones = (
        *valid_scenario.world.zones,
        Zone(zone_id="retail-center", name="Retail Center", kind="retail"),
        Zone(zone_id="cafe", name="Cafe", kind="social"),
    )
    routes = (
        *valid_scenario.world.routes,
        Route(
            route_id="retail-commute",
            source_zone="office",
            target_zone="retail-center",
            transit_zone="highway-north",
            travel_minutes=30,
        ),
        Route(
            route_id="retail-social",
            source_zone="retail-center",
            target_zone="cafe",
            travel_minutes=15,
        ),
        Route(
            route_id="cafe-home",
            source_zone="cafe",
            target_zone="home-north",
            travel_minutes=15,
        ),
    )
    world = valid_scenario.world.model_copy(update={"zones": zones, "routes": routes})

    base = valid_scenario.population[0]
    variations = (
        {},
        {"age": 41, "occupation": "retail-worker"},
        {"age": 22, "occupation": "student"},
    )
    population: list[PersonProfile] = []
    states: list[ConsumerState] = []
    for index, (agent_id, changes) in enumerate(
        zip(("person-001", "person-002", "person-003"), variations, strict=True)
    ):
        profile = base.model_copy(
            update={"agent_id": agent_id, "display_name": f"Arman 00{index + 1}", **changes}
        )
        traits = ConsumerTraits(
            price_sensitivity=round(0.5 + 0.1 * index, 4),
            novelty_seeking=0.6,
            social_susceptibility=round(0.4 + 0.1 * index, 4),
            advertising_skepticism=0.3,
            mobile_attention=round(0.8 - 0.2 * index, 4),
            outdoor_attention=0.5,
            brand_loyalty=0.4,
            impulsivity=round(0.3 + 0.2 * index, 4),
        )
        population.append(
            PersonProfile.model_validate(profile.model_copy(update={"traits": traits}))
        )
        state = valid_scenario.initial_states[0].model_copy(update={"agent_id": agent_id})
        states.append(ConsumerState.model_validate(state))

    return Scenario.model_validate(
        valid_scenario.model_dump()
        | {
            "scenario_id": "scenario-small",
            "name": "Small three-agent scenario",
            "world": world.model_dump(),
            "population": tuple(profile.model_dump() for profile in population),
            "initial_states": tuple(state.model_dump() for state in states),
            "relationships": tuple(
                edge.model_dump() for edge in _chain(("person-001", "person-002", "person-003"))
            ),
        }
    )


def rescale_scenario(scenario: Scenario, *, days: int) -> Scenario:
    return Scenario.model_validate(scenario.model_dump() | {"days": days})


def maximum_thirty_agent_scenario(valid_scenario: Scenario) -> Scenario:
    """The performance gate's population: 30 agents over the routable ten-zone world.

    Built exactly the way the CLI assembles a project - the packaged generator draws
    profiles and a relationship graph, the routable-zone assignment keys every commute
    onto the default world - on top of the small builder's ten-zone world (the contract
    fixture's four zones plus the retail legs the routine templates name). The campaign
    set is the small scenario's own. This is a builder, not a fixture, so the perf
    suite can import it standalone.
    """
    from adlife.cli.project import _assign_routable_zones, _initial_state_for, default_world
    from adlife.core.simulation.population import generate_population, generate_relationships

    profiles = _assign_routable_zones(generate_population(30, 42, "fa-IR"), 42)
    relationships = generate_relationships(profiles, 42)
    return Scenario.model_validate(
        valid_scenario.model_dump()
        | {
            "scenario_id": "scenario-maximum",
            "name": "Maximum thirty-agent scenario",
            "days": 7,
            "world": default_world().model_dump(),
            "population": tuple(profile.model_dump() for profile in profiles),
            "initial_states": tuple(
                _initial_state_for(profile).model_dump() for profile in profiles
            ),
            "relationships": tuple(edge.model_dump() for edge in relationships),
        }
    )


def strip_campaigns(scenario: Scenario) -> Scenario:
    return Scenario.model_validate(scenario.model_dump() | {"campaigns": ()})


def pair_scenario(scenario: Scenario) -> Scenario:
    """Reduce to two agents joined by one friendship, the smallest social population."""
    population = [profile for profile in scenario.population if profile.agent_id != "person-003"]
    states = [state for state in scenario.initial_states if state.agent_id != "person-003"]
    return Scenario.model_validate(
        scenario.model_dump()
        | {
            "scenario_id": "scenario-duo",
            "population": tuple(profile.model_dump() for profile in population),
            "initial_states": tuple(state.model_dump() for state in states),
            "relationships": tuple(
                edge.model_dump()
                for edge in (
                    Relationship(
                        source_id="person-001", target_id="person-002", kind="friend", strength=0.8
                    ),
                )
            ),
        }
    )


# -- the rules-mode cognition harness shared by the experiment suites ---------------


def rule_inputs_for(plan) -> dict:
    """Wire the terminal rule fallback for every request a plan raises."""
    from adlife.adapters.cognition.rules import RuleCognitionInputs

    inputs: dict[str, RuleCognitionInputs] = {}
    for decision in plan.attention:
        if not decision.noticed:
            continue
        opportunity = decision.opportunity
        inputs[decision.events[2].event_id] = RuleCognitionInputs(
            profile=opportunity.profile,
            state=opportunity.state,
            campaign=opportunity.campaign,
            placement=opportunity.placement,
        )
    return inputs


def rule_fallback_for(plan):
    """Build specification section 12's terminal fallback for one tick's requests."""
    from adlife.adapters.cognition.rules import RuleCognitionProvider

    return RuleCognitionProvider.for_requests(plan.requests, rule_inputs_for(plan))


class RuleCognitionPort:
    """The core-side cognition seam for whole-run tests, answering through the rules.

    Every request is answered straight through the terminal fallback the runner builds
    from the plan - which IS the documented rule formula - in-process, with no budget
    and no retry. The runner sees only this port, never an adapter.
    """

    def __init__(self) -> None:
        self.answered: list[object] = []

    async def resolve(self, requests, *, fallback_provider):
        answers = {}
        for request in requests:
            self.answered.append(request)
            answers[request.request_id] = await fallback_provider.answer(request)
        return answers

    @property
    def provider_metadata(self):
        from adlife.adapters.cognition.rules import RULE_MODEL_ID
        from adlife.core.ports.cognition import ProviderMetadata, SamplingSettings

        return ProviderMetadata(
            kind="rule",
            model_id=RULE_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="d" * 64,
        )
