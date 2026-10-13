from __future__ import annotations

import json
import socket
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import adlife.city.catalog as catalog_module
from adlife.city.catalog import (
    CityCatalogError,
    UnknownCatalogCity,
    VerifiedCatalogCity,
    load_city_catalog,
    load_verified_catalog_cities,
    select_catalog_city,
)
from adlife.core.domain.city import CityPackV2, parse_city_pack_json
from adlife.core.domain.city_catalog import CityCatalog, parse_city_catalog_json
from adlife.core.domain.serialization import canonical_json
from tests.unit.city.test_city_pack import pack_v2_data


def entry_data(pack: CityPackV2 | None = None) -> dict[str, Any]:
    if pack is None:
        parsed = parse_city_pack_json(json.dumps(pack_v2_data()))
        assert isinstance(parsed, CityPackV2)
        pack = parsed
    return {
        "city_id": pack.city_id,
        "display_name": pack.name,
        "resource_name": f"{pack.city_id}.json",
        "pack_sha256": pack.fingerprint,
        "pack_schema_version": 2,
        "data_origin": "fictional",
        "qualification": "fictional-fixture",
        "reviewer_role": None,
        "reviewed_on": None,
        "coverage": pack.bounds.model_dump(mode="json"),
        "time_zone": pack.time_zone,
        "source": pack.source.model_dump(mode="json"),
        "known_omissions": pack.known_omissions,
    }


def catalog_data(*entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "issued_on": "2026-09-29",
        "entries": entries or (entry_data(),),
    }


def write_catalog_root(
    root: Path,
    *,
    pack: CityPackV2 | None = None,
    entry: dict[str, Any] | None = None,
) -> tuple[CityPackV2, dict[str, Any]]:
    if pack is None:
        parsed = parse_city_pack_json(json.dumps(pack_v2_data()))
        assert isinstance(parsed, CityPackV2)
        pack = parsed
    if entry is None:
        entry = entry_data(pack)
    (root / "catalog").mkdir(parents=True)
    (root / "catalog" / str(entry["resource_name"])).write_text(
        canonical_json(pack) + "\n", encoding="utf-8"
    )
    (root / "catalog.json").write_text(canonical_json(catalog_data(entry)) + "\n", encoding="utf-8")
    return pack, entry


def test_catalog_contract_is_strict_sorted_and_immutable() -> None:
    first = entry_data()
    second = {
        **first,
        "city_id": "another-fictional-city",
        "display_name": "Another Fictional City",
        "resource_name": "another-fictional-city.json",
        "pack_sha256": "f" * 64,
    }
    catalog = CityCatalog.model_validate(catalog_data(first, second))
    assert [entry.city_id for entry in catalog.entries] == [
        "another-fictional-city",
        "sample-city-v2",
    ]
    with pytest.raises(ValidationError):
        catalog.entries = ()
    with pytest.raises(ValidationError):
        CityCatalog.model_validate({**catalog_data(), "unexpected": True})


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource_name", "../city.json"),
        ("resource_name", "folder/city.json"),
        ("resource_name", r"folder\city.json"),
        ("resource_name", "con.json"),
        ("pack_schema_version", 1),
        ("pack_schema_version", 2.0),
        ("qualification", "unreviewed"),
        ("display_name", "api_key=topsecret123"),
        ("time_zone", "Mars/Olympus"),
    ],
)
def test_catalog_entry_refuses_unsafe_or_misleading_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CityCatalog.model_validate(catalog_data({**entry_data(), field: value}))


