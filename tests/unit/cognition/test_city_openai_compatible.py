from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import ssl
import time
import warnings
from collections.abc import Iterable
from dataclasses import replace
from hashlib import sha256
from typing import Any, cast

import httpcore
import pytest

from adlife.adapters.cognition import city_openai_compatible
from adlife.adapters.cognition.city_credentials import ResolvedCredential, SecretBuffer
from adlife.adapters.cognition.city_endpoint_policy import (
    CityEndpointBindingError,
    CityEndpointPolicy,
)
from adlife.adapters.cognition.city_openai_compatible import (
    CityOpenAICompatibleProvider,
    CityProviderAuthenticationError,
    CityProviderCallError,
    CityProviderProtocolError,
    CityProviderRateLimitError,
    CityProviderResponseTooLargeError,
    CityProviderServerError,
    CityProviderTimeoutError,
    CityProviderTransportError,
    CityProviderUnavailableError,
)
from adlife.city.provider_profiles import (
    ProviderProfileDraftV1,
    ProviderProfileRevisionV1,
)
from adlife.city.provider_runtime import build_provider_runtime_policy
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DISCLOSURE,
    MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES,
    CityCognitionInputV1,
    CityCognitionProviderProfileSnapshotV1,
    CityProviderRepairContextV1,
    canonical_city_cognition_provider_profile_payload,
    city_cognition_provider_profile_revision_sha256,
)
from adlife.core.simulation.city_cognition import build_city_cognition_requests
from tests.unit.city.test_city_cognition import _evidence, _input

REMOTE_BASE_URL = "https://api.example.invalid/openai/v1"
REMOTE_ADDRESS = "93.184.216.34"
FAKE_CREDENTIAL = "sk-test-city-provider-secret"


def test_v1_provider_reexports_the_shared_error_class_identities() -> None:
    shared = importlib.import_module("adlife.adapters.cognition.city_provider_transport")
    for name in (
        "CityProviderCallError",
        "CityProviderAuthenticationError",
        "CityProviderTimeoutError",
        "CityProviderRateLimitError",
        "CityProviderServerError",
        "CityProviderUnavailableError",
        "CityProviderResponseTooLargeError",
        "CityProviderProtocolError",
        "CityProviderTransportError",
    ):
        assert getattr(city_openai_compatible, name) is getattr(shared, name)


def _adlife_traceback_local_rendering(error: BaseException) -> str:
    retained_locals: list[str] = []
    current = error.__traceback__
    while current is not None:
        module_name = current.tb_frame.f_globals.get("__name__")
        if isinstance(module_name, str) and module_name.startswith("adlife."):
            retained_locals.extend(repr(value) for value in current.tb_frame.f_locals.values())
        current = current.tb_next
    return "\n".join(retained_locals)


def test_transport_depends_only_on_httpcore_public_backend_api() -> None:
    source = inspect.getsource(city_openai_compatible)

    assert "httpcore._backends" not in source
    assert isinstance(httpcore.AnyIOBackend(), httpcore.AsyncNetworkBackend)


def _profile(*, timeout: int = 30, **updates: object) -> ProviderProfileRevisionV1:
    document: dict[str, object] = {
        "schema_version": 1,
        "profile_id": "remote-acme",
        "display_label": "Remote Acme",
        "mode": "remote",
        "base_url": REMOTE_BASE_URL,
        "model_id": "example-model",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 192,
        "attempt_timeout_seconds": timeout,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "remote-acme",
    }
    document.update(updates)
    draft = ProviderProfileDraftV1.model_validate(document)
    credential_slot_id = sha256(
        canonical_city_cognition_provider_profile_payload(draft)
    ).hexdigest()
    facts = {**draft.model_dump(mode="json"), "credential_slot_id": credential_slot_id}
    return ProviderProfileRevisionV1.model_validate(
        {
            **facts,
            "revision_sha256": city_cognition_provider_profile_revision_sha256(facts),
        }
    )


class _Resolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        self.calls.append((hostname, port))
        return (REMOTE_ADDRESS,)


def _case(*, timeout: int = 30, resolver: object | None = None):
    profile = _profile(timeout=timeout)
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        profile.model_dump(mode="json")
    )
    compiled, responses, context = _evidence()
    request = build_city_cognition_requests(context, compiled.mobility, responses)[0]
    cognition_input = _input(
        context,
        (request,),
        mode="remote",
        provider_profile=snapshot,
        aggregate_output_token_ceiling=1_536,
    )
    resolver = _Resolver() if resolver is None else resolver
    policy = CityEndpointPolicy(
        build_provider_runtime_policy(
            provider_origins=(f"remote-acme={REMOTE_BASE_URL}",),
        ),
        resolver=resolver,
    )
    return request, cognition_input, policy, policy.bind(profile), resolver


def _remote_credential(cognition_input: CityCognitionInputV1) -> ResolvedCredential:
    profile = cognition_input.provider_profile
    assert profile is not None
    return ResolvedCredential(
        source="environment",
        secret=SecretBuffer(FAKE_CREDENTIAL),
        origin_approval_id=profile.origin_approval_id,
        credential_slot_id=profile.credential_slot_id,
    )


