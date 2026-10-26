"""Strict bounded-signal admission over the shared credential-bound transport."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from types import MappingProxyType
from typing import Literal, Self
from unicodedata import bidirectional

import httpcore

from adlife.adapters.cognition.city_credentials import (
    LOCAL_CREDENTIAL_SOURCES,
    REMOTE_CREDENTIAL_SOURCES,
    ResolvedCredential,
    SecretBuffer,
)
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
    validated_check_attempt_timeout_seconds,
)
from adlife.core.domain.city_cognition import MAX_CITY_COGNITION_USAGE_INTEGER
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionInputV2,
    CityCognitionRequestV2,
    CityCognitionResultV2,
    CityCognitionResultV3,
    CityProviderAttemptV2,
    CityProviderRepairContextV2,
    CityProviderRepairReasonV2,
    CityProviderUsageV2,
)
from adlife.core.domain.person import (
    contains_credential_assignment_text,
    contains_labelled_secret_member,
    contains_provider_secret_text,
    contains_sensitive_text,
)
from adlife.core.domain.serialization import canonical_json

_Outcome = CityProviderAttemptV2 | Literal["protocol", "internal"]
# "length" means the answer reached the frozen per-attempt output cap. Its text is still
# validated like any other answer, so it can never be accepted for less than "stop" is.
_FINISH_REASONS = ("stop", "length")
# A vLLM stop string is operator configuration, never model output, and stays short.
_MAX_STOP_REASON_CHARACTERS = 256
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


def _unsafe(
    text: str,
    credential_present: Callable[[str | bytes], bool],
    secret_text: Callable[[str], bool] = contains_provider_secret_text,
) -> bool:
    for _ in range(4):
        # Raw JSON and opaque protocol metadata may contain legitimate numbers.
        # This screen is exclusively for credentials and labelled/provider secrets.
        if credential_present(text) or secret_text(text):
            return True
        decoded = _decode_escapes(text)
        if decoded == text:
            return False
        text = decoded
    return True


def _invalid(
    reason: CityProviderRepairReasonV2, usage: CityProviderUsageV2 | None = None
) -> CityProviderAttemptV2:
    # Usage is kept once the envelope parsed: the provider charged for the answer.
    return CityProviderAttemptV2.model_validate(
        {
            "schema_version": 2,
            "status": "invalid-result",
            "result": None,
            "usage": None if usage is None else usage.model_dump(mode="python", warnings="none"),
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


def _null(value: object) -> bool:
    return value is None


def _null_or_text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _null_or_object(value: object) -> bool:
    return value is None or isinstance(value, dict)


def _null_or_empty_list(value: object) -> bool:
    return value is None or (isinstance(value, list) and not value)


def _null_or_token_ids(value: object) -> bool:
    return value is None or (isinstance(value, list) and all(_integer(item) for item in value))


def _null_or_stop_reason(value: object) -> bool:
    """Null, the token ID, or the bounded stop string that ended the answer."""
    return (
        value is None
        or _integer(value)
        or (isinstance(value, str) and len(value) <= _MAX_STOP_REASON_CHARACTERS)
    )


def _null_or_integer_tree(value: object) -> bool:
    """Null, or arbitrarily nested lists of nonnegative integers (no bool or float)."""
    if value is None:
        return True
    if not isinstance(value, list):
        return False
    pending: list[object] = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, list):
            pending.extend(current)
        elif not _integer(current):
            return False
    return True


# A vLLM server, including one behind the GapGPT gateway, adds these members to the
# OpenAI shape. Each is admitted only as null or the type vLLM sends. None is parsed,
# interpreted, or retained: reasoning text, the echoed prompt, and the stop token or stop
# string that ended an answer (vLLM reports a generation-config end token such as Qwen's
# as an integer ID) are inert, and every string in them has already passed a credential and
# secret screen: the whole-body one, or for the free text of _INERT_ENVELOPE_MEMBERS and
# _INERT_MESSAGE_MEMBERS the credential-assignment one. A real function call, audio part, or
# tool call stays a protocol error.
#
# Other gateways add their own inert members (OpenRouter's ``provider`` and
# ``native_finish_reason``, LM Studio's ``stats``, a usage ``cost``). Members this adapter
# does not know are ignored rather than refused: only ``content`` and the token counts are
# ever read, the whole body has already passed the size, credential and secret screens, and
# the admitted members above are still type-checked whenever they appear.
_VLLM_ENVELOPE_MEMBERS: Mapping[str, Callable[[object], bool]] = MappingProxyType(
    {
        "prompt_logprobs": _null,
        "prompt_token_ids": _null_or_token_ids,
        "prompt_text": _null_or_text,
        "kv_transfer_params": _null,
        "ec_transfer_params": _null,
        "metrics": _null_or_object,
    }
)
_VLLM_CHOICE_MEMBERS: Mapping[str, Callable[[object], bool]] = MappingProxyType(
    {
        "stop_reason": _null_or_stop_reason,
        "token_ids": _null_or_token_ids,
        "routed_experts": _null_or_integer_tree,
    }
)
_VLLM_MESSAGE_MEMBERS: Mapping[str, Callable[[object], bool]] = MappingProxyType(
    {
        "annotations": _null_or_empty_list,
        "audio": _null,
        "function_call": _null,
        "tool_calls": _null_or_empty_list,
        "reasoning": _null_or_text,
        "reasoning_content": _null_or_text,
    }
)


# The free-text members nobody parses or keeps: the echoed prompt, server metrics, and the
# model's reasoning (OpenRouter's unadmitted ``reasoning_details`` carries the same text).
# An in-character model reasons in everyday words ("I only buy basic groceries now"), and
# the raw-body screen's prose heuristics would throw away a valid answer the provider has
# already charged for. Their strings get the bound-credential check and the
# credential-assignment screen instead, which still refuse the provider's own key, an email,
# a labelled or header assignment, and every key shape.
_INERT_ENVELOPE_MEMBERS = ("prompt_text", "metrics")
_INERT_MESSAGE_MEMBERS = ("reasoning", "reasoning_content", "reasoning_details")


def _without_inert_members(
    envelope: Mapping[str, object],
) -> tuple[dict[str, object], list[object]]:
    """Split a parsed envelope into the members that are read and the inert free text."""
    screened = {key: value for key, value in envelope.items() if key not in _INERT_ENVELOPE_MEMBERS}
    inert = [envelope[key] for key in _INERT_ENVELOPE_MEMBERS if key in envelope]
    choices = screened.get("choices")
    if isinstance(choices, list):
        kept: list[object] = []
        for choice in choices:
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict):
                inert.extend(message[key] for key in _INERT_MESSAGE_MEMBERS if key in message)
                message = {
                    key: value
                    for key, value in message.items()
                    if key not in _INERT_MESSAGE_MEMBERS
                }
                choice = {**choice, "message": message}
            kept.append(choice)
        screened["choices"] = kept
    return screened, inert


def _without_thoughts(envelope: dict[str, object]) -> tuple[dict[str, object], list[object]]:
    """Set apart the inner thoughts a thoughts-prompt answer carries in its content.

    A thought is the person's everyday words ("I only buy basic groceries now"), which the
    raw-body screen's prose heuristics would read as a credential and so discard the whole
    answer. The thoughts get their own screens instead: the bound-credential check here,
    and the thought model's persona-text screens, which drop just the thought.
    """
    choices = envelope.get("choices")
    if not isinstance(choices, list):
        return envelope, []
    thoughts: list[object] = []
    kept: list[object] = []
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            try:
                document = _strict_json(content)
            except _InvalidJson:
                document = None
            if isinstance(document, dict) and "thoughts" in document and isinstance(message, dict):
                thoughts.append(document.pop("thoughts"))
                content = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                choice = {**choice, "message": {**message, "content": content}}
        kept.append(choice)
    return {**envelope, "choices": kept}, thoughts


def _admit_members(
    container: Mapping[str, object],
    vendor: Mapping[str, Callable[[object], bool]],
) -> None:
    """Refuse an admitted vendor member of another type; ignore members nobody reads.

    Every standard OpenAI member is checked by the caller where it is read.
    """
    if not all(admitted(container[name]) for name, admitted in vendor.items() if name in container):
        raise CityProviderProtocolError


def _content_and_usage(
    envelope: Mapping[str, object],
) -> tuple[str | None, CityProviderUsageV2 | None]:
    """V2-only envelope admission; a completion must finish with stop or length.

    ``length`` means the answer reached the frozen per-attempt output cap, so it may be a
    complete object followed by runaway whitespace or an object that never closed. Its text
    is returned for the same strict validation as a ``stop`` answer, and ``None`` means a
    truncated answer carries no text at all (a model that spent the cap thinking). Neither
    is a protocol mismatch, so the caller can return a repairable invalid result.
    """
    _admit_members(envelope, _VLLM_ENVELOPE_MEMBERS)
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
    if not isinstance(choice, dict):
        raise CityProviderProtocolError
    _admit_members(choice, _VLLM_CHOICE_MEMBERS)
    finish_reason = choice.get("finish_reason")
    # Tuple membership compares by equality, so an unhashable value is refused, not raised.
    if finish_reason not in _FINISH_REASONS:
        raise CityProviderProtocolError
    if "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0):
        raise CityProviderProtocolError
    if choice.get("logprobs") is not None:
        raise CityProviderProtocolError
    message = choice.get("message")
    if not isinstance(message, dict):
        raise CityProviderProtocolError
    _admit_members(message, _VLLM_MESSAGE_MEMBERS)
    if ("role" in message and message["role"] != "assistant") or message.get("refusal") is not None:
        raise CityProviderProtocolError
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise CityProviderProtocolError
    answered = isinstance(content, str) and bool(content.strip())
    if finish_reason == "stop" and not answered:
        raise CityProviderProtocolError
    raw = envelope.get("usage")
    usage = None
    if raw is not None:
        # Gateways add their own usage members (a cost, a cache flag); only counts are read.
        if not isinstance(raw, dict):
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
                or any(
                    details[name] is not None and not _integer(details[name])
                    for name in allowed
                    if name in details
                )
            ):
                raise CityProviderProtocolError
        usage = CityProviderUsageV2(
            schema_version=2, prompt_tokens=prompt, completion_tokens=completion
        )
    return (content if answered else None), usage


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
        check_attempt_timeout_seconds: int | None = None,
    ) -> None:
        """Bind one frozen input; only an explicit compatibility check may extend its deadline.

        ``check_attempt_timeout_seconds`` replaces the frozen per-attempt transport
        timeout for this provider instance alone. Funded runs never pass it, so their
        attempts keep the frozen effective settings.
        """
        failure = ""
        try:
            self._initialize(
                endpoint_policy=endpoint_policy,
                endpoint=endpoint,
                cognition_input=cognition_input,
                credential=credential,
                network_backend_factory=network_backend_factory,
                check_attempt_timeout_seconds=check_attempt_timeout_seconds,
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
        del check_attempt_timeout_seconds
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
        check_attempt_timeout_seconds: int | None,
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
                    (
                        endpoint.trust_class == "local"
                        and credential.source in LOCAL_CREDENTIAL_SOURCES
                    )
                    or (
                        endpoint.trust_class == "remote"
                        and credential.source in REMOTE_CREDENTIAL_SOURCES
                    )
                )
            )
        if not credential_is_bound:
            raise CityEndpointBindingError from None
        check_timeout = validated_check_attempt_timeout_seconds(check_attempt_timeout_seconds)
        self._endpoint, self._cognition_input, self._closed = endpoint, checked_input, False
        self._transport = CityProviderHttpTransport(
            endpoint_policy=endpoint_policy,
            endpoint=endpoint,
            credential=credential,
            attempt_timeout_seconds=(
                settings.attempt_timeout_seconds if check_timeout is None else check_timeout
            ),
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
        try:
            envelope = _strict_json(text)
        except _InvalidJson:
            # No members to set apart: the whole raw text keeps the raw-body screen.
            return _invalid("unsafe-content" if _unsafe(text, present) else "invalid-json")
        if not isinstance(envelope, dict):
            return _invalid("invalid-json")
        screened, inert = _without_inert_members(envelope)
        thoughts_answer = self._cognition_input.causal_contract.result_schema_version == 3
        thoughts: list[object] = []
        if thoughts_answer:
            screened, thoughts = _without_thoughts(screened)
            if any(present(text) for text in _strings(thoughts)):
                return _invalid("unsafe-content")
        # The members that are read keep the whole-body screen, re-serialized in their
        # original order, so a label and a value in sibling members are still seen.
        remainder = json.dumps(screened, ensure_ascii=False, separators=(",", ":"))
        if (
            _unsafe(remainder, present)
            or contains_labelled_secret_member(envelope)
            or any(_unsafe(s, present) for s in _strings(screened))
            or any(
                _unsafe(s, present, contains_credential_assignment_text) for s in _strings(inert)
            )
        ):
            return _invalid("unsafe-content")
        # Vendor-member type admission still sees the whole envelope.
        content, usage = _content_and_usage(envelope)
        if content is None:
            # A length-truncated answer with no text at all; the schema repair may recover.
            return _invalid("invalid-json", usage)
        try:
            document = _strict_json(content)
        except _InvalidJson:
            # V2 has no accepted free-text result fields. Sensitive-persona
            # heuristics apply only to a rejected, unstructured prose answer.
            return _invalid(
                "unsafe-content" if contains_sensitive_text(content) else "invalid-json", usage
            )
        if not isinstance(document, dict):
            return _invalid("invalid-schema", usage)
        labels = {key: value for key, value in document.items() if key != "thoughts"}
        if contains_labelled_secret_member(labels if thoughts_answer else document) or any(
            _unsafe(s, present) for s in _strings(labels if thoughts_answer else document)
        ):
            return _invalid("unsafe-content", usage)
        try:
            kept = None
            if thoughts_answer:
                answer = CityCognitionResultV3.model_validate_json(canonical_json(document))
                result = answer.signal_result()
                languages = request.causal_identity.thought_languages or ()
                kept = answer.kept_thoughts(languages)
            else:
                result = CityCognitionResultV2.model_validate_json(canonical_json(document))
            # Do not trust a constructed model or an existing instance at either boundary.
            result = CityCognitionResultV2.model_validate(
                result.model_dump(mode="python", warnings="none")
            )
            if result.request_id != request.request_id:
                return _invalid("request-id-mismatch", usage)
            attempt: dict[str, object] = {
                "schema_version": 2,
                "status": "valid",
                "result": result.model_dump(mode="python", warnings="none"),
                "repair_context": None,
                "usage": usage.model_dump(mode="python", warnings="none") if usage else None,
            }
            if kept is not None:
                attempt["thoughts"] = kept.model_dump(mode="python", warnings="none")
            return CityProviderAttemptV2.model_validate(attempt)
        except (ValueError, TypeError):
            return _invalid("invalid-schema", usage)


__all__ = ["CityOpenAICompatibleProviderV2"]
