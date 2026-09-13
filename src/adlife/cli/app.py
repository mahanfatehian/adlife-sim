import typer

from adlife import __version__

app = typer.Typer(
    name="adlife",
    help="AdLife Lab — synthetic consumer society and campaign simulator.",
    no_args_is_help=True,
)


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
