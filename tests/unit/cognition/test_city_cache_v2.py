"""V2 cache records carry reusable causal evidence, never full run provenance."""

from __future__ import annotations

import importlib
import json
import os
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import (
    CityCognitionProviderProfileSnapshotV1,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.domain.city_cognition_v2 import CityCognitionRunContextV2
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition_v2 import (
    build_city_cognition_input_v2,
    build_city_cognition_requests_v2,
    build_city_rules_result_v2,
)
from tests.unit.city.test_city_cognition_v2 import _dense_evidence
from tests.unit.city.test_city_cognition_v2_contract import profile_data


def _module():
    return importlib.import_module("adlife.adapters.cognition.city_cache_v2")


def _storage():
    return importlib.import_module("adlife.adapters.cognition.city_cache_storage")


@lru_cache
def _evidence(agent_count=1):
    return _dense_evidence(agent_count=agent_count)


def _case(*, mode="local", agent_count=1, limit=20, run_hash=None, profile_updates=None):
    context, mobility, baseline = _evidence(agent_count)
    if run_hash is not None:
        context = CityCognitionRunContextV2.model_validate(
            context.model_dump(mode="python") | {"run_input_sha256": run_hash}
        )
    requests = build_city_cognition_requests_v2(context, mobility, baseline)
    profile = None
    if mode in {"local", "remote"}:
        facts = profile_data(mode)
        facts.pop("revision_sha256")
        facts.update(profile_updates or {})
        profile = CityCognitionProviderProfileSnapshotV1.model_validate(
            facts | {"revision_sha256": city_cognition_provider_profile_revision_sha256(facts)}
        )
    frozen = build_city_cognition_input_v2(
        context,
        requests,
        mode=mode,
        funded_response_limit=limit if profile is not None else 60,
        provider_profile=profile,
    )
    return context, baseline, frozen, requests


def _material(request, frozen):
    prompts = importlib.import_module("adlife.adapters.cognition.city_prompts_v2")
    profile = frozen.provider_profile
    settings = frozen.effective_settings
    assert profile is not None and settings is not None
    contract = frozen.causal_contract
    return {
        "cache_key_version": "city-cognition-cache-v2",
        "request_id": request.request_id,
        "provider_projection_sha256": prompts.city_cognition_provider_projection_sha256_v2(request),
        "causal_contract_sha256": frozen.causal_contract_sha256,
        "provider_revision_sha256": profile.revision_sha256,
        "provider_profile_snapshot_sha256": profile.fingerprint,
        "provider_effective_settings_sha256": settings.fingerprint,
        **{
            field: getattr(contract, field)
            for field in (
                "prompt_contract_sha256",
                "result_schema_contract_sha256",
                "selection_policy_sha256",
                "fallback_policy_sha256",
                "mock_policy_sha256",
                "influence_policy_sha256",
            )
        },
    }


def _record(request, frozen, *, result=None, prompt_tokens=17, completion_tokens=5):
    module = _module()
    material = _material(request, frozen)
    key = sha256(canonical_json(material).encode("utf-8")).hexdigest()
    return module.CityCognitionCacheRecordV2(
        schema_version=2,
        key=key,
        **material,
        result=build_city_rules_result_v2(request) if result is None else result,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _cache_case(tmp_path):
    module = _module()
    _, _, frozen, requests = _case()
    record = _record(requests[0], frozen)
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    return module.CityCognitionCacheV2(workspace), record


def _path(cache, key):
    return cache.workspace.root / "city-cognition-cache-v2" / f"{key}.json"


def _bypass(value, **changes):
    return type(value).model_construct(
        **({name: getattr(value, name) for name in type(value).model_fields} | changes)
    )


def test_v2_cache_key_isolated_and_binds_minimized_projection(tmp_path: Path) -> None:
    module = _module()
    from tests.unit.cognition.test_city_cache import _case as v1_case

    _, _, frozen, requests = _case()
    request = requests[0]
    material = _material(request, frozen)
    key = module.CityCognitionCacheV2.make_key(request, frozen)
    assert key == sha256(canonical_json(material).encode("utf-8")).hexdigest()
    assert module.CITY_COGNITION_CACHE_KEY_VERSION_V2 == "city-cognition-cache-v2"
    assert module.MAX_CITY_COGNITION_CACHE_RECORD_V2_BYTES == 131_072
    _, v1_record = v1_case(tmp_path / "v1")
    assert key != v1_record.key
    assert material["provider_projection_sha256"] != request.request_record_sha256
    assert material["provider_projection_sha256"] != request.causal_identity.fingerprint
    assert set(material) == {
        "cache_key_version",
        "request_id",
        "provider_projection_sha256",
        "causal_contract_sha256",
        "provider_revision_sha256",
        "provider_profile_snapshot_sha256",
        "provider_effective_settings_sha256",
        "prompt_contract_sha256",
        "result_schema_contract_sha256",
        "selection_policy_sha256",
        "fallback_policy_sha256",
        "mock_policy_sha256",
        "influence_policy_sha256",
    }


def test_cache_reuses_causal_identity_across_full_provenance_and_funding_limits():
    module = _module()
    _, _, first, requests = _case(limit=10, run_hash="b" * 64)
    _, _, second, other = _case(limit=20, run_hash="e" * 64)
    assert first.fingerprint != second.fingerprint
    assert requests[0].run_input_sha256 != other[0].run_input_sha256
    assert requests[0].request_record_sha256 != other[0].request_record_sha256
    assert requests[0].request_id == other[0].request_id
    assert module.CityCognitionCacheV2.make_key(requests[0], first) == (
        module.CityCognitionCacheV2.make_key(other[0], second)
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"model_id": "another-model"},
        {"temperature": 0.5},
        {"max_output_tokens": 64},
        {"display_label": "Another label"},
    ],
)
def test_changed_frozen_provider_contract_changes_key(updates):
    module = _module()
    _, _, frozen, requests = _case()
    _, _, changed, other = _case(profile_updates=updates)
    assert module.CityCognitionCacheV2.make_key(requests[0], frozen) != (
        module.CityCognitionCacheV2.make_key(other[0], changed)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "offline",
        "unfunded",
        "wrong-run",
        "wrong-record",
        "missing-reservation",
        "wrong-settings",
        "wrong-policy",
    ],
)
def test_key_requires_current_funded_request_admission(mutation):
    module = _module()
    _, _, frozen, requests = _case(agent_count=4, limit=10)
    request = requests[0]
    if mutation == "offline":
        _, _, frozen, requests = _case(mode="rules")
        request = requests[0]
    elif mutation == "unfunded":
        request = requests[10]
    elif mutation == "wrong-run":
        _, _, frozen, _ = _case(run_hash="e" * 64)
    elif mutation == "wrong-record":
        frozen = _bypass(
            frozen,
            reservations=(
                _bypass(frozen.reservations[0], request_record_sha256="e" * 64),
                *frozen.reservations[1:],
            ),
        )
    elif mutation == "missing-reservation":
        frozen = _bypass(frozen, reservations=())
    elif mutation == "wrong-settings":
        frozen = _bypass(frozen, effective_settings=_bypass(frozen.effective_settings, top_p=0.5))
    else:
        frozen = _bypass(frozen, causal_contract_sha256="e" * 64)
    with pytest.raises((TypeError, ValueError)):
        module.CityCognitionCacheV2.make_key(request, frozen)


