"""Persona contracts and the repository's single credential and identifier screen.

WHAT THE SCREENS IN THIS MODULE PROMISE, AND WHAT THEY DO NOT
-------------------------------------------------------------
Perfect secret detection is undecidable. An opaque random token under an unlabelled
field is indistinguishable from an order number, a request identifier or a model name,
and no pattern can separate them. These functions therefore make a NARROW, TESTABLE
promise:

    Known credential shapes and labelled secrets are detected and redacted on a
    best-effort basis. This is defense in depth. It is not a guarantee that a screened
    string carries no credential.

Everything claimed is driven from the three named tables below and pinned by
``tests/security/test_redaction_corpus.py``, which a later task extends by adding one
row. Nothing outside those tables is claimed.

Three screens are published, from narrowest to broadest:

* :func:`contains_secret_or_email_text` - an email address, a labelled secret
  assignment, or a known vendor credential shape. This is the rule untrusted campaign
  copy and provider-authored paraphrase are screened with, so it deliberately omits the
  digit-run heuristics and the transport header names that occur in ordinary
  advertising copy.
* :func:`contains_sensitive_text` - the above plus the phone, national-identifier and
  labelled-identifier heuristics specification section 19 scopes to persona fields.
* :func:`contains_provider_secret_text` - the narrow screen minus nothing and plus the
  transport header names, bearer values and label/value adjacency. This is the
  fail-closed re-check for a raw provider body, where over-redaction costs a diagnostic
  and under-redaction costs a credential.

:func:`contains_labelled_secret_member` is a fourth, structural screen: it walks JSON
data rather than text, because a credential label in a mapping KEY with the secret in
the sibling VALUE is invisible to every per-string matcher above.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]

# --- the three tables every claim in this module is driven from -----------------------

SECRET_LABEL_WORDS: tuple[str, ...] = (
    r"api[_ -]?keys?",
    r"api[_ -]?tokens?",
    r"access[_ -]?tokens?",
    r"auth[_ -]?tokens?",
    r"session[_ -]?tokens?",
    r"refresh[_ -]?tokens?",
    r"bearer[_ -]?tokens?",
    r"id[_ -]?tokens?",
    r"client[_ -]?secrets?",
    r"private[_ -]?keys?",
    r"secret[_ -]?keys?",
    r"signing[_ -]?keys?",
    r"secrets?",
    r"passwords?",
    r"passphrases?",
    r"credentials?",
)
"""Credential LABELS screened everywhere, including in untrusted campaign copy.

Each entry is a regular-expression fragment, and the word separator is written
``[_ -]?`` so ``api_key``, ``api key``, ``api-key`` and ``apikey`` are one row. The label
is anchored between :data:`_SECRET_LABEL_START` and :data:`_SECRET_LABEL_END`, which are
non-alphanumeric lookarounds rather than ``\\b`` so an environment variable name spells a
label too: ``ADLIFE_API_KEY`` - the variable specification section 6.4 mandates -
``OPENAI_API_KEY`` and ``X_ACCESS_TOKEN`` all match.

Longer entries are listed before the shorter entries they contain so the longest label is
preferred; the shorter rows stay reachable by backtracking.

DOCUMENTED SIDE EFFECT (finding S10). Because the anchors are non-alphanumeric rather
than word boundaries, ``secret`` matches inside ``top_secret`` and ``brand_secret``, so
campaign copy of the shape ``top_secret: ...`` or ``brand_secret=...`` is refused at
request build. That is deliberate - the alternative is to miss ``CLIENT_SECRET=`` - and
it is a fail-closed refusal of a request that has not been sent, not a leak. Copy that
only uses the word (``our secret sauce``) is untouched: a label only counts when an
assignment operator follows it.
"""

AUTH_HEADER_LABELS: tuple[str, ...] = (
    r"proxy[_ -]?authorization",
    r"authorization",
    r"www[_ -]?authenticate",
    r"proxy[_ -]?authenticate",
    r"set[_ -]?cookie",
    r"cookies?",
    r"x[_ -]?amz[_ -]?security[_ -]?token",
    r"x[_ -]?goog[_ -]?api[_ -]?key",
    r"x[_ -]?csrf[_ -]?token",
    r"x[_ -]?xsrf[_ -]?token",
    r"x[_ -]?api[_ -]?key",
    r"x[_ -]?api[_ -]?token",
    r"x[_ -]?auth[_ -]?token",
    r"x[_ -]?session[_ -]?token",
    r"x[_ -]?access[_ -]?token",
)
"""Transport header NAMES, screened only where a raw provider body is (finding S3).