def test_catalog_qualification_requires_complete_review_evidence() -> None:
    base = entry_data()
    with pytest.raises(ValidationError, match="review"):
        CityCatalog.model_validate(catalog_data({**base, "qualification": "rights-reviewed"}))
    with pytest.raises(ValidationError, match="review"):
        CityCatalog.model_validate(
            catalog_data(
                {
                    **base,
                    "reviewer_role": "Data rights counsel",
                    "reviewed_on": "2026-09-29",
                }
            )
        )
    reviewed = CityCatalog.model_validate(
        catalog_data(
            {
                **base,
                "data_origin": "real-world",
                "qualification": "rights-reviewed",
                "reviewer_role": "Data rights counsel",
                "reviewed_on": "2026-09-29",
            }
        )
    )
    assert reviewed.entries[0].qualification == "rights-reviewed"


def test_catalog_qualification_cannot_disguise_real_data_as_a_fixture() -> None:
    base = entry_data()
    with pytest.raises(ValidationError, match="fictional"):
        CityCatalog.model_validate(
            catalog_data(
                {
                    **base,
                    "data_origin": "real-world",
                    "source": {
                        **base["source"],
                        "provider": "OpenStreetMap contributors",
                        "dataset": "Tehran extract",
                    },
                }
            )
        )


@pytest.mark.parametrize("reviewed_on", ["2025-12-31", "9999-12-31"])
def test_rights_review_date_must_be_within_catalog_evidence_window(reviewed_on: str) -> None:
    base = entry_data()
    with pytest.raises(ValidationError, match="review"):
        CityCatalog.model_validate(
            catalog_data(
                {
                    **base,
                    "data_origin": "real-world",
                    "qualification": "rights-reviewed",
                    "reviewer_role": "Data rights counsel",
                    "reviewed_on": reviewed_on,
                }
            )
        )


def test_catalog_issuance_date_is_valid_and_not_before_source_evidence() -> None:
    with pytest.raises(ValidationError, match="issued_on"):
        CityCatalog.model_validate({**catalog_data(), "issued_on": "2026-02-30"})
    with pytest.raises(ValidationError, match="issued"):
        CityCatalog.model_validate({**catalog_data(), "issued_on": "2026-09-28"})


@pytest.mark.parametrize("duplicate", ["city_id", "resource_name", "pack_sha256"])
def test_catalog_refuses_duplicate_entry_identity(duplicate: str) -> None:
    first = entry_data()
    second = {
        **first,
        "city_id": "another-fictional-city",
        "display_name": "Another Fictional City",
        "resource_name": "another-fictional-city.json",
        "pack_sha256": "f" * 64,
    }
    second[duplicate] = first[duplicate]
    with pytest.raises(ValidationError, match="duplicate"):
        CityCatalog.model_validate(catalog_data(first, second))


@pytest.mark.parametrize("version", [None, True, 1.0, 0, 2])
def test_catalog_version_dispatch_fails_closed(version: object) -> None:
    document = catalog_data()
    if version is None:
        del document["schema_version"]
    else:
        document["schema_version"] = version
    with pytest.raises((ValidationError, ValueError), match=r"schema_version|version"):
        parse_city_catalog_json(json.dumps(document))


def test_packaged_catalog_contains_exactly_three_explicit_fictional_v2_fixtures() -> None:
    catalog = load_city_catalog()
    assert catalog.issued_on == "2026-10-13"
    assert [entry.city_id for entry in catalog.entries] == [
        "fictional-grid-v2",
        "fictional-harbor-loop-v2",
        "fictional-river-junction-v2",
    ]
    assert all(entry.data_origin == "fictional" for entry in catalog.entries)
    assert all(entry.qualification == "fictional-fixture" for entry in catalog.entries)
    assert all(
        entry.reviewer_role is None and entry.reviewed_on is None for entry in catalog.entries
    )
    assert all(
        date.fromisoformat(entry.source.published_on) <= date(2026, 10, 13)
        for entry in catalog.entries
    )
    assert len({entry.pack_sha256 for entry in catalog.entries}) == 3

    entry = next(item for item in catalog.entries if item.city_id == "fictional-grid-v2")
    pack = select_catalog_city("fictional-grid-v2")
    assert pack.schema_version == entry.pack_schema_version == 2
    assert (
        pack.fingerprint
        == entry.pack_sha256
        == ("c567018e97feb4d04bea1eb3f3526981fdd915df22ad3a54237b2f5e6cb8b92c")
    )
    assert "fictional" in pack.name.lower()
    with pytest.raises(UnknownCatalogCity):
        select_catalog_city("unknown-city")


