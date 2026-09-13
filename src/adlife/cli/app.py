import typer

from adlife import __version__
from adlife.cli.commands.campaign import app as campaign_app

app = typer.Typer(
    name="adlife",
    help="AdLife Lab — synthetic consumer society and campaign simulator.",
    no_args_is_help=True,
)
app.add_typer(campaign_app, name="campaign")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"adlife {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the installed version.",
    ),
) -> None:
    del version


def main() -> None:
    app()
