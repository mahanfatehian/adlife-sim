"""Exact, process-local authorization for explicit provider effect scopes.

Registry locks protect values only. Operation admission is always the caller's outer
boundary; no I/O, credential access, callbacks, or awaits occur under these locks.
"""

from __future__ import annotations

from hashlib import sha256
from threading import RLock
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from adlife.city.provider_profiles import ProviderProfileRevisionV1
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_FALLBACK_POLICY_SHA256,
    CITY_COGNITION_PROMPT_CONTRACT_SHA256,
    CITY_COGNITION_RESULT_SCHEMA_SHA256,
    CITY_COGNITION_SELECTION_POLICY_SHA256,
    CityCognitionEffectiveSettingsV1,
    CityCognitionProviderProfileSnapshotV1,
    CityCognitionRuntimeFallbackReason,
    Hash,
)
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_SELECTION_LIMITS,
    CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT,
    CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    CityCognitionCausalContractV2,
    CityCognitionEffectiveSettingsV2,
)
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

ProviderEffectScope = Literal["explanation-only", "bounded-signals-v1"]
CompatibilityStatus = Literal["compatible", "incompatible", "unavailable"]
CompatibilityReason = (
    CityCognitionRuntimeFallbackReason
    | Literal["credential-unavailable", "endpoint-policy-refused"]
    | None
)


def _bounded_contract(
    causal_contract: CityCognitionCausalContractV2 | None,
) -> CityCognitionCausalContractV2:
    """The contract a bounded-signals check vouches for: the current one, or its thoughts twin.

    A run that writes inner thoughts sends a different request and answer, so it needs a
    check of that exact shape; every other bounded-signals run needs the current contract's.
    """
    contract = CityCognitionCausalContractV2.model_validate(
        (causal_contract or CURRENT_CITY_COGNITION_CAUSAL_CONTRACT).model_dump(mode="python")
    )
    if contract.fingerprint not in {
        CURRENT_CITY_COGNITION_CAUSAL_CONTRACT.fingerprint,
        CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT.fingerprint,
    }:
        raise ValueError("a bounded-signals check covers only the current contracts")
    return contract


def _scope_contract_hashes(
    effect_scope: ProviderEffectScope,
    causal_contract: CityCognitionCausalContractV2 | None = None,
) -> dict[str, str | None]:
    if effect_scope == "explanation-only":
        return {
            "prompt_contract_sha256": CITY_COGNITION_PROMPT_CONTRACT_SHA256,
            "result_schema_contract_sha256": CITY_COGNITION_RESULT_SCHEMA_SHA256,
            "selection_policy_sha256": CITY_COGNITION_SELECTION_POLICY_SHA256,
            "fallback_policy_sha256": CITY_COGNITION_FALLBACK_POLICY_SHA256,
            "causal_contract_sha256": None,
            "mock_policy_sha256": None,
            "influence_policy_sha256": None,
        }
    if effect_scope != "bounded-signals-v1":
        raise ValueError("provider effect scope is invalid")
    contract = _bounded_contract(causal_contract)
    return {
        "causal_contract_sha256": contract.fingerprint,
        **{
            name: getattr(contract, name)
            for name in (
                "prompt_contract_sha256",
                "result_schema_contract_sha256",
                "selection_policy_sha256",
                "fallback_policy_sha256",
                "mock_policy_sha256",
                "influence_policy_sha256",
            )
        },
    }


class WorkbenchProviderCompatibilityKey(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True, revalidate_instances="always")

    effect_scope: ProviderEffectScope
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    revision_sha256: Hash
    profile_snapshot_sha256: Hash
    effective_settings_sha256: Hash
    prompt_contract_sha256: Hash
    result_schema_contract_sha256: Hash
    selection_policy_sha256: Hash
    fallback_policy_sha256: Hash
    causal_contract_sha256: Hash | None = None
    mock_policy_sha256: Hash | None = None
    influence_policy_sha256: Hash | None = None

    @model_validator(mode="after")
    def coherent_scope_contract(self) -> Self:
        contract = None
        if self.effect_scope == "bounded-signals-v1":
            thoughts = CITY_COGNITION_THOUGHTS_CAUSAL_CONTRACT
            if self.causal_contract_sha256 == thoughts.fingerprint:
                contract = thoughts
        if any(
            getattr(self, name) != expected
            for name, expected in _scope_contract_hashes(self.effect_scope, contract).items()
        ):
            raise ValueError("compatibility key disagrees with its scoped contract")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