def test_packaged_fictional_city_packs_are_distinct_connected_and_honest() -> None:
    verified = load_verified_catalog_cities()

    assert len({city.pack.bounds for city in verified}) == 3
    topology_signatures: set[tuple[int, int, tuple[int, ...]]] = set()
    for city in verified:
        pack = city.pack
        adjacency = {node.node_id: set() for node in pack.nodes}
        for road in pack.roads:
            if "forward" in road.directions:
                adjacency[road.source_node].add(road.target_node)
            if "backward" in road.directions:
                adjacency[road.target_node].add(road.source_node)
        start = pack.nodes[0].node_id
        visited: set[str] = set()
        pending = [start]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency[current] - visited)
        assert visited == set(adjacency)
        topology_signatures.add(
            (
                len(pack.nodes),
                len(pack.roads),
                tuple(sorted(len(neighbors) for neighbors in adjacency.values())),
            )
        )
        omissions = " ".join(pack.known_omissions).lower()
        for absent_calibration in ("traffic", "population", "land use"):
            assert absent_calibration in omissions
        if pack.city_id != "fictional-grid-v2":
            assert "resident calibration" in omissions
        assert city.entry.source == pack.source
        assert city.entry.coverage == pack.bounds
        assert city.entry.pack_sha256 == pack.fingerprint

    assert len(topology_signatures) == 3


def test_default_catalog_apis_share_one_lazy_verified_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_module._default_catalog_snapshot.cache_clear()
    labels: list[str] = []
    original = catalog_module._read_bounded

    def counted_read(*args: object, **kwargs: object) -> bytes:
        label = kwargs.get("label")
        assert isinstance(label, str)
        labels.append(label)
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog_module, "_read_bounded", counted_read)
    try:
        verified = load_verified_catalog_cities()
        catalog = load_city_catalog()
        selected = select_catalog_city(verified[0].entry.city_id)
    finally:
        catalog_module._default_catalog_snapshot.cache_clear()

    assert labels == ["index", "pack resource", "pack resource", "pack resource"]
    assert isinstance(verified[0], VerifiedCatalogCity)
    assert catalog.entries == tuple(city.entry for city in verified)
    assert selected is verified[0].pack


def test_verified_catalog_cities_are_stably_ordered_frozen_and_slotted() -> None:
    verified = load_verified_catalog_cities()

    assert [city.entry.city_id for city in verified] == sorted(
        city.entry.city_id for city in verified
    )
    assert not hasattr(verified[0], "__dict__")
    with pytest.raises(FrozenInstanceError):
        verified[0].pack = verified[0].pack


def test_explicit_catalog_root_is_fresh_and_never_uses_the_default_cache(
    tmp_path: Path,
) -> None:
    _pack, entry = write_catalog_root(tmp_path)
    first = load_verified_catalog_cities(root=tmp_path)
    assert first[0].entry.city_id == entry["city_id"]

    (tmp_path / "catalog.json").write_bytes(b"not-json")

    with pytest.raises(CityCatalogError, match="schema validation"):
        load_verified_catalog_cities(root=tmp_path)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
@pytest.mark.parametrize("resource", ["index", "pack"])
def test_catalog_refuses_bom_or_non_utf8_resource_encodings(
    tmp_path: Path,
    encoding: str,
    resource: str,
) -> None:
    pack, entry = write_catalog_root(tmp_path)
    if resource == "index":
        text = canonical_json(catalog_data(entry)) + "\n"
        (tmp_path / "catalog.json").write_bytes(text.encode(encoding))
    else:
        text = canonical_json(pack) + "\n"
        (tmp_path / "catalog" / str(entry["resource_name"])).write_bytes(text.encode(encoding))

    with pytest.raises(CityCatalogError, match="schema validation"):
        load_verified_catalog_cities(root=tmp_path)


