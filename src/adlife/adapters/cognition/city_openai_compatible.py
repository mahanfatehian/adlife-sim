"""Hardened OpenAI-compatible transport for noncausal city explanations.

The adapter implements one deliberately small protocol: a non-streaming HTTP/1.1
``POST`` to the approved Chat Completions target with the frozen strict JSON schema.
Every attempt resolves independently and connects to the policy-selected numeric address
while retaining the approved HTTP Host and TLS SNI identities.  Raw provider content and
credentials never cross the city cognition port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import ssl
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Self, cast
from unicodedata import bidirectional

import httpcore
from pydantic import ValidationError

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
from adlife.adapters.cognition.city_prompts import (
    build_city_cognition_payload,
    build_city_cognition_repair_payload,
)
from adlife.core.domain.city_cognition import (
    MAX_CITY_COGNITION_USAGE_INTEGER,
    MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES,
    CityCognitionInputV1,
    CityCognitionRequestV1,
    CityCognitionResultV1,
    CityCognitionRuntimeFallbackReason,
    CityProviderAttemptV1,
    CityProviderRepairContextV1,
    CityProviderRepairReason,
    CityProviderUsageV1,
)
from adlife.core.domain.person import (
    contains_provider_secret_text,
    contains_sensitive_text,
)
from adlife.core.domain.serialization import canonical_json

_JSON_CONTENT_TYPES = frozenset({"application/json"})
_TRANSIENT_SERVER_STATUSES = frozenset({500, 502, 503, 504})
_AUTHENTICATION_STATUSES = frozenset({401, 403})
_ENVELOPE_FIELDS = frozenset(
    {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "system_fingerprint",
        "service_tier",
    }
)
_CHOICE_FIELDS = frozenset({"index", "message", "finish_reason", "logprobs"})
_MESSAGE_FIELDS = frozenset({"role", "content", "refusal"})
_USAGE_FIELDS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
    }
)
_PROMPT_TOKEN_DETAIL_FIELDS = frozenset({"cached_tokens", "audio_tokens"})
_COMPLETION_TOKEN_DETAIL_FIELDS = frozenset(
    {
        "reasoning_tokens",
        "audio_tokens",
        "accepted_prediction_tokens",
        "rejected_prediction_tokens",
    }
)
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
_BIDI_CONTROLS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"})
_CITY_PROVIDER_TRANSPORT_ACTIVE: ContextVar[bool] = ContextVar(
    "adlife_city_provider_transport_active",
    default=False,
)


class _DropCityProviderHttpcoreLogs(logging.Filter):
    """Keep provider-controlled wire values out of dependency debug logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        del record
        return not _CITY_PROVIDER_TRANSPORT_ACTIVE.get()


