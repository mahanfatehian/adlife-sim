"""Publish a deterministic city pack from an operator-supplied local OSM extract."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from adlife.city.osm import (
    MAX_OSM_INPUT_BYTES,
    OSMImportError,
    convert_overpass_json,
    convert_overpass_json_v2,
)
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.domain.city import CityPackDocument
from adlife.core.domain.serialization import canonical_json


def _publish_new_file(path: Path, contents: bytes) -> None:
    """Atomically publish one file, never replacing an existing entry or symlink."""
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(contents)
            destination.flush()
            os.fsync(destination.fileno())
        os.link(temporary, path)
    except FileExistsError:
        raise CommandError("output city pack already exists", ExitCode.CONFLICT) from None
    except OSError:
        raise CommandError("output city pack could not be written") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_v2_provenance(
    time_zone: str | None, source_date: str | None, source_version: str | None
) -> tuple[str, str, str]:
    if time_zone is None or source_date is None or source_version is None:
        raise CommandError("schema 2 requires --time-zone, --source-date and --source-version")
    return time_zone, source_date, source_version


@command_boundary
def command(
    input_path: Annotated[Path, typer.Argument(help="Local Overpass JSON extract.")],
    output: Annotated[Path, typer.Option("--output", help="New city-pack JSON destination.")],
    city_id: Annotated[str, typer.Option("--city-id", help="Stable city-pack identifier.")],
    name: Annotated[str, typer.Option("--name", help="Public display name.")],
    largest_component: Annotated[
        bool,
        typer.Option(
            "--largest-component",
            help="Explicitly discard all but the largest strongly connected road component.",
        ),
    ] = False,
    schema_version: Annotated[
        int,
        typer.Option(
            "--schema-version",
            help="Output schema: 1 (legacy default) or 2 (geometry and provenance).",
        ),
    ] = 1,
    time_zone: Annotated[
        str | None,
        typer.Option("--time-zone", help="IANA city time zone required by schema 2."),
    ] = None,
    source_date: Annotated[
        str | None,
        typer.Option("--source-date", help="Extract publication date required by schema 2."),
    ] = None,
    source_version: Annotated[
        str | None,
        typer.Option("--source-version", help="Extract version required by schema 2."),
    ] = None,
) -> None:
    """Convert locally obtained OSM streets; no download or network request is made."""
    if schema_version not in {1, 2}:
        raise CommandError("city-pack schema version must be 1 or 2")
    provenance = (time_zone, source_date, source_version)
    v2_provenance: tuple[str, str, str] | None = None
    if schema_version == 1 and any(value is not None for value in provenance):
        raise CommandError("schema 2 provenance options require --schema-version 2")
    if schema_version == 2:
        v2_provenance = _require_v2_provenance(time_zone, source_date, source_version)
    if output.exists() or output.is_symlink():
        raise CommandError("output city pack already exists", ExitCode.CONFLICT)
    try:
        with input_path.open("rb") as source:
            data = source.read(MAX_OSM_INPUT_BYTES + 1)
    except OSError:
        raise CommandError("OSM extract could not be read") from None
    pack: CityPackDocument
    try:
        if schema_version == 1:
            converted = convert_overpass_json(
                data, city_id=city_id, name=name, largest_component=largest_component
            )
            pack = converted.pack
            document: dict[str, object] = {
                "city_id": pack.city_id,
                "pack_sha256": pack.fingerprint,
                "nodes": len(pack.nodes),
                "roads": len(pack.roads),
                "dropped_nodes": converted.dropped_nodes,
                "dropped_roads": converted.dropped_roads,
                "output": str(output),
            }
        else:
            if v2_provenance is None:
                raise CommandError(
                    "schema 2 requires --time-zone, --source-date and --source-version"
                )
            time_zone_v2, source_date_v2, source_version_v2 = v2_provenance
            converted_v2 = convert_overpass_json_v2(
                data,
                city_id=city_id,
                name=name,
                time_zone=time_zone_v2,
                source_date=source_date_v2,
                source_version=source_version_v2,
                largest_component=largest_component,
            )
            pack_v2 = converted_v2.pack
            pack = pack_v2
            document = {
                "city_id": pack_v2.city_id,
                "pack_sha256": pack_v2.fingerprint,
                "schema_version": pack_v2.schema_version,
                "source_sha256": pack_v2.source.source_sha256,
                "nodes": len(pack_v2.nodes),
                "roads": len(pack_v2.roads),
                "quality": asdict(converted_v2.quality),
                "output": str(output),
            }
    except OSMImportError as error:
        raise CommandError(str(error)) from None
    encoded = (canonical_json(pack) + "\n").encode("utf-8")
    _publish_new_file(output, encoded)
    if output_format() == "human":
        if schema_version == 1:
            print(
                f"city pack: {output} ({document['nodes']} nodes, {document['roads']} roads; "
                f"dropped {document['dropped_nodes']} nodes and "
                f"{document['dropped_roads']} roads)"
            )
        else:
            quality = document["quality"]
            assert isinstance(quality, dict)
            print(
                f"city pack v2: {output} ({document['nodes']} nodes, "
                f"{document['roads']} roads; dropped {quality['dropped_nodes']} nodes and "
                f"{quality['dropped_roads']} roads)"
            )
    else:
        emit_result(document)


__all__ = ["command"]
