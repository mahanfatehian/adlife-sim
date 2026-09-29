import json
from dataclasses import asdict
from typing import Any

import pytest

from adlife.city import osm as osm_module
from adlife.city.osm import OSMImportError, convert_overpass_json
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.serialization import canonical_json


def extract() -> dict[str, Any]:
    return {
        "version": 0.6,
        "elements": [
            {"type": "node", "id": 10, "lat": 35.0, "lon": 51.0},
            {"type": "node", "id": 11, "lat": 35.0, "lon": 51.01},
            {"type": "node", "id": 12, "lat": 35.01, "lon": 51.01},
            {"type": "way", "id": 20, "nodes": [10, 11, 12], "tags": {"highway": "residential"}},
            {"type": "way", "id": 21, "nodes": [12, 10], "tags": {"highway": "service"}},
            {"type": "way", "id": 22, "nodes": [11, 10], "tags": {"highway": "footway"}},
        ],
    }


def convert(document: dict[str, Any], **kwargs: Any) -> Any:
    return convert_overpass_json(
        json.dumps(document).encode("utf-8"), city_id="tehran-pilot", name="تهران", **kwargs
    )


def convert_v2(document: dict[str, Any], **kwargs: Any) -> Any:
    converter = getattr(osm_module, "convert_overpass_json_v2", None)
    assert converter is not None, "v2 OSM conversion is not implemented"
    options = {
        "time_zone": "Asia/Tehran",
        "source_date": "2026-09-29",
        "source_version": "local-extract-1",
        **kwargs,
    }
    return converter(
        json.dumps(document).encode("utf-8"),
        city_id="tehran-pilot",
        name="تهران",
        **options,
    )


def test_import_preserves_geometry_with_osm_license_and_ignores_private_tags() -> None:
    document = extract()
    elements = document["elements"]
    assert isinstance(elements, list)
    elements[3]["tags"]["contact:email"] = "person@example.org"
    result = convert(document)
    pack = result.pack
    assert pack.fingerprint == "1fb3858d19406651a0c9718b62cb085154b9ca28514dfed0d9ea949fa9bc1323"
    assert pack.city_id == "tehran-pilot"
    assert pack.name == "تهران"
    assert str(pack.source_url) == "https://www.openstreetmap.org/copyright"
    assert pack.license == "ODbL-1.0"
    assert pack.attribution == "© OpenStreetMap contributors"
    assert [(n.node_id, n.latitude, n.longitude) for n in pack.nodes] == [
        ("osm-node-10", 35.0, 51.0),
        ("osm-node-11", 35.0, 51.01),
        ("osm-node-12", 35.01, 51.01),
    ]
    assert [(r.road_id, r.source_node, r.target_node) for r in pack.roads] == [
        ("osm-way-20-0", "osm-node-10", "osm-node-11"),
        ("osm-way-20-1", "osm-node-11", "osm-node-12"),
        ("osm-way-21-0", "osm-node-12", "osm-node-10"),
    ]
    assert "person@example.org" not in canonical_json(pack)


def test_permuted_elements_produce_identical_pack_bytes() -> None:
    first = extract()
    second = extract()
    elements = second["elements"]
    assert isinstance(elements, list)
    elements.reverse()
    assert canonical_json(convert(first).pack) == canonical_json(convert(second).pack)


def test_reverse_oneway_and_implicit_motorway_direction() -> None:
    document = extract()
    elements = document["elements"]
    assert isinstance(elements, list)
    elements[3]["tags"] = {"highway": "motorway", "oneway": "-1"}
    elements[4]["tags"] = {"highway": "motorway", "oneway": "no"}
    # The one-way way points 12 -> 11 -> 10, and the two-way link closes the graph.
    result = convert(document)
    roads = {r.road_id: r for r in result.pack.roads}
    assert (roads["osm-way-20-0"].source_node, roads["osm-way-20-0"].target_node) == (
        "osm-node-11",
        "osm-node-10",
    )
    assert roads["osm-way-20-0"].one_way is True
    assert roads["osm-way-21-0"].one_way is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["elements"].append({"type": "node", "id": 10, "lat": 35, "lon": 51}),
        lambda d: d["elements"][0].update(id=True),
        lambda d: d["elements"][3].update(nodes=[10, 99]),
        lambda d: d["elements"][3]["tags"].update(oneway="reversible"),
        lambda d: d["elements"][0].update(lat=100),
    ],
)
def test_refuses_malformed_extract_without_echoing_source(mutation: Any) -> None:
    document = extract()
    mutation(document)
    elements = document["elements"]
    assert isinstance(elements, list)
    elements[3]["tags"]["api_key"] = "topsecret123"
    with pytest.raises(OSMImportError) as caught:
        convert(document)
    assert "topsecret123" not in str(caught.value)


