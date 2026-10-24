"""Start the local guided app (the city workbench) on loopback."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from adlife.city.provider_runtime import ProviderOriginRegistry
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    prepare_workbench_workspace,
    verify_workbench_workspace,
)
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import info

DEFAULT_PORT = 8765

ProviderOriginOption = Annotated[
    list[str] | None,
    typer.Option(
        "--provider-origin",
        metavar="ID=URL",
        help=(
            "Approve an exact provider base URL at startup; repeat for more. Any other "
            "OpenAI-compatible address can also be connected from the guided pages."
        ),
    ),
]
ProviderKeyEnvOption = Annotated[
    list[str] | None,
    typer.Option(
        "--provider-key-env",
        metavar="ID=ENVIRONMENT_VARIABLE",
        help=(
            "Bind one startup-approved remote provider ID to an environment-variable name; "
            "repeat for more. Credential values are never command-line options."
        ),
    ),
]


def serve_guided_app(
    workspace: Path,
    *,
    port: int,
    provider_origin: list[str] | None,
    provider_key_env: list[str] | None,
    when_serving: Callable[[int, str], None] | None = None,
) -> None:
    """Pin the workspace, build the app and serve it on 127.0.0.1 until Ctrl+C.

    ``when_serving(port, url)`` runs just before the server starts; this command itself
    never opens a browser.
    """
    if output_format() != "human":
        raise CommandError("the guided app is interactive; use human output format")
    provider_runtime = ProviderOriginRegistry(
        provider_origins=provider_origin or (),
        provider_key_envs=provider_key_env or (),
        refused_local_ports=(port,),
    )
    try:
        pinned = prepare_workbench_workspace(workspace)
        verify_workbench_workspace(pinned)
        application = create_city_workbench_app(
            pinned,
            provider_runtime=provider_runtime,
            server_port=port,
        )
    except UnsafeWorkbenchWorkspace:
        raise CommandError("workbench workspace is unsafe or unavailable") from None

    url = f"http://127.0.0.1:{port}/guide"
    info(f"city workbench: http://127.0.0.1:{port}/guide")
    if when_serving is not None:
        when_serving(port, url)
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


@command_boundary
def command(
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            help="Dedicated local directory for verified workbench artifacts.",
        ),
    ],
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = DEFAULT_PORT,
    provider_origin: ProviderOriginOption = None,
    provider_key_env: ProviderKeyEnvOption = None,
) -> None:
    """Serve the guided pages at /guide and their local API on loopback.

    The root address redirects to /guide. A process-local bounded worker creates verified
    artifacts. Workspace city-pack upload is available but remains unreviewed for rights.
    AI provider addresses are approved at startup or connected from the guided pages for
    this process only. This profile includes no authentication or OAuth.
    """
    serve_guided_app(
        workspace,
        port=port,
        provider_origin=provider_origin,
        provider_key_env=provider_key_env,
    )


__all__ = [
    "DEFAULT_PORT",
    "ProviderKeyEnvOption",
    "ProviderOriginOption",
    "command",
    "serve_guided_app",
]
