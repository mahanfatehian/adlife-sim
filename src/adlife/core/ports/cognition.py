"""Adapter-free cognition provider contracts.

This module is the only place the cognition boundary is defined. It carries no
transport, no credentials and no filesystem knowledge: a provider is anything with an
``evaluate`` coroutine that turns a :class:`CognitionRequest` into a validated
:class:`CognitionResult`.

The result schema is the union of the two documents that govern it. The implementation
plan names ``valence``, ``relevance``, ``credibility``, ``discussion_hook``,
``grounded_reasons`` and ``rule_modifier``; the design specification's LLM cognition
contract additionally requires ``emotion``, ``sentiment_delta``, ``recall_delta``,
``purchase_reason`` and ``share_probability``. Both sets are required, so both are
present and bounded here.

A language model never supplies purchase probability. ``rule_modifier`` is the only
numeric influence a provider has over intention, it is bounded to +/-0.10, and
``purchase_reason`` is qualitative text. Purchase intention stays rule-derived in
:mod:`adlife.core.simulation.decision`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Annotated, Literal, Protocol, Self, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    Field,
    TypeAdapter,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from adlife.core.domain.campaign import CampaignId
from adlife.core.domain.json_values import (
    FrozenJsonMapping,
    freeze_json_mapping,
    thaw_json_mapping,
)
from adlife.core.domain.person import (
    REDACTION_PLACEHOLDER,
    DomainModel,
    contains_secret_or_email_text,
    contains_sensitive_text,
    redact_secret_text,
)
from adlife.core.domain.state import Activity, Channel

PROMPT_VERSION = "cognition-v1"
"""The default prompt contract version; it participates in every cache key."""

MAX_RELEVANT_MEMORIES = 3
"""Prompts carry at most three memories, as the data-minimization rule requires."""

MAX_GROUNDED_REASONS = 3
MAX_SAFETY_FLAGS = 8
MAX_REQUEST_JSON_BYTES = 8192
"""A prompt payload above this canonical size is not a minimized prompt."""

ProviderKind = Literal["rule", "mock", "replay", "local-llm", "remote-llm", "fallback"]
"""Every value is also a persistable ``EventSource``, so events need no translation."""

OFFLINE_PROVIDER_KINDS: frozenset[str] = frozenset({"rule", "mock", "replay", "fallback"})
NETWORK_PROVIDER_KINDS: frozenset[str] = frozenset({"local-llm", "remote-llm"})

Emotion = Literal["curious", "positive", "neutral", "skeptical", "annoyed"]

FallbackReason = Literal[
    "budget-exhausted",
    "provider-unavailable",
    "timeout",
    "rate-limited",
    "http-error",
    "invalid-response",
    "cache-miss",
]

REQUEST_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}:event-[0-9]{8}$"
"""A cognition request is named by the stable event id that caused it.

Every cache key, every replay hit and the mock provider's fixture choice are functions
of this identifier, so it must be a deterministic function of the run rather than a
clock- or UUID-derived value. The pattern is exactly the shape
:func:`adlife.core.simulation.engine.stable_event_id` produces, and
:meth:`CognitionRequest.bind_the_request_id_to_its_own_run` additionally requires the
run part to be this request's own ``run_id``.
"""

RequestId = Annotated[str, Field(pattern=REQUEST_ID_PATTERN)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
FlagText = Annotated[str, Field(min_length=1, max_length=80)]
MemoryText = Annotated[str, Field(min_length=1, max_length=280)]

_CAMPAIGN_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(CampaignId)
"""The domain campaign identifier rule itself, rather than a hand-rolled copy of it."""

_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_UNC_PATH = re.compile(r"\\\\[^\\\s]+\\")
_BACKSLASH_PATH = re.compile(r"[A-Za-z0-9._-]\\[A-Za-z0-9._-]")
_POSIX_PATH = re.compile(r"(?<![\w.:/])/[A-Za-z0-9._-]+")
_HOME_PATH = re.compile(r"(?<![\w~])~[\\/]")
_DOT_RELATIVE_PATH = re.compile(r"(?<![\w./])\.\.?[\\/]")
_EXTENSION_PATH = re.compile(
    r"(?<![\w./])(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}(?!\w)"
)
_FILE_URL = re.compile(r"file://", re.IGNORECASE)
_PATH_PATTERNS = (
    _WINDOWS_PATH,
    _UNC_PATH,
    _BACKSLASH_PATH,
    _POSIX_PATH,
    _HOME_PATH,
    _DOT_RELATIVE_PATH,
    _EXTENSION_PATH,
    _FILE_URL,
)
_REDACTED_PATH_PATTERNS = tuple(
    pattern for pattern in _PATH_PATTERNS if pattern is not _BACKSLASH_PATH
)
"""Every path shape except the bare-backslash one.

