"""The OpenAI-compatible cognition provider: one HTTP call, bounded and screened.

This module is the only place in the repository that speaks to a network endpoint, so
it carries the whole network boundary and nothing else. Retries, budgets, caching and
the terminal rule fallback belong to :mod:`adlife.adapters.cognition.service`; a single
:meth:`OpenAICompatibleProvider.answer` is one attempt at one request.

WHAT THIS MODULE PROMISES ABOUT CREDENTIALS
-------------------------------------------
Two claims are STRUCTURAL and hold absolutely.

* The credential is never read from a configuration file or a command-line argument.
  :func:`resolve_api_key` accepts it from the environment variable specification
  section 6.4 names, or from a hidden prompt the caller supplies, and nothing else.
  :class:`~adlife.config.models.ProviderSettings` has no field to put one in.
* The credential lives only in the transport's request headers. It is not stored on the
  provider, it is not in :class:`~adlife.core.ports.cognition.ProviderMetadata` - which
  has no credential field and whose ``base_url`` validator strips userinfo, query and
  fragment - and a base URL carrying userinfo is refused outright rather than silently
  stripped. That claim is only as strong as the transport, so a remote endpoint reached
  over plain ``http`` is refused too: see :class:`InsecureProviderUrl`.

Every other claim is a SCREEN, and screens are best effort. Each failure this module
raises builds its own message from a status code, a timeout, an exception class name and
the redacted endpoint, rather than echoing a transport exception; every echoed body and
every echoed provider-chosen field NAME passes through
:func:`~adlife.core.ports.cognition.redact_provider_body` - the published redactor; this
module never writes a second one; and redaction runs BEFORE any bound, because a bound
checked first chops a credential into a fragment no pattern matches afterwards. A
rejected answer's underlying ``pydantic.ValidationError`` is suppressed with ``from
None`` rather than chained, because such an error renders the input value it rejected
and an exception chain is rendered by any traceback. The property that follows, and the
only one asserted here, is the repository-wide one:

    EVERY CREDENTIAL SHAPE THE REPOSITORY CAN NAME IS REMOVED FROM, OR REFUSED ENTRY TO,
    A MESSAGE, A LOG RECORD OR A STORED RECORD THIS MODULE PRODUCES.

It is NOT "a credential cannot leak". An opaque token under an unlabelled field in a
provider's error body is indistinguishable from an order number. The named shapes are
the tables in :mod:`adlife.core.domain.person`, and the one surface deliberately left
unscreened is :attr:`ProviderCallError.raw_response`, which is kept exactly as received
so that whatever stores it can redact it once. ``tests/security/`` pins the rest: each
corpus row is replayed down the error message, the repair prompt, the resolution, the
log records, the undefined-field warning, the echo bound and a formatted traceback.

FAILURE CONTRACT
----------------
Specification section 12 requires that provider failures never abort a run. Every
failure path out of this module therefore raises a :class:`ProviderCallError`, which is
a :class:`~adlife.core.ports.cognition.CognitionError`: no bare ``httpx`` exception, no
bare ``pydantic.ValidationError``, no ``KeyError`` from a malformed envelope. A
``TypeError`` for a wrongly typed argument is deliberately NOT translated, following the
precedent set in :mod:`adlife.adapters.cognition.rules`: that is a defect in a caller,
not a provider failure.
"""

from __future__ import annotations

import getpass
import json
import logging
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import ClassVar, Final, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from adlife.adapters.cognition.prompts import (
    build_messages,
    build_repair_messages,
    cognition_json_schema,
    prompt_template_sha256,
)
from adlife.core.domain.person import REDACTION_PLACEHOLDER
from adlife.core.ports.cognition import (
    MAX_TOKEN_COUNT as _MAX_TOKEN_COUNT,
)
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionError,
    CognitionRequest,
    CognitionResult,
    FallbackReason,
    ProviderKind,
    ProviderMetadata,
    ProviderUsage,
    SamplingSettings,
    redact_provider_body,
)

