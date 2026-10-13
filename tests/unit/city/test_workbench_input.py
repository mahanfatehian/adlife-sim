from __future__ import annotations

import importlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.core.domain.json_values import MAX_JSON_DEPTH
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialActiveWindow
from adlife.core.domain.spatial_response import SpatialResponseTraits


def _models():
    spec = importlib.util.find_spec("adlife.city.workbench_input")
    assert spec is not None, "workbench input contracts are not implemented"
    return importlib.import_module("adlife.city.workbench_input")


def _creatives():
    spec = importlib.util.find_spec("adlife.city.workbench_creatives")
    assert spec is not None, "workbench creative catalog is not implemented"
    return importlib.import_module("adlife.city.workbench_creatives")


def draft_data(*, phone: bool = True, roadside: bool = False) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "scenario_id": "launch-study",
        "name": "Fictional launch study",
        "campaign": {
            "campaign_id": "fictional-launch",
            "name": "Fictional launch",
            "creative_template_id": "fictional-device-launch-v1",
            "target_interests": ["technology", "commuting"],
            "relative_price": 1.25,
        },
        "phone": None,
        "roadside": None,
    }
    if phone:
        scenario["phone"] = {
            "active_windows": [
                {"day": 2, "start_minute": 480, "end_minute": 1080},
                {"day": 1, "start_minute": 480, "end_minute": 1080},
            ],
            "frequency_cap_per_agent_per_day": 3,
            "eligible_activities": ["leisure", "commute"],
            "opportunity_probability_per_minute": 0.05,
        }
    if roadside:
        scenario["roadside"] = {
            "active_windows": [
                {"day": 2, "start_minute": 420, "end_minute": 1140},
                {"day": 1, "start_minute": 420, "end_minute": 1140},
            ],
            "frequency_cap_per_agent_per_day": 2,
            "road_id": "road-main",
            "travel_direction": "forward",
            "road_fraction": 0.5,
            "side": "right",
            "orientation_degrees": 90.0,
            "max_view_distance_meters": 120.0,
        }
    return {
        "schema_version": 1,
        "city_id": "fictional-grid-v2",
        "scenario": scenario,
        "cohort": {
            "interests": ["technology", "commuting"],
            "traits": {
                "price_sensitivity": 0.5,
                "novelty_seeking": 0.6,
                "advertising_skepticism": 0.4,
                "mobile_recall_encoding": 0.7,
                "roadside_recall_encoding": 0.6,
                "impulsivity": 0.3,
            },
            "initial_state": {
                "brand_sentiment": 0.0,
                "recall_strength": 0.1,
                "purchase_intention": 0.2,
            },
        },
        "settings": {
            "run_id": "launch-run",
            "agent_count": 20,
            "days": 2,
            "seed": 42,
            "response_mode": "deterministic-rules",
        },
    }


def _parse_draft(data: dict[str, Any]):
    return _models().WorkbenchRunDraft.model_validate_json(json.dumps(data))


def test_wire_draft_accepts_each_supported_placement_shape_and_is_canonical() -> None:
    models = _models()
    for phone, roadside in ((True, False), (False, True), (True, True)):
        draft = _parse_draft(draft_data(phone=phone, roadside=roadside))
        assert isinstance(draft, models.WorkbenchRunDraft)
        assert draft.scenario.phone is not None if phone else draft.scenario.phone is None
        assert draft.scenario.roadside is not None if roadside else draft.scenario.roadside is None

    draft = _parse_draft(draft_data())
    assert draft.scenario.campaign.target_interests == frozenset({"commuting", "technology"})
    assert draft.scenario.phone is not None
    assert draft.scenario.phone.eligible_activities == ("commute", "leisure")
    assert tuple(window.day for window in draft.scenario.phone.active_windows) == (1, 2)
    assert canonical_json(draft) == canonical_json(
        models.WorkbenchRunDraft.model_validate_json(canonical_json(draft))
    )


