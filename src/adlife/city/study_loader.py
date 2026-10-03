"""Read bounded local spatial-study definitions without reflecting their contents."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from adlife.core.domain.spatial_study import (
    SpatialStudyDefinition,
    parse_spatial_study_definition_json,
)

MAX_SPATIAL_STUDY_BYTES = 65_536


class SpatialStudyDefinitionError(ValueError):
    """A local study definition cannot be read or fails its versioned contract."""


def load_spatial_study_definition(path: Path) -> SpatialStudyDefinition:
    """Read one local UTF-8 study definition under a hard materialization bound."""
    try:
        with path.open("rb") as source:
            data = source.read(MAX_SPATIAL_STUDY_BYTES + 1)
    except OSError:
        raise SpatialStudyDefinitionError("spatial study definition could not be read") from None
    if len(data) > MAX_SPATIAL_STUDY_BYTES:
        raise SpatialStudyDefinitionError("spatial study definition is too large")
    try:
        document = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SpatialStudyDefinitionError("spatial study definition must be UTF-8") from None
    try:
        return parse_spatial_study_definition_json(document)
    except (ValidationError, ValueError):
        raise SpatialStudyDefinitionError(
            "spatial study definition failed schema or version validation"
        ) from None


__all__ = [
    "MAX_SPATIAL_STUDY_BYTES",
    "SpatialStudyDefinitionError",
    "load_spatial_study_definition",
]
