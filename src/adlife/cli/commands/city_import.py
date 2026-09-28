"""Publish a deterministic city pack from an operator-supplied local OSM extract."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from adlife.city.osm import MAX_OSM_INPUT_BYTES, OSMImportError, convert_overpass_json
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
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
) -> None:
    """Convert locally obtained OSM streets; no download or network request is made."""
    if output.exists() or output.is_symlink():
        raise CommandError("output city pack already exists", ExitCode.CONFLICT)
    try:
        with input_path.open("rb") as source:
            data = source.read(MAX_OSM_INPUT_BYTES + 1)
    except OSError:
        raise CommandError("OSM extract could not be read") from None
    try:
        converted = convert_overpass_json(
            data, city_id=city_id, name=name, largest_component=largest_component
        )
    except OSMImportError as error:
        raise CommandError(str(error)) from None
    encoded = (canonical_json(converted.pack) + "\n").encode("utf-8")
    _publish_new_file(output, encoded)
    document = {
        "city_id": converted.pack.city_id,
        "pack_sha256": converted.pack.fingerprint,
        "nodes": len(converted.pack.nodes),
        "roads": len(converted.pack.roads),
        "dropped_nodes": converted.dropped_nodes,
        "dropped_roads": converted.dropped_roads,
        "output": str(output),
    }
    if output_format() == "human":
        print(
            f"city pack: {output} ({document['nodes']} nodes, {document['roads']} roads; "
            f"dropped {document['dropped_nodes']} nodes and {document['dropped_roads']} roads)"
        )
    else:
        emit_result(document)


__all__ = ["command"]
