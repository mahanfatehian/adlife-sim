"""Open a validated saved city run in the read-only loopback viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from adlife.city.run_store import CityRunStore
from adlife.city.web import create_city_app
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import info
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    run_id: Annotated[str, typer.Argument(help="Saved city run ID.")],
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """View a fully verified mobility run; the HTTP API cannot switch artifacts."""
    if output_format() != "human":
        raise CommandError("city dashboard is interactive; use human output format")
    try:
        validate_run_id(run_id)
    except UnsafeRunLocation:
        raise CommandError("invalid city run identifier") from None
    stored = CityRunStore(root).load(run_id)
    info(f"saved city run: http://127.0.0.1:{port} ({stored.manifest.run_id})")
    uvicorn.run(
        create_city_app(stored.mobility, run_id=stored.manifest.run_id),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )


__all__ = ["command"]
