"""Independent execution binding refuses rehashed but miscompiled assumptions."""

from __future__ import annotations

import pytest

from adlife.city.workbench_binding import validate_workbench_execution_binding
from adlife.city.workbench_input import WorkbenchRunInput, WorkbenchRunInputV2
from adlife.city.workbench_validation import construct_workbench_run
from tests.unit.city.test_workbench_validation import _draft, persona_draft


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
    changed = unchecked_copy(compiled.workbench_input, schema_version=3)
    with pytest.raises(ValueError, match="schema version"):
        bind(compiled, workbench_input=changed)