def test_wire_draft_rejects_missing_placement_and_client_owned_derived_fields() -> None:
    missing = draft_data(phone=False, roadside=False)
    with pytest.raises(ValidationError, match="placement"):
        _parse_draft(missing)

    for path, value in (
        (("scenario", "campaign", "message"), "client copy"),
        (("scenario", "campaign", "creative_sha256"), "f" * 64),
        (("scenario", "roadside", "longitude"), 51.0),
        (("scenario", "roadside", "latitude"), 35.0),
        (("scenario", "roadside", "placement_id"), "client-placement"),
        (("cohort", "agent_ids"), ["person-000"]),
        (("cohort", "fictional"), True),
    ):
        candidate = draft_data(phone=False, roadside=True)
        target: Any = candidate
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = value
        with pytest.raises(ValidationError, match="extra"):
            _parse_draft(candidate)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), True),
        (("schema_version",), 2),
        (("settings", "agent_count"), True),
        (("settings", "agent_count"), 31),
        (("settings", "days"), 0),
        (("settings", "seed"), -1),
        (("settings", "seed"), 2**63),
        (("settings", "response_mode"), "hybrid"),
        (("scenario", "campaign", "relative_price"), float("nan")),
        (("scenario", "campaign", "relative_price"), float("inf")),
        (("scenario", "phone", "opportunity_probability_per_minute"), 1.01),
        (("scenario", "roadside", "road_fraction"), 0.0),
        (("scenario", "roadside", "orientation_degrees"), 360.0),
        (("cohort", "initial_state", "brand_sentiment"), -1.01),
        (("cohort", "initial_state", "recall_strength"), True),
        (("cohort", "traits", "impulsivity"), 1.01),
    ],
)
def test_wire_draft_rejects_wrong_literals_types_and_non_finite_numbers(
    path: tuple[str, ...], value: object
) -> None:
    candidate = draft_data(phone=True, roadside=True)
    target: Any = candidate
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        _parse_draft(candidate)


@pytest.mark.parametrize(
    "value",
    [
        "person@example.org",
        "Authorization: Bearer secret-token-1234567890",
        "api_key=secret-token-1234567890",
        "unsafe\u202evalue",
        "unsafe\x00value",
        "   ",
    ],
)
def test_wire_draft_rejects_private_credential_and_control_text(value: str) -> None:
    for path in (
        ("scenario", "name"),
        ("scenario", "campaign", "name"),
        ("scenario", "campaign", "target_interests", 0),
        ("cohort", "interests", 0),
    ):
        candidate = draft_data()
        target: Any = candidate
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = value
        with pytest.raises(ValidationError):
            _parse_draft(candidate)


@pytest.mark.parametrize(
    "path",
    [
        ("city_id",),
        ("scenario", "scenario_id"),
        ("scenario", "campaign", "campaign_id"),
        ("scenario", "campaign", "creative_template_id"),
        ("scenario", "roadside", "road_id"),
        ("settings", "run_id"),
    ],
)
def test_wire_draft_rejects_credential_shaped_identifiers(path: tuple[str, ...]) -> None:
    candidate = draft_data(phone=True, roadside=True)
    target: Any = candidate
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = "sk-" + "a" * 32

    with pytest.raises(ValidationError, match="credential"):
        _parse_draft(candidate)


@pytest.mark.parametrize("value", [[{}], [[]]])
def test_wire_draft_maps_unhashable_activity_members_to_validation_errors(
    value: list[object],
) -> None:
    candidate = draft_data()
    candidate["scenario"]["phone"]["eligible_activities"] = value

    with pytest.raises(ValidationError):
        _parse_draft(candidate)


def test_wire_draft_rejects_duplicate_and_overlapping_members() -> None:
    duplicate_interest = draft_data()
    duplicate_interest["cohort"]["interests"] = ["technology", "technology"]
    with pytest.raises(ValidationError, match="unique"):
        _parse_draft(duplicate_interest)

    duplicate_activity = draft_data()
    duplicate_activity["scenario"]["phone"]["eligible_activities"] = [
        "commute",
        "commute",
    ]
    with pytest.raises(ValidationError, match="unique"):
        _parse_draft(duplicate_activity)

    overlap = draft_data()
    overlap["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 100, "end_minute": 200},
        {"day": 1, "start_minute": 199, "end_minute": 300},
    ]
    with pytest.raises(ValidationError, match="overlap"):
        _parse_draft(overlap)

    backward = draft_data()
    backward["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 200, "end_minute": 200}
    ]
    with pytest.raises(ValidationError, match="after"):
        _parse_draft(backward)