def _local_case():
    profile = _profile(
        profile_id="local-ollama",
        display_label="Local Ollama",
        mode="local",
        base_url="http://127.0.0.1:11434/v1",
        origin_approval_id="local-ollama",
    )
    snapshot = CityCognitionProviderProfileSnapshotV1.model_validate(
        profile.model_dump(mode="json")
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
    policy = CityEndpointPolicy(build_provider_runtime_policy(), resolver=_Resolver())
    return request, cognition_input, profile, policy, policy.bind(profile)


def _result(request_id: str, **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "request_id": request_id,
        "interpretation": "The fictional commuter noticed a relevant offer.",
        "decision_factors": ["Committed rule-owned response", "Declared interests"],
        "safety_flags": [],
        "disclosure": CITY_COGNITION_DISCLOSURE,
    }
    result.update(updates)
    return result


def _completion(content: object, **updates: object) -> bytes:
    envelope: dict[str, object] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "example-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
    }
    envelope.update(updates)
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


def _http_response(
    body: bytes,
    *,
    status: int = 200,
    reason: str = "OK",
    headers: Iterable[tuple[str, str]] = (),
    version: str = "1.1",
) -> bytes:
    complete_headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
        ("Connection", "close"),
        *headers,
    ]
    head = "".join(f"{name}: {value}\r\n" for name, value in complete_headers)
    return f"HTTP/{version} {status} {reason}\r\n{head}\r\n".encode("ascii") + body


def _chunked_http_response_at_size(body: bytes, *, wire_size: int) -> bytes:
    """Build valid chunked HTTP whose complete cleartext representation has one size."""
    head = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n\r\n"
    )
    terminal = b"0\r\n\r\n"
    remaining = wire_size - len(head) - len(terminal) - (6 * len(body))
    if remaining < 0 or not body:
        raise ValueError("wire size cannot hold the chunked response")

    padding_sizes: list[int] = []
    while remaining:
        if remaining == 1:
            raise ValueError("one byte cannot form a valid chunk extension")
        padding = min(remaining, 4_096)
        if remaining - padding == 1:
            padding -= 1
        padding_sizes.append(padding)
        remaining -= padding
    if len(padding_sizes) > len(body):
        raise ValueError("response body has too few chunks for the requested wire size")

    padded_chunks: list[bytes] = []
    for index, item in enumerate(body):
        padding = padding_sizes[index] if index < len(padding_sizes) else 0
        extension = b"" if padding == 0 else b";" + b"a" * (padding - 1)
        padded_chunks.append(b"1" + extension + b"\r\n" + bytes((item,)) + b"\r\n")
    response = head + b"".join(padded_chunks) + terminal
    assert len(response) == wire_size
    return response


class _ScriptedStream(httpcore.AsyncNetworkStream):
    def __init__(self, response: bytes, *, peer: str = REMOTE_ADDRESS) -> None:
        self._response = bytearray(response)
        self.peer = peer
        self.writes: list[bytes] = []
        self.tls_server_names: list[str | None] = []
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        if not self._response:
            return b""
        chunk = bytes(self._response[:max_bytes])
        del self._response[:max_bytes]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(bytes(buffer))

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del timeout
        assert ssl_context.check_hostname is True
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        self.tls_server_names.append(server_hostname)
        return self

    def get_extra_info(self, info: str) -> Any:
        if info == "server_addr":
            return (self.peer, 443)
        if info == "is_readable":
            return False
        return None


class _ScriptedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, stream: _ScriptedStream) -> None:
        self.stream = stream
        self.connects: list[tuple[str, int]] = []
        self.unix_calls = 0

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del timeout, local_address, socket_options
        self.connects.append((host, port))
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        self.unix_calls += 1
        raise AssertionError("city provider transport must not use a Unix socket")

    async def sleep(self, seconds: float) -> None:
        raise AssertionError(f"httpcore retry was attempted after {seconds}")


class _BackendFactory:
    def __init__(self, responses: Iterable[bytes]) -> None:
        self._responses = list(responses)
        self.backends: list[_ScriptedBackend] = []

    def __call__(self) -> httpcore.AsyncNetworkBackend:
        backend = _ScriptedBackend(_ScriptedStream(self._responses.pop(0)))
        self.backends.append(backend)
        return backend


def _provider(response: bytes, *, timeout: int = 30):
    request, cognition_input, policy, endpoint, resolver = _case(timeout=timeout)
    factory = _BackendFactory((response,))
    resolved_credential = _remote_credential(cognition_input)
    provider = CityOpenAICompatibleProvider(
        endpoint_policy=policy,
        endpoint=endpoint,
        cognition_input=cognition_input,
        credential=resolved_credential,
        network_backend_factory=factory,
    )
    return request, cognition_input, provider, resolved_credential.secret, resolver, factory


def _provider_with_factory(
    factory: _BackendFactory,
    *,
    timeout: int = 30,
    resolver: object | None = None,
):
    request, cognition_input, policy, endpoint, resolver = _case(
        timeout=timeout,
        resolver=resolver,
    )
    resolved_credential = _remote_credential(cognition_input)
    provider = CityOpenAICompatibleProvider(
        endpoint_policy=policy,
        endpoint=endpoint,
        cognition_input=cognition_input,
        credential=resolved_credential,
        network_backend_factory=factory,
    )
    return request, cognition_input, provider, resolved_credential.secret, resolver, factory


