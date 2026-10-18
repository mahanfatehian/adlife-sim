"""Independent execution binding refuses rehashed but miscompiled assumptions."""

from __future__ import annotations

import json

import pytest

from adlife.city.workbench_binding import (
    build_city_cognition_run_context,
    validate_workbench_execution_binding,
)
from adlife.city.workbench_input import (
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV3,
    WorkbenchRunInput,
    WorkbenchRunInputV2,
    WorkbenchRunInputV3,
)
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.simulation.city_cognition import build_city_cognition_requests
from adlife.core.simulation.spatial_attention import evaluate_spatial_attention
from adlife.core.simulation.spatial_opportunity import evaluate_spatial_opportunities
from adlife.core.simulation.spatial_response import evaluate_spatial_responses
from tests.unit.city.test_workbench_input import draft_data, draft_v3_data
from tests.unit.city.test_workbench_validation import _draft, cognition_draft, persona_draft


def unchecked_copy(model, **changes):
    """Represent corrupt in-memory evidence without invoking normal copy validation."""
    return type(model).model_construct(
        **{
            **{name: getattr(model, name) for name in type(model).model_fields},
            **changes,
        }
    )


def bind(compiled, *, workbench_input=None, response=None):
    settings = compiled.workbench_input.draft.settings
    return validate_workbench_execution_binding(
        compiled.workbench_input if workbench_input is None else workbench_input,
        pack=compiled.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        places=None,
        spatial_scenario=compiled.scenario,
        spatial_response=compiled.response_input if response is None else response,
    )


@pytest.mark.parametrize("schema", [1, 2])
def test_execution_binding_preserves_each_supported_input_schema(schema: int) -> None:
    compiled = construct_workbench_run(_draft() if schema == 1 else persona_draft())
    bound = bind(compiled)
    assert type(bound) is (WorkbenchRunInput if schema == 1 else WorkbenchRunInputV2)
    assert bound == compiled.workbench_input
    assert bound is not compiled.workbench_input


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("field", ["profiles", "initial_states"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "misassigned"])
def test_binding_refuses_missing_extra_duplicate_or_misassigned_scientific_agents(
    schema: int, field: str, mutation: str
) -> None:
    compiled = construct_workbench_run(_draft() if schema == 1 else persona_draft())
    items = list(getattr(compiled.response_input, field))
    if mutation == "missing":
        items.pop()
    elif mutation == "extra":
        items.append(items[-1].model_copy(update={"agent_id": "person-004"}))
    elif mutation == "duplicate":
        items[-1] = items[0]
    else:
        items[0] = items[0].model_copy(update={"agent_id": "person-004"})
    changed = unchecked_copy(compiled.response_input, **{field: tuple(items)})
    with pytest.raises(ValueError, match=r"population|state"):
        bind(compiled, response=changed)


@pytest.mark.parametrize("field", ["interests", "traits", "initial_state"])
def test_schema2_binding_refuses_causal_values_assigned_to_the_wrong_agent(field: str) -> None:
    compiled = construct_workbench_run(persona_draft())
    response = compiled.response_input
    collection = "initial_states" if field == "initial_state" else "profiles"
    items = list(getattr(response, collection))
    if field == "initial_state":
        values = items[-1].model_dump(exclude={"agent_id"})
    else:
        values = {field: getattr(items[-1], field)}
    items[0] = items[0].model_copy(update=values)
    with pytest.raises(ValueError, match=r"population|state"):
        bind(compiled, response=response.model_copy(update={collection: tuple(items)}))


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "unknown", "swapped"])
def test_schema2_binding_revalidates_frozen_persona_assignments(mutation: str) -> None:
    compiled = construct_workbench_run(persona_draft())
    value = compiled.workbench_input
    population = value.draft.population
    assignments = list(population.assignments)
    if mutation == "missing":
        assignments.pop()
    elif mutation == "extra":
        assignments.append(assignments[-1].model_copy(update={"agent_id": "person-004"}))
    elif mutation == "duplicate":
        assignments[-1] = assignments[0]
    elif mutation == "unknown":
        assignments[0] = assignments[0].model_copy(update={"persona_id": "persona-030"})
    else:
        assignments[0] = assignments[0].model_copy(update={"persona_id": "persona-002"})
        assignments[-1] = assignments[-1].model_copy(update={"persona_id": "persona-001"})
    changed = unchecked_copy(
        value,
        draft=unchecked_copy(
            value.draft,
            population=unchecked_copy(population, assignments=tuple(assignments)),
        ),
    )
    assert changed.fingerprint != value.fingerprint
    with pytest.raises(ValueError, match="assignment"):
        bind(compiled, workbench_input=changed)