An error body is where a provider echoes the request headers back, so these have to be
redactable. They are deliberately NOT in :data:`SECRET_LABEL_WORDS`: ``cookie`` is an
ordinary word in advertising copy, and refusing a campaign for naming a biscuit would
turn a security screen into a product defect. The ``x-`` rows overlap
:data:`SECRET_LABEL_WORDS`, because the label anchors already admit a leading ``x-``;
they are spelled out anyway so the covered header names are readable in one place.
"""

VENDOR_SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "openai-style-key",
        re.compile(
            r"(?<![A-Za-z0-9])(?:sk|pk|rk)[_-](?:live|test|proj)?[_-]?"
            r"[A-Za-z0-9][A-Za-z0-9._~+/=-]{7,}",
            re.IGNORECASE,
        ),
    ),
    ("github-token", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{16,}")),
    ("slack-token", re.compile(r"(?<![A-Za-z0-9])xox[abeprs]-[A-Za-z0-9-]{10,}")),
    ("slack-app-token", re.compile(r"(?<![A-Za-z0-9])xapp-[A-Za-z0-9-]{10,}")),
    (
        "aws-access-key-id",
        re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    ),
    ("google-api-key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{16,}")),
    (
        "json-web-token",
        re.compile(
            r"(?<![A-Za-z0-9._~+/=-])eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"
        ),
    ),
    (
        "pem-private-key",
        re.compile(
            r"-----BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY-----"
            r"[\s\S]*?(?:-----END[A-Z0-9 ]{0,40}PRIVATE KEY-----|\Z)"
        ),
    ),
)
"""Credential VALUE shapes that need no label to announce them (finding S4).

A provider that quotes a credential back does not always name the field it came from, so
an unlabelled ``{"credential": "ghp_..."}`` is exactly as much of a leak as a labelled
one. Every row is a published vendor prefix rather than an entropy guess:

* ``openai-style-key``  - ``sk``/``pk``/``rk`` with a ``live``/``test``/``proj`` segment.
* ``github-token``      - ``ghp_``, ``gho_``, ``ghu_``, ``ghs_``, ``ghr_``.
* ``slack-token``       - ``xoxb-``, ``xoxp-``, ``xoxa-``, ``xoxe-``, ``xoxr-``, ``xoxs-``.
* ``slack-app-token``   - ``xapp-``.
* ``aws-access-key-id`` - ``AKIA``/``ASIA`` and sixteen upper-case alphanumerics.
* ``google-api-key``    - ``AIza`` and a long url-safe tail.
* ``json-web-token``    - every JWS header is the base64url of a JSON object, so ``eyJ``;
  the third segment may be empty for an unsecured token.
* ``pem-private-key``   - the whole armoured block, header to footer, or to end of text
  when the footer is missing.

The tail of the openai-style row admits ``-``, ``_``, ``.``, ``~``, ``+``, ``/`` and
``=`` so a hyphenated key is removed to its END rather than to its first delimiter
(finding S5): ``sk-live-AAAABBBB-CCCCDDDD-EEEEFFFF`` must not leave ``-CCCCDDDD`` behind.
"""

# --- identifier heuristics, which are persona-only by design -------------------------

_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<![\w-])\+?\d[\d ()-]{7,}\d(?![\w-])")
_NATIONAL_ID_PATTERN = re.compile(r"(?<![\w-])\d{10}(?![\w-])")
_IDENTIFIER_LABEL = (
    r"(?:national[_ -]?id|social[_ -]?security(?:[_ -]?number)?|ssn"
    r"|passport(?:[_ -]?(?:no|number))?|tax[_ -]?id|insurance[_ -]?(?:no|number))"
)
_LABELLED_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])" + _IDENTIFIER_LABEL + r"[_ -]*[:=]?[_ -]*\+?\d[\d ()-]{4,}\d",
    re.IGNORECASE,
)
"""A digit run a national-identifier LABEL introduces, however it is punctuated.

