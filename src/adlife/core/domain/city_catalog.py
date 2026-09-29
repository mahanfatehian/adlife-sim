"""Strict metadata contract for the packaged, offline city catalog."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal, Self
from unicodedata import bidirectional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from adlife.core.domain.city import CityBounds, CitySource
from adlife.core.domain.person import DomainModel, contains_secret_or_email_text

Qualification = Literal["fictional-fixture", "rights-reviewed"]
DataOrigin = Literal["fictional", "real-world"]
CatalogOmission = Annotated[str, Field(min_length=1, max_length=200)]

_BIDI_CONTROLS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"})
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_RESERVED_RESOURCE_STEMS = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def _public_text(value: str) -> str:
    if not value.strip():
        raise ValueError("catalog text cannot be blank")
    if any(
        ord(character) < 32
        or 127 <= ord(character) < 160
        or bidirectional(character) in _BIDI_CONTROLS
        for character in value
    ):
        raise ValueError("catalog text contains a display control character")
    if contains_secret_or_email_text(value):
        raise ValueError("catalog text contains a credential or private identifier")
    return value


class CityCatalogEntry(DomainModel):
    """One content-addressed v2 pack and its explicit qualification status."""

    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    display_name: str = Field(min_length=1, max_length=120)
    resource_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}\.json$")
    pack_sha256: str = Field(pattern=_HASH_PATTERN)
    pack_schema_version: Literal[2] = 2
    data_origin: DataOrigin
    qualification: Qualification
    reviewer_role: str | None = Field(default=None, min_length=1, max_length=120)
    reviewed_on: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    coverage: CityBounds
    time_zone: str = Field(min_length=1, max_length=64)
    source: CitySource
    known_omissions: tuple[CatalogOmission, ...] = Field(min_length=1, max_length=16)

    @field_validator("display_name")
    @classmethod
    def safe_display_name(cls, value: str) -> str:
        return _public_text(value)

    @field_validator("pack_schema_version", mode="before")
    @classmethod
    def exact_pack_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("catalog pack_schema_version must be integer 2")
        return value

    @field_validator("reviewer_role")
    @classmethod
    def safe_reviewer_role(cls, value: str | None) -> str | None:
        return None if value is None else _public_text(value)

    @field_validator("resource_name")
    @classmethod
    def portable_resource_name(cls, value: str) -> str:
        if value.removesuffix(".json") in _RESERVED_RESOURCE_STEMS:
            raise ValueError("catalog resource name is reserved on Windows")
        return value

    @field_validator("reviewed_on")
    @classmethod
    def real_review_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("catalog reviewed_on must be a calendar date") from None
        return value

    @field_validator("time_zone")
    @classmethod
    def known_time_zone(cls, value: str) -> str:
        if not value.isascii() or any(character.isspace() for character in value):
            raise ValueError("catalog time zone must be an IANA identifier")
        try:
            zone = ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("catalog time zone must be an installed IANA identifier") from None
        if zone.key != value:
            raise ValueError("catalog time zone must be an IANA identifier")
        return value

    @field_validator("known_omissions")
    @classmethod
    def canonical_omissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_public_text(item) for item in value)
        if len(set(checked)) != len(checked):
            raise ValueError("catalog known omissions must be unique")
        return tuple(sorted(checked))

    @model_validator(mode="after")
    def honest_qualification(self) -> Self:
        has_role = self.reviewer_role is not None
        has_date = self.reviewed_on is not None
        if self.qualification == "fictional-fixture":
            if self.data_origin != "fictional":
                raise ValueError("fictional catalog entries require fictional data origin")
            if has_role or has_date:
                raise ValueError("fictional catalog entries cannot claim review evidence")
            return self

        if self.data_origin != "real-world":
            raise ValueError("rights-reviewed catalog entries require real-world data origin")
        if not (has_role and has_date):
            raise ValueError("rights-reviewed catalog entries require review role and date")
        assert self.reviewed_on is not None
        reviewed_on = date.fromisoformat(self.reviewed_on)
        published_on = date.fromisoformat(self.source.published_on)
        if reviewed_on < published_on:
            raise ValueError("rights review cannot predate the source publication")
        return self


class CityCatalog(DomainModel):
    """A small deterministic index; every referenced pack is verified by the adapter."""

    schema_version: Literal[1] = 1
    issued_on: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    entries: tuple[CityCatalogEntry, ...] = Field(min_length=1, max_length=50)

    @field_validator("issued_on")
    @classmethod
    def real_issuance_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("catalog issued_on must be a calendar date") from None
        return value

    @model_validator(mode="after")
    def unique_sorted_entries(self) -> Self:
        entries = tuple(sorted(self.entries, key=lambda entry: entry.city_id))
        issued_on = date.fromisoformat(self.issued_on)
        for entry in entries:
            if date.fromisoformat(entry.source.published_on) > issued_on:
                raise ValueError("source publication cannot follow catalog issued_on")
            if entry.reviewed_on is not None and date.fromisoformat(entry.reviewed_on) > issued_on:
                raise ValueError("rights review cannot follow catalog issued_on")
        for field in ("city_id", "resource_name", "pack_sha256"):
            values = tuple(getattr(entry, field) for entry in entries)
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate catalog entry {field}")
        object.__setattr__(self, "entries", entries)
        return self


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("city catalog JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"city catalog JSON contains non-finite constant {value}")


def parse_city_catalog_json(document: str | bytes) -> CityCatalog:
    """Parse the only supported catalog schema without coercing its version token."""
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("city catalog is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("city catalog must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int:
        raise ValueError("city catalog schema_version must be an integer")
    if version != 1:
        raise ValueError("unsupported city catalog schema_version")
    normalized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return CityCatalog.model_validate_json(normalized)


__all__ = [
    "CityCatalog",
    "CityCatalogEntry",
    "DataOrigin",
    "Qualification",
    "parse_city_catalog_json",
]
