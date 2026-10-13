from __future__ import annotations

import json
from copy import deepcopy

from hypothesis import given
from hypothesis import strategies as st

from adlife.city import workbench_input as models
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_workbench_input import (
    normalized_v2_input,
    persona_data,
)


@given(
    order=st.permutations((0, 1, 2)),
    interests=st.permutations(("technology", "commuting")),
)
def test_persona_array_permutations_preserve_assignments_and_complete_input_hash(
    order: list[int], interests: list[str]
) -> None:
    raw = {
        "personas": [
            persona_data(persona_id="persona-003", count=1),
            persona_data(persona_id="persona-001", count=2),
            persona_data(persona_id="persona-002", count=3),
        ]
    }
    permuted = deepcopy(raw)
    permuted["personas"] = [permuted["personas"][i] for i in order]
    for persona in permuted["personas"]:
        persona["interests"] = interests
    values = []
    for payload in (raw, permuted):
        population = models.WorkbenchPopulationDraftV2.model_validate_json(json.dumps(payload))
        materialized = models.materialize_workbench_population(population, agent_count=6)
        value = normalized_v2_input()
        draft = value.draft.model_copy(
            update={
                "population": materialized,
                "settings": value.draft.settings.model_copy(update={"agent_count": 6}),
            }
        )
        values.append(value.model_copy(update={"draft": draft}))
    first, second = values
    assert first.fingerprint == second.fingerprint
    assert canonical_json(first) == canonical_json(second)
    assert [item.model_dump() for item in first.draft.population.assignments] == [
        {"agent_id": "person-001", "persona_id": "persona-001"},
        {"agent_id": "person-002", "persona_id": "persona-001"},
        {"agent_id": "person-003", "persona_id": "persona-002"},
        {"agent_id": "person-004", "persona_id": "persona-002"},
        {"agent_id": "person-005", "persona_id": "persona-002"},
        {"agent_id": "person-006", "persona_id": "persona-003"},
    ]


@given(count=st.integers(min_value=1, max_value=30))
def test_materialization_is_deterministic_and_contiguous(count: int) -> None:
    population = models.WorkbenchPopulationDraftV2.model_validate_json(
        json.dumps({"personas": [persona_data(count=count)]})
    )
    first = models.materialize_workbench_population(population, agent_count=count)
    assert models.materialize_workbench_population(population, agent_count=count) == first
    assert tuple(item.agent_id for item in first.assignments) == tuple(
        f"person-{i:03d}" for i in range(1, count + 1)
    )


def test_persona_id_change_changes_provenance_and_order_dependent_assignment() -> None:
    first = normalized_v2_input()
    raw = {"personas": [persona_data(count=2), persona_data(persona_id="persona-002")]}
    populations = []
    for persona_id in ("persona-001", "persona-003"):
        raw["personas"][0]["persona_id"] = persona_id
        population = models.WorkbenchPopulationDraftV2.model_validate_json(json.dumps(raw))
        populations.append(models.materialize_workbench_population(population, agent_count=3))
    original, changed = populations
    inputs = [
        first.model_copy(update={"draft": first.draft.model_copy(update={"population": p})})
        for p in populations
    ]
    assert inputs[0].fingerprint != inputs[1].fingerprint
    assert original.assignments[0].persona_id == "persona-001"
    assert changed.assignments[0].persona_id == "persona-002"
    assert changed.assignments[1].persona_id == "persona-003"
