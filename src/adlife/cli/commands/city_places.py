"""Validate local synthetic place inputs against an immutable city pack."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.catalog import CityCatalogError, UnknownCatalogCity, select_catalog_city
from adlife.city.loader import CityPackError, load_city_pack
from adlife.city.place_loader import CityPlaceSetError, load_city_place_set
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.simulation.city_mobility import CityMobility

app = typer.Typer(
    name="city-places",
    help="Validate bounded synthetic place inputs; no download or network request is made.",
    no_args_is_help=True,
)


@app.command("validate")
@command_boundary
def validate_command(
    places: Annotated[Path, typer.Argument(help="Local city place-set JSON file.")],
    pack: Annotated[
        Path | None,
        typer.Argument(help="Local city-pack JSON file; omit with --city-id."),
    ] = None,
    city_id: Annotated[
        str | None,
        typer.Option("--city-id", help="Verified packaged city ID from `city-catalog list`."),
    ] = None,
    agents: Annotated[int, typer.Option("--agents", min=1, max=250)] = 20,
    days: Annotated[int, typer.Option("--days", min=1, max=31)] = 7,
    seed: Annotated[int, typer.Option("--seed", min=0)] = 42,
) -> None:
    """Prove binding, capacity, deterministic assignment and directed routability."""
    if (pack is None) == (city_id is None):
        raise CommandError("choose exactly one city pack path or --city-id")
    try:
        city_pack = select_catalog_city(city_id) if city_id is not None else load_city_pack(pack)
        place_set = load_city_place_set(places)
    except UnknownCatalogCity as error:
        raise CommandError(str(error)) from None
    except CityCatalogError as error:
        raise CommandError(str(error), ExitCode.ARTIFACT_ERROR) from None
    except (CityPackError, CityPlaceSetError) as error:
        raise CommandError(str(error)) from None
    simulation = CityMobility(
        city_pack,
        seed=seed,
        agent_count=agents,
        days=days,
        places=place_set,
    )
    document: dict[str, object] = {
        "valid": True,
        "city_id": city_pack.city_id,
        "city_sha256": city_pack.fingerprint,
        "place_set_sha256": place_set.fingerprint,
        "place_count": len(place_set.places),
        "agent_count": agents,
        "assignment_count": len(simulation.place_assignments),
    }
    if output_format() == "human":
        document["_lines"] = [
            f"valid synthetic place set: {place_set.fingerprint}",
            f"assigned {agents} synthetic agents across {len(place_set.places)} places",
        ]
    emit_result(document)


__all__ = ["app", "validate_command"]
