"""Verify a frozen city mobility trace without mutating its source artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.run_store import CityRunStore
from adlife.city.runs import replay_city_run
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    run_id: Annotated[str, typer.Argument(help="Saved city run ID.")],
) -> None:
    """Recompute every minute frame and refuse a mismatched or partial artifact."""
    try:
        validate_run_id(run_id)
    except UnsafeRunLocation:
        raise CommandError("invalid city run identifier") from None
    result = replay_city_run(CityRunStore(root).load(run_id))
    document = {
        "run_id": result.run_id,
        "identical": result.identical,
        "city_sha256": result.city_sha256,
        "agents_sha256": result.agents_sha256,
        "trace_sha256": result.trace_sha256,
        "frame_count": result.frame_count,
        "position_count": result.position_count,
    }
    if result.place_set_sha256 is not None:
        document["place_set_sha256"] = result.place_set_sha256
        document["place_assignments_sha256"] = result.place_assignments_sha256
    if output_format() == "human":
        document["_lines"] = [
            f"city replay identical: {result.run_id} ({result.frame_count} minute frames)"
        ]
    emit_result(document)


__all__ = ["command"]