def _normalized_draft():
    models = _models()
    wire = _parse_draft(draft_data(phone=True, roadside=True))
    phone = wire.scenario.phone
    roadside = wire.scenario.roadside
    assert phone is not None and roadside is not None
    return models.NormalizedWorkbenchRunDraft(
        schema_version=1,
        city_id=wire.city_id,
        scenario=models.NormalizedWorkbenchScenarioDraft(
            scenario_id=wire.scenario.scenario_id,
            name=wire.scenario.name,
            campaign=wire.scenario.campaign,
            phone=models.NormalizedPhonePlacementDraft(
                placement_id="phone-placement",
                active_windows=(
                    SpatialActiveWindow(start_minute=480, end_minute=1080),
                    SpatialActiveWindow(start_minute=1920, end_minute=2520),
                ),
                frequency_cap_per_agent_per_day=3,
                eligible_activities=("commute", "leisure"),
                opportunity_probability_per_minute=0.05,
            ),
            roadside=models.NormalizedRoadsidePlacementDraft(
                placement_id="roadside-placement",
                active_windows=(
                    SpatialActiveWindow(start_minute=420, end_minute=1140),
                    SpatialActiveWindow(start_minute=1860, end_minute=2580),
                ),
                frequency_cap_per_agent_per_day=2,
                road_id="road-main",
                travel_direction="forward",
                road_fraction=0.5,
                side="right",
                orientation_degrees=90.0,
                max_view_distance_meters=120.0,
            ),
        ),
        cohort=wire.cohort,
        settings=wire.settings,
    )


@pytest.mark.parametrize(
    "path",
    [
        ("city_id",),
        ("scenario", "scenario_id"),
        ("scenario", "campaign", "campaign_id"),
        ("scenario", "campaign", "creative_template_id"),
        ("scenario", "roadside", "road_id"),
        ("settings", "run_id"),
    ],
)
def test_normalized_draft_rejects_credential_shaped_identifiers(
    path: tuple[str, ...],
) -> None:
    models = _models()
    candidate = _normalized_draft().model_dump(mode="json")
    target: Any = candidate
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = "sk-" + "a" * 32

    with pytest.raises(ValidationError, match="credential"):
        models.NormalizedWorkbenchRunDraft.model_validate_json(json.dumps(candidate))


def test_normalized_input_is_frozen_canonical_and_hashes_complete_template() -> None:
    models = _models()
    template = models.CreativeTemplate(
        template_id="fictional-device-launch-v1",
        template_version=1,
        product_name="Lumen Pocket",
        product_category="fictional consumer device",
        message="A fictional compact device for a synthetic commute.",
        call_to_action="Explore the fictional concept",
        disclosure="Fictional creative for synthetic simulation only.",
    )
    value = models.WorkbenchRunInput(
        schema_version=1,
        draft=_normalized_draft(),
        creative_template=template,
    )
    restored = models.WorkbenchRunInput.model_validate_json(canonical_json(value))

    assert restored == value
    assert restored.fingerprint == value.fingerprint
    assert len(value.fingerprint) == 64
    changed_template = template.model_copy(update={"message": "Changed fictional message."})
    changed = value.model_copy(update={"creative_template": changed_template})
    assert changed.fingerprint != value.fingerprint
    with pytest.raises(ValidationError):
        models.NormalizedPhonePlacementDraft(
            placement_id="client-chosen",
            active_windows=(SpatialActiveWindow(start_minute=0, end_minute=1),),
            frequency_cap_per_agent_per_day=1,
            eligible_activities=("commute",),
            opportunity_probability_per_minute=0.5,
        )


def test_persisted_workbench_input_parser_round_trips_strict_canonical_model() -> None:
    models = _models()
    template = models.CreativeTemplate(
        template_id="fictional-device-launch-v1",
        template_version=1,
        product_name="Lumen Pocket",
        product_category="fictional consumer device",
        message="A fictional compact device for a synthetic commute.",
        call_to_action="Explore the fictional concept",
        disclosure="Fictional creative for synthetic simulation only.",
    )
    value = models.WorkbenchRunInput(
        schema_version=1,
        draft=_normalized_draft(),
        creative_template=template,
    )

    assert models.parse_workbench_run_input_json(canonical_json(value)) == value
    assert models.parse_workbench_run_input_json(canonical_json(value).encode("utf-8")) == value


@pytest.mark.parametrize(
    "document",
    [
        b"\xff",
        b"{",
        b"[]",
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":1,"draft":NaN}',
    ],
)
def test_persisted_workbench_input_parser_refuses_invalid_json(document: bytes) -> None:
    models = _models()
    with pytest.raises((ValidationError, ValueError)):
        models.parse_workbench_run_input_json(document)