The bare :data:`_NATIONAL_ID_PATTERN` and :data:`_PHONE_PATTERN` no longer fire on a
digit run that CONTINUES a hyphenated token, because ``spring-1234567890`` is an ordinary
campaign slug (``^[a-z0-9][a-z0-9-]{0,79}$``) and refusing it broke the terminal rule
fallback for every run whose campaign happened to be named that way - finding S8. The
coverage that narrowing gives up is restored here explicitly: ``national-id-1234567890``
is still refused, because a national-identifier label introduces the digits. A bare
``1234567890``, or ``+1 415 555 0134``, is unchanged and still refused.
"""

# --- label anchors, windows and the composed patterns --------------------------------

_SECRET_LABEL_START = r"(?<![A-Za-z0-9])"
"""A label boundary an underscore does not close.

``\\b`` cannot match between ``_`` and a letter, so a label carried by an environment
variable name - ``ADLIFE_API_KEY``, the name design specification section 6.4 mandates,
or ``OPENAI_API_KEY``, ``DB_PASSWORD``, ``X_ACCESS_TOKEN`` - was never recognised as a
label at all. The underscore is a word character; it is not a letter or a digit.
"""
_SECRET_LABEL_END = r"(?![A-Za-z0-9])"

SECRET_LABEL_GAP = 120
"""How far a redactable secret LABEL may sit from the ``:`` or ``=`` it introduces.

The window used to be 32 characters, which missed both bodies finding S1 cites: a JSON
key long enough to describe itself (``api_key_for_the_..._service``) and an error
sentence that names the header before punctuating it (``Incorrect API key provided in
your request headers today:``). The window still excludes ``,``, ``{``, ``}``, ``[`` and
``]``, so it can never cross out of the JSON member the label sits in.
"""

SECRET_ADJACENCY_GAP = 48
"""How far a secret-shaped VALUE may sit from a secret LABEL in a sibling member.

Finding S2: ``{"name":"api_key","value":"0000abcdef1234567890"}`` and
``["api_key","0000abcdef1234567890"]`` put the label and the value in different JSON
members, where no single label-then-operator pattern can reach. The gap admits quotes,
commas, colons, whitespace and short bare words but NOT a brace or a bracket, so a match
can never leave the object or array the label sits in.
"""

MIN_ADJACENT_VALUE_CHARS = 12
"""How long a quoted token beside a secret label must be before it is removed.

Short values are ordinary structure (``401``, ``true``, ``en-US``). This is a heuristic
and it is documented as one: it over-redacts a long quoted token that happens to sit
beside a credential label in a diagnostic body, which costs a diagnostic, and it misses a
credential shorter than twelve characters, which is one reason this module promises
defense in depth rather than a guarantee.
"""


def _label_group(labels: Iterable[str]) -> str:
    """Compose one alternation from a documented label table."""
    return "(?:" + "|".join(labels) + ")"


_SECRET_LABEL = _label_group(SECRET_LABEL_WORDS)
_REDACTION_LABEL = _label_group((*SECRET_LABEL_WORDS, *AUTH_HEADER_LABELS))
_ASSIGNMENT = r"\s*[\x22\x27]?\s*[:=]"
"""An assignment operator, with the quote a JSON key closes with allowed in front of it.

In JSON the character after a quoted key is ``"``, not ``:`` or ``=``, so a screen that
required the operator immediately could not fire on any JSON body at all - finding S6.
The quote characters are spelled ``\\x22`` and ``\\x27`` so the pattern stays a plain raw
string.
"""

_SECRET_PATTERN = re.compile(
    _SECRET_LABEL_START + _SECRET_LABEL + _SECRET_LABEL_END + _ASSIGNMENT,
    re.IGNORECASE,
)
_HEADER_SECRET_PATTERN = re.compile(
    _SECRET_LABEL_START + _REDACTION_LABEL + _SECRET_LABEL_END + _ASSIGNMENT,
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)

_SECRET_VALUE_PATTERN = re.compile(
    _SECRET_LABEL_START
    + _REDACTION_LABEL
    + _SECRET_LABEL_END
    + rf"[^\r\n{{}}\[\],]{{0,{SECRET_LABEL_GAP}}}?[:=]\s*"
    + r"(?:[\x22\x27][^\x22\x27\r\n]*[\x22\x27]|[^\x22\x27\r\n{}\[\]]+)",
    re.IGNORECASE,
)
"""A secret label and the value it introduces, removed together.