logger = logging.getLogger(__name__)

ADLIFE_API_KEY_VARIABLE: Final = "ADLIFE_API_KEY"
"""The one environment variable specification section 6.4 names."""

LOCAL_API_KEY_PLACEHOLDER: Final = "ollama"
"""Local endpoints ignore the credential but still require a non-empty header value."""

HIDDEN_API_KEY_PROMPT: Final[Callable[[str], str]] = getpass.getpass
"""The documented hidden prompt. It is never called unless a caller passes it in."""

DEFAULT_LOCAL_BASE_URL: Final = "http://127.0.0.1:11434/v1"
"""Specification section 6.3's default local endpoint."""

LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost"})

CHAT_COMPLETIONS_PATH: Final = "chat/completions"

MAX_ECHOED_BODY_CHARS: Final = 500
"""How much of a failing body a diagnostic message may carry, after redaction."""

ECHOED_BODY_TRUNCATION_MARKER: Final = "..."
"""What a deterministically shortened diagnostic body ends with."""

_EXCERPT_SETTLING_PASSES: Final = 4
"""Redact-then-bound passes allowed before an echoed body is dropped entirely."""

MAX_ECHOED_FIELD_NAMES: Final = 10
"""How many undefined answer-field NAMES one warning may list.

A provider names its own JSON keys, so a key is provider-controlled text of unbounded
length and unbounded count. The count is bounded here and the text is redacted; the
warning still reports how many there were, because that number is the useful part.
"""

MAX_TOKEN_COUNT: Final = _MAX_TOKEN_COUNT
"""Re-exported from the contract, so a screen here and the bound there cannot drift."""

ProviderMode = Literal["local", "remote"]


class ProviderConfigurationError(CognitionError):
    """Raised when a provider cannot be configured safely. It echoes no value."""


class MissingApiKey(ProviderConfigurationError):
    """Raised when no credential is available. The message names the source, not a value."""


class InsecureProviderUrl(ProviderConfigurationError):
    """Raised when a base URL would expose the credential.

    Two shapes are refused, and neither message names any part of the URL.

    * A URL carrying userinfo. It is refused rather than quietly stripped: a caller who
      put a credential in a URL needs to know it was not used, and a silently stripped
      URL fails later as an opaque authentication error.
    * Plain ``http`` to a host that is not loopback. The one structural claim this module
      makes about the credential is that it lives only in the transport request headers,
      and that claim is worth exactly as much as the transport. An ``Authorization:
      Bearer`` header sent in cleartext across a network is readable by everything on the
      path. Loopback keeps plain ``http``, because specification section 6.3 default
      local endpoint is ``http://127.0.0.1:11434/v1`` and that traffic never leaves the
      machine.
    """


class ProviderCallError(CognitionError):
    """One failed attempt at one cognition request.

    ``fallback_reason`` is the enum a service stamps on its fallback, so the mapping
    from failure to reason lives with the failure instead of in a conditional ladder
    that can silently lose a case. ``retryable`` says whether asking again could help.
    """

    fallback_reason: ClassVar[FallbackReason] = "provider-unavailable"
    default_retryable: ClassVar[bool] = True

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        """Redact the message here, so no composer of one can forget to.

        A failure message is the single most likely place for a credential to escape:
        an endpoint that rejects a key usually echoes it back in the error body, and
        that body is what a diagnostic quotes. Screening at construction makes the
        property structural for the whole hierarchy rather than a habit at each raise
        site. It is the published redactor; this module writes no second one.
        """
        super().__init__(redact_provider_body(message))
        self.raw_response = raw_response
        """The body exactly as received, unredacted and unbounded.

        It is kept raw on purpose: :class:`~adlife.core.ports.cognition.CognitionRecord`
        and :class:`~adlife.adapters.cognition.service.CognitionResolution` redact and
        bound it when they store it, and pre-redacting here would redact twice and
        change what the repair prompt is asked to fix. Never log this attribute; log
        ``str(error)``, which is redacted.
        """
        self.retryable = self.default_retryable if retryable is None else retryable


