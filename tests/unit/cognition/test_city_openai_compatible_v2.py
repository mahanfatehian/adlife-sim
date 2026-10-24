"""Strict V2 response admission over the shared hardened HTTP transport."""

from __future__ import annotations

import importlib
import json
from dataclasses import replace

import pytest

from adlife.adapters.cognition.city_endpoint_policy import CityEndpointBindingError
from adlife.adapters.cognition.city_provider_transport import (
    CityProviderProtocolError,
    CityProviderResponseTooLargeError,
    CityProviderUnavailableError,
)
from adlife.core.domain.city_cognition_v2 import CityCognitionResultV2, CityProviderAttemptV2
from tests.unit.cognition.test_city_openai_compatible import (
    FAKE_CREDENTIAL,
    REMOTE_ADDRESS,
    _BackendFactory,
    _case,
    _completion,
    _http_response,
    _remote_credential,
)
from tests.unit.cognition.test_city_prompts_v2 import _provider_case


def _module():
    return importlib.import_module("adlife.adapters.cognition.city_openai_compatible_v2")


def _result(bound_request_id, **updates):
    return (
        dict(
            schema_version=2,
            request_id=bound_request_id,
            affect="curious",
            relevance="high",
            credibility="medium",
            salience="low",
            safety_flags=[],
            disclosure="synthetic-bounded-cognition-not-observed-behavior",
        )
        | updates
    )


def _provider(body=None, *, response=None, factory=None, resolver=None):
    module = _module()
    request, frozen = _provider_case()
    _, _, policy, endpoint, resolver = _case(resolver=resolver)
    if body is None:
        body = _completion(json.dumps(_result(request.request_id)))
    factory = _BackendFactory((response or _http_response(body),)) if factory is None else factory
    credential = _remote_credential(frozen)
    provider = module.CityOpenAICompatibleProviderV2(
        endpoint_policy=policy,
        endpoint=endpoint,
        cognition_input=frozen,
        credential=credential,
        network_backend_factory=factory,
    )
    return request, frozen, provider, credential.secret, resolver, factory


async def test_valid_exchange_uses_minimized_payload_and_fresh_exact_v2_models() -> None:
    module = _module()
    request, frozen, provider, credential, resolver, factory = _provider()
    async with provider:
        attempt = await provider.call(request)
    assert type(attempt) is CityProviderAttemptV2
    assert type(attempt.result) is CityCognitionResultV2
    assert attempt.status == "valid"
    assert attempt.result.request_id == request.request_id
    assert attempt.usage.prompt_tokens == 20
    assert provider.is_closed
    assert FAKE_CREDENTIAL not in repr(provider)
    wire = b"".join(factory.backends[0].stream.writes)
    headers, body = wire.split(b"\r\n\r\n", 1)
    assert b"Authorization: Bearer " + FAKE_CREDENTIAL.encode() in headers
    assert b"Accept-Encoding: identity" in headers
    assert json.loads(body) == importlib.import_module(
        "adlife.adapters.cognition.city_prompts_v2"
    ).build_city_cognition_payload_v2(request, frozen)
    assert FAKE_CREDENTIAL.encode() not in body
    assert b"person-001" not in body and b"request_record_sha256" not in body
    assert resolver.calls == [("api.example.invalid", 443)]
    assert factory.backends[0].connects == [(REMOTE_ADDRESS, 443)]
    assert module.CityOpenAICompatibleProviderV2 is type(provider)
    assert credential.cleared is False
    credential.clear()


