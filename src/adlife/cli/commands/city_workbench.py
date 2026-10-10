"""Start the local city workbench validation foundation on loopback."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    prepare_workbench_workspace,
    verify_workbench_workspace,
)
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import info


@command_boundary
def command(
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            help="Dedicated local directory for future verified workbench artifacts.",
        ),
    ],
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Serve the local scenario-validation foundation; run jobs are not enabled."""
    if output_format() != "human":
        raise CommandError("city workbench is interactive; use human output format")
    try:
        pinned = prepare_workbench_workspace(workspace)
        verify_workbench_workspace(pinned)
        application = create_city_workbench_app(pinned)
    except UnsafeWorkbenchWorkspace:
        raise CommandError("workbench workspace is unsafe or unavailable") from None

    info(f"city workbench: http://127.0.0.1:{port}")
    uvicorn.run(
        application,
        host="127.0.0.1",
        port=port,
        workers=1,
        reload=False,
        log_level="warning",
        access_log=False,
        server_header=False,
    )


__all__ = ["command"]