def build_workbench_provider_compatibility_key(
    profile: ProviderProfileRevisionV1,
    *,
    effect_scope: ProviderEffectScope,
    causal_contract: CityCognitionCausalContractV2 | None = None,
) -> WorkbenchProviderCompatibilityKey:
    """Derive identity from public immutable facts, without resolving a credential.

    ``causal_contract`` is the bounded-signals contract the check vouches for; it defaults
    to the current one, and a run that writes inner thoughts passes the thoughts contract.
    """
    hashes = _scope_contract_hashes(effect_scope, causal_contract)
    profile = ProviderProfileRevisionV1.model_validate(profile.model_dump(mode="python"))
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        profile.model_dump(mode="python")
    )
    settings: CityCognitionEffectiveSettingsV1 | CityCognitionEffectiveSettingsV2
    if effect_scope == "explanation-only":
        settings = CityCognitionEffectiveSettingsV1.model_validate(
            {
                name: getattr(snapshot, name)
                for name in CityCognitionEffectiveSettingsV1.model_fields
            }
        )
    else:
        facts = {
            name: getattr(snapshot, name)
            for name in CityCognitionEffectiveSettingsV2.model_fields
            if name != "schema_version"
        }
        limits = CITY_COGNITION_SELECTION_LIMITS[
            _bounded_contract(causal_contract).selection_policy_version
        ]
        facts.update(
            schema_version=2,
            max_output_tokens=limits.effective_output_tokens(snapshot.max_output_tokens),
        )
        settings = CityCognitionEffectiveSettingsV2.model_validate(facts)
    return WorkbenchProviderCompatibilityKey.model_validate(
        {
            "effect_scope": effect_scope,
            "profile_id": profile.profile_id,
            "revision_sha256": profile.revision_sha256,
            "profile_snapshot_sha256": snapshot.fingerprint,
            "effective_settings_sha256": settings.fingerprint,
            **hashes,
        }
    )


class WorkbenchProviderCompatibilityOutcome(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True, revalidate_instances="always")
    status: CompatibilityStatus
    reason_code: CompatibilityReason = None

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        unavailable = {"credential-unavailable", "endpoint-policy-refused"}
        if self.status == "compatible":
            valid = self.reason_code is None
        elif self.status == "unavailable":
            valid = self.reason_code in unavailable
        else:
            valid = self.reason_code is not None and self.reason_code not in unavailable
        if not valid:
            raise ValueError("provider check status and reason are inconsistent")
        return self


class WorkbenchProviderCompatibilityRecord(WorkbenchProviderCompatibilityOutcome):
    key: WorkbenchProviderCompatibilityKey


class WorkbenchProviderCompatibilityRequired(RuntimeError):
    def __init__(self) -> None:
        super().__init__("provider compatibility verification is required")


class WorkbenchProviderCompatibilityRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, WorkbenchProviderCompatibilityRecord] = {}

    def record(self, record: WorkbenchProviderCompatibilityRecord) -> None:
        checked = WorkbenchProviderCompatibilityRecord.model_validate(
            record.model_dump(mode="python")
        )
        with self._lock:
            self._records[checked.key.fingerprint] = checked

    def lookup(
        self, key: WorkbenchProviderCompatibilityKey
    ) -> WorkbenchProviderCompatibilityRecord | None:
        checked = WorkbenchProviderCompatibilityKey.model_validate(key.model_dump(mode="python"))
        with self._lock:
            record = self._records.get(checked.fingerprint)
            return (
                None
                if record is None
                else WorkbenchProviderCompatibilityRecord.model_validate(
                    record.model_dump(mode="python")
                )
            )

    def require_success(self, key: WorkbenchProviderCompatibilityKey) -> None:
        record = self.lookup(key)
        if record is None or record.status != "compatible" or record.reason_code is not None:
            raise WorkbenchProviderCompatibilityRequired()

    def invalidate(self, profile_id: str, revision_sha256: str) -> None:
        with self._lock:
            self._records = {
                fingerprint: record
                for fingerprint, record in self._records.items()
                if (record.key.profile_id, record.key.revision_sha256)
                != (profile_id, revision_sha256)
            }