@pytest.mark.parametrize("metadata", ["created", "usage", "completion-id", "model-id"])
async def test_realistic_unix_created_timestamp_does_not_trigger_sensitive_text_screening(
    metadata,
) -> None:
    _module()
    request, _ = _provider_case()
    updates = {
        "created": {"created": 1792310400},
        "usage": {
            "usage": {
                "prompt_tokens": 1792310400,
                "completion_tokens": 12,
                "total_tokens": 1792310412,
            }
        },
        "completion-id": {"id": "1792310400"},
        "model-id": {"model": "1792310400"},
    }[metadata]
    request, _, provider, *_ = _provider(
        _completion(json.dumps(_result(request.request_id)), **updates)
    )
    attempt = await provider.call(request)
    assert attempt.status == "valid"
    assert attempt.result.request_id == request.request_id
    await provider.aclose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2.0),
        ("schema_version", 1),
        ("request_id", "cog-" + "0" * 64),
        ("disclosure", "wrong"),
        ("affect", "happy"),
        ("relevance", "very-high"),
        ("credibility", "none"),
        ("salience", "loud"),
        ("safety_flags", ["unknown"]),
        ("safety_flags", ["unsafe-content", "unsafe-content"]),
        ("extra", "discard me"),
        ("purchase_probability", 0.5),
    ],
)
async def test_invalid_inner_fields_return_typed_invalid_attempt(field, value) -> None:
    _module()
    request, _ = _provider_case()
    _, _, provider, *_ = _provider(
        _completion(json.dumps(_result(request.request_id, **{field: value})))
    )
    attempt = await provider.call(request)
    assert attempt.status == "invalid-result"
    assert attempt.result is None
    assert attempt.repair_context.reason_code == (
        "request-id-mismatch" if field == "request_id" else "invalid-schema"
    )
    await provider.aclose()


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "request_id",
        "affect",
        "relevance",
        "credibility",
        "salience",
        "safety_flags",
        "disclosure",
    ],
)
async def test_every_result_field_is_required(field) -> None:
    _module()
    request, _ = _provider_case()
    result = _result(request.request_id)
    result.pop(field)
    _, _, provider, *_ = _provider(_completion(json.dumps(result)))
    assert (await provider.call(request)).status == "invalid-result"
    await provider.aclose()


@pytest.mark.parametrize(
    "inner",
    [
        "[]",
        "null",
        "true",
        "not-json",
        '{"affect":"neutral","affect":"curious"}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e9999}',
        '"\\ud800"',
        "[" * 2000 + "0" + "]" * 2000,
    ],
)
async def test_strict_inner_json_rejects_duplicates_nonfinite_and_wrong_roots(inner) -> None:
    _module()
    request, _, provider, *_ = _provider(_completion(inner))
    assert (await provider.call(request)).status == "invalid-result"
    await provider.aclose()


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        b"not-json",
        b"[]",
        b'{"choices":[],"choices":[]}',
        b'{"score":NaN}',
        b'{"score":1e9999}',
        b"[" * 2000 + b"0" + b"]" * 2000,
    ],
)
async def test_strict_outer_json_returns_invalid_attempt(body) -> None:
    _module()
    request, _, provider, *_ = _provider(body)
    attempt = await provider.call(request)
    assert attempt.status == "invalid-result"
    assert attempt.repair_context.reason_code == "invalid-json"
    await provider.aclose()


async def test_malformed_outer_unicode_is_rejected_in_the_strict_json_prepass() -> None:
    _module()
    request, _, provider, *_ = _provider(_completion("{}", id="\ud800"))
    attempt = await provider.call(request)
    assert attempt.status == "invalid-result"
    assert attempt.repair_context.reason_code == "invalid-json"
    await provider.aclose()