def test_canonical_round_trip_contains_only_reusable_evidence(tmp_path):
    cache, record = _cache_case(tmp_path)
    cache.put(record.key, record)
    document = _path(cache, record.key).read_bytes()
    assert document == (canonical_json(record) + "\n").encode()
    assert len(document) <= 131_072
    assert cache.get(record.key) == record
    assert set(json.loads(document)) == set(_material(_case()[3][0], _case()[2])) | {
        "schema_version",
        "key",
        "result",
        "prompt_tokens",
        "completion_tokens",
    }
    for forbidden in (
        "run_input_sha256",
        "request_record_sha256",
        "cognition_input",
        "causal_identity",
        "display_name",
        "raw_response",
        "base_url",
        "credential",
        "terminal",
        "prompt_text",
    ):
        assert forbidden.encode() not in document


def test_only_absence_is_a_miss(tmp_path):
    module = _module()
    cache = module.CityCognitionCacheV2(prepare_workbench_workspace(tmp_path / "workspace"))
    assert cache.get("f" * 64) is None
    assert not (cache.workspace.root / "city-cognition-cache-v2").exists()


@pytest.mark.parametrize(
    "kind",
    [
        "pretty",
        "no-newline",
        "duplicate",
        "nonfinite",
        "utf8",
        "root-array",
        "oversize",
        "deep",
        "numeric-version",
    ],
)
def test_present_invalid_bytes_are_corruption_and_are_preserved(tmp_path, kind):
    cache, record = _cache_case(tmp_path)
    canonical = (canonical_json(record) + "\n").encode()
    if kind == "pretty":
        document = json.dumps(record.model_dump(mode="json"), indent=2).encode()
    elif kind == "no-newline":
        document = canonical[:-1]
    elif kind == "duplicate":
        document = b'{"schema_version":2,' + canonical[1:]
    elif kind == "nonfinite":
        document = canonical.replace(b'"prompt_tokens":17', b'"prompt_tokens":NaN')
    elif kind == "utf8":
        document = b"\xff"
    elif kind == "root-array":
        document = b"[]\n"
    elif kind == "oversize":
        document = b" " * 131_073
    elif kind == "deep":
        document = b"[" * 2000 + b"]" * 2000
    else:
        document = canonical.replace(b'"schema_version":2', b'"schema_version":2.0', 1)
    path = _path(cache, record.key)
    path.parent.mkdir()
    path.write_bytes(document)
    with pytest.raises(_storage().CityCognitionCacheCorruption) as caught:
        cache.get(record.key)
    assert caught.value.__cause__ is None
    assert path.read_bytes() == document


