"""Browser-safe translation and read DTOs for the local city workbench."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import ClassVar, Literal, Self

from pydantic import Field, model_validator

from adlife.city.catalog import (
    CityCatalogError,
    load_city_catalog,
    select_catalog_city,
)
from adlife.city.workbench_input import WorkbenchRunDraft
from adlife.core.domain.city import CityBounds, CityPackV2, CitySource
from adlife.core.domain.city_catalog import (
    CityCatalogEntry,
    DataOrigin,
    Qualification,
)
from adlife.core.domain.person import DomainModel

_CANONICAL_SEED = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_MAX_SEED = 2**63 - 1
_HASH_PATTERN = r"^[0-9a-f]{64}$"


class WorkbenchDraftTransportError(ValueError):
    """A value-free browser transport refusal safe to expose to a client."""

    field: ClassVar[Literal["settings.seed"]] = "settings.seed"
    message: ClassVar[str] = "Enter a whole-number seed from 0 through 9223372036854775807."

    def __init__(self) -> None:
        super().__init__(self.message)


def parse_workbench_http_draft(payload: Mapping[str, object]) -> WorkbenchRunDraft:
    """Translate the browser's exact decimal seed into the strict internal draft."""
    settings = payload.get("settings")
    if not isinstance(settings, Mapping) or "seed" not in settings:
        raise WorkbenchDraftTransportError
    seed_token = settings["seed"]
    if type(seed_token) is not str or _CANONICAL_SEED.fullmatch(seed_token) is None:
        raise WorkbenchDraftTransportError
    seed = int(seed_token)
    if seed > _MAX_SEED:
        raise WorkbenchDraftTransportError

    translated = dict(payload)
    translated_settings = dict(settings)
    translated_settings["seed"] = seed
    translated["settings"] = translated_settings
    encoded = json.dumps(
        translated,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return WorkbenchRunDraft.model_validate_json(encoded)


class WorkbenchCityDetail(DomainModel):
    """Verified path-free catalog metadata and the selected immutable city pack."""

    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    display_name: str = Field(min_length=1, max_length=120)
    pack_schema_version: Literal[2] = 2
    data_origin: DataOrigin
    qualification: Qualification
    reviewer_role: str | None = Field(default=None, min_length=1, max_length=120)
    reviewed_on: str | None = Field(
        default=None,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    )
    coverage: CityBounds
    time_zone: str = Field(min_length=1, max_length=64)
    source: CitySource
    known_omissions: tuple[str, ...] = Field(min_length=1, max_length=16)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    pack: CityPackV2

    @model_validator(mode="after")
    def catalog_metadata_matches_pack(self) -> Self:
        # Reuse the catalog entry's complete safety and qualification policy instead
        # of maintaining a second public-text/date validator at this boundary.
        CityCatalogEntry(
            city_id=self.city_id,
            display_name=self.display_name,
            resource_name="verified-city.json",
            pack_sha256=self.city_sha256,
            pack_schema_version=self.pack_schema_version,
            data_origin=self.data_origin,
            qualification=self.qualification,
            reviewer_role=self.reviewer_role,
            reviewed_on=self.reviewed_on,
            coverage=self.coverage,
            time_zone=self.time_zone,
            source=self.source,
            known_omissions=self.known_omissions,
        )
        if (
            self.city_sha256 != self.pack.fingerprint
            or self.pack_schema_version != self.pack.schema_version
            or self.city_id != self.pack.city_id
            or self.display_name != self.pack.name
            or self.coverage != self.pack.bounds
            or self.time_zone != self.pack.time_zone
            or self.source != self.pack.source
            or self.known_omissions != self.pack.known_omissions
        ):
            raise ValueError("workbench city detail does not match its verified pack")
        return self


def build_workbench_city_detail(city_id: str) -> WorkbenchCityDetail:
    """Build one path-free detail after independently cross-checking catalog selection."""
    catalog = load_city_catalog()
    pack = select_catalog_city(city_id)
    entry = next((item for item in catalog.entries if item.city_id == city_id), None)
    if entry is None or (
        entry.pack_sha256 != pack.fingerprint
        or entry.pack_schema_version != pack.schema_version
        or entry.city_id != pack.city_id
        or entry.display_name != pack.name
        or entry.coverage != pack.bounds
        or entry.time_zone != pack.time_zone
        or entry.source != pack.source
        or entry.known_omissions != pack.known_omissions
    ):
        raise CityCatalogError("city catalog detail failed integrity validation")
    return WorkbenchCityDetail(
        city_id=entry.city_id,
        display_name=entry.display_name,
        pack_schema_version=entry.pack_schema_version,
        data_origin=entry.data_origin,
        qualification=entry.qualification,
        reviewer_role=entry.reviewer_role,
        reviewed_on=entry.reviewed_on,
        coverage=entry.coverage,
        time_zone=entry.time_zone,
        source=entry.source,
        known_omissions=entry.known_omissions,
        city_sha256=entry.pack_sha256,
        pack=pack,
    )


__all__ = [
    "WorkbenchCityDetail",
    "WorkbenchDraftTransportError",
    "build_workbench_city_detail",
    "parse_workbench_http_draft",
]