@pytest.mark.parametrize(
    "change",
    [
        "missing-finish",
        "content_filter",
        "tool_calls",
        "function_call",
        "none-finish",
        "stream",
        "refusal",
        "tools",
        "function",
        "audio",
        "reasoning",
        "content-array",
        "role",
        "index-bool",
        "index-other",
        "multiple",
        "usage-bool",
        "usage-total",
        "usage-details",
        "root-missing",
    ],
)
async def test_outer_envelope_requires_stop_or_length_and_closed_protocol(change) -> None:
    # finish_reason "length" has its own admission tests below: a truncated answer is a
    # repairable invalid result, not an incompatible protocol.
    _module()
    request, _ = _provider_case()
    envelope = json.loads(_completion(json.dumps(_result(request.request_id))))
    choice = envelope["choices"][0]
    message = choice["message"]
    if change == "missing-finish":
        choice.pop("finish_reason")
    elif change in {"content_filter", "tool_calls", "function_call"}:
        choice["finish_reason"] = change
    elif change == "none-finish":
        choice["finish_reason"] = None
    elif change == "stream":
        envelope["object"] = "chat.completion.chunk"
    elif change == "refusal":
        message["refusal"] = "Refused"
    elif change in {"tools", "function", "audio", "reasoning"}:
        # Empty tool calls and null vLLM members are admitted; a real call, function,
        # audio part, or non-text reasoning member is not.
        name, value = {
            "tools": (
                "tool_calls",
                [{"id": "call-1", "type": "function", "function": {"name": "lookup"}}],
            ),
            "function": ("function_call", {"name": "lookup", "arguments": "{}"}),
            "audio": ("audio", {"id": "audio-1"}),
            "reasoning": ("reasoning_content", []),
        }[change]
        message[name] = value
    elif change == "content-array":
        message["content"] = [{"type": "text", "text": "{}"}]
    elif change == "role":
        message["role"] = "user"
    elif change == "index-bool":
        choice["index"] = False
    elif change == "index-other":
        choice["index"] = 1
    elif change == "multiple":
        envelope["choices"].append(choice)
    elif change == "usage-bool":
        envelope["usage"]["prompt_tokens"] = True
    elif change == "usage-total":
        envelope["usage"]["total_tokens"] = 1
    elif change == "usage-details":
        envelope["usage"]["completion_tokens_details"] = {"reasoning_tokens": True}
    elif change == "root-missing":
        envelope = {}
    _, _, provider, *_ = _provider(json.dumps(envelope).encode())
    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)
    assert caught.value.__cause__ is caught.value.__context__ is None
    await provider.aclose()


def _gateway_prompt_text(request, frozen) -> str:
    """The chat-templated prompt a vLLM gateway may echo back in ``prompt_text``."""
    messages = importlib.import_module(
        "adlife.adapters.cognition.city_prompts_v2"
    ).build_city_cognition_messages_v2(request, frozen)
    return (
        "".join(
            f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n"
            for message in messages
        )
        + "<|im_start|>assistant\n"
    )


def _vllm_completion(
    content,
    *,
    finish_reason="stop",
    prompt_text=None,
    reasoning=None,
    reasoning_tokens=0,
    completion_tokens=137,
    envelope_updates=None,
    choice_updates=None,
    message_updates=None,
) -> bytes:
    """The non-streaming envelope GapGPT returned for ``gapgpt-qwen-3.6``.

    That model ID resolves to Qwen/Qwen3.6-35B-A3B-FP8 served by vLLM, which adds its own
    metadata members to the OpenAI shape. The member names and types are the ones observed
    live; the values are fictional.
    """
    envelope = json.loads(_completion(content))
    envelope.update(
        prompt_logprobs=None,
        prompt_token_ids=[151644, 8948, 198, 151645],
        prompt_text=prompt_text,
        kv_transfer_params=None,
        ec_transfer_params=None,
        metrics={"arrival_time": 1792310400.25, "first_token_latency": 0.41, "queue": 0},
    )
    envelope["usage"] = {
        "prompt_tokens": 992,
        "completion_tokens": completion_tokens,
        "total_tokens": 992 + completion_tokens,
        "prompt_tokens_details": None,
        "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
    }
    choice = envelope["choices"][0]
    choice.update(
        finish_reason=finish_reason,
        logprobs=None,
        stop_reason=None,
        token_ids=None,
        routed_experts=None,
    )
    choice["message"].update(
        annotations=None,
        audio=None,
        function_call=None,
        tool_calls=[],
        reasoning=reasoning,
        reasoning_content=None,
    )
    envelope.update(envelope_updates or {})
    choice.update(choice_updates or {})
    choice["message"].update(message_updates or {})
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


