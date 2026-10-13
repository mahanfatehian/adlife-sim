"""Neutral trust classifications for packaged and workspace city packs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal
from unicodedata import bidirectional

from adlife.city.catalog import (
    CityCatalogError,
    VerifiedCatalogCity,
    load_verified_catalog_cities,
)
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.person import contains_secret_or_email_text

StorageOrigin = Literal["package", "workspace-upload"]
WorkbenchDataOrigin = Literal["fictional", "real-world", "operator-supplied"]
WorkbenchQualification = Literal[
    "fictional-fixture",
    "rights-reviewed",
    "operator-supplied-unreviewed",
]

_TRUST_CLASSIFICATIONS = frozenset(
    {
        ("package", "fictional", "fictional-fixture"),
        ("package", "real-world", "rights-reviewed"),
        (
            "workspace-upload",
            "operator-supplied",
            "operator-supplied-unreviewed",
        ),
    }
)
_BIDI_CONTROLS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"})
_CANONICAL_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class UnknownWorkbenchCity(ValueError):
    """A requested workbench city identifier does not exist."""


class WorkbenchCityRegistryUnavailable(RuntimeError):
    """The workspace registry cannot be trusted or read."""


class PackagedCityCatalogUnavailable(RuntimeError):
    """The packaged city catalog cannot be trusted or read."""


def validate_workbench_city_classification(
    *,
    storage_origin: object,
    data_origin: object,
    qualification: object,
    reviewer_role: object,
    reviewed_on: object,
    source_published_on: str,
) -> None:
    """Enforce the closed workbench trust matrix and its review evidence."""
    classification = (storage_origin, data_origin, qualification)
    if classification not in _TRUST_CLASSIFICATIONS:
        raise ValueError("workbench city classification is invalid")

    if qualification != "rights-reviewed":
        if reviewer_role is not None or reviewed_on is not None:
            raise ValueError("unreviewed workbench cities cannot claim review evidence")
        return

    if type(reviewer_role) is not str or not reviewer_role.strip() or len(reviewer_role) > 120:
        raise ValueError("rights-reviewed workbench cities require review evidence")
    if any(
        ord(character) < 32
        or 127 <= ord(character) < 160
        or bidirectional(character) in _BIDI_CONTROLS
        for character in reviewer_role
    ) or contains_secret_or_email_text(reviewer_role):
        raise ValueError("rights-reviewed workbench cities require safe review evidence")
    if type(reviewed_on) is not str:
        raise ValueError("rights-reviewed workbench cities require review evidence")
    if _CANONICAL_DATE.fullmatch(reviewed_on) is None:
        raise ValueError("workbench city review evidence must use a canonical calendar date")
    try:
        review_date = date.fromisoformat(reviewed_on)
        source_date = date.fromisoformat(source_published_on)
    except ValueError:
        raise ValueError("workbench city review evidence must use calendar dates") from None
    if review_date < source_date:
        raise ValueError("workbench city review cannot predate source publication")


@dataclass(frozen=True, slots=True)
class ResolvedWorkbenchCity:
    """An exact city pack paired with its operational trust classification."""

    pack: CityPackV2
    storage_origin: StorageOrigin
    data_origin: WorkbenchDataOrigin
    qualification: WorkbenchQualification
    reviewer_role: str | None
    reviewed_on: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.pack, CityPackV2):
            raise TypeError("resolved workbench city requires a CityPackV2")
        validate_workbench_city_classification(
            storage_origin=self.storage_origin,
            data_origin=self.data_origin,
            qualification=self.qualification,
            reviewer_role=self.reviewer_role,
            reviewed_on=self.reviewed_on,
            source_published_on=self.pack.source.published_on,
        )


def _resolve_packaged_city(verified: VerifiedCatalogCity) -> ResolvedWorkbenchCity:
    entry = verified.entry
    return ResolvedWorkbenchCity(
        pack=verified.pack,
        storage_origin="package",
        data_origin=entry.data_origin,
        qualification=entry.qualification,
        reviewer_role=entry.reviewer_role,
        reviewed_on=entry.reviewed_on,
    )


def load_packaged_workbench_cities() -> tuple[ResolvedWorkbenchCity, ...]:
    """Translate one verified packaged snapshot without exposing resource paths."""
    try:
        verified = load_verified_catalog_cities()
    except CityCatalogError:
        raise PackagedCityCatalogUnavailable("the packaged city catalog is unavailable") from None
    return tuple(_resolve_packaged_city(city) for city in verified)


def select_packaged_workbench_city(city_id: str) -> ResolvedWorkbenchCity:
    """Resolve one packaged city from one verified snapshot operation."""
    selected = next(
        (city for city in load_packaged_workbench_cities() if city.pack.city_id == city_id),
        None,
    )
    if selected is None:
        raise UnknownWorkbenchCity("workbench city identifier is unknown")
    return selected


__all__ = [
    "PackagedCityCatalogUnavailable",
    "ResolvedWorkbenchCity",
    "StorageOrigin",
    "UnknownWorkbenchCity",
    "WorkbenchCityRegistryUnavailable",
    "WorkbenchDataOrigin",
    "WorkbenchQualification",
    "load_packaged_workbench_cities",
    "select_packaged_workbench_city",
    "validate_workbench_city_classification",
]