The value is taken as a whole quoted string when one follows the operator, and otherwise
as everything up to the next quote, brace, bracket or line end. The unquoted branch
deliberately admits ``,`` and spaces so ``api_key=AAAABBBB,CCCCDDDD`` and
``api_key: AAAABBBB CCCCDDDD`` are removed in full rather than up to the first delimiter
(finding S5). The quoted branch is tried first, so an ordinary JSON member loses only its
own value and the rest of the object survives.
"""

_LABEL_ADJACENT_SECRET_PATTERN = re.compile(
    _SECRET_LABEL_START
    + _REDACTION_LABEL
    + _SECRET_LABEL_END
    + rf"[\x22\x27\s,:=A-Za-z0-9_-]{{0,{SECRET_ADJACENCY_GAP}}}?"
    + r"[\x22\x27][A-Za-z0-9][A-Za-z0-9._~+/=-]"
    + rf"{{{MIN_ADJACENT_VALUE_CHARS - 1},}}[\x22\x27]",
    re.IGNORECASE,
)
"""A secret-shaped value in a SIBLING member or array element of a label (finding S2)."""

REDACTION_PLACEHOLDER = "[redacted]"

_SENSITIVE_PATTERNS = (
    _EMAIL_PATTERN,
    _PHONE_PATTERN,
    _NATIONAL_ID_PATTERN,
    _LABELLED_IDENTIFIER_PATTERN,
    _SECRET_PATTERN,
    *(pattern for _, pattern in VENDOR_SECRET_SHAPES),
)
_SECRET_OR_EMAIL_PATTERNS = (
    _EMAIL_PATTERN,
    _SECRET_PATTERN,
    *(pattern for _, pattern in VENDOR_SECRET_SHAPES),
)
_PROVIDER_SECRET_PATTERNS = (
    _EMAIL_PATTERN,
    _HEADER_SECRET_PATTERN,
    _BEARER_PATTERN,
    _SECRET_VALUE_PATTERN,
    _LABEL_ADJACENT_SECRET_PATTERN,
    *(pattern for _, pattern in VENDOR_SECRET_SHAPES),
)
"""Exactly the shapes :func:`redact_secret_text` removes, so the screen and the redactor
cannot drift: anything the screen can see, the redactor can remove, and anything the
redactor leaves behind, the screen can still object to."""
_PEM_PATTERN = next(pattern for name, pattern in VENDOR_SECRET_SHAPES if name == "pem-private-key")
_REDACTION_PATTERNS = (
    _PEM_PATTERN,
    _BEARER_PATTERN,
    _SECRET_VALUE_PATTERN,
    _LABEL_ADJACENT_SECRET_PATTERN,
    *(pattern for name, pattern in VENDOR_SECRET_SHAPES if name != "pem-private-key"),
    _EMAIL_PATTERN,
)
"""The redaction order: armoured block, bearer, labelled value, adjacent value, shapes.

The armoured PEM block runs first because it is the only multi-line match and any other
pattern firing inside it would split it. The labelled patterns run before the value
shapes so a label is removed WITH its value rather than left standing beside a
placeholder, which would keep tripping the fail-closed re-check.
"""

_WHOLE_SECRET_LABEL_PATTERN = re.compile(
    r"[A-Za-z0-9_-]{0,40}?" + _REDACTION_LABEL + r"s?",
    re.IGNORECASE,
)
"""A JSON key is a credential label when a label is the whole of it, prefix included.