def test_admitted_vllm_members_are_exactly_the_observed_set() -> None:
    """Widening the admitted vendor members is a deliberate contract change, never silent."""
    module = _module()
    assert set(module._VLLM_ENVELOPE_MEMBERS) == {
        "prompt_logprobs",
        "prompt_token_ids",
        "prompt_text",
        "kv_transfer_params",
        "ec_transfer_params",
        "metrics",
    }
    assert set(module._VLLM_CHOICE_MEMBERS) == {"stop_reason", "token_ids", "routed_experts"}
    assert set(module._VLLM_MESSAGE_MEMBERS) == {
        "annotations",
        "audio",
        "function_call",
        "tool_calls",
        "reasoning",
        "reasoning_content",
    }
    assert module._FINISH_REASONS == ("stop", "length")


def _gateway_completion(content, gateway) -> bytes:
    """The extra members two other OpenAI-compatible gateways add (values fictional)."""
    envelope = json.loads(_completion(content))
    choice = envelope["choices"][0]
    if gateway == "openrouter":
        envelope.update(provider="Fictional Inference", system_fingerprint=None)
        choice.update(native_finish_reason="stop", logprobs=None)
        choice["message"].update(reasoning=None, reasoning_details=[], refusal=None)
        envelope["usage"].update(
            cost=0.000137,
            is_byok=False,
            prompt_tokens_details={"cached_tokens": 0, "audio_tokens": 0},
            completion_tokens_details={"reasoning_tokens": 0, "image_tokens": 0},
            cost_details={"upstream_inference_cost": None},
        )
    else:
        envelope.update(
            stats={"tokens_per_second": 41.5, "time_to_first_token": 0.2},
            system_fingerprint="fictional-local-model",
        )
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("gateway", ["openrouter", "lm-studio"])
async def test_other_gateways_extra_members_are_ignored_not_refused(gateway) -> None:
    """Members nobody reads (OpenRouter's provider and cost, LM Studio's stats) are inert."""
    _module()
    request, _ = _provider_case()
    body = _gateway_completion(json.dumps(_result(request.request_id)), gateway)
    _, _, provider, credential, *_ = _provider(body)
    attempt = await provider.call(request)
    assert attempt.status == "valid"
    assert attempt.result == CityCognitionResultV2.model_validate_json(
        json.dumps(_result(request.request_id))
    )
    retained = attempt.model_dump_json() + repr(attempt) + repr(provider)
    for vendor_text in ("Fictional Inference", "tokens_per_second", "is_byok"):
        assert vendor_text not in retained
    await provider.aclose()
    credential.clear()


async def test_vllm_gateway_metadata_is_admitted_but_never_parsed_or_retained() -> None:
    _module()
    request, frozen = _provider_case()
    reasoning = "Relevance is high because the declared interests overlap the targets."
    prompt_text = _gateway_prompt_text(request, frozen)
    body = _vllm_completion(
        json.dumps(_result(request.request_id)), prompt_text=prompt_text, reasoning=reasoning
    )
    _, _, provider, credential, *_ = _provider(body)
    attempt = await provider.call(request)
    assert attempt.status == "valid"
    assert attempt.result == CityCognitionResultV2.model_validate_json(
        json.dumps(_result(request.request_id))
    )
    assert (attempt.usage.prompt_tokens, attempt.usage.completion_tokens) == (992, 137)
    retained = attempt.model_dump_json() + repr(attempt) + repr(provider)
    for vendor_text in (reasoning, "Relevance is high", "im_start", "arrival_time"):
        assert vendor_text not in retained
    await provider.aclose()
    credential.clear()


