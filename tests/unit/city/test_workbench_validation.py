from __future__ import annotations

import importlib
import importlib.util
import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from adlife.city.catalog import select_catalog_city
from adlife.city.run_store import CityRunStore
from adlife.city.workbench_input import (
    WorkbenchRunDraft,
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV3,
    WorkbenchRunInput,
    WorkbenchRunInputV2,
    WorkbenchRunInputV3,
)
from adlife.core.domain.spatial_campaign import (
    PhoneOpportunityPlacement,
    RoadsideBillboardPlacement,
)
from adlife.core.domain.spatial_response import validate_spatial_response_input
from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data
from tests.unit.city.test_workbench_input import (
    draft_data,
    draft_v2_data,
    draft_v3_data,
    persona_data,
)


def persona_draft() -> WorkbenchRunDraftV2:
    """Two explicitly different causal profiles, submitted in reverse ID order."""
    data = draft_v2_data()
    first = persona_data(count=2)
    second = persona_data(persona_id="persona-002")
    second["interests"] = ["food"]
    second["traits"] = dict.fromkeys(second["traits"], 0.9)
    second["initial_state"] = {
        "brand_sentiment": -0.6,
        "recall_strength": 0.8,
        "purchase_intention": 0.7,
    }
    data["population"]["personas"] = [second, first]
    return WorkbenchRunDraftV2.model_validate_json(json.dumps(data))


def cognition_draft(*, mode: str = "rules", budget: int = 0) -> WorkbenchRunDraftV3:
    return WorkbenchRunDraftV3.model_validate_json(
        json.dumps(
            draft_v3_data(
                mode=mode,
                aggregate_output_token_ceiling=budget,
            )
        )
    )


def test_schema2_compiles_each_assignment_with_its_explicit_causal_values() -> None:
    result = _module().construct_workbench_run(persona_draft())

    assert isinstance(result.workbench_input, WorkbenchRunInputV2)
    assert [
        (a.agent_id, a.persona_id) for a in result.workbench_input.draft.population.assignments
    ] == [
        ("person-001", "persona-001"),
        ("person-002", "persona-001"),
        ("person-003", "persona-002"),
    ]
    assert [profile.interests for profile in result.response_input.profiles] == [
        frozenset({"technology", "commuting"}),
        frozenset({"technology", "commuting"}),
        frozenset({"food"}),
    ]
    assert [p.traits.novelty_seeking for p in result.response_input.profiles] == [0.6, 0.6, 0.9]
    assert result.response_input.profiles[2].traits.model_dump() == {
        "price_sensitivity": 0.9,
        "novelty_seeking": 0.9,
        "advertising_skepticism": 0.9,
        "mobile_recall_encoding": 0.9,
        "roadside_recall_encoding": 0.9,
        "impulsivity": 0.9,
    }
    assert [
        (s.agent_id, s.campaign_id, s.brand_sentiment, s.recall_strength, s.purchase_intention)
        for s in result.response_input.initial_states
    ] == [
        ("person-001", "fictional-launch", 0.0, 0.1, 0.2),
        ("person-002", "fictional-launch", 0.0, 0.1, 0.2),
        ("person-003", "fictional-launch", -0.6, 0.8, 0.7),
    ]
    assert set(result.response_input.profiles[0].model_dump()) == {
        "agent_id",
        "fictional",
        "interests",
        "traits",
    }


@pytest.mark.parametrize(
    ("mode", "budget"),
    [("rules", 0), ("mock", 0), ("local", 3_072), ("remote", 92_160)],
)
def test_schema3_compilation_freezes_cognition_without_changing_schema2_science(
    mode: str,
    budget: int,
) -> None:
    schema2 = _module().construct_workbench_run(
        WorkbenchRunDraftV2.model_validate_json(json.dumps(draft_v2_data()))
    )
    schema3 = _module().construct_workbench_run(cognition_draft(mode=mode, budget=budget))

    assert isinstance(schema3.workbench_input, WorkbenchRunInputV3)
    assert schema3.workbench_input.draft.population == schema2.workbench_input.draft.population
    assert schema3.workbench_input.draft.cognition.mode == mode
    assert schema3.workbench_input.draft.cognition.aggregate_output_token_ceiling == budget
    assert schema3.workbench_input.fingerprint != schema2.workbench_input.fingerprint
    assert schema3.pack == schema2.pack
    assert schema3.mobility.agents == schema2.mobility.agents
    assert schema3.mobility.frame(0) == schema2.mobility.frame(0)
    assert schema3.mobility.frame(2_879) == schema2.mobility.frame(2_879)
    assert schema3.scenario == schema2.scenario
    assert schema3.response_input == schema2.response_input


