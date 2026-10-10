from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from adlife.city.catalog import CityCatalogError, load_city_catalog, select_catalog_city
from adlife.city.workbench_transport import (
    WorkbenchCityDetail,
    WorkbenchDraftTransportError,
    build_workbench_city_detail,
    parse_workbench_http_draft,
)
from tests.unit.city.test_workbench_input import draft_data


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0", 0),
        ("42", 42),
        ("9223372036854775807", 2**63 - 1),
    ],
)
def test_http_draft_translates_canonical_seed_string_exactly(
    token: str,
    expected: int,
) -> None:
    payload = draft_data()
    payload["settings"]["seed"] = token
    original = deepcopy(payload)

    draft = parse_workbench_http_draft(payload)

    assert draft.settings.seed == expected
    assert payload == original
    assert payload["settings"]["seed"] == token


@pytest.mark.parametrize(
    "token",
    [
        0,
        42,
        True,
        False,
        None,
        "+1",
        "-1",
        " 1",
        "1 ",
        "1.0",
        "1e3",
        "01",
        "",
        "0000000000000000000",
        "12345678901234567890",
        "9223372036854775808",
    ],
)
def test_http_draft_refuses_noncanonical_or_out_of_range_seed(token: object) -> None:
    payload = draft_data()
    payload["settings"]["seed"] = token

    with pytest.raises(WorkbenchDraftTransportError) as caught:
        parse_workbench_http_draft(payload)

    assert caught.value.field == "settings.seed"
    assert caught.value.message == ("Enter a whole-number seed from 0 through 9223372036854775807.")
    assert str(caught.value) == caught.value.message


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"settings": None},
        {"settings": []},
        {"settings": {}},
    ],
)
def test_http_draft_refuses_missing_or_non_object_seed_location(
    payload: dict[str, object],
) -> None:
    with pytest.raises(WorkbenchDraftTransportError) as caught:
        parse_workbench_http_draft(payload)

    assert caught.value.field == "settings.seed"
    assert caught.value.message == ("Enter a whole-number seed from 0 through 9223372036854775807.")


def test_http_draft_leaves_all_other_fields_to_strict_domain_validation() -> None:
    payload = draft_data()
    payload["settings"]["seed"] = "42"
    payload["unexpected"] = True

    with pytest.raises(ValidationError) as caught:
        parse_workbench_http_draft(payload)

    assert caught.value.errors()[0]["type"] == "extra_forbidden"


def test_city_detail_contains_verified_path_free_catalog_data() -> None:
    catalog = load_city_catalog()
    entry = catalog.entries[0]
    pack = select_catalog_city(entry.city_id)

    detail = build_workbench_city_detail(entry.city_id)
    document = detail.model_dump(mode="json")

    assert isinstance(detail, WorkbenchCityDetail)
    assert document == {
        "schema_version": 1,
        "city_id": entry.city_id,
        "display_name": entry.display_name,
        "pack_schema_version": entry.pack_schema_version,
        "data_origin": entry.data_origin,
        "qualification": entry.qualification,
        "reviewer_role": entry.reviewer_role,
        "reviewed_on": entry.reviewed_on,
        "coverage": entry.coverage.model_dump(mode="json"),
        "time_zone": entry.time_zone,
        "source": entry.source.model_dump(mode="json"),
        "known_omissions": list(entry.known_omissions),
        "city_sha256": pack.fingerprint,
        "pack": pack.model_dump(mode="json"),
    }
    assert "resource_name" not in document
    assert "pack_sha256" not in document


def test_city_detail_is_strict_frozen_and_forbids_unknown_fields() -> None:
    detail = build_workbench_city_detail("fictional-grid-v2")
    document: dict[str, Any] = detail.model_dump(mode="python", round_trip=True)
    document["resource_name"] = "private-resource.json"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkbenchCityDetail.model_validate(document)
    frozen_field = "city_id"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(detail, frozen_field, "changed")


def test_city_detail_cross_checks_catalog_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adlife.city.workbench_transport as transport

    catalog = load_city_catalog()
    entry = catalog.entries[0]
    mismatched = entry.model_copy(update={"pack_sha256": "0" * 64})
    mismatched_catalog = catalog.model_copy(update={"entries": (mismatched,)})
    monkeypatch.setattr(transport, "load_city_catalog", lambda: mismatched_catalog)

    with pytest.raises(CityCatalogError, match="integrity"):
        build_workbench_city_detail(entry.city_id)