@pytest.mark.parametrize(
    ("container", "field", "value"),
    [
        ("envelope", "prompt_token_ids", None),
        ("envelope", "prompt_token_ids", []),
        ("envelope", "prompt_text", None),
        ("envelope", "metrics", None),
        ("envelope", "metrics", {}),
        ("choice", "routed_experts", [[3, 17], [5, 101]]),
        # vLLM names the stop token or stop string that ended an answer, for example the
        # integer ID of a generation-config end token, and may list the generated tokens.
        ("choice", "stop_reason", 151643),
        ("choice", "stop_reason", "</s>"),
        ("choice", "token_ids", [12, 13]),
        ("choice", "token_ids", []),
        ("message", "annotations", []),
        ("message", "tool_calls", None),
        ("message", "reasoning", ""),
        ("message", "reasoning_content", "A second reasoning member is ignored as well."),
    ],
)
async def test_each_admitted_vllm_member_accepts_null_or_its_observed_type(
    container, field, value
) -> None:
    _module()
    request, _ = _provider_case()
    updates = {f"{container}_updates": {field: value}}
    _, _, provider, *_ = _provider(
        _vllm_completion(json.dumps(_result(request.request_id)), **updates)
    )
    assert (await provider.call(request)).status == "valid"
    await provider.aclose()


@pytest.mark.parametrize("container", ["envelope", "choice", "message"])
async def test_every_vllm_member_is_optional(container) -> None:
    _module()
    request, _ = _provider_case()
    vllm = json.loads(_vllm_completion(json.dumps(_result(request.request_id))))
    plain = json.loads(_completion(json.dumps(_result(request.request_id))))
    target = {
        "envelope": vllm,
        "choice": vllm["choices"][0],
        "message": vllm["choices"][0]["message"],
    }[container]
    baseline = {
        "envelope": plain,
        "choice": plain["choices"][0],
        "message": plain["choices"][0]["message"],
    }[container]
    for field in set(target) - set(baseline):
        target.pop(field)
    _, _, provider, *_ = _provider(json.dumps(vllm).encode())
    assert (await provider.call(request)).status == "valid"
    await provider.aclose()


@pytest.mark.parametrize(
    ("container", "field", "value"),
    [
        ("envelope", "prompt_logprobs", []),
        ("envelope", "prompt_logprobs", {"0": -0.1}),
        ("envelope", "prompt_token_ids", [151644, "198"]),
        ("envelope", "prompt_token_ids", [True]),
        ("envelope", "prompt_token_ids", [-1]),
        ("envelope", "prompt_token_ids", [1.5]),
        ("envelope", "prompt_token_ids", "151644 198"),
        ("envelope", "prompt_text", 7),
        ("envelope", "prompt_text", ["system"]),
        ("envelope", "kv_transfer_params", {}),
        ("envelope", "kv_transfer_params", "remote"),
        ("envelope", "ec_transfer_params", {}),
        ("envelope", "metrics", []),
        ("envelope", "metrics", "fast"),
        ("choice", "stop_reason", -1),
        ("choice", "stop_reason", True),
        ("choice", "stop_reason", 1.5),
        ("choice", "stop_reason", ["</s>"]),
        ("choice", "stop_reason", "x" * 257),
        ("choice", "token_ids", ["12"]),
        ("choice", "token_ids", [True]),
        ("choice", "token_ids", 12),
        ("choice", "routed_experts", "0,1"),
        ("choice", "routed_experts", [[1, 2.5]]),
        ("choice", "routed_experts", [[True]]),
        ("choice", "routed_experts", {"layer": 1}),
        ("message", "annotations", [{"type": "url_citation"}]),
        ("message", "annotations", {}),
        ("message", "audio", {"id": "audio-1"}),
        ("message", "function_call", {"name": "lookup"}),
        ("message", "tool_calls", [{"id": "call-1", "type": "function"}]),
        ("message", "tool_calls", {}),
        ("message", "reasoning", 3),
        ("message", "reasoning", ["step"]),
        ("message", "reasoning_content", {"text": "step"}),
    ],
)
async def test_vllm_members_with_another_type_are_protocol_errors(container, field, value) -> None:
    _module()
    request, _ = _provider_case()
    updates = {f"{container}_updates": {field: value}}
    _, _, provider, *_ = _provider(
        _vllm_completion(json.dumps(_result(request.request_id)), **updates)
    )
    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)
    assert caught.value.__cause__ is caught.value.__context__ is None
    await provider.aclose()


