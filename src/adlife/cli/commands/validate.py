"""`adlife validate PATH`: the gate a project passes before anything runs it."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.cli.errors import CommandError, command_boundary
from adlife.cli.output import emit_json, info
from adlife.cli.project import load_project


@command_boundary
def command(
    path: Annotated[Path, typer.Argument(help="The study directory to validate.")],
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show the full validation detail."),
    ] = False,
) -> None:
    """Validate a study directory without running anything."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        project = load_project(path)
    except CommandError as error:
        errors.append(str(error))
    else:
        population = len(project.scenario.population)
        campaigns = len(project.scenario.campaigns)
        days = project.scenario.days
        if verbose:
            info(
                f"scenario {project.scenario.scenario_id}: {population} agents, "
                f"{campaigns} campaigns, {days} day(s)"
            )
        if not campaigns:
            warnings.append("the project defines no campaigns; a run will produce no exposure")

    document = {
        "valid": not errors,
        "errors": errors,
    }
    emit_json(document)
    for warning in warnings:
        info(warning)
    if errors:
        info("validation failed: " + errors[0])
        raise SystemExit(2)
