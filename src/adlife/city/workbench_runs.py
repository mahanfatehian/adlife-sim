"""Pinned, verified, and bounded run access for the local city workbench."""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from adlife.adapters.cognition.city_cache import CityCognitionCache
from adlife.adapters.cognition.city_credentials import (
    CityCredentialStore,
    OSKeyringCityCredentialStore,
    ResolvedCredential,
)
from adlife.adapters.cognition.city_endpoint_policy import (
    BoundProviderEndpoint,
    CityEndpointPolicy,
)
from adlife.adapters.cognition.city_openai_compatible import CityOpenAICompatibleProvider
from adlife.adapters.cognition.city_service import (
    CityCognitionCancellation,
    CityCognitionService,
)
from adlife.city.provider_profiles import (
    ProviderProfileRegistry,
    ProviderProfileRevisionV1,
)
from adlife.city.provider_runtime import ProviderRuntimePolicy, build_provider_runtime_policy
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.runs import (
    PreparedCityRun,
    PreparedCityScientificRun,
    finalize_city_run,
    prepare_city_run,
    prepare_city_scientific_run,
    publish_city_run,
)
from adlife.city.workbench_input import WorkbenchRunInputV3
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
from adlife.core.simulation.city_cognition import allocate_city_cognition_reservations

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
    run_schema_version: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9]
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
    cognition_mode: Literal["rules", "mock", "local", "remote"] | None = None
    provider_profile_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
    )
    provider_revision_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    aggregate_output_token_ceiling: int | None = Field(default=None, ge=0, le=92_160)
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
        if self.run_schema_version in {1, 2, 3} and self.opportunity_count != 0:
            raise ValueError("legacy run summary cannot carry opportunity evidence")
        if self.run_schema_version in {5, 6, 7, 8, 9}:
            if self.impression_count != self.opportunity_count:
                raise ValueError("run summary impressions must match opportunities")
        elif self.impression_count != 0 or self.noticed_count != 0:
            raise ValueError("run summary cannot carry unavailable attention evidence")
        if self.noticed_count > self.impression_count:
            raise ValueError("run summary notices cannot exceed impressions")
        if self.run_schema_version in {6, 7, 8, 9}:
            if self.response_count != self.noticed_count:
                raise ValueError("run summary responses must match notices")
        elif self.response_count != 0:
            raise ValueError("run summary cannot carry unavailable response evidence")
        if (self.scenario_id is None) != (self.scenario_sha256 is None):
            raise ValueError("run summary scenario binding must be complete")
        if self.run_schema_version in {4, 5, 6, 7, 8, 9} and self.scenario_id is None:
            raise ValueError("spatial run summary requires a scenario")
        if self.run_schema_version in {4, 5, 6, 7, 8, 9} and not self.campaign_ids:
            raise ValueError("spatial run summary requires at least one campaign")
        if self.run_schema_version in {4, 5, 6, 7, 8, 9} and not self.channels:
            raise ValueError("spatial run summary requires at least one channel")
        if self.run_schema_version in {1, 2, 3} and (
            self.scenario_id is not None or self.campaign_ids or self.channels
        ):
            raise ValueError("legacy run summary cannot carry spatial identities")
        cognition_fields = (
            self.cognition_mode,
            self.provider_profile_id,
            self.provider_revision_sha256,
            self.aggregate_output_token_ceiling,
        )
        if self.run_schema_version != 9:
            if any(value is not None for value in cognition_fields):
                raise ValueError("pre-V9 run summary cannot carry cognition settings")
        else:
            if self.cognition_mode is None or self.aggregate_output_token_ceiling is None:
                raise ValueError("V9 run summary requires cognition settings")
            provider_mode = self.cognition_mode in {"local", "remote"}
            if provider_mode != (
                self.provider_profile_id is not None and self.provider_revision_sha256 is not None
            ):
                raise ValueError("V9 run summary provider binding is incomplete or misplaced")
            if not provider_mode and self.aggregate_output_token_ceiling != 0:
                raise ValueError("offline V9 run summary cannot reserve provider tokens")
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
    if manifest.schema_version == 10:
        raise ValueError("V10 workbench runtime support is not available yet")
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
    cognition_input = stored.cognition_input
    provider_profile = None if cognition_input is None else cognition_input.provider_profile
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
        cognition_mode=None if cognition_input is None else cognition_input.mode,
        provider_profile_id=(None if provider_profile is None else provider_profile.profile_id),
        provider_revision_sha256=(
            None if provider_profile is None else provider_profile.revision_sha256
        ),
        aggregate_output_token_ceiling=(
            None if cognition_input is None else cognition_input.aggregate_output_token_ceiling
        ),
        inspector_url=f"/runs/{manifest.run_id}",
    )