def test_persisted_workbench_input_parser_refuses_unknown_fields_and_creative_drift() -> None:
    models = _models()
    template = models.CreativeTemplate(
        template_id="fictional-device-launch-v1",
        template_version=1,
        product_name="Lumen Pocket",
        product_category="fictional consumer device",
        message="A fictional compact device for a synthetic commute.",
        call_to_action="Explore the fictional concept",
        disclosure="Fictional creative for synthetic simulation only.",
    )
    value = models.WorkbenchRunInput(
        draft=_normalized_draft(),
        creative_template=template,
    ).model_dump(mode="json")

    with_extra = deepcopy(value)
    with_extra["request_headers"] = {"authorization": "redacted"}
    with pytest.raises(ValidationError):
        models.parse_workbench_run_input_json(json.dumps(with_extra))

    mismatch = deepcopy(value)
    mismatch["creative_template"]["template_id"] = "fictional-cafe-launch-v1"
    with pytest.raises(ValidationError, match="does not match"):
        models.parse_workbench_run_input_json(json.dumps(mismatch))


def test_packaged_creative_catalog_is_bounded_safe_sorted_and_selectable() -> None:
    creatives = _creatives()
    catalog = creatives.load_creative_template_catalog()

    assert catalog.schema_version == 1
    assert len(catalog.templates) >= 2
    assert tuple(item.template_id for item in catalog.templates) == tuple(
        sorted(item.template_id for item in catalog.templates)
    )
    assert all(
        item.disclosure == "Fictional creative for synthetic simulation only."
        for item in catalog.templates
    )
    selected = creatives.select_creative_template(catalog.templates[0].template_id)
    assert selected == catalog.templates[0]
    assert len(selected.fingerprint) == 64
    with pytest.raises(creatives.UnknownCreativeTemplate, match="unknown"):
        creatives.select_creative_template("missing-template")


def test_creative_template_rejects_credential_shaped_identifier() -> None:
    models = _models()

    with pytest.raises(ValidationError, match="credential"):
        models.CreativeTemplate(
            template_id="sk-" + "a" * 32,
            template_version=1,
            product_name="Fictional product",
            product_category="fictional category",
            message="Fictional message.",
            call_to_action="Explore the fictional concept",
            disclosure="Fictional creative for synthetic simulation only.",
        )


def test_creative_catalog_refuses_oversize_duplicates_and_unsafe_text(tmp_path: Path) -> None:
    creatives = _creatives()
    resource = tmp_path / "creative_templates.json"
    resource.write_bytes(b"{" + b" " * creatives.MAX_CREATIVE_TEMPLATE_CATALOG_BYTES + b"}")
    with pytest.raises(creatives.CreativeTemplateCatalogError, match="size"):
        creatives.load_creative_template_catalog(root=tmp_path)

    valid = json.loads(
        (Path(__file__).parents[3] / "src/adlife/city/creative_templates.json").read_text(
            encoding="utf-8"
        )
    )
    valid["templates"].append(deepcopy(valid["templates"][0]))
    resource.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(creatives.CreativeTemplateCatalogError, match="schema"):
        creatives.load_creative_template_catalog(root=tmp_path)

    valid["templates"].pop()
    valid["templates"][0]["message"] = "api_key=secret-token-1234567890"
    resource.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(creatives.CreativeTemplateCatalogError, match="schema"):
        creatives.load_creative_template_catalog(root=tmp_path)


def test_traits_contract_used_by_wire_draft_is_the_existing_domain_type() -> None:
    draft = _parse_draft(draft_data())
    assert isinstance(draft.cohort.traits, SpatialResponseTraits)


def persona_data(*, persona_id: str = "persona-001", count: int = 1) -> dict[str, Any]:
    return {
        **draft_data()["cohort"],
        "persona_id": persona_id,
        "count": count,
        "fictional": True,
        "display_name": "Fictional Mina",
        "age": 28,
        "occupation": "Fictional designer",
        "background": "An invented person who enjoys synthetic city scenarios.",
        "mbti": "INTJ",
    }


def draft_v2_data(*, agent_count: int = 3) -> dict[str, Any]:
    data = draft_data()
    data.pop("cohort")
    data["schema_version"] = 2
    data["settings"]["agent_count"] = agent_count
    data["population"] = {"personas": [persona_data(count=agent_count)]}
    return data


