import pytest
from pydantic import ValidationError

from adlife.core.domain.campaign import BillboardPlacement, TimeWindow
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState, ExposureCount, Memory
from adlife.core.domain.world import Relationship, RoutineBlock, World


def scenario_data(scenario: Scenario) -> dict[str, object]:
    return scenario.model_dump(mode="python")


def state_for(profile: PersonProfile) -> ConsumerState:
    return ConsumerState(
        agent_id=profile.agent_id,
        location=profile.home_zone,
        activity="sleep",
        mood=0.0,
        fatigue=0.0,
        brand_sentiment=profile.initial_brand_sentiment,
        recall_strength=0.0,
        purchase_intention=0.0,
        cognition_budget_remaining=6,
    )


@pytest.mark.parametrize(
    "field",
    [
        "price_sensitivity",
        "novelty_seeking",
        "social_susceptibility",
        "advertising_skepticism",
        "mobile_attention",
        "outdoor_attention",
        "brand_loyalty",
        "impulsivity",
    ],
)
@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_each_trait_is_bounded(
    valid_profile: PersonProfile,
    field: str,
    value: float,
) -> None:
    data = valid_profile.traits.model_dump(mode="python")
    data[field] = value

    with pytest.raises(ValidationError):
        ConsumerTraits.model_validate(data)


def test_traits_reject_string_coercion(valid_profile: PersonProfile) -> None:
    data = valid_profile.traits.model_dump(mode="python")
    data["price_sensitivity"] = "0.5"

    with pytest.raises(ValidationError):
        ConsumerTraits.model_validate(data)


def test_profile_is_fictional_and_serializable(valid_profile: PersonProfile) -> None:
    assert valid_profile.fictional is True
    restored = PersonProfile.model_validate_json(valid_profile.model_dump_json())
    assert restored == valid_profile


def test_profile_rejects_nonfictional_identity(valid_profile: PersonProfile) -> None:
    data = valid_profile.model_dump(mode="python")
    data["fictional"] = False

    with pytest.raises(ValidationError):
        PersonProfile.model_validate(data)


@pytest.mark.parametrize("age", [17, 66])
def test_profile_age_is_bounded(valid_profile: PersonProfile, age: int) -> None:
    data = valid_profile.model_dump(mode="python")
    data["age"] = age

    with pytest.raises(ValidationError):
        PersonProfile.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "person@example.com"),
        ("household_type", "+1 202 555 0198"),
        ("routine_template", "national-id-1234567890"),
        ("interests", frozenset({"api_key=example-secret-value"})),
    ],
)
def test_profile_rejects_sensitive_persona_text(
    valid_profile: PersonProfile,
    field: str,
    value: object,
) -> None:
    data = valid_profile.model_dump(mode="python")
    data[field] = value

    with pytest.raises(ValidationError, match="sensitive identifier or secret"):
        PersonProfile.model_validate(data)


def test_profile_forbids_extra_fields(valid_profile: PersonProfile) -> None:
    data = valid_profile.model_dump(mode="python")
    data["email"] = "person@example.com"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PersonProfile.model_validate(data)


def test_profile_is_frozen(valid_profile: PersonProfile) -> None:
    with pytest.raises(ValidationError, match="Instance is frozen"):
        valid_profile.age = 30


def test_profile_copy_rejects_bytes_for_strict_string(
    valid_profile: PersonProfile,
) -> None:
    with pytest.raises(ValidationError):
        valid_profile.model_copy(update={"display_name": b"Arman 001"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mood", 1.01),
        ("fatigue", -0.01),
        ("brand_sentiment", -1.01),
        ("recall_strength", 1.01),
        ("purchase_intention", 1.01),
        ("ad_fatigue", 1.01),
        ("daily_reinforcement", 0.21),
        ("social_proof", -1.01),
        ("cognition_budget_remaining", 7),
        ("disposable_budget", -0.01),
    ],
)
def test_consumer_state_values_are_bounded(
    consumer_state: ConsumerState,
    field: str,
    value: object,
) -> None:
    data = consumer_state.model_dump(mode="python")
    data[field] = value

    with pytest.raises(ValidationError):
        ConsumerState.model_validate(data)


