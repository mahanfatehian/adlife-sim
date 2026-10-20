"""Pinned, verified, and bounded run access for the local city workbench."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections import OrderedDict
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from inspect import isawaitable
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal, NamedTuple, Self

from pydantic import Field, field_validator, model_validator

from adlife.adapters.cognition.city_cache import CityCognitionCache
from adlife.adapters.cognition.city_cache_v2 import CityCognitionCacheV2
from adlife.adapters.cognition.city_credentials import (
    CityCredentialStore,
    OSKeyringCityCredentialStore,
    ResolvedCredential,
    SecretBuffer,
)
from adlife.adapters.cognition.city_endpoint_policy import (
    BoundProviderEndpoint,
    CityEndpointPolicy,
)
from adlife.adapters.cognition.city_openai_compatible import CityOpenAICompatibleProvider
from adlife.adapters.cognition.city_openai_compatible_v2 import CityOpenAICompatibleProviderV2
from adlife.adapters.cognition.city_service import (
    CityCognitionCancellation,
    CityCognitionService,
)
from adlife.adapters.cognition.city_service_v2 import CityCognitionServiceV2
from adlife.city.provider_compatibility import (
    WorkbenchProviderCompatibilityRegistry,
    build_workbench_provider_compatibility_key,
)
from adlife.city.provider_profiles import (
    ProviderProfileRegistry,
    ProviderProfileRevisionV1,
)
from adlife.city.provider_runtime import ProviderRuntimePolicy, build_provider_runtime_policy
from adlife.city.run_store import (
    CityRunStore,
    StoredCityRun,
    _hold_parents,
    _open_artifact,
    _safe_status,
)
from adlife.city.runs import (
    PreparedCityRun,
    PreparedCityScientificRun,
    PreparedCityScientificRunV2,
    finalize_city_run,
    finalize_city_run_v2,
    prepare_city_run,
    prepare_city_scientific_run,
    prepare_city_scientific_run_v2,
    publish_city_run,
)
from adlife.city.workbench_input import WorkbenchRunInputV3, WorkbenchRunInputV4
from adlife.city.workbench_providers import WorkbenchCheckProviderFactoryV2
from adlife.city.workbench_validation import ValidatedWorkbenchRun
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    WorkbenchWorkspace,
    verify_workbench_workspace,
)
from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
)
from adlife.core.domain.city_run import CityRunManifestV10
from adlife.core.domain.person import DomainModel
from adlife.core.ports.city_cognition import CityCognitionProviderV2
from adlife.core.ports.run_store import (
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
    validate_run_id,
)
from adlife.core.simulation.city_cognition import allocate_city_cognition_reservations
from adlife.core.simulation.city_cognition_v2 import allocate_city_cognition_reservations_v2
from adlife.core.simulation.spatial_response_v2 import SpatialResponseEvaluationV2

MAX_DISCOVERY_CANDIDATES = 256
MAX_RUN_PAGE_LIMIT = 100
MAX_RUN_PAGE_OFFSET = 10_000
DEFAULT_RUN_PAGE_LIMIT = 20

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_SEED_PATTERN = r"^(?:0|[1-9][0-9]{0,18})$"
WorkbenchChannel = Literal["mobile-feed", "roadside-billboard"]

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class WorkbenchRunSummary(DomainModel):
    """Path-free public facts from one fully verified completed artifact."""

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    run_schema_version: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    workbench_input_schema_version: Literal[4] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
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
    effect_scope: Literal["bounded-signals-v1"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    cognition_mode: Literal["rules", "mock", "local", "remote"] | None = None
    provider_profile_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
    )
    provider_revision_sha256: str | None = Field(default=None, pattern=_HASH_PATTERN)
    funded_response_limit: Literal[10, 20, 40, 60] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    aggregate_output_token_ceiling: int | None = Field(default=None, ge=0, le=92_160)
    eligible_response_count: int | None = Field(
        default=None,
        ge=0,
        le=520_800,
        exclude_if=lambda value: value is None,
    )
    selected_request_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    funded_request_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    budget_skipped_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    unselected_response_count: int | None = Field(
        default=None,
        ge=0,
        le=520_800,
        exclude_if=lambda value: value is None,
    )
    applied_response_count: int | None = Field(
        default=None,
        ge=0,
        le=520_800,
        exclude_if=lambda value: value is None,
    )
    rules_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    mock_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    provider_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    cache_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    fallback_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    neutralized_response_count: int | None = Field(
        default=None,
        ge=0,
        le=520_800,
        exclude_if=lambda value: value is None,
    )
    uncertain_result_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    safety_neutralized_result_count: int | None = Field(
        default=None,
        ge=0,
        le=60,
        exclude_if=lambda value: value is None,
    )
    inspector_url: str = Field(pattern=r"^/runs/[a-z0-9][a-z0-9-]{0,39}$", max_length=46)

    @field_validator(
        "run_schema_version",
        "workbench_input_schema_version",
        "city_schema_version",
        "funded_response_limit",
        "eligible_response_count",
        "selected_request_count",
        "funded_request_count",
        "budget_skipped_count",
        "unselected_response_count",
        "applied_response_count",
        "rules_count",
        "mock_count",
        "provider_count",
        "cache_count",
        "fallback_count",
        "neutralized_response_count",
        "uncertain_result_count",
        "safety_neutralized_result_count",
        mode="before",
    )
    @classmethod
    def exact_schema_versions(cls, value: object) -> object:
        if value is None:
            return value
        if type(value) is not int:
            raise ValueError("run summary version and count values must be exact integers")
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
        if self.run_schema_version == 10 and self.city_schema_version != 2:
            raise ValueError("run summary schema 10 requires city schema 2")
        if self.campaign_ids != tuple(sorted(set(self.campaign_ids))):
            raise ValueError("run summary campaign identifiers must be unique and canonical")
        if self.channels != tuple(sorted(set(self.channels))):
            raise ValueError("run summary channels must be unique and canonical")
        if self.run_schema_version in {1, 2, 3} and self.opportunity_count != 0:
            raise ValueError("legacy run summary cannot carry opportunity evidence")
        if self.run_schema_version in {5, 6, 7, 8, 9, 10}:
            if self.impression_count != self.opportunity_count:
                raise ValueError("run summary impressions must match opportunities")
        elif self.impression_count != 0 or self.noticed_count != 0:
            raise ValueError("run summary cannot carry unavailable attention evidence")
        if self.noticed_count > self.impression_count:
            raise ValueError("run summary notices cannot exceed impressions")
        if self.run_schema_version in {6, 7, 8, 9, 10}:
            if self.response_count != self.noticed_count:
                raise ValueError("run summary responses must match notices")
        elif self.response_count != 0:
            raise ValueError("run summary cannot carry unavailable response evidence")
        if (self.scenario_id is None) != (self.scenario_sha256 is None):
            raise ValueError("run summary scenario binding must be complete")
        if self.run_schema_version in {4, 5, 6, 7, 8, 9, 10} and self.scenario_id is None:
            raise ValueError("spatial run summary requires a scenario")
        if self.run_schema_version in {4, 5, 6, 7, 8, 9, 10} and not self.campaign_ids:
            raise ValueError("spatial run summary requires at least one campaign")
        if self.run_schema_version in {4, 5, 6, 7, 8, 9, 10} and not self.channels:
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
        v10_fields = (
            self.workbench_input_schema_version,
            self.effect_scope,
            self.funded_response_limit,
            self.eligible_response_count,
            self.selected_request_count,
            self.funded_request_count,
            self.budget_skipped_count,
            self.unselected_response_count,
            self.applied_response_count,
            self.rules_count,
            self.mock_count,
            self.provider_count,
            self.cache_count,
            self.fallback_count,
            self.neutralized_response_count,
            self.uncertain_result_count,
            self.safety_neutralized_result_count,
        )
        if self.run_schema_version not in {9, 10}:
            if any(value is not None for value in cognition_fields):
                raise ValueError("pre-V9 run summary cannot carry cognition settings")
        elif self.run_schema_version == 9:
            if self.cognition_mode is None or self.aggregate_output_token_ceiling is None:
                raise ValueError("V9 run summary requires cognition settings")
            provider_mode = self.cognition_mode in {"local", "remote"}
            if provider_mode != (
                self.provider_profile_id is not None and self.provider_revision_sha256 is not None
            ):
                raise ValueError("V9 run summary provider binding is incomplete or misplaced")
            if not provider_mode and self.aggregate_output_token_ceiling != 0:
                raise ValueError("offline V9 run summary cannot reserve provider tokens")
        else:
            if any(value is None for value in v10_fields):
                raise ValueError("V10 run summary requires complete bounded cognition evidence")
            if self.cognition_mode is None or self.aggregate_output_token_ceiling is None:
                raise ValueError("V10 run summary requires cognition settings")
            assert self.funded_response_limit is not None
            provider_mode = self.cognition_mode in {"local", "remote"}
            if provider_mode:
                if self.provider_profile_id is None or self.provider_revision_sha256 is None:
                    raise ValueError("V10 provider binding is incomplete")
                if (
                    self.aggregate_output_token_ceiling
                    != self.funded_response_limit * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
                ):
                    raise ValueError("V10 provider funding ceiling is inconsistent")
            elif (
                self.provider_profile_id is not None
                or self.provider_revision_sha256 is not None
                or self.funded_response_limit != 60
                or self.aggregate_output_token_ceiling != 0
            ):
                raise ValueError("offline V10 provider settings are misplaced")
            assert self.eligible_response_count is not None
            assert self.selected_request_count is not None
            assert self.funded_request_count is not None
            assert self.budget_skipped_count is not None
            assert self.unselected_response_count is not None
            assert self.applied_response_count is not None
            assert self.rules_count is not None
            assert self.mock_count is not None
            assert self.provider_count is not None
            assert self.cache_count is not None
            assert self.fallback_count is not None
            assert self.neutralized_response_count is not None
            assert self.uncertain_result_count is not None
            assert self.safety_neutralized_result_count is not None
            expected_funded = (
                min(self.selected_request_count, self.funded_response_limit)
                if provider_mode
                else self.selected_request_count
            )
            if (
                self.workbench_input_schema_version != 4
                or self.effect_scope != "bounded-signals-v1"
                or self.eligible_response_count != self.response_count
                or self.eligible_response_count
                != self.selected_request_count + self.unselected_response_count
                or self.selected_request_count
                != self.funded_request_count + self.budget_skipped_count
                or self.funded_request_count != expected_funded
                or self.budget_skipped_count != self.selected_request_count - expected_funded
                or self.applied_response_count != self.eligible_response_count
                or self.rules_count
                + self.mock_count
                + self.provider_count
                + self.cache_count
                + self.fallback_count
                + self.budget_skipped_count
                != self.selected_request_count
            ):
                raise ValueError("V10 bounded cognition counts are inconsistent")
            if self.cognition_mode == "rules" and (
                self.rules_count != self.selected_request_count
                or any(
                    (
                        self.mock_count,
                        self.provider_count,
                        self.cache_count,
                        self.fallback_count,
                        self.budget_skipped_count,
                    )
                )
            ):
                raise ValueError("V10 rules outcomes do not match mode")
            if self.cognition_mode == "mock" and (
                self.mock_count != self.selected_request_count
                or any(
                    (
                        self.rules_count,
                        self.provider_count,
                        self.cache_count,
                        self.fallback_count,
                        self.budget_skipped_count,
                    )
                )
            ):
                raise ValueError("V10 mock outcomes do not match mode")
            if provider_mode and (self.rules_count or self.mock_count):
                raise ValueError("V10 provider outcomes cannot carry offline sources")
            diagnostic_capacity = self.provider_count + self.cache_count
            always_neutral = (
                self.unselected_response_count
                + self.rules_count
                + self.fallback_count
                + self.budget_skipped_count
            )
            if (
                self.uncertain_result_count > diagnostic_capacity
                or self.safety_neutralized_result_count > diagnostic_capacity
                or not always_neutral
                + max(self.uncertain_result_count, self.safety_neutralized_result_count)
                <= self.neutralized_response_count
                <= always_neutral
                + min(
                    self.uncertain_result_count + self.safety_neutralized_result_count,
                    diagnostic_capacity,
                )
            ):
                raise ValueError("V10 neutralization counts are inconsistent")
        if self.run_schema_version != 10 and any(value is not None for value in v10_fields):
            raise ValueError("pre-V10 run summary cannot carry V10 evidence")
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
    cognition_input = stored.cognition_input
    cognition_evaluation = stored.cognition_evaluation
    if manifest.schema_version == 10:
        if (
            type(manifest) is not CityRunManifestV10
            or type(stored.workbench_input) is not WorkbenchRunInputV4
            or type(cognition_input) is not CityCognitionInputV2
            or type(cognition_evaluation) is not CityCognitionEvaluationV2
            or type(stored.response_evaluation) is not SpatialResponseEvaluationV2
        ):
            raise ValueError("V10 run is missing complete bounded cognition evidence")
        cognition_summary = cognition_evaluation.summary
        coverage = cognition_summary.coverage
        outcomes = cognition_summary.outcomes
        neutralization = cognition_summary.neutralization
        provider_profile = cognition_input.provider_profile
        return WorkbenchRunSummary(
            run_id=manifest.run_id,
            run_schema_version=10,
            workbench_input_schema_version=4,
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
            opportunity_count=manifest.opportunity_count,
            impression_count=manifest.impression_count,
            noticed_count=manifest.noticed_count,
            response_count=manifest.response_count,
            effect_scope=cognition_input.effect_scope,
            cognition_mode=cognition_input.mode,
            provider_profile_id=(None if provider_profile is None else provider_profile.profile_id),
            provider_revision_sha256=(
                None if provider_profile is None else provider_profile.revision_sha256
            ),
            funded_response_limit=cognition_input.funded_response_limit,
            aggregate_output_token_ceiling=cognition_input.aggregate_output_token_ceiling,
            eligible_response_count=coverage.eligible_response_count,
            selected_request_count=coverage.selected_request_count,
            funded_request_count=coverage.funded_request_count,
            budget_skipped_count=coverage.budget_skipped_count,
            unselected_response_count=coverage.unselected_response_count,
            applied_response_count=coverage.applied_response_count,
            rules_count=outcomes.rules,
            mock_count=outcomes.mock,
            provider_count=outcomes.provider,
            cache_count=outcomes.cache,
            fallback_count=outcomes.fallback,
            neutralized_response_count=neutralization.neutralized_response_count,
            uncertain_result_count=neutralization.uncertain_result_count,
            safety_neutralized_result_count=(neutralization.safety_neutralized_result_count),
            inspector_url=f"/runs/{manifest.run_id}",
        )
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


@dataclass(slots=True)
class WorkbenchCognitionOperationV2:
    """Private V2 operation material held only until one job reaches a terminal state."""

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


# The discovery cache vouches for a run only after reading every entry under the store's
# own pinned, no-follow rules. The bounds sit above the largest layout the store accepts
# (one level of sidecar directories, about twenty entries, stream limits summing to about
# 3.5 GiB); a run beyond them is simply verified in full on every listing.
_FINGERPRINT_MAX_ENTRIES = 64
_FINGERPRINT_MAX_BYTES = 4 * 1_073_741_824
_FINGERPRINT_CHUNK_BYTES = 1_048_576


class _UnfingerprintableRun(Exception):
    """A run directory holds something the discovery cache will not vouch for."""


class _ArtifactEntry(NamedTuple):
    """One exact entry relative to its run directory; a directory carries no bytes."""

    path: str
    size: int | None
    sha256: str | None


_ArtifactFingerprint = tuple[_ArtifactEntry, ...]
_ArtifactStamp = tuple[str, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _RunObservation:
    """An exact content fingerprint plus the file identities seen while reading it."""

    fingerprint: _ArtifactFingerprint
    stamps: tuple[_ArtifactStamp, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedRun:
    fingerprint: _ArtifactFingerprint
    summary: WorkbenchRunSummary
    generation: int


def _artifact_stamp(path: str, status: os.stat_result) -> _ArtifactStamp:
    return path, status.st_dev, status.st_ino, status.st_mtime_ns, status.st_ctime_ns


def _hash_artifact(path: Path, budget: int) -> tuple[int, str, os.stat_result]:
    """Hash one regular file through the store's pinned, link-refusing artifact read."""
    digest = sha256()
    size = 0
    with _open_artifact(path, "rb") as source:
        opened = os.fstat(source.fileno())
        if opened.st_size > budget:
            raise _UnfingerprintableRun("run directory exceeds the fingerprint budget")
        while chunk := source.read(_FINGERPRINT_CHUNK_BYTES):
            size += len(chunk)
            if size > opened.st_size:
                raise _UnfingerprintableRun("run artifact grew while it was fingerprinted")
            digest.update(chunk)
    if size != opened.st_size:
        raise _UnfingerprintableRun("run artifact shrank while it was fingerprinted")
    return size, digest.hexdigest(), opened


