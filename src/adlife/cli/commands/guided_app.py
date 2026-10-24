"""``adlife app``: start the guided app with sensible defaults and open it in the browser."""

from __future__ import annotations

import socket
import threading
import time
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from adlife.cli.commands.city_workbench import (
    DEFAULT_PORT,
    ProviderKeyEnvOption,
    ProviderOriginOption,
    serve_guided_app,
)
from adlife.cli.errors import command_boundary

DEFAULT_APP_WORKSPACE = Path("adlife-workspace")
_BROWSER_WAIT_SECONDS = 30.0


def _open_when_ready(port: int, url: str) -> None:
    """Open the default browser once the server accepts connections (best effort)."""
    deadline = time.monotonic() + _BROWSER_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        return
    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - a missing browser never stops the server
        return


def _open_in_background(port: int, url: str) -> None:
    threading.Thread(target=_open_when_ready, args=(port, url), daemon=True).start()


@command_boundary
def command(
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            help="Local directory for saved runs and settings (created if missing).",
        ),
    ] = DEFAULT_APP_WORKSPACE,
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = DEFAULT_PORT,
    open_browser: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the app in the default browser."),
    ] = True,
    provider_origin: ProviderOriginOption = None,
    provider_key_env: ProviderKeyEnvOption = None,
) -> None:
    """Start the AdLife app in your browser: set up a study, watch it run, read the report.

    Everything runs on this computer at http://127.0.0.1:PORT/guide. Connect an online
    OpenAI-compatible AI or a model server on this computer from the app itself, or use
    practice answers with no AI at all. Press Ctrl+C to stop.
    """
    serve_guided_app(
        workspace,
        port=port,
        provider_origin=provider_origin,
        provider_key_env=provider_key_env,
        when_serving=_open_in_background if open_browser else None,
    )


__all__ = ["DEFAULT_APP_WORKSPACE", "command"]
