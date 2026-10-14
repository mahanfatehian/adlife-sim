"""Browser-safe translation and read DTOs for the local city workbench."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import ClassVar, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from adlife.city.run_store import StoredCityRun
from adlife.city.workbench_binding import validate_workbench_execution_binding
from adlife.city.workbench_catalog import (
    ResolvedWorkbenchCity,
    StorageOrigin,
    WorkbenchDataOrigin,
    WorkbenchQualification,
    validate_workbench_city_classification,
)
from adlife.city.workbench_input import (
    CreativeTemplate,
    NormalizedWorkbenchPopulationV2,
    NormalizedWorkbenchScenarioDraft,
    WorkbenchCohortDraft,
    WorkbenchRunDraft,
    WorkbenchRunDraftDocument,
    WorkbenchRunDraftV2,
    WorkbenchRunDraftV3,
    WorkbenchRunInput,
    WorkbenchRunInputV2,
    _reject_structural_secrets,
)
from adlife.core.domain.city import CityBounds, CityPackV2, CitySource
from adlife.core.domain.city_run import CityRunManifestV7, CityRunManifestV8
from adlife.core.domain.identifiers import validate_portable_run_identifier
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

_CANONICAL_SEED = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_MAX_SEED = 2**63 - 1
_HASH_PATTERN = r"^[0-9a-f]{64}$"


def _exact_pack_schema_version(value: object) -> object:
    if type(value) is not int or value != 2:
        raise ValueError("pack_schema_version must be integer 2")
    return value


class WorkbenchDraftTransportError(ValueError):
    """A value-free browser transport refusal safe to expose to a client."""

    _MESSAGES: ClassVar[dict[Literal["request", "schema_version", "settings.seed"], str]] = {
        "request": "Request data cannot contain credential fields.",
        "schema_version": "Use workbench schema version 1, 2, or 3.",
        "settings.seed": "Enter a whole-number seed from 0 through 9223372036854775807.",
    }

    def __init__(
        self,
        field: Literal["request", "schema_version", "settings.seed"] = "settings.seed",
    ) -> None:
        self.field = field
        self.message = self._MESSAGES[field]
        super().__init__(self.message)


def parse_workbench_http_draft(payload: Mapping[str, object]) -> WorkbenchRunDraftDocument:
    """Translate the browser's exact decimal seed into the strict internal draft."""
    try:
        _reject_structural_secrets(payload)
    except ValueError:
        raise WorkbenchDraftTransportError("request") from None
    version = payload.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2, 3):
        raise WorkbenchDraftTransportError("schema_version")
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
    if version == 2:
        return WorkbenchRunDraftV2.model_validate_json(encoded)
    if version == 3:
        return WorkbenchRunDraftV3.model_validate_json(encoded)
    return WorkbenchRunDraft.model_validate_json(encoded)


class WorkbenchCityCatalogRow(DomainModel):
    """Path-free metadata for one exact city available to the workbench."""

    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    display_name: str = Field(min_length=1, max_length=120)
    pack_schema_version: Literal[2] = 2
    storage_origin: StorageOrigin
    data_origin: WorkbenchDataOrigin
    qualification: WorkbenchQualification
    reviewer_role: str | None = Field(default=None, min_length=1, max_length=120)
    reviewed_on: str | None = Field(
        default=None,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    )
    coverage: CityBounds
    time_zone: str = Field(min_length=1, max_length=64)
    source: CitySource
    known_omissions: tuple[str, ...] = Field(min_length=1, max_length=16)
    pack_sha256: str = Field(pattern=_HASH_PATTERN)

    @field_validator("pack_schema_version", mode="before")
    @classmethod
    def exact_pack_schema_version(cls, value: object) -> object:
        return _exact_pack_schema_version(value)

    @model_validator(mode="after")
    def valid_classification(self) -> Self:
        validate_workbench_city_classification(
            storage_origin=self.storage_origin,
            data_origin=self.data_origin,
            qualification=self.qualification,
            reviewer_role=self.reviewer_role,
            reviewed_on=self.reviewed_on,
            source_published_on=self.source.published_on,
        )
        return self