@pytest.mark.parametrize("schema", [1, 2])
def test_binding_refuses_future_input_schema(schema: int) -> None:
    compiled = construct_workbench_run(_draft() if schema == 1 else persona_draft())
    changed = unchecked_copy(compiled.workbench_input, schema_version=5)
    with pytest.raises(ValueError, match="schema version"):
        bind(compiled, workbench_input=changed)


def _schema3_evidence(draft=None):
    compiled = construct_workbench_run(cognition_draft() if draft is None else draft)
    assert isinstance(compiled.workbench_input, WorkbenchRunInputV3)
    opportunities = evaluate_spatial_opportunities(compiled.mobility, compiled.scenario)
    attention = evaluate_spatial_attention(
        opportunities,
        seed=compiled.workbench_input.draft.settings.seed,
    )
    responses = evaluate_spatial_responses(
        compiled.response_input,
        compiled.scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
    )
    return compiled, responses


def cognition_context(
    compiled,
    responses,
    *,
    workbench_input=None,
    pack=None,
    scenario=None,
    response_input=None,
):
    return build_city_cognition_run_context(
        compiled.workbench_input if workbench_input is None else workbench_input,
        pack=compiled.pack if pack is None else pack,
        spatial_scenario=compiled.scenario if scenario is None else scenario,
        response_input=compiled.response_input if response_input is None else response_input,
        response_evaluation=responses,
    )


def test_schema3_cognition_context_reconstructs_and_binds_every_scientific_identity() -> None:
    compiled, responses = _schema3_evidence()

    context = cognition_context(compiled, responses)

    assignments = compiled.workbench_input.draft.population.assignments
    campaign = compiled.workbench_input.draft.scenario.campaign
    assert context.run_input_sha256 == compiled.workbench_input.fingerprint
    assert context.response_input_sha256 == compiled.response_input.fingerprint
    assert context.response_input_sha256 == responses.response_input_sha256
    assert context.scenario_sha256 == compiled.scenario.fingerprint == responses.scenario_sha256
    assert context.city_sha256 == compiled.pack.fingerprint == responses.city_sha256
    assert tuple((item.agent_id, item.persona_id) for item in context.agents) == tuple(
        (item.agent_id, item.persona_id) for item in assignments
    )
    assert tuple(item.campaign_id for item in context.campaigns) == (campaign.campaign_id,)
    assert (
        context.campaigns[0].creative_sha256
        == compiled.workbench_input.creative_template.fingerprint
    )
    expected_grid = {(assignment.agent_id, campaign.campaign_id) for assignment in assignments}
    assert {
        (state.agent_id, state.campaign_id) for state in responses.final_states
    } == expected_grid


def test_cognition_context_requires_exact_schema3_input_and_revalidates_both_models() -> None:
    compiled, responses = _schema3_evidence()
    schema2 = construct_workbench_run(persona_draft())
    with pytest.raises(TypeError, match="schema-3"):
        cognition_context(compiled, responses, workbench_input=schema2.workbench_input)

    corrupt_input = type(compiled.workbench_input).model_construct(
        **{
            **{
                field: getattr(compiled.workbench_input, field)
                for field in type(compiled.workbench_input).model_fields
            },
            "schema_version": 5,
        }
    )
    with pytest.raises(ValueError, match="schema version"):
        cognition_context(compiled, responses, workbench_input=corrupt_input)

    corrupt_evaluation = type(responses).model_construct(
        **{
            **{field: getattr(responses, field) for field in type(responses).model_fields},
            "final_states": tuple(reversed(responses.final_states)),
        }
    )
    with pytest.raises(ValueError, match="scientific execution"):
        cognition_context(compiled, corrupt_evaluation)


@pytest.mark.parametrize("field", ["traits", "initial_state"])
def test_cognition_context_refuses_response_evidence_from_different_causal_population(
    field: str,
) -> None:
    compiled, responses = _schema3_evidence()
    input_document = compiled.workbench_input.model_dump(mode="python")
    persona = input_document["draft"]["population"]["personas"][0]
    if field == "traits":
        persona["traits"]["novelty_seeking"] = 0.1
    else:
        persona["initial_state"]["brand_sentiment"] = 0.5
    changed = WorkbenchRunInputV3.model_validate(input_document)

    with pytest.raises(ValueError, match=r"response population|initial response state"):
        cognition_context(compiled, responses, workbench_input=changed)


