import importlib
import json

import pytest
from pydantic import ValidationError

from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data


def place_set_data() -> dict[str, object]:
    pack = load_pack_v2(pack_v2_data())
    return {
        "schema_version": 1,
        "city_id": pack.city_id,
        "city_sha256": pack.fingerprint,
        "name": "Fictional weekday places",
        "places": [
            {
                "place_id": "home-a",
                "kind": "home",
                "node_id": "a",
                "label": "Cedar Court",
                "provenance": {"method": "operator-authored-fictional"},
            },
            {
                "place_id": "home-b",
                "kind": "home",
                "node_id": "b",
                "label": "Juniper House",
                "provenance": {"method": "operator-authored-fictional"},
            },
            {
                "place_id": "work-c",
                "kind": "workplace",
                "node_id": "c",
                "label": "North Works",
                "provenance": {
                    "method": "source-derived",
                    "reference": "Fictional land-use fixture 2026.09",
                },
            },
            {
                "place_id": "leisure-b",
                "kind": "leisure",
                "node_id": "b",
                "label": "Lantern Park",
                "provenance": {
                    "method": "inferred",
                    "reference": "Operator-authored amenity classification v1",
                },
            },
        ],
    }


def _places_module():
    try:
        return importlib.import_module("adlife.core.domain.city_places")
    except ModuleNotFoundError:
        pytest.fail("city place-set contract is not implemented")


def _parse(data: dict[str, object]):
    return _places_module().parse_city_place_set_json(json.dumps(data))


def test_place_set_is_strict_canonical_and_content_addressed() -> None:
    data = place_set_data()
    places = data["places"]
    assert isinstance(places, list)
    first = _parse(data)
    second = _parse({**data, "places": list(reversed(places))})

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert [place.kind for place in first.places] == [
        "home",
        "home",
        "leisure",
        "workplace",
    ]
    assert len(first.fingerprint) == 64
    assert canonical_json(first) == canonical_json(second)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_place_set_schema_version_is_exact(version: object) -> None:
    data = place_set_data()
    data["schema_version"] = version
    with pytest.raises((ValidationError, ValueError), match="schema_version"):
        _parse(data)


def test_place_set_parser_rejects_duplicate_keys_and_non_objects() -> None:
    parser = _places_module().parse_city_place_set_json
    with pytest.raises(ValueError, match="duplicate object key"):
        parser('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="JSON object"):
        parser("[]")


@pytest.mark.parametrize("missing_kind", ["home", "workplace", "leisure"])
def test_place_set_requires_every_routine_role(missing_kind: str) -> None:
    data = place_set_data()
    places = data["places"]
    assert isinstance(places, list)
    data["places"] = [place for place in places if place["kind"] != missing_kind]
    if missing_kind == "home":
        data["places"].append(
            {
                "place_id": "leisure-a",
                "kind": "leisure",
                "node_id": "a",
                "label": "Fictional Square",
                "provenance": {"method": "operator-authored-fictional"},
            }
        )
    with pytest.raises(ValidationError, match=missing_kind):
        _parse(data)


def test_place_ids_are_unique_and_role_node_duplicates_cannot_add_weight() -> None:
    duplicate_id = place_set_data()
    places = duplicate_id["places"]
    assert isinstance(places, list)
    places[1] = {**places[1], "place_id": "home-a"}
    with pytest.raises(ValidationError, match="identifier"):
        _parse(duplicate_id)

    duplicate_node = place_set_data()
    duplicate_places = duplicate_node["places"]
    assert isinstance(duplicate_places, list)
    duplicate_places[1] = {**duplicate_places[1], "node_id": "a"}
    with pytest.raises(ValidationError, match="role and node"):
        _parse(duplicate_node)


@pytest.mark.parametrize(
    ("method", "reference", "message"),
    [
        ("operator-authored-fictional", "Unexpected source", "reference"),
        ("source-derived", None, "reference"),
        ("inferred", None, "reference"),
        ("unknown", None, "method"),
    ],
)
def test_place_provenance_requires_coherent_public_evidence(
    method: str, reference: str | None, message: str
) -> None:
    data = place_set_data()
    places = data["places"]
    assert isinstance(places, list)
    provenance: dict[str, object] = {"method": method}
    if reference is not None:
        provenance["reference"] = reference
    places[0] = {**places[0], "provenance": provenance}
    with pytest.raises(ValidationError, match=message):
        _parse(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", "api_key=topsecret123"),
        ("label", "North\u202eWorks"),
        ("reference", "analyst@example.org"),
    ],
)
def test_place_metadata_refuses_private_or_spoofed_text(field: str, value: str) -> None:
    data = place_set_data()
    places = data["places"]
    assert isinstance(places, list)
    if field == "reference":
        places[2] = {
            **places[2],
            "provenance": {"method": "source-derived", "reference": value},
        }
    else:
        places[2] = {**places[2], field: value}
    with pytest.raises(ValidationError, match=r"credential|private|control"):
        _parse(data)


def test_place_set_forbids_unknown_fields() -> None:
    data = place_set_data()
    data["extra"] = "not allowed"
    with pytest.raises(ValidationError, match="extra"):
        _parse(data)
