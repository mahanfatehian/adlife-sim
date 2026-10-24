"""Exact, path-free saved-run projections of verified frozen cognition evidence.

Every builder selects its contract from the verified manifest class and its exact
integer schema token before reading any version-specific field, then revalidates the
complete source models and their run, input, request, and policy bindings. V9
explanation evidence keeps its historical schema-1 documents; V10 bounded signals use
explicit schema-2 documents. Builders are pure projections: they never read provider,
cache, keyring, environment, or filesystem state and never repair persisted evidence.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import ConfigDict, Field, field_validator, model_validator

from adlife.city.run_store import StoredCityRun
from adlife.city.workbench_binding import validate_workbench_execution_binding
from adlife.city.workbench_input import WorkbenchRunInputV4
from adlife.city.workbench_transport import (
    WorkbenchRunInputViewDocument,
    WorkbenchRunInputViewV4,
    WorkbenchRunSettingsView,
    build_workbench_run_input_view,
)
from adlife.core.domain.city_cognition import (
    MAX_CITY_COGNITION_AGGREGATE_OUTPUT_TOKENS,
    MAX_CITY_COGNITION_REQUESTS,
    CityCognitionEvaluationV1,
    CityCognitionInputV1,
    CityCognitionMode,
    CityCognitionSummaryV1,
    CityCognitionTerminalRecordV1,
    Hash,
)
from adlife.core.domain.city_cognition_v2 import (
    _CITY_COGNITION_TERMINAL_POLICY_HASHES_BY_VERSION,
    CITY_COGNITION_MAX_SELECTED_REQUESTS,
    CITY_COGNITION_SELECTION_LIMITS,
    CityCognitionEvaluationV2,
    CityCognitionFundedResponseLimit,
    CityCognitionInputV2,
    CityCognitionModeV2,
    CityCognitionSummaryV2,
    CityCognitionTerminalRecordV2,
    city_cognition_contract_selection_version,
)
from adlife.core.domain.city_run import (
    CityRunManifestV9,
    CityRunManifestV10,
    build_city_cognition_manifest_receipt,
    build_city_cognition_manifest_receipt_v2,
)
from adlife.core.domain.person import DomainModel, contains_provider_secret_text
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation._validation import revalidate_model

_RunId: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
_ProfileId: TypeAlias = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
_ModelId: TypeAlias = Annotated[str, Field(min_length=1, max_length=120)]
_OFFLINE_MODES = frozenset({"rules", "mock"})


class _CognitionView(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    @field_validator(
        "run_schema_version", "funded_response_limit", mode="before", check_fields=False
    )
    @classmethod
    def exact_integer_tokens(cls, value: object) -> object:
        # Literal equality admits True and 10.0; versions and limits need int tokens.
        if type(value) is not int:
            raise ValueError("cognition view versions and limits must be exact integers")
        return value

    @field_validator("provider_profile_id", "model_id", check_fields=False)
    @classmethod
    def secret_free_provider_text(cls, value: str | None) -> str | None:
        if value is not None and contains_provider_secret_text(value):
            raise ValueError("cognition view provider text contains credential-shaped content")
        return value

    def _check_provider_fields(self, mode: str, *values: object) -> None:
        if mode in _OFFLINE_MODES:
            if any(value is not None for value in values):
                raise ValueError("offline cognition views cannot carry provider provenance")
        elif any(value is None for value in values):
            raise ValueError("provider cognition views require complete provider provenance")


class WorkbenchCognitionSummaryViewV1(_CognitionView):
    """The historical V9 explanation-only summary document, key for key."""

    schema_version: Literal[1]
    run_id: _RunId
    run_schema_version: Literal[9]
    cognition_input_sha256: Hash
    mode: CityCognitionMode
    explainer_id: str = Field(pattern=r"^city-[a-z0-9-]+-explainer-v1$")
    provider_profile_id: _ProfileId | None
    provider_revision_sha256: Hash | None
    model_id: _ModelId | None
    aggregate_output_token_ceiling: int = Field(
        ge=0,
        le=MAX_CITY_COGNITION_AGGREGATE_OUTPUT_TOKENS,
    )
    summary: CityCognitionSummaryV1

    @model_validator(mode="after")
    def coherent_provenance(self) -> Self:
        self._check_provider_fields(
            self.mode,
            self.provider_profile_id,
            self.provider_revision_sha256,
            self.model_id,
        )
        return self


class WorkbenchCognitionSummaryViewV2(_CognitionView):
    """Bounded-signal coverage, outcomes, and the complete frozen policy provenance."""

    schema_version: Literal[2]
    run_id: _RunId
    run_schema_version: Literal[10]
    effect_scope: Literal["bounded-signals-v1"]
    cognition_input_sha256: Hash
    mode: CityCognitionModeV2
    provider_profile_id: _ProfileId | None
    provider_revision_sha256: Hash | None
    model_id: _ModelId | None
    funded_response_limit: CityCognitionFundedResponseLimit
    causal_contract_sha256: Hash
    prompt_contract_id: Literal["city-cognition-signal-prompt-v2"]
    prompt_contract_sha256: Hash
    result_schema_id: Literal["city-cognition-signal-result-v2"]
    result_schema_contract_sha256: Hash
    selection_policy_sha256: Hash
    fallback_policy_sha256: Hash
    mock_policy_sha256: Hash
    influence_policy_id: Literal["city-cognition-influence-v1"]
    influence_policy_sha256: Hash
    summary: CityCognitionSummaryV2

    @model_validator(mode="after")
    def bound_summary(self) -> Self:
        summary = self.summary
        version = city_cognition_contract_selection_version(self.causal_contract_sha256)
        if version is None or any(
            getattr(self, field) != expected
            for field, expected in _CITY_COGNITION_TERMINAL_POLICY_HASHES_BY_VERSION[version]
        ):
            raise ValueError("cognition summary view does not bind the frozen V2 contract")
        limits = CITY_COGNITION_SELECTION_LIMITS[version]
        if (
            summary.effect_scope != self.effect_scope
            or summary.mode != self.mode
            or summary.causal_contract_sha256 != self.causal_contract_sha256
            or summary.mock_policy_sha256 != self.mock_policy_sha256
            or summary.influence_policy_sha256 != self.influence_policy_sha256
        ):
            raise ValueError("cognition summary view does not bind its terminal summary")
        self._check_provider_fields(
            self.mode,
            self.provider_profile_id,
            self.provider_revision_sha256,
            self.model_id,
        )
        coverage = summary.coverage
        if (
            self.funded_response_limit not in limits.funded_response_limits
            or (
                self.mode in _OFFLINE_MODES
                and self.funded_response_limit != limits.offline_funded_response_limit
            )
        ) or (
            coverage.funded_request_count
            != min(coverage.selected_request_count, self.funded_response_limit)
        ):
            raise ValueError("cognition summary view funding does not match its coverage")
        return self


class WorkbenchCognitionRecordsViewV1(_CognitionView):
    """The historical V9 terminal narrative records in canonical selection order."""

    schema_version: Literal[1]
    run_id: _RunId
    run_schema_version: Literal[9]
    cognition_input_sha256: Hash
    records: tuple[CityCognitionTerminalRecordV1, ...] = Field(
        max_length=MAX_CITY_COGNITION_REQUESTS
    )


class WorkbenchCognitionRecordsViewV2(_CognitionView):
    """Bounded-signal terminal records, one per selected request, in selection order."""

    schema_version: Literal[2]
    run_id: _RunId
    run_schema_version: Literal[10]
    cognition_input_sha256: Hash
    records: tuple[CityCognitionTerminalRecordV2, ...] = Field(
        max_length=CITY_COGNITION_MAX_SELECTED_REQUESTS
    )

    @model_validator(mode="after")
    def canonical_records(self) -> Self:
        records = self.records
        if tuple(record.selection_index for record in records) != tuple(range(len(records))):
            raise ValueError("cognition records view must preserve canonical selection order")
        for field in ("request_id", "request_record_sha256", "baseline_response_id", "record_hash"):
            if len({getattr(record, field) for record in records}) != len(records):
                raise ValueError("cognition records view bindings must be unique")
        if (
            len({record.run_input_sha256 for record in records}) > 1
            or len(
                {
                    (record.response_input_sha256, record.scenario_sha256, record.city_sha256)
                    for record in records
                }
            )
            > 1
        ):
            raise ValueError("cognition records view mixes run or scientific provenance")
        return self


WorkbenchCognitionSummaryView: TypeAlias = (
    WorkbenchCognitionSummaryViewV1 | WorkbenchCognitionSummaryViewV2
)
WorkbenchCognitionRecordsView: TypeAlias = (
    WorkbenchCognitionRecordsViewV1 | WorkbenchCognitionRecordsViewV2
)


def _cognition_version(stored: StoredCityRun) -> Literal[9, 10]:
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    manifest = stored.manifest
    version = manifest.schema_version
    if type(version) is int and version == 9 and type(manifest) is CityRunManifestV9:
        return 9
    if type(version) is int and version == 10 and type(manifest) is CityRunManifestV10:
        return 10
    raise ValueError("cognition views require a verified schema-v9 or schema-v10 run")


def _v1_evidence(
    stored: StoredCityRun,
) -> tuple[CityRunManifestV9, CityCognitionInputV1, CityCognitionEvaluationV1]:
    manifest = stored.manifest
    if (
        type(manifest) is not CityRunManifestV9
        or type(stored.cognition_input) is not CityCognitionInputV1
        or type(stored.cognition_evaluation) is not CityCognitionEvaluationV1
    ):
        raise ValueError("schema-v9 cognition views require V1 cognition evidence")
    cognition_input = revalidate_model(
        stored.cognition_input, CityCognitionInputV1, label="V9 cognition input"
    )
    evaluation = revalidate_model(
        stored.cognition_evaluation, CityCognitionEvaluationV1, label="V9 cognition evaluation"
    )
    if (
        evaluation.cognition_input_sha256 != cognition_input.fingerprint
        or evaluation.run_input_sha256 != cognition_input.run_input_sha256
        or evaluation.mode != cognition_input.mode
        or cognition_input.run_input_sha256 != manifest.workbench_input_sha256
        or build_city_cognition_manifest_receipt(cognition_input, evaluation) != manifest.cognition
    ):
        raise ValueError("V9 cognition evidence does not match its verified manifest")
    return manifest, cognition_input, evaluation


def _v2_evidence(
    stored: StoredCityRun,
) -> tuple[CityRunManifestV10, CityCognitionInputV2, CityCognitionEvaluationV2]:
    manifest = stored.manifest
    if (
        type(manifest) is not CityRunManifestV10
        or type(stored.cognition_input) is not CityCognitionInputV2
        or type(stored.cognition_evaluation) is not CityCognitionEvaluationV2
    ):
        raise ValueError("schema-v10 cognition views require V2 cognition evidence")
    cognition_input = revalidate_model(
        stored.cognition_input, CityCognitionInputV2, label="V10 cognition input"
    )
    evaluation = revalidate_model(
        stored.cognition_evaluation, CityCognitionEvaluationV2, label="V10 cognition evaluation"
    )
    if (
        evaluation.cognition_input_sha256 != cognition_input.fingerprint
        or evaluation.run_input_sha256 != cognition_input.run_input_sha256
        or evaluation.effect_scope != cognition_input.effect_scope
        or evaluation.mode != cognition_input.mode
        or cognition_input.run_input_sha256 != manifest.workbench_input_sha256
        or manifest.influence_policy_sha256
        != cognition_input.causal_contract.influence_policy_sha256
        or build_city_cognition_manifest_receipt_v2(cognition_input, evaluation)
        != manifest.cognition
    ):
        raise ValueError("V10 cognition evidence does not match its verified manifest")
    return manifest, cognition_input, evaluation


def build_workbench_cognition_summary_view(stored: StoredCityRun) -> WorkbenchCognitionSummaryView:
    """Project one verified V9 or V10 cognition summary by exact manifest dispatch."""
    if _cognition_version(stored) == 9:
        manifest, legacy_input, legacy = _v1_evidence(stored)
        legacy_profile = legacy_input.provider_profile
        return WorkbenchCognitionSummaryViewV1(
            schema_version=1,
            run_id=manifest.run_id,
            run_schema_version=9,
            cognition_input_sha256=legacy_input.fingerprint,
            mode=legacy_input.mode,
            explainer_id=legacy_input.explainer_id,
            provider_profile_id=None if legacy_profile is None else legacy_profile.profile_id,
            provider_revision_sha256=(
                None if legacy_profile is None else legacy_profile.revision_sha256
            ),
            model_id=None if legacy_profile is None else legacy_profile.model_id,
            aggregate_output_token_ceiling=legacy_input.aggregate_output_token_ceiling,
            summary=legacy.summary,
        )
    bounded_manifest, cognition_input, evaluation = _v2_evidence(stored)
    profile = cognition_input.provider_profile
    contract = cognition_input.causal_contract
    return WorkbenchCognitionSummaryViewV2(
        schema_version=2,
        run_id=bounded_manifest.run_id,
        run_schema_version=10,
        effect_scope=cognition_input.effect_scope,
        cognition_input_sha256=cognition_input.fingerprint,
        mode=cognition_input.mode,
        provider_profile_id=None if profile is None else profile.profile_id,
        provider_revision_sha256=None if profile is None else profile.revision_sha256,
        model_id=None if profile is None else profile.model_id,
        funded_response_limit=cognition_input.funded_response_limit,
        causal_contract_sha256=cognition_input.causal_contract_sha256,
        prompt_contract_id=contract.prompt_contract_id,
        prompt_contract_sha256=contract.prompt_contract_sha256,
        result_schema_id=contract.result_schema_id,
        result_schema_contract_sha256=contract.result_schema_contract_sha256,
        selection_policy_sha256=contract.selection_policy_sha256,
        fallback_policy_sha256=contract.fallback_policy_sha256,
        mock_policy_sha256=contract.mock_policy_sha256,
        influence_policy_id=contract.influence_policy.policy_id,
        influence_policy_sha256=contract.influence_policy_sha256,
        summary=evaluation.summary,
    )


def build_workbench_cognition_records_view(stored: StoredCityRun) -> WorkbenchCognitionRecordsView:
    """Project one verified V9 or V10 terminal record stream by exact manifest dispatch."""
    if _cognition_version(stored) == 9:
        manifest, legacy_input, legacy = _v1_evidence(stored)
        return WorkbenchCognitionRecordsViewV1(
            schema_version=1,
            run_id=manifest.run_id,
            run_schema_version=9,
            cognition_input_sha256=legacy_input.fingerprint,
            records=legacy.records,
        )
    bounded_manifest, cognition_input, evaluation = _v2_evidence(stored)
    return WorkbenchCognitionRecordsViewV2(
        schema_version=2,
        run_id=bounded_manifest.run_id,
        run_schema_version=10,
        cognition_input_sha256=cognition_input.fingerprint,
        records=evaluation.records,
    )


def build_workbench_run_input_view_v4(stored: StoredCityRun) -> WorkbenchRunInputViewV4:
    """Project one verified V10 schema-4 input after rechecking every frozen binding."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    manifest = stored.manifest
    if (
        type(manifest) is not CityRunManifestV10
        or type(manifest.schema_version) is not int
        or type(manifest.workbench_input_schema_version) is not int
        or (manifest.schema_version, manifest.workbench_input_schema_version) != (10, 4)
    ):
        raise ValueError("workbench input view V4 requires a schema-v10 run")
    scenario = stored.spatial_scenario
    response_input = stored.response_input
    if stored.workbench_input is None or scenario is None or response_input is None:
        raise ValueError("workbench input view requires its frozen schema-4 evidence")
    try:
        bound_input = validate_workbench_execution_binding(
            stored.workbench_input,
            pack=stored.pack,
            run_id=manifest.run_id,
            seed=manifest.seed,
            agent_count=manifest.agent_count,
            days=manifest.days,
            places=stored.mobility.places,
            spatial_scenario=scenario,
            spatial_response=response_input,
        )
    except ValueError:
        raise ValueError("workbench input view binding is invalid") from None
    if not isinstance(bound_input, WorkbenchRunInputV4):
        raise ValueError("workbench input view requires workbench input schema 4")
    _, cognition_input, _ = _v2_evidence(stored)
    cognition = bound_input.draft.cognition
    profile = cognition_input.provider_profile
    if (
        cognition.effect_scope != cognition_input.effect_scope
        or cognition.mode != cognition_input.mode
        or cognition.funded_response_limit != cognition_input.funded_response_limit
        or cognition.aggregate_output_token_ceiling
        != cognition_input.aggregate_output_token_ceiling
        or cognition.causal_contract_sha256 != cognition_input.causal_contract_sha256
        or canonical_json(cognition.causal_contract)
        != canonical_json(cognition_input.causal_contract)
        or cognition.provider_profile_id != (None if profile is None else profile.profile_id)
        or cognition.provider_revision_sha256
        != (None if profile is None else profile.revision_sha256)
        or cognition_input.run_input_sha256 != bound_input.fingerprint
    ):
        raise ValueError("workbench input view cognition binding is invalid")
    creative = bound_input.creative_template
    if (
        manifest.workbench_input_sha256 != bound_input.fingerprint
        or manifest.city_sha256 != stored.pack.fingerprint
        or manifest.scenario_sha256 != scenario.fingerprint
        or manifest.response_input_sha256 != response_input.fingerprint
        or len(scenario.campaigns) != 1
        or scenario.campaigns[0].creative_sha256 != creative.fingerprint
    ):
        raise ValueError("workbench input view hashes are inconsistent")
    draft = bound_input.draft
    settings = draft.settings
    return WorkbenchRunInputViewV4.model_validate(
        {
            "run_id": manifest.run_id,
            "manifest_sha256": sha256(
                (canonical_json(manifest) + "\n").encode("utf-8")
            ).hexdigest(),
            "workbench_input_sha256": bound_input.fingerprint,
            "city_sha256": stored.pack.fingerprint,
            "scenario_sha256": scenario.fingerprint,
            "creative_sha256": creative.fingerprint,
            "scenario": draft.scenario,
            "population": draft.population,
            "settings": WorkbenchRunSettingsView(
                run_id=settings.run_id,
                agent_count=settings.agent_count,
                days=settings.days,
                seed=str(settings.seed),
                response_mode=settings.response_mode,
            ),
            "cognition": cognition,
            "creative_template": creative,
        }
    )


def build_saved_workbench_run_input_view(stored: StoredCityRun) -> WorkbenchRunInputViewDocument:
    """Dispatch the frozen workbench-input projection by exact manifest class."""
    if not isinstance(stored, StoredCityRun):
        raise TypeError("stored must be a StoredCityRun")
    if type(stored.manifest) is CityRunManifestV10:
        return build_workbench_run_input_view_v4(stored)
    return build_workbench_run_input_view(stored)


__all__ = [
    "WorkbenchCognitionRecordsView",
    "WorkbenchCognitionRecordsViewV1",
    "WorkbenchCognitionRecordsViewV2",
    "WorkbenchCognitionSummaryView",
    "WorkbenchCognitionSummaryViewV1",
    "WorkbenchCognitionSummaryViewV2",
    "build_saved_workbench_run_input_view",
    "build_workbench_cognition_records_view",
    "build_workbench_cognition_summary_view",
    "build_workbench_run_input_view_v4",
]
