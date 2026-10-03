from __future__ import annotations

import importlib
import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import parse_spatial_campaign_scenario_json
from tests.unit.city.test_spatial_campaign_contract import spatial_scenario_data


def spatial_response_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "city_sha256": "a" * 64,
        "scenario_sha256": "d" * 64,
        "profiles": [
            {
                "agent_id": "person-001",
                "fictional": True,
                "interests": ["coffee", "outdoors"],
                "traits": {
                    "price_sensitivity": 0.8,
                    "novelty_seeking": 0.3,
                    "advertising_skepticism": 0.6,
                    "mobile_recall_encoding": 0.7,
                    "roadside_recall_encoding": 0.4,
                    "impulsivity": 0.2,
                },
            },
            {
                "agent_id": "person-000",
                "fictional": True,
                "interests": ["snacks", "coffee"],
                "traits": {
                    "price_sensitivity": 0.2,
                    "novelty_seeking": 0.9,
                    "advertising_skepticism": 0.1,
                    "mobile_recall_encoding": 0.8,
                    "roadside_recall_encoding": 0.5,
                    "impulsivity": 0.7,
                },
            },
        ],
        "campaigns": [
            {
                "campaign_id": "snack-launch",
                "creative_sha256": "c" * 64,
                "target_interests": ["snacks", "social"],
                "relative_price": 0.75,
            },
            {
                "campaign_id": "coffee-launch",
                "creative_sha256": "b" * 64,
                "target_interests": ["coffee", "work"],
                "relative_price": 1.25,
            },
        ],
        "initial_states": [
            {
                "agent_id": agent_id,
                "campaign_id": campaign_id,
                "brand_sentiment": sentiment,
                "recall_strength": 0.1,
                "purchase_intention": 0.2,
            }
            for agent_id, campaign_id, sentiment in (
                ("person-001", "snack-launch", -0.1),
                ("person-000", "coffee-launch", 0.1),
                ("person-001", "coffee-launch", 0.0),
                ("person-000", "snack-launch", 0.2),
            )
        ],
    }


def bound_response_data() -> tuple[dict[str, Any], object]:
    scenario = parse_spatial_campaign_scenario_json(json.dumps(spatial_scenario_data()))
    data = spatial_response_data()
    data["city_sha256"] = scenario.city_sha256
    data["scenario_sha256"] = scenario.fingerprint
    return data, scenario


def _module():
    try:
        return importlib.import_module("adlife.core.domain.spatial_response")
    except ModuleNotFoundError:
        pytest.fail("spatial response input contract is not implemented")


def _parse(data: dict[str, Any]):
    return _module().parse_spatial_response_input_json(json.dumps(data))


def test_response_input_is_strict_canonical_immutable_and_content_addressed() -> None:
    baseline = spatial_response_data()
    permuted = deepcopy(baseline)
    permuted["profiles"].reverse()
    permuted["campaigns"].reverse()
    permuted["initial_states"].reverse()
    for profile in permuted["profiles"]:
        profile["interests"].reverse()
    for campaign in permuted["campaigns"]:
        campaign["target_interests"].reverse()

    first = _parse(baseline)
    second = _parse(permuted)

    assert first == second
    assert canonical_json(first) == canonical_json(second)
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert tuple(profile.agent_id for profile in first.profiles) == (
        "person-000",
        "person-001",
    )
    assert tuple(campaign.campaign_id for campaign in first.campaigns) == (
        "coffee-launch",
        "snack-launch",
    )
    assert tuple((state.agent_id, state.campaign_id) for state in first.initial_states) == (
        ("person-000", "coffee-launch"),
        ("person-000", "snack-launch"),
        ("person-001", "coffee-launch"),
        ("person-001", "snack-launch"),
    )
    with pytest.raises(ValidationError):
        first.model_copy(update={"profiles": ()})


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_response_input_schema_version_requires_the_exact_integer_token(version: object) -> None:
    data = spatial_response_data()
    data["schema_version"] = version
    with pytest.raises((ValidationError, ValueError), match="schema_version"):
        _parse(data)


