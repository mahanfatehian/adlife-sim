from __future__ import annotations

import json
from copy import deepcopy

import pytest

from adlife.core.domain.city import CityPack, CityPackDocument, CityPackV2
from adlife.core.domain.spatial_campaign import (
    SpatialCampaignScenario,
    parse_spatial_campaign_scenario_json,
)
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_campaign_contract import spatial_scenario_data


def scenario_for(pack: CityPackDocument) -> SpatialCampaignScenario:
    data = spatial_scenario_data()
    data["city_id"] = pack.city_id
    data["city_sha256"] = pack.fingerprint
    billboard = data["placements"][0]
    if isinstance(pack, CityPack):
        billboard["longitude"] = 0.005
        billboard["latitude"] = 0.0
    else:
        billboard["longitude"] = 0.005
        billboard["latitude"] = 0.004
    return parse_spatial_campaign_scenario_json(json.dumps(data))


def _validate(scenario: SpatialCampaignScenario, pack: CityPackDocument):
    from adlife.core.domain import spatial_campaign

    validate = getattr(spatial_campaign, "validate_spatial_scenario_against_city", None)
    assert validate is not None, "spatial city binding is not implemented"
    return validate(scenario, pack)


@pytest.mark.parametrize(
    "pack",
    [load_pack(pack_data()), load_pack_v2(pack_v2_data())],
    ids=["v1-straight-road", "v2-intermediate-shape"],
)
def test_spatial_scenario_binds_to_v1_and_v2_road_geometry(
    pack: CityPackDocument,
) -> None:
    scenario = scenario_for(pack)
    before = scenario.model_dump(mode="json")

    evidence = _validate(scenario, pack)

    assert evidence.scenario_sha256 == scenario.fingerprint
    assert evidence.city_sha256 == pack.fingerprint
    assert evidence.campaign_count == 2
    assert evidence.placement_count == 2
    assert evidence.billboard_count == 1
    assert evidence.phone_count == 1
    assert evidence.max_billboard_binding_error_meters == pytest.approx(0.0, abs=1e-9)
    assert scenario.model_dump(mode="json") == before


def test_spatial_binding_refuses_city_identity_and_hash_mismatch() -> None:
    pack = load_pack_v2(pack_v2_data())
    scenario = scenario_for(pack)

    wrong_id = scenario.model_copy(update={"city_id": "different-city"})
    with pytest.raises(ValueError, match="city identifier"):
        _validate(wrong_id, pack)

    wrong_hash = scenario.model_copy(update={"city_sha256": "f" * 64})
    with pytest.raises(ValueError, match="city fingerprint"):
        _validate(wrong_hash, pack)


def test_spatial_binding_refuses_unknown_road() -> None:
    pack = load_pack_v2(pack_v2_data())
    data = spatial_scenario_data()
    data["city_sha256"] = pack.fingerprint
    data["placements"][0]["road_id"] = "missing-road"
    scenario = parse_spatial_campaign_scenario_json(json.dumps(data))

    with pytest.raises(ValueError, match="unknown city road"):
        _validate(scenario, pack)


def test_spatial_binding_refuses_direction_not_supported_by_one_way_road() -> None:
    pack_document = pack_v2_data()
    roads = pack_document["roads"]
    assert isinstance(roads, list)
    roads[0]["directions"] = ["forward"]
    pack = load_pack_v2(pack_document)
    assert isinstance(pack, CityPackV2)
    data = spatial_scenario_data()
    data["city_sha256"] = pack.fingerprint
    data["placements"][0]["travel_direction"] = "backward"
    scenario = parse_spatial_campaign_scenario_json(json.dumps(data))

    with pytest.raises(ValueError, match=r"does not support.*backward"):
        _validate(scenario, pack)


def test_spatial_binding_refuses_coordinate_outside_city_bounds_before_snapping() -> None:
    pack = load_pack_v2(pack_v2_data())
    data = spatial_scenario_data()
    data["city_sha256"] = pack.fingerprint
    data["placements"][0]["longitude"] = 0.02
    scenario = parse_spatial_campaign_scenario_json(json.dumps(data))

    with pytest.raises(ValueError, match="outside city bounds"):
        _validate(scenario, pack)


def test_spatial_binding_refuses_off_network_coordinate_without_mutating_it() -> None:
    pack = load_pack_v2(pack_v2_data())
    data = spatial_scenario_data()
    data["city_sha256"] = pack.fingerprint
    data["placements"][0]["latitude"] = 0.009
    scenario = parse_spatial_campaign_scenario_json(json.dumps(data))
    declared = (
        scenario.placements[0].longitude,
        scenario.placements[0].latitude,
    )

    with pytest.raises(ValueError, match="more than 1 meter from its road fraction"):
        _validate(scenario, pack)
    assert (
        scenario.placements[0].longitude,
        scenario.placements[0].latitude,
    ) == declared


def test_spatial_binding_accepts_sub_meter_authoring_error_and_reports_it() -> None:
    pack = load_pack_v2(pack_v2_data())
    data = spatial_scenario_data()
    data["city_sha256"] = pack.fingerprint
    data["placements"][0]["longitude"] = 0.005001
    scenario = parse_spatial_campaign_scenario_json(json.dumps(data))

    evidence = _validate(scenario, pack)

    assert 0 < evidence.max_billboard_binding_error_meters < 1


def test_spatial_binding_uses_physical_fraction_independent_of_travel_direction() -> None:
    pack = load_pack_v2(pack_v2_data())
    data = deepcopy(spatial_scenario_data())
    data["city_sha256"] = pack.fingerprint
    data["placements"][0]["travel_direction"] = "backward"
    scenario = parse_spatial_campaign_scenario_json(json.dumps(data))

    evidence = _validate(scenario, pack)

    assert evidence.max_billboard_binding_error_meters == pytest.approx(0.0, abs=1e-9)
