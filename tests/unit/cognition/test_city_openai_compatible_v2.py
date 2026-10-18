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
        "length",
        "content_filter",
        "tool_calls",
        "function_call",
        "none-finish",
        "stream",
        "extra",
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
        "usage-extra",
        "usage-details",
        "root-missing",
    ],
)
async def test_outer_envelope_requires_exact_stop_and_closed_protocol(change) -> None:
    _module()
    request, _ = _provider_case()
    envelope = json.loads(_completion(json.dumps(_result(request.request_id))))
    choice = envelope["choices"][0]
    message = choice["message"]
    if change == "missing-finish":
        choice.pop("finish_reason")
    elif change in {"length", "content_filter", "tool_calls", "function_call"}:
        choice["finish_reason"] = change
    elif change == "none-finish":
        choice["finish_reason"] = None
    elif change == "stream":
        envelope["object"] = "chat.completion.chunk"
    elif change == "extra":
        envelope["unknown"] = "opaque-diagnostic-marker"
    elif change == "refusal":
        message["refusal"] = "Refused"
    elif change in {"tools", "function", "audio", "reasoning"}:
        message[
            {
                "tools": "tool_calls",
                "function": "function_call",
                "audio": "audio",
                "reasoning": "reasoning_content",
            }[change]
        ] = []
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
    elif change == "usage-extra":
        envelope["usage"]["unknown"] = 1
    elif change == "usage-details":
        envelope["usage"]["completion_tokens_details"] = {"reasoning_tokens": True}
    elif change == "root-missing":
        envelope = {}
    _, _, provider, *_ = _provider(json.dumps(envelope).encode())
    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)
    assert caught.value.__cause__ is caught.value.__context__ is None
    await provider.aclose()


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
