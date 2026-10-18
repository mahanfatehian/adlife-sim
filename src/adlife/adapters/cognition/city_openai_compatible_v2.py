"""Strict bounded-signal admission over the shared credential-bound transport."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from typing import Literal, Self
from unicodedata import bidirectional

import httpcore

from adlife.adapters.cognition.city_credentials import ResolvedCredential, SecretBuffer
from adlife.adapters.cognition.city_endpoint_policy import (
    BoundProviderEndpoint,
    CityEndpointBindingError,
    CityEndpointPolicy,
)
from adlife.adapters.cognition.city_prompts_v2 import (
    build_city_cognition_payload_v2,
    build_city_cognition_repair_payload_v2,
)
from adlife.adapters.cognition.city_provider_transport import (
    CityProviderHttpTransport,
    CityProviderProtocolError,
    CityProviderTransportError,
    CityProviderUnavailableError,
)
from adlife.core.domain.city_cognition import MAX_CITY_COGNITION_USAGE_INTEGER
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionInputV2,
    CityCognitionRequestV2,
    CityCognitionResultV2,
    CityProviderAttemptV2,
    CityProviderRepairContextV2,
    CityProviderRepairReasonV2,
    CityProviderUsageV2,
)
from adlife.core.domain.person import (
    contains_labelled_secret_member,
    contains_provider_secret_text,
    contains_sensitive_text,
)
from adlife.core.domain.serialization import canonical_json

_Outcome = CityProviderAttemptV2 | Literal["protocol", "internal"]
_ENVELOPE_FIELDS = frozenset(
    {"id", "object", "created", "model", "choices", "usage", "system_fingerprint", "service_tier"}
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
_JSON_ESCAPE = re.compile(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')
_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_BIDI = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"})


class _InvalidJson(ValueError):
    pass


def _strict_json(text: str) -> dict[str, object]:
    def constant(_value: str) -> None:
        raise _InvalidJson

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _InvalidJson
            result[key] = value
        return result

    def finite(raw: str) -> float:
        result = float(raw)
        if not math.isfinite(result):
            raise _InvalidJson
        return result

    try:
        value: object = json.loads(
            text, parse_constant=constant, parse_float=finite, object_pairs_hook=unique
        )
        if not isinstance(value, dict):
            raise _InvalidJson
        # Bound nesting and reject malformed Unicode before selecting any fields.
        canonical_json(value).encode("utf-8")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _InvalidJson from None


def _strings(value: object) -> Iterator[str]:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _decode_escapes(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group()[1:]
        return chr(int(value[1:], 16)) if value.startswith("u") else _SIMPLE_ESCAPES[value]

    return _JSON_ESCAPE.sub(replace, text)


def _unsafe(text: str, credential_present: Callable[[str | bytes], bool]) -> bool:
    for _ in range(4):
        # Raw JSON and opaque protocol metadata may contain legitimate numbers.
        # This screen is exclusively for credentials and labelled/provider secrets.
        if credential_present(text) or contains_provider_secret_text(text):
            return True
        decoded = _decode_escapes(text)
        if decoded == text:
            return False
        text = decoded
    return True


def _invalid(reason: CityProviderRepairReasonV2) -> CityProviderAttemptV2:
    return CityProviderAttemptV2.model_validate(
        {
            "schema_version": 2,
            "status": "invalid-result",
            "result": None,
            "usage": None,
            "repair_context": {
                "schema_version": 2,
                "reason_code": reason,
                "safe_excerpt": "The answer failed bounded signal validation.",
            },
        }
    )


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_CITY_COGNITION_USAGE_INTEGER


def _wire_text(value: object, *, nullable: bool = False) -> bool:
    if value is None and nullable:
        return True
    return (
        isinstance(value, str)
        and bool(value)
        and not any(
            ord(c) < 32
            or 127 <= ord(c) < 160
            or 0xD800 <= ord(c) <= 0xDFFF
            or c in {"\u2028", "\u2029"}
            or bidirectional(c) in _BIDI
            for c in value
        )
    )


def _content_and_usage(envelope: Mapping[str, object]) -> tuple[str, CityProviderUsageV2 | None]:
    """V2-only envelope admission; completion must explicitly finish with stop."""
    if set(envelope) - _ENVELOPE_FIELDS:
        raise CityProviderProtocolError
    if "object" in envelope and envelope["object"] != "chat.completion":
        raise CityProviderProtocolError
    for field in ("id", "model", "system_fingerprint", "service_tier"):
        if field in envelope and not _wire_text(
            envelope[field], nullable=field in {"system_fingerprint", "service_tier"}
        ):
            raise CityProviderProtocolError
    if "created" in envelope and not _integer(envelope["created"]):
        raise CityProviderProtocolError
    choices = envelope.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise CityProviderProtocolError
    choice = choices[0]
    if (
        not isinstance(choice, dict)
        or set(choice) - _CHOICE_FIELDS
        or choice.get("finish_reason") != "stop"
    ):
        raise CityProviderProtocolError
    if "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0):
        raise CityProviderProtocolError
    if choice.get("logprobs") is not None:
        raise CityProviderProtocolError
    message = choice.get("message")
    if not isinstance(message, dict) or set(message) - _MESSAGE_FIELDS:
        raise CityProviderProtocolError
    if ("role" in message and message["role"] != "assistant") or message.get("refusal") is not None:
        raise CityProviderProtocolError
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise CityProviderProtocolError
    raw = envelope.get("usage")
    usage = None
    if raw is not None:
        if not isinstance(raw, dict) or set(raw) - _USAGE_FIELDS:
            raise CityProviderProtocolError
        prompt, completion, total = (
            raw.get("prompt_tokens"),
            raw.get("completion_tokens"),
            raw.get("total_tokens"),
        )
        if not _integer(prompt) or not _integer(completion):
            raise CityProviderProtocolError
        assert isinstance(prompt, int) and isinstance(completion, int)
        if total is not None and (not _integer(total) or total != prompt + completion):
            raise CityProviderProtocolError
        for field, allowed in (
            ("prompt_tokens_details", {"cached_tokens", "audio_tokens"}),
            (
                "completion_tokens_details",
                {
                    "reasoning_tokens",
                    "audio_tokens",
                    "accepted_prediction_tokens",
                    "rejected_prediction_tokens",
                },
            ),
        ):
            details = raw.get(field)
            if details is not None and (
                not isinstance(details, dict)
                or set(details) - allowed
                or any(v is not None and not _integer(v) for v in details.values())
            ):
                raise CityProviderProtocolError
        usage = CityProviderUsageV2(
            schema_version=2, prompt_tokens=prompt, completion_tokens=completion
        )
    return content, usage


class CityOpenAICompatibleProviderV2:
    """One bounded provider with no simulation authority and no raw-body retention."""

    __slots__ = ("_closed", "_cognition_input", "_endpoint", "_transport")

    def __init__(
        self,
        *,
        endpoint_policy: CityEndpointPolicy,
        endpoint: BoundProviderEndpoint,
        cognition_input: CityCognitionInputV2,
        credential: ResolvedCredential,
        network_backend_factory: Callable[[], httpcore.AsyncNetworkBackend] | None = None,
    ) -> None:
        failure = ""
        try:
            self._initialize(
                endpoint_policy=endpoint_policy,
                endpoint=endpoint,
                cognition_input=cognition_input,
                credential=credential,
                network_backend_factory=network_backend_factory,
            )
            return
        except CityEndpointBindingError:
            failure = "binding"
        except ValueError:
            failure = "input"
        except TypeError:
            failure = "factory"
        # Forged endpoint/credential metadata can itself contain a secret. Discard
        # the complete initialization frame, including the uninitialized instance.
        del self, endpoint_policy, endpoint, cognition_input, credential, network_backend_factory
        if failure == "binding":
            raise CityEndpointBindingError from None
        if failure == "input":
            raise ValueError("city cognition provider input failed validation") from None
        raise TypeError("network_backend_factory must be callable") from None

    def _initialize(
        self,
        *,
        endpoint_policy: CityEndpointPolicy,
        endpoint: BoundProviderEndpoint,
        cognition_input: CityCognitionInputV2,
        credential: ResolvedCredential,
        network_backend_factory: Callable[[], httpcore.AsyncNetworkBackend] | None,
    ) -> None:
        checked_input = None
        if (
            type(endpoint_policy) is not CityEndpointPolicy
            or type(endpoint) is not BoundProviderEndpoint
        ):
            raise CityEndpointBindingError from None
        try:
            if type(cognition_input) is CityCognitionInputV2:
                checked_input = CityCognitionInputV2.model_validate(
                    cognition_input.model_dump(mode="python", warnings="none")
                )
        except (AttributeError, TypeError, ValueError):
            pass
        if checked_input is None:
            del cognition_input, credential
            raise ValueError("city cognition provider input failed validation") from None
        profile, settings = checked_input.provider_profile, checked_input.effective_settings
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
        with suppress(AttributeError, TypeError, ValueError):
            credential_is_bound = (
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
            )
        if not credential_is_bound:
            raise CityEndpointBindingError from None
        self._endpoint, self._cognition_input, self._closed = endpoint, checked_input, False
        self._transport = CityProviderHttpTransport(
            endpoint_policy=endpoint_policy,
            endpoint=endpoint,
            credential=credential,
            attempt_timeout_seconds=settings.attempt_timeout_seconds,
            max_content_length_bytes=checked_input.max_wire_response_bytes,
            max_wire_response_bytes=checked_input.max_wire_response_bytes,
            max_decoded_response_bytes=checked_input.max_wire_response_bytes,
            network_backend_factory=network_backend_factory,
        )

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} profile_id={self._endpoint.profile_id!r} "
            f"revision={self._endpoint.profile_revision_sha256!r} "
            f"credential=[redacted] closed={self._closed}>"
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

    async def call(self, request: CityCognitionRequestV2) -> CityProviderAttemptV2:
        payload = None
        with suppress(ValueError):
            payload = build_city_cognition_payload_v2(request, self._cognition_input)
        if payload is None:
            del request
            raise ValueError("city cognition V2 prompt contracts failed validation") from None
        return await self._post(request, payload)

    async def repair_call(
        self, request: CityCognitionRequestV2, *, context: CityProviderRepairContextV2
    ) -> CityProviderAttemptV2:
        payload = None
        with suppress(ValueError):
            payload = build_city_cognition_repair_payload_v2(
                request, self._cognition_input, context=context
            )
        del context
        if payload is None:
            del request
            raise ValueError("city cognition V2 prompt contracts failed validation") from None
        return await self._post(request, payload)

    async def _post(
        self, request: CityCognitionRequestV2, payload: Mapping[str, object]
    ) -> CityProviderAttemptV2:
        if self._closed:
            raise CityProviderUnavailableError from None
        body = await self._transport.post_json(payload)
        parsed = self._parse_attempt(request, body)
        body = b""
        if parsed == "protocol":
            raise CityProviderProtocolError from None
        if parsed == "internal":
            raise CityProviderTransportError from None
        return parsed

    def _parse_attempt(self, request: CityCognitionRequestV2, body: bytes) -> _Outcome:
        outcome: _Outcome = "internal"
        try:
            outcome = self._parse_untrusted(request, body)
        except CityProviderProtocolError:
            outcome = "protocol"
        except Exception:
            pass
        return outcome

    def _parse_untrusted(
        self, request: CityCognitionRequestV2, body: bytes
    ) -> CityProviderAttemptV2:
        present = self._transport.contains_bound_credential
        if present(body):
            return _invalid("unsafe-content")
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _invalid("invalid-json")
        if _unsafe(text, present):
            return _invalid("unsafe-content")
        try:
            envelope = _strict_json(text)
        except _InvalidJson:
            return _invalid("invalid-json")
        if not isinstance(envelope, dict):
            return _invalid("invalid-json")
        if contains_labelled_secret_member(envelope) or any(
            _unsafe(s, present) for s in _strings(envelope)
        ):
            return _invalid("unsafe-content")
        content, usage = _content_and_usage(envelope)
        try:
            document = _strict_json(content)
        except _InvalidJson:
            # V2 has no accepted free-text result fields. Sensitive-persona
            # heuristics apply only to a rejected, unstructured prose answer.
            return _invalid(
                "unsafe-content" if contains_sensitive_text(content) else "invalid-json"
            )
        if not isinstance(document, dict):
            return _invalid("invalid-schema")
        if contains_labelled_secret_member(document) or any(
            _unsafe(s, present) for s in _strings(document)
        ):
            return _invalid("unsafe-content")
        try:
            result = CityCognitionResultV2.model_validate_json(canonical_json(document))
            # Do not trust a constructed model or an existing instance at either boundary.
            result = CityCognitionResultV2.model_validate(
                result.model_dump(mode="python", warnings="none")
            )
            if result.request_id != request.request_id:
                return _invalid("request-id-mismatch")
            return CityProviderAttemptV2.model_validate(
                {
                    "schema_version": 2,
                    "status": "valid",
                    "result": result.model_dump(mode="python", warnings="none"),
                    "repair_context": None,
                    "usage": usage.model_dump(mode="python", warnings="none") if usage else None,
                }
            )
        except (ValueError, TypeError):
            return _invalid("invalid-schema")


__all__ = ["CityOpenAICompatibleProviderV2"]