async def test_call_posts_the_exact_frozen_json_schema_request_to_one_pinned_http11_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, cognition_input, provider, credential, resolver, factory = _provider(
        _http_response(_completion(json.dumps(_result("placeholder"))))
    )
    # Rebuild the scripted response now that the content-addressed request id is known.
    factory._responses[0] = _http_response(
        _completion(json.dumps(_result(request.request_id), separators=(",", ":")))
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")

    attempt = await provider.call(request)

    assert attempt.status == "valid"
    assert attempt.result is not None
    assert attempt.result.request_id == request.request_id
    assert attempt.usage is not None
    assert attempt.usage.prompt_tokens == 20
    assert attempt.usage.completion_tokens == 12
    assert resolver.calls == [("api.example.invalid", 443)]
    assert len(factory.backends) == 1
    backend = factory.backends[0]
    assert backend.connects == [(REMOTE_ADDRESS, 443)]
    assert backend.unix_calls == 0
    assert backend.stream.tls_server_names == ["api.example.invalid"]
    wire = b"".join(backend.stream.writes)
    request_head, request_body = wire.split(b"\r\n\r\n", 1)
    assert request_head.startswith(b"POST /openai/v1/chat/completions HTTP/1.1\r\n")
    assert b"Host: api.example.invalid\r\n" in request_head
    assert b"Accept-Encoding: identity\r\n" in request_head
    assert b"Authorization: Bearer " + FAKE_CREDENTIAL.encode("ascii") in request_head
    payload = json.loads(request_body)
    settings = cognition_input.effective_settings
    assert settings is not None
    assert payload["model"] == "example-model"
    assert payload["temperature"] == settings.temperature
    assert payload["top_p"] == settings.top_p
    assert payload["seed"] == settings.seed
    assert payload["max_tokens"] == settings.max_output_tokens
    assert payload["stream"] is False
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in payload
    assert "functions" not in payload
    assert credential.cleared is False

    await provider.aclose()
    assert provider.is_closed is True


async def test_local_transport_never_attaches_a_supplied_remote_credential() -> None:
    request, cognition_input, profile, policy, endpoint = _local_case()
    response = _http_response(
        _completion(json.dumps(_result(request.request_id), separators=(",", ":")))
    )

    class LocalBackendFactory(_BackendFactory):
        def __init__(self) -> None:
            super().__init__(())

        def __call__(self) -> httpcore.AsyncNetworkBackend:
            backend = _ScriptedBackend(_ScriptedStream(response, peer="127.0.0.1"))
            self.backends.append(backend)
            return backend

    factory = LocalBackendFactory()
    supplied_remote_secret = SecretBuffer(FAKE_CREDENTIAL)
    resolved_credential = ResolvedCredential(
        source="local-dummy",
        secret=supplied_remote_secret,
        origin_approval_id=profile.origin_approval_id,
        credential_slot_id=profile.credential_slot_id,
    )
    provider = CityOpenAICompatibleProvider(
        endpoint_policy=policy,
        endpoint=endpoint,
        cognition_input=cognition_input,
        credential=resolved_credential,
        network_backend_factory=factory,
    )

    attempt = await provider.call(request)

    assert attempt.status == "valid"
    wire = b"".join(factory.backends[0].stream.writes)
    request_head = wire.split(b"\r\n\r\n", 1)[0]
    authorization = next(
        line for line in request_head.split(b"\r\n") if line.startswith(b"Authorization: ")
    )
    assert authorization.startswith(b"Authorization: Bearer ")
    assert authorization != b"Authorization: Bearer "
    assert FAKE_CREDENTIAL.encode("ascii") not in authorization
    assert supplied_remote_secret.cleared is False
    await provider.aclose()


def test_local_constructor_refuses_a_remote_credential_source() -> None:
    _request, cognition_input, profile, policy, endpoint = _local_case()
    credential = ResolvedCredential(
        source="environment",
        secret=SecretBuffer(FAKE_CREDENTIAL),
        origin_approval_id=profile.origin_approval_id,
        credential_slot_id=profile.credential_slot_id,
    )

    with pytest.raises(CityEndpointBindingError) as caught:
        CityOpenAICompatibleProvider(
            endpoint_policy=policy,
            endpoint=endpoint,
            cognition_input=cognition_input,
            credential=credential,
            network_backend_factory=_BackendFactory(()),
        )

    assert FAKE_CREDENTIAL not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert credential.secret.cleared is False
    credential.clear()


async def test_repair_call_uses_only_the_screened_repair_context() -> None:
    request, _input_document, provider, _credential, _resolver, factory = _provider(
        _http_response(_completion("placeholder"))
    )
    factory._responses[0] = _http_response(
        _completion(json.dumps(_result(request.request_id), separators=(",", ":")))
    )
    context = CityProviderRepairContextV1(
        schema_version=1,
        reason_code="invalid-schema",
        safe_excerpt="The answer did not match the required result schema.",
    )

    attempt = await provider.repair_call(request, context=context)

    assert attempt.status == "valid"
    wire = b"".join(factory.backends[0].stream.writes)
    payload = json.loads(wire.split(b"\r\n\r\n", 1)[1])
    assert payload["messages"][-1]["role"] == "user"
    assert "invalid-schema" in payload["messages"][-1]["content"]
    assert context.safe_excerpt not in json.dumps(payload)
    await provider.aclose()


async def test_every_attempt_reresolves_and_opens_a_fresh_pinned_socket() -> None:
    request, _input_document, _policy, _endpoint, _resolver = _case()
    response = _http_response(
        _completion(json.dumps(_result(request.request_id), separators=(",", ":")))
    )
    factory = _BackendFactory((response, response))
    request, _input_document, provider, _credential, resolver, factory = _provider_with_factory(
        factory
    )

    first = await provider.call(request)
    second = await provider.call(request)

    assert first == second
    assert resolver.calls == [
        ("api.example.invalid", 443),
        ("api.example.invalid", 443),
    ]
    assert len(factory.backends) == 2
    assert factory.backends[0] is not factory.backends[1]
    assert all(backend.connects == [(REMOTE_ADDRESS, 443)] for backend in factory.backends)
    await provider.aclose()


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("{", "invalid-json"),
        ("[]", "invalid-schema"),
        (
            json.dumps(_result("cog-" + "0" * 64), separators=(",", ":")),
            "request-id-mismatch",
        ),
        (
            json.dumps(
                _result("cog-" + "0" * 64, interpretation=17),
                separators=(",", ":"),
            ),
            "invalid-schema",
        ),
        (
            json.dumps(
                _result("cog-" + "0" * 64, provider_extension="unsupported"),
                separators=(",", ":"),
            ),
            "invalid-schema",
        ),
        (
            '{"schema_version":1,"request_id":"cog-'
            + "0" * 64
            + '","interpretation":NaN,"decision_factors":[],"safety_flags":[],'
            '"disclosure":"synthetic-narrative-not-observed-behavior"}',
            "invalid-json",
        ),
    ],
)
async def test_invalid_provider_results_become_bounded_typed_attempts(
    content: str,
    reason: str,
) -> None:
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(_completion(content))
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.result is None
    assert attempt.usage is None
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == reason
    assert len(attempt.repair_context.safe_excerpt) <= 240
    assert content not in repr(attempt)
    await provider.aclose()


