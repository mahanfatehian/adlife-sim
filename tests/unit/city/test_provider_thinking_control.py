"""Opt-in thinking control: one recorded profile fact that changes only the wire request.

A Qwen model served by vLLM (GapGPT's ``gapgpt-qwen-3.6`` resolves to
Qwen/Qwen3.6-35B-A3B-FP8) may spend the whole frozen 256-token attempt cap reasoning and
return empty content with ``finish_reason`` ``length``. Sending ``chat_template_kwargs``
``{"enable_thinking": false}`` stopped that in every live call, but OpenAI refuses unknown
request arguments, so the member is sent only when a profile revision turns thinking off.
When the control is off, every profile, run, and hash stays byte-identical to the bytes
written before the control existed.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from adlife.adapters.cognition.city_openai_compatible_v2 import CityOpenAICompatibleProviderV2
from adlife.adapters.cognition.city_prompts import (
    build_city_cognition_payload,
    build_city_cognition_repair_payload,
)
from adlife.adapters.cognition.city_prompts_v2 import (
    build_city_cognition_payload_v2,
    build_city_cognition_repair_payload_v2,
)
from adlife.city import workbench_providers
from adlife.city.provider_compatibility import build_workbench_provider_compatibility_key
from adlife.city.provider_profiles import (
    ProviderProfileDraftV1,
    ProviderProfileRegistry,
)
from adlife.core.domain.city_cognition import (
    CityCognitionProviderProfileSnapshotV1,
    CityProviderRepairContextV1,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
    CityCognitionEffectiveSettingsV2,
    CityCognitionInputV2,
    CityProviderRepairContextV2,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.simulation.city_cognition import build_city_cognition_requests
from tests.unit.city.test_city_cognition import _evidence, _input
from tests.unit.city.test_provider_profiles import _draft, _record_path, _registry
from tests.unit.city.test_workbench_providers import _controller, _scoped_controller
from tests.unit.cognition.test_city_openai_compatible import (
    _BackendFactory,
    _case,
    _completion,
    _http_response,
    _profile,
    _remote_credential,
)
from tests.unit.cognition.test_city_openai_compatible_v2 import _result
from tests.unit.cognition.test_city_prompts_v2 import _provider_case

THINKING_OFF = {"enable_thinking": False}
# Hashes written before the control existed, by tests/golden/test_city_cognition_v2_science.py
# (its recorded-provider profile) and tests/unit/city/test_provider_profiles.py (_draft()).
GOLDEN_PROFILE_REVISION = "44af20b431db215f598bc6560d8ddcce06cd6fdeb6d50bdf46dd2ad8e395816b"
GOLDEN_PROFILE_SNAPSHOT = "d1e8412d1eeb9e03b73895c1d79bada17cf0825fd4a3b8b6ad909ec0c82adc97"
GOLDEN_EFFECTIVE_SETTINGS_V2 = "031dabcc9df66a7b83ed750f994c3705108a38ebd5b16b21bcbccf91608c6a00"
DRAFT_REVISION = "c1f628a955f1f1727f9824bcf426818c82fcbe0c268d4ff7ac08e30a62117622"
DRAFT_CREDENTIAL_SLOT = "65dcb348e53d13c216b8301ff5e06e5f26bb1b0e48eed716ab57fe3aafd7c52a"


def _recorded_facts(**updates: object) -> dict[str, object]:
    """The recorded-provider profile facts of the V10 core golden."""
    facts: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "fictional-provider",
        "credential_slot_id": "fictional-provider",
        "display_label": "Fictional recorded provider",
        "mode": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "fictional-model",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.0,
        "top_p": 1.0,
        "seed_policy": "omit",
        "seed": None,
        "max_output_tokens": 256,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "fictional-origin",
    }
    facts.update(updates)
    return facts


def _snapshot(**updates: object) -> CityCognitionProviderProfileSnapshotV1:
    facts = _recorded_facts(**updates)
    return CityCognitionProviderProfileSnapshotV1.model_validate(
        {**facts, "revision_sha256": city_cognition_provider_profile_revision_sha256(facts)}
    )


def _settings_v2(
    snapshot: CityCognitionProviderProfileSnapshotV1,
) -> CityCognitionEffectiveSettingsV2:
    """Exactly how the frozen V2 input derives its settings from the profile."""
    facts = {
        name: getattr(snapshot, name)
        for name in CityCognitionEffectiveSettingsV2.model_fields
        if name != "schema_version"
    }
    facts.update(
        schema_version=2,
        max_output_tokens=min(
            snapshot.max_output_tokens, CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT
        ),
    )
    return CityCognitionEffectiveSettingsV2.model_validate(facts)


def test_profiles_without_the_control_keep_their_exact_revision_bytes(tmp_path) -> None:
    explicit_default = ProviderProfileDraftV1.model_validate_json(
        json.dumps({**_draft().model_dump(mode="json"), "disable_thinking": False})
    )
    for draft in (_draft(), _draft(disable_thinking=False), explicit_default):
        assert draft == _draft()
        assert draft.disable_thinking is False
        assert "disable_thinking" not in draft.model_dump(mode="json")
        assert "disable_thinking" not in draft.model_dump(mode="python")
    registry = _registry(tmp_path)
    revision = registry.create(explicit_default)
    assert (revision.revision_sha256, revision.credential_slot_id) == (
        DRAFT_REVISION,
        DRAFT_CREDENTIAL_SLOT,
    )
    stored = _record_path(registry, revision).read_bytes()
    assert stored == (canonical_json(revision) + "\n").encode("utf-8")
    assert b"disable_thinking" not in stored


def test_turned_off_thinking_is_a_distinct_revision_that_records_the_fact(tmp_path) -> None:
    registry = _registry(tmp_path)
    plain = registry.create(_draft())
    revision = registry.create(_draft(disable_thinking=True))
    assert revision.disable_thinking is True
    assert revision.revision_sha256 != plain.revision_sha256
    assert revision.credential_slot_id != plain.credential_slot_id
    stored = _record_path(registry, revision).read_bytes()
    assert b'"disable_thinking":true' in stored
    assert stored == (canonical_json(revision) + "\n").encode("utf-8")
    assert revision.revision_sha256 == city_cognition_provider_profile_revision_sha256(
        json.loads(stored)
    )
    reopened = ProviderProfileRegistry(registry.workspace)
    assert reopened.load(revision.profile_id, revision.revision_sha256) == revision
    assert reopened.load(plain.profile_id, plain.revision_sha256).disable_thinking is False


@pytest.mark.parametrize("value", [1, 0, 1.0, "true", "false", None, [], {}])
def test_thinking_control_admits_only_an_exact_boolean(value) -> None:
    with pytest.raises(ValidationError):
        _draft(disable_thinking=value)
    with pytest.raises(ValidationError):
        ProviderProfileDraftV1.model_validate_json(
            json.dumps({**_draft().model_dump(mode="json"), "disable_thinking": value})
        )
    with pytest.raises(ValidationError):
        _snapshot(disable_thinking=value)


def test_unchanged_snapshot_and_v2_settings_keep_the_hashes_saved_runs_bind() -> None:
    snapshot = _snapshot()
    settings = _settings_v2(snapshot)
    assert snapshot.revision_sha256 == GOLDEN_PROFILE_REVISION
    assert snapshot.fingerprint == GOLDEN_PROFILE_SNAPSHOT
    assert settings.fingerprint == GOLDEN_EFFECTIVE_SETTINGS_V2
    assert _snapshot(disable_thinking=False) == snapshot
    assert "disable_thinking" not in canonical_json(snapshot) + canonical_json(settings)


def test_turned_off_thinking_is_frozen_into_snapshot_settings_and_v2_input() -> None:
    snapshot = _snapshot(disable_thinking=True)
    settings = _settings_v2(snapshot)
    assert '"disable_thinking":true' in canonical_json(snapshot)
    assert '"disable_thinking":true' in canonical_json(settings)
    assert snapshot.revision_sha256 != GOLDEN_PROFILE_REVISION
    assert snapshot.fingerprint != GOLDEN_PROFILE_SNAPSHOT
    assert settings.fingerprint != GOLDEN_EFFECTIVE_SETTINGS_V2

    _request, frozen = _provider_case(profile=_profile(disable_thinking=True))
    assert frozen.provider_profile.disable_thinking is True
    assert frozen.effective_settings.disable_thinking is True
    document = frozen.model_dump(mode="json")
    assert CityCognitionInputV2.model_validate_json(json.dumps(document)) == frozen
    document["effective_settings"].pop("disable_thinking")
    with pytest.raises(ValidationError):
        CityCognitionInputV2.model_validate_json(json.dumps(document))

    _request, plain = _provider_case()
    document = plain.model_dump(mode="json")
    document["effective_settings"]["disable_thinking"] = True
    with pytest.raises(ValidationError):
        CityCognitionInputV2.model_validate_json(json.dumps(document))


@pytest.mark.parametrize("disabled", [False, True])
def test_v2_requests_and_repairs_skip_thinking_only_when_the_profile_says_so(disabled) -> None:
    request, frozen = _provider_case(profile=_profile(disable_thinking=disabled))
    context = CityProviderRepairContextV2(
        schema_version=2,
        reason_code="invalid-json",
        safe_excerpt="The answer failed bounded signal validation.",
    )
    for payload in (
        build_city_cognition_payload_v2(request, frozen),
        build_city_cognition_repair_payload_v2(request, frozen, context=context),
    ):
        if disabled:
            assert payload["chat_template_kwargs"] == THINKING_OFF
        else:
            assert "chat_template_kwargs" not in payload


@pytest.mark.parametrize("disabled", [False, True])
def test_v1_explanations_honor_the_same_frozen_profile_fact(disabled) -> None:
    facts = _recorded_facts(origin_approval_id="local-ollama", max_output_tokens=384)
    if disabled:
        facts["disable_thinking"] = True
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        {**facts, "revision_sha256": city_cognition_provider_profile_revision_sha256(facts)}
    )
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    cognition_input = _input(
        context,
        (request,),
        mode="local",
        provider_profile=snapshot,
        aggregate_output_token_ceiling=1_536,
    )
    repair = CityProviderRepairContextV1(
        schema_version=1, reason_code="invalid-json", safe_excerpt="bounded provider excerpt"
    )
    for payload in (
        build_city_cognition_payload(request, cognition_input),
        build_city_cognition_repair_payload(request, cognition_input, context=repair),
    ):
        if disabled:
            assert payload["chat_template_kwargs"] == THINKING_OFF
        else:
            assert "chat_template_kwargs" not in payload


async def test_v2_provider_sends_the_control_on_the_attempt_and_its_repair() -> None:
    profile = _profile(disable_thinking=True, model_id="gapgpt-qwen-3.6")
    request, frozen = _provider_case(profile=profile)
    _, _, policy, _, _ = _case()
    factory = _BackendFactory(
        (
            _http_response(_completion("{}")),
            _http_response(_completion(json.dumps(_result(request.request_id)))),
        )
    )
    credential = _remote_credential(frozen)
    provider = CityOpenAICompatibleProviderV2(
        endpoint_policy=policy,
        endpoint=policy.bind(profile),
        cognition_input=frozen,
        credential=credential,
        network_backend_factory=factory,
    )
    try:
        first = await provider.call(request)
        assert first.status == "invalid-result"
        second = await provider.repair_call(request, context=first.repair_context)
        assert second.status == "valid"
    finally:
        await provider.aclose()
        credential.secret.clear()
    bodies = [
        json.loads(b"".join(backend.stream.writes).split(b"\r\n\r\n", 1)[1])
        for backend in factory.backends
    ]
    assert len(bodies) == 2
    assert all(body["chat_template_kwargs"] == THINKING_OFF for body in bodies)
    assert all(body["model"] == "gapgpt-qwen-3.6" for body in bodies)


def test_both_compatibility_probes_and_the_check_key_bind_the_control(tmp_path) -> None:
    controller, _keyring, _providers = _controller(tmp_path)
    plain = controller.registry.create(_draft())
    off = controller.registry.create(_draft(disable_thinking=True))

    plain_request, plain_input = workbench_providers._compatibility_probe_v2(plain)
    off_request, off_input = workbench_providers._compatibility_probe_v2(off)
    assert plain_input.effective_settings.disable_thinking is False
    assert off_input.effective_settings.disable_thinking is True
    assert "chat_template_kwargs" not in build_city_cognition_payload_v2(plain_request, plain_input)
    assert build_city_cognition_payload_v2(off_request, off_input)["chat_template_kwargs"] == (
        THINKING_OFF
    )

    v1_request = workbench_providers._compatibility_request()
    v1_plain = workbench_providers._compatibility_input(plain, v1_request)
    v1_off = workbench_providers._compatibility_input(off, v1_request)
    assert "chat_template_kwargs" not in build_city_cognition_payload(v1_request, v1_plain)
    assert build_city_cognition_payload(v1_request, v1_off)["chat_template_kwargs"] == (
        THINKING_OFF
    )

    for scope in ("explanation-only", "bounded-signals-v1"):
        plain_key = build_workbench_provider_compatibility_key(plain, effect_scope=scope)
        off_key = build_workbench_provider_compatibility_key(off, effect_scope=scope)
        assert plain_key.revision_sha256 != off_key.revision_sha256
        assert plain_key.profile_snapshot_sha256 != off_key.profile_snapshot_sha256
        assert plain_key.fingerprint != off_key.fingerprint
    bounded_plain = build_workbench_provider_compatibility_key(
        plain, effect_scope="bounded-signals-v1"
    )
    bounded_off = build_workbench_provider_compatibility_key(off, effect_scope="bounded-signals-v1")
    assert bounded_plain.effective_settings_sha256 != bounded_off.effective_settings_sha256


async def test_bounded_check_builds_its_probe_from_the_selected_revision(tmp_path) -> None:
    controller, _keyring, _v1, providers = _scoped_controller(tmp_path)
    off = controller.registry.create(_draft(disable_thinking=True))
    outcome = await controller.check(
        off.profile_id, off.revision_sha256, effect_scope="bounded-signals-v1"
    )
    assert outcome.status == "compatible"
    probe_input = providers[0].kwargs["cognition_input"]
    assert probe_input.provider_profile.disable_thinking is True
    assert probe_input.effective_settings.disable_thinking is True


def test_provider_views_report_the_control_only_when_thinking_is_turned_off(tmp_path) -> None:
    controller, _keyring, _providers = _controller(tmp_path)
    plain = controller.create(_draft())
    off = controller.create(
        {**_draft(model_id="gapgpt-qwen-3.6").model_dump(mode="json"), "disable_thinking": True}
    )
    assert "disable_thinking" not in plain.model_dump(mode="json")
    assert off.model_dump(mode="json")["disable_thinking"] is True
    listed = {
        profile["model_id"]: profile
        for profile in controller.list().model_dump(mode="json")["profiles"]
    }
    assert "disable_thinking" not in listed["qwen2.5:7b"]
    assert listed["gapgpt-qwen-3.6"]["disable_thinking"] is True
