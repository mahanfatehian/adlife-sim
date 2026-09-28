"""Start the read-only, loopback-only geographic mobility pilot."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from adlife.city.loader import CityPackError, load_city_pack
from adlife.city.web import create_city_app
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import info
from adlife.core.simulation.city_mobility import CityMobility


@command_boundary
def command(
    pack: Annotated[
        Path | None,
        typer.Option("--pack", help="Local city-pack JSON; defaults to a fictional offline grid."),
    ] = None,
    agents: Annotated[int, typer.Option("--agents", min=1, max=250)] = 20,
    days: Annotated[int, typer.Option("--days", min=1, max=31)] = 7,
    seed: Annotated[int, typer.Option("--seed", min=0)] = 42,
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Explore deterministic street mobility in a private local browser session."""
    if output_format() != "human":
        raise CommandError("city dashboard is interactive; use human output format")
    try:
        city_pack = load_city_pack(pack)
    except CityPackError as error:
        raise CommandError(str(error)) from None
    simulation = CityMobility(city_pack, seed=seed, agent_count=agents, days=days)
    info(f"city dashboard: http://127.0.0.1:{port} ({city_pack.name})")
    uvicorn.run(
        create_city_app(simulation),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )


__all__ = ["command"]