def test_refuses_large_and_nonstandard_json() -> None:
    with pytest.raises(OSMImportError, match="too large"):
        convert_overpass_json(b" " * (16_777_216 + 1), city_id="a", name="City")
    with pytest.raises(OSMImportError, match="JSON"):
        convert_overpass_json(b'{"elements": NaN}', city_id="a", name="City")


def test_disconnected_extract_refused_unless_loss_is_explicit() -> None:
    document = extract()
    elements = document["elements"]
    elements.extend(
        [
            {"type": "node", "id": 30, "lat": 35.1, "lon": 51.1},
            {"type": "node", "id": 31, "lat": 35.1, "lon": 51.11},
            {"type": "way", "id": 40, "nodes": [30, 31], "tags": {"highway": "residential"}},
        ]
    )
    with pytest.raises(OSMImportError, match="disconnected"):
        convert(document)
    result = convert(document, largest_component=True)
    assert len(result.pack.nodes) == 3
    assert len(result.pack.roads) == 3
    assert result.dropped_nodes == 2
    assert result.dropped_roads == 1


def test_equal_size_component_tie_has_stable_identifier_rule() -> None:
    document = {
        "elements": [
            {"type": "node", "id": 1, "lat": 0, "lon": 0},
            {"type": "node", "id": 2, "lat": 0, "lon": 0.01},
            {"type": "node", "id": 3, "lat": 1, "lon": 1},
            {"type": "node", "id": 4, "lat": 1, "lon": 1.01},
            {"type": "way", "id": 2, "nodes": [3, 4], "tags": {"highway": "service"}},
            {"type": "way", "id": 1, "nodes": [1, 2], "tags": {"highway": "service"}},
        ]
    }
    result = convert(document, largest_component=True)
    assert [node.node_id for node in result.pack.nodes] == ["osm-node-1", "osm-node-2"]
    assert result.dropped_nodes == 2
    assert result.dropped_roads == 1


def test_largest_component_refuses_a_single_oneway_segment() -> None:
    document = {
        "elements": [
            {"type": "node", "id": 1, "lat": 0, "lon": 0},
            {"type": "node", "id": 2, "lat": 0, "lon": 0.01},
            {
                "type": "way",
                "id": 1,
                "nodes": [1, 2],
                "tags": {"highway": "residential", "oneway": "yes"},
            },
        ]
    }
    with pytest.raises(OSMImportError, match="connected"):
        convert(document, largest_component=True)


def test_huge_integer_coordinate_is_refused_as_bad_input() -> None:
    document = extract()
    document["elements"][0]["lat"] = 10**1000
    with pytest.raises(OSMImportError, match="coordinate"):
        convert(document)


def test_overpass_error_remark_cannot_publish_partial_streets() -> None:
    document = extract()
    document["remark"] = "runtime error: query timed out; api_key=topsecret123"
    with pytest.raises(OSMImportError, match="incomplete") as caught:
        convert(document)
    assert "topsecret123" not in str(caught.value)


def test_nonstring_element_type_is_refused_as_input_error() -> None:
    document = extract()
    document["elements"].append({"type": ["way"], "id": 123})
    with pytest.raises(OSMImportError, match="type"):
        convert(document)


def test_overlong_json_integer_is_refused_without_parser_detail() -> None:
    data = b'{"elements":[{"type":"node","id":' + b"9" * 5_000 + b"}]}"
    with pytest.raises(OSMImportError, match="JSON"):
        convert_overpass_json(data, city_id="sample", name="Sample")


def test_car_specific_access_overrides_broader_access_ban() -> None:
    document = extract()
    document["elements"][3]["tags"].update(access="no", motor_vehicle="yes")
    roads = convert(document).pack.roads
    assert {road.road_id for road in roads} == {
        "osm-way-20-0",
        "osm-way-20-1",
        "osm-way-21-0",
    }
    restricted = extract()
    restricted["elements"][3]["tags"].update(access="yes", motorcar="no")
    assert [road.road_id for road in convert(restricted).pack.roads] == ["osm-way-21-0"]