def normalized_v2_input():
    models = _models()
    wire = models.WorkbenchRunDraftV2.model_validate_json(json.dumps(draft_v2_data()))
    normalized = models.NormalizedWorkbenchRunDraftV2(
        city_id=wire.city_id,
        scenario=_normalized_draft().scenario,
        population=models.materialize_workbench_population(
            wire.population, agent_count=wire.settings.agent_count
        ),
        settings=wire.settings,
    )
    return models.WorkbenchRunInputV2(
        draft=normalized,
        creative_template=_creatives().select_creative_template("fictional-device-launch-v1"),
    )


@pytest.mark.parametrize(
    "mbti",
    [
        "INTJ",
        "INTP",
        "ENTJ",
        "ENTP",
        "INFJ",
        "INFP",
        "ENFJ",
        "ENFP",
        "ISTJ",
        "ISFJ",
        "ESTJ",
        "ESFJ",
        "ISTP",
        "ISFP",
        "ESTP",
        "ESFP",
        None,
    ],
)
def test_persona_v2_accepts_exact_mbti_and_existing_causal_contracts(mbti: str | None) -> None:
    data = persona_data()
    data["mbti"] = mbti
    persona = _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))
    assert persona.mbti == mbti
    assert isinstance(persona.traits, SpatialResponseTraits)
    assert json.loads(canonical_json(persona))["interests"] == ["commuting", "technology"]
    with pytest.raises(ValidationError, match="frozen"):
        persona.display_name = "Changed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fictional", False),
        ("fictional", 1),
        ("fictional", "true"),
        ("persona_id", "persona-000"),
        ("persona_id", "persona-01"),
        ("persona_id", "person-001"),
        ("persona_id", "persona-1000"),
        ("count", True),
        ("count", 1.0),
        ("count", "1"),
        ("count", 0),
        ("count", 31),
        ("display_name", ""),
        ("display_name", "a" * 81),
        ("display_name", 7),
        ("age", True),
        ("age", 18.0),
        ("age", "28"),
        ("age", 17),
        ("age", 66),
        ("occupation", ""),
        ("occupation", "a" * 81),
        ("background", "a" * 801),
        ("background", 1),
        ("mbti", "intj"),
        ("mbti", "XXXX"),
        ("mbti", 1),
        ("interests", []),
        ("interests", ["same", "same"]),
        ("interests", ["a" * 81]),
        ("interests", [str(i) for i in range(13)]),
        ("unexpected", "refused"),
    ],
)
def test_persona_v2_refuses_wrong_types_ranges_and_unknown_fields(
    field: str, value: object
) -> None:
    data = persona_data()
    data[field] = value
    with pytest.raises(ValidationError):
        _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))


def test_persona_v2_optional_context_and_mandatory_fictional_disclosure() -> None:
    data = persona_data()
    for field in ("age", "occupation", "background", "mbti"):
        data.pop(field)
    persona = _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))
    assert (persona.age, persona.occupation, persona.background, persona.mbti) == (None,) * 4
    data.pop("fictional")
    with pytest.raises(ValidationError):
        _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf"), -1.01, 1.01])
@pytest.mark.parametrize("field", list(draft_data()["cohort"]["traits"]))
def test_persona_v2_traits_refuse_bool_nonfinite_and_out_of_bounds(
    field: str, value: object
) -> None:
    data = persona_data()
    data["traits"][field] = value
    with pytest.raises(ValidationError):
        _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field", ["brand_sentiment", "recall_strength", "purchase_intention"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf"), -1.01, 1.01])
def test_persona_v2_state_refuses_bool_nonfinite_and_out_of_bounds(
    field: str, value: object
) -> None:
    data = persona_data()
    data["initial_state"][field] = value
    with pytest.raises(ValidationError):
        _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("count", [1, 30])
def test_v2_materialization_covers_agent_boundaries(count: int) -> None:
    models = _models()
    draft = models.WorkbenchRunDraftV2.model_validate_json(
        json.dumps(draft_v2_data(agent_count=count))
    )
    population = models.materialize_workbench_population(draft.population, agent_count=count)
    assert [item.model_dump() for item in population.assignments] == [
        {"agent_id": f"person-{index:03d}", "persona_id": "persona-001"}
        for index in range(1, count + 1)
    ]
    assert (
        models.NormalizedWorkbenchPopulationV2.model_validate_json(canonical_json(population))
        == population
    )


@pytest.mark.parametrize(
    "personas",
    [
        [],
        [persona_data(), persona_data()],
        [persona_data(count=30), persona_data(persona_id="persona-002")],
    ],
)
def test_population_v2_rejects_empty_duplicate_ids_and_excess_total(personas: list[object]) -> None:
    with pytest.raises(ValidationError):
        _models().WorkbenchPopulationDraftV2.model_validate_json(json.dumps({"personas": personas}))


