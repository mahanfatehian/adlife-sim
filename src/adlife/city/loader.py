"""Read bounded local city packs without exposing file contents in errors."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from adlife.core.domain.city import CityPackDocument, parse_city_pack_json

MAX_CITY_PACK_BYTES = 4_194_304


class CityPackError(ValueError):
    """A local city pack cannot be read or does not satisfy the versioned schema."""


def load_city_pack(path: Path | None) -> CityPackDocument:
    """Read a named pack, or the explicitly fictional offline demo pack."""
    try:
        if path is None:
            data = files("adlife.city").joinpath("demo_city.json").read_bytes()
        else:
            with path.open("rb") as source:
                data = source.read(MAX_CITY_PACK_BYTES + 1)
    except OSError:
        raise CityPackError("city pack could not be read") from None
    if len(data) > MAX_CITY_PACK_BYTES:
        raise CityPackError("city pack is too large")
    try:
        document = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CityPackError("city pack must be UTF-8") from None
    try:
        return parse_city_pack_json(document)
    except ValidationError as error:
        if any(item["type"] == "json_invalid" for item in error.errors(include_input=False)):
            raise CityPackError("city pack is not valid JSON") from None
        raise CityPackError("city pack failed schema or road-graph validation") from None
    except ValueError as error:
        if "valid UTF-8 JSON" in str(error):
            raise CityPackError("city pack is not valid JSON") from None
        raise CityPackError("city pack failed schema or version validation") from None


__all__ = ["MAX_CITY_PACK_BYTES", "CityPackError", "load_city_pack"]