@pytest.mark.parametrize("count", [1, 30])
def test_schema2_compiles_exact_agent_coverage_at_population_bounds(count: int) -> None:
    draft = WorkbenchRunDraftV2.model_validate_json(json.dumps(draft_v2_data(agent_count=count)))
    result = _module().construct_workbench_run(draft)
    expected = tuple(f"person-{i:03d}" for i in range(1, count + 1))
    assert tuple(p.agent_id for p in result.response_input.profiles) == expected
    assert tuple(s.agent_id for s in result.response_input.initial_states) == expected
    assert tuple(a.agent_id for a in result.mobility.agents) == expected


def test_schema2_compilation_preserves_canonical_persona_order() -> None:
    first = persona_draft()
    data = first.model_dump(mode="json")
    data["population"]["personas"].reverse()
    second = WorkbenchRunDraftV2.model_validate_json(json.dumps(data))
    left, right = (_module().construct_workbench_run(draft) for draft in (first, second))
    assert left.workbench_input.fingerprint == right.workbench_input.fingerprint
    assert left.response_input == right.response_input


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("version", [3, True, 2.0])
def test_compilation_refuses_unvalidated_or_future_draft_versions(
    schema: int, version: object
) -> None:
    draft = _draft() if schema == 1 else persona_draft()
    unsafe = type(draft).model_construct(
        **{
            **{name: getattr(draft, name) for name in type(draft).model_fields},
            "schema_version": version,
        }
    )
    with pytest.raises(ValueError, match="schema_version"):
        _module().construct_workbench_run(unsafe)


def _module():
    spec = importlib.util.find_spec("adlife.city.workbench_validation")
    assert spec is not None, "workbench validation service is not implemented"
    return importlib.import_module("adlife.city.workbench_validation")


def _draft(*, phone: bool = True, roadside: bool = False, agents: int = 3):
    data = draft_data(phone=phone, roadside=roadside)
    data["settings"]["agent_count"] = agents
    if roadside:
        data["scenario"]["roadside"]["road_id"] = "middle-west"
    return WorkbenchRunDraft.model_validate_json(json.dumps(data))


def test_construct_workbench_run_uses_the_exact_pre_resolved_pack_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    selected_pack = select_catalog_city("fictional-grid-v2")

    def refuse_reload(_city_id: str):
        raise AssertionError("pre-resolved city was looked up again")

    monkeypatch.setattr(module, "_load_pack", refuse_reload)

    result = module.construct_workbench_run(_draft(), selected_pack=selected_pack)

    assert result.pack is selected_pack
    assert result.mobility.pack is selected_pack
    assert result.scenario.city_sha256 == selected_pack.fingerprint
    assert result.response_input.city_sha256 == selected_pack.fingerprint


def test_construct_workbench_run_refuses_a_pre_resolved_pack_for_another_city() -> None:
    module = _module()
    selected_pack = select_catalog_city("fictional-grid-v2")
    data = draft_data()
    data["city_id"] = "operator-city"
    draft = WorkbenchRunDraft.model_validate_json(json.dumps(data))

    with pytest.raises(module.WorkbenchValidationError) as captured:
        module.construct_workbench_run(draft, selected_pack=selected_pack)

    assert captured.value.code == "unknown-city"
    assert captured.value.field == "city_id"


def test_construct_workbench_run_keeps_packaged_only_default_behavior() -> None:
    result = _module().construct_workbench_run(_draft())

    assert result.pack.city_id == "fictional-grid-v2"
    assert result.pack == select_catalog_city("fictional-grid-v2")


@pytest.mark.parametrize(
    ("phone", "roadside", "channels"),
    [
        (True, False, {"mobile-feed"}),
        (False, True, {"roadside-billboard"}),
        (True, True, {"mobile-feed", "roadside-billboard"}),
    ],
)
def test_construct_workbench_run_binds_each_supported_placement(
    phone: bool, roadside: bool, channels: set[str]
) -> None:
    result = _module().construct_workbench_run(_draft(phone=phone, roadside=roadside, agents=3))

    assert result.pack.city_id == "fictional-grid-v2"
    assert result.mobility.pack == result.pack
    assert result.scenario.city_sha256 == result.pack.fingerprint
    assert result.scenario.days == 2
    assert {placement.channel for placement in result.scenario.placements} == channels
    assert result.response_input.city_sha256 == result.pack.fingerprint
    assert result.response_input.scenario_sha256 == result.scenario.fingerprint
    assert result.scenario.campaigns[0].creative_sha256 == (
        result.workbench_input.creative_template.fingerprint
    )


@pytest.mark.parametrize("agent_count", [1, 30])
def test_construct_workbench_run_expands_complete_deterministic_response_grid(
    agent_count: int,
) -> None:
    first = _module().construct_workbench_run(_draft(phone=True, roadside=True, agents=agent_count))
    second = _module().construct_workbench_run(
        _draft(phone=True, roadside=True, agents=agent_count)
    )

    assert len(first.mobility.agents) == agent_count
    assert tuple(profile.agent_id for profile in first.response_input.profiles) == tuple(
        f"person-{index:03d}" for index in range(1, agent_count + 1)
    )
    assert len(first.response_input.initial_states) == agent_count
    assert first.workbench_input.fingerprint == second.workbench_input.fingerprint
    assert first.scenario.fingerprint == second.scenario.fingerprint
    assert first.response_input.fingerprint == second.response_input.fingerprint
    assert first.scenario == second.scenario
    assert first.response_input == second.response_input
    validate_spatial_response_input(
        first.response_input,
        first.scenario,
        agent_ids=tuple(agent.agent_id for agent in first.mobility.agents),
    )


