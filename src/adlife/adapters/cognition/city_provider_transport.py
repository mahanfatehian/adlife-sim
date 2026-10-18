"""Shared bounded HTTP/1.1 transport for approved city cognition providers."""

from __future__ import annotations

import asyncio
import re
import ssl
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, cast

import httpcore

from adlife.adapters.cognition.city_credentials import (
    CityCredentialStoreError,
    ResolvedCredential,
    SecretBuffer,
    _new_local_dummy_credential,
)
from adlife.adapters.cognition.city_endpoint_policy import (
    BoundProviderEndpoint,
    CityEndpointBindingError,
    CityEndpointPeerMismatch,
    CityEndpointPolicy,
    CityEndpointResolutionError,
    ResolvedProviderEndpoint,
)
from adlife.core.domain.city_cognition import CityCognitionRuntimeFallbackReason
from adlife.core.domain.serialization import canonical_json

_JSON_CONTENT_TYPES = frozenset({"application/json"})
_TRANSIENT_SERVER_STATUSES = frozenset({500, 502, 503, 504})
_AUTHENTICATION_STATUSES = frozenset({401, 403})
_RAW_HEADER_END = re.compile(rb"\n\r?\n")
_JSON_ESCAPE = re.compile(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')
_JSON_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class CityProviderCallError(RuntimeError):
    """One value-free expected runtime failure suitable for deterministic fallback."""

    reason_code: ClassVar[CityCognitionRuntimeFallbackReason]
    retryable: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__("city cognition provider attempt failed")


class CityProviderAuthenticationError(CityProviderCallError):
    reason_code = "authentication-refused"

    def __init__(self) -> None:
        super().__init__()
        self.args = ("city cognition provider authentication was refused",)


class CityProviderTimeoutError(CityProviderCallError):
    reason_code = "timeout-exhausted"
    retryable = True

    def __init__(self) -> None:
        super().__init__()
        self.args = ("city cognition provider attempt timed out",)


class CityProviderRateLimitError(CityProviderCallError):
    reason_code = "rate-limit-exhausted"
    retryable = True

    def __init__(self) -> None:
        super().__init__()
        self.args = ("city cognition provider rate limited the attempt",)


class CityProviderServerError(CityProviderCallError):
    reason_code = "server-error-exhausted"
    retryable = True

    def __init__(self) -> None:
        super().__init__()
        self.args = ("city cognition provider returned a transient server error",)


class CityProviderUnavailableError(CityProviderCallError):
    reason_code = "endpoint-resolution-refused"
    retryable = True

    def __init__(self) -> None:
        super().__init__()
        self.args = ("city cognition provider endpoint was unavailable",)


class CityProviderResponseTooLargeError(CityProviderCallError):
    reason_code = "response-too-large"

    def __init__(self) -> None:
        super().__init__()
        self.args = ("city cognition provider response exceeded its byte limit",)


class CityProviderProtocolError(CityProviderCallError):
    reason_code = "protocol-incompatible"

    def __init__(self) -> None:
        super().__init__()
        self.args = ("city cognition provider response was protocol-incompatible",)


class CityProviderTransportError(RuntimeError):
    """An unexpected adapter or dependency defect crossed the transport boundary."""

    def __init__(self) -> None:
        super().__init__("city cognition provider transport failed unexpectedly")


@dataclass(frozen=True, slots=True)
class _WireResponse:
    status: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes


_TransportFailure = Literal[
    "authentication",
    "internal",
    "protocol",
    "rate-limit",
    "response-too-large",
    "server",
    "timeout",
    "unavailable",
]
_TransportOutcome = _WireResponse | _TransportFailure


async def _redact_httpcore_trace(event: str, info: dict[str, Any]) -> None:
    """Redact request-local diagnostics before httpcore renders its debug event.

    Completed/failed trace mappings contain raw response headers or exceptions.
    Started mappings contain safe request/connection metadata and are also used as
    actual operation kwargs, so they must remain intact. No logger is reconfigured.
    """
    if event.endswith((".complete", ".failed")):
        info.clear()


def _decode_json_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        escaped = match.group(0)[1:]
        if escaped.startswith("u"):
            return chr(int(escaped[1:], 16))
        return _JSON_SIMPLE_ESCAPES[escaped]

    return _JSON_ESCAPE.sub(replace, value)


def _header_values(
    headers: tuple[tuple[bytes, bytes], ...],
    name: bytes,
) -> tuple[bytes, ...]:
    folded = name.lower()
    return tuple(value.strip() for key, value in headers if key.lower() == folded)


def _content_type(headers: tuple[tuple[bytes, bytes], ...]) -> str:
    values = _header_values(headers, b"content-type")
    if len(values) != 1:
        raise CityProviderProtocolError from None
    rendered: str | None = None
    with suppress(UnicodeDecodeError):
        rendered = values[0].decode("ascii")
    if rendered is None:
        raise CityProviderProtocolError from None
    media_type = rendered.split(";", 1)[0].strip().lower()
    if media_type not in _JSON_CONTENT_TYPES:
        raise CityProviderProtocolError from None
    return media_type


def _content_length(headers: tuple[tuple[bytes, bytes], ...], maximum: int) -> int | None:
    values = _header_values(headers, b"content-length")
    if not values:
        return None
    if len(values) != 1:
        raise CityProviderProtocolError from None
    value: int | None = None
    try:
        rendered = values[0].decode("ascii")
        if rendered.isdecimal():
            value = int(rendered)
    except (UnicodeError, ValueError):
        pass
    if value is None:
        raise CityProviderProtocolError from None
    if value > maximum:
        raise CityProviderResponseTooLargeError from None
    return value


def _peer_address(response: httpcore.Response) -> str:
    stream = response.extensions.get("network_stream")
    peer: object = None
    if stream is not None and callable(getattr(stream, "get_extra_info", None)):
        peer = stream.get_extra_info("server_addr")
    if isinstance(peer, tuple) and peer:
        peer = peer[0]
    if type(peer) is not str:
        raise CityEndpointPeerMismatch from None
    return peer


def _classify_status(status: int) -> None:
    if status == 200:
        return
    if status in _AUTHENTICATION_STATUSES:
        raise CityProviderAuthenticationError from None
    if status == 429:
        raise CityProviderRateLimitError from None
    if status in _TRANSIENT_SERVER_STATUSES:
        raise CityProviderServerError from None
    raise CityProviderProtocolError from None


class _PeerVerifyingNetworkBackend(httpcore.AsyncNetworkBackend):
    """Pin the socket before httpcore can write headers containing a credential.

    ``httpcore`` receives the selected numeric address as its URL host, and this wrapper
    additionally verifies the connected peer immediately after ``connect_tcp``.  TLS and
    the first request write occur only after the verified stream is returned.
    """

    def __init__(
        self,
        *,
        inner: httpcore.AsyncNetworkBackend,
        resolved: ResolvedProviderEndpoint,
        max_wire_bytes: int,
    ) -> None:
        self._inner = inner
        self._resolved = resolved
        self._max_wire_bytes = max_wire_bytes
        self._prepared: httpcore.AsyncNetworkStream | None = None

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self._resolved.selected_address or port != self._resolved.endpoint.port:
            raise CityEndpointPeerMismatch from None
        if self._prepared is not None:
            stream, self._prepared = self._prepared, None
            return stream
        stream = await self._inner.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        peer: object = stream.get_extra_info("server_addr")
        if isinstance(peer, tuple) and peer:
            peer = peer[0]
        mismatch = type(peer) is not str
        if not mismatch:
            try:
                self._resolved.verify_peer_address(cast("str", peer))
            except CityEndpointPeerMismatch:
                mismatch = True
        if mismatch:
            await stream.aclose()
            raise CityEndpointPeerMismatch from None
        return _WireBoundedNetworkStream(stream, max_wire_bytes=self._max_wire_bytes)

    async def prepare(self, timeout: float) -> None:
        """Verify the socket before the caller materializes its Authorization header.

        The next httpcore connect takes ownership of this single prepared stream;
        aclose also covers failures before that handoff.
        """
        self._prepared = await self.connect_tcp(
            self._resolved.selected_address, self._resolved.endpoint.port, timeout=timeout
        )

    async def aclose(self) -> None:
        if self._prepared is not None:
            await self._prepared.aclose()
            self._prepared = None

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise CityEndpointPeerMismatch from None

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _WireResponseByteCounter:
    """Count inbound cleartext HTTP bytes, including headers and transfer framing."""

    __slots__ = ("_maximum", "_received")

    def __init__(self, maximum: int) -> None:
        self._received = 0
        self._maximum = maximum

    def accepts(self, size: int) -> bool:
        if size > self._maximum - self._received:
            return False
        self._received += size
        return True


class _RawResponseHeaderGuard:
    """Refuse duplicate lengths before h11 can merge matching raw field values."""

    __slots__ = ("_done", "_pending")

    def __init__(self) -> None:
        self._pending = bytearray()
        self._done = False

    def check(self, chunk: bytes) -> None:
        if self._done:
            return
        # The stream's independent wire counter bounds this buffer, including an
        # unfinished head. Only heads are inspected; body/trailer bytes stay opaque.
        search_from = max(0, len(self._pending) - 2)
        self._pending.extend(chunk)
        while (boundary := _RAW_HEADER_END.search(self._pending, search_from)) is not None:
            head = bytes(self._pending[: boundary.start()]).split(b"\n")
            del self._pending[: boundary.end()]
            search_from = 0
            fields: list[bytes] = []
            for line in head[1:]:
                line = line.rstrip(b"\r")
                if line.startswith((b" ", b"\t")) and fields:
                    fields[-1] += b" " + line.strip()
                else:
                    fields.append(line)
            lengths = [
                value.strip()
                for field in fields
                for name, separator, value in [field.partition(b":")]
                if separator and name.lower() == b"content-length"
            ]
            if len(lengths) > 1 or any(b"," in value for value in lengths):
                raise CityProviderProtocolError from None
            status = head[0].split()
            if len(status) > 1 and status[1].isdigit() and 100 <= int(status[1]) < 200:
                # A following final response has its own independent header block.
                continue
            self._pending.clear()
            self._done = True
            return


class _WireBoundedNetworkStream(httpcore.AsyncNetworkStream):
    """Bound inbound HTTP bytes while leaving request writes and TLS setup uncharged."""

    __slots__ = ("_counter", "_header_guard", "_inner")

    def __init__(
        self,
        inner: httpcore.AsyncNetworkStream,
        *,
        max_wire_bytes: int,
        counter: _WireResponseByteCounter | None = None,
        header_guard: _RawResponseHeaderGuard | None = None,
    ) -> None:
        self._inner = inner
        self._counter = _WireResponseByteCounter(max_wire_bytes) if counter is None else counter
        self._header_guard = _RawResponseHeaderGuard() if header_guard is None else header_guard

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        chunk = await self._inner.read(max_bytes, timeout=timeout)
        if self._counter.accepts(len(chunk)):
            self._header_guard.check(chunk)
            return chunk
        with suppress(Exception):
            await self._inner.aclose()
        raise CityProviderResponseTooLargeError from None

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._inner.write(buffer, timeout=timeout)

    async def aclose(self) -> None:
        # httpcore's pool-close trace has no request/trace extension. Sanitize at
        # this boundary before that trace can render a dependency exception.
        failure: Literal["timeout", "network", "protocol", "internal", "cancelled"] | None = None
        try:
            await self._inner.aclose()
        except asyncio.CancelledError:
            failure = "cancelled"
        except httpcore.TimeoutException:
            failure = "timeout"
        except httpcore.NetworkError:
            failure = "network"
        except httpcore.ProtocolError:
            failure = "protocol"
        except Exception:
            failure = "internal"
        if failure == "cancelled":
            raise asyncio.CancelledError from None
        if failure == "timeout":
            raise httpcore.ReadTimeout("city provider socket close failed") from None
        if failure == "network":
            raise httpcore.ReadError("city provider socket close failed") from None
        if failure == "protocol":
            raise httpcore.RemoteProtocolError("city provider socket close failed") from None
        if failure == "internal":
            raise CityProviderTransportError from None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        stream = await self._inner.start_tls(
            ssl_context,
            server_hostname=server_hostname,
            timeout=timeout,
        )
        return _WireBoundedNetworkStream(
            stream, max_wire_bytes=0, counter=self._counter, header_guard=self._header_guard
        )

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class CityProviderHttpTransport:
    """One private credential and a fresh pinned connection for every JSON POST."""

    __slots__ = (
        "_attempt_timeout_seconds",
        "_closed",
        "_credential",
        "_endpoint",
        "_endpoint_policy",
        "_max_content_length_bytes",
        "_max_decoded_response_bytes",
        "_max_wire_response_bytes",
        "_network_backend_factory",
    )

    def __init__(
        self,
        *,
        endpoint_policy: CityEndpointPolicy,
        endpoint: BoundProviderEndpoint,
        credential: ResolvedCredential,
        attempt_timeout_seconds: int,
        max_content_length_bytes: int,
        max_wire_response_bytes: int,
        max_decoded_response_bytes: int,
        network_backend_factory: Callable[[], httpcore.AsyncNetworkBackend] | None = None,
    ) -> None:
        if (
            type(endpoint_policy) is not CityEndpointPolicy
            or type(endpoint) is not BoundProviderEndpoint
        ):
            raise CityEndpointBindingError from None
        bound = False
        try:
            bound = (
                type(credential) is ResolvedCredential
                and type(credential.secret) is SecretBuffer
                and not credential.secret.cleared
                and type(credential.origin_approval_id) is str
                and credential.origin_approval_id == endpoint.origin_approval_id
                and type(credential.credential_slot_id) is str
                and re.fullmatch(r"[0-9a-f]{64}", credential.credential_slot_id) is not None
                and type(credential.source) is str
                and (
                    (endpoint.trust_class == "local" and credential.source == "local-dummy")
                    or (
                        endpoint.trust_class == "remote"
                        and credential.source in {"keyring", "environment"}
                    )
                )
            )
        except (AttributeError, TypeError, ValueError):
            bound = False
        if not bound:
            raise CityEndpointBindingError from None
        for limit in (
            attempt_timeout_seconds,
            max_content_length_bytes,
            max_wire_response_bytes,
            max_decoded_response_bytes,
        ):
            if type(limit) is not int or limit <= 0:
                raise ValueError("city provider transport limits must be positive integers")
        if network_backend_factory is not None and not callable(network_backend_factory):
            raise TypeError("network_backend_factory must be callable")
        self._endpoint_policy = endpoint_policy
        self._endpoint = endpoint
        self._network_backend_factory = network_backend_factory
        self._attempt_timeout_seconds = attempt_timeout_seconds
        self._max_content_length_bytes = max_content_length_bytes
        self._max_wire_response_bytes = max_wire_response_bytes
        self._max_decoded_response_bytes = max_decoded_response_bytes
        self._closed = False
        copied: SecretBuffer | None = None
        with suppress(CityCredentialStoreError):
            copied = (
                _new_local_dummy_credential()
                if endpoint.trust_class == "local"
                else credential.secret.copy()
            )
        if copied is None:
            raise CityEndpointBindingError from None
        self._credential = copied

    def __repr__(self) -> str:
        return f"<{type(self).__name__} credential=[redacted] closed={self._closed}>"

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def aclose(self) -> None:
        self._credential.clear()
        self._closed = True

    def contains_bound_credential(self, value: str | bytes) -> bool:
        if self._closed:
            return False
        known = self._credential.reveal_text()
        if isinstance(value, bytes):
            return known.encode("ascii") in value
        for _ in range(4):
            if known in value:
                return True
            decoded = _decode_json_escapes(value)
            if decoded == value:
                return False
            value = decoded
        return known in value

    async def post_json(self, payload: Mapping[str, object]) -> bytes:
        if self._closed:
            raise CityProviderUnavailableError from None
        timed_out = False
        outcome: _TransportOutcome | None = None
        try:
            async with asyncio.timeout(self._attempt_timeout_seconds):
                outcome = await self._send_once_safely(payload)
        except TimeoutError:
            timed_out = True
        if timed_out:
            raise CityProviderTimeoutError from None
        if outcome == "timeout":
            raise CityProviderTimeoutError from None
        if outcome == "unavailable":
            raise CityProviderUnavailableError from None
        if outcome == "authentication":
            raise CityProviderAuthenticationError from None
        if outcome == "rate-limit":
            raise CityProviderRateLimitError from None
        if outcome == "server":
            raise CityProviderServerError from None
        if outcome == "response-too-large":
            raise CityProviderResponseTooLargeError from None
        if outcome == "protocol":
            raise CityProviderProtocolError from None
        if outcome == "internal":
            raise CityProviderTransportError from None
        assert isinstance(outcome, _WireResponse)
        return outcome.body

    async def _send_once_safely(
        self,
        payload: Mapping[str, object],
    ) -> _TransportOutcome:
        """Discard raw transport tracebacks before an error reaches the public call."""
        outcome: _TransportOutcome | None = None
        cancelled = False
        failed = False
        try:
            outcome = await self._send_once(payload)
        except asyncio.CancelledError:
            cancelled = True
        except (CityEndpointResolutionError, CityEndpointPeerMismatch):
            outcome = "unavailable"
        except Exception:
            failed = True
        if cancelled:
            # Raise while still inside the caller's timeout context. Its own cancellation
            # is therefore translated to TimeoutError, while operator cancellation stays
            # CancelledError. The discarded exception carried the raw transport frames.
            raise asyncio.CancelledError from None
        if failed:
            raise CityProviderTransportError from None
        assert outcome is not None
        return outcome

    async def _send_once(
        self,
        payload: Mapping[str, object],
    ) -> _TransportOutcome:
        resolved = await self._endpoint_policy.resolve_attempt(self._endpoint)
        body = canonical_json(payload).encode("utf-8")
        url = httpcore.URL(
            scheme=resolved.endpoint.scheme,
            host=resolved.selected_address,
            port=resolved.endpoint.port,
            target=resolved.endpoint.chat_completions_target,
        )
        timeout = self._attempt_timeout_seconds
        extensions: dict[str, object] = {
            "trace": _redact_httpcore_trace,
            "timeout": {
                "connect": float(timeout),
                "read": float(timeout),
                "write": float(timeout),
                "pool": float(timeout),
            },
        }
        if resolved.endpoint.tls_server_hostname is not None:
            extensions["sni_hostname"] = resolved.endpoint.tls_server_hostname
        inner_backend = (
            httpcore.AnyIOBackend()
            if self._network_backend_factory is None
            else self._network_backend_factory()
        )
        backend = _PeerVerifyingNetworkBackend(
            inner=inner_backend,
            resolved=resolved,
            max_wire_bytes=self._max_wire_response_bytes,
        )
        ssl_context = ssl.create_default_context()
        response: _WireResponse | None = None
        failure: _TransportFailure | None = None
        try:
            await backend.prepare(float(timeout))
            # No credential text or request header exists until peer verification passes.
            credential = self._credential.reveal_text().encode("ascii")
            headers = (
                (b"Host", resolved.endpoint.host_header.encode("ascii")),
                (b"Authorization", b"Bearer " + credential),
                (b"Accept", b"application/json"),
                (b"Content-Type", b"application/json"),
                (b"Accept-Encoding", b"identity"),
                (b"Content-Length", str(len(body)).encode("ascii")),
                (b"Connection", b"close"),
            )
            async with (
                httpcore.AsyncConnectionPool(
                    ssl_context=ssl_context,
                    proxy=None,
                    max_connections=1,
                    max_keepalive_connections=0,
                    keepalive_expiry=0,
                    http1=True,
                    http2=False,
                    retries=0,
                    local_address=None,
                    uds=None,
                    network_backend=backend,
                ) as pool,
                pool.stream(
                    b"POST",
                    url,
                    headers=headers,
                    content=body,
                    extensions=cast("dict[str, Any]", extensions),
                ) as received,
            ):
                response = await self._read_response(received, resolved)
        except CityProviderAuthenticationError:
            failure = "authentication"
        except CityProviderRateLimitError:
            failure = "rate-limit"
        except CityProviderServerError:
            failure = "server"
        except CityProviderResponseTooLargeError:
            failure = "response-too-large"
        except CityProviderProtocolError:
            failure = "protocol"
        except httpcore.TimeoutException:
            failure = "timeout"
        except CityEndpointPeerMismatch:
            failure = "unavailable"
        except httpcore.NetworkError:
            failure = "unavailable"
        except httpcore.ProtocolError:
            failure = "protocol"
        finally:
            await backend.aclose()
        if failure is not None:
            return failure
        assert response is not None
        return response

    async def _read_response(
        self,
        response: httpcore.Response,
        resolved: ResolvedProviderEndpoint,
    ) -> _WireResponse:
        if response.extensions.get("http_version") != b"HTTP/1.1":
            raise CityProviderProtocolError from None
        resolved.verify_peer_address(_peer_address(response))
        headers = tuple(response.headers)
        _classify_status(response.status)
        _content_type(headers)
        if _header_values(headers, b"content-encoding"):
            raise CityProviderProtocolError from None
        declared_length = _content_length(headers, self._max_content_length_bytes)
        chunks: list[bytes] = []
        decoded_bytes = 0
        async for chunk in response.aiter_stream():
            decoded_bytes += len(chunk)
            if decoded_bytes > self._max_decoded_response_bytes:
                raise CityProviderResponseTooLargeError from None
            chunks.append(chunk)
        body = b"".join(chunks)
        if declared_length is not None and declared_length != decoded_bytes:
            raise CityProviderProtocolError from None
        if len(body) > self._max_decoded_response_bytes:
            raise CityProviderResponseTooLargeError from None
        return _WireResponse(status=response.status, headers=headers, body=body)