@pytest.mark.parametrize("agent_count", [True, 0, 31, 2])
def test_materialization_v2_refuses_invalid_or_mismatched_agent_count(agent_count: object) -> None:
    models = _models()
    population = models.WorkbenchPopulationDraftV2.model_validate_json(
        json.dumps({"personas": [persona_data()]})
    )
    with pytest.raises(ValueError):
        models.materialize_workbench_population(population, agent_count=agent_count)


@pytest.mark.parametrize("version", [True, 1, 2.0, "2", 3])
def test_schema_v2_requires_exact_integer_version_at_every_document_layer(version: object) -> None:
    models = _models()
    wire = draft_v2_data()
    wire["schema_version"] = version
    with pytest.raises(ValidationError):
        models.WorkbenchRunDraftV2.model_validate_json(json.dumps(wire))
    value = normalized_v2_input().model_dump(mode="json")
    for model, candidate in (
        (models.WorkbenchRunInputV2, {**value, "schema_version": version}),
        (models.NormalizedWorkbenchRunDraftV2, {**value["draft"], "schema_version": version}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(candidate))


def test_v2_draft_and_normalized_draft_bind_population_total_to_settings() -> None:
    wire = draft_v2_data()
    wire["settings"]["agent_count"] = 2
    with pytest.raises(ValidationError, match="count"):
        _models().WorkbenchRunDraftV2.model_validate_json(json.dumps(wire))
    normalized = normalized_v2_input().draft.model_dump(mode="json")
    normalized["settings"]["agent_count"] = 2
    with pytest.raises(ValidationError, match="count"):
        _models().NormalizedWorkbenchRunDraftV2.model_validate_json(json.dumps(normalized))


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "duplicate", "wrong-persona", "gap", "swapped"]
)
def test_normalized_v2_rejects_incomplete_or_noncanonical_assignments(mutation: str) -> None:
    models = _models()
    population = models.WorkbenchPopulationDraftV2.model_validate_json(
        json.dumps({"personas": [persona_data(count=2), persona_data(persona_id="persona-002")]})
    )
    data = models.materialize_workbench_population(population, agent_count=3).model_dump(
        mode="json"
    )
    assignments = data["assignments"]
    if mutation == "missing":
        assignments.pop()
    elif mutation == "extra":
        assignments.append({"agent_id": "person-004", "persona_id": "persona-002"})
    elif mutation == "duplicate":
        assignments[1] = assignments[0]
    elif mutation == "wrong-persona":
        assignments[0]["persona_id"] = "persona-999"
    elif mutation == "gap":
        assignments[0]["agent_id"] = "person-000"
    else:
        assignments[1]["persona_id"], assignments[2]["persona_id"] = (
            assignments[2]["persona_id"],
            assignments[1]["persona_id"],
        )
    with pytest.raises(ValidationError):
        models.NormalizedWorkbenchPopulationV2.model_validate_json(json.dumps(data))


def test_persisted_v2_input_round_trip_hash_creative_binding_and_exact_dispatch() -> None:
    models = _models()
    value = normalized_v2_input()
    assert models.parse_workbench_run_input_json(canonical_json(value)) == value
    assert models.parse_workbench_run_input_json(canonical_json(value).encode("utf-8")) == value
    assert len(value.fingerprint) == 64
    with pytest.raises(ValidationError, match="does not match"):
        value.model_copy(
            update={
                "creative_template": value.creative_template.model_copy(
                    update={"template_id": "fictional-cafe-launch-v1"}
                )
            }
        )
    for version in (True, 2.0, "2", 3, None):
        data = value.model_dump(mode="json")
        data["schema_version"] = version
        with pytest.raises(ValueError):
            models.parse_workbench_run_input_json(json.dumps(data))


@pytest.mark.parametrize(
    "field", ["display_name", "occupation", "background", "interests", "persona_id", "mbti"]
)
@pytest.mark.parametrize(
    "text",
    [
        "person@example.org",
        "+1 415 555 0134",
        "national-id-1234567890",
        "Authorization: Bearer secret-token-1234567890",
        "sk-" + "a" * 32,
        "unsafe\ntext",
        "unsafe\u202etext",
        "unsafe\u2028text",
        "unsafe\ud800text",
    ],
)
def test_every_persona_string_refuses_private_credential_and_display_control_text(
    field: str, text: str
) -> None:
    data = persona_data()
    data[field] = [text] if field == "interests" else text
    with pytest.raises(ValidationError):
        _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))