@pytest.mark.parametrize(
    ("container", "field", "value"),
    [
        ("envelope", "prompt_text", "system: api_key=opaquevalue1234567890"),
        ("envelope", "metrics", {"note": "Authorization: Bearer abcdefghijklmnop"}),
        ("message", "reasoning", "The key FAKE is in my context."),
        ("message", "reasoning_content", "Contact fictional.person@example.org for details."),
    ],
)
async def test_vllm_member_text_keeps_every_whole_body_secret_screen(
    container, field, value
) -> None:
    _module()
    request, _ = _provider_case()
    if value == "The key FAKE is in my context.":
        value = value.replace("FAKE", FAKE_CREDENTIAL)
    updates = {f"{container}_updates": {field: value}}
    _, _, provider, *_ = _provider(
        _vllm_completion(json.dumps(_result(request.request_id)), **updates)
    )
    attempt = await provider.call(request)
    assert attempt.status == "invalid-result"
    assert attempt.repair_context.reason_code == "unsafe-content"
    assert (value if isinstance(value, str) else value["note"]) not in attempt.model_dump_json()
    await provider.aclose()


_RUNAWAY = "\n  " + "\t" * 240


async def test_length_finish_accepts_a_complete_valid_object_followed_by_runaway_whitespace():
    _module()
    request, _ = _provider_case()
    body = _vllm_completion(
        json.dumps(_result(request.request_id), indent=1) + _RUNAWAY,
        finish_reason="length",
        completion_tokens=256,
    )
    _, _, provider, *_ = _provider(body)
    attempt = await provider.call(request)
    assert attempt.status == "valid"
    assert attempt.result.request_id == request.request_id
    assert attempt.usage.completion_tokens == 256
    await provider.aclose()


@pytest.mark.parametrize(
    "content",
    [
        # vLLM grammar decoding that never closes the object and fills max_tokens.
        "unclosed",
        # A thinking model that spent every output token reasoning.
        "",
        None,
        " \n\t ",
        '{"schema_version": 2, "request_id": "cog-',
    ],
)
async def test_length_finish_without_a_complete_object_is_a_repairable_invalid_json(content):
    _module()
    request, _ = _provider_case()
    if content == "unclosed":
        content = json.dumps(_result(request.request_id), indent=1)[:-2] + _RUNAWAY
    body = _vllm_completion(
        content,
        finish_reason="length",
        completion_tokens=256,
        reasoning="Let me weigh each category before answering." if not content else None,
        reasoning_tokens=256 if not (content or "").strip() else 0,
    )
    _, _, provider, *_ = _provider(body)
    attempt = await provider.call(request)
    assert attempt.status == "invalid-result"
    assert attempt.result is None
    assert attempt.repair_context.reason_code == "invalid-json"
    await provider.aclose()


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"request_id": "cog-" + "0" * 64}, "request-id-mismatch"),
        ({"affect": "elated"}, "invalid-schema"),
        ({"safety_flags": ["unsafe-content", "unsafe-content"]}, "invalid-schema"),
    ],
)
async def test_length_finish_with_a_complete_but_invalid_object_keeps_its_reason(
    updates, reason
) -> None:
    _module()
    request, _ = _provider_case()
    body = _vllm_completion(
        json.dumps(_result(request.request_id, **updates)) + _RUNAWAY, finish_reason="length"
    )
    _, _, provider, *_ = _provider(body)
    attempt = await provider.call(request)
    assert attempt.status == "invalid-result"
    assert attempt.repair_context.reason_code == reason
    await provider.aclose()


