import json

import pytest
from pydantic import ValidationError

from adlife.core.domain import city as city_domain
from adlife.core.domain.serialization import canonical_json

CityPack = city_domain.CityPack


def pack_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "city_id": "sample-city",
        "name": "Fictional Sample City",
        "source_url": "https://example.org/fictional-map",
        "license": "CC0-1.0",
        "attribution": "Fictional streets for demonstration",
        "nodes": [
            {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
            {"node_id": "b", "longitude": 0.01, "latitude": 0.0},
            {"node_id": "c", "longitude": 0.01, "latitude": 0.01},
        ],
        "roads": [
            {"road_id": "ab", "source_node": "a", "target_node": "b", "kind": "residential"},
            {"road_id": "bc", "source_node": "b", "target_node": "c", "kind": "primary"},
            {"road_id": "ca", "source_node": "c", "target_node": "a", "kind": "residential"},
        ],
    }


def load_pack(data: dict[str, object]) -> CityPack:
    return CityPack.model_validate_json(json.dumps(data))


def pack_v2_data() -> dict[str, object]:
    return {
        "schema_version": 2,
        "city_id": "sample-city-v2",
        "name": "Fictional Sample City V2",
        "time_zone": "Etc/UTC",
        "source": {
            "provider": "Fictional Cartography Lab",
            "dataset": "Fictional Grid",
            "version": "2026.09",
            "published_on": "2026-09-29",
            "source_sha256": "0" * 64,
            "source_url": "https://example.org/fictional-map-v2",
            "license": "CC0-1.0",
            "attribution": "Fictional streets for demonstration",
        },
        "bounds": {"west": 0.0, "south": 0.0, "east": 0.01, "north": 0.01},
        "known_omissions": ["No measured traffic", "No turn restrictions"],
        "nodes": [
            {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
            {"node_id": "b", "longitude": 0.01, "latitude": 0.0},
            {"node_id": "c", "longitude": 0.01, "latitude": 0.01},
        ],
        "roads": [
            {
                "road_id": "ab",
                "source_node": "a",
                "target_node": "b",
                "kind": "residential",
                "directions": ["forward", "backward"],
                "shape": [{"longitude": 0.005, "latitude": 0.004}],
            },
            {
                "road_id": "bc",
                "source_node": "b",
                "target_node": "c",
                "kind": "primary",
                "directions": ["forward", "backward"],
                "shape": [],
            },
            {
                "road_id": "ca",
                "source_node": "c",
                "target_node": "a",
                "kind": "residential",
                "directions": ["forward", "backward"],
                "shape": [],
            },
        ],
    }


def load_pack_v2(data: dict[str, object]) -> object:
    model = getattr(city_domain, "CityPackV2", None)
    parser = getattr(city_domain, "parse_city_pack_json", None)
    assert model is not None, "CityPackV2 is not implemented"
    assert parser is not None, "parse_city_pack_json is not implemented"
    pack = parser(json.dumps(data))
    assert isinstance(pack, model)
    return pack


def test_pack_order_does_not_change_fingerprint() -> None:
    data = pack_data()
    nodes, roads = data["nodes"], data["roads"]
    assert isinstance(nodes, list) and isinstance(roads, list)
    reversed_data = {**data, "nodes": list(reversed(nodes)), "roads": list(reversed(roads))}
    first = load_pack(data)
    second = load_pack(reversed_data)
    assert first.fingerprint == second.fingerprint
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_v1_city_pack_fingerprint_is_a_compatibility_golden() -> None:
    pack = load_pack(pack_data())
    assert pack.fingerprint == "178f2cae93f8d144b014510b35e7e7b3a517b2a82522d6d2f10aadcafcde43a3"
    assert '"schema_version":1' in canonical_json(pack)


def test_v2_pack_order_is_canonical() -> None:
    data = pack_v2_data()
    nodes = data["nodes"]
    roads = data["roads"]
    omissions = data["known_omissions"]
    assert isinstance(nodes, list) and isinstance(roads, list) and isinstance(omissions, list)
    reordered_roads = list(reversed(roads))
    for road in reordered_roads:
        assert isinstance(road, dict)
        road["directions"] = list(reversed(road["directions"]))
    reordered = {
        **data,
        "nodes": list(reversed(nodes)),
        "roads": reordered_roads,
        "known_omissions": list(reversed(omissions)),
    }
    first = load_pack_v2(pack_v2_data())
    second = load_pack_v2(reordered)
    assert first.fingerprint == second.fingerprint
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.roads[0].directions == ("forward", "backward")


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("time_zone",), "Mars/Olympus", "time zone"),
        (("known_omissions",), [], "omission"),
        (("bounds", "east"), 0.02, "bounds"),
        (("roads", 0, "directions"), [], "direction"),
        (("roads", 0, "directions"), ["forward", "forward"], "direction"),
        (("roads", 0, "shape"), [{"longitude": 0.005, "latitude": 86.0}], "latitude"),
        (("source", "provider"), "api_key=topsecret123", "credential"),
        (("source", "source_url"), "https://user:secret@example.org/map", "credential"),
    ],
)
def test_v2_pack_rejects_invalid_metadata_and_geometry(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    root = pack_v2_data()
    data: object = root
    for member in path[:-1]:
        if isinstance(data, dict):
            assert isinstance(member, str)
            data = data[member]
        else:
            assert isinstance(data, list) and isinstance(member, int)
            data = data[member]
    final = path[-1]
    if isinstance(data, dict):
        assert isinstance(final, str)
        data[final] = value
    else:
        assert isinstance(data, list) and isinstance(final, int)
        data[final] = value
    with pytest.raises((ValidationError, ValueError), match=message):
        load_pack_v2(root)


def test_v2_pack_rejects_unknown_nodes_and_visual_only_intersections() -> None:
    missing = pack_v2_data()
    roads = missing["roads"]
    assert isinstance(roads, list) and isinstance(roads[0], dict)
    roads[0]["target_node"] = "missing"
    with pytest.raises(ValidationError, match="unknown road node"):
        load_pack_v2(missing)

    crossing = pack_v2_data()
    crossing["bounds"] = {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0}
    crossing["nodes"] = [
        {"node_id": "nw", "longitude": 0.0, "latitude": 1.0},
        {"node_id": "ne", "longitude": 1.0, "latitude": 1.0},
        {"node_id": "sw", "longitude": 0.0, "latitude": 0.0},
        {"node_id": "se", "longitude": 1.0, "latitude": 0.0},
    ]
    crossing["roads"] = [
        {
            "road_id": "nw-se",
            "source_node": "nw",
            "target_node": "se",
            "kind": "primary",
            "directions": ["forward", "backward"],
            "shape": [],
        },
        {
            "road_id": "sw-ne",
            "source_node": "sw",
            "target_node": "ne",
            "kind": "primary",
            "directions": ["forward", "backward"],
            "shape": [],
        },
    ]
    with pytest.raises(ValidationError, match="connected"):
        load_pack_v2(crossing)


def test_v2_pack_rejects_dateline_geometry_and_extra_fields() -> None:
    dateline = pack_v2_data()
    dateline["bounds"] = {"west": -179.9, "south": 0.0, "east": 179.9, "north": 0.01}
    nodes = dateline["nodes"]
    assert isinstance(nodes, list) and isinstance(nodes[0], dict) and isinstance(nodes[1], dict)
    nodes[0]["longitude"] = 179.9
    nodes[1]["longitude"] = -179.9
    roads = dateline["roads"]
    assert isinstance(roads, list) and isinstance(roads[0], dict)
    roads[0]["shape"] = []
    with pytest.raises(ValidationError, match="date line"):
        load_pack_v2(dateline)

    extra = pack_v2_data()
    extra["download_url"] = "https://example.org/untrusted"
    with pytest.raises(ValidationError, match="extra"):
        load_pack_v2(extra)


@pytest.mark.parametrize("version", [None, True, 1.0, 0, 3])
def test_city_pack_version_dispatch_fails_closed(version: object) -> None:
    data = pack_v2_data()
    if version is None:
        del data["schema_version"]
    else:
        data["schema_version"] = version
    with pytest.raises((ValidationError, ValueError), match=r"schema_version|version"):
        parser = getattr(city_domain, "parse_city_pack_json", None)
        assert parser is not None, "parse_city_pack_json is not implemented"
        parser(json.dumps(data))


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("source_url", "javascript:alert(1)"),
        ("source_url", "https://user:secret@example.org/roads"),
        ("source_url", "https://example.org/roads?api_key=topsecret123"),
        ("attribution", "api_key=topsecret123"),
        ("name", "Fictional\x1b[31mCity"),
        ("name", "Fake\u202ecity"),
        ("attribution", "Map\nFAKE PASS"),
        ("license", " "),
        ("name", ""),
    ],
)
def test_pack_rejects_invalid_metadata(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        load_pack({**pack_data(), field: value})


def test_pack_rejects_missing_road_node() -> None:
    data = pack_data()
    data["roads"] = [
        {"road_id": "bad", "source_node": "a", "target_node": "missing", "kind": "primary"}
    ]
    with pytest.raises(ValidationError, match="unknown road node"):
        load_pack(data)


def test_pack_rejects_nonfinite_coordinates() -> None:
    data = pack_data()
    data["nodes"] = [{"node_id": "a", "longitude": float("nan"), "latitude": 0.0}]
    with pytest.raises(ValidationError):
        load_pack(data)


def test_pack_rejects_zero_length_road() -> None:
    data = pack_data()
    nodes = data["nodes"]
    assert isinstance(nodes, list)
    nodes[1] = {"node_id": "b", "longitude": 0.0, "latitude": 0.0}
    with pytest.raises(ValidationError, match="distinct coordinates"):
        load_pack(data)


def test_pack_rejects_disconnected_streets() -> None:
    data = pack_data()
    data["roads"] = [
        {"road_id": "ab", "source_node": "a", "target_node": "b", "kind": "residential"}
    ]
    with pytest.raises(ValidationError, match="connected"):
        load_pack(data)


@pytest.mark.parametrize("collection", ["nodes", "roads"])
def test_pack_rejects_duplicate_identifiers(collection: str) -> None:
    data = pack_data()
    items = data[collection]
    assert isinstance(items, list)
    items.append(items[0])
    with pytest.raises(ValidationError, match="duplicate city"):
        load_pack(data)


def test_pack_is_frozen() -> None:
    pack = load_pack(pack_data())
    with pytest.raises(ValidationError):
        pack.name = "Changed"


def test_persian_city_name_remains_valid() -> None:
    data = pack_data()
    data["name"] = "شهر خیالی"
    assert load_pack(data).name == "شهر خیالی"


def test_city_pack_refuses_date_line_road_that_canvas_cannot_project() -> None:
    data = pack_data()
    data["nodes"] = [
        {"node_id": "a", "longitude": 179.9, "latitude": 0.0},
        {"node_id": "b", "longitude": -179.9, "latitude": 0.0},
    ]
    data["roads"] = [{"road_id": "ab", "source_node": "a", "target_node": "b", "kind": "motorway"}]
    with pytest.raises(ValidationError, match="date line"):
        load_pack(data)