class ProviderTimeout(ProviderCallError):
    """The endpoint did not answer inside the configured timeout."""

    fallback_reason: ClassVar[FallbackReason] = "timeout"


class ProviderUnavailable(ProviderCallError):
    """The endpoint could not be reached at all."""

    fallback_reason: ClassVar[FallbackReason] = "provider-unavailable"


class ProviderRateLimited(ProviderCallError):
    """The endpoint answered HTTP 429."""

    fallback_reason: ClassVar[FallbackReason] = "rate-limited"


class ProviderHttpError(ProviderCallError):
    """The endpoint answered with an error status other than 429.

    Only a server-side status is retryable. A 401 or a 404 does not become correct by
    being asked again, and retrying one spends the run's remaining attempts for nothing.
    """

    fallback_reason: ClassVar[FallbackReason] = "http-error"


class InvalidProviderResponse(ProviderCallError):
    """The endpoint answered, but not with a usable cognition result.

    This is never retried with the same prompt - a deterministic schema failure repeats -
    and is the only failure that earns specification section 12's one repair attempt.
    """

    fallback_reason: ClassVar[FallbackReason] = "invalid-response"
    default_retryable: ClassVar[bool] = False


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """One completed provider exchange: the answer and the body it was parsed from.

    ``raw_response`` is ``None`` for a provider that has no wire body to show - every
    offline provider returns a validated result rather than text - and is the exact
    unredacted body otherwise, so the consumer that stores it owns the redaction.
    """

    answer: CognitionAnswer
    raw_response: str | None


def _numeric_bounds() -> Mapping[str, tuple[float, float]]:
    """Read every numeric bound off the answer contract itself.

    Restating the bounds here would be a second source of truth that drifts the moment
    :class:`~adlife.core.ports.cognition.CognitionResult` changes. Reading them means a
    clamp is always the contract's own clamp.
    """
    bounds: dict[str, tuple[float, float]] = {}
    for name, field in CognitionResult.model_fields.items():
        if field.annotation is not float:
            continue
        lower: float | None = None
        upper: float | None = None
        for constraint in field.metadata:
            lower = getattr(constraint, "ge", lower)
            upper = getattr(constraint, "le", upper)
        if lower is not None and upper is not None:
            bounds[name] = (float(lower), float(upper))
    return bounds


NUMERIC_BOUNDS: Final[Mapping[str, tuple[float, float]]] = _numeric_bounds()


def resolve_api_key(
    mode: ProviderMode,
    *,
    environ: Mapping[str, str] | None = None,
    prompt: Callable[[str], str] | None = None,
) -> str:
    """Resolve a provider credential from the environment or a hidden prompt.

    Specification section 6.4 allows exactly two sources, and this function implements
    both and nothing else: there is no configuration-file source and no command-line
    source, so a credential cannot reach shell history or project YAML through it.

    The function fails CLOSED. ``prompt`` defaults to ``None``, which means "do not
    ask": an unattended run raises :class:`MissingApiKey` instead of blocking forever on
    a terminal nobody is watching. A caller that can safely ask passes
    :data:`HIDDEN_API_KEY_PROMPT`. No message, error or log record here contains the
    resolved value.
    """
    if mode not in ("local", "remote"):
        raise ValueError("mode must be 'local' or 'remote'")
    source = os.environ if environ is None else environ
    configured = source.get(ADLIFE_API_KEY_VARIABLE, "").strip()
    if configured:
        return configured
    if mode == "local":
        return LOCAL_API_KEY_PLACEHOLDER
    if prompt is None:
        raise MissingApiKey(
            f"a remote cognition provider needs {ADLIFE_API_KEY_VARIABLE} in the "
            "environment, or a hidden prompt the caller supplies"
        )
    typed = prompt(f"{ADLIFE_API_KEY_VARIABLE} (input hidden): ").strip()
    if not typed:
        raise MissingApiKey("no credential was entered at the hidden prompt")
    return typed


