"""Verified, offline-only access to packaged v2 city packs."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from pydantic import ValidationError

from adlife.city.loader import MAX_CITY_PACK_BYTES
from adlife.core.domain.city import CityPackV2, parse_city_pack_json
from adlife.core.domain.city_catalog import CityCatalog, parse_city_catalog_json

MAX_CITY_CATALOG_BYTES = 262_144


class CityCatalogError(ValueError):
    """The packaged catalog or one of its referenced packs failed integrity checks."""


class UnknownCatalogCity(ValueError):
    """A requested catalog identifier does not exist."""


def _read_bounded(resource: Traversable | Path, *, limit: int, label: str) -> bytes:
    try:
        with resource.open("rb") as source:
            data = source.read(limit + 1)
    except OSError:
        raise CityCatalogError(f"city catalog {label} is missing or unreadable") from None
    if len(data) > limit:
        raise CityCatalogError(f"city catalog {label} exceeds its size limit")
    return data


def _contained_file(root: Path, relative: Path, *, label: str) -> Path:
    if root.is_symlink():
        raise CityCatalogError(f"city catalog {label} has an unsafe location")
    try:
        resolved_root = root.resolve()
        candidate = resolved_root / relative
        resolved = candidate.resolve()
    except OSError:
        raise CityCatalogError(f"city catalog {label} has an unsafe location") from None
    if candidate.is_symlink() or not resolved.is_relative_to(resolved_root):
        raise CityCatalogError(f"city catalog {label} has an unsafe location")
    current = candidate.parent
    while current != resolved_root:
        if current.is_symlink():
            raise CityCatalogError(f"city catalog {label} has an unsafe location")
        current = current.parent
    return candidate


def _load_verified_catalog(
    *, root: Path | None = None
) -> tuple[CityCatalog, dict[str, CityPackV2]]:
    if root is None:
        package_root = files("adlife.city")
        catalog_resource: Traversable | Path = (
            _contained_file(package_root, Path("catalog.json"), label="index")
            if isinstance(package_root, Path)
            else package_root.joinpath("catalog.json")
        )
    else:
        catalog_resource = _contained_file(root, Path("catalog.json"), label="index")
    catalog_bytes = _read_bounded(catalog_resource, limit=MAX_CITY_CATALOG_BYTES, label="index")
    try:
        catalog = parse_city_catalog_json(catalog_bytes)
    except (ValidationError, ValueError, TypeError):
        raise CityCatalogError("city catalog index failed schema validation") from None

    packs: dict[str, CityPackV2] = {}
    for entry in catalog.entries:
        if root is None:
            pack_resource: Traversable | Path = (
                _contained_file(
                    package_root,
                    Path("catalog") / entry.resource_name,
                    label="pack resource",
                )
                if isinstance(package_root, Path)
                else package_root.joinpath("catalog", entry.resource_name)
            )
        else:
            pack_resource = _contained_file(
                root,
                Path("catalog") / entry.resource_name,
                label="pack resource",
            )
        pack_bytes = _read_bounded(
            pack_resource,
            limit=MAX_CITY_PACK_BYTES,
            label="pack resource",
        )
        try:
            parsed = parse_city_pack_json(pack_bytes)
        except (ValidationError, ValueError, TypeError):
            raise CityCatalogError("city catalog pack failed schema validation") from None
        if not isinstance(parsed, CityPackV2):
            raise CityCatalogError("city catalog pack failed schema validation")
        if parsed.fingerprint != entry.pack_sha256:
            raise CityCatalogError("city catalog pack failed integrity validation")
        if (
            parsed.schema_version != entry.pack_schema_version
            or parsed.city_id != entry.city_id
            or parsed.name != entry.display_name
            or parsed.bounds != entry.coverage
            or parsed.time_zone != entry.time_zone
            or parsed.source != entry.source
            or parsed.known_omissions != entry.known_omissions
        ):
            raise CityCatalogError("city catalog pack metadata does not match its index")
        packs[entry.city_id] = parsed
    return catalog, packs


def load_city_catalog(*, root: Path | None = None) -> CityCatalog:
    """Load and fully verify the local catalog and every referenced pack."""
    catalog, _ = _load_verified_catalog(root=root)
    return catalog


def select_catalog_city(city_id: str, *, root: Path | None = None) -> CityPackV2:
    """Select one verified local pack; unknown IDs never fall back to another city."""
    _, packs = _load_verified_catalog(root=root)
    try:
        return packs[city_id]
    except KeyError:
        raise UnknownCatalogCity("catalog city identifier is unknown") from None


__all__ = [
    "MAX_CITY_CATALOG_BYTES",
    "CityCatalogError",
    "UnknownCatalogCity",
    "load_city_catalog",
    "select_catalog_city",
]
