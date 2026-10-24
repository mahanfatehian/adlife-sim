"""Bounded fictional starting points for the schema-2 audience editor.

Each template is the existing flat persona draft: descriptive context (name, age,
occupation, background, MBTI) precedes explicit causal interests, traits, and state
in the resource. No causal value is inferred from descriptive context.
"""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from adlife.city.workbench_input import WorkbenchPersonaDraftV2
from adlife.core.domain.person import DomainModel, contains_labelled_secret_member
from adlife.core.domain.serialization import canonical_json

MAX_PERSONA_TEMPLATE_CATALOG_BYTES = 32_768
_TEMPLATE_IDS = tuple(f"persona-{index:03d}" for index in range(1, 11))


class PersonaTemplateCatalogError(ValueError):
    """The fictional persona catalog is missing, oversized, or invalid."""


class PersonaTemplateCatalog(DomainModel):
    """Exactly ten immutable, validated persona drafts in stable ID order."""

    schema_version: Literal[1]
    templates: tuple[WorkbenchPersonaDraftV2, ...] = Field(min_length=10, max_length=10)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported persona template catalog schema version")
        return value

    @model_validator(mode="after")
    def fixed_sorted_templates(self) -> Self:
        templates = tuple(sorted(self.templates, key=lambda item: item.persona_id))
        if tuple(item.persona_id for item in templates) != _TEMPLATE_IDS:
            raise ValueError("persona template identifiers must match the fixed catalog")
        object.__setattr__(self, "templates", templates)
        if len(canonical_json(self).encode("utf-8")) > MAX_PERSONA_TEMPLATE_CATALOG_BYTES:
            raise ValueError("persona template catalog exceeds its canonical size limit")
        return self


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("persona template catalog contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("persona template catalog contains a non-finite value")


def _parse_catalog(document: bytes) -> PersonaTemplateCatalog:
    try:
        payload = json.loads(
            document.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict) or contains_labelled_secret_member(payload):
            raise ValueError("persona template catalog contains invalid structure")
        normalized = canonical_json(payload)
        if len(normalized.encode("utf-8")) > MAX_PERSONA_TEMPLATE_CATALOG_BYTES:
            raise ValueError("persona template catalog exceeds its canonical size limit")
        return PersonaTemplateCatalog.model_validate_json(normalized)
    except (UnicodeError, RecursionError, TypeError, ValueError):
        raise PersonaTemplateCatalogError(
            "persona template catalog failed schema validation"
        ) from None


def load_persona_template_catalog(*, root: Path | None = None) -> PersonaTemplateCatalog:
    """Read the bounded package resource or an explicit test/resource directory."""
    resource: Traversable | Path
    if root is None:
        resource = files("adlife.city").joinpath("persona_templates.json")
    else:
        resource = root / "persona_templates.json"
        if root.is_symlink() or resource.is_symlink():
            raise PersonaTemplateCatalogError("persona template catalog has an unsafe location")
    try:
        with resource.open("rb") as source:
            document = source.read(MAX_PERSONA_TEMPLATE_CATALOG_BYTES + 1)
    except OSError:
        raise PersonaTemplateCatalogError(
            "persona template catalog is missing or unreadable"
        ) from None
    if len(document) > MAX_PERSONA_TEMPLATE_CATALOG_BYTES:
        raise PersonaTemplateCatalogError("persona template catalog exceeds its size limit")
    return _parse_catalog(document)


__all__ = [
    "MAX_PERSONA_TEMPLATE_CATALOG_BYTES",
    "PersonaTemplateCatalog",
    "PersonaTemplateCatalogError",
    "load_persona_template_catalog",
]