def test_vehicle_specific_oneway_controls_car_direction() -> None:
    document = extract()
    document["elements"][3]["tags"]["oneway:motor_vehicle"] = "yes"
    roads = {road.road_id: road for road in convert(document).pack.roads}
    assert roads["osm-way-20-0"].one_way is True
    exception = extract()
    exception["elements"][3]["tags"].update(oneway="yes", **{"oneway:motorcar": "no"})
    roads = {road.road_id: road for road in convert(exception).pack.roads}
    assert roads["osm-way-20-0"].one_way is False


def test_directional_car_access_is_refused_instead_of_imported_as_two_way() -> None:
    document = extract()
    document["elements"][3]["tags"]["motorcar:backward"] = "no"
    with pytest.raises(OSMImportError, match="directional"):
        convert(document)


@pytest.mark.parametrize(
    "restriction,message",
    [
        ({"access:conditional": "yes @ (08:00-09:00)"}, "conditional"),
        ({"motorcar:backward": "yes"}, "directional"),
    ],
)
def test_unsupported_rules_are_refused_before_access_exclusion(
    restriction: dict[str, str], message: str
) -> None:
    document = extract()
    document["elements"][3]["tags"].update(access="no", **restriction)
    with pytest.raises(OSMImportError, match=message):
        convert(document)
    with pytest.raises(OSMImportError, match=message):
        convert_v2(document)


def test_unrelated_tag_namespace_is_not_misread_as_motorcar_access() -> None:
    document = extract()
    document["elements"][3]["tags"]["motorcaravan:conditional"] = "no @ (08:00-09:00)"
    assert len(convert(document).pack.roads) == 3
    assert len(convert_v2(document).pack.roads) == 3


def test_segment_limit_is_checked_before_expanding_a_way() -> None:
    document = extract()
    document["elements"][3]["nodes"] = [10, 11] * 10_001
    with pytest.raises(OSMImportError, match="too many road segments"):
        convert(document)


def test_missing_node_in_excluded_way_still_refuses_incomplete_extract() -> None:
    document = extract()
    document["elements"][5]["nodes"] = [11, 999]
    with pytest.raises(OSMImportError, match="missing node"):
        convert(document)


def test_unused_nodes_are_bounded_before_model_construction() -> None:
    document = extract()
    document["elements"].extend(
        {"type": "node", "id": index, "lat": 35.0, "lon": 51.0} for index in range(100, 50_100)
    )
    with pytest.raises(OSMImportError, match="too many node elements"):
        convert(document)


def test_v2_import_records_exact_provenance_directions_bounds_and_quality() -> None:
    result = convert_v2(extract())
    pack = result.pack
    assert isinstance(pack, CityPackV2)
    assert pack.fingerprint == "d6f89ebf13502af8d14842c1a9c0e1aa3094a8163d1c17de7faf56994ba129f1"
    assert pack.schema_version == 2
    assert pack.time_zone == "Asia/Tehran"
    assert pack.source.provider == "OpenStreetMap contributors"
    assert pack.source.dataset == "OpenStreetMap road extract"
    assert pack.source.version == "local-extract-1"
    assert pack.source.published_on == "2026-09-29"
    assert pack.source.source_sha256 == (
        "131d87d89d11fb4b6f9183c930cc41120470ad18b27e7423dfe79231ce07b71d"
    )
    assert str(pack.source.source_url) == "https://www.openstreetmap.org/copyright"
    assert pack.source.license == "ODbL-1.0"
    assert pack.source.attribution == "© OpenStreetMap contributors"
    assert pack.bounds.model_dump() == {
        "west": 51.0,
        "south": 35.0,
        "east": 51.01,
        "north": 35.01,
    }
    assert set(pack.known_omissions) == {
        "Measured or live traffic and speeds are not represented",
        "Time-dependent access rules are not represented",
        "Turn restrictions are not represented",
    }
    assert [(road.road_id, road.directions, road.shape) for road in pack.roads] == [
        ("osm-way-20-10-11", ("forward", "backward"), ()),
        ("osm-way-20-11-12", ("forward", "backward"), ()),
        ("osm-way-21-12-10", ("forward", "backward"), ()),
    ]
    assert asdict(result.quality) == {
        "input_nodes": 3,
        "input_ways": 3,
        "eligible_ways": 2,
        "excluded_ways": 1,
        "retained_nodes": 3,
        "retained_roads": 3,
        "dropped_nodes": 0,
        "dropped_roads": 0,
    }