@pytest.mark.parametrize(
    "unsafe_content",
    [
        'api_key="0000abcdef1234567890"',
        "fictional.person@example.invalid",
        "+1 415 555 0134",
    ],
)
async def test_sensitive_provider_output_is_never_echoed_into_a_repair_context(
    unsafe_content: str,
) -> None:
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(_completion(unsafe_content))
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "unsafe-content"
    serialized = attempt.model_dump_json()
    assert unsafe_content not in serialized
    assert "0000abcdef1234567890" not in serialized
    assert "example.invalid" not in serialized
    assert "415 555" not in serialized
    await provider.aclose()


@pytest.mark.parametrize(
    "updates",
    [
        # Real OpenAI and Ollama envelopes carry a ten-digit Unix "created" time.
        {"created": 1792391034},
        {"created": 1792391034, "system_fingerprint": "fp_ollama"},
        {
            "created": 1792391034,
            "usage": {"prompt_tokens": 571, "completion_tokens": 211, "total_tokens": 782},
        },
    ],
)
async def test_realistic_unix_created_timestamp_is_not_screened_as_persona_text(
    updates: dict[str, object],
) -> None:
    request, _input_document, provider, _credential, _resolver, factory = _provider(
        _http_response(_completion("placeholder"))
    )
    factory._responses[0] = _http_response(
        _completion(json.dumps(_result(request.request_id)), **updates)
    )

    attempt = await provider.call(request)

    assert attempt.status == "valid"
    assert attempt.result is not None
    assert attempt.result.request_id == request.request_id
    await provider.aclose()


@pytest.mark.parametrize(
    "updates",
    [
        {"created": 1792391034, "system_fingerprint": "+1 415 555 0134"},
        {"created": 1792391034, "id": "fictional.person@example.invalid"},
        {"created": 1792391034, "model": "4155550134"},
    ],
)
async def test_decoded_envelope_text_keeps_the_sensitive_persona_screen(
    updates: dict[str, object],
) -> None:
    request, _input_document, provider, _credential, _resolver, factory = _provider(
        _http_response(_completion("placeholder"))
    )
    factory._responses[0] = _http_response(
        _completion(json.dumps(_result(request.request_id)), **updates)
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "unsafe-content"
    serialized = attempt.model_dump_json()
    assert "415 555" not in serialized and "4155550134" not in serialized
    assert "example.invalid" not in serialized
    await provider.aclose()


async def test_model_authored_result_text_keeps_the_sensitive_persona_screen() -> None:
    request, _input_document, provider, _credential, _resolver, factory = _provider(
        _http_response(_completion("placeholder"))
    )
    factory._responses[0] = _http_response(
        _completion(
            json.dumps(
                _result(
                    request.request_id,
                    interpretation="The fictional commuter can be reached at +1 415 555 0134.",
                )
            ),
            created=1792391034,
        )
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "unsafe-content"
    assert "415 555" not in attempt.model_dump_json()
    await provider.aclose()


async def test_exact_configured_credential_is_detected_even_when_json_escaped() -> None:
    escaped = "".join(f"\\u{ord(character):04x}" for character in FAKE_CREDENTIAL)
    body = (
        '{"choices":[{"message":{"role":"assistant","content":"'
        + escaped
        + '"},"finish_reason":"stop"}]}'
    ).encode("ascii")
    _request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(body)
    )

    attempt = await provider.call(_request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "unsafe-content"
    assert FAKE_CREDENTIAL not in repr(attempt)
    await provider.aclose()


@pytest.mark.parametrize(
    "response",
    [
        _http_response(b"data: {}\n\n", headers=(("Content-Type", "text/event-stream"),)),
        _http_response(b"compressed", headers=(("Content-Encoding", "gzip"),)),
        _http_response(
            _completion([{"type": "text", "text": "not accepted"}]),
        ),
        _http_response(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [{"id": "call-1"}],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ).encode("utf-8")
        ),
        _http_response(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "{}",
                                "function_call": {"name": "unsupported"},
                            },
                            "finish_reason": "function_call",
                        }
                    ]
                }
            ).encode("utf-8")
        ),
        _http_response(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "{}",
                                "audio": {"data": "unsupported"},
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode("utf-8")
        ),
        _http_response(_completion("{}"), version="1.0"),
    ],
)
async def test_streaming_compression_content_arrays_tools_and_non_http11_are_refused(
    response: bytes,
) -> None:
    request, _input_document, provider, _credential, _resolver, _factory = _provider(response)

    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)

    assert caught.value.reason_code == "protocol-incompatible"
    assert caught.value.retryable is False
    assert caught.value.__cause__ is None
    await provider.aclose()


