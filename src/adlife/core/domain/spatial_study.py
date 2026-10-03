"""Strict, path-free definitions for repeated-seed spatial studies."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal, Self, TypeAlias

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from adlife.core.domain.identifiers import (
    PortableRunIdentifierError,
    validate_portable_run_identifier,
)
from adlife.core.domain.json_values import MAX_JSON_DEPTH
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

SpatialStudyDesign: TypeAlias = Literal["a-a", "paired-contrast"]
SpatialStudyAnalysisScope: TypeAlias = Literal[
    "attention",
    "attention-and-response",
]


def _portable_identifier(value: str) -> str:
    try:
        return validate_portable_run_identifier(value)
    except PortableRunIdentifierError as error:
        raise ValueError(f"study identifier is invalid: {error}") from None


class SpatialStudyPair(DomainModel):
    """One common-random-number control/treatment seed pair."""

    model_config = ConfigDict(hide_input_in_errors=True)

    seed: int = Field(ge=0, le=2**63 - 1)
    control_run_id: str
    treatment_run_id: str

    @field_validator("control_run_id", "treatment_run_id")
    @classmethod
    def portable_run_identifier(cls, value: str) -> str:
        return _portable_identifier(value)


class SpatialStudyDefinition(DomainModel):
    """A bounded read-only study over already committed city runs."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    study_id: str
    design: SpatialStudyDesign
    analysis_scope: SpatialStudyAnalysisScope
    pairs: tuple[SpatialStudyPair, ...] = Field(min_length=2, max_length=100)

    @field_validator("study_id")
    @classmethod
    def portable_study_identifier(cls, value: str) -> str:
        return _portable_identifier(value)

    @model_validator(mode="after")
    def canonical_protocol(self) -> Self:
        validated_pairs = tuple(
            SpatialStudyPair.model_validate(pair.model_dump(mode="python", round_trip=True))
            for pair in self.pairs
        )
        pairs = tuple(sorted(validated_pairs, key=lambda pair: pair.seed))
        if tuple(pair.seed for pair in pairs) != tuple(range(len(pairs))):
            raise ValueError("spatial study pair seeds must be unique and contiguous from zero")

        seed_by_run: dict[str, int] = {}
        for pair in pairs:
            for run_id in {pair.control_run_id, pair.treatment_run_id}:
                previous_seed = seed_by_run.setdefault(run_id, pair.seed)
                if previous_seed != pair.seed:
                    raise ValueError(
                        "spatial study run identifiers cannot be reused across seed pairs"
                    )
            if self.design == "paired-contrast" and pair.control_run_id == pair.treatment_run_id:
                raise ValueError("paired contrast arms must name different saved runs")

        object.__setattr__(self, "pairs", pairs)
        return self

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self).encode("utf-8")).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("spatial study definition JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"spatial study definition JSON contains non-finite constant {value}")


def _refuse_overdeep(payload: object) -> None:
    pending: list[tuple[object, int]] = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, dict):
            if depth >= MAX_JSON_DEPTH:
                raise ValueError("spatial study definition exceeds the JSON nesting limit")
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            if depth >= MAX_JSON_DEPTH:
                raise ValueError("spatial study definition exceeds the JSON nesting limit")
            pending.extend((item, depth + 1) for item in value)


def _refuse_unknown_members(payload: dict[str, Any]) -> None:
    allowed_definition = {
        "schema_version",
        "study_id",
        "design",
        "analysis_scope",
        "pairs",
    }
    if not set(payload) <= allowed_definition:
        raise ValueError("spatial study definition contains extra fields")
    pairs = payload.get("pairs")
    if isinstance(pairs, list):
        allowed_pair = {"seed", "control_run_id", "treatment_run_id"}
        if any(isinstance(pair, dict) and not set(pair) <= allowed_pair for pair in pairs):
            raise ValueError("spatial study definition contains extra pair fields")


def parse_spatial_study_definition_json(
    document: str | bytes,
) -> SpatialStudyDefinition:
    """Parse one strict study definition without version-token coercion."""
    if isinstance(document, bytes):
        try:
            document = document.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("spatial study definition is not valid UTF-8 JSON") from None
    try:
        payload = json.loads(
            document,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("spatial study definition is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("spatial study definition must be a JSON object")
    _refuse_overdeep(payload)
    _refuse_unknown_members(payload)
    version = payload.get("schema_version")
    if type(version) is not int:
        raise ValueError("spatial study definition schema_version must be an integer")
    if version != 1:
        raise ValueError("unsupported spatial study definition schema_version")
    try:
        normalized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (ValueError, RecursionError):
        raise ValueError("spatial study definition is not valid UTF-8 JSON") from None
    try:
        return SpatialStudyDefinition.model_validate_json(normalized)
    except ValidationError:
        # Pydantic locations may contain attacker-controlled mapping keys. The domain
        # constructors remain detailed for trusted Python callers; the public parser is
        # deliberately content-free because it accepts untrusted JSON.
        raise ValueError("spatial study definition failed schema validation") from None


__all__ = [
    "SpatialStudyAnalysisScope",
    "SpatialStudyDefinition",
    "SpatialStudyDesign",
    "SpatialStudyPair",
    "parse_spatial_study_definition_json",
]
