from __future__ import annotations

import asyncio
import importlib
import logging

import pytest

from adlife.adapters.cognition.city_credentials import SecretBuffer
from tests.unit.cognition.test_city_openai_compatible import (
    FAKE_CREDENTIAL,
    REMOTE_ADDRESS,
    _adlife_traceback_local_rendering,
    _BackendFactory,
    _CancellableStream,
    _case,
    _FailingFactory,
    _http_response,
    _remote_credential,
    _ScriptedBackend,
    _WrongPeerFactory,
)


def _module():
    # Import inside the test so the missing implementation is a collected failure.
    return importlib.import_module("adlife.adapters.cognition.city_provider_transport")


def _transport(responses=(), *, factory=None, resolver=None, **limits):
    module = _module()
    _request, cognition_input, policy, endpoint, resolver = _case(resolver=resolver)
    credential = _remote_credential(cognition_input)
    factory = _BackendFactory(responses) if factory is None else factory
    transport = module.CityProviderHttpTransport(
        endpoint_policy=policy,
        endpoint=endpoint,
        credential=credential,
        attempt_timeout_seconds=30,
        max_content_length_bytes=limits.get("declared", 1024),
        max_wire_response_bytes=limits.get("wire", 4096),
        max_decoded_response_bytes=limits.get("decoded", 1024),
        network_backend_factory=factory,
    )
    return transport, credential, resolver, factory


async def test_transport_pins_endpoint_and_bounds_one_http11_identity_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    transport, credential, resolver, factory = _transport(
        (_http_response(b"{}"), _http_response(b"[]")), declared=2, decoded=2
    )

    assert await transport.post_json({"request": "one"}) == b"{}"
    assert await transport.post_json({"request": "two"}) == b"[]"
    assert resolver.calls == [("api.example.invalid", 443)] * 2
    assert len(factory.backends) == 2
    for backend in factory.backends:
        assert backend.connects == [(REMOTE_ADDRESS, 443)]
        assert backend.unix_calls == 0
        assert backend.stream.tls_server_names == ["api.example.invalid"]
        wire = b"".join(backend.stream.writes)
        assert wire.startswith(b"POST /openai/v1/chat/completions HTTP/1.1\r\n")
        assert b"Host: api.example.invalid\r\n" in wire
        assert b"Accept-Encoding: identity\r\n" in wire
        assert b"Connection: close\r\n" in wire
        assert backend.stream.closed
    await transport.aclose()
    assert transport.is_closed
    assert not credential.secret.cleared
    credential.clear()


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (_http_response(b"{}", version="2"), "CityProviderProtocolError"),
        (
            _http_response(b"{}", status=302, headers=(("Location", "https://other.invalid"),)),
            "CityProviderProtocolError",
        ),
        (
            _http_response(b"{}", headers=(("Content-Encoding", "gzip"),)),
            "CityProviderProtocolError",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: x\r\n\r\n{}",
            "CityProviderProtocolError",
        ),
        (_http_response(b"{}", headers=(("Content-Length", "3"),)), "CityProviderProtocolError"),
        (_http_response(b"{}", status=401), "CityProviderAuthenticationError"),
        (_http_response(b"{}", status=429), "CityProviderRateLimitError"),
        (_http_response(b"{}", status=503), "CityProviderServerError"),
    ],
)
async def test_transport_refuses_unsafe_protocol_without_following_redirects(response, error):
    transport, credential, _resolver, factory = _transport((response,))
    with pytest.raises(getattr(_module(), error)) as caught:
        await transport.post_json({})
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert len(factory.backends) == 1
    assert factory.backends[0].stream.closed
    await transport.aclose()
    credential.clear()


@pytest.mark.parametrize("limits", [{"declared": 1}, {"wire": 2}, {"decoded": 1}])
async def test_transport_enforces_each_independent_response_limit(limits):
    transport, credential, _resolver, _factory = _transport((_http_response(b"{}"),), **limits)
    with pytest.raises(_module().CityProviderResponseTooLargeError):
        await transport.post_json({})
    await transport.aclose()
    credential.clear()


async def test_transport_refuses_peer_drift_before_revealing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _WrongPeerFactory(_http_response(b"{}"))
    transport, credential, _resolver, _factory = _transport(factory=factory)
    reveals = []
    original = SecretBuffer.reveal_text

    def reveal(secret):
        reveals.append(True)
        return original(secret)

    monkeypatch.setattr(SecretBuffer, "reveal_text", reveal)
    with pytest.raises(_module().CityProviderUnavailableError):
        await transport.post_json({})
    assert reveals == []
    assert factory.backends[0].stream.writes == []
    assert factory.backends[0].stream.closed
    await transport.aclose()
    credential.clear()


async def test_transport_sanitizes_dependency_timeout_and_preserves_logging_state(caplog):
    module = _module()
    caplog.set_level(logging.DEBUG, logger="httpcore")
    logger = logging.getLogger("httpcore.connection")
    before = (logger.level, logger.disabled, tuple(logger.handlers), tuple(logger.filters))
    transport, credential, _resolver, _factory = _transport(factory=_FailingFactory())
    with pytest.raises(module.CityProviderTimeoutError) as caught:
        await transport.post_json({})
    assert str(caught.value) == "city cognition provider attempt timed out"
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert FAKE_CREDENTIAL not in _adlife_traceback_local_rendering(caught.value)
    assert FAKE_CREDENTIAL not in caplog.text
    assert "private/provider.sock" not in caplog.text
    assert before == (logger.level, logger.disabled, tuple(logger.handlers), tuple(logger.filters))
    await transport.aclose()
    credential.clear()


async def test_transport_owns_credential_and_detects_raw_and_escaped_echoes():
    transport, credential, _resolver, _factory = _transport((_http_response(b"{}"),))
    credential.clear()
    assert transport.contains_bound_credential(FAKE_CREDENTIAL.encode())
    assert transport.contains_bound_credential(FAKE_CREDENTIAL)
    escaped = "".join(f"\\u{ord(character):04x}" for character in FAKE_CREDENTIAL)
    assert transport.contains_bound_credential(escaped)
    assert transport.contains_bound_credential(escaped.replace("\\", "\\\\"))
    assert not transport.contains_bound_credential("ordinary provider text")
    assert await transport.post_json({}) == b"{}"
    await transport.aclose()
    await transport.aclose()
    assert not transport.contains_bound_credential("ordinary provider text")
    with pytest.raises(_module().CityProviderUnavailableError):
        await transport.post_json({})


async def test_transport_cancellation_drops_dependency_traceback_and_closes_stream():
    stream = _CancellableStream()

    class Factory(_BackendFactory):
        def __call__(self):
            backend = _ScriptedBackend(stream)
            self.backends.append(backend)
            return backend

    factory = Factory(())
    transport, credential, _resolver, _factory = _transport(factory=factory)
    task = asyncio.create_task(transport.post_json({}))
    await asyncio.wait_for(stream.read_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert factory.backends[0].stream.closed
    await transport.aclose()
    credential.clear()