@pytest.mark.parametrize("header_name", ["Content-Type", "Content-Length"])
async def test_security_relevant_duplicate_response_headers_are_refused(
    header_name: str,
) -> None:
    body = _completion("{}")
    duplicate_value = "application/json" if header_name == "Content-Type" else str(len(body) + 1)
    response = _http_response(body, headers=((header_name, duplicate_value),))
    request, _input_document, provider, _credential, _resolver, _factory = _provider(response)

    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)

    assert caught.value.reason_code == "protocol-incompatible"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()


async def test_redirect_is_refused_without_opening_a_second_socket() -> None:
    response = _http_response(
        b"{}",
        status=302,
        reason="Found",
        headers=(("Location", "https://attacker.invalid/v1/chat/completions"),),
    )
    request, _input_document, provider, _credential, resolver, factory = _provider(response)

    with pytest.raises(CityProviderProtocolError):
        await provider.call(request)

    assert len(factory.backends) == 1
    assert resolver.calls == [("api.example.invalid", 443)]
    await provider.aclose()


async def test_provider_native_envelope_extensions_are_not_silently_accepted() -> None:
    request, _input_document, _policy, _endpoint, _resolver = _case()
    body = _completion(
        json.dumps(_result(request.request_id), separators=(",", ":")),
        vendor_probability=0.75,
    )
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(body)
    )

    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)

    assert caught.value.reason_code == "protocol-incompatible"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()


@pytest.mark.parametrize(
    ("field", "hostile_value"),
    [
        ("id", "chatcmpl-\ud800"),
        ("model", "example\u202e-model"),
        ("system_fingerprint", "fingerprint\x1f"),
        ("service_tier", "default\u0085"),
    ],
)
async def test_invalid_unicode_or_display_controls_in_envelope_text_are_refused(
    field: str,
    hostile_value: str,
) -> None:
    request, _input_document, _policy, _endpoint, _resolver = _case()
    body = _completion(
        json.dumps(_result(request.request_id), separators=(",", ":")),
        **{field: hostile_value},
    )
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(body)
    )

    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)

    assert hostile_value not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()


async def test_wire_and_decoded_response_limits_are_enforced_before_json_parsing() -> None:
    body = b"{" + b" " * 1_048_576
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(body)
    )

    with pytest.raises(CityProviderResponseTooLargeError) as caught:
        await provider.call(request)

    assert caught.value.reason_code == "response-too-large"
    assert caught.value.__cause__ is None
    assert _factory.backends[0].stream.closed is True
    await provider.aclose()


async def test_exact_wire_response_limit_is_accepted_across_tls_stream_handoff() -> None:
    request, _input_document, provider, _credential, _resolver, factory = _provider(b"")
    body = _completion(json.dumps(_result(request.request_id), separators=(",", ":")))
    response = _chunked_http_response_at_size(
        body,
        wire_size=MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES,
    )
    factory._responses[0] = response

    attempt = await provider.call(request)

    assert attempt.status == "valid"
    assert attempt.result is not None
    assert attempt.result.request_id == request.request_id
    assert factory.backends[0].stream.tls_server_names == ["api.example.invalid"]
    await provider.aclose()


async def test_chunked_transfer_framing_cannot_bypass_the_wire_byte_limit() -> None:
    head = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n\r\n"
    )
    one_byte_with_large_extension = b"1;" + b"a" * 1_024 + b"\r\nx\r\n"
    response = head + one_byte_with_large_extension * 1_050 + b"0\r\n\r\n"
    assert len(response) > MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES
    request, _input_document, provider, _credential, _resolver, factory = _provider(response)

    with pytest.raises(CityProviderResponseTooLargeError) as caught:
        await provider.call(request)

    assert caught.value.reason_code == "response-too-large"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert factory.backends[0].stream.closed is True
    await provider.aclose()


@pytest.mark.parametrize(
    ("status", "error_type", "retryable"),
    [
        (401, CityProviderAuthenticationError, False),
        (403, CityProviderAuthenticationError, False),
        (429, CityProviderRateLimitError, True),
        (500, CityProviderServerError, True),
        (502, CityProviderServerError, True),
        (503, CityProviderServerError, True),
        (504, CityProviderServerError, True),
        (400, CityProviderProtocolError, False),
        (408, CityProviderProtocolError, False),
        (501, CityProviderProtocolError, False),
    ],
)
async def test_http_statuses_have_exact_retry_classification_without_body_echo(
    status: int,
    error_type: type[Exception],
    retryable: bool,
) -> None:
    secret = "0000abcdef1234567890"
    response = _http_response(
        f'{{"api_key":"{secret}"}}'.encode(),
        status=status,
        reason="Refused",
    )
    request, _input_document, provider, _credential, _resolver, _factory = _provider(response)

    with pytest.raises(error_type) as caught:
        await provider.call(request)

    assert isinstance(caught.value, CityProviderCallError)
    assert caught.value.retryable is retryable
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None
    await provider.aclose()


