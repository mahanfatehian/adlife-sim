from __future__ import annotations

import asyncio
import importlib
import logging
import subprocess
import sys
from textwrap import dedent

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


def test_import_does_not_mutate_process_logger_configuration():
    program = dedent("""
        import importlib
        import logging
        import httpcore

        names = ("httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2",
                 "httpcore.proxy", "httpcore.socks")
        for name in names:
            logger = logging.getLogger(name)
            logger.addFilter(logging.Filter())
            logger.addHandler(logging.NullHandler())
            logger.setLevel(logging.DEBUG)
        loggers = [logging.getLogger()] + [
            value for value in logging.root.manager.loggerDict.values()
            if isinstance(value, logging.Logger)
        ]
        def state():
            return {logger.name: (logger.level, logger.disabled, tuple(logger.filters),
                                  tuple(logger.handlers), logger.propagate) for logger in loggers}
        before = state()
        importlib.import_module("adlife.adapters.cognition.city_provider_transport")
        after = state()
        changed = [name for name in before if before[name] != after[name]]
        assert not changed, f"import mutated process-global loggers: {changed}"
    """)
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("phase", ["response", "write", "read", "close", "malformed"])
async def test_httpcore_logs_keep_events_without_provider_values_or_global_mutation(phase, caplog):
    from tests.unit.cognition.test_city_openai_compatible import _ScriptedStream

    caplog.set_level(logging.DEBUG, logger="httpcore")
    module = _module()
    names = (
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    )
    loggers = [logging.getLogger(name) for name in names]
    before = [
        (logger.level, logger.disabled, tuple(logger.filters), tuple(logger.handlers))
        for logger in loggers
    ]
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n"
        + f"X-Diagnostic: {FAKE_CREDENTIAL}\r\n\r\n".encode()
        + b"{}"
    )
    if phase == "malformed":
        response = f"HTTP/1.1 200 OK\r\n{FAKE_CREDENTIAL}\r\n\r\n".encode()

    class FailingStream(_ScriptedStream):
        async def write(self, buffer, timeout=None):
            if phase == "write":
                raise RuntimeError(f"Authorization: Bearer {FAKE_CREDENTIAL}")
            await super().write(buffer, timeout=timeout)

        async def read(self, max_bytes, timeout=None):
            if phase == "read":
                import httpcore

                raise httpcore.ReadTimeout(f"Authorization: Bearer {FAKE_CREDENTIAL}")
            return await super().read(max_bytes, timeout=timeout)

        async def aclose(self):
            await super().aclose()
            if phase == "close":
                raise RuntimeError(f"Authorization: Bearer {FAKE_CREDENTIAL}")

    class Factory(_BackendFactory):
        def __call__(self):
            backend = _ScriptedBackend(FailingStream(response))
            self.backends.append(backend)
            return backend

    transport, credential, _resolver, _factory = _transport(factory=Factory(()))
    try:
        if phase == "response":
            assert await transport.post_json({}) == b"{}"
        else:
            error = (
                module.CityProviderTimeoutError
                if phase == "read"
                else module.CityProviderProtocolError
                if phase == "malformed"
                else module.CityProviderTransportError
            )
            with pytest.raises(error):
                await transport.post_json({})
        assert "send_request_headers.started" in caplog.text
        assert FAKE_CREDENTIAL not in caplog.text
        assert "Authorization: Bearer" not in caplog.text
        assert before == [
            (logger.level, logger.disabled, tuple(logger.filters), tuple(logger.handlers))
            for logger in loggers
        ]
    finally:
        await transport.aclose()
        credential.clear()


@pytest.mark.parametrize(
    "length_headers", [b"Content-Length: 2\r\nContent-Length: 2", b"Content-Length: 2, 2"]
)
@pytest.mark.parametrize("fragment_size", [1, 65536])
async def test_transport_rejects_matching_raw_content_length_duplicates(
    length_headers, fragment_size
):
    from tests.unit.cognition.test_city_openai_compatible import _ScriptedStream

    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + length_headers
        + b"\r\nConnection: close\r\n\r\n{}"
    )

    class FragmentedStream(_ScriptedStream):
        async def read(self, max_bytes, timeout=None):
            return await super().read(min(max_bytes, fragment_size), timeout=timeout)

    class Factory(_BackendFactory):
        def __call__(self):
            backend = _ScriptedBackend(FragmentedStream(response))
            self.backends.append(backend)
            return backend

    transport, credential, _resolver, factory = _transport(factory=Factory(()))
    try:
        with pytest.raises(_module().CityProviderProtocolError) as caught:
            await transport.post_json({})
        assert str(caught.value) == "city cognition provider response was protocol-incompatible"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert factory.backends[0].stream.closed
    finally:
        await transport.aclose()
        credential.clear()


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


@pytest.mark.parametrize(
    ("status", "retry_after", "error", "seconds"),
    [
        (429, "7", "CityProviderRateLimitError", 7),
        (503, "2", "CityProviderServerError", 2),
        (429, "0", "CityProviderRateLimitError", 0),
        (429, "120", "CityProviderRateLimitError", 120),
        (429, None, "CityProviderRateLimitError", None),
        (503, None, "CityProviderServerError", None),
        (429, "soon", "CityProviderRateLimitError", None),
        (429, "-1", "CityProviderRateLimitError", None),
        (429, "1.5", "CityProviderRateLimitError", None),
        (429, "99999999", "CityProviderRateLimitError", None),
        (503, "Wed, 21 Oct 2026 07:28:00 GMT", "CityProviderServerError", None),
    ],
)
async def test_transport_carries_only_a_bounded_whole_second_retry_after_hint(
    status, retry_after, error, seconds
):
    headers = () if retry_after is None else (("Retry-After", retry_after),)
    transport, credential, _resolver, _factory = _transport(
        (_http_response(b"{}", status=status, headers=headers),)
    )
    with pytest.raises(getattr(_module(), error)) as caught:
        await transport.post_json({})
    assert caught.value.retry_after_seconds == seconds
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    await transport.aclose()
    credential.clear()


def test_errors_without_a_hint_default_to_no_retry_after():
    module = _module()
    names = ("CityProviderRateLimitError", "CityProviderServerError", "CityProviderTimeoutError")
    for name in names:
        assert getattr(module, name)().retry_after_seconds is None


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