``api_key``, ``ADLIFE_API_KEY``, ``x-api-token`` and ``vendor_client_secret`` are labels;
``api_key_usage_notes`` is not, because the pattern has to consume the entire key.
"""


def _is_secret_label_text(value: str) -> bool:
    """True when a whole string is nothing but a credential label.

    This is the KEY side of finding S7: ``{"api_key": "sk-live-..."}`` puts the label in
    the key and the credential in the sibling value, which no per-string matcher can see.
    """
    return _WHOLE_SECRET_LABEL_PATTERN.fullmatch(value.strip()) is not None


def _carries_text(value: object) -> bool:
    """True when JSON data holds any non-empty string anywhere inside it."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_carries_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_carries_text(item) for item in value)
    return value is not None


def contains_sensitive_text(value: str) -> bool:
    """True when text carries an email, phone number, national identifier, or a secret.

    This is the single definition of the rule the design specification names in section
    19; the cognition prompt boundary reuses it rather than restating the patterns. It is
    :func:`contains_secret_or_email_text` plus the persona-only identifier heuristics.

    It is best effort: it detects the shapes in the tables at the top of this module and
    makes no claim about an unlabelled opaque token.
    """
    return any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS)


def contains_secret_or_email_text(value: str) -> bool:
    """True when text carries an email address, a labelled secret, or a vendor key shape.

    This is the strict subset of :func:`contains_sensitive_text` that stays meaningful
    for third-party copy nobody wrote as a persona field. The phone-number and
    national-identifier patterns are digit-run heuristics: they are the right rule for a
    generated persona, and the wrong rule for a price, a delivery window or a date range
    in an advertisement. Specification section 19 scopes identifier rejection to persona
    fields, so untrusted campaign text is screened with this narrower rule instead.

    It knows the JSON spelling of an assignment and every row of
    :data:`VENDOR_SECRET_SHAPES`, so it fires on the bodies a real provider returns
    rather than only on the ``label: value`` shape its own first test used. It does NOT
    know the transport header names in :data:`AUTH_HEADER_LABELS`, on purpose: those are
    ordinary words in advertising copy. Use :func:`contains_provider_secret_text` for a
    raw provider body.

    It is best effort, and it is a screen rather than a proof.
    """
    return any(pattern.search(value) for pattern in _SECRET_OR_EMAIL_PATTERNS)


def contains_provider_secret_text(value: str) -> bool:
    """True when a RAW PROVIDER BODY still looks like it carries a credential.

    This is the broadest text screen and the fail-closed re-check behind
    :func:`adlife.core.ports.cognition.redact_provider_body`. On top of
    :func:`contains_secret_or_email_text` it knows the transport header names in
    :data:`AUTH_HEADER_LABELS`, an ``Authorization: Bearer`` value, and a secret-shaped
    value sitting in a sibling member of a secret label.

    It carries no persona digit-run heuristic, because a provider body legitimately
    contains request identifiers, counters and token counts.

    It is best effort. A body it calls clean may still carry an opaque token under an
    unlabelled field, and nothing in this repository claims otherwise.
    """
    return any(pattern.search(value) for pattern in _PROVIDER_SECRET_PATTERNS)


