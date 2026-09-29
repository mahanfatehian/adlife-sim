"""Inspect the verified packaged city catalog without network access."""

from __future__ import annotations

from typing import Annotated

import typer

from adlife.city.catalog import CityCatalogError, load_city_catalog
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.domain.city_catalog import CityCatalog, CityCatalogEntry

app = typer.Typer(
    name="city-catalog",
    help="Inspect verified local city packs; no download or network request is made.",
    no_args_is_help=True,
)


def _verified_catalog() -> CityCatalog:
    try:
        return load_city_catalog()
    except CityCatalogError as error:
        raise CommandError(str(error), ExitCode.ARTIFACT_ERROR) from None


def _document(entry: CityCatalogEntry) -> dict[str, object]:
    return entry.model_dump(mode="json")


@app.command("list")
@command_boundary
def list_command(
    search: Annotated[
        str | None,
        typer.Option("--search", help="Case-insensitive local ID/name/source filter."),
    ] = None,
) -> None:
    """List locally packaged, integrity-verified city entries."""
    catalog = _verified_catalog()
    entries = catalog.entries
    if search is not None:
        needles = search.casefold().split()
        entries = tuple(
            entry
            for entry in entries
            if all(
                needle
                in " ".join(
                    (
                        entry.city_id,
                        entry.display_name,
                        entry.source.provider,
                        entry.source.dataset,
                    )
                ).casefold()
                for needle in needles
            )
        )
    documents = [_document(entry) for entry in entries]
    if output_format() == "human":
        lines = [
            f"{entry.city_id}: {entry.display_name} "
            f"[{entry.qualification}; schema v{entry.pack_schema_version}]"
            for entry in entries
        ] or ["no catalog cities matched"]
        emit_result({"_lines": lines})
    elif output_format() == "json":
        emit_result({"cities": documents})
    else:
        emit_result(documents)


@app.command("show")
@command_boundary
def show_command(
    city_id: Annotated[str, typer.Argument(help="Exact local catalog city identifier.")],
) -> None:
    """Show one verified catalog entry without loading an arbitrary path or URL."""
    catalog = _verified_catalog()
    entry = next((item for item in catalog.entries if item.city_id == city_id), None)
    if entry is None:
        raise CommandError("catalog city identifier is unknown")
    document = _document(entry)
    if output_format() == "human":
        document["_lines"] = [
            f"catalog city: {entry.city_id} — {entry.display_name}",
            f"qualification: {entry.qualification} ({entry.data_origin} data)",
            f"pack: schema v{entry.pack_schema_version} / {entry.pack_sha256}",
            f"source: {entry.source.dataset} {entry.source.version} ({entry.source.published_on})",
            f"license: {entry.source.license}; {entry.source.attribution}",
        ]
    emit_result(document)


__all__ = ["app", "list_command", "show_command"]