def test_catalog_selection_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    assert select_catalog_city("fictional-grid-v2").schema_version == 2


@pytest.mark.parametrize(
    "member,value",
    [
        (("city_id",), "changed-city"),
        (("name",), "Changed Fictional City"),
        (("time_zone",), "Europe/London"),
        (("source", "version"), "changed-source"),
        (("source", "license"), "MIT"),
        (("known_omissions",), ["Changed omission"]),
        (("bounds", "east"), 0.02),
    ],
)
def test_catalog_refuses_pack_metadata_mismatch(
    tmp_path: Path, member: tuple[str, ...], value: object
) -> None:
    data = pack_v2_data()
    cursor: dict[str, Any] = data
    for key in member[:-1]:
        cursor = cursor[key]
    cursor[member[-1]] = value
    if member == ("bounds", "east"):
        nodes = data["nodes"]
        assert isinstance(nodes, list) and isinstance(nodes[1], dict)
        nodes[1]["longitude"] = 0.02
        roads = data["roads"]
        assert isinstance(roads, list) and isinstance(roads[0], dict)
        roads[0]["shape"] = []
    parsed = parse_city_pack_json(json.dumps(data))
    assert isinstance(parsed, CityPackV2)
    original_entry = entry_data()
    original_entry["pack_sha256"] = parsed.fingerprint
    write_catalog_root(tmp_path, pack=parsed, entry=original_entry)
    with pytest.raises(CityCatalogError, match=r"metadata|integrity"):
        load_city_catalog(root=tmp_path)


def test_catalog_refuses_missing_and_hash_mismatched_resources(tmp_path: Path) -> None:
    pack, entry = write_catalog_root(tmp_path)
    (tmp_path / "catalog" / str(entry["resource_name"])).unlink()
    with pytest.raises(CityCatalogError):
        load_city_catalog(root=tmp_path)
    (tmp_path / "catalog" / str(entry["resource_name"])).write_text(
        canonical_json(pack) + "\n", encoding="utf-8"
    )
    entry["pack_sha256"] = "f" * 64
    (tmp_path / "catalog.json").write_text(
        canonical_json(catalog_data(entry)) + "\n", encoding="utf-8"
    )
    with pytest.raises(CityCatalogError, match="integrity"):
        load_city_catalog(root=tmp_path)


def test_catalog_and_pack_reads_are_bounded(tmp_path: Path) -> None:
    pack, entry = write_catalog_root(tmp_path)
    (tmp_path / "catalog.json").write_bytes(b" " * (262_144 + 1))
    with pytest.raises(CityCatalogError, match="size limit"):
        load_city_catalog(root=tmp_path)
    (tmp_path / "catalog.json").write_text(
        canonical_json(catalog_data(entry)) + "\n", encoding="utf-8"
    )
    (tmp_path / "catalog" / str(entry["resource_name"])).write_bytes(b" " * (4_194_304 + 1))
    with pytest.raises(CityCatalogError, match="size limit"):
        load_city_catalog(root=tmp_path)
    assert pack.fingerprint == entry["pack_sha256"]


def test_catalog_root_cannot_escape_through_a_pack_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    parsed = parse_city_pack_json(json.dumps(pack_v2_data()))
    assert isinstance(parsed, CityPackV2)
    outside.write_text(canonical_json(parsed) + "\n", encoding="utf-8")
    root = tmp_path / "root"
    _, entry = write_catalog_root(root, pack=parsed)
    resource = root / "catalog" / str(entry["resource_name"])
    resource.unlink()
    try:
        resource.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create file symlinks")
    with pytest.raises(CityCatalogError, match=r"unsafe|integrity"):
        load_city_catalog(root=root)
