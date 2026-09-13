import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]

_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")
_NATIONAL_ID_PATTERN = re.compile(r"(?<!\d)\d{10}(?!\d)")
_SECRET_PATTERN = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?token|secret|password)\b\s*[:=]",
    re.IGNORECASE,
)
_SENSITIVE_PATTERNS = (
    _EMAIL_PATTERN,
    _PHONE_PATTERN,
    _NATIONAL_ID_PATTERN,
    _SECRET_PATTERN,
)


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
        if any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS for value in text_values):
            raise ValueError("persona text contains a sensitive identifier or secret")
        return self


__all__ = ["ConsumerTraits", "DomainModel", "PersonProfile"]