def test_response_parser_rejects_duplicate_keys_nonobjects_and_nonfinite_values() -> None:
    parser = _module().parse_spatial_response_input_json
    with pytest.raises(ValueError, match="duplicate object key"):
        parser('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="JSON object"):
        parser("[]")
    with pytest.raises(ValueError, match="non-finite"):
        parser('{"schema_version":1,"relative_price":NaN}')


@pytest.mark.parametrize(
    "interest",
    [
        "api_key=topsecret123",
        "analyst@example.org",
        "+1 202 555 0187",
        "interest\u202eexe",
    ],
)
def test_response_interests_refuse_known_secrets_contacts_and_control_text(
    interest: str,
) -> None:
    data = spatial_response_data()
    data["profiles"][0]["interests"] = [interest]
    with pytest.raises(ValidationError, match=r"sensitive|credential|private|control"):
        _parse(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("profiles", 0, "fictional"), False),
        (("profiles", 0, "traits", "price_sensitivity"), True),
        (("profiles", 0, "traits", "novelty_seeking"), -0.01),
        (("profiles", 0, "traits", "advertising_skepticism"), 1.01),
        (("profiles", 0, "traits", "mobile_recall_encoding"), float("inf")),
        (("profiles", 0, "traits", "roadside_recall_encoding"), float("nan")),
        (("profiles", 0, "traits", "impulsivity"), -1.0),
        (("campaigns", 0, "relative_price"), 0.0),
        (("campaigns", 0, "relative_price"), 100.01),
        (("initial_states", 0, "brand_sentiment"), -1.01),
        (("initial_states", 0, "recall_strength"), 1.01),
        (("initial_states", 0, "purchase_intention"), True),
    ],
)
def test_response_numeric_and_literal_bounds_are_strict(
    path: tuple[str | int, ...], value: object
) -> None:
    data = spatial_response_data()
    target: Any = data
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    with pytest.raises((ValidationError, ValueError)):
        _parse(data)


def test_response_input_forbids_unknown_fields_and_duplicate_identifiers() -> None:
    extra = spatial_response_data()
    extra["provider_api_key"] = "never accepted"
    with pytest.raises(ValidationError, match="extra"):
        _parse(extra)

    duplicate_profile = spatial_response_data()
    duplicate_profile["profiles"][1]["agent_id"] = "person-001"
    with pytest.raises(ValidationError, match="profile"):
        _parse(duplicate_profile)

    duplicate_campaign = spatial_response_data()
    duplicate_campaign["campaigns"][1]["campaign_id"] = "snack-launch"
    with pytest.raises(ValidationError, match="campaign"):
        _parse(duplicate_campaign)

    duplicate_state = spatial_response_data()
    duplicate_state["initial_states"][1] = deepcopy(duplicate_state["initial_states"][0])
    with pytest.raises(ValidationError, match="state"):
        _parse(duplicate_state)


def test_response_binding_requires_exact_city_scenario_population_campaigns_and_states() -> None:
    data, scenario = bound_response_data()
    response_input = _parse(data)
    validate = _module().validate_spatial_response_input

    assert (
        validate(
            response_input,
            scenario,
            agent_ids=("person-001", "person-000"),
        )
        is None
    )

    cases: list[tuple[str, dict[str, Any], tuple[str, ...]]] = []
    wrong_city = deepcopy(data)
    wrong_city["city_sha256"] = "f" * 64
    cases.append(("city", wrong_city, ("person-000", "person-001")))
    wrong_scenario = deepcopy(data)
    wrong_scenario["scenario_sha256"] = "f" * 64
    cases.append(("scenario", wrong_scenario, ("person-000", "person-001")))
    wrong_creative = deepcopy(data)
    wrong_creative["campaigns"][0]["creative_sha256"] = "f" * 64
    cases.append(("creative", wrong_creative, ("person-000", "person-001")))
    missing_campaign = deepcopy(data)
    missing_campaign["campaigns"].pop()
    cases.append(("campaign", missing_campaign, ("person-000", "person-001")))
    missing_state = deepcopy(data)
    missing_state["initial_states"].pop()
    cases.append(("state", missing_state, ("person-000", "person-001")))
    extra_agent_ids = ("person-000", "person-001", "person-002")
    cases.append(("agent", deepcopy(data), extra_agent_ids))

    for message, candidate, agent_ids in cases:
        with pytest.raises(ValueError, match=message):
            validate(_parse(candidate), scenario, agent_ids=agent_ids)


def test_response_binding_refuses_duplicate_or_malformed_agent_id_arguments() -> None:
    data, scenario = bound_response_data()
    response_input = _parse(data)
    validate = _module().validate_spatial_response_input

    with pytest.raises(ValueError, match="unique"):
        validate(
            response_input,
            scenario,
            agent_ids=("person-000", "person-000"),
        )
    with pytest.raises(ValueError, match="agent"):
        validate(response_input, scenario, agent_ids=("../escape", "person-001"))