class WorkbenchCityDetail(DomainModel):
    """Verified path-free metadata and the selected immutable city pack."""

    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    display_name: str = Field(min_length=1, max_length=120)
    pack_schema_version: Literal[2] = 2
    storage_origin: StorageOrigin
    data_origin: WorkbenchDataOrigin
    qualification: WorkbenchQualification
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

    @field_validator("pack_schema_version", mode="before")
    @classmethod
    def exact_pack_schema_version(cls, value: object) -> object:
        return _exact_pack_schema_version(value)

    @model_validator(mode="after")
    def catalog_metadata_matches_pack(self) -> Self:
        validate_workbench_city_classification(
            storage_origin=self.storage_origin,
            data_origin=self.data_origin,
            qualification=self.qualification,
            reviewer_role=self.reviewer_role,
            reviewed_on=self.reviewed_on,
            source_published_on=self.source.published_on,
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


def build_workbench_city_catalog_row(city: ResolvedWorkbenchCity) -> WorkbenchCityCatalogRow:
    """Project one already-resolved city into the path-free list contract."""
    if not isinstance(city, ResolvedWorkbenchCity):
        raise TypeError("city must be a ResolvedWorkbenchCity")
    pack = city.pack
    return WorkbenchCityCatalogRow(
        city_id=pack.city_id,
        display_name=pack.name,
        pack_schema_version=pack.schema_version,
        storage_origin=city.storage_origin,
        data_origin=city.data_origin,
        qualification=city.qualification,
        reviewer_role=city.reviewer_role,
        reviewed_on=city.reviewed_on,
        coverage=pack.bounds,
        time_zone=pack.time_zone,
        source=pack.source,
        known_omissions=pack.known_omissions,
        pack_sha256=pack.fingerprint,
    )


def build_workbench_city_detail(city: ResolvedWorkbenchCity) -> WorkbenchCityDetail:
    """Project one already-resolved city into the path-free detail contract."""
    if not isinstance(city, ResolvedWorkbenchCity):
        raise TypeError("city must be a ResolvedWorkbenchCity")
    pack = city.pack
    return WorkbenchCityDetail(
        city_id=pack.city_id,
        display_name=pack.name,
        pack_schema_version=pack.schema_version,
        storage_origin=city.storage_origin,
        data_origin=city.data_origin,
        qualification=city.qualification,
        reviewer_role=city.reviewer_role,
        reviewed_on=city.reviewed_on,
        coverage=pack.bounds,
        time_zone=pack.time_zone,
        source=pack.source,
        known_omissions=pack.known_omissions,
        city_sha256=pack.fingerprint,
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


class WorkbenchRunInputViewV2(DomainModel):
    """Path-free browser projection of one fully verified schema-v8 input."""

    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    run_schema_version: Literal[8] = 8
    workbench_input_schema_version: Literal[2] = 2
    manifest_sha256: str = Field(pattern=_HASH_PATTERN)
    workbench_input_sha256: str = Field(pattern=_HASH_PATTERN)
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_sha256: str = Field(pattern=_HASH_PATTERN)
    creative_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario: NormalizedWorkbenchScenarioDraft
    population: NormalizedWorkbenchPopulationV2
    settings: WorkbenchRunSettingsView
    creative_template: CreativeTemplate

    @field_validator("run_schema_version", mode="before")
    @classmethod
    def exact_run_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 8:
            raise ValueError("run_schema_version must be integer 8")
        return value

    @field_validator("workbench_input_schema_version", mode="before")
    @classmethod
    def exact_input_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("workbench_input_schema_version must be integer 2")
        return value

    @model_validator(mode="after")
    def internally_consistent(self) -> Self:
        if self.run_id != self.settings.run_id:
            raise ValueError("workbench input view run identifier is inconsistent")
        if self.creative_sha256 != self.creative_template.fingerprint:
            raise ValueError("workbench input view creative hash is inconsistent")
        if self.scenario.campaign.creative_template_id != self.creative_template.template_id:
            raise ValueError("workbench input view creative template is inconsistent")
        if sum(persona.count for persona in self.population.personas) != self.settings.agent_count:
            raise ValueError("workbench input view population count is inconsistent")
        return self


WorkbenchRunInputViewDocument: TypeAlias = WorkbenchRunInputView | WorkbenchRunInputViewV2


def build_workbench_run_input_view(stored: StoredCityRun) -> WorkbenchRunInputViewDocument:
    """Project one verified v7/v8 record after independently rechecking its binding."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    manifest = stored.manifest
    workbench_input = stored.workbench_input
    if not isinstance(manifest, (CityRunManifestV7, CityRunManifestV8)):
        raise ValueError("workbench input view requires a schema-v7 or schema-v8 run")
    if workbench_input is None:
        raise ValueError("workbench input view requires its frozen workbench input")
    try:
        bound_input = validate_workbench_execution_binding(
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
    if isinstance(manifest, CityRunManifestV7) and not isinstance(bound_input, WorkbenchRunInput):
        raise ValueError("workbench input view requires workbench input schema 1")
    if isinstance(manifest, CityRunManifestV8) and not isinstance(bound_input, WorkbenchRunInputV2):
        raise ValueError("workbench input view requires workbench input schema 2")
    workbench_input = bound_input
    scenario = stored.spatial_scenario
    if scenario is None:
        raise ValueError("workbench input view requires its bound scenario")
    creative = workbench_input.creative_template
    if (
        manifest.workbench_input_schema_version != workbench_input.schema_version
        or manifest.workbench_input_sha256 != workbench_input.fingerprint
        or manifest.city_sha256 != stored.pack.fingerprint
        or manifest.scenario_sha256 != scenario.fingerprint
        or stored.response_input is None
        or manifest.response_input_sha256 != stored.response_input.fingerprint
        or len(scenario.campaigns) != 1
        or scenario.campaigns[0].creative_sha256 != creative.fingerprint
    ):
        raise ValueError("workbench input view hashes are inconsistent")
    draft = workbench_input.draft
    settings = draft.settings
    common = dict(
        run_id=manifest.run_id,
        manifest_sha256=sha256((canonical_json(manifest) + "\n").encode("utf-8")).hexdigest(),
        workbench_input_sha256=workbench_input.fingerprint,
        city_sha256=stored.pack.fingerprint,
        scenario_sha256=scenario.fingerprint,
        creative_sha256=creative.fingerprint,
        scenario=draft.scenario,
        settings=WorkbenchRunSettingsView(
            run_id=settings.run_id,
            agent_count=settings.agent_count,
            days=settings.days,
            seed=str(settings.seed),
            response_mode=settings.response_mode,
        ),
        creative_template=creative,
    )
    if isinstance(workbench_input, WorkbenchRunInputV2):
        return WorkbenchRunInputViewV2.model_validate(
            {**common, "population": workbench_input.draft.population}
        )
    return WorkbenchRunInputView.model_validate({**common, "cohort": workbench_input.draft.cohort})


__all__ = [
    "WorkbenchCityCatalogRow",
    "WorkbenchCityDetail",
    "WorkbenchDraftTransportError",
    "WorkbenchRunInputView",
    "WorkbenchRunInputViewDocument",
    "WorkbenchRunInputViewV2",
    "WorkbenchRunSettingsView",
    "build_workbench_city_catalog_row",
    "build_workbench_city_detail",
    "build_workbench_run_input_view",
    "parse_workbench_http_draft",
]