def _provider_kind(base_url: str) -> ProviderKind:
    """Name the endpoint: a loopback host is the local provider, anything else remote."""
    host = urlsplit(base_url).hostname
    return "local-llm" if host is not None and host in LOOPBACK_HOSTS else "remote-llm"


def _excerpt(body: str) -> str:
    """Redact a failing body FIRST, then keep only enough of it to diagnose the failure.

    The order is the repository's rule, not a preference:
    :func:`~adlife.core.ports.cognition.bound_raw_provider_body` states it and
    ``prompts._bounded_echo`` follows it. A bound checked before redaction bounds the
    WRONG string - a credential straddling the cut is chopped into a fragment too short
    for the vendor pattern to match, and the later redaction in
    :meth:`ProviderCallError.__init__` leaves that fragment in the message (finding S9).

    Redaction still runs a second time at construction, and deliberately: that is what
    screens a message a later task composes without passing it through here.
    :func:`~adlife.core.ports.cognition.redact_provider_body` is idempotent, so running
    it twice removes nothing extra. Both passes are the published redactor; this module
    writes no second one.
    """
    text = body
    for _ in range(_EXCERPT_SETTLING_PASSES):
        settled = redact_provider_body(text)
        if len(settled) > MAX_ECHOED_BODY_CHARS:
            keep = MAX_ECHOED_BODY_CHARS - len(ECHOED_BODY_TRUNCATION_MARKER)
            settled = settled[:keep] + ECHOED_BODY_TRUNCATION_MARKER
        if settled == text:
            return text
        text = settled
    return REDACTION_PLACEHOLDER


def _strip_markdown_fence(content: str) -> str:
    """Unwrap a fenced answer.

    The prompt asks for no markdown, and a model that fences its JSON anyway has made a
    presentation mistake rather than a schema mistake. Unwrapping it locally is cheaper
    and more reliable than spending specification section 12's one repair attempt on it.
    """
    text = content.strip()
    if not text.startswith("```") or not text.endswith("```"):
        return text
    inner = text[3:-3]
    newline = inner.find("\n")
    if newline != -1 and inner[:newline].strip().isalpha():
        inner = inner[newline + 1 :]
    return inner.strip()


def _reject_json_constant(name: str) -> float:
    raise ValueError(f"a cognition answer must not contain the JSON constant {name}")


