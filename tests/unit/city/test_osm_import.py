import json
from typing import Any

import pytest

from adlife.city.osm import OSMImportError, convert_overpass_json
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


def test_import_preserves_geometry_with_osm_license_and_ignores_private_tags() -> None:
    document = extract()
    elements = document["elements"]
    assert isinstance(elements, list)
    elements[3]["tags"]["contact:email"] = "person@example.org"
    result = convert(document)
    pack = result.pack
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
