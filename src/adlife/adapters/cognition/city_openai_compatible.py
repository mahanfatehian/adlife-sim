"""Hardened OpenAI-compatible transport for noncausal city explanations.

The adapter implements one deliberately small protocol: a non-streaming HTTP/1.1
``POST`` to the approved Chat Completions target with the frozen strict JSON schema.
Every attempt resolves independently and connects to the policy-selected numeric address
while retaining the approved HTTP Host and TLS SNI identities.  Raw provider content and
credentials never cross the city cognition port.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator, Mapping
from typing import Literal, Self, cast
from unicodedata import bidirectional

import httpcore
from pydantic import ValidationError

from adlife.adapters.cognition.city_credentials import (
    ResolvedCredential,
    SecretBuffer,
)
from adlife.adapters.cognition.city_endpoint_policy import (
    BoundProviderEndpoint,
    CityEndpointBindingError,
    CityEndpointPolicy,
)
from adlife.adapters.cognition.city_prompts import (
    build_city_cognition_payload,
    build_city_cognition_repair_payload,
)
from adlife.adapters.cognition.city_provider_transport import (
    CityProviderAuthenticationError,
    CityProviderCallError,
    CityProviderHttpTransport,
    CityProviderProtocolError,
    CityProviderRateLimitError,
    CityProviderResponseTooLargeError,
    CityProviderServerError,
    CityProviderTimeoutError,
    CityProviderTransportError,
    CityProviderUnavailableError,
    validated_check_attempt_timeout_seconds,
)
from adlife.core.domain.city_cognition import (
    MAX_CITY_COGNITION_USAGE_INTEGER,
    MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES,
    CityCognitionInputV1,
    CityCognitionRequestV1,
    CityCognitionResultV1,
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


def _unsafe_provider_text(value: str, *, credential_present: Callable[[str | bytes], bool]) -> bool:
    """Screen decoded and repeatedly escaped provider text without ever returning it."""
    current = value
    for _ in range(4):
        if (
            credential_present(current)
            or contains_provider_secret_text(current)
            or contains_sensitive_text(current)
        ):
            return True
        decoded = _decode_json_escapes(current)
        if decoded == current:
            return False
        current = decoded
    return True


def _unsafe_protocol_text(value: str, *, credential_present: Callable[[str | bytes], bool]) -> bool:
    """Screen the whole raw body for the bound credential and provider-secret shapes only.

    Raw protocol JSON legitimately carries numbers, such as the ten-digit Unix
    ``created`` time of every real completion, that persona phone and national-ID
    heuristics would misread. Those heuristics apply to every decoded envelope string
    and every model-authored result string instead.
    """
    current = value
    for _ in range(4):
        if credential_present(current) or contains_provider_secret_text(current):
            return True
        decoded = _decode_json_escapes(current)
        if decoded == current:
            return False
        current = decoded
    return True


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


class CityOpenAICompatibleProvider:
    """One bounded provider using only the frozen city cognition input."""

    __slots__ = (
        "_closed",
        "_cognition_input",
        "_endpoint",
        "_transport",
    )

    def __init__(
        self,
        *,
        endpoint_policy: CityEndpointPolicy,
        endpoint: BoundProviderEndpoint,
        cognition_input: CityCognitionInputV1,
        credential: ResolvedCredential,
        network_backend_factory: Callable[[], httpcore.AsyncNetworkBackend] | None = None,
        check_attempt_timeout_seconds: int | None = None,
    ) -> None:
        """Bind one frozen input; only an explicit compatibility check may extend its deadline.

        ``check_attempt_timeout_seconds`` replaces the frozen per-attempt transport
        timeout for this provider instance alone. Runs never pass it, so their attempts
        keep the frozen effective settings.
        """
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
        check_timeout = validated_check_attempt_timeout_seconds(check_attempt_timeout_seconds)
        self._endpoint = endpoint
        self._cognition_input = checked_input
        self._closed = False
        self._transport = CityProviderHttpTransport(
            endpoint_policy=endpoint_policy,
            endpoint=endpoint,
            credential=credential,
            attempt_timeout_seconds=(
                settings.attempt_timeout_seconds if check_timeout is None else check_timeout
            ),
            max_content_length_bytes=MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES,
            max_wire_response_bytes=MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES,
            max_decoded_response_bytes=MAX_CITY_COGNITION_WIRE_RESPONSE_BYTES,
            network_backend_factory=network_backend_factory,
        )

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
        await self._transport.aclose()
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
        body = await self._transport.post_json(payload)
        parsed = self._parse_attempt(request, body)
        body = b""
        # The public exception is raised only after the untrusted response is no longer
        # retained by this value-free orchestration frame.
        if parsed == "protocol":
            raise CityProviderProtocolError from None
        if parsed == "internal":
            raise CityProviderTransportError from None
        return parsed

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
        if self._transport.contains_bound_credential(body):
            return _invalid_attempt("unsafe-content")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return _invalid_attempt("invalid-json")
        if _unsafe_protocol_text(
            text, credential_present=self._transport.contains_bound_credential
        ):
            return _invalid_attempt("unsafe-content")
        try:
            decoded = _strict_json_value(text)
        except _StrictJsonError:
            return _invalid_attempt("invalid-json")
        if not isinstance(decoded, Mapping):
            return _invalid_attempt("invalid-json")
        if any(
            _unsafe_provider_text(
                item, credential_present=self._transport.contains_bound_credential
            )
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
            _unsafe_provider_text(
                item, credential_present=self._transport.contains_bound_credential
            )
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