_HTTPCORE_LOG_FILTER = _DropCityProviderHttpcoreLogs()
for _httpcore_logger_name in (
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(_httpcore_logger_name).addFilter(_HTTPCORE_LOG_FILTER)


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
_ParseOutcome = CityProviderAttemptV1 | Literal["internal", "protocol"]


class _StrictJsonError(ValueError):
    pass


def _reject_json_constant(_value: str) -> None:
    raise _StrictJsonError


def _strict_json_value(value: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise _StrictJsonError
            result[key] = item
        return result

    def finite_float(raw: str) -> float:
        parsed = float(raw)
        if not math.isfinite(parsed):
            raise _StrictJsonError
        return parsed

    try:
        return json.loads(
            value,
            parse_constant=_reject_json_constant,
            parse_float=finite_float,
            object_pairs_hook=unique_object,
        )
    except (RecursionError, UnicodeError, ValueError):
        raise _StrictJsonError from None


def _iter_strings(value: object) -> Iterator[str]:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, Mapping):
            for key, item in current.items():
                if isinstance(key, str):
                    yield key
                pending.append(item)
        elif isinstance(current, list):
            pending.extend(current)


def _decode_json_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        escaped = match.group(0)[1:]
        if escaped.startswith("u"):
            return chr(int(escaped[1:], 16))
        return _JSON_SIMPLE_ESCAPES[escaped]

    return _JSON_ESCAPE.sub(replace, value)


def _has_invalid_wire_text(value: str) -> bool:
    return any(
        ord(character) < 32
        or 127 <= ord(character) < 160
        or 0xD800 <= ord(character) <= 0xDFFF
        or character in {"\u2028", "\u2029"}
        or bidirectional(character) in _BIDI_CONTROLS
        for character in value
    )


def _unsafe_provider_text(value: str, *, known_credential: str) -> bool:
    """Screen raw and repeatedly escaped provider text without ever returning it."""
    current = value
    for _ in range(4):
        if (
            known_credential in current
            or contains_provider_secret_text(current)
            or contains_sensitive_text(current)
        ):
            return True
        decoded = _decode_json_escapes(current)
        if decoded == current:
            return False
        current = decoded
    return True


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


def _content_length(headers: tuple[tuple[bytes, bytes], ...]) -> int | None:
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
    if value > MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES:
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


def _safe_repair_context(reason: CityProviderRepairReason) -> CityProviderRepairContextV1:
    excerpts: dict[CityProviderRepairReason, str] = {
        "invalid-json": "The answer was not one valid JSON response object.",
        "invalid-schema": "The answer did not match the required result schema.",
        "request-id-mismatch": "The answer request identifier did not match this request.",
        "unsafe-content": ("The answer contained screened sensitive or credential-shaped text."),
    }
    return CityProviderRepairContextV1(
        schema_version=1,
        reason_code=reason,
        safe_excerpt=excerpts[reason],
    )


def _invalid_attempt(reason: CityProviderRepairReason) -> CityProviderAttemptV1:
    return CityProviderAttemptV1(
        schema_version=1,
        status="invalid-result",
        repair_context=_safe_repair_context(reason),
    )


def _usage(envelope: Mapping[str, object]) -> CityProviderUsageV1 | None:
    raw = envelope.get("usage")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise CityProviderProtocolError from None
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    total = raw.get("total_tokens")
    if (
        type(prompt) is not int
        or type(completion) is not int
        or not 0 <= prompt <= MAX_CITY_COGNITION_USAGE_INTEGER
        or not 0 <= completion <= MAX_CITY_COGNITION_USAGE_INTEGER
        or (
            total is not None
            and (
                type(total) is not int
                or total != prompt + completion
                or total > MAX_CITY_COGNITION_USAGE_INTEGER
            )
        )
    ):
        raise CityProviderProtocolError from None
    return CityProviderUsageV1(
        schema_version=1,
        prompt_tokens=prompt,
        completion_tokens=completion,
    )


def _bounded_optional_usage_details(
    value: object,
    *,
    allowed_fields: frozenset[str],
) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping) or set(value) - allowed_fields:
        return False
    return all(
        item is None or (type(item) is int and 0 <= item <= MAX_CITY_COGNITION_USAGE_INTEGER)
        for item in value.values()
    )


