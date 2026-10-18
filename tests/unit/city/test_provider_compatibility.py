"""Scoped compatibility authorization is process-local and exact."""

from __future__ import annotations

import importlib
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from adlife.city.provider_profiles import ProviderProfileRegistry
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import CityCognitionProviderProfileSnapshotV1
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2,
    CityCognitionEffectiveSettingsV2,
)
from tests.unit.city.test_provider_profiles import _draft


def compatibility_module():
    return importlib.import_module("adlife.city.provider_compatibility")


def profile_revision(tmp_path: Path, **updates):
    return ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "profiles")).create(
        _draft(**updates)
    )


def test_v1_success_cannot_authorize_bounded_signal_scope(tmp_path: Path):
    module = compatibility_module()
    profile = profile_revision(tmp_path)
    registry = module.WorkbenchProviderCompatibilityRegistry()
    v1 = module.build_workbench_provider_compatibility_key(profile, effect_scope="explanation-only")
    v2 = module.build_workbench_provider_compatibility_key(
        profile, effect_scope="bounded-signals-v1"
    )
    registry.record(module.WorkbenchProviderCompatibilityRecord(key=v1, status="compatible"))
    registry.require_success(v1)
    assert v1.fingerprint != v2.fingerprint
    with pytest.raises(module.WorkbenchProviderCompatibilityRequired) as error:
        registry.require_success(v2)
    assert str(error.value) == "provider compatibility verification is required"
    assert registry.lookup(v2) is None


def test_v2_key_binds_original_profile_and_capped_exact_settings(tmp_path: Path):
    module = compatibility_module()
    profile = profile_revision(tmp_path)
    key = module.build_workbench_provider_compatibility_key(
        profile, effect_scope="bounded-signals-v1"
    )
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        profile.model_dump(mode="python")
    )
    settings = CityCognitionEffectiveSettingsV2.model_validate(
        {
            name: 2
            if name == "schema_version"
            else 128
            if name == "max_output_tokens"
            else getattr(profile, name)
            for name in CityCognitionEffectiveSettingsV2.model_fields
        }
    )
    assert key.profile_id == profile.profile_id
    assert key.revision_sha256 == profile.revision_sha256
    assert key.profile_snapshot_sha256 == snapshot.fingerprint
    assert key.effective_settings_sha256 == settings.fingerprint
    contract = CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V2
    assert key.causal_contract_sha256 == contract.fingerprint
    for name in (
        "prompt_contract_sha256",
        "result_schema_contract_sha256",
        "selection_policy_sha256",
        "fallback_policy_sha256",
        "mock_policy_sha256",
        "influence_policy_sha256",
    ):
        assert getattr(key, name) == getattr(contract, name)
    assert "credential" not in key.model_dump_json()
    assert sha256(b"sk-test-compatibility-secret").hexdigest() not in key.model_dump_json()


@pytest.mark.parametrize(
    "field",
    [
        "causal_contract_sha256",
        "prompt_contract_sha256",
        "result_schema_contract_sha256",
        "selection_policy_sha256",
        "fallback_policy_sha256",
        "mock_policy_sha256",
        "influence_policy_sha256",
    ],
)
def test_v2_key_refuses_incoherent_contract_member(tmp_path: Path, field):
    module = compatibility_module()
    key = module.build_workbench_provider_compatibility_key(
        profile_revision(tmp_path), effect_scope="bounded-signals-v1"
    )
    facts = key.model_dump(mode="python")
    facts[field] = "f" * 64
    with pytest.raises(ValidationError):
        module.WorkbenchProviderCompatibilityKey.model_validate(facts)


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_id", "another-model"),
        ("temperature", 0.3),
        ("top_p", 0.8),
        ("seed", 43),
        ("max_output_tokens", 64),
        ("attempt_timeout_seconds", 20),
        ("transient_retries", 1),
        ("repair_attempts", 0),
        ("display_label", "Changed label"),
        ("profile_id", "another-profile"),
    ],
)
def test_changed_profile_cannot_reuse_success(tmp_path: Path, field, value):
    module = compatibility_module()
    profiles = ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "profiles"))
    first = profiles.create(_draft())
    changed = profiles.create(_draft(**{field: value}))
    key = module.build_workbench_provider_compatibility_key(
        first, effect_scope="bounded-signals-v1"
    )
    other = module.build_workbench_provider_compatibility_key(
        changed, effect_scope="bounded-signals-v1"
    )
    assert key.fingerprint != other.fingerprint
    registry = module.WorkbenchProviderCompatibilityRegistry()
    registry.record(module.WorkbenchProviderCompatibilityRecord(key=key, status="compatible"))
    with pytest.raises(module.WorkbenchProviderCompatibilityRequired):
        registry.require_success(other)


def test_registry_revalidates_untrusted_copies_and_exact_outcomes(tmp_path: Path):
    module = compatibility_module()
    key = module.build_workbench_provider_compatibility_key(
        profile_revision(tmp_path), effect_scope="bounded-signals-v1"
    )
    registry = module.WorkbenchProviderCompatibilityRegistry()
    compatible = module.WorkbenchProviderCompatibilityRecord(key=key, status="compatible")
    registry.record(compatible)
    object.__setattr__(compatible, "status", "incompatible")
    assert registry.lookup(key).status == "compatible"
    returned = registry.lookup(key)
    object.__setattr__(returned, "status", "unavailable")
    registry.require_success(key)
    for status, reason in (
        ("incompatible", "invalid-result"),
        ("unavailable", "credential-unavailable"),
    ):
        registry.record(
            module.WorkbenchProviderCompatibilityRecord(key=key, status=status, reason_code=reason)
        )
        with pytest.raises(module.WorkbenchProviderCompatibilityRequired):
            registry.require_success(key)
    with pytest.raises(ValidationError):
        registry.record(compatible)
    assert module.WorkbenchProviderCompatibilityRegistry().lookup(key) is None


def test_invalidation_clears_both_scopes_only_for_exact_revision(tmp_path: Path):
    module = compatibility_module()
    profiles = ProviderProfileRegistry(prepare_workbench_workspace(tmp_path / "profiles"))
    first = profiles.create(_draft())
    other = profiles.create(_draft(model_id="another-model"))
    registry = module.WorkbenchProviderCompatibilityRegistry()
    keys = [
        module.build_workbench_provider_compatibility_key(p, effect_scope=scope)
        for p in (first, other)
        for scope in ("explanation-only", "bounded-signals-v1")
    ]
    for key in keys:
        registry.record(module.WorkbenchProviderCompatibilityRecord(key=key, status="compatible"))
    registry.invalidate(first.profile_id, first.revision_sha256)
    assert [registry.lookup(key) is None for key in keys] == [True, True, False, False]


@pytest.mark.parametrize("scope", ["bad", "", None, True, 2, [], {}])
def test_key_builder_refuses_non_exact_scope(tmp_path: Path, scope):
    module = compatibility_module()
    with pytest.raises(ValueError):
        module.build_workbench_provider_compatibility_key(
            profile_revision(tmp_path), effect_scope=scope
        )


def test_key_builder_revalidates_forged_profile(tmp_path: Path):
    module = compatibility_module()
    original = profile_revision(tmp_path)
    forged = type(original).model_construct(
        **{**original.model_dump(mode="python"), "model_id": "forged-model"}
    )
    with pytest.raises(ValidationError):
        module.build_workbench_provider_compatibility_key(forged, effect_scope="bounded-signals-v1")