def _observe_run_directory(directory: Path) -> _RunObservation:
    """Read every entry under one run directory once, refusing links and reparse points."""
    entries: list[_ArtifactEntry] = []
    stamps: list[_ArtifactStamp] = []
    budget = _FINGERPRINT_MAX_BYTES
    seen = 0
    with ExitStack() as holds:
        holds.enter_context(_hold_parents(directory))
        status = os.lstat(directory)
        _safe_status(status, directory=True)
        stamps.append(_artifact_stamp("", status))
        pending = [(directory, "")]
        while pending:
            parent, prefix = pending.pop()
            names: list[str] = []
            with os.scandir(parent) as children:
                for child in children:
                    seen += 1
                    if seen > _FINGERPRINT_MAX_ENTRIES:
                        raise _UnfingerprintableRun("run directory has too many entries")
                    names.append(child.name)
            for name in sorted(names):
                path = parent / name
                relative = prefix + name
                status = os.lstat(path)
                if stat.S_ISDIR(status.st_mode):
                    if prefix:
                        raise _UnfingerprintableRun("run directory nests too deeply")
                    # Refuses a junction or other directory reparse point, then pins it.
                    _safe_status(status, directory=True)
                    holds.enter_context(_hold_parents(path))
                    entries.append(_ArtifactEntry(relative, None, None))
                    stamps.append(_artifact_stamp(relative, status))
                    pending.append((path, f"{relative}/"))
                    continue
                # Refuses links, reparse points, extra hard links, and non-regular files,
                # exactly as the store refuses them before reading an artifact.
                _safe_status(status, directory=False)
                size, digest, opened = _hash_artifact(path, budget)
                budget -= size
                entries.append(_ArtifactEntry(relative, size, digest))
                stamps.append(_artifact_stamp(relative, opened))
    return _RunObservation(
        fingerprint=tuple(sorted(entries, key=lambda entry: entry.path)),
        stamps=tuple(sorted(stamps)),
    )


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
        cognition_service_v2: CityCognitionServiceV2 | None = None,
        compatibility_registry: WorkbenchProviderCompatibilityRegistry | None = None,
        provider_factory_v2: WorkbenchCheckProviderFactoryV2 | None = None,
    ) -> None:
        if not isinstance(workspace, WorkbenchWorkspace):
            raise TypeError("workspace must be a WorkbenchWorkspace")
        self._workspace = workspace
        self.compatibility_registry = (
            WorkbenchProviderCompatibilityRegistry()
            if compatibility_registry is None
            else compatibility_registry
        )
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
        self._cognition_service_v2 = (
            CityCognitionServiceV2(cache=CityCognitionCacheV2(workspace))
            if cognition_service_v2 is None
            else cognition_service_v2
        )
        self._provider_factory_v2 = (
            CityOpenAICompatibleProviderV2 if provider_factory_v2 is None else provider_factory_v2
        )
        # Process-local and bounded: run identifier -> summary of one full verification,
        # valid only while the run's exact content fingerprint stays the same. The worker
        # thread seeds it while request threads list, so every access holds the lock.
        self._verified_runs: OrderedDict[str, _VerifiedRun] = OrderedDict()
        self._verified_lock = Lock()
        self._verified_generation = 0

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

    def preflight_cognition_v2(
        self,
        validated: ValidatedWorkbenchRun,
    ) -> WorkbenchCognitionOperationV2:
        """Authorize an exact bounded-signals profile before resolving private material."""
        if (
            not isinstance(validated, ValidatedWorkbenchRun)
            or type(validated.workbench_input) is not WorkbenchRunInputV4
        ):
            raise TypeError("V2 cognition preflight requires a schema-4 workbench run")
        cognition = validated.workbench_input.draft.cognition
        if cognition.mode in {"rules", "mock"}:
            return WorkbenchCognitionOperationV2(
                profile=None,
                endpoint=None,
                credential=None,
            )
        assert cognition.provider_profile_id is not None
        assert cognition.provider_revision_sha256 is not None
        profile = self._provider_registry.load(
            cognition.provider_profile_id,
            cognition.provider_revision_sha256,
        )
        self.compatibility_registry.require_success(
            build_workbench_provider_compatibility_key(
                profile,
                effect_scope="bounded-signals-v1",
            )
        )
        endpoint = self._endpoint_policy.bind(profile)
        credential = None
        try:
            credential = self._credential_store.resolve(profile)
            return WorkbenchCognitionOperationV2(
                profile=profile,
                endpoint=endpoint,
                credential=credential,
            )
        except BaseException:
            if credential is not None:
                credential.clear()
            raise

    def prepare(self, validated: ValidatedWorkbenchRun) -> PreparedCityRun:
        """Evaluate one already frozen workbench request without publishing it."""
        if not isinstance(validated, ValidatedWorkbenchRun):
            raise TypeError("validated must be a ValidatedWorkbenchRun")
        workbench_input = validated.workbench_input
        if isinstance(workbench_input, (WorkbenchRunInputV3, WorkbenchRunInputV4)):
            raise ValueError("schema-3 and schema-4 workbench runs require cognition preparation")
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

    def prepare_scientific_v2(
        self,
        validated: ValidatedWorkbenchRun,
        operation: WorkbenchCognitionOperationV2,
    ) -> PreparedCityScientificRunV2:
        """Prepare immutable V2 science without provider, cache, or artifact I/O."""
        if (
            not isinstance(validated, ValidatedWorkbenchRun)
            or type(validated.workbench_input) is not WorkbenchRunInputV4
        ):
            raise TypeError("V2 scientific preparation requires a schema-4 workbench run")
        if not isinstance(operation, WorkbenchCognitionOperationV2):
            raise TypeError("operation must be a WorkbenchCognitionOperationV2")
        settings = validated.workbench_input.draft.settings
        self._root()
        return prepare_city_scientific_run_v2(
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

    def _validate_v2_operation(
        self,
        prepared: PreparedCityScientificRunV2,
        operation: WorkbenchCognitionOperationV2,
    ) -> None:
        if type(prepared) is not PreparedCityScientificRunV2:
            raise TypeError("prepared must be a PreparedCityScientificRunV2")
        if not isinstance(operation, WorkbenchCognitionOperationV2):
            raise TypeError("operation must be a WorkbenchCognitionOperationV2")
        frozen = CityCognitionInputV2.model_validate(
            prepared.cognition_input.model_dump(mode="python")
        )
        provider_mode = frozen.mode in {"local", "remote"}
        material_present = (
            operation.profile is not None,
            operation.endpoint is not None,
            operation.credential is not None,
        )
        if provider_mode != all(material_present) or (not provider_mode and any(material_present)):
            raise ValueError("V2 cognition operation does not match the frozen mode")
        expected = allocate_city_cognition_reservations_v2(
            prepared.cognition_requests,
            mode=frozen.mode,
            funded_response_limit=frozen.funded_response_limit,
        )
        if frozen.reservations != expected:
            raise ValueError("V2 cognition funding does not match the frozen requests")
        if not provider_mode:
            return
        assert operation.profile is not None
        assert operation.endpoint is not None
        assert operation.credential is not None
        if (
            type(operation.profile) is not ProviderProfileRevisionV1
            or type(operation.endpoint) is not BoundProviderEndpoint
            or type(operation.credential) is not ResolvedCredential
            or type(operation.credential.secret) is not SecretBuffer
        ):
            raise ValueError("V2 cognition operation does not match the frozen provider")
        snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
            operation.profile.model_dump(mode="python")
        )
        rebound_endpoint = self._endpoint_policy.bind(operation.profile)
        endpoint = operation.endpoint
        credential = operation.credential
        if (
            frozen.provider_profile != snapshot
            or frozen.effective_settings is None
            or endpoint != rebound_endpoint
            or endpoint.profile_id != operation.profile.profile_id
            or endpoint.profile_revision_sha256 != operation.profile.revision_sha256
            or endpoint.origin_approval_id != operation.profile.origin_approval_id
            or endpoint.trust_class != operation.profile.mode
            or endpoint.base_url != operation.profile.base_url
            or credential.origin_approval_id != operation.profile.origin_approval_id
            or credential.credential_slot_id != operation.profile.credential_slot_id
            or credential.secret.cleared
            or (operation.profile.mode == "local" and credential.source != "local-dummy")
            or (
                operation.profile.mode == "remote"
                and credential.source not in {"environment", "keyring"}
            )
        ):
            raise ValueError("V2 cognition operation does not match the frozen provider")

    def resolve_cognition_v2(
        self,
        prepared: PreparedCityScientificRunV2,
        operation: WorkbenchCognitionOperationV2,
        cancellation: CityCognitionCancellation,
        *,
        progress: Callable[[], None] | None = None,
    ) -> CityCognitionEvaluationV2:
        """Resolve V2 terminal evidence while keeping provider ownership asynchronous.

        ``progress`` is passed to the cognition service unchanged; it is called once per
        funded provider request that reaches a terminal outcome and never alters evidence.
        """
        try:
            self._validate_v2_operation(prepared, operation)
            if not isinstance(cancellation, CityCognitionCancellation):
                raise TypeError("cancellation must be a CityCognitionCancellation")

            async def resolve() -> CityCognitionEvaluationV2:
                provider: CityCognitionProviderV2 | None = None
                if operation.profile is not None:
                    assert operation.endpoint is not None
                    assert operation.credential is not None
                    candidate = self._provider_factory_v2(
                        endpoint_policy=self._endpoint_policy,
                        endpoint=operation.endpoint,
                        cognition_input=prepared.cognition_input,
                        credential=operation.credential,
                    )
                    close = getattr(candidate, "aclose", None)
                    if not isinstance(candidate, CityCognitionProviderV2) or not callable(close):
                        if callable(close):
                            close_result = close()
                            if not isawaitable(close_result):
                                raise TypeError(
                                    "V2 provider close does not implement the runtime contract"
                                )
                            await close_result
                        raise TypeError("V2 provider does not implement the runtime contract")
                    provider = candidate
                return await self._cognition_service_v2.resolve_many(
                    prepared.cognition_input,
                    prepared.cognition_requests,
                    baseline_evaluation=prepared.baseline_evaluation,
                    provider=provider,
                    cancellation=cancellation,
                    progress=progress,
                )

            return asyncio.run(resolve())
        finally:
            if isinstance(operation, WorkbenchCognitionOperationV2):
                operation.clear()

    @staticmethod
    def finalize_cognition_v2(
        prepared: PreparedCityScientificRunV2,
        evaluation: CityCognitionEvaluationV2,
    ) -> PreparedCityRun:
        """Purely apply complete validated V2 evidence to the frozen rule baseline."""
        if type(prepared) is not PreparedCityScientificRunV2:
            raise TypeError("prepared must be a PreparedCityScientificRunV2")
        if type(evaluation) is not CityCognitionEvaluationV2:
            raise TypeError("evaluation must be a CityCognitionEvaluationV2")
        return finalize_city_run_v2(
            prepared,
            cognition_input=prepared.cognition_input,
            cognition_evaluation=evaluation,
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
        """Return path-free metadata only after a new full artifact verification.

        A job's verification step lands here, so a run whose exact bytes stayed the same
        around this verification also seeds the discovery cache.
        """
        validate_run_id(run_id)
        return self._verify_and_remember(run_id, self._observe(run_id))

    def _observe(self, run_id: str) -> _RunObservation | None:
        """Fingerprint one run behind load()'s location checks, or None to verify fully."""
        try:
            root = self._root()
            store = CityRunStore(root)
            if not _direct_directory_exists(root / "city-runs", label="city-runs location"):
                return None
            directory = store.run_directory(run_id)
            if not _direct_directory_exists(directory, label="city run candidate"):
                return None
            return _observe_run_directory(directory)
        except (OSError, StorageError, ValueError, _UnfingerprintableRun):
            # Any refusal, an unsafe workspace included, only disables the shortcut: the
            # full verification that follows reports it exactly as it always has.
            return None

    def content_fingerprint(self, run_id: str) -> str | None:
        """sha256 over the canonical observation (entries and file identities), or None.

        None is never a verdict: it only tells a caller to verify the run in full.
        """
        try:
            validate_run_id(run_id)
        except (StorageError, ValueError):
            return None
        observation = self._observe(run_id)
        if observation is None:
            return None
        canonical = json.dumps(
            [observation.fingerprint, observation.stamps], ensure_ascii=True, separators=(",", ":")
        )
        return sha256(canonical.encode("ascii")).hexdigest()

    def _verify_and_remember(
        self,
        run_id: str,
        before: _RunObservation | None,
    ) -> WorkbenchRunSummary:
        summary = _summary(self.load(run_id))
        # Vouch only for bytes and identities that did not move around the verification.
        if before is not None and self._observe(run_id) == before:
            self._remember(run_id, before.fingerprint, summary)
        return summary

    def _remember(
        self,
        run_id: str,
        fingerprint: _ArtifactFingerprint,
        summary: WorkbenchRunSummary,
    ) -> None:
        with self._verified_lock:
            self._verified_generation += 1
            self._verified_runs[run_id] = _VerifiedRun(
                fingerprint, summary, self._verified_generation
            )
            self._verified_runs.move_to_end(run_id)
            while len(self._verified_runs) > MAX_DISCOVERY_CANDIDATES:
                self._verified_runs.popitem(last=False)

    def _recall(
        self,
        run_id: str,
        observation: _RunObservation | None,
    ) -> WorkbenchRunSummary | None:
        with self._verified_lock:
            entry = self._verified_runs.get(run_id)
            if entry is None:
                return None
            if observation is None or observation.fingerprint != entry.fingerprint:
                del self._verified_runs[run_id]
                return None
            self._verified_runs.move_to_end(run_id)
            return entry.summary

    def _verified_runs_generation(self) -> int:
        with self._verified_lock:
            return self._verified_generation

    def _forget_unlisted(self, listed: frozenset[str], generation: int) -> None:
        """Evict runs a scan did not see, keeping entries seeded after that scan began."""
        with self._verified_lock:
            for run_id in [
                run_id
                for run_id, entry in self._verified_runs.items()
                if run_id not in listed and entry.generation <= generation
            ]:
                del self._verified_runs[run_id]

    def _listed_summary(self, run_id: str) -> WorkbenchRunSummary:
        observation = self._observe(run_id)
        cached = self._recall(run_id, observation)
        if cached is not None:
            return cached
        return self._verify_and_remember(run_id, observation)

    def list_runs(
        self,
        *,
        offset: int = 0,
        limit: int = DEFAULT_RUN_PAGE_LIMIT,
    ) -> WorkbenchRunPage:
        """Verify at most 256 direct candidates and page the stable verified subset.

        A candidate whose exact content fingerprint matches an earlier full verification
        in this process reuses that summary; any changed byte, file, or path, or anything
        the store would refuse, sends it through a new full verification instead.
        """
        offset, limit = _pagination(offset, limit)
        root = self._root()
        generation = self._verified_runs_generation()
        runs_directory = root / "city-runs"
        if not _direct_directory_exists(runs_directory, label="city-runs location"):
            self._forget_unlisted(frozenset(), generation)
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

        self._forget_unlisted(frozenset(candidate_names), generation)
        for name in sorted(candidate_names):
            try:
                validate_run_id(name)
                _require_direct_directory(
                    runs_directory / name,
                    label="city run candidate",
                )
                summaries.append(self._listed_summary(name))
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
    "WorkbenchCognitionOperation",
    "WorkbenchCognitionOperationV2",
    "WorkbenchRunPage",
    "WorkbenchRunRepository",
    "WorkbenchRunSummary",
]
