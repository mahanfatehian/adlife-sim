import json

import pytest
from pydantic import ValidationError

from adlife.core.domain.city import CityPack


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


def test_pack_order_does_not_change_fingerprint() -> None:
    data = pack_data()
    nodes, roads = data["nodes"], data["roads"]
    assert isinstance(nodes, list) and isinstance(roads, list)
    reversed_data = {**data, "nodes": list(reversed(nodes)), "roads": list(reversed(roads))}
    first = load_pack(data)
    second = load_pack(reversed_data)
    assert first.fingerprint == second.fingerprint
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


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