def _persona_at_byte_size(size: int) -> dict[str, Any]:
    data = persona_data()
    data["background"] = ""
    overhead = len(canonical_json(data).encode("utf-8"))
    quotient, remainder = divmod(size - overhead, 4)
    data["background"] = "😀" * quotient + "a" * remainder
    assert len(data["background"]) <= 800
    assert len(canonical_json(data).encode("utf-8")) == size
    return data


def test_persona_canonical_utf8_byte_limit_is_inclusive_and_value_free() -> None:
    models = _models()
    exact = _persona_at_byte_size(3_072)
    models.WorkbenchPersonaDraftV2.model_validate_json(json.dumps(exact))
    with pytest.raises(ValidationError, match="size") as captured:
        models.WorkbenchPersonaDraftV2.model_validate_json(json.dumps(_persona_at_byte_size(3_073)))
    assert "😀" not in str(captured.value.errors(include_input=False))


def test_population_has_an_independent_canonical_utf8_size_limit() -> None:
    models = _models()
    # A legal population cannot reach this ceiling (30 * 3,072 < 98,304).
    # Exercise the canonical-byte seam and prove raw oversized populations hit it first.
    for size in (98_304, 98_305):
        personas = [persona_data(persona_id=f"persona-{i:03d}") for i in range(1, 31)]
        for item in personas:
            item["background"] = "😀" * 600
        payload = {"personas": personas}
        delta = size - len(canonical_json(payload).encode("utf-8"))
        for item in personas:
            amount = min(delta, 600)
            quotient, remainder = divmod(amount, 4)
            item["background"] += "😀" * quotient + "a" * remainder
            delta -= amount
        assert delta == 0
        assert len(canonical_json(payload).encode("utf-8")) == size
        if size == 98_304:
            models.validate_workbench_population_bytes(payload)
        else:
            with pytest.raises(ValueError, match=r"population.*size"):
                models.validate_workbench_population_bytes(payload)
            with pytest.raises(ValidationError, match=r"population.*size") as captured:
                models.WorkbenchPopulationDraftV2.model_validate_json(json.dumps(payload))
            assert captured.value.errors()[0]["loc"] == ()


def test_persisted_input_retains_independent_raw_request_byte_limit() -> None:
    models = _models()
    value = canonical_json(normalized_v2_input()).encode("utf-8")
    exact = value + b" " * (131_072 - len(value))
    assert models.parse_workbench_run_input_json(exact).schema_version == 2
    with pytest.raises(ValueError, match="size"):
        models.parse_workbench_run_input_json(exact + b" ")


@pytest.mark.parametrize("interests", [["technology", "api_key"], ["api_key", "technology"]])
def test_persona_screen_checks_canonical_nested_members_independently_of_raw_order(
    interests: list[str],
) -> None:
    data = persona_data()
    data["interests"] = interests
    with pytest.raises(ValidationError, match="labelled secret"):
        _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))


def test_persona_python_construction_screens_structural_members_of_interest_sets() -> None:
    data = persona_data()
    data["interests"] = frozenset({"api_key", "technology"})
    data["traits"] = SpatialResponseTraits(**data["traits"])
    data["initial_state"] = _models().WorkbenchInitialState(**data["initial_state"])
    with pytest.raises(ValidationError, match="labelled secret"):
        _models().WorkbenchPersonaDraftV2.model_validate(data)


def test_normalized_population_canonicalizes_assignment_array_order() -> None:
    value = normalized_v2_input()
    data = value.model_dump(mode="json")
    data["draft"]["population"]["assignments"].reverse()
    restored = _models().parse_workbench_run_input_json(json.dumps(data))
    assert restored == value
    assert restored.fingerprint == value.fingerprint


@pytest.mark.parametrize(
    "nested", [{"api_key": "opaque-private-marker"}, ["api_key", "opaque-private-marker"]]
)
def test_persisted_v2_parser_refuses_structural_secret_before_model_validation(
    nested: object,
) -> None:
    data = normalized_v2_input().model_dump(mode="json")
    data["draft"]["population"]["personas"][0]["background"] = nested
    with pytest.raises(ValueError, match="labelled secret") as captured:
        _models().parse_workbench_run_input_json(json.dumps(data))
    assert "opaque-private-marker" not in str(captured.value)


