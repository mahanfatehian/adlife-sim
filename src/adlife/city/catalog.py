"""Verified, offline-only access to packaged v2 city packs."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from pydantic import ValidationError

from adlife.city.loader import MAX_CITY_PACK_BYTES
from adlife.core.domain.city import CityPackV2, parse_city_pack_json
from adlife.core.domain.city_catalog import (
    CityCatalog,
    CityCatalogEntry,
    parse_city_catalog_json,
)

MAX_CITY_CATALOG_BYTES = 262_144


class CityCatalogError(ValueError):
    """The packaged catalog or one of its referenced packs failed integrity checks."""


class UnknownCatalogCity(ValueError):
    """A requested catalog identifier does not exist."""


@dataclass(frozen=True, slots=True)
class VerifiedCatalogCity:
    """One catalog entry bound to the exact immutable pack it describes."""

    entry: CityCatalogEntry
    pack: CityPackV2


@dataclass(frozen=True, slots=True)
class _VerifiedCatalogSnapshot:
    catalog: CityCatalog
    cities: tuple[VerifiedCatalogCity, ...]


def _read_bounded(resource: Traversable | Path, *, limit: int, label: str) -> bytes:
    try:
        with resource.open("rb") as source:
            data = source.read(limit + 1)
    except OSError:
        raise CityCatalogError(f"city catalog {label} is missing or unreadable") from None
    if len(data) > limit:
        raise CityCatalogError(f"city catalog {label} exceeds its size limit")
    return data


def _strict_utf8(document: bytes) -> str:
    if document.startswith(
        (
            b"\xef\xbb\xbf",
            b"\xff\xfe",
            b"\xfe\xff",
            b"\xff\xfe\x00\x00",
            b"\x00\x00\xfe\xff",
        )
    ):
        raise ValueError("city catalog resource must be strict UTF-8 without a BOM")
    try:
        return document.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("city catalog resource must be strict UTF-8") from None


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


def _load_verified_catalog(*, root: Path | None = None) -> _VerifiedCatalogSnapshot:
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
        catalog = parse_city_catalog_json(_strict_utf8(catalog_bytes))
    except (ValidationError, ValueError, TypeError):
        raise CityCatalogError("city catalog index failed schema validation") from None

    verified: list[VerifiedCatalogCity] = []
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
            parsed = parse_city_pack_json(_strict_utf8(pack_bytes))
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
        verified.append(VerifiedCatalogCity(entry=entry, pack=parsed))
    return _VerifiedCatalogSnapshot(catalog=catalog, cities=tuple(verified))


@lru_cache(maxsize=1)
def _default_catalog_snapshot() -> _VerifiedCatalogSnapshot:
    return _load_verified_catalog()


def _catalog_snapshot(*, root: Path | None) -> _VerifiedCatalogSnapshot:
    if root is None:
        return _default_catalog_snapshot()
    return _load_verified_catalog(root=root)


def load_verified_catalog_cities(
    *,
    root: Path | None = None,
) -> tuple[VerifiedCatalogCity, ...]:
    """Load a stable entry/pack snapshot, caching only packaged resources."""
    return _catalog_snapshot(root=root).cities


def load_city_catalog(*, root: Path | None = None) -> CityCatalog:
    """Load and fully verify the local catalog and every referenced pack."""
    return _catalog_snapshot(root=root).catalog


def select_catalog_city(city_id: str, *, root: Path | None = None) -> CityPackV2:
    """Select one verified local pack; unknown IDs never fall back to another city."""
    selected = next(
        (
            verified.pack
            for verified in _catalog_snapshot(root=root).cities
            if verified.entry.city_id == city_id
        ),
        None,
    )
    if selected is None:
        raise UnknownCatalogCity("catalog city identifier is unknown")
    return selected


__all__ = [
    "MAX_CITY_CATALOG_BYTES",
    "CityCatalogError",
    "UnknownCatalogCity",
    "VerifiedCatalogCity",
    "load_city_catalog",
    "load_verified_catalog_cities",
    "select_catalog_city",
]
