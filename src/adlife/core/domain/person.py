import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]

_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")
_NATIONAL_ID_PATTERN = re.compile(r"(?<!\d)\d{10}(?!\d)")
_SECRET_LABEL_START = r"(?<![A-Za-z0-9])"
"""A label boundary an underscore does not close.

``\\b`` cannot match between ``_`` and a letter, so a label carried by an environment
variable name - ``ADLIFE_API_KEY``, the name design specification section 6.4 mandates,
or ``OPENAI_API_KEY``, ``DB_PASSWORD``, ``X_ACCESS_TOKEN`` - was never recognised as a
label at all. The underscore is a word character; it is not a letter or a digit.
"""
_SECRET_LABEL_END = r"(?![A-Za-z0-9])"
_SECRET_LABEL = r"(?:api[_ -]?key|access[_ -]?token|secret|password)"
_SECRET_PATTERN = re.compile(
    _SECRET_LABEL_START + _SECRET_LABEL + _SECRET_LABEL_END + r"\s*[:=]",
    re.IGNORECASE,
)
_SENSITIVE_PATTERNS = (
    _EMAIL_PATTERN,
    _PHONE_PATTERN,
    _NATIONAL_ID_PATTERN,
    _SECRET_PATTERN,
)
_SECRET_OR_EMAIL_PATTERNS = (
    _EMAIL_PATTERN,
    _SECRET_PATTERN,
)

REDACTION_PLACEHOLDER = "[redacted]"

# A secret label is redacted together with the value it introduces, so the short window
# between them excludes the structural characters a JSON body uses to end a value. The
# quote characters are spelled \x22 and \x27 so the pattern stays a plain raw string.
# The label is anchored on _SECRET_LABEL_START for the reason documented there.
_BEARER_PATTERN = re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_REDACTION_LABEL = r"(?:api[_ -]?key|access[_ -]?token|authorization|secret|password)"
_SECRET_VALUE_PATTERN = re.compile(
    _SECRET_LABEL_START
    + _REDACTION_LABEL
    + _SECRET_LABEL_END
    + r"[^\r\n{}\[\],]{0,32}?[:=]\s*[\x22\x27]?[^\s\x22\x27,}]+",
    re.IGNORECASE,
)
_VENDOR_KEY_PATTERN = re.compile(
    r"\b(?:sk|pk|rk)[_-](?:live|test|proj)?[_-]?[A-Za-z0-9]{8,}",
    re.IGNORECASE,
)
# A JSON web token is recognised by its own shape rather than by the field it sits in.
# A provider that quotes a credential back does not always name the field it came from,
# and an unlabelled `{"credential": "eyJ..."}` is exactly as much of a leak as a labelled
# one. Every JWS header is the base64url of a JSON object, so it begins `eyJ`; the third
# segment may be empty for an unsecured token.
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._~+/=-])eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"
)
_REDACTION_PATTERNS = (
    _BEARER_PATTERN,
    _SECRET_VALUE_PATTERN,
    _JWT_PATTERN,
    _VENDOR_KEY_PATTERN,
    _EMAIL_PATTERN,
)


def contains_sensitive_text(value: str) -> bool:
    """True when text carries an email, phone number, national identifier, or a secret.

    This is the single definition of the rule the design specification names in section
    19; the cognition prompt boundary reuses it rather than restating the patterns.
    """
    return any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS)


def contains_secret_or_email_text(value: str) -> bool:
    """True when text carries an email address or an explicit secret assignment.

    This is the strict subset of :func:`contains_sensitive_text` that stays meaningful
    for third-party copy nobody wrote as a persona field. The phone-number and
    national-identifier patterns are digit-run heuristics: they are the right rule for a
    generated persona, and the wrong rule for a price, a delivery window or a date range
    in an advertisement. Specification section 19 scopes identifier rejection to persona
    fields, so untrusted campaign text is screened with this narrower rule instead.
    """
    return any(pattern.search(value) for pattern in _SECRET_OR_EMAIL_PATTERNS)


def redact_secret_text(value: str) -> str:
    """Replace authorization headers, secret values, vendor key shapes and emails.

    Specification section 19 requires logs to redact authorization headers and likely
    secret patterns. This is that rule, applied wherever provider-authored text is kept
    rather than rejected: the value a secret label introduces is removed with the label,
    not just the label, because the label is never the part worth hiding.

    Two kinds of match are made, and the distinction is the guarantee:

    * LABELLED - an authorization header, or a secret label and the value it introduces,
      including a label spelled as an environment variable name.
    * VALUE-SHAPED, with no label needed - a vendor key shape (``sk-``/``pk-``/``rk-``), a
      JSON web token, an email address. A provider that echoes a credential does not
      always name the field it came from, so these are matched wherever they appear.

    It deliberately over-redacts rather than under-redacts within those shapes, and it is
    idempotent: the placeholder carries no label, no key shape and no address, so
    redacting twice gives the same text as redacting once. What it cannot claim is a
    fully opaque random token under an unlabelled field, which is indistinguishable from
    ordinary data; :func:`adlife.core.ports.cognition.redact_provider_body` closes the
    rest of that gap by refusing to store any body this module's detector still calls a
    secret.
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
    "REDACTION_PLACEHOLDER",
    "ConsumerTraits",
    "DomainModel",
    "PersonProfile",
    "contains_secret_or_email_text",
    "contains_sensitive_text",
    "redact_secret_text",
]