def _validate_envelope(envelope: Mapping[str, object]) -> None:
    if set(envelope) - _ENVELOPE_FIELDS:
        raise CityProviderProtocolError from None
    if "object" in envelope and envelope["object"] != "chat.completion":
        raise CityProviderProtocolError from None
    if "id" in envelope and (
        not isinstance(envelope["id"], str)
        or not envelope["id"]
        or _has_invalid_wire_text(envelope["id"])
    ):
        raise CityProviderProtocolError from None
    if "model" in envelope and (
        not isinstance(envelope["model"], str)
        or not envelope["model"]
        or _has_invalid_wire_text(envelope["model"])
    ):
        raise CityProviderProtocolError from None
    if "created" in envelope and (
        type(envelope["created"]) is not int
        or not 0 <= envelope["created"] <= MAX_CITY_COGNITION_USAGE_INTEGER
    ):
        raise CityProviderProtocolError from None
    for optional_text in ("system_fingerprint", "service_tier"):
        if (
            optional_text in envelope
            and envelope[optional_text] is not None
            and (
                not isinstance(envelope[optional_text], str)
                or _has_invalid_wire_text(cast("str", envelope[optional_text]))
            )
        ):
            raise CityProviderProtocolError from None

    choices = envelope.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise CityProviderProtocolError from None
    choice = choices[0]
    if not isinstance(choice, Mapping) or set(choice) - _CHOICE_FIELDS:
        raise CityProviderProtocolError from None
    if "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0):
        raise CityProviderProtocolError from None
    if "logprobs" in choice and choice["logprobs"] is not None:
        raise CityProviderProtocolError from None
    if "finish_reason" in choice and choice["finish_reason"] not in {
        "stop",
        "length",
        "content_filter",
    }:
        raise CityProviderProtocolError from None
    message = choice.get("message")
    if not isinstance(message, Mapping) or set(message) - _MESSAGE_FIELDS:
        raise CityProviderProtocolError from None
    if "role" in message and message["role"] != "assistant":
        raise CityProviderProtocolError from None
    if "refusal" in message and message["refusal"] is not None:
        raise CityProviderProtocolError from None

    raw_usage = envelope.get("usage")
    if raw_usage is not None:
        if not isinstance(raw_usage, Mapping) or set(raw_usage) - _USAGE_FIELDS:
            raise CityProviderProtocolError from None
        if not _bounded_optional_usage_details(
            raw_usage.get("prompt_tokens_details"),
            allowed_fields=_PROMPT_TOKEN_DETAIL_FIELDS,
        ) or not _bounded_optional_usage_details(
            raw_usage.get("completion_tokens_details"),
            allowed_fields=_COMPLETION_TOKEN_DETAIL_FIELDS,
        ):
            raise CityProviderProtocolError from None


def _message_content(envelope: Mapping[str, object]) -> str:
    choices = envelope.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise CityProviderProtocolError from None
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise CityProviderProtocolError from None
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise CityProviderProtocolError from None
    if any(key in message for key in ("tool_calls", "function_call", "audio")):
        raise CityProviderProtocolError from None
    if choice.get("finish_reason") in {"tool_calls", "function_call"}:
        raise CityProviderProtocolError from None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise CityProviderProtocolError from None
    return content


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
    ) -> None:
        self._inner = inner
        self._resolved = resolved

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
        return _WireBoundedNetworkStream(stream)

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

    __slots__ = ("_received",)

    def __init__(self) -> None:
        self._received = 0

    def accepts(self, size: int) -> bool:
        if size > MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES - self._received:
            return False
        self._received += size
        return True


