"""`adlife init NAME`: a new study directory copied from packaged resources."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.cli.errors import command_boundary
from adlife.cli.output import info
from adlife.cli.project import init_project


@command_boundary
def command(
    name: Annotated[str, typer.Argument(help="The study directory name to create.")],
    parent: Annotated[
        Path,
        typer.Option("--parent", help="The parent directory the study is created under."),
    ] = Path("."),
) -> None:
    """Create a new study directory from the packaged demo project."""
    root = init_project(parent / name)
    info(f"created study at {root}")
    info("next: adlife validate " + str(root))
