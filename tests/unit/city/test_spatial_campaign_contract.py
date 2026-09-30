from __future__ import annotations

import importlib
import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json


def spatial_scenario_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scenario_id": "fictional-launch",
        "name": "Fictional city launch",
        "days": 2,
        "city_id": "sample-city-v2",
        "city_sha256": "a" * 64,
        "campaigns": [
            {
                "campaign_id": "coffee-launch",
                "name": "Fictional coffee launch",
                "creative_sha256": "b" * 64,
            },
            {
                "campaign_id": "snack-launch",
                "name": "Fictional snack launch",
                "creative_sha256": "c" * 64,
            },
        ],
        "placements": [
            {
                "placement_id": "billboard-ab",
                "campaign_id": "coffee-launch",
                "channel": "roadside-billboard",
                "active_windows": [
                    {"start_minute": 600, "end_minute": 720},
                    {"start_minute": 120, "end_minute": 240},
                ],
                "frequency_cap_per_agent_per_day": 3,
                "road_id": "ab",
                "travel_direction": "forward",
                "road_fraction": 0.5,
                "longitude": 0.005,
                "latitude": 0.004,
                "side": "right",
                "orientation_degrees": 90.0,
                "max_view_distance_meters": 120.0,
            },
            {
                "placement_id": "phone-snack",
                "campaign_id": "snack-launch",
                "channel": "mobile-feed",
                "active_windows": [{"start_minute": 60, "end_minute": 1_380}],
                "frequency_cap_per_agent_per_day": 2,
                "opportunity_model": "keyed-activity-minute-v1",
                "eligible_activities": ["work", "home"],
                "opportunity_probability_per_minute": 0.05,
            },
        ],
    }


def _module():
    try:
        return importlib.import_module("adlife.core.domain.spatial_campaign")
    except ModuleNotFoundError:
        pytest.fail("spatial campaign contract is not implemented")


def _parse(data: dict[str, Any]):
    return _module().parse_spatial_campaign_scenario_json(json.dumps(data))


def test_spatial_scenario_is_strict_canonical_and_content_addressed() -> None:
    first_data = spatial_scenario_data()
    second_data = deepcopy(first_data)
    second_data["campaigns"].reverse()
    second_data["placements"].reverse()
    second_data["placements"][1]["active_windows"].reverse()
    second_data["placements"][0]["eligible_activities"].reverse()

    first = _parse(first_data)
    second = _parse(second_data)

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert [campaign.campaign_id for campaign in first.campaigns] == [
        "coffee-launch",
        "snack-launch",
    ]
    assert [placement.placement_id for placement in first.placements] == [
        "billboard-ab",
        "phone-snack",
    ]
    assert canonical_json(first) == canonical_json(second)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_spatial_scenario_schema_version_is_exact(version: object) -> None:
    data = spatial_scenario_data()
    data["schema_version"] = version
    with pytest.raises((ValidationError, ValueError), match="schema_version"):
        _parse(data)


def test_spatial_parser_rejects_duplicate_keys_non_objects_and_nonfinite_values() -> None:
    parser = _module().parse_spatial_campaign_scenario_json
    with pytest.raises(ValueError, match="duplicate object key"):
        parser('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="JSON object"):
        parser("[]")
    with pytest.raises(ValueError, match="non-finite"):
        parser('{"schema_version":1,"days":NaN}')


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario", "api_key=topsecret123"),
        ("campaign", "analyst@example.org"),
        ("campaign", "Launch\u202eexe"),
    ],
)
def test_spatial_public_names_refuse_secrets_private_ids_and_display_spoofing(
    field: str, value: str
) -> None:
    data = spatial_scenario_data()
    if field == "scenario":
        data["name"] = value
    else:
        data["campaigns"][0]["name"] = value
    with pytest.raises(ValidationError, match=r"credential|private|control"):
        _parse(data)


def test_spatial_scenario_requires_unique_campaign_and_placement_ids() -> None:
    duplicate_campaign = spatial_scenario_data()
    duplicate_campaign["campaigns"][1]["campaign_id"] = "coffee-launch"
    with pytest.raises(ValidationError, match="campaign identifier"):
        _parse(duplicate_campaign)

    duplicate_placement = spatial_scenario_data()
    duplicate_placement["placements"][1]["placement_id"] = "billboard-ab"
    with pytest.raises(ValidationError, match="placement identifier"):
        _parse(duplicate_placement)


def test_every_placement_references_a_campaign_and_every_campaign_is_used() -> None:
    unknown = spatial_scenario_data()
    unknown["placements"][0]["campaign_id"] = "missing-campaign"
    with pytest.raises(ValidationError, match="unknown campaign"):
        _parse(unknown)

    unused = spatial_scenario_data()
    unused["placements"] = [unused["placements"][0]]
    with pytest.raises(ValidationError, match="without a placement"):
        _parse(unused)


def test_windows_are_canonical_non_overlapping_and_inside_scenario() -> None:
    canonical = _parse(spatial_scenario_data())
    windows = canonical.placements[0].active_windows
    assert [(item.start_minute, item.end_minute) for item in windows] == [
        (120, 240),
        (600, 720),
    ]

    overlapping = spatial_scenario_data()
    overlapping["placements"][0]["active_windows"] = [
        {"start_minute": 100, "end_minute": 300},
        {"start_minute": 200, "end_minute": 400},
    ]
    with pytest.raises(ValidationError, match="overlap"):
        _parse(overlapping)

    beyond_duration = spatial_scenario_data()
    beyond_duration["placements"][0]["active_windows"][0]["end_minute"] = 2_881
    with pytest.raises(ValidationError, match="scenario duration"):
        _parse(beyond_duration)


def test_duplicate_physical_billboards_and_phone_policies_are_refused() -> None:
    duplicate_billboard = spatial_scenario_data()
    clone = deepcopy(duplicate_billboard["placements"][0])
    clone["placement_id"] = "billboard-ab-copy"
    clone["campaign_id"] = "snack-launch"
    duplicate_billboard["placements"].append(clone)
    with pytest.raises(ValidationError, match="physical billboard"):
        _parse(duplicate_billboard)

    duplicate_phone = spatial_scenario_data()
    clone = deepcopy(duplicate_phone["placements"][1])
    clone["placement_id"] = "phone-snack-copy"
    clone["active_windows"] = [{"start_minute": 1_400, "end_minute": 1_500}]
    clone["frequency_cap_per_agent_per_day"] = 7
    clone["eligible_activities"] = ["commute"]
    clone["opportunity_probability_per_minute"] = 0.2
    duplicate_phone["placements"].append(clone)
    with pytest.raises(ValidationError, match="phone opportunity policy"):
        _parse(duplicate_phone)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("days",), True, "integer"),
        (("campaigns", 0, "creative_sha256"), "A" * 64, "pattern"),
        (("placements", 0, "road_fraction"), 0.0, "greater than"),
        (("placements", 0, "road_fraction"), 1.0, "less than"),
        (("placements", 0, "orientation_degrees"), 360.0, "less than"),
        (("placements", 0, "frequency_cap_per_agent_per_day"), True, "integer"),
        (("placements", 1, "opportunity_probability_per_minute"), 1.01, "less than"),
    ],
)
def test_spatial_numeric_and_hash_bounds_are_strict(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    data = spatial_scenario_data()
    target: Any = data
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    with pytest.raises(ValidationError, match=message):
        _parse(data)


def test_spatial_scenario_forbids_unknown_fields() -> None:
    data = spatial_scenario_data()
    data["provider_api_key"] = "never accepted"
    with pytest.raises(ValidationError, match="extra"):
        _parse(data)
