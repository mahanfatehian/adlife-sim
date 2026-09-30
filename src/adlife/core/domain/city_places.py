"""Versioned synthetic place inputs bound to one immutable city pack."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from adlife.core.domain.city import _validate_public_metadata
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

PlaceKind = Literal["home", "workplace", "leisure"]
PlaceProvenanceMethod = Literal[
    "operator-authored-fictional",
    "source-derived",
    "inferred",
]

_HASH_PATTERN = r"^[0-9a-f]{64}$"
_KIND_ORDER = {"home": 0, "leisure": 1, "workplace": 2}


class CityPlaceProvenance(DomainModel):
    """How one synthetic place entered the scenario, without private data."""

    method: PlaceProvenanceMethod
    reference: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("reference")
    @classmethod
    def public_reference(cls, value: str | None) -> str | None:
        return None if value is None else _validate_public_metadata(value)

    @model_validator(mode="after")
    def coherent_evidence(self) -> Self:
        authored = self.method == "operator-authored-fictional"
        if authored and self.reference is not None:
            raise ValueError("operator-authored fictional provenance cannot have a reference")
        if not authored and self.reference is None:
            raise ValueError(f"{self.method} provenance requires a reference")
        return self


class CityPlace(DomainModel):
    """One role-specific scenario point bound to a routable city node."""

    place_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    kind: PlaceKind
    node_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    label: str = Field(min_length=1, max_length=120)
    provenance: CityPlaceProvenance

    @field_validator("label")
    @classmethod
    def public_label(cls, value: str) -> str:
        return _validate_public_metadata(value)


class CityPlaceSet(DomainModel):
    """Canonical candidate points for deterministic synthetic routine assignment."""

    schema_version: Literal[1] = 1
    city_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    city_sha256: str = Field(pattern=_HASH_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    places: tuple[CityPlace, ...] = Field(min_length=3, max_length=10_000)

    @field_validator("name")
    @classmethod
    def public_name(cls, value: str) -> str:
        return _validate_public_metadata(value)

    @model_validator(mode="after")
    def canonical_places(self) -> Self:
        places = tuple(
            sorted(self.places, key=lambda item: (_KIND_ORDER[item.kind], item.place_id))
        )
        identifiers = tuple(place.place_id for place in places)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate city place identifier")
        role_nodes = tuple((place.kind, place.node_id) for place in places)
        if len(role_nodes) != len(set(role_nodes)):
            raise ValueError("a city place role and node pair must be unique")
        present = {place.kind for place in places}
        for required in ("home", "workplace", "leisure"):
            if required not in present:
                raise ValueError(f"city place set requires a {required} place")
        object.__setattr__(self, "places", places)
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("city place set JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"city place set JSON contains non-finite constant {value}")


def parse_city_place_set_json(document: str | bytes) -> CityPlaceSet:
    """Parse one strict place-set document without version-token coercion."""
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("city place set is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("city place set must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int:
        raise ValueError("city place set schema_version must be an integer")
    if version != 1:
        raise ValueError("unsupported city place set schema_version")
    normalized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return CityPlaceSet.model_validate_json(normalized)


__all__ = [
    "CityPlace",
    "CityPlaceProvenance",
    "CityPlaceSet",
    "PlaceKind",
    "PlaceProvenanceMethod",
    "parse_city_place_set_json",
]