def test_v2_source_hash_and_output_ignore_order_and_irrelevant_private_tags() -> None:
    first = extract()
    second = extract()
    elements = second["elements"]
    assert isinstance(elements, list)
    elements.reverse()
    way = next(element for element in elements if element.get("id") == 20)
    way["tags"] = {
        "contact:email": "private@example.org",
        **dict(reversed(tuple(way["tags"].items()))),
    }
    irrelevant = {
        "type": "way",
        "id": 99,
        "nodes": [10, 11],
        "tags": {"highway": "footway", "api_key": "topsecret123"},
    }
    elements.append(irrelevant)
    converted_first = convert_v2(first)
    converted_second = convert_v2(second)
    assert converted_first.pack.source.source_sha256 == converted_second.pack.source.source_sha256
    assert canonical_json(converted_first.pack) == canonical_json(converted_second.pack)
    assert "private@example.org" not in canonical_json(converted_second.pack)
    assert "topsecret123" not in canonical_json(converted_second.pack)

    changed = extract()
    changed["elements"][0]["lat"] = 35.001
    assert (
        convert_v2(changed).pack.source.source_sha256 != converted_first.pack.source.source_sha256
    )
    changed_access = extract()
    changed_access["elements"][3]["tags"]["motorcar"] = "no"
    assert (
        convert_v2(changed_access).pack.source.source_sha256
        != converted_first.pack.source.source_sha256
    )


def test_v2_segment_identity_survives_an_inserted_earlier_member() -> None:
    baseline = convert_v2(extract()).pack
    changed = extract()
    changed["elements"].append({"type": "node", "id": 9, "lat": 35.002, "lon": 50.999})
    changed["elements"][3]["nodes"].insert(0, 9)
    expanded = convert_v2(changed).pack
    baseline_ids = {road.road_id for road in baseline.roads}
    expanded_ids = {road.road_id for road in expanded.roads}
    assert baseline_ids <= expanded_ids
    assert "osm-way-20-9-10" in expanded_ids


def test_v2_reverse_oneway_preserves_physical_endpoint_order() -> None:
    document = extract()
    document["elements"][3]["tags"] = {"highway": "motorway", "oneway": "-1"}
    document["elements"][4]["tags"] = {"highway": "motorway", "oneway": "no"}
    roads = {road.road_id: road for road in convert_v2(document).pack.roads}
    reverse = roads["osm-way-20-10-11"]
    assert (reverse.source_node, reverse.target_node) == ("osm-node-10", "osm-node-11")
    assert reverse.directions == ("backward",)
    assert roads["osm-way-21-12-10"].directions == ("forward", "backward")


@pytest.mark.parametrize(
    "mutation,message",
    [
        (
            lambda d: d["elements"].append(
                {
                    "type": "relation",
                    "id": 50,
                    "members": [],
                    "tags": {"type": "restriction", "restriction": "no_left_turn"},
                }
            ),
            "turn restriction",
        ),
        (
            lambda d: d["elements"][3]["tags"].update({"access:conditional": "no @ (08:00-09:00)"}),
            "conditional",
        ),
        (
            lambda d: d["elements"][3]["tags"].update({"motorcar:backward": "no"}),
            "directional",
        ),
    ],
)
def test_v2_refuses_road_semantics_it_cannot_represent(mutation: Any, message: str) -> None:
    document = extract()
    mutation(document)
    with pytest.raises(OSMImportError, match=message):
        convert_v2(document)


def test_v2_largest_component_reports_bounded_quality_counts() -> None:
    document = extract()
    document["elements"].extend(
        [
            {"type": "node", "id": 30, "lat": 35.1, "lon": 51.1},
            {"type": "node", "id": 31, "lat": 35.1, "lon": 51.11},
            {"type": "way", "id": 40, "nodes": [30, 31], "tags": {"highway": "residential"}},
        ]
    )
    with pytest.raises(OSMImportError, match="disconnected"):
        convert_v2(document)
    result = convert_v2(document, largest_component=True)
    assert result.quality.retained_nodes == 3
    assert result.quality.retained_roads == 3
    assert result.quality.dropped_nodes == 2
    assert result.quality.dropped_roads == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("time_zone", "Mars/Olympus"),
        ("source_date", "2026-02-30"),
        ("source_version", " "),
    ],
)
def test_v2_refuses_invalid_operator_provenance(field: str, value: str) -> None:
    kwargs = {field: value}
    with pytest.raises(OSMImportError, match=r"provenance|metadata"):
        convert_v2(extract(), **kwargs)


def test_way_count_is_bounded_before_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(osm_module, "MAX_OSM_WAY_ELEMENTS", 2, raising=False)
    with pytest.raises(OSMImportError, match="too many way elements"):
        convert(extract())
    with pytest.raises(OSMImportError, match="too many way elements"):
        convert_v2(extract())
