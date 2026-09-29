from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.city.catalog import (
    CityCatalogError,
    UnknownCatalogCity,
    load_city_catalog,
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


def test_packaged_catalog_contains_only_an_explicit_fictional_v2_fixture() -> None:
    catalog = load_city_catalog()
    assert catalog.issued_on == "2026-09-29"
    assert [entry.city_id for entry in catalog.entries] == ["fictional-grid-v2"]
    entry = catalog.entries[0]
    assert entry.qualification == "fictional-fixture"
    assert entry.reviewer_role is None and entry.reviewed_on is None
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