def extract_message_content(payload: object) -> str:
    """Read the assistant message out of a chat-completion envelope.

    Every shape error raises :class:`InvalidProviderResponse` rather than ``KeyError``,
    ``IndexError`` or ``TypeError``, because a malformed envelope is a provider failure
    that a run has to survive.
    """
    if not isinstance(payload, Mapping):
        raise InvalidProviderResponse("the provider answer was not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InvalidProviderResponse("the provider answer carried no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise InvalidProviderResponse("the provider answer's first choice was not an object")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise InvalidProviderResponse("the provider answer's first choice carried no message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise InvalidProviderResponse("the provider answer carried no message content")
    return content


def extract_token_usage(payload: object) -> tuple[int, int]:
    """Read the prompt and completion token counts, or report zero.

    An absent, malformed, negative or impossibly large count is reported as zero rather
    than estimated: a fabricated cost would be presented to a reader as a measurement.

    The magnitude screen is not decoration. Token counts are the only number this module
    takes from a provider body that is not clamped against a documented range read off
    the contract, and an unbounded one reaches :class:`ProviderUsage`, the persisted
    cache record and the report metrics. A value above ``2**63-1`` makes a SQLite
    ``INTEGER`` write raise ``OverflowError``, which is not a
    :class:`~adlife.core.ports.cognition.CognitionError` and would abort a run - the one
    outcome specification section 12 forbids. Clamping to the ceiling would be worse than
    reporting zero: it would present a number nobody measured as though it were counted.
    """
    if not isinstance(payload, Mapping):
        return (0, 0)
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return (0, 0)

    def _count(name: str) -> int:
        value = usage.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        if value < 0 or value > MAX_TOKEN_COUNT:
            return 0
        return value

    return (_count("prompt_tokens"), _count("completion_tokens"))


def coerce_cognition_result(content: str, *, request_id: str) -> CognitionResult:
    """Parse, clamp and validate one answer body against the cognition contract.

    Specification section 12 requires that numeric values are clamped after a validation
    warning is logged, so an answer that overshoots one bound is corrected rather than
    thrown away - burning the single repair attempt on an otherwise complete answer
    would drop to the rule fallback systematically. Three things are NOT clamped:

    * a non-finite number, because ``min``/``max`` on a NaN invents a value the model
      never produced, and ``1e400`` parses to infinity without ever being a JSON
      constant;
    * a boolean, which is an ``int`` in Python and would silently clamp to 0.0 or 1.0;
    * anything structural - a missing field, a wrong type, a wrong ``request_id``.

    Unknown fields are dropped with a warning instead of refused, because a tolerant
    reader is the right stance towards an external API that may add fields, and the
    contract still validates every field that is kept.
    """
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    try:
        parsed = json.loads(_strip_markdown_fence(content), parse_constant=_reject_json_constant)
    except ValueError as error:
        raise InvalidProviderResponse(
            f"the provider answer was not valid JSON: {_excerpt(str(error))}",
            raw_response=content,
        ) from error
    if not isinstance(parsed, dict):
        raise InvalidProviderResponse(
            "the provider answer was not a JSON object",
            raw_response=content,
        )

    known = set(CognitionResult.model_fields)
    unknown = sorted(set(parsed) - known)
    if unknown:
        # A provider chooses its own JSON KEYS, so a key is provider-controlled text of
        # unbounded length and unbounded count, exactly like a body. It is screened like
        # one: the published redactor first, the echo bound second, and the number of
        # names echoed capped so that one verbose endpoint cannot fill a log file.
        logger.warning(
            "cognition answer for %s carried %d undefined field(s), including %s; "
            "they were dropped",
            request_id,
            len(unknown),
            _excerpt(", ".join(unknown[:MAX_ECHOED_FIELD_NAMES])),
        )
    cleaned = {name: value for name, value in parsed.items() if name in known}

    for name, (lower, upper) in NUMERIC_BOUNDS.items():
        value = cleaned.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value):
            raise InvalidProviderResponse(
                f"the provider answer gave a non-finite value for {name}",
                raw_response=content,
            )
        clamped = min(max(float(value), lower), upper)
        if clamped != value:
            logger.warning(
                "cognition answer for %s gave %s = %r, outside [%s, %s]; clamped to %s",
                request_id,
                name,
                value,
                lower,
                upper,
                clamped,
            )
        cleaned[name] = clamped

    try:
        answer_json = json.dumps(cleaned, allow_nan=False)
    except ValueError as error:
        # A non-finite number OUTSIDE the clamped fields - inside ``grounded_reasons``,
        # say - reaches here rather than the ``isfinite`` check above, and ``json.dumps``
        # refuses it. Without this arm that refusal escapes as a bare ``ValueError``,
        # which is exactly the shape specification section 12 forbids from aborting a run.
        raise InvalidProviderResponse(
            "the provider answer carried a value that is not representable as JSON",
            raw_response=content,
        ) from error

    try:
        result = CognitionResult.model_validate_json(answer_json)
    except ValidationError as error:
        # ``from None`` rather than ``from error``, for the same reason the unusable base
        # URL is raised that way: a pydantic error renders the INPUT VALUE it rejected,
        # so a credential in a rejected field would travel out on ``__cause__`` and into
        # any formatted traceback, ``logger.exception`` call or crash report, past the
        # redaction that screens this exception own message. The count is kept, because
        # how many fields failed is the diagnostic; which values failed is not.
        raise InvalidProviderResponse(
            f"the provider answer did not match the cognition schema: "
            f"{error.error_count()} field(s) rejected",
            raw_response=content,
        ) from None
    if result.request_id != request_id:
        raise InvalidProviderResponse(
            f"the provider answered request {result.request_id} rather than {request_id}",
            raw_response=content,
        )
    return result


class OpenAICompatibleProvider:
    """Call one OpenAI-compatible chat-completions endpoint for one cognition request.

    The same class serves specification section 6.3's local endpoint and section 6.4's
    remote one; the only difference is the base URL, and the provider kind it reports
    follows from that URL rather than from a flag a caller could set wrongly.

    The credential is written once into the transport's headers and is never stored on
    the instance, never placed in :attr:`provider_metadata`, and never rendered by
    :meth:`__repr__`.
    """

    __slots__ = ("_client", "_clock", "_metadata", "_timeout_seconds", "model")

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        sampling: SamplingSettings | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(api_key, str) or not api_key.strip():
            raise MissingApiKey(
                "a cognition provider needs a non-empty credential; resolve one with "
                f"resolve_api_key() from {ADLIFE_API_KEY_VARIABLE} or a hidden prompt"
            )
        if not isinstance(base_url, str) or not base_url.strip():
            raise ProviderConfigurationError("base_url must be a non-empty string")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ProviderConfigurationError("timeout_seconds must be a positive number")
        parts = urlsplit(base_url)
        if parts.username or parts.password:
            raise InsecureProviderUrl(
                "the provider base URL must not carry a credential before its host; "
                f"supply the credential through {ADLIFE_API_KEY_VARIABLE} instead"
            )
        if parts.scheme == "http" and _provider_kind(base_url) != "local-llm":
            raise InsecureProviderUrl(
                "a remote cognition endpoint must be https: the credential is sent as an "
                "Authorization header and plain http puts it on the wire in cleartext"
            )
        try:
            metadata = ProviderMetadata(
                kind=_provider_kind(base_url),
                base_url=base_url,
                model_id=model,
                sampling=SamplingSettings() if sampling is None else sampling,
                prompt_sha256=prompt_template_sha256(),
            )
        except ValidationError:
            raise ProviderConfigurationError(
                "the provider base URL is not a usable http or https endpoint"
            ) from None
        normalized = metadata.base_url
        if normalized is None:  # pragma: no cover - a network kind always keeps its URL
            raise ProviderConfigurationError("the provider base URL could not be normalized")
        self.model = model
        self._metadata = metadata
        self._timeout_seconds = float(timeout_seconds)
        self._clock: Callable[[], float] = time.monotonic if clock is None else clock
        self._client = httpx.AsyncClient(
            base_url=normalized.rstrip("/") + "/",
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def __repr__(self) -> str:
        """Name the endpoint and the model. There is no credential to render."""
        return (
            f"{type(self).__name__}(base_url={self._metadata.base_url!r}, "
            f"model={self.model!r}, kind={self._metadata.kind!r})"
        )

    @property
    def provider_metadata(self) -> ProviderMetadata:
        """Everything that may change this provider's answers, and no credential."""
        return self._metadata

    @property
    def is_closed(self) -> bool:
        return bool(self._client.is_closed)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def call(self, request: CognitionRequest) -> ProviderCall:
        """Make one attempt and return the answer with the body it was parsed from."""
        return await self._post(request, build_messages(request))

    async def repair_call(
        self,
        request: CognitionRequest,
        *,
        invalid_content: str,
        error: str,
    ) -> ProviderCall:
        """Make specification section 12's single schema-repair attempt."""
        return await self._post(
            request,
            build_repair_messages(request, invalid_content=invalid_content, error=error),
        )

    async def evaluate(self, request: CognitionRequest) -> CognitionResult:
        return (await self.call(request)).answer.result

    async def answer(self, request: CognitionRequest) -> CognitionAnswer:
        return (await self.call(request)).answer

    async def answer_repair(
        self,
        request: CognitionRequest,
        *,
        invalid_content: str,
        error: str,
    ) -> CognitionAnswer:
        call = await self.repair_call(request, invalid_content=invalid_content, error=error)
        return call.answer

    async def _post(
        self,
        request: CognitionRequest,
        messages: tuple[dict[str, str], ...],
    ) -> ProviderCall:
        if not isinstance(request, CognitionRequest):
            raise TypeError("request must be a CognitionRequest")
        sampling = self._metadata.sampling
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": sampling.temperature,
            "top_p": sampling.top_p,
            "max_tokens": sampling.max_output_tokens,
            "messages": [dict(message) for message in messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": cognition_json_schema(),
            },
        }
        if sampling.seed is not None:
            payload["seed"] = sampling.seed

        started = self._clock()
        response = await self._send(payload)
        elapsed_ms = max(0, round((self._clock() - started) * 1000))
        self._raise_for_status(response)

        try:
            envelope = response.json()
        except ValueError as error:
            raise InvalidProviderResponse(
                "the provider answer was not JSON",
                raw_response=response.text,
            ) from error
        content = extract_message_content(envelope)
        result = coerce_cognition_result(content, request_id=request.request_id)
        prompt_tokens, completion_tokens = extract_token_usage(envelope)
        return ProviderCall(
            answer=CognitionAnswer(
                result=result,
                usage=ProviderUsage(
                    provider_kind=self._metadata.kind,
                    model_id=self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=elapsed_ms,
                    cache_hit=False,
                ),
            ),
            raw_response=content,
        )

    async def _send(self, payload: Mapping[str, object]) -> httpx.Response:
        """Post the payload, translating every transport failure into this hierarchy.

        The messages are composed here rather than taken from the transport exception:
        an httpx message carries the request URL and, for some backends, request detail,
        and composing our own is the reliable way to keep a diagnostic free of anything
        the caller configured.
        """
        endpoint = self._metadata.base_url
        try:
            return await self._client.post(CHAT_COMPLETIONS_PATH, json=dict(payload))
        except httpx.TimeoutException as error:
            raise ProviderTimeout(
                f"the cognition endpoint {endpoint} did not answer within "
                f"{self._timeout_seconds} seconds ({type(error).__name__})"
            ) from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable(
                f"the cognition endpoint {endpoint} could not be reached ({type(error).__name__})"
            ) from error

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            body = _excerpt(error.response.text)
            endpoint = self._metadata.base_url
            if status == httpx.codes.TOO_MANY_REQUESTS:
                raise ProviderRateLimited(
                    f"the cognition endpoint {endpoint} rate limited this run: {body}"
                ) from error
            raise ProviderHttpError(
                f"the cognition endpoint {endpoint} answered HTTP {status}: {body}",
                retryable=status >= 500,
            ) from error


__all__ = [
    "ADLIFE_API_KEY_VARIABLE",
    "CHAT_COMPLETIONS_PATH",
    "DEFAULT_LOCAL_BASE_URL",
    "ECHOED_BODY_TRUNCATION_MARKER",
    "HIDDEN_API_KEY_PROMPT",
    "LOCAL_API_KEY_PLACEHOLDER",
    "LOOPBACK_HOSTS",
    "MAX_ECHOED_BODY_CHARS",
    "MAX_ECHOED_FIELD_NAMES",
    "MAX_TOKEN_COUNT",
    "NUMERIC_BOUNDS",
    "InsecureProviderUrl",
    "InvalidProviderResponse",
    "MissingApiKey",
    "OpenAICompatibleProvider",
    "ProviderCall",
    "ProviderCallError",
    "ProviderConfigurationError",
    "ProviderHttpError",
    "ProviderMode",
    "ProviderRateLimited",
    "ProviderTimeout",
    "ProviderUnavailable",
    "coerce_cognition_result",
    "extract_message_content",
    "extract_token_usage",
    "resolve_api_key",
]