@dataclass(slots=True)
class WorkbenchCognitionOperation:
    """Private operation-scoped provider material, never exposed by job DTOs."""

    profile: ProviderProfileRevisionV1 | None
    endpoint: BoundProviderEndpoint | None
    credential: ResolvedCredential | None

    def clear(self) -> None:
        if self.credential is not None:
            self.credential.clear()


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

    def __init__(
        self,
        workspace: WorkbenchWorkspace,
        *,
        provider_runtime: ProviderRuntimePolicy | None = None,
        provider_registry: ProviderProfileRegistry | None = None,
        credential_store: CityCredentialStore | None = None,
        endpoint_policy: CityEndpointPolicy | None = None,
        cognition_service: CityCognitionService | None = None,
    ) -> None:
        if not isinstance(workspace, WorkbenchWorkspace):
            raise TypeError("workspace must be a WorkbenchWorkspace")
        self._workspace = workspace
        runtime = build_provider_runtime_policy() if provider_runtime is None else provider_runtime
        self._provider_registry = (
            ProviderProfileRegistry(workspace) if provider_registry is None else provider_registry
        )
        self._credential_store = (
            OSKeyringCityCredentialStore(runtime) if credential_store is None else credential_store
        )
        self._endpoint_policy = (
            CityEndpointPolicy(runtime) if endpoint_policy is None else endpoint_policy
        )
        self._cognition_service = (
            CityCognitionService(cache=CityCognitionCache(workspace))
            if cognition_service is None
            else cognition_service
        )

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

    def preflight_cognition(
        self,
        validated: ValidatedWorkbenchRun,
    ) -> WorkbenchCognitionOperation:
        """Resolve immutable provider inputs before inspecting a run destination."""
        if not isinstance(validated, ValidatedWorkbenchRun) or not isinstance(
            validated.workbench_input, WorkbenchRunInputV3
        ):
            raise TypeError("cognition preflight requires a schema-3 workbench run")
        cognition = validated.workbench_input.draft.cognition
        if cognition.mode in {"rules", "mock"}:
            return WorkbenchCognitionOperation(profile=None, endpoint=None, credential=None)
        assert cognition.provider_profile_id is not None
        assert cognition.provider_revision_sha256 is not None
        profile = self._provider_registry.load(
            cognition.provider_profile_id,
            cognition.provider_revision_sha256,
        )
        endpoint = self._endpoint_policy.bind(profile)
        credential = self._credential_store.resolve(profile)
        return WorkbenchCognitionOperation(
            profile=profile,
            endpoint=endpoint,
            credential=credential,
        )

    def prepare(self, validated: ValidatedWorkbenchRun) -> PreparedCityRun:
        """Evaluate one already frozen workbench request without publishing it."""
        if not isinstance(validated, ValidatedWorkbenchRun):
            raise TypeError("validated must be a ValidatedWorkbenchRun")
        workbench_input = validated.workbench_input
        if isinstance(workbench_input, WorkbenchRunInputV3):
            raise ValueError("schema-3 workbench runs require cognition preparation")
        self._root()
        settings = workbench_input.draft.settings
        return prepare_city_run(
            validated.pack,
            run_id=settings.run_id,
            seed=settings.seed,
            agent_count=settings.agent_count,
            days=settings.days,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=workbench_input,
        )

    def prepare_scientific(
        self,
        validated: ValidatedWorkbenchRun,
        operation: WorkbenchCognitionOperation,
    ) -> PreparedCityScientificRun:
        """Compute rule-owned science without contacting a provider or publishing."""
        if not isinstance(validated, ValidatedWorkbenchRun) or not isinstance(
            validated.workbench_input, WorkbenchRunInputV3
        ):
            raise TypeError("scientific preparation requires a schema-3 workbench run")
        if not isinstance(operation, WorkbenchCognitionOperation):
            raise TypeError("operation must be a WorkbenchCognitionOperation")
        settings = validated.workbench_input.draft.settings
        self._root()
        return prepare_city_scientific_run(
            validated.pack,
            run_id=settings.run_id,
            seed=settings.seed,
            agent_count=settings.agent_count,
            days=settings.days,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
            provider_profile=operation.profile,
        )

    @staticmethod
    def select_explanations(
        prepared: PreparedCityScientificRun,
        operation: WorkbenchCognitionOperation,
    ) -> None:
        """Recheck the frozen selection and operation binding before async calls."""
        if not isinstance(prepared, PreparedCityScientificRun):
            raise TypeError("prepared must be a PreparedCityScientificRun")
        if not isinstance(operation, WorkbenchCognitionOperation):
            raise TypeError("operation must be a WorkbenchCognitionOperation")
        provider_mode = prepared.cognition_input.mode in {"local", "remote"}
        if provider_mode != (
            operation.profile is not None
            and operation.endpoint is not None
            and operation.credential is not None
        ):
            raise ValueError("cognition operation does not match the frozen input")
        profile = prepared.cognition_input.provider_profile
        if operation.profile is not None and (
            profile is None
            or profile.profile_id != operation.profile.profile_id
            or profile.revision_sha256 != operation.profile.revision_sha256
            or operation.credential is None
            or operation.credential.secret.cleared
        ):
            raise ValueError("cognition operation does not match the frozen provider")
        expected_reservations = allocate_city_cognition_reservations(
            prepared.cognition_requests,
            aggregate_output_token_ceiling=(
                prepared.cognition_input.aggregate_output_token_ceiling
            ),
        )
        if prepared.cognition_input.reservations != expected_reservations or tuple(
            reservation.request_id for reservation in prepared.cognition_input.reservations
        ) != tuple(request.request_id for request in prepared.cognition_requests):
            raise ValueError("cognition selection does not match its reservations")

    def resolve_cognition(
        self,
        prepared: PreparedCityScientificRun,
        operation: WorkbenchCognitionOperation,
        cancellation: CityCognitionCancellation,
    ) -> PreparedCityRun:
        """Resolve bounded explanations and finalize one publishable V9 artifact."""
        self.select_explanations(prepared, operation)
        provider = None
        try:
            if operation.profile is not None:
                assert operation.endpoint is not None
                assert operation.credential is not None
                provider = CityOpenAICompatibleProvider(
                    endpoint_policy=self._endpoint_policy,
                    endpoint=operation.endpoint,
                    cognition_input=prepared.cognition_input,
                    credential=operation.credential,
                )
            evaluation = asyncio.run(
                self._cognition_service.resolve_many(
                    prepared.cognition_input,
                    prepared.cognition_requests,
                    provider=provider,
                    cancellation=cancellation,
                )
            )
            return finalize_city_run(
                prepared,
                cognition_input=prepared.cognition_input,
                cognition_evaluation=evaluation,
            )
        finally:
            operation.clear()

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
