"""Pinned, verified, and bounded run access for the local city workbench."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.runs import PreparedCityRun, prepare_city_run, publish_city_run
from adlife.city.workbench_validation import ValidatedWorkbenchRun
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    WorkbenchWorkspace,
    verify_workbench_workspace,
)
from adlife.core.domain.person import DomainModel
from adlife.core.ports.run_store import (
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
    validate_run_id,
)

MAX_DISCOVERY_CANDIDATES = 256
MAX_RUN_PAGE_LIMIT = 100
MAX_RUN_PAGE_OFFSET = 10_000
DEFAULT_RUN_PAGE_LIMIT = 20

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_SEED_PATTERN = r"^(?:0|[1-9][0-9]{0,18})$"
WorkbenchChannel = Literal["mobile-feed", "roadside-billboard"]

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class WorkbenchRunSummary(DomainModel):
    """Path-free public facts from one freshly verified completed artifact."""

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    run_schema_version: Literal[1, 2, 3, 4, 5, 6, 7]
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    city_schema_version: Literal[1, 2]
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    scenario_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,79}$",
    )
    scenario_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    campaign_ids: tuple[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")], ...] = Field(
        default=(), max_length=20
    )
    channels: tuple[WorkbenchChannel, ...] = Field(default=(), max_length=2)
    seed: str = Field(pattern=_SEED_PATTERN, max_length=19)
    days: int = Field(ge=1, le=7)
    agent_count: int = Field(ge=1, le=30)
    frame_count: int = Field(ge=1, le=10_080)
    opportunity_count: int = Field(ge=0, le=520_800)
    impression_count: int = Field(ge=0, le=520_800)
    noticed_count: int = Field(ge=0, le=520_800)
    response_count: int = Field(ge=0, le=520_800)
    inspector_url: str = Field(pattern=r"^/runs/[a-z0-9][a-z0-9-]{0,39}$", max_length=46)

    @field_validator("run_schema_version", "city_schema_version", mode="before")
    @classmethod
    def exact_schema_versions(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("run summary schema versions must be exact integers")
        return value

    @field_validator("seed")
    @classmethod
    def bounded_decimal_seed(cls, value: str) -> str:
        if int(value) > 2**63 - 1:
            raise ValueError("run summary seed exceeds the supported range")
        return value

    @model_validator(mode="after")
    def canonical_collections_and_counts(self) -> Self:
        if self.frame_count != self.days * 1_440:
            raise ValueError("run summary frame count does not match its duration")
        if self.run_schema_version == 1 and self.city_schema_version != 1:
            raise ValueError("run summary schema 1 requires city schema 1")
        if self.run_schema_version == 2 and self.city_schema_version != 2:
            raise ValueError("run summary schema 2 requires city schema 2")
        if self.campaign_ids != tuple(sorted(set(self.campaign_ids))):
            raise ValueError("run summary campaign identifiers must be unique and canonical")
        if self.channels != tuple(sorted(set(self.channels))):
            raise ValueError("run summary channels must be unique and canonical")
        if self.run_schema_version < 4 and self.opportunity_count != 0:
            raise ValueError("legacy run summary cannot carry opportunity evidence")
        if self.run_schema_version >= 5:
            if self.impression_count != self.opportunity_count:
                raise ValueError("run summary impressions must match opportunities")
        elif self.impression_count != 0 or self.noticed_count != 0:
            raise ValueError("run summary cannot carry unavailable attention evidence")
        if self.noticed_count > self.impression_count:
            raise ValueError("run summary notices cannot exceed impressions")
        if self.run_schema_version >= 6:
            if self.response_count != self.noticed_count:
                raise ValueError("run summary responses must match notices")
        elif self.response_count != 0:
            raise ValueError("run summary cannot carry unavailable response evidence")
        if (self.scenario_id is None) != (self.scenario_sha256 is None):
            raise ValueError("run summary scenario binding must be complete")
        if self.run_schema_version >= 4 and self.scenario_id is None:
            raise ValueError("spatial run summary requires a scenario")
        if self.run_schema_version >= 4 and not self.campaign_ids:
            raise ValueError("spatial run summary requires at least one campaign")
        if self.run_schema_version >= 4 and not self.channels:
            raise ValueError("spatial run summary requires at least one channel")
        if self.run_schema_version < 4 and (
            self.scenario_id is not None or self.campaign_ids or self.channels
        ):
            raise ValueError("legacy run summary cannot carry spatial identities")
        if self.inspector_url != f"/runs/{self.run_id}":
            raise ValueError("run summary inspector URL does not match its run identifier")
        return self


class WorkbenchRunPage(DomainModel):
    """A bounded page over the verified subset of one bounded directory scan."""

    schema_version: Literal[1] = 1
    offset: int = Field(ge=0, le=MAX_RUN_PAGE_OFFSET)
    limit: int = Field(ge=1, le=MAX_RUN_PAGE_LIMIT)
    returned_count: int = Field(ge=0, le=MAX_RUN_PAGE_LIMIT)
    verified_total: int = Field(ge=0, le=MAX_DISCOVERY_CANDIDATES)
    scanned_count: int = Field(ge=0, le=MAX_DISCOVERY_CANDIDATES)
    corrupt_count: int = Field(ge=0, le=MAX_DISCOVERY_CANDIDATES)
    truncated: bool
    runs: tuple[WorkbenchRunSummary, ...] = Field(max_length=MAX_RUN_PAGE_LIMIT)

    @field_validator(
        "offset",
        "limit",
        "returned_count",
        "verified_total",
        "scanned_count",
        "corrupt_count",
        mode="before",
    )
    @classmethod
    def exact_integers(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("run page counts must be exact integers")
        return value

    @model_validator(mode="after")
    def internally_consistent(self) -> Self:
        if self.returned_count != len(self.runs):
            raise ValueError("run page returned count does not match its runs")
        if self.returned_count > self.limit:
            raise ValueError("run page exceeds its requested limit")
        if self.returned_count > max(self.verified_total - self.offset, 0):
            raise ValueError("run page exceeds its verified result window")
        if self.scanned_count != self.verified_total + self.corrupt_count:
            raise ValueError("run page scan counts are inconsistent")
        if self.truncated and self.scanned_count != MAX_DISCOVERY_CANDIDATES:
            raise ValueError("truncated run page must report a complete bounded scan")
        run_ids = tuple(item.run_id for item in self.runs)
        if run_ids != tuple(sorted(set(run_ids))):
            raise ValueError("run page entries must be ordered by run identifier")
        return self


def _summary(stored: StoredCityRun) -> WorkbenchRunSummary:
    manifest = stored.manifest
    scenario = stored.spatial_scenario
    campaign_ids = (
        ()
        if scenario is None
        else tuple(sorted(campaign.campaign_id for campaign in scenario.campaigns))
    )
    channels: tuple[WorkbenchChannel, ...] = (
        ()
        if scenario is None
        else tuple(sorted({placement.channel for placement in scenario.placements}))
    )
    return WorkbenchRunSummary(
        run_id=manifest.run_id,
        run_schema_version=manifest.schema_version,
        city_id=stored.pack.city_id,
        city_schema_version=stored.pack.schema_version,
        city_sha256=manifest.city_sha256,
        scenario_id=None if scenario is None else scenario.scenario_id,
        scenario_sha256=None if scenario is None else scenario.fingerprint,
        campaign_ids=campaign_ids,
        channels=channels,
        seed=str(manifest.seed),
        days=manifest.days,
        agent_count=manifest.agent_count,
        frame_count=manifest.frame_count,
        opportunity_count=int(getattr(manifest, "opportunity_count", 0)),
        impression_count=int(getattr(manifest, "impression_count", 0)),
        noticed_count=int(getattr(manifest, "noticed_count", 0)),
        response_count=int(getattr(manifest, "response_count", 0)),
        inspector_url=f"/runs/{manifest.run_id}",
    )


def _pagination(offset: object, limit: object) -> tuple[int, int]:
    if (
        type(offset) is not int
        or not 0 <= offset <= MAX_RUN_PAGE_OFFSET
        or type(limit) is not int
        or not 1 <= limit <= MAX_RUN_PAGE_LIMIT
    ):
        raise ValueError("workbench run pagination is invalid")
    return offset, limit


def _require_direct_directory(path: Path, *, label: str) -> None:
    status = os.lstat(path)
    attributes = int(getattr(status, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(status.st_mode)
        or attributes & _REPARSE_POINT
        or not stat.S_ISDIR(status.st_mode)
    ):
        raise UnsafeRunLocation(f"{label} is unsafe")


def _direct_directory_exists(path: Path, *, label: str) -> bool:
    try:
        _require_direct_directory(path, label=label)
    except FileNotFoundError:
        return False
    except OSError:
        raise UnsafeRunLocation(f"{label} is unreadable") from None
    return True


def _direct_location_exists(path: Path, *, label: str) -> bool:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        raise UnsafeRunLocation(f"{label} is unreadable") from None
    attributes = int(getattr(status, "st_file_attributes", 0))
    if stat.S_ISLNK(status.st_mode) or attributes & _REPARSE_POINT:
        raise UnsafeRunLocation(f"{label} is unsafe")
    return True


class WorkbenchRunRepository:
    """Recheck one pinned workspace around every artifact boundary."""

    def __init__(self, workspace: WorkbenchWorkspace) -> None:
        if not isinstance(workspace, WorkbenchWorkspace):
            raise TypeError("workspace must be a WorkbenchWorkspace")
        self._workspace = workspace

    def _root(self) -> Path:
        return verify_workbench_workspace(self._workspace)

    def ensure_available(self, run_id: str) -> None:
        """Refuse any existing location, including incomplete or corrupt artifacts."""
        validate_run_id(run_id)
        root = self._root()
        _direct_directory_exists(root / "city-runs", label="city-runs location")
        directory = CityRunStore(root).run_directory(run_id)
        if _direct_location_exists(directory, label="city run candidate"):
            raise DuplicateRun("city run identifier already exists")

    def prepare(self, validated: ValidatedWorkbenchRun) -> PreparedCityRun:
        """Evaluate one already frozen workbench request without publishing it."""
        if not isinstance(validated, ValidatedWorkbenchRun):
            raise TypeError("validated must be a ValidatedWorkbenchRun")
        self._root()
        settings = validated.workbench_input.draft.settings
        return prepare_city_run(
            validated.pack,
            run_id=settings.run_id,
            seed=settings.seed,
            agent_count=settings.agent_count,
            days=settings.days,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
        )

    def publish(self, prepared: PreparedCityRun) -> StoredCityRun:
        """Recheck collision and let the store perform final no-clobber publication."""
        if not isinstance(prepared, PreparedCityRun):
            raise TypeError("prepared must be a PreparedCityRun")
        self.ensure_available(prepared.manifest.run_id)
        root = self._root()
        _direct_directory_exists(root / "city-runs", label="city-runs location")
        return publish_city_run(prepared, root=root)

    def load(self, run_id: str) -> StoredCityRun:
        """Freshly load and verify one portable run identifier."""
        validate_run_id(run_id)
        root = self._root()
        runs_directory = root / "city-runs"
        store = CityRunStore(root)
        if _direct_directory_exists(runs_directory, label="city-runs location"):
            _direct_directory_exists(
                store.run_directory(run_id),
                label="city run candidate",
            )
        return store.load(run_id)

    def verified_summary(self, run_id: str) -> WorkbenchRunSummary:
        """Return path-free metadata only after a new full artifact verification."""
        return _summary(self.load(run_id))

    def list_runs(
        self,
        *,
        offset: int = 0,
        limit: int = DEFAULT_RUN_PAGE_LIMIT,
    ) -> WorkbenchRunPage:
        """Verify at most 256 direct candidates and page the stable verified subset."""
        offset, limit = _pagination(offset, limit)
        root = self._root()
        runs_directory = root / "city-runs"
        if not _direct_directory_exists(runs_directory, label="city-runs location"):
            return WorkbenchRunPage(
                offset=offset,
                limit=limit,
                returned_count=0,
                verified_total=0,
                scanned_count=0,
                corrupt_count=0,
                truncated=False,
                runs=(),
            )

        summaries: list[WorkbenchRunSummary] = []
        candidate_names: list[str] = []
        corrupt_count = 0
        truncated = False
        try:
            with os.scandir(runs_directory) as entries:
                for entry in entries:
                    if len(candidate_names) == MAX_DISCOVERY_CANDIDATES:
                        truncated = True
                        break
                    candidate_names.append(entry.name)
        except OSError:
            raise UnsafeRunLocation("city-runs directory is unreadable") from None

        for name in sorted(candidate_names):
            try:
                validate_run_id(name)
                _require_direct_directory(
                    runs_directory / name,
                    label="city run candidate",
                )
                summaries.append(self.verified_summary(name))
            except UnsafeWorkbenchWorkspace:
                raise
            except (OSError, StorageError, TypeError, ValueError):
                corrupt_count += 1

        selected = tuple(summaries[offset : offset + limit])
        return WorkbenchRunPage(
            offset=offset,
            limit=limit,
            returned_count=len(selected),
            verified_total=len(summaries),
            scanned_count=len(candidate_names),
            corrupt_count=corrupt_count,
            truncated=truncated,
            runs=selected,
        )


__all__ = [
    "DEFAULT_RUN_PAGE_LIMIT",
    "MAX_DISCOVERY_CANDIDATES",
    "MAX_RUN_PAGE_LIMIT",
    "MAX_RUN_PAGE_OFFSET",
    "WorkbenchRunPage",
    "WorkbenchRunRepository",
    "WorkbenchRunSummary",
]