A raw provider body is usually JSON, whose own escape sequences put a backslash between
two ordinary characters; redacting on that shape would mangle the body wholesale without
hiding a single secret. Every shape that actually names a location is kept.
"""


def _sort_prompt_json_keys(value: object) -> object:
    """Sort object keys recursively; leave every array element exactly where it is."""
    if isinstance(value, Mapping):
        return FrozenJsonMapping({key: _sort_prompt_json_keys(value[key]) for key in sorted(value)})
    if isinstance(value, tuple):
        return tuple(_sort_prompt_json_keys(item) for item in value)
    return value


def _canonical_prompt_json(value: object) -> FrozenJsonMapping:
    """Freeze one prompt object and put every object KEY inside it into sorted order.

    Sorting keys is what makes a request a function of its content rather than of the
    order a prompt builder happened to insert in: the cache key already digests a
    key-sorted canonical JSON, but the persisted record and the text a live provider is
    shown are ``model_dump_json()``, which walks the mapping. Two ``==``-equal requests
    therefore have to serialize to the same bytes, or the record stored under one key is
    not a function of that key.

    Array ELEMENT order is deliberately left alone. It is prompt content: an exposure
    history, a ranked list, an ordered set of placements. Reordering elements by their
    canonical JSON text turns ``[2, 10, 3, 1, 21]`` into ``[10, 1, 21, 2, 3]`` in the
    text the model reads, which is corruption rather than canonicalisation. A caller that
    projects a domain ``frozenset`` - :attr:`~adlife.core.domain.person.PersonProfile.interests`
    or :attr:`~adlife.core.domain.campaign.Campaign.target_interests` - must therefore
    sort that projection itself, because ``list(frozenset)`` is ordered by this process's
    string hash seed. ``sorted(profile.interests)`` is the whole obligation.
    """
    frozen = freeze_json_mapping(value)
    return FrozenJsonMapping({key: _sort_prompt_json_keys(frozen[key]) for key in sorted(frozen)})


def redact_provider_body(value: str) -> str:
    """Redact credentials, contact addresses and filesystem paths from a raw answer body.

    A raw provider body is kept rather than rejected, because it is the only evidence a
    failed call leaves behind, so it is screened by redaction instead. Rejecting it would
    turn a provider error into an unstorable record, which is the failure specification
    section 12 forbids; storing it verbatim would put an echoed authorization header on
    disk, which specification section 19 forbids.

    Redaction is pattern matching, so the result is checked against the repository's own
    secret detector before it is returned. The detector and the redactor do not recognise
    exactly the same text - a label with no value after it is detected and not removed -
    and a body the detector still calls a secret is dropped wholesale rather than stored.
    That is what makes "a stored record cannot carry a credential" a checked property of
    this function rather than a claim about its patterns.
    """
    redacted = redact_secret_text(value)
    for pattern in _REDACTED_PATH_PATTERNS:
        redacted = pattern.sub(REDACTION_PLACEHOLDER, redacted)
    if contains_secret_or_email_text(redacted):
        return REDACTION_PLACEHOLDER
    return redacted


class CognitionError(Exception):
    """Base class for every cognition boundary failure."""


class CognitionModel(DomainModel):
    """Strict, frozen, JSON-closed base for every cognition contract.

    It inherits the domain base deliberately: ``strict=True``, ``extra="forbid"``,
    ``frozen=True``, ``allow_inf_nan=False`` and the revalidating ``model_copy`` are the
    same guarantees every other contract in this repository makes.
    """


def _walk_text(value: object) -> Iterator[str]:
    """Yield every string inside prompt or answer data, keys included."""
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk_text(key)
            yield from _walk_text(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_text(item)


def _reject_filesystem_paths(value: object, *, label: str) -> None:
    """Reject a filesystem path anywhere in prompt or answer data.

    Absolute, drive-qualified, UNC, home-relative, dot-relative and extension-bearing
    relative paths are all refused, as is a ``file://`` URL. Ordinary URLs survive: a
    path segment preceded by ``:``, ``.`` or ``/`` belongs to a URL, not to a local
    filesystem. A bare multi-segment relative token with no extension (``he/she/they``,
    ``24/7/365``) is indistinguishable from ordinary prose and is deliberately allowed.
    """
    for text in _walk_text(value):
        if any(pattern.search(text) for pattern in _PATH_PATTERNS):
            raise ValueError(f"{label} must not contain a filesystem path")


def _reject_sensitive_text(value: object, *, label: str) -> None:
    """Reject an email address, phone number, national identifier or secret.

    The rule is the domain one, imported rather than restated, so the prompt boundary
    can never drift from the persona contract it mirrors. It applies to the fields
    specification section 19 scopes it to: the persona, and the agent's own memories,
    which are written from persona-derived text.
    """
    for text in _walk_text(value):
        if contains_sensitive_text(text):
            raise ValueError(f"{label} must not contain a sensitive identifier or secret")


def _reject_secret_or_email_text(value: object, *, label: str) -> None:
    """Reject an email address or an explicit secret assignment in untrusted copy.

    Campaign files may be untrusted, so their text is screened - but with the narrower
    domain rule, not the persona one. The persona screen's phone and national-identifier
    patterns are digit-run heuristics that match ordinary advertising copy: a delivery
    window, a discount range, a price, a date range. Applying them here would refuse a
    campaign the binding domain contract accepts, and it would refuse it by raising a
    ``ValidationError`` while the request is being built - before any provider call
    exists to fall back from, which specification section 12 forbids a failure from
    doing. A campaign may still never carry a credential or a contact address into a
    prompt, and it may never carry a filesystem path; those screens are unchanged.
    """
    for text in _walk_text(value):
        if contains_secret_or_email_text(text):
            raise ValueError(f"{label} must not contain a sensitive identifier or secret")


class SamplingSettings(CognitionModel):
    """Provider sampling settings; they participate in every cache key.

    The defaults are the reproducible ones - temperature zero and no nucleus truncation -
    because a provider configured by omission must not become less deterministic.
    """

    schema_version: Literal[1] = 1
    temperature: float = Field(default=0.0, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    max_output_tokens: int = Field(default=512, ge=1, le=8192)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)


class ProviderMetadata(CognitionModel):
    """Everything about a provider that may change its answers.

    There is no credential field, and :meth:`normalize_base_url` removes the userinfo,
    query and fragment a URL could smuggle one through, so neither this metadata nor a
    cache key derived from it can carry an API key. The other way a credential could
    reach a stored record - a provider echoing one back in its raw body - is screened by
    :meth:`CognitionRecord.redact_the_raw_provider_body`, which removes labelled and
    value-shaped secrets and drops any body that still trips the repository's detector.
    """

    schema_version: Literal[1] = 1
    kind: ProviderKind
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model_id: str = Field(min_length=1, max_length=120)
    model_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sampling: SamplingSettings
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            raise ValueError("base_url must be an http or https URL")
        try:
            host = parts.hostname
            port = parts.port
        except ValueError as error:
            raise ValueError("base_url must name a valid host") from error
        if not host:
            raise ValueError("base_url must name a valid host")
        netloc = host if port is None else f"{host}:{port}"
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    @model_validator(mode="after")
    def match_base_url_to_kind(self) -> Self:
        if self.kind in NETWORK_PROVIDER_KINDS and self.base_url is None:
            raise ValueError("base_url is required for a network cognition provider")
        if self.kind in OFFLINE_PROVIDER_KINDS and self.base_url is not None:
            raise ValueError("base_url must be absent for an offline cognition provider")
        return self


class CognitionRequest(CognitionModel):
    """One bounded, minimized cognition prompt payload.

    ``fictional_persona`` and ``campaign`` are free-form JSON objects, so they are
    recursively JSON-validated and deeply frozen exactly like a domain event payload.
    :func:`_canonical_prompt_json` additionally sorts their object keys at every depth,
    so two ``==``-equal requests serialize to the same bytes and the record stored under
    a cache key is a function of that key. Array element order is preserved as the caller
    authored it; projecting a domain ``frozenset`` into a prompt array is the caller's
    obligation to sort.

    ``activity`` and ``channel`` are the domain contracts themselves, so an unknown or
    case-variant value can never fork a cache key or a provider fixture.
    """

    schema_version: Literal[1] = 1
    request_id: RequestId
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    simulated_minute: int = Field(ge=0, le=10080)
    agent_id: str = Field(pattern=r"^person-[0-9]{3}$")
    fictional_persona: Mapping[str, object]
    activity: Activity
    mood: float = Field(ge=-1, le=1)
    relevant_memories: tuple[MemoryText, ...] = Field(max_length=MAX_RELEVANT_MEMORIES)
    campaign: Mapping[str, object]
    channel: Channel
    exposure_count: int = Field(ge=1, le=14)
    creative_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(
        default=PROMPT_VERSION,
        pattern=r"^[a-z0-9][a-z0-9.-]{0,39}$",
    )

    @field_validator("fictional_persona", "campaign", mode="before")
    @classmethod
    def validate_prompt_json(cls, value: object) -> FrozenJsonMapping:
        return _canonical_prompt_json(value)

    @field_validator("fictional_persona", "campaign")
    @classmethod
    def freeze_prompt_json(cls, value: Mapping[str, object]) -> FrozenJsonMapping:
        return _canonical_prompt_json(value)

    @field_serializer("fictional_persona", "campaign")
    def serialize_prompt_json(self, value: Mapping[str, object]) -> dict[str, object]:
        return thaw_json_mapping(value)

    @model_validator(mode="after")
    def bind_the_request_id_to_its_own_run(self) -> Self:
        if not self.request_id.startswith(f"{self.run_id}:"):
            raise ValueError("request_id must be the stable event id of its own run")
        return self

    @model_validator(mode="after")
    def validate_prompt_boundary(self) -> Self:
        try:
            _CAMPAIGN_ID_ADAPTER.validate_python(
                self.campaign.get("campaign_id"),
                strict=True,
            )
        except ValidationError as error:
            raise ValueError("campaign must carry a campaign_id slug") from error
        _reject_filesystem_paths(self.fictional_persona, label="fictional_persona")
        _reject_filesystem_paths(self.campaign, label="campaign")
        _reject_filesystem_paths(self.relevant_memories, label="relevant_memories")
        _reject_sensitive_text(self.fictional_persona, label="fictional_persona")
        _reject_secret_or_email_text(self.campaign, label="campaign")
        _reject_sensitive_text(self.relevant_memories, label="relevant_memories")
        size = len(self.model_dump_json().encode("utf-8"))
        if size > MAX_REQUEST_JSON_BYTES:
            raise ValueError(
                "a cognition prompt must stay minimized: "
                f"{size} bytes exceeds {MAX_REQUEST_JSON_BYTES}"
            )
        return self

    @property
    def campaign_id(self) -> str:
        campaign_id = self.campaign["campaign_id"]
        if not isinstance(campaign_id, str):  # pragma: no cover - validated at construction
            raise TypeError("campaign_id must be a string")
        return campaign_id


class CognitionResult(CognitionModel):
    """One validated cognition answer, bounded on every numeric field.

    Provider-authored text is screened the way the text it paraphrases is screened. A
    filesystem path is refused everywhere. For identifiers and secrets the answer fields
    split in two, deliberately and as a pair:

    ``interpretation``, ``purchase_reason``, ``discussion_hook``, ``grounded_reasons`` and
    ``safety_flags`` restate the campaign copy the prompt carried, so they carry the same
    NARROW rule that copy carries - :func:`contains_secret_or_email_text`. The persona
    screen's phone and national-identifier clauses are digit-run heuristics that match a
    price, a discount range, a delivery window or a date range; applying them to a
    paraphrase would invalidate an answer that merely quoted what it was shown, burn
    specification section 12's single repair attempt and drop to the rule fallback
    systematically rather than exceptionally.

    ``memory_summary`` keeps the STRICT persona rule, and it keeps it because
    ``relevant_memories`` keeps it: a summary is written straight back into a later
    prompt's memories, so a summary accepted here under a weaker rule would raise at the
    next request build instead of at the provider boundary, where a failure can still be
    handled as a fallback. The two fields move together or not at all.
    """

    schema_version: Literal[1] = 1
    request_id: RequestId
    interpretation: str = Field(min_length=1, max_length=240)
    emotion: Emotion
    valence: float = Field(ge=-1, le=1)
    relevance: float = Field(ge=0, le=1)
    credibility: float = Field(ge=0, le=1)
    sentiment_delta: float = Field(ge=-0.25, le=0.25)
    recall_delta: float = Field(ge=0, le=0.30)
    purchase_reason: str = Field(min_length=1, max_length=240)
    share_probability: float = Field(ge=0, le=1)
    discussion_hook: str = Field(min_length=1, max_length=200)
    grounded_reasons: tuple[ShortText, ...] = Field(max_length=MAX_GROUNDED_REASONS)
    memory_summary: str = Field(min_length=1, max_length=280)
    rule_modifier: float = Field(ge=-0.10, le=0.10)
    safety_flags: tuple[FlagText, ...] = Field(default=(), max_length=MAX_SAFETY_FLAGS)

    @model_validator(mode="after")
    def validate_answer_boundary(self) -> Self:
        _reject_filesystem_paths(self.interpretation, label="interpretation")
        _reject_filesystem_paths(self.purchase_reason, label="purchase_reason")
        _reject_filesystem_paths(self.discussion_hook, label="discussion_hook")
        _reject_filesystem_paths(self.memory_summary, label="memory_summary")
        _reject_filesystem_paths(self.grounded_reasons, label="grounded_reasons")
        _reject_filesystem_paths(self.safety_flags, label="safety_flags")
        _reject_secret_or_email_text(self.interpretation, label="interpretation")
        _reject_secret_or_email_text(self.purchase_reason, label="purchase_reason")
        _reject_secret_or_email_text(self.discussion_hook, label="discussion_hook")
        _reject_secret_or_email_text(self.grounded_reasons, label="grounded_reasons")
        _reject_secret_or_email_text(self.safety_flags, label="safety_flags")
        _reject_sensitive_text(self.memory_summary, label="memory_summary")
        return self


class ProviderUsage(CognitionModel):
    """What one cognition answer cost and where it came from."""

    schema_version: Literal[1] = 1
    provider_kind: ProviderKind
    model_id: str = Field(min_length=1, max_length=120)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cache_hit: bool = False
    fallback_reason: FallbackReason | None = None

    @model_validator(mode="after")
    def require_a_reason_exactly_for_a_fallback(self) -> Self:
        if (self.provider_kind == "fallback") != (self.fallback_reason is not None):
            raise ValueError(
                "a fallback usage record must name its fallback_reason, "
                "and only a fallback may carry one"
            )
        return self


class CognitionAnswer(CognitionModel):
    """One provider answer together with the provenance an event has to record.

    Specification section 13 requires every event to name its source and the model that
    produced it, so provenance cannot be reachable only through a method one adapter
    happens to publish beside the port. A service coded against
    :class:`CognitionProvider` reads both here, and for replay both come out of a single
    read of a single record rather than two independent ones.
    """

    schema_version: Literal[1] = 1
    result: CognitionResult
    usage: ProviderUsage


class CognitionRecord(CognitionModel):
    """One cached cognition exchange: the request, the answer, and what it cost.

    Every field on this record is persisted, so every field is screened. The validated
    ``result`` is screened by rejection, because a bad answer can be retried, repaired or
    replaced by the rule fallback. ``raw_response`` is screened by redaction, because it
    is the diagnostic body of a call that may already have failed and there is nothing
    left to fall back to: an error body is exactly where a provider echoes the
    authorization header back.
    """

    schema_version: Literal[1] = 1
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: CognitionRequest
    provider_metadata: ProviderMetadata
    raw_response: str | None = Field(default=None, max_length=65536)
    result: CognitionResult
    usage: ProviderUsage

    @field_validator("raw_response")
    @classmethod
    def redact_the_raw_provider_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return redact_provider_body(value)

    @model_validator(mode="after")
    def bind_result_to_request(self) -> Self:
        if self.result.request_id != self.request.request_id:
            raise ValueError("a cognition record must bind its result to its own request")
        return self


@runtime_checkable
class CognitionProvider(Protocol):
    """The single cognition port every adapter implements.

    ``evaluate`` answers the question. ``answer`` returns the same result together with
    the :class:`ProviderUsage` an event must carry, in one operation, so a consumer can
    never stamp a replayed fallback as an ordinary call and never observe a result and a
    provenance drawn from two different reads of the same record.
    """

    async def evaluate(self, request: CognitionRequest) -> CognitionResult: ...

    async def answer(self, request: CognitionRequest) -> CognitionAnswer: ...


__all__ = [
    "MAX_GROUNDED_REASONS",
    "MAX_RELEVANT_MEMORIES",
    "MAX_REQUEST_JSON_BYTES",
    "MAX_SAFETY_FLAGS",
    "NETWORK_PROVIDER_KINDS",
    "OFFLINE_PROVIDER_KINDS",
    "PROMPT_VERSION",
    "REQUEST_ID_PATTERN",
    "CognitionAnswer",
    "CognitionError",
    "CognitionModel",
    "CognitionProvider",
    "CognitionRecord",
    "CognitionRequest",
    "CognitionResult",
    "Emotion",
    "FallbackReason",
    "ProviderKind",
    "ProviderMetadata",
    "ProviderUsage",
    "RequestId",
    "SamplingSettings",
    "redact_provider_body",
]