@pytest.mark.parametrize(
    ("finish_reason", "message_updates"),
    [
        ("length", {"content": [{"type": "text", "text": "{}"}]}),
        ("length", {"content": 7}),
        ("length", {"refusal": "I cannot help with that."}),
        ("length", {"tool_calls": [{"id": "call-1", "type": "function"}]}),
        ("stop", {"content": ""}),
        ("stop", {"content": None}),
        ("stop", {"content": " \n"}),
    ],
)
async def test_truncation_never_relaxes_the_closed_message_protocol(
    finish_reason, message_updates
) -> None:
    _module()
    request, _ = _provider_case()
    body = _vllm_completion(
        json.dumps(_result(request.request_id)),
        finish_reason=finish_reason,
        message_updates=message_updates,
    )
    _, _, provider, *_ = _provider(body)
    with pytest.raises(CityProviderProtocolError):
        await provider.call(request)
    await provider.aclose()


async def test_whitespace_runaway_is_repaired_once_into_one_valid_signal() -> None:
    _module()
    request, _, provider, credential, _, factory = _provider()
    runaway = json.dumps(_result(request.request_id), indent=1)[:-2] + _RUNAWAY
    factory._responses = [
        _http_response(_vllm_completion(runaway, finish_reason="length", completion_tokens=256)),
        _http_response(_vllm_completion(json.dumps(_result(request.request_id)))),
    ]
    first = await provider.call(request)
    assert first.status == "invalid-result"
    assert first.repair_context.reason_code == "invalid-json"
    second = await provider.repair_call(request, context=first.repair_context)
    assert second.status == "valid"
    assert second.result.request_id == request.request_id
    sent = json.loads(b"".join(factory.backends[1].stream.writes).split(b"\r\n\r\n", 1)[1])
    assert "rejected with reason invalid-json" in sent["messages"][-1]["content"]
    await provider.aclose()
    credential.clear()


@pytest.mark.parametrize(
    "updates",
    [
        dict(affect="uncertain"),
        dict(relevance="uncertain"),
        dict(credibility="uncertain"),
        dict(salience="uncertain"),
        dict(safety_flags=["unsafe-content"]),
        dict(safety_flags=["unsupported-inference", "stereotype-risk"]),
    ],
)
async def test_uncertain_and_safety_results_remain_valid_raw_signals(updates) -> None:
    _module()
    request, _ = _provider_case()
    _, _, provider, *_ = _provider(_completion(json.dumps(_result(request.request_id, **updates))))
    attempt = await provider.call(request)
    assert attempt.status == "valid"
    for name, value in updates.items():
        assert attempt.result.model_dump(mode="json")[name] == value
    await provider.aclose()


async def test_v1_result_cannot_cross_v2_provider_admission() -> None:
    _module()
    from tests.unit.cognition.test_city_openai_compatible import _result as v1_result

    request, _ = _provider_case()
    _, _, provider, *_ = _provider(_completion(json.dumps(v1_result(request.request_id))))
    assert (await provider.call(request)).status == "invalid-result"
    await provider.aclose()


async def test_provider_repairs_once_with_only_reason_and_closes_its_private_credential() -> None:
    _module()
    request, _, provider, credential, _, factory = _provider()
    factory._responses = [
        _http_response(_completion("{}")),
        *_BackendFactory(
            (_http_response(_completion(json.dumps(_result(request.request_id)))),)
        )._responses,
    ]
    first = await provider.call(request)
    second = await provider.repair_call(request, context=first.repair_context)
    assert second.status == "valid"
    sent = json.loads(b"".join(factory.backends[1].stream.writes).split(b"\r\n\r\n", 1)[1])
    assert "invalid-schema" in sent["messages"][-1]["content"]
    await provider.aclose()
    with pytest.raises(CityProviderUnavailableError):
        await provider.call(request)
    assert not credential.cleared
    credential.clear()