class _WireBoundedNetworkStream(httpcore.AsyncNetworkStream):
    """Bound inbound HTTP bytes while leaving request writes and TLS setup uncharged."""

    __slots__ = ("_counter", "_inner")

    def __init__(
        self,
        inner: httpcore.AsyncNetworkStream,
        *,
        counter: _WireResponseByteCounter | None = None,
    ) -> None:
        self._inner = inner
        self._counter = _WireResponseByteCounter() if counter is None else counter

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        chunk = await self._inner.read(max_bytes, timeout=timeout)
        if self._counter.accepts(len(chunk)):
            return chunk
        with suppress(Exception):
            await self._inner.aclose()
        raise CityProviderResponseTooLargeError from None

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._inner.write(buffer, timeout=timeout)

    async def aclose(self) -> None:
        await self._inner.aclose()

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
        return _WireBoundedNetworkStream(stream, counter=self._counter)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class CityOpenAICompatibleProvider:
    """One bounded provider using only the frozen city cognition input."""

    __slots__ = (
        "_closed",
        "_cognition_input",
        "_credential",
        "_endpoint",
        "_endpoint_policy",
        "_network_backend_factory",
    )

    def __init__(
        self,
        *,
        endpoint_policy: CityEndpointPolicy,
        endpoint: BoundProviderEndpoint,
        cognition_input: CityCognitionInputV1,
        credential: ResolvedCredential,
        network_backend_factory: Callable[[], httpcore.AsyncNetworkBackend] | None = None,
    ) -> None:
        checked_input: CityCognitionInputV1 | None = None
        if type(endpoint_policy) is not CityEndpointPolicy:
            raise CityEndpointBindingError from None
        if type(endpoint) is not BoundProviderEndpoint:
            raise CityEndpointBindingError from None
        try:
            if type(cognition_input) is CityCognitionInputV1:
                checked_input = CityCognitionInputV1.model_validate(
                    cognition_input.model_dump(mode="python", warnings="none")
                )
        except (AttributeError, TypeError, ValueError, ValidationError):
            checked_input = None
        if checked_input is None:
            raise ValueError("city cognition provider input failed validation") from None
        profile = checked_input.provider_profile
        settings = checked_input.effective_settings
        if (
            checked_input.mode not in {"local", "remote"}
            or profile is None
            or settings is None
            or profile.profile_id != endpoint.profile_id
            or profile.revision_sha256 != endpoint.profile_revision_sha256
            or profile.origin_approval_id != endpoint.origin_approval_id
            or profile.mode != endpoint.trust_class
            or profile.base_url != endpoint.base_url
        ):
            raise CityEndpointBindingError from None
        credential_is_bound = False
        try:
            if (
                type(credential) is ResolvedCredential
                and type(credential.source) is str
                and type(credential.secret) is SecretBuffer
                and not credential.secret.cleared
                and type(credential.origin_approval_id) is str
                and type(credential.credential_slot_id) is str
                and credential.origin_approval_id == profile.origin_approval_id
                and credential.credential_slot_id == profile.credential_slot_id
                and (
                    (endpoint.trust_class == "local" and credential.source == "local-dummy")
                    or (
                        endpoint.trust_class == "remote"
                        and credential.source in {"keyring", "environment"}
                    )
                )
            ):
                credential_is_bound = True
        except (AttributeError, TypeError, ValueError):
            credential_is_bound = False
        if not credential_is_bound:
            raise CityEndpointBindingError from None
        if network_backend_factory is not None and not callable(network_backend_factory):
            raise TypeError("network_backend_factory must be callable")
        self._endpoint_policy = endpoint_policy
        self._endpoint = endpoint
        self._cognition_input = checked_input
        self._network_backend_factory = network_backend_factory
        self._closed = False
        copied_credential: SecretBuffer | None = None
        try:
            copied_credential = (
                _new_local_dummy_credential()
                if endpoint.trust_class == "local"
                else credential.secret.copy()
            )
        except CityCredentialStoreError:
            copied_credential = None
        if copied_credential is None:
            raise CityEndpointBindingError from None
        # Secret acquisition is the final constructor operation: no later validation or
        # user callback can fail while an unowned internal copy remains live.
        self._credential = copied_credential

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} profile_id={self._endpoint.profile_id!r} "
            f"revision={self._endpoint.profile_revision_sha256!r} credential=[redacted] "
            f"closed={self._closed}>"
        )

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def aclose(self) -> None:
        self._credential.clear()
        self._closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1:
        payload = build_city_cognition_payload(request, self._cognition_input)
        return await self._post(request, payload)

    async def repair_call(
        self,
        request: CityCognitionRequestV1,
        *,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1:
        payload = build_city_cognition_repair_payload(
            request,
            self._cognition_input,
            context=context,
        )
        return await self._post(request, payload)

    async def _post(
        self,
        request: CityCognitionRequestV1,
        payload: Mapping[str, object],
    ) -> CityProviderAttemptV1:
        if self._closed:
            raise CityProviderUnavailableError from None
        settings = self._cognition_input.effective_settings
        assert settings is not None
        timed_out = False
        outcome: _TransportOutcome | None = None
        log_guard = _CITY_PROVIDER_TRANSPORT_ACTIVE.set(True)
        try:
            try:
                async with asyncio.timeout(settings.attempt_timeout_seconds):
                    outcome = await self._send_once_safely(payload)
            except TimeoutError:
                timed_out = True
        finally:
            _CITY_PROVIDER_TRANSPORT_ACTIVE.reset(log_guard)
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
        parsed = self._parse_attempt(request, outcome.body)
        # The public exception is raised only after the untrusted response is no longer
        # retained by this value-free orchestration frame.
        outcome = None
        if parsed == "protocol":
            raise CityProviderProtocolError from None
        if parsed == "internal":
            raise CityProviderTransportError from None
        return parsed

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
        settings = self._cognition_input.effective_settings
        assert settings is not None
        timeout = settings.attempt_timeout_seconds
        extensions: dict[str, object] = {
            "timeout": {
                "connect": float(timeout),
                "read": float(timeout),
                "write": float(timeout),
                "pool": float(timeout),
            }
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
        )
        ssl_context = ssl.create_default_context()
        # Reveal and copy the credential only after every fallible setup operation. Any
        # later Exception is collapsed by `_send_once_safely` after this frame unwinds.
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
        response: _WireResponse | None = None
        failure: _TransportFailure | None = None
        try:
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
        declared_length = _content_length(headers)
        chunks: list[bytes] = []
        decoded_bytes = 0
        async for chunk in response.aiter_stream():
            decoded_bytes += len(chunk)
            if decoded_bytes > MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES:
                raise CityProviderResponseTooLargeError from None
            chunks.append(chunk)
        body = b"".join(chunks)
        if declared_length is not None and declared_length != decoded_bytes:
            raise CityProviderProtocolError from None
        if len(body) > MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES:
            raise CityProviderResponseTooLargeError from None
        return _WireResponse(status=response.status, headers=headers, body=body)

    def _parse_attempt(
        self,
        request: CityCognitionRequestV1,
        body: bytes,
    ) -> _ParseOutcome:
        """Collapse raw parser frames into value-free outcome codes."""
        outcome: CityProviderAttemptV1 | None = None
        protocol_failed = False
        internally_failed = False
        try:
            outcome = self._parse_attempt_untrusted(request, body)
        except CityProviderProtocolError:
            protocol_failed = True
        except Exception:
            internally_failed = True
        if protocol_failed:
            return "protocol"
        if internally_failed:
            return "internal"
        assert outcome is not None
        return outcome

    def _parse_attempt_untrusted(
        self,
        request: CityCognitionRequestV1,
        body: bytes,
    ) -> CityProviderAttemptV1:
        known_credential = self._credential.reveal_text()
        if known_credential.encode("ascii") in body:
            return _invalid_attempt("unsafe-content")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return _invalid_attempt("invalid-json")
        if _unsafe_provider_text(text, known_credential=known_credential):
            return _invalid_attempt("unsafe-content")
        try:
            decoded = _strict_json_value(text)
        except _StrictJsonError:
            return _invalid_attempt("invalid-json")
        if not isinstance(decoded, Mapping):
            return _invalid_attempt("invalid-json")
        if any(
            _unsafe_provider_text(item, known_credential=known_credential)
            for item in _iter_strings(decoded)
        ):
            return _invalid_attempt("unsafe-content")
        _validate_envelope(decoded)
        content = _message_content(decoded)
        try:
            result_document = _strict_json_value(content)
        except _StrictJsonError:
            return _invalid_attempt("invalid-json")
        if not isinstance(result_document, Mapping):
            return _invalid_attempt("invalid-schema")
        if any(
            _unsafe_provider_text(item, known_credential=known_credential)
            for item in _iter_strings(result_document)
        ):
            return _invalid_attempt("unsafe-content")
        try:
            # The domain model is strict: Python lists are intentionally not accepted as
            # tuples, while JSON arrays are their valid wire representation.  Re-encode
            # the already strictly parsed finite mapping and validate through the JSON
            # boundary rather than introducing a permissive Python coercion here.
            result = CityCognitionResultV1.model_validate_json(canonical_json(result_document))
        except (TypeError, ValueError, ValidationError):
            return _invalid_attempt("invalid-schema")
        if result.request_id != request.request_id:
            return _invalid_attempt("request-id-mismatch")
        return CityProviderAttemptV1(
            schema_version=1,
            status="valid",
            result=result,
            usage=_usage(decoded),
        )


__all__ = [
    "CityOpenAICompatibleProvider",
    "CityProviderAuthenticationError",
    "CityProviderCallError",
    "CityProviderProtocolError",
    "CityProviderRateLimitError",
    "CityProviderResponseTooLargeError",
    "CityProviderServerError",
    "CityProviderTimeoutError",
    "CityProviderTransportError",
    "CityProviderUnavailableError",
]