class _HangingStream(_ScriptedStream):
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        await __import__("asyncio").sleep(60)
        return b""


class _CancellableStream(_ScriptedStream):
    def __init__(self) -> None:
        super().__init__(b"")
        self.read_started = asyncio.Event()

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        self.read_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled read resumed")


class _HangingFactory(_BackendFactory):
    def __init__(self) -> None:
        super().__init__(())

    def __call__(self) -> httpcore.AsyncNetworkBackend:
        backend = _ScriptedBackend(_HangingStream(b""))
        self.backends.append(backend)
        return backend


async def test_whole_attempt_timeout_uses_the_frozen_effective_setting() -> None:
    factory = _HangingFactory()
    request, _input_document, provider, _credential, resolver, _factory = _provider_with_factory(
        factory, timeout=1
    )
    started = time.monotonic()

    with pytest.raises(CityProviderTimeoutError) as caught:
        await provider.call(request)

    elapsed = time.monotonic() - started
    assert 0.8 <= elapsed < 2.5
    assert caught.value.retryable is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert resolver.calls == [("api.example.invalid", 443)]
    assert factory.backends[0].stream.closed is True
    await provider.aclose()


async def test_cancellation_interrupts_a_slow_stream_and_closes_the_socket() -> None:
    stream = _CancellableStream()

    class Factory(_BackendFactory):
        def __init__(self) -> None:
            super().__init__(())

        def __call__(self) -> httpcore.AsyncNetworkBackend:
            backend = _ScriptedBackend(stream)
            self.backends.append(backend)
            return backend

    factory = Factory()
    request, _input_document, provider, _credential, _resolver, _factory = _provider_with_factory(
        factory
    )
    task = asyncio.create_task(provider.call(request))
    await asyncio.wait_for(stream.read_started.wait(), timeout=1)
    writes_before_cancel = tuple(stream.writes)

    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert stream.closed is True
    assert tuple(stream.writes) == writes_before_cancel
    assert FAKE_CREDENTIAL not in _adlife_traceback_local_rendering(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()


async def test_provider_repr_close_and_exceptions_never_reveal_the_credential() -> None:
    request, _input_document, provider, original, _resolver, _factory = _provider(
        _http_response(b"{}", status=401, reason="Unauthorized")
    )

    assert FAKE_CREDENTIAL not in repr(provider)
    with pytest.raises(CityProviderAuthenticationError) as caught:
        await provider.call(request)
    assert FAKE_CREDENTIAL not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()
    assert original.cleared is False
    with pytest.raises(CityProviderUnavailableError) as closed:
        await provider.call(request)
    assert FAKE_CREDENTIAL not in repr(closed.value)


class _RefusingResolver:
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        del hostname, port
        return ("127.0.0.1",)


async def test_endpoint_resolution_refusal_is_mapped_to_the_service_reason_contract() -> None:
    factory = _BackendFactory(())
    request, _input_document, provider, _credential, _resolver, _factory = _provider_with_factory(
        factory, resolver=_RefusingResolver()
    )

    with pytest.raises(CityProviderUnavailableError) as caught:
        await provider.call(request)

    assert caught.value.reason_code == "endpoint-resolution-refused"
    assert caught.value.retryable is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert factory.backends == []
    await provider.aclose()


class _WrongPeerFactory(_BackendFactory):
    def __init__(self, response: bytes) -> None:
        super().__init__(())
        self._response = response

    def __call__(self) -> httpcore.AsyncNetworkBackend:
        backend = _ScriptedBackend(_ScriptedStream(self._response, peer="93.184.216.35"))
        self.backends.append(backend)
        return backend


async def test_peer_mismatch_is_refused_before_authorization_is_written() -> None:
    request, _input_document, _policy, _endpoint, _resolver = _case()
    response = _http_response(
        _completion(json.dumps(_result(request.request_id), separators=(",", ":")))
    )
    factory = _WrongPeerFactory(response)
    request, _input_document, provider, _credential, _resolver, _factory = _provider_with_factory(
        factory
    )

    with pytest.raises(CityProviderUnavailableError) as caught:
        await provider.call(request)

    assert caught.value.reason_code == "endpoint-resolution-refused"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert factory.backends[0].stream.writes == []
    await provider.aclose()


@pytest.mark.parametrize("duplicate_location", ["envelope", "result"])
async def test_duplicate_json_members_are_rejected_before_last_value_can_win(
    duplicate_location: str,
) -> None:
    request, _input_document, _policy, _endpoint, _resolver = _case()
    valid_result = json.dumps(_result(request.request_id), separators=(",", ":"))
    if duplicate_location == "result":
        content = valid_result[:-1] + ',"interpretation":"replacement"}'
        body = _completion(content)
    else:
        valid_envelope = _completion(valid_result).decode("utf-8")
        body = (valid_envelope[:-1] + ',"choices":[]}').encode("utf-8")
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(body)
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "invalid-json"
    await provider.aclose()


@pytest.mark.parametrize("escape_passes", [1, 2])
async def test_nested_unicode_escaped_credential_echo_is_unsafe_not_repairable(
    escape_passes: int,
) -> None:
    request, _input_document, _policy, _endpoint, _resolver = _case()
    encoded_secret = "".join(f"\\u{ord(character):04x}" for character in FAKE_CREDENTIAL)
    if escape_passes == 2:
        encoded_secret = encoded_secret.replace("\\", "\\\\")
    content = (
        '{"schema_version":1,"request_id":"'
        + request.request_id
        + '","interpretation":"'
        + encoded_secret
        + '","decision_factors":["Committed evidence"],"safety_flags":[],'
        '"disclosure":"synthetic-narrative-not-observed-behavior"}'
    )
    request, _input_document, provider, _credential, _resolver, _factory = _provider(
        _http_response(_completion(content))
    )

    attempt = await provider.call(request)

    assert attempt.status == "invalid-result"
    assert attempt.repair_context is not None
    assert attempt.repair_context.reason_code == "unsafe-content"
    assert FAKE_CREDENTIAL not in attempt.model_dump_json()
    await provider.aclose()


class _SlowStream(_ScriptedStream):
    """A provider that answers only after a model-load delay."""

    def __init__(self, response: bytes, *, delay: float) -> None:
        super().__init__(response)
        self._delay: float | None = delay

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._delay is not None:
            delay, self._delay = self._delay, None
            await asyncio.sleep(delay)
        return await super().read(max_bytes, timeout)


class _SlowBackendFactory(_BackendFactory):
    def __init__(self, responses: Iterable[bytes], *, delay: float) -> None:
        super().__init__(responses)
        self._delay = delay

    def __call__(self) -> httpcore.AsyncNetworkBackend:
        backend = _ScriptedBackend(_SlowStream(self._responses.pop(0), delay=self._delay))
        self.backends.append(backend)
        return backend


@pytest.mark.parametrize("override", [None, 3])
async def test_check_timeout_override_extends_only_this_transport_deadline(
    override: int | None,
) -> None:
    request, cognition_input, policy, endpoint, _resolver = _case(timeout=1)
    factory = _SlowBackendFactory(
        (_http_response(_completion(json.dumps(_result(request.request_id)))),), delay=1.5
    )
    credential = _remote_credential(cognition_input)
    provider = CityOpenAICompatibleProvider(
        endpoint_policy=policy,
        endpoint=endpoint,
        cognition_input=cognition_input,
        credential=credential,
        network_backend_factory=factory,
        check_attempt_timeout_seconds=override,
    )
    try:
        if override is None:
            # Runs keep the frozen one-second attempt deadline of this profile.
            with pytest.raises(CityProviderTimeoutError):
                await provider.call(request)
        else:
            attempt = await provider.call(request)
            assert attempt.status == "valid"
        settings = cognition_input.effective_settings
        assert settings is not None and settings.attempt_timeout_seconds == 1
    finally:
        await provider.aclose()
        credential.secret.clear()


@pytest.mark.parametrize("override", [0, 121, -1, True, 1.5, "30"])
def test_check_timeout_override_refuses_unbounded_or_coerced_values(override: object) -> None:
    _request, cognition_input, policy, endpoint, _resolver = _case()
    credential = _remote_credential(cognition_input)
    with pytest.raises(ValueError, match="check timeout"):
        CityOpenAICompatibleProvider(
            endpoint_policy=policy,
            endpoint=endpoint,
            cognition_input=cognition_input,
            credential=credential,
            check_attempt_timeout_seconds=override,  # type: ignore[arg-type]
        )
    credential.secret.clear()


class _FailingBackend(_ScriptedBackend):
    def __init__(self) -> None:
        pass

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del host, port, timeout, local_address, socket_options
        raise httpcore.ConnectTimeout(
            f"Authorization: Bearer {FAKE_CREDENTIAL} C:/private/provider.sock"
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError

    async def sleep(self, seconds: float) -> None:
        del seconds


class _FailingFactory(_BackendFactory):
    def __init__(self) -> None:
        super().__init__(())

    def __call__(self) -> httpcore.AsyncNetworkBackend:
        backend = _FailingBackend()
        self.backends.append(backend)
        return backend


class _UnexpectedWriteStream(_ScriptedStream):
    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del buffer, timeout
        raise RuntimeError(f"unexpected dependency diagnostic: {FAKE_CREDENTIAL}")


class _UnexpectedWriteFactory(_BackendFactory):
    def __init__(self) -> None:
        super().__init__(())

    def __call__(self) -> httpcore.AsyncNetworkBackend:
        backend = _ScriptedBackend(_UnexpectedWriteStream(b""))
        self.backends.append(backend)
        return backend


async def test_unexpected_post_reveal_failure_is_a_value_free_programming_error() -> None:
    factory = _UnexpectedWriteFactory()
    request, _input_document, provider, _credential, _resolver, _factory = _provider_with_factory(
        factory
    )

    with pytest.raises(CityProviderTransportError) as caught:
        await provider.call(request)

    assert FAKE_CREDENTIAL not in str(caught.value)
    assert FAKE_CREDENTIAL not in _adlife_traceback_local_rendering(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert factory.backends[0].stream.closed is True
    await provider.aclose()


async def test_transport_exception_value_chain_cannot_retain_request_secrets() -> None:
    factory = _FailingFactory()
    request, _input_document, provider, _credential, _resolver, _factory = _provider_with_factory(
        factory
    )

    with pytest.raises(CityProviderTimeoutError) as caught:
        await provider.call(request)

    assert FAKE_CREDENTIAL not in repr(caught.value)
    assert "private" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()


async def test_malformed_response_header_exception_context_is_not_retained() -> None:
    body = _completion("{}")
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\xff\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    request, _input_document, provider, _credential, _resolver, _factory = _provider(response)

    with pytest.raises(CityProviderProtocolError) as caught:
        await provider.call(request)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    await provider.aclose()


async def test_request_and_frozen_input_must_be_exactly_cross_bound_before_network() -> None:
    request, _input_document, provider, _credential, _resolver, factory = _provider(
        _http_response(b"{}")
    )
    tampered = request.model_construct(**{**request.__dict__, "run_input_sha256": "f" * 64})

    with pytest.raises(ValueError, match=r"failed validation|cross-bound") as caught:
        await provider.call(tampered)

    assert request.request_id not in str(caught.value)
    assert factory.backends == []
    await provider.aclose()


def test_constructor_refuses_a_different_endpoint_profile_revision() -> None:
    _request, cognition_input, policy, endpoint, _resolver = _case()
    mismatched = replace(endpoint, profile_revision_sha256="f" * 64)
    credential = _remote_credential(cognition_input)

    with pytest.raises(CityEndpointBindingError) as caught:
        CityOpenAICompatibleProvider(
            endpoint_policy=policy,
            endpoint=mismatched,
            cognition_input=cognition_input,
            credential=credential,
            network_backend_factory=_BackendFactory(()),
        )

    assert cognition_input.run_input_sha256 not in str(caught.value)
    assert FAKE_CREDENTIAL not in repr(caught.value)
    assert credential.secret.cleared is False
    credential.clear()


@pytest.mark.parametrize(
    "updates",
    [
        {"origin_approval_id": "remote-alt"},
        {"credential_slot_id": "f" * 64},
        {"source": "local-dummy"},
    ],
)
def test_constructor_refuses_cross_origin_slot_or_source_credentials(
    updates: dict[str, object],
) -> None:
    _request, cognition_input, policy, endpoint, _resolver = _case()
    profile = cognition_input.provider_profile
    assert profile is not None
    credential = ResolvedCredential(
        source="environment",
        secret=SecretBuffer(FAKE_CREDENTIAL),
        origin_approval_id=profile.origin_approval_id,
        credential_slot_id=profile.credential_slot_id,
    )
    forged = replace(credential, **updates)

    with pytest.raises(CityEndpointBindingError) as caught:
        CityOpenAICompatibleProvider(
            endpoint_policy=policy,
            endpoint=endpoint,
            cognition_input=cognition_input,
            credential=forged,
            network_backend_factory=_BackendFactory(()),
        )

    assert FAKE_CREDENTIAL not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert credential.secret.cleared is False
    credential.clear()


def test_constructor_requires_a_live_exact_resolved_credential() -> None:
    _request, cognition_input, policy, endpoint, _resolver = _case()
    bare_secret = SecretBuffer(FAKE_CREDENTIAL)

    with pytest.raises(CityEndpointBindingError):
        CityOpenAICompatibleProvider(
            endpoint_policy=policy,
            endpoint=endpoint,
            cognition_input=cognition_input,
            credential=cast("ResolvedCredential", bare_secret),
            network_backend_factory=_BackendFactory(()),
        )
    assert bare_secret.cleared is False

    cleared = _remote_credential(cognition_input)
    cleared.clear()
    with pytest.raises(CityEndpointBindingError):
        CityOpenAICompatibleProvider(
            endpoint_policy=policy,
            endpoint=endpoint,
            cognition_input=cognition_input,
            credential=cleared,
            network_backend_factory=_BackendFactory(()),
        )

    bare_secret.clear()


def test_constructor_refuses_forged_credential_metadata_without_formatting_it() -> None:
    _request, cognition_input, policy, endpoint, _resolver = _case()
    profile = cognition_input.provider_profile
    assert profile is not None
    hostile = "sk-do-not-format-forged-credential-metadata"

    class NoisyText(str):
        def __eq__(self, other: object) -> bool:
            del other
            warnings.warn(hostile, UserWarning, stacklevel=2)
            return True

    credential = ResolvedCredential(
        source="environment",
        secret=SecretBuffer(FAKE_CREDENTIAL),
        origin_approval_id=NoisyText(profile.origin_approval_id),
        credential_slot_id=profile.credential_slot_id,
    )

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(CityEndpointBindingError) as caught:
            CityOpenAICompatibleProvider(
                endpoint_policy=policy,
                endpoint=endpoint,
                cognition_input=cognition_input,
                credential=credential,
                network_backend_factory=_BackendFactory(()),
            )

    assert caught_warnings == []
    assert hostile not in repr(caught.value)
    assert FAKE_CREDENTIAL not in repr(caught.value)
    assert credential.secret.cleared is False
    credential.clear()


def test_constructor_does_not_emit_hostile_unvalidated_values_as_pydantic_warnings() -> None:
    _request, cognition_input, policy, endpoint, _resolver = _case()
    credential = _remote_credential(cognition_input)
    hostile_value = "sk-warning-channel-secret-000000000000"
    bypassed_input = CityCognitionInputV1.model_construct(
        **{**cognition_input.__dict__, "provider_profile": hostile_value}
    )

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(ValueError) as caught:
            CityOpenAICompatibleProvider(
                endpoint_policy=policy,
                endpoint=endpoint,
                cognition_input=bypassed_input,
                credential=credential,
                network_backend_factory=_BackendFactory(()),
            )

    assert caught_warnings == []
    assert hostile_value not in repr(caught.value)
    assert credential.secret.cleared is False
    credential.clear()
