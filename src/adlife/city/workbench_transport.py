"""Browser-safe translation and read DTOs for the local city workbench."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import ClassVar, Literal, Self

from pydantic import Field, field_validator, model_validator

from adlife.city.catalog import (
    CityCatalogError,
    load_city_catalog,
    select_catalog_city,
)
from adlife.city.run_store import StoredCityRun
from adlife.city.workbench_binding import validate_workbench_execution_binding
from adlife.city.workbench_input import (
    CreativeTemplate,
    NormalizedWorkbenchScenarioDraft,
    WorkbenchCohortDraft,
    WorkbenchRunDraft,
)
from adlife.core.domain.city import CityBounds, CityPackV2, CitySource
from adlife.core.domain.city_catalog import (
    CityCatalogEntry,
    DataOrigin,
    Qualification,
)
from adlife.core.domain.city_run import CityRunManifestV7
from adlife.core.domain.identifiers import validate_portable_run_identifier
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

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


class WorkbenchRunSettingsView(DomainModel):
    """Browser projection of immutable execution settings with an exact seed token."""

    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    agent_count: int = Field(ge=1, le=30)
    days: int = Field(ge=1, le=7)
    seed: str = Field(pattern=_CANONICAL_SEED.pattern, max_length=19)
    response_mode: Literal["deterministic-rules"] = "deterministic-rules"

    @field_validator("run_id")
    @classmethod
    def portable_run_id(cls, value: str) -> str:
        return validate_portable_run_identifier(value)

    @field_validator("seed")
    @classmethod
    def bounded_seed(cls, value: str) -> str:
        if int(value) > _MAX_SEED:
            raise ValueError("workbench input view seed exceeds its supported range")
        return value


class WorkbenchRunInputView(DomainModel):
    """Path-free browser projection of one fully verified schema-v7 input."""

    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    run_schema_version: Literal[7] = 7
    workbench_input_schema_version: Literal[1] = 1
    manifest_sha256: str = Field(pattern=_HASH_PATTERN)
    workbench_input_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    creative_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario: NormalizedWorkbenchScenarioDraft
    cohort: WorkbenchCohortDraft
    settings: WorkbenchRunSettingsView
    creative_template: CreativeTemplate

    @field_validator("run_schema_version", mode="before")
    @classmethod
    def exact_run_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 7:
            raise ValueError("run_schema_version must be integer 7")
        return value

    @field_validator("workbench_input_schema_version", mode="before")
    @classmethod
    def exact_input_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("workbench_input_schema_version must be integer 1")
        return value

    @model_validator(mode="after")
    def internally_consistent(self) -> Self:
        if self.run_id != self.settings.run_id:
            raise ValueError("workbench input view run identifier is inconsistent")
        if self.creative_sha256 != self.creative_template.fingerprint:
            raise ValueError("workbench input view creative hash is inconsistent")
        if self.scenario.campaign.creative_template_id != self.creative_template.template_id:
            raise ValueError("workbench input view creative template is inconsistent")
        return self


def build_workbench_run_input_view(stored: StoredCityRun) -> WorkbenchRunInputView:
    """Project one already verified v7 record after rechecking every input binding."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    manifest = stored.manifest
    workbench_input = stored.workbench_input
    if not isinstance(manifest, CityRunManifestV7):
        raise ValueError("workbench input view requires a schema-v7 run")
    if workbench_input is None:
        raise ValueError("workbench input view requires its frozen workbench input")
    try:
        workbench_input = validate_workbench_execution_binding(
            workbench_input,
            pack=stored.pack,
            run_id=manifest.run_id,
            seed=manifest.seed,
            agent_count=manifest.agent_count,
            days=manifest.days,
            places=stored.mobility.places,
            spatial_scenario=stored.spatial_scenario,
            spatial_response=stored.response_input,
        )
    except ValueError:
        raise ValueError("workbench input view binding is invalid") from None
    scenario = stored.spatial_scenario
    if scenario is None:
        raise ValueError("workbench input view requires its bound scenario")
    creative = workbench_input.creative_template
    if (
        manifest.workbench_input_schema_version != workbench_input.schema_version
        or manifest.workbench_input_sha256 != workbench_input.fingerprint
        or manifest.city_sha256 != stored.pack.fingerprint
        or manifest.scenario_sha256 != scenario.fingerprint
        or len(scenario.campaigns) != 1
        or scenario.campaigns[0].creative_sha256 != creative.fingerprint
    ):
        raise ValueError("workbench input view hashes are inconsistent")
    draft = workbench_input.draft
    settings = draft.settings
    return WorkbenchRunInputView(
        run_id=manifest.run_id,
        manifest_sha256=sha256((canonical_json(manifest) + "\n").encode("utf-8")).hexdigest(),
        workbench_input_sha256=workbench_input.fingerprint,
        city_sha256=stored.pack.fingerprint,
        scenario_sha256=scenario.fingerprint,
        creative_sha256=creative.fingerprint,
        scenario=draft.scenario,
        cohort=draft.cohort,
        settings=WorkbenchRunSettingsView(
            run_id=settings.run_id,
            agent_count=settings.agent_count,
            days=settings.days,
            seed=str(settings.seed),
            response_mode=settings.response_mode,
        ),
        creative_template=creative,
    )


__all__ = [
    "WorkbenchCityDetail",
    "WorkbenchDraftTransportError",
    "WorkbenchRunInputView",
    "WorkbenchRunSettingsView",
    "build_workbench_city_detail",
    "build_workbench_run_input_view",
    "parse_workbench_http_draft",
]