def contains_labelled_secret_member(value: object) -> bool:
    """True when JSON data pairs a credential LABEL with a value, at any depth.

    The per-string screens cannot see this shape, because neither string is a secret on
    its own: the label is in the mapping KEY and the credential is in the sibling VALUE,
    or the two are adjacent elements of an array. That is the channel finding S7 used to
    write ``{"api_key": "sk-live-..."}`` straight into a persisted cognition request,
    where the per-string rejection screen never looked.

    A mapping member whose key is a credential label is refused whenever the member
    carries any non-empty text at all - a minimized prompt has no legitimate reason to
    name one - and an array element that is nothing but a credential label is refused
    when a non-empty string follows it.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_label_text(key) and _carries_text(item):
                return True
            if contains_labelled_secret_member(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        previous_is_label = False
        for item in value:
            if previous_is_label and isinstance(item, str) and item.strip():
                return True
            previous_is_label = isinstance(item, str) and _is_secret_label_text(item)
            if contains_labelled_secret_member(item):
                return True
    return False


def redact_secret_text(value: str) -> str:
    """Replace authorization headers, secret values, vendor key shapes and emails.

    Specification section 19 requires logs to redact authorization headers and likely
    secret patterns. This is that rule, applied wherever provider-authored text is kept
    rather than rejected: the value a secret label introduces is removed with the label,
    not just the label, because the label is never the part worth hiding.

    Three kinds of match are made, all driven from the tables at the top of this module,
    and the distinction is the whole of the promise:

    * LABELLED - a credential label or a transport header name and the value it
      introduces, including a label spelled as an environment variable name, and
      including a label separated from its operator by up to :data:`SECRET_LABEL_GAP`
      characters of prose or JSON key.
    * ADJACENT - a secret-shaped value in a sibling JSON member or array element of a
      credential label, within :data:`SECRET_ADJACENCY_GAP` characters.
    * VALUE-SHAPED, with no label needed - every row of :data:`VENDOR_SECRET_SHAPES`, and
      an email address.

    It deliberately over-redacts rather than under-redacts within those shapes, and it is
    idempotent: the placeholder carries no label, no key shape and no address, so
    redacting twice gives the same text as redacting once.

    WHAT IT DOES NOT DO. It cannot remove an opaque random token under an unlabelled
    field that sits nowhere near a label, because that is indistinguishable from an order
    number. :func:`adlife.core.ports.cognition.redact_provider_body` narrows the residual
    risk by dropping any body :func:`contains_provider_secret_text` still objects to, but
    neither function promises that a surviving body carries no credential. This is
    defense in depth, not a guarantee.
    """
    for pattern in _REDACTION_PATTERNS:
        value = pattern.sub(REDACTION_PLACEHOLDER, value)
    return value


class DomainModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if not update:
            return super().model_copy(update=update, deep=deep)

        data = self.model_dump(mode="python", round_trip=True)
        data.update(update)
        return type(self).model_validate(data)


class ConsumerTraits(DomainModel):
    price_sensitivity: float = Field(ge=0, le=1)
    novelty_seeking: float = Field(ge=0, le=1)
    social_susceptibility: float = Field(ge=0, le=1)
    advertising_skepticism: float = Field(ge=0, le=1)
    mobile_attention: float = Field(ge=0, le=1)
    outdoor_attention: float = Field(ge=0, le=1)
    brand_loyalty: float = Field(ge=0, le=1)
    impulsivity: float = Field(ge=0, le=1)


class PersonProfile(DomainModel):
    schema_version: Literal[1] = 1
    agent_id: str = Field(pattern=r"^person-[0-9]{3}$")
    display_name: ShortText
    fictional: Literal[True] = True
    age: int = Field(ge=18, le=65)
    occupation: Literal["student", "office-worker", "retail-worker", "freelancer", "unemployed"]
    income_band: Literal["low", "middle", "high"]
    household_type: ShortText
    home_zone: Slug
    work_or_study_zone: Slug | None
    interests: frozenset[ShortText] = Field(min_length=1, max_length=12)
    traits: ConsumerTraits
    initial_brand_sentiment: float = Field(ge=-1, le=1)
    routine_template: Slug

    @field_serializer("interests", when_used="json")
    def serialize_interests(self, value: frozenset[str]) -> list[str]:
        """Serialize interests in a stable order so persisted profiles stay byte-identical."""
        return sorted(value)

    @model_validator(mode="after")
    def reject_sensitive_persona_text(self) -> Self:
        text_values = (
            self.display_name,
            self.household_type,
            self.routine_template,
            *self.interests,
        )
        if any(contains_sensitive_text(value) for value in text_values):
            raise ValueError("persona text contains a sensitive identifier or secret")
        return self


__all__ = [
    "AUTH_HEADER_LABELS",
    "MIN_ADJACENT_VALUE_CHARS",
    "REDACTION_PLACEHOLDER",
    "SECRET_ADJACENCY_GAP",
    "SECRET_LABEL_GAP",
    "SECRET_LABEL_WORDS",
    "VENDOR_SECRET_SHAPES",
    "ConsumerTraits",
    "DomainModel",
    "PersonProfile",
    "contains_labelled_secret_member",
    "contains_provider_secret_text",
    "contains_secret_or_email_text",
    "contains_sensitive_text",
    "redact_secret_text",
]