def test_structural_screen_accepts_exact_shared_depth_and_refuses_one_more() -> None:
    nested: object = "Fictional context"
    for _ in range(MAX_JSON_DEPTH):
        nested = [nested]
    before = json.dumps(nested)
    assert _models()._reject_structural_secrets(nested) == nested
    assert json.dumps(nested) == before
    with pytest.raises(ValueError) as captured:
        _models()._reject_structural_secrets([nested])
    assert type(captured.value) is ValueError
    assert str(captured.value) == "workbench input exceeds its nesting limit"


@pytest.mark.parametrize("depth", [MAX_JSON_DEPTH + 1, MAX_JSON_DEPTH * 16])
def test_persisted_v2_parser_bounds_nesting_before_structural_secret_recursion(depth) -> None:
    data = normalized_v2_input().model_dump(mode="json")
    data["api_key"] = "depth-placeholder"
    document = json.dumps(data).replace(
        '"depth-placeholder"', "[" * depth + '"opaque-private-depth-marker"' + "]" * depth
    )
    with pytest.raises(ValueError) as captured:
        _models().parse_workbench_run_input_json(document)
    assert type(captured.value) is ValueError
    assert str(captured.value) == "workbench input exceeds its nesting limit"


@pytest.mark.parametrize("field", ["agent_count", "days", "seed"])
def test_v2_draft_preserves_strict_settings_integer_contract(field: str) -> None:
    data = draft_v2_data()
    data["settings"][field] = True
    with pytest.raises(ValidationError):
        _models().WorkbenchRunDraftV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("value", [-1.0, 1.0])
def test_persona_v2_accepts_sentiment_endpoints(value: float) -> None:
    data = persona_data()
    data["initial_state"]["brand_sentiment"] = value
    persona = _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))
    assert persona.initial_state.brand_sentiment == value


def test_population_v2_accepts_30_distinct_personas_and_rejects_31() -> None:
    data = {"personas": [persona_data(persona_id=f"persona-{i:03d}") for i in range(1, 31)]}
    population = _models().WorkbenchPopulationDraftV2.model_validate_json(json.dumps(data))
    assert len(population.personas) == 30
    data["personas"].append(persona_data(persona_id="persona-031"))
    with pytest.raises(ValidationError):
        _models().WorkbenchPopulationDraftV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_persisted_v2_parser_refuses_non_utf8_json_bytes(encoding: str) -> None:
    document = canonical_json(normalized_v2_input()).encode(encoding)
    with pytest.raises(ValueError, match="UTF-8"):
        _models().parse_workbench_run_input_json(document)


@pytest.mark.parametrize("label", ["api_key", "x-api-key"])
@pytest.mark.parametrize("label_first", [True, False])
def test_persona_interest_secret_labels_are_refused_on_either_side_of_companion(
    label: str,
    label_first: bool,
) -> None:
    data = persona_data()
    companion = "opaque-private-marker"
    data["interests"] = [label, companion] if label_first else [companion, label]
    with pytest.raises(ValidationError, match="labelled secret"):
        _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("label", ["api_key", "x-api-key"])
def test_persona_python_interest_set_refuses_labels_regardless_of_lexical_position(
    label: str,
) -> None:
    data = persona_data()
    data["interests"] = frozenset({label, "opaque-private-marker"})
    data["traits"] = SpatialResponseTraits(**data["traits"])
    data["initial_state"] = _models().WorkbenchInitialState(**data["initial_state"])
    with pytest.raises(ValidationError, match="labelled secret"):
        _models().WorkbenchPersonaDraftV2.model_validate(data)


@pytest.mark.parametrize(
    "interests",
    [
        ["technology", "commuting", "cookie baking", "api_key_usage_notes"],
        ["api_key"],
        ["x-api-key"],
    ],
)
def test_persona_ordinary_interests_and_unaccompanied_labels_remain_supported(
    interests: list[str],
) -> None:
    data = persona_data()
    data["interests"] = interests
    persona = _models().WorkbenchPersonaDraftV2.model_validate_json(json.dumps(data))
    assert persona.interests == frozenset(interests)


def test_schema1_cohort_interest_screening_keeps_its_existing_contract() -> None:
    data = draft_data()
    data["cohort"]["interests"] = ["opaque-private-marker", "x-api-key"]
    draft = _parse_draft(data)
    assert draft.cohort.interests == frozenset({"opaque-private-marker", "x-api-key"})
