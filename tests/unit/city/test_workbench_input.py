from __future__ import annotations

import importlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

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
