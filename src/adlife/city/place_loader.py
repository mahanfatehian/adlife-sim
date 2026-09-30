"""Read bounded local synthetic place sets without exposing their contents."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from adlife.core.domain.city_places import CityPlaceSet, parse_city_place_set_json

MAX_CITY_PLACE_SET_BYTES = 1_048_576


class CityPlaceSetError(ValueError):
    """A local place set cannot be read or does not satisfy its versioned schema."""


def load_city_place_set(path: Path) -> CityPlaceSet:
    """Read and validate one local, bounded place-set JSON document."""
    try:
        with path.open("rb") as source:
            data = source.read(MAX_CITY_PLACE_SET_BYTES + 1)
    except OSError:
        raise CityPlaceSetError("city place set could not be read") from None
    if len(data) > MAX_CITY_PLACE_SET_BYTES:
        raise CityPlaceSetError("city place set is too large")
    try:
        document = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CityPlaceSetError("city place set must be UTF-8") from None
    try:
        return parse_city_place_set_json(document)
    except (ValidationError, ValueError):
        raise CityPlaceSetError("city place set failed schema or version validation") from None


__all__ = ["MAX_CITY_PLACE_SET_BYTES", "CityPlaceSetError", "load_city_place_set"]