def test_cognition_context_descriptive_persona_changes_only_the_run_binding() -> None:
    compiled, responses = _schema3_evidence()
    document = compiled.workbench_input.model_dump(mode="python")
    document["draft"]["population"]["personas"][0]["background"] = (
        "A different invented background with no causal authority."
    )
    changed = WorkbenchRunInputV3.model_validate(document)

    original = cognition_context(compiled, responses)
    updated = cognition_context(compiled, responses, workbench_input=changed)

    assert updated.run_input_sha256 == changed.fingerprint
    assert updated.run_input_sha256 != original.run_input_sha256
    assert updated.response_input_sha256 == original.response_input_sha256
    assert updated.scenario_sha256 == original.scenario_sha256
    assert updated.city_sha256 == original.city_sha256
    assert updated.agents[0].background != original.agents[0].background


def test_schema2_and_schema3_science_match_but_run_bound_request_ids_do_not() -> None:
    source = cognition_draft()
    schema2 = construct_workbench_run(
        WorkbenchRunDraftV2(
            city_id=source.city_id,
            scenario=source.scenario,
            population=source.population,
            settings=source.settings,
        )
    )
    schema3, responses = _schema3_evidence()
    assert schema2.response_input == schema3.response_input
    assert schema2.scenario == schema3.scenario
    assert schema2.workbench_input.fingerprint != schema3.workbench_input.fingerprint

    schema3_context = cognition_context(schema3, responses)
    schema2_bound_context = schema3_context.model_copy(
        update={"run_input_sha256": schema2.workbench_input.fingerprint}
    )
    schema3_requests = build_city_cognition_requests(
        schema3_context,
        schema3.mobility,
        responses,
    )
    schema2_bound_requests = build_city_cognition_requests(
        schema2_bound_context,
        schema3.mobility,
        responses,
    )

    assert [item.response_event_id for item in schema2_bound_requests] == [
        item.response_event_id for item in schema3_requests
    ]
    assert [item.request_id for item in schema2_bound_requests] != [
        item.request_id for item in schema3_requests
    ]


def test_cognition_context_exposes_no_provider_selection_beyond_bound_run_hash() -> None:
    compiled = construct_workbench_run(cognition_draft(mode="remote", budget=1_536))
    opportunities = evaluate_spatial_opportunities(compiled.mobility, compiled.scenario)
    attention = evaluate_spatial_attention(
        opportunities, seed=compiled.workbench_input.draft.settings.seed
    )
    responses = evaluate_spatial_responses(
        compiled.response_input,
        compiled.scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
    )

    context = cognition_context(compiled, responses)
    document = context.model_dump(mode="json")

    assert set(document) == {
        "schema_version",
        "run_input_sha256",
        "response_input_sha256",
        "scenario_sha256",
        "city_sha256",
        "agents",
        "campaigns",
    }
    assert "provider-main" not in str(document)
    assert "a" * 64 not in str(document)


def test_cognition_context_refuses_zero_response_final_state_drift() -> None:
    draft = cognition_draft()
    phone = draft.scenario.phone
    assert phone is not None
    no_opportunity_draft = draft.model_copy(
        update={
            "scenario": draft.scenario.model_copy(
                update={
                    "phone": phone.model_copy(update={"opportunity_probability_per_minute": 0.0})
                }
            )
        }
    )
    compiled = construct_workbench_run(no_opportunity_draft)
    opportunities = evaluate_spatial_opportunities(compiled.mobility, compiled.scenario)
    attention = evaluate_spatial_attention(opportunities, seed=draft.settings.seed)
    responses = evaluate_spatial_responses(
        compiled.response_input,
        compiled.scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
    )
    assert responses.counts.response_count == 0
    changed_states = list(responses.final_states)
    changed_states[0] = changed_states[0].model_copy(update={"brand_sentiment": 0.5})
    changed = responses.model_copy(update={"final_states": tuple(changed_states)})

    with pytest.raises(ValueError, match="initial response state"):
        cognition_context(compiled, changed)


def test_cognition_context_refuses_evaluation_from_a_different_attention_seed() -> None:
    compiled, _responses = _schema3_evidence()
    opportunities = evaluate_spatial_opportunities(compiled.mobility, compiled.scenario)
    attention = evaluate_spatial_attention(
        opportunities,
        seed=compiled.workbench_input.draft.settings.seed + 1,
    )
    changed = evaluate_spatial_responses(
        compiled.response_input,
        compiled.scenario,
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in compiled.mobility.agents),
    )

    with pytest.raises(ValueError, match="response seed"):
        cognition_context(compiled, changed)


