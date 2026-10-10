"""Bounded access to packaged fictional workbench creative templates."""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from adlife.city.workbench_input import CreativeTemplate, CreativeTemplateCatalog

MAX_CREATIVE_TEMPLATE_CATALOG_BYTES = 65_536


class CreativeTemplateCatalogError(ValueError):
    """The packaged creative catalog is missing, oversized, or invalid."""


class UnknownCreativeTemplate(ValueError):
    """A requested packaged creative identifier does not exist."""


def _read_bounded(resource: Traversable | Path) -> bytes:
    try:
        with resource.open("rb") as source:
            data = source.read(MAX_CREATIVE_TEMPLATE_CATALOG_BYTES + 1)
    except OSError:
        raise CreativeTemplateCatalogError(
            "creative template catalog is missing or unreadable"
        ) from None
    if len(data) > MAX_CREATIVE_TEMPLATE_CATALOG_BYTES:
        raise CreativeTemplateCatalogError("creative template catalog exceeds its size limit")
    return data


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("creative template catalog contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"creative template catalog contains non-finite constant {value}")


def _parse_catalog(document: bytes) -> CreativeTemplateCatalog:
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError("creative template catalog must be an object")
        normalized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return CreativeTemplateCatalog.model_validate_json(normalized)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise CreativeTemplateCatalogError(
            "creative template catalog failed schema validation"
        ) from None


def load_creative_template_catalog(*, root: Path | None = None) -> CreativeTemplateCatalog:
    """Load the complete bounded packaged catalog in canonical template order."""
    resource: Traversable | Path
    if root is None:
        resource = files("adlife.city").joinpath("creative_templates.json")
    else:
        if root.is_symlink():
            raise CreativeTemplateCatalogError("creative template catalog has an unsafe location")
        resource = root / "creative_templates.json"
        if resource.is_symlink():
            raise CreativeTemplateCatalogError("creative template catalog has an unsafe location")
    return _parse_catalog(_read_bounded(resource))


def select_creative_template(
    template_id: str,
    *,
    root: Path | None = None,
) -> CreativeTemplate:
    """Select exactly one packaged template; unknown IDs never fall back."""
    catalog = load_creative_template_catalog(root=root)
    template = next((item for item in catalog.templates if item.template_id == template_id), None)
    if template is None:
        raise UnknownCreativeTemplate("creative template identifier is unknown")
    return template


__all__ = [
    "MAX_CREATIVE_TEMPLATE_CATALOG_BYTES",
    "CreativeTemplateCatalogError",
    "UnknownCreativeTemplate",
    "load_creative_template_catalog",
    "select_creative_template",
]