@pytest.mark.parametrize(
    "field",
    [
        "provider_projection_sha256",
        "causal_contract_sha256",
        "provider_revision_sha256",
        "provider_profile_snapshot_sha256",
        "provider_effective_settings_sha256",
        "prompt_contract_sha256",
        "result_schema_contract_sha256",
        "selection_policy_sha256",
        "fallback_policy_sha256",
        "mock_policy_sha256",
        "influence_policy_sha256",
    ],
)
def test_changed_digest_under_existing_key_is_corruption(tmp_path, field):
    cache, record = _cache_case(tmp_path)
    data = record.model_dump(mode="json") | {field: "e" * 64}
    path = _path(cache, record.key)
    path.parent.mkdir()
    path.write_bytes((canonical_json(data) + "\n").encode())
    with pytest.raises(_storage().CityCognitionCacheCorruption):
        cache.get(record.key)
    with pytest.raises((TypeError, ValueError)):
        cache.put(record.key, _bypass(record, **{field: "e" * 64}))


@pytest.mark.parametrize(
    "kind",
    [
        "wrong-id",
        "forged-result",
        "v1-result",
        "half-usage",
        "bool-usage",
        "overflow",
        "bool-version",
        "float-version",
        "extra-secret",
    ],
)
def test_record_revalidates_result_usage_schema_and_extra_fields(tmp_path, kind):
    cache, record = _cache_case(tmp_path)
    fields = {name: getattr(record, name) for name in type(record).model_fields}
    if kind == "wrong-id":
        fields["result"] = record.result.model_copy(update={"request_id": "cog-" + "e" * 64})
    elif kind == "forged-result":
        fields["result"] = _bypass(record.result, affect="invented")
    elif kind == "v1-result":
        from tests.unit.cognition.test_city_cache import _case as v1_case

        fields["result"] = v1_case(tmp_path / "v1")[1].result
    elif kind == "half-usage":
        fields["completion_tokens"] = None
    elif kind == "bool-usage":
        fields["prompt_tokens"] = True
    elif kind == "overflow":
        fields["completion_tokens"] = 2**63
    elif kind in {"bool-version", "float-version"}:
        fields["schema_version"] = True if kind == "bool-version" else 2.0
    else:
        fields["api_key"] = "opaque-fixture-secret"
    with pytest.raises((TypeError, ValueError)):
        type(record).model_validate(fields)
    if kind != "extra-secret":
        with pytest.raises((TypeError, ValueError)):
            cache.put(record.key, type(record).model_construct(**fields))
    assert not _path(cache, record.key).exists()


