"""Read bounded local spatial-response inputs without reflecting their contents."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from adlife.core.domain.spatial_response import (
    SpatialResponseInput,
    parse_spatial_response_input_json,
)

MAX_SPATIAL_RESPONSE_INPUT_BYTES = 2_097_152


class SpatialResponseInputError(ValueError):
    """A local response document cannot be read or fails its versioned contract."""


def load_spatial_response_input(path: Path) -> SpatialResponseInput:
    """Read one local UTF-8 response input under a hard materialization bound."""
    try:
        with path.open("rb") as source:
            data = source.read(MAX_SPATIAL_RESPONSE_INPUT_BYTES + 1)
    except OSError:
        raise SpatialResponseInputError("spatial response input could not be read") from None
    if len(data) > MAX_SPATIAL_RESPONSE_INPUT_BYTES:
        raise SpatialResponseInputError("spatial response input is too large")
    try:
        document = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SpatialResponseInputError("spatial response input must be UTF-8") from None
    try:
        return parse_spatial_response_input_json(document)
    except (ValidationError, ValueError):
        raise SpatialResponseInputError(
            "spatial response input failed schema or version validation"
        ) from None


__all__ = [
    "MAX_SPATIAL_RESPONSE_INPUT_BYTES",
    "SpatialResponseInputError",
    "load_spatial_response_input",
]
