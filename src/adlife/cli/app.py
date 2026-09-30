"""The AdLife Lab command tree and shared output contract.

Every command consumes the core through explicit factories wired in its own module;
this file only registers the tree and carries the global ``--format human|json|jsonl``
and ``--no-color`` options the output contract is built on. The geographic city
viewer is interactive and accepts human mode only. The eager ``--version`` callback
returns before a command is executed.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any, Literal

import typer
from typer import _click as click
from typer.core import TyperGroup

from adlife import __version__
from adlife.cli.commands.campaign import app as campaign_app
from adlife.cli.commands.city import command as city_command
from adlife.cli.commands.city_catalog import app as city_catalog_app
from adlife.cli.commands.city_import import command as city_import_command
from adlife.cli.commands.city_places import app as city_places_app
from adlife.cli.commands.city_replay import command as city_replay_command
from adlife.cli.commands.city_run import command as city_run_command
from adlife.cli.commands.city_view import command as city_view_command
from adlife.cli.commands.compare import command as compare_command
from adlife.cli.commands.demo import command as demo_command
from adlife.cli.commands.doctor import command as doctor_command
from adlife.cli.commands.init import command as init_command
from adlife.cli.commands.population import app as population_app
from adlife.cli.commands.replay import command as replay_command
from adlife.cli.commands.report import command as report_command
from adlife.cli.commands.run import command as run_command
from adlife.cli.commands.validate import command as validate_command
from adlife.cli.errors import _report, set_output_format
from adlife.core.simulation.runner import InterruptedRun


class OutputGroup(TyperGroup):
    """Keep parser failures inside the same machine-output boundary as commands."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **kwargs: Any,
    ) -> Any:
        arguments = list(sys.argv[1:] if args is None else args)
        fmt = "human"
        for index, argument in enumerate(arguments):
            if argument == "--format" and index + 1 < len(arguments):
                fmt = arguments[index + 1]
                break
            if argument.startswith("--format="):
                fmt = argument.partition("=")[2]
                break
        set_output_format(fmt)
        try:
            return super().main(
                args=arguments,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **kwargs,
            )
        except click.ClickException as error:
            if fmt not in {"json", "jsonl"}:
                error.show()
            else:
                _report(error.format_message(), error.exit_code, type(error).__name__)
            if standalone_mode:
                raise SystemExit(error.exit_code) from None
            raise


app = typer.Typer(
    cls=OutputGroup,
    name="adlife",
    help="AdLife Lab — synthetic consumer society and campaign simulator.",
    no_args_is_help=True,
)
app.add_typer(campaign_app, name="campaign")
app.add_typer(population_app, name="population")
app.add_typer(city_catalog_app, name="city-catalog")
app.add_typer(city_places_app, name="city-places")
app.command("init")(init_command)
app.command("validate")(validate_command)
app.command("run")(run_command)
app.command("replay")(replay_command)
app.command("compare")(compare_command)
app.command("doctor")(doctor_command)
app.command("report")(report_command)
app.command("demo")(demo_command)
app.command("city")(city_command)
app.command("city-import")(city_import_command)
app.command("city-run")(city_run_command)
app.command("city-replay")(city_replay_command)
app.command("city-view")(city_view_command)


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