def test_cross_namespace_records_never_become_hits(tmp_path):
    cache, record = _cache_case(tmp_path)
    from tests.unit.cognition.test_city_cache import _case as v1_case
    from tests.unit.cognition.test_city_cache import _path as v1_path

    old_cache, old_record = v1_case(tmp_path / "v1")
    v2_path = _path(cache, record.key)
    v2_path.parent.mkdir()
    v2_path.write_bytes((canonical_json(old_record) + "\n").encode())
    with pytest.raises(_storage().CityCognitionCacheCorruption):
        cache.get(record.key)
    old_path = v1_path(old_cache, old_record)
    old_path.parent.mkdir()
    old_path.write_bytes((canonical_json(record) + "\n").encode())
    with pytest.raises(_storage().CityCognitionCacheCorruption):
        old_cache.get(old_record.key)
    old_path.write_bytes((canonical_json(old_record) + "\n").encode())
    assert old_cache.get(old_record.key) == old_record


def test_no_clobber_conflict_preserves_first_valid_record(tmp_path):
    cache, record = _cache_case(tmp_path)
    cache.put(record.key, record)
    original = _path(cache, record.key).read_bytes()
    with pytest.raises(_storage().CityCognitionCacheConflict):
        cache.put(record.key, record)
    changed = record.model_copy(
        update={"result": record.result.model_copy(update={"affect": "positive"})}
    )
    with pytest.raises(_storage().CityCognitionCacheConflict):
        cache.put(record.key, changed)
    assert _path(cache, record.key).read_bytes() == original


@pytest.mark.parametrize("potentially_committed", [False, True])
def test_publication_ambiguity_propagates_shared_identity(
    tmp_path, monkeypatch, potentially_committed
):
    cache, record = _cache_case(tmp_path)
    storage = _storage()
    error = storage.CityCognitionCachePublicationError(potentially_committed)

    def fail(self, key, document):
        assert key == record.key
        assert document == (canonical_json(record) + "\n").encode()
        raise error

    monkeypatch.setattr(storage.CityCognitionCacheByteStorage, "publish", fail)
    with pytest.raises(storage.CityCognitionCachePublicationError) as caught:
        cache.put(record.key, record)
    assert caught.value is error
    assert caught.value.potentially_committed is potentially_committed


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "reparse"])
def test_v2_cache_retains_storage_link_refusal(tmp_path, monkeypatch, kind):
    cache, record = _cache_case(tmp_path)
    cache.put(record.key, record)
    path = _path(cache, record.key)
    if kind == "symlink":
        target = tmp_path / "external.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("symlinks unavailable")
    elif kind == "hardlink":
        os.link(path, tmp_path / "second-link.json")
    else:
        from types import SimpleNamespace

        storage = _storage()
        original = storage.os.lstat

        def reparse(candidate, *args, **kwargs):
            stat = original(candidate, *args, **kwargs)
            if Path(candidate) == path:
                return SimpleNamespace(
                    **(
                        {name: getattr(stat, name) for name in dir(stat) if name.startswith("st_")}
                        | {"st_file_attributes": 0x400}
                    ),
                )
            return stat

        monkeypatch.setattr(storage.os, "lstat", reparse)
    with pytest.raises(
        (_storage().CityCognitionCacheCorruption, _storage().CityCognitionCacheUnavailable)
    ):
        cache.get(record.key)
