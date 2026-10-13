from __future__ import annotations

from dataclasses import FrozenInstanceError
from itertools import product
from typing import cast

import pytest

import adlife.city.workbench_catalog as workbench_catalog
from adlife.city.catalog import CityCatalogError, select_catalog_city
from adlife.city.workbench_catalog import (
    PackagedCityCatalogUnavailable,
    ResolvedWorkbenchCity,
    StorageOrigin,
    UnknownWorkbenchCity,
    WorkbenchDataOrigin,
    WorkbenchQualification,
    load_packaged_workbench_cities,
    select_packaged_workbench_city,
)

_VALID_TRIPLES = {
    ("package", "fictional", "fictional-fixture"),
    ("package", "real-world", "rights-reviewed"),
    (
        "workspace-upload",
        "operator-supplied",
        "operator-supplied-unreviewed",
    ),
}


def _resolved(
    storage_origin: str,
    data_origin: str,
    qualification: str,
) -> ResolvedWorkbenchCity:
    pack = select_catalog_city("fictional-grid-v2")
    reviewed = qualification == "rights-reviewed"
    return ResolvedWorkbenchCity(
        pack=pack,
        storage_origin=cast(StorageOrigin, storage_origin),
        data_origin=cast(WorkbenchDataOrigin, data_origin),
        qualification=cast(WorkbenchQualification, qualification),
        reviewer_role="Data rights reviewer" if reviewed else None,
        reviewed_on=pack.source.published_on if reviewed else None,
    )


@pytest.mark.parametrize("triple", sorted(_VALID_TRIPLES))
def test_resolved_city_accepts_only_the_three_documented_trust_triples(
    triple: tuple[str, str, str],
) -> None:
    resolved = _resolved(*triple)

    assert (
        resolved.storage_origin,
        resolved.data_origin,
        resolved.qualification,
    ) == triple


@pytest.mark.parametrize(
    "triple",
    sorted(
        set(
            product(
                ("package", "workspace-upload"),
                ("fictional", "real-world", "operator-supplied"),
                (
                    "fictional-fixture",
                    "rights-reviewed",
                    "operator-supplied-unreviewed",
                ),
            )
        )
        - _VALID_TRIPLES
    ),
)
def test_resolved_city_refuses_every_other_trust_triple(
    triple: tuple[str, str, str],
) -> None:
    with pytest.raises(ValueError, match="classification"):
        _resolved(*triple)


@pytest.mark.parametrize(
    ("qualification", "reviewer_role", "reviewed_on"),
    [
        ("fictional-fixture", "Reviewer", None),
        ("fictional-fixture", None, "2026-09-29"),
        ("operator-supplied-unreviewed", "Reviewer", None),
        ("operator-supplied-unreviewed", None, "2026-09-29"),
        ("rights-reviewed", None, "2026-09-29"),
        ("rights-reviewed", "Reviewer", None),
        ("rights-reviewed", "  ", "2026-09-29"),
        ("rights-reviewed", "Reviewer", "not-a-date"),
        ("rights-reviewed", "Reviewer", "20260929"),
        ("rights-reviewed", "Reviewer", "2026-W40-2"),
        ("rights-reviewed", "Reviewer", "2026-09-28"),
    ],
)
def test_resolved_city_enforces_review_evidence_rules(
    qualification: str,
    reviewer_role: str | None,
    reviewed_on: str | None,
) -> None:
    pack = select_catalog_city("fictional-grid-v2")
    if qualification == "rights-reviewed":
        storage_origin = "package"
        data_origin = "real-world"
    elif qualification == "fictional-fixture":
        storage_origin = "package"
        data_origin = "fictional"
    else:
        storage_origin = "workspace-upload"
        data_origin = "operator-supplied"

    with pytest.raises(ValueError, match="review"):
        ResolvedWorkbenchCity(
            pack=pack,
            storage_origin=cast(StorageOrigin, storage_origin),
            data_origin=cast(WorkbenchDataOrigin, data_origin),
            qualification=cast(WorkbenchQualification, qualification),
            reviewer_role=reviewer_role,
            reviewed_on=reviewed_on,
        )


def test_resolved_city_is_frozen_and_slotted() -> None:
    resolved = _resolved("package", "fictional", "fictional-fixture")

    assert not hasattr(resolved, "__dict__")
    with pytest.raises(FrozenInstanceError):
        resolved.storage_origin = "workspace-upload"


def test_packaged_workbench_cities_preserve_verified_identity_and_order() -> None:
    resolved = load_packaged_workbench_cities()

    assert [city.pack.city_id for city in resolved] == sorted(
        city.pack.city_id for city in resolved
    )
    selected = select_packaged_workbench_city(resolved[0].pack.city_id)
    assert selected.pack is resolved[0].pack
    assert selected.storage_origin == "package"
    assert selected.data_origin == "fictional"
    assert selected.qualification == "fictional-fixture"
    assert selected.reviewer_role is None
    assert selected.reviewed_on is None


def test_packaged_workbench_catalog_translates_integrity_and_unknown_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-never-expose-catalog-error-1234567890"

    def unavailable() -> None:
        raise CityCatalogError(secret)

    monkeypatch.setattr(workbench_catalog, "load_verified_catalog_cities", unavailable)

    with pytest.raises(PackagedCityCatalogUnavailable) as caught:
        load_packaged_workbench_cities()
    assert secret not in str(caught.value)

    monkeypatch.undo()
    with pytest.raises(UnknownWorkbenchCity) as missing:
        select_packaged_workbench_city("unknown-city")
    assert "unknown-city" not in str(missing.value)
