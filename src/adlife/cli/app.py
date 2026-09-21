"""The AdLife Lab command tree: eight commands, one output contract.

Every command consumes the core through explicit factories wired in its own module;
this file only registers the tree and carries the global ``--format human|json|jsonl``
and ``--no-color`` options the output contract is built on. The eager ``--version``
from Task 1 is retained verbatim: help and version must not pay for imports the
commands make lazily.
"""

from __future__ import annotations

import sys
from typing import Literal

import typer

from adlife import __version__
from adlife.cli.commands.campaign import app as campaign_app
from adlife.cli.commands.compare import command as compare_command
from adlife.cli.commands.doctor import command as doctor_command
from adlife.cli.commands.init import command as init_command
from adlife.cli.commands.population import app as population_app
from adlife.cli.commands.replay import command as replay_command
from adlife.cli.commands.run import command as run_command
from adlife.cli.commands.validate import command as validate_command
from adlife.cli.errors import set_output_format
from adlife.core.simulation.runner import InterruptedRun

app = typer.Typer(
    name="adlife",
    help="AdLife Lab — synthetic consumer society and campaign simulator.",
    no_args_is_help=True,
)
app.add_typer(campaign_app, name="campaign")
app.add_typer(population_app, name="population")
app.command("init")(init_command)
app.command("validate")(validate_command)
app.command("run")(run_command)
app.command("replay")(replay_command)
app.command("compare")(compare_command)
app.command("doctor")(doctor_command)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"adlife {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the installed version.",
    ),
    output_format: Literal["human", "json", "jsonl"] = typer.Option(
        "human",
        "--format",
        help="Output mode: human prose, one JSON document, or JSONL streams.",
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable colored output (also honors NO_COLOR and TERM=dumb).",
    ),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["output_format"] = output_format
    ctx.obj["no_color"] = no_color
    set_output_format(output_format)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except InterruptedRun as interrupted:
        # The runner recorded the interrupted artifact; exit 130 as documented.
        print(str(interrupted), file=sys.stderr)
        raise SystemExit(130) from None