@pytest.mark.parametrize("binding", ["revision", "origin", "profile", "slot", "source"])
def test_constructor_requires_exact_profile_endpoint_and_credential_slot(binding) -> None:
    module = _module()
    _request, frozen = _provider_case()
    _, _, policy, endpoint, _ = _case()
    credential = _remote_credential(frozen)
    if binding in {"revision", "origin", "profile"}:
        endpoint = replace(
            endpoint,
            **{
                {
                    "revision": "profile_revision_sha256",
                    "origin": "origin_approval_id",
                    "profile": "profile_id",
                }[binding]: "f" * 64
            },
        )
    else:
        credential = replace(
            credential, **{"credential_slot_id" if binding == "slot" else "source": "wrong"}
        )
    with pytest.raises(CityEndpointBindingError):
        module.CityOpenAICompatibleProviderV2(
            endpoint_policy=policy, endpoint=endpoint, cognition_input=frozen, credential=credential
        )
    credential.secret.clear()


@pytest.mark.parametrize("kind", ["redirect", "overflow", "peer", "dns"])
async def test_v2_uses_shared_transport_refusals(kind) -> None:
    _module()
    from tests.unit.cognition.test_city_openai_compatible import _Resolver

    class Drift(_Resolver):
        async def resolve(self, hostname, port):
            return ("127.0.0.1",)

    class PeerDrift(_BackendFactory):
        def __call__(self):
            backend = super().__call__()
            backend.stream.peer = "127.0.0.1"
            return backend

    request, _, provider, _, _, factory = _provider(
        resolver=Drift() if kind == "dns" else None,
        factory=PeerDrift((_http_response(b"{}"),)) if kind == "peer" else None,
    )
    expected = CityProviderUnavailableError
    if kind == "redirect":
        factory._responses[0] = _http_response(
            b"{}", status=302, headers=(("Location", "http://127.0.0.1/"),)
        )
        expected = CityProviderProtocolError
    elif kind == "overflow":
        factory._responses[0] = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1048577\r\n\r\n"
        )
        expected = CityProviderResponseTooLargeError
    with pytest.raises(expected):
        await provider.call(request)
    if kind == "peer":
        assert factory.backends[0].stream.writes == []
    await provider.aclose()


@pytest.mark.parametrize("override", [None, 3])
async def test_check_timeout_override_extends_only_this_v2_transport_deadline(override) -> None:
    module = _module()
    from tests.unit.cognition.test_city_openai_compatible import _profile, _SlowBackendFactory

    request, frozen = _provider_case(profile=_profile(timeout=1))
    _, _, policy, endpoint, _ = _case(timeout=1)
    factory = _SlowBackendFactory(
        (_http_response(_completion(json.dumps(_result(request.request_id)))),), delay=1.5
    )
    credential = _remote_credential(frozen)
    provider = module.CityOpenAICompatibleProviderV2(
        endpoint_policy=policy,
        endpoint=endpoint,
        cognition_input=frozen,
        credential=credential,
        network_backend_factory=factory,
        check_attempt_timeout_seconds=override,
    )
    try:
        if override is None:
            # Funded runs keep the frozen one-second attempt deadline of this profile.
            from adlife.adapters.cognition.city_provider_transport import CityProviderTimeoutError

            with pytest.raises(CityProviderTimeoutError):
                await provider.call(request)
        else:
            attempt = await provider.call(request)
            assert attempt.status == "valid"
        assert frozen.effective_settings.attempt_timeout_seconds == 1
    finally:
        await provider.aclose()
        credential.secret.clear()


@pytest.mark.parametrize("override", [0, 121, -1, True, 1.5, "30"])
def test_check_timeout_override_refuses_unbounded_or_coerced_v2_values(override) -> None:
    module = _module()
    _request, frozen = _provider_case()
    _, _, policy, endpoint, _ = _case()
    credential = _remote_credential(frozen)
    with pytest.raises(ValueError, match="failed validation"):
        module.CityOpenAICompatibleProviderV2(
            endpoint_policy=policy,
            endpoint=endpoint,
            cognition_input=frozen,
            credential=credential,
            check_attempt_timeout_seconds=override,
        )
    credential.secret.clear()