def test_consumer_state_updates_by_copy(consumer_state: ConsumerState) -> None:
    updated = consumer_state.model_copy(update={"mood": 0.5})

    assert consumer_state.mood == 0.0
    assert updated.mood == 0.5
    with pytest.raises(ValidationError, match="Instance is frozen"):
        consumer_state.mood = 0.5


@pytest.mark.parametrize(
    "update",
    [
        {"mood": 2.0},
        {"mood": "0.5"},
        {"unexpected_state": True},
    ],
)
def test_consumer_state_copy_revalidates_updates(
    consumer_state: ConsumerState,
    update: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        consumer_state.model_copy(update=update)


def test_consumer_state_copy_rejects_mutable_collection_updates(
    consumer_state: ConsumerState,
) -> None:
    memory = Memory(
        memory_id="memory-001",
        created_minute=15,
        kind="advertising",
        summary="Noticed a fictional campaign.",
        salience=0.5,
        campaign_id="campaign-phone",
        caused_by_event_ids=("event-001",),
    )

    with pytest.raises(ValidationError):
        consumer_state.model_copy(update={"memories": [memory]})


def test_consumer_state_copy_accepts_tuple_collection_updates(
    consumer_state: ConsumerState,
) -> None:
    memory = Memory(
        memory_id="memory-001",
        created_minute=15,
        kind="advertising",
        summary="Noticed a fictional campaign.",
        salience=0.5,
        campaign_id="campaign-phone",
        caused_by_event_ids=("event-001",),
    )

    updated = consumer_state.model_copy(update={"memories": (memory,)})

    assert updated.memories == (memory,)
    assert isinstance(updated.memories, tuple)


@pytest.mark.parametrize(
    ("field", "value"),
    [("memory_id", ""), ("created_minute", 10081), ("salience", 1.01)],
)
def test_memory_values_are_bounded(field: str, value: object) -> None:
    data: dict[str, object] = {
        "memory_id": "memory-001",
        "created_minute": 15,
        "kind": "advertising",
        "summary": "Noticed a fictional campaign.",
        "salience": 0.5,
        "caused_by_event_ids": ("event-001",),
    }
    data[field] = value

    with pytest.raises(ValidationError):
        Memory.model_validate(data)


def test_consumer_state_holds_at_most_five_memories(
    consumer_state: ConsumerState,
) -> None:
    memories = tuple(
        Memory(
            memory_id=f"memory-{index:03d}",
            created_minute=index * 15,
            kind="reflection",
            summary=f"Fictional reflection {index}",
            salience=0.5,
            caused_by_event_ids=(f"event-{index:03d}",),
        )
        for index in range(6)
    )
    data = consumer_state.model_dump(mode="python")
    data["memories"] = memories

    with pytest.raises(ValidationError):
        ConsumerState.model_validate(data)


def test_consumer_state_rejects_duplicate_exposure_counts(
    consumer_state: ConsumerState,
) -> None:
    duplicate = ExposureCount(
        campaign_id="campaign-phone",
        channel="mobile-feed",
        count=3,
    )
    data = consumer_state.model_dump(mode="python")
    data["exposure_counts"] = (duplicate, duplicate)

    with pytest.raises(ValidationError, match="duplicate exposure count"):
        ConsumerState.model_validate(data)


def test_exposure_count_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ExposureCount(
            campaign_id="campaign-phone",
            channel="mobile-feed",
            count=15,
        )


def test_exposure_count_lookup_returns_zero_for_missing_pair(
    consumer_state: ConsumerState,
) -> None:
    state = consumer_state.model_copy(
        update={
            "exposure_counts": (
                ExposureCount(
                    campaign_id="campaign-phone",
                    channel="mobile-feed",
                    count=2,
                ),
            )
        }
    )

    assert state.exposure_count("campaign-phone", "mobile-feed") == 2
    assert state.exposure_count("campaign-phone", "highway-billboard") == 0


def test_world_round_trips_with_schema_version(valid_scenario: Scenario) -> None:
    restored = World.model_validate_json(valid_scenario.world.model_dump_json())

    assert restored == valid_scenario.world
    assert restored.schema_version == 1


def test_routine_block_is_versioned(valid_scenario: Scenario) -> None:
    assert valid_scenario.routine_blocks[0].schema_version == 1


def test_routine_block_requires_forward_time_window() -> None:
    with pytest.raises(ValidationError, match="end_minute_of_day must be after"):
        RoutineBlock(
            template_id="office-worker",
            day_type="weekday",
            start_minute_of_day=600,
            end_minute_of_day=600,
            activity="work",
            zone_id="office",
        )


def test_scenario_rejects_duplicate_agent_ids(valid_scenario: Scenario) -> None:
    profile = valid_scenario.population[0]
    data = scenario_data(valid_scenario)
    data["population"] = (profile, profile.model_copy(update={"display_name": "Arman copy"}))
    data["initial_states"] = (
        valid_scenario.initial_states[0],
        valid_scenario.initial_states[0],
    )

    with pytest.raises(ValidationError, match="duplicate agent_id"):
        Scenario.model_validate(data)


def test_scenario_rejects_duplicate_state_ids(valid_scenario: Scenario) -> None:
    profile = valid_scenario.population[0]
    second = profile.model_copy(update={"agent_id": "person-002", "display_name": "Mina 002"})
    data = scenario_data(valid_scenario)
    data["population"] = (profile, second)
    data["initial_states"] = (
        valid_scenario.initial_states[0],
        valid_scenario.initial_states[0],
    )

    with pytest.raises(ValidationError, match="duplicate initial state agent_id"):
        Scenario.model_validate(data)


def test_scenario_rejects_duplicate_zone_ids(valid_scenario: Scenario) -> None:
    world = valid_scenario.world
    data = scenario_data(valid_scenario)
    data["world"] = world.model_copy(update={"zones": (*world.zones, world.zones[0])})

    with pytest.raises(ValidationError, match="duplicate zone_id"):
        Scenario.model_validate(data)


def test_scenario_rejects_duplicate_route_ids(valid_scenario: Scenario) -> None:
    world = valid_scenario.world
    data = scenario_data(valid_scenario)
    data["world"] = world.model_copy(update={"routes": (*world.routes, world.routes[0])})

    with pytest.raises(ValidationError, match="duplicate route_id"):
        Scenario.model_validate(data)


def test_scenario_rejects_duplicate_campaign_ids(valid_scenario: Scenario) -> None:
    campaign = valid_scenario.campaigns[0]
    data = scenario_data(valid_scenario)
    data["campaigns"] = (campaign, campaign.model_copy(update={"name": "Copy"}))

    with pytest.raises(ValidationError, match="duplicate campaign_id"):
        Scenario.model_validate(data)


def test_scenario_rejects_profile_zone_outside_world(valid_scenario: Scenario) -> None:
    profile = valid_scenario.population[0].model_copy(update={"home_zone": "unknown-zone"})
    data = scenario_data(valid_scenario)
    data["population"] = (profile,)

    with pytest.raises(ValidationError, match="unknown home zone"):
        Scenario.model_validate(data)


def test_scenario_rejects_profile_work_zone_outside_world(
    valid_scenario: Scenario,
) -> None:
    profile = valid_scenario.population[0].model_copy(update={"work_or_study_zone": "unknown-zone"})
    data = scenario_data(valid_scenario)
    data["population"] = (profile,)

    with pytest.raises(ValidationError, match="unknown work or study zone"):
        Scenario.model_validate(data)


def test_scenario_rejects_state_location_outside_world(valid_scenario: Scenario) -> None:
    state = valid_scenario.initial_states[0].model_copy(update={"location": "unknown-zone"})
    data = scenario_data(valid_scenario)
    data["initial_states"] = (state,)

    with pytest.raises(ValidationError, match="unknown state location"):
        Scenario.model_validate(data)


def test_scenario_rejects_state_route_outside_world(valid_scenario: Scenario) -> None:
    state = valid_scenario.initial_states[0].model_copy(
        update={"current_route_id": "unknown-route"}
    )
    data = scenario_data(valid_scenario)
    data["initial_states"] = (state,)

    with pytest.raises(ValidationError, match="unknown state route"):
        Scenario.model_validate(data)


def test_scenario_rejects_route_zone_outside_world(valid_scenario: Scenario) -> None:
    route = valid_scenario.world.routes[0].model_copy(update={"target_zone": "unknown-zone"})
    world = valid_scenario.world.model_copy(update={"routes": (route,)})
    data = scenario_data(valid_scenario)
    data["world"] = world

    with pytest.raises(ValidationError, match="unknown route zone"):
        Scenario.model_validate(data)


def test_scenario_rejects_routine_zone_outside_world(valid_scenario: Scenario) -> None:
    block = valid_scenario.routine_blocks[0].model_copy(update={"zone_id": "unknown-zone"})
    data = scenario_data(valid_scenario)
    data["routine_blocks"] = (block,)

    with pytest.raises(ValidationError, match="unknown routine zone"):
        Scenario.model_validate(data)


def test_scenario_rejects_routine_route_outside_world(valid_scenario: Scenario) -> None:
    block = valid_scenario.routine_blocks[0].model_copy(update={"route_id": "unknown-route"})
    data = scenario_data(valid_scenario)
    data["routine_blocks"] = (block,)

    with pytest.raises(ValidationError, match="unknown routine route"):
        Scenario.model_validate(data)


def test_scenario_rejects_billboard_route_outside_world(valid_scenario: Scenario) -> None:
    campaign = valid_scenario.campaigns[0].model_copy(
        update={
            "placements": (
                BillboardPlacement(
                    channel="highway-billboard",
                    route_id="unknown-route",
                    active_windows=(TimeWindow(start_minute_of_day=420, end_minute_of_day=600),),
                    frequency_cap=3,
                    visibility=0.8,
                ),
            )
        }
    )
    data = scenario_data(valid_scenario)
    data["campaigns"] = (campaign,)

    with pytest.raises(ValidationError, match="unknown placement route"):
        Scenario.model_validate(data)


def test_scenario_rejects_phone_zone_outside_world(valid_scenario: Scenario) -> None:
    world = valid_scenario.world
    zones = tuple(zone for zone in world.zones if zone.zone_id != "online")
    data = scenario_data(valid_scenario)
    data["world"] = world.model_copy(update={"zones": zones})

    with pytest.raises(ValidationError, match="unknown placement zone"):
        Scenario.model_validate(data)


def test_scenario_rejects_relationship_endpoint_outside_population(
    valid_scenario: Scenario,
) -> None:
    data = scenario_data(valid_scenario)
    data["relationships"] = (
        Relationship(
            source_id="person-001",
            target_id="person-999",
            kind="friend",
            strength=0.8,
        ),
    )

    with pytest.raises(ValidationError, match="relationship endpoint outside population"):
        Scenario.model_validate(data)


def test_scenario_rejects_self_relationship(valid_scenario: Scenario) -> None:
    data = scenario_data(valid_scenario)
    data["relationships"] = (
        Relationship(
            source_id="person-001",
            target_id="person-001",
            kind="friend",
            strength=0.8,
        ),
    )

    with pytest.raises(ValidationError, match="self-relationship"):
        Scenario.model_validate(data)


def test_scenario_rejects_duplicate_undirected_relationship(valid_scenario: Scenario) -> None:
    profile = valid_scenario.population[0]
    second = profile.model_copy(update={"agent_id": "person-002", "display_name": "Mina 002"})
    edge = Relationship(
        source_id="person-001",
        target_id="person-002",
        kind="friend",
        strength=0.8,
    )
    reverse = edge.model_copy(update={"source_id": "person-002", "target_id": "person-001"})
    data = scenario_data(valid_scenario)
    data["population"] = (profile, second)
    data["initial_states"] = (valid_scenario.initial_states[0], state_for(second))
    data["relationships"] = (edge, reverse)

    with pytest.raises(ValidationError, match="duplicate relationship"):
        Scenario.model_validate(data)


def test_scenario_requires_connection_for_two_agents(valid_scenario: Scenario) -> None:
    profile = valid_scenario.population[0]
    second = profile.model_copy(update={"agent_id": "person-002", "display_name": "Mina 002"})
    data = scenario_data(valid_scenario)
    data["population"] = (profile, second)
    data["initial_states"] = (valid_scenario.initial_states[0], state_for(second))
    data["relationships"] = ()

    with pytest.raises(ValidationError, match="each agent must have a relationship"):
        Scenario.model_validate(data)


def test_scenario_rejects_disconnected_graph_with_three_agents(
    valid_scenario: Scenario,
) -> None:
    first = valid_scenario.population[0]
    second = first.model_copy(update={"agent_id": "person-002", "display_name": "Mina 002"})
    third = first.model_copy(update={"agent_id": "person-003", "display_name": "Nima 003"})
    data = scenario_data(valid_scenario)
    data["population"] = (first, second, third)
    data["initial_states"] = (
        valid_scenario.initial_states[0],
        state_for(second),
        state_for(third),
    )
    data["relationships"] = (
        Relationship(
            source_id="person-001",
            target_id="person-002",
            kind="friend",
            strength=0.8,
        ),
    )

    with pytest.raises(ValidationError, match="relationship graph must be connected"):
        Scenario.model_validate(data)


def test_scenario_accepts_connected_graph_with_three_agents(
    valid_scenario: Scenario,
) -> None:
    first = valid_scenario.population[0]
    second = first.model_copy(update={"agent_id": "person-002", "display_name": "Mina 002"})
    third = first.model_copy(update={"agent_id": "person-003", "display_name": "Nima 003"})
    data = scenario_data(valid_scenario)
    data["population"] = (first, second, third)
    data["initial_states"] = (
        valid_scenario.initial_states[0],
        state_for(second),
        state_for(third),
    )
    data["relationships"] = (
        Relationship(
            source_id="person-001",
            target_id="person-002",
            kind="friend",
            strength=0.8,
        ),
        Relationship(
            source_id="person-002",
            target_id="person-003",
            kind="colleague",
            strength=0.6,
        ),
    )

    restored = Scenario.model_validate(data)

    assert {profile.agent_id for profile in restored.population} == {
        "person-001",
        "person-002",
        "person-003",
    }


def test_scenario_rejects_campaign_beyond_duration(valid_scenario: Scenario) -> None:
    campaign = valid_scenario.campaigns[0].model_copy(update={"end_minute": 1441})
    data = scenario_data(valid_scenario)
    data["campaigns"] = (campaign,)

    with pytest.raises(ValidationError, match="outside simulation duration"):
        Scenario.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("days", 0), ("days", 8), ("tick_minutes", 30)],
)
def test_scenario_rejects_invalid_duration_or_tick(
    valid_scenario: Scenario,
    field: str,
    value: int,
) -> None:
    data = scenario_data(valid_scenario)
    data["campaigns"] = ()
    data[field] = value

    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_scenario_requires_at_least_one_agent(valid_scenario: Scenario) -> None:
    data = scenario_data(valid_scenario)
    data["population"] = ()
    data["initial_states"] = ()

    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_scenario_rejects_more_than_thirty_agents(valid_scenario: Scenario) -> None:
    template = valid_scenario.population[0]
    population = tuple(
        template.model_copy(
            update={"agent_id": f"person-{index:03d}", "display_name": f"Person {index:03d}"}
        )
        for index in range(1, 32)
    )
    data = scenario_data(valid_scenario)
    data["population"] = population
    data["initial_states"] = tuple(state_for(profile) for profile in population)

    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_scenario_requires_one_state_per_agent(valid_scenario: Scenario) -> None:
    data = scenario_data(valid_scenario)
    data["initial_states"] = ()

    with pytest.raises(ValidationError, match="initial states must match population"):
        Scenario.model_validate(data)


def test_scenario_rejects_unknown_schema_version(valid_scenario: Scenario) -> None:
    data = scenario_data(valid_scenario)
    data["schema_version"] = 2

    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_scenario_is_serializable_and_frozen(valid_scenario: Scenario) -> None:
    restored = Scenario.model_validate_json(valid_scenario.model_dump_json())

    assert restored == valid_scenario
    with pytest.raises(ValidationError, match="Instance is frozen"):
        valid_scenario.days = 2
