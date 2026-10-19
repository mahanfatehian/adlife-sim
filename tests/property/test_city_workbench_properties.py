from __future__ import annotations

import json
from copy import deepcopy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from adlife.city import workbench_input as models
from adlife.core.domain.city_cognition_v2 import CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_workbench_input import (
    normalized_v2_input,
    persona_data,
)


@given(response_count=st.integers(min_value=0, max_value=30))
def test_v10_summary_count_equations_are_strict_and_cannot_be_bypassed(
    response_count: int,
) -> None:
    from adlife.city.workbench_runs import WorkbenchRunSummary

    document = {
        "run_id": "property-v10",
        "run_schema_version": 10,
        "workbench_input_schema_version": 4,
        "city_id": "fictional-grid-v2",
        "city_schema_version": 2,
        "city_sha256": "a" * 64,
        "scenario_id": "launch-study",
        "scenario_sha256": "b" * 64,
        "campaign_ids": ("fictional-launch",),
        "channels": ("mobile-feed",),
        "seed": "42",
        "days": 1,
        "agent_count": 1,
        "frame_count": 1_440,
        "opportunity_count": response_count,
        "impression_count": response_count,
        "noticed_count": response_count,
        "response_count": response_count,
        "effect_scope": "bounded-signals-v1",
        "cognition_mode": "rules",
        "provider_profile_id": None,
        "provider_revision_sha256": None,
        "funded_response_limit": 60,
        "aggregate_output_token_ceiling": 0,
        "eligible_response_count": response_count,
        "selected_request_count": response_count,
        "funded_request_count": response_count,
        "budget_skipped_count": 0,
        "unselected_response_count": 0,
        "applied_response_count": response_count,
        "rules_count": response_count,
        "mock_count": 0,
        "provider_count": 0,
        "cache_count": 0,
        "fallback_count": 0,
        "neutralized_response_count": response_count,
        "uncertain_result_count": 0,
        "safety_neutralized_result_count": 0,
        "inspector_url": "/runs/property-v10",
    }
    summary = WorkbenchRunSummary.model_validate(document)
    assert summary.response_count == response_count
    for field, value in (
        ("applied_response_count", response_count + 1),
        ("selected_request_count", response_count + 1),
        ("rules_count", response_count + 1),
        ("eligible_response_count", True),
    ):
        malformed = dict(document)
        malformed[field] = value
        with pytest.raises(ValidationError):
            WorkbenchRunSummary.model_validate(malformed)


def test_v10_summary_requires_city_v2_and_exact_provider_funding() -> None:
    from adlife.city.workbench_runs import WorkbenchRunSummary

    document = {
        "run_id": "provider-property-v10",
        "run_schema_version": 10,
        "workbench_input_schema_version": 4,
        "city_id": "fictional-grid-v2",
        "city_schema_version": 2,
        "city_sha256": "a" * 64,
        "scenario_id": "launch-study",
        "scenario_sha256": "b" * 64,
        "campaign_ids": ("fictional-launch",),
        "channels": ("mobile-feed",),
        "seed": "42",
        "days": 1,
        "agent_count": 1,
        "frame_count": 1_440,
        "opportunity_count": 12,
        "impression_count": 12,
        "noticed_count": 12,
        "response_count": 12,
        "effect_scope": "bounded-signals-v1",
        "cognition_mode": "local",
        "provider_profile_id": "provider-main",
        "provider_revision_sha256": "c" * 64,
        "funded_response_limit": 10,
        "aggregate_output_token_ceiling": 10 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
        "eligible_response_count": 12,
        "selected_request_count": 12,
        "funded_request_count": 10,
        "budget_skipped_count": 2,
        "unselected_response_count": 0,
        "applied_response_count": 12,
        "rules_count": 0,
        "mock_count": 0,
        "provider_count": 10,
        "cache_count": 0,
        "fallback_count": 0,
        "neutralized_response_count": 2,
        "uncertain_result_count": 0,
        "safety_neutralized_result_count": 0,
        "inspector_url": "/runs/provider-property-v10",
    }
    assert WorkbenchRunSummary.model_validate(document).funded_request_count == 10
    assert WorkbenchRunSummary.model_validate_json(json.dumps(document)).city_schema_version == 2

    wrong_funding = dict(document)
    wrong_funding.update(
        funded_request_count=9,
        budget_skipped_count=3,
        provider_count=9,
        neutralized_response_count=3,
    )
    wrong_city = dict(document)
    wrong_city["city_schema_version"] = 1
    for malformed in (wrong_funding, wrong_city):
        with pytest.raises(ValidationError):
            WorkbenchRunSummary.model_validate(malformed)
        with pytest.raises(ValidationError):
            WorkbenchRunSummary.model_validate_json(json.dumps(malformed))


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