def test_construct_workbench_run_normalizes_windows_ids_and_verified_coordinate() -> None:
    result = _module().construct_workbench_run(_draft(phone=True, roadside=True, agents=2))
    normalized = result.workbench_input.draft.scenario
    assert normalized.phone is not None and normalized.roadside is not None
    assert normalized.phone.placement_id == "phone-placement"
    assert normalized.roadside.placement_id == "roadside-placement"
    assert [(item.start_minute, item.end_minute) for item in normalized.phone.active_windows] == [
        (480, 1080),
        (1920, 2520),
    ]
    assert [
        (item.start_minute, item.end_minute) for item in normalized.roadside.active_windows
    ] == [(420, 1140), (1860, 2580)]

    placements = {item.placement_id: item for item in result.scenario.placements}
    phone = placements["phone-placement"]
    roadside = placements["roadside-placement"]
    assert isinstance(phone, PhoneOpportunityPlacement)
    assert phone.opportunity_model == "keyed-activity-minute-v1"
    assert isinstance(roadside, RoadsideBillboardPlacement)
    assert (roadside.longitude, roadside.latitude) == pytest.approx((0.02, 0.034))


@pytest.mark.parametrize(
    ("mutation", "code", "field"),
    [
        ("city", "unknown-city", "city_id"),
        ("template", "unknown-creative-template", "scenario.campaign.creative_template_id"),
        ("road", "invalid-road-placement", "scenario.roadside.road_id"),
        ("day", "invalid-schedule", "scenario.phone.active_windows"),
    ],
)
def test_construct_workbench_run_returns_safe_typed_failures(
    mutation: str, code: str, field: str
) -> None:
    data = draft_data(phone=True, roadside=True)
    data["scenario"]["roadside"]["road_id"] = "middle-west"
    if mutation == "city":
        data["city_id"] = "missing-city"
    elif mutation == "template":
        data["scenario"]["campaign"]["creative_template_id"] = "missing-template"
    elif mutation == "road":
        data["scenario"]["roadside"]["road_id"] = "missing-road"
    else:
        data["settings"]["days"] = 1
    draft = WorkbenchRunDraft.model_validate_json(json.dumps(data))

    with pytest.raises(_module().WorkbenchValidationError) as captured:
        _module().construct_workbench_run(draft)
    assert captured.value.code == code
    assert captured.value.field == field
    rendered = str(captured.value)
    assert "missing" not in rendered
    assert "C:\\" not in rendered


def test_construct_workbench_run_refuses_unsupported_road_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = draft_data(phone=False, roadside=True)
    data["scenario"]["roadside"]["road_id"] = "ab"
    data["scenario"]["roadside"]["travel_direction"] = "backward"
    draft = WorkbenchRunDraft.model_validate_json(json.dumps(data))
    pack_data = pack_v2_data()
    roads = pack_data["roads"]
    assert isinstance(roads, list) and isinstance(roads[0], dict)
    roads[0]["directions"] = ["forward"]
    module = _module()
    monkeypatch.setattr(module, "_load_pack", lambda _city_id: load_pack_v2(pack_data))

    with pytest.raises(module.WorkbenchValidationError) as captured:
        module.construct_workbench_run(draft)

    assert captured.value.code == "invalid-road-placement"
    assert captured.value.field == "scenario.roadside.travel_direction"


def test_construct_workbench_run_creates_no_artifacts_or_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tuple(tmp_path.rglob("*"))

    def refuse_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("validation attempted a network connection")

    def refuse_store(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("validation attempted to instantiate a run store")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(CityRunStore, "__init__", refuse_store)
    monkeypatch.chdir(tmp_path)
    result = _module().construct_workbench_run(_draft(phone=True, roadside=True, agents=2))

    assert result.mobility.agents
    assert tuple(tmp_path.rglob("*")) == before


def test_constructed_input_refuses_creative_or_response_binding_drift() -> None:
    result = _module().construct_workbench_run(_draft(phone=True, roadside=False, agents=2))
    other = deepcopy(result.workbench_input.creative_template.model_dump(mode="python"))
    other["template_id"] = "fictional-crispy-meal-v1"
    with pytest.raises(ValidationError, match="creative template"):
        WorkbenchRunInput(
            schema_version=1,
            draft=result.workbench_input.draft,
            creative_template=other,
        )

    incomplete = result.response_input.model_dump(mode="python")
    incomplete["initial_states"] = incomplete["initial_states"][:-1]
    with pytest.raises(ValidationError, match="every profile"):
        type(result.response_input).model_validate(incomplete)
