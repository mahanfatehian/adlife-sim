"""Start the local deterministic city workbench on loopback."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from adlife.city.provider_runtime import build_provider_runtime_policy
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
            help="Dedicated local directory for verified workbench artifacts.",
        ),
    ],
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
    provider_origin: Annotated[
        list[str] | None,
        typer.Option(
            "--provider-origin",
            metavar="ID=URL",
            help=(
                "Approve an exact provider base URL for this process; repeat for more. "
                "The fixed local-ollama loopback approval is always present."
            ),
        ),
    ] = None,
    provider_key_env: Annotated[
        list[str] | None,
        typer.Option(
            "--provider-key-env",
            metavar="ID=ENVIRONMENT_VARIABLE",
            help=(
                "Bind one approved remote provider ID to an environment-variable name; "
                "repeat for more. Credential values are never command-line options."
            ),
        ),
    ] = None,
) -> None:
    """Operate CITY -> CAMPAIGN -> RUN -> INSPECT in the local deterministic workbench.

    A process-local bounded worker creates verified artifacts. Workspace city-pack upload
    is available but remains unreviewed for rights. Provider origins must be
    startup-approved. This profile includes no authentication or OAuth.
    """
    if output_format() != "human":
        raise CommandError("city workbench is interactive; use human output format")
    provider_runtime = build_provider_runtime_policy(
        provider_origins=provider_origin or (),
        provider_key_envs=provider_key_env or (),
    )
    try:
        pinned = prepare_workbench_workspace(workspace)
        verify_workbench_workspace(pinned)
        application = create_city_workbench_app(
            pinned,
            provider_runtime=provider_runtime,
        )
    except UnsafeWorkbenchWorkspace:
        raise CommandError("workbench workspace is unsafe or unavailable") from None

    info(f"city workbench: http://127.0.0.1:{port}/guide")
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