@pytest.mark.parametrize("artifact", ["pack", "scenario", "response-input", "evaluation"])
def test_cognition_context_revalidates_every_supplied_scientific_artifact(
    artifact: str,
) -> None:
    compiled, responses = _schema3_evidence()
    kwargs = {}
    evaluation = responses
    if artifact == "pack":
        kwargs["pack"] = unchecked_copy(compiled.pack, schema_version=1)
    elif artifact == "scenario":
        kwargs["scenario"] = unchecked_copy(compiled.scenario, schema_version=2)
    elif artifact == "response-input":
        kwargs["response_input"] = unchecked_copy(compiled.response_input, schema_version=2)
    else:
        evaluation = unchecked_copy(responses, schema_version=2)

    with pytest.raises(ValueError, match="scientific execution"):
        cognition_context(compiled, evaluation, **kwargs)


def test_cognition_context_refuses_changed_phone_probability() -> None:
    compiled, responses = _schema3_evidence()
    phone = next(
        placement
        for placement in compiled.scenario.placements
        if placement.placement_id == "phone-placement"
    )
    changed_phone = phone.model_copy(update={"opportunity_probability_per_minute": 0.25})
    changed_scenario = type(compiled.scenario).model_validate(
        {
            **compiled.scenario.model_dump(mode="python"),
            "placements": tuple(
                changed_phone if placement.placement_id == "phone-placement" else placement
                for placement in compiled.scenario.placements
            ),
        }
    )

    with pytest.raises(ValueError, match="phone placement"):
        cognition_context(compiled, responses, scenario=changed_scenario)


def test_cognition_context_refuses_changed_scenario_name() -> None:
    compiled, responses = _schema3_evidence()
    changed_scenario = type(compiled.scenario).model_validate(
        {
            **compiled.scenario.model_dump(mode="python"),
            "name": "Different fictional scenario",
        }
    )

    with pytest.raises(ValueError, match="scenario does not match"):
        cognition_context(compiled, responses, scenario=changed_scenario)


def test_cognition_context_refuses_crossed_roadside_pack_or_placement_geometry() -> None:
    document = draft_v3_data()
    document["scenario"]["roadside"] = draft_data(phone=False, roadside=True)["scenario"][
        "roadside"
    ]
    document["scenario"]["roadside"]["road_id"] = "middle-west"
    draft = WorkbenchRunDraftV3.model_validate_json(json.dumps(document))
    compiled, responses = _schema3_evidence(draft)

    pack_document = compiled.pack.model_dump(mode="python")
    road = next(item for item in pack_document["roads"] if item["road_id"] == "middle-west")
    road["shape"][0]["latitude"] = 0.033
    changed_pack = type(compiled.pack).model_validate(pack_document)
    assert changed_pack.fingerprint != compiled.pack.fingerprint
    with pytest.raises(ValueError, match="scenario does not match"):
        cognition_context(compiled, responses, pack=changed_pack)

    roadside = next(
        placement
        for placement in compiled.scenario.placements
        if placement.placement_id == "roadside-placement"
    )
    changed_roadside = roadside.model_copy(update={"longitude": roadside.longitude + 0.001})
    changed_scenario = type(compiled.scenario).model_validate(
        {
            **compiled.scenario.model_dump(mode="python"),
            "placements": tuple(
                changed_roadside if placement.placement_id == "roadside-placement" else placement
                for placement in compiled.scenario.placements
            ),
        }
    )
    with pytest.raises(ValueError, match="roadside placement"):
        cognition_context(compiled, responses, scenario=changed_scenario)


def test_cognition_context_refuses_evaluation_from_another_scientific_execution() -> None:
    compiled, responses = _schema3_evidence()
    changed_document = draft_v3_data()
    changed_document["scenario"]["phone"]["opportunity_probability_per_minute"] = 0.25
    changed_draft = WorkbenchRunDraftV3.model_validate_json(json.dumps(changed_document))
    _changed_compiled, changed_responses = _schema3_evidence(changed_draft)

    with pytest.raises(ValueError, match="evaluation does not match execution artifacts"):
        cognition_context(compiled, changed_responses)

    assert changed_responses.response_input_sha256 != responses.response_input_sha256


def test_schema3_natural_slash_prose_remains_buildable_as_cognition_context() -> None:
    document = draft_v3_data()
    persona = document["population"]["personas"][0]
    persona["occupation"] = "Food/retail planner"
    persona["background"] = (
        "Balances home/work routines and/or weekend plans with fictional friends."
    )
    document["scenario"]["campaign"]["name"] = "Food/retail launch"
    draft = WorkbenchRunDraftV3.model_validate_json(json.dumps(document))
    compiled, responses = _schema3_evidence(draft)

    context = cognition_context(compiled, responses)

    assert context.agents[0].occupation == "Food/retail planner"
    assert context.agents[0].background == persona["background"]
    assert context.campaigns[0].name == "Food/retail launch"
