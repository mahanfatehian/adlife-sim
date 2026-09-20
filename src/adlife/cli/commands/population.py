"""`adlife population generate`: a deterministic fictional population as YAML or JSON."""

from __future__ import annotations

from typing import Annotated, Literal

import typer
import yaml

from adlife.cli.errors import CommandError, command_boundary
from adlife.cli.output import emit_json
from adlife.core.simulation.population import generate_population, generate_relationships

app = typer.Typer(name="population", help="Generate and inspect fictional populations.")
LOCALES = ("fa-IR", "en-US")


@app.callback()
def population_root() -> None:
    """Operate on fictional populations."""


@app.command("generate")
@command_boundary
def generate(
    size: Annotated[
        int, typer.Option("--size", min=1, max=30, help="Population size (1-30).")
    ] = 20,
    seed: Annotated[int, typer.Option("--seed", min=0, help="The generation seed.")] = 42,
    locale: Annotated[str, typer.Option("--locale", help="fa-IR or en-US.")] = "fa-IR",
    out: Annotated[
        str | None,
        typer.Option("--out", help="Write YAML to this file instead of stdout."),
    ] = None,
    output_format: Annotated[
        Literal["yaml", "json"],
        typer.Option("--as", help="Output document format."),
    ] = "yaml",
) -> None:
    """Generate a stable fictional population and its connected relationships."""
    if locale not in LOCALES:
        raise CommandError(f"unsupported locale {locale!r}; expected one of: {', '.join(LOCALES)}")
    profiles = generate_population(size, seed, locale)
    relationships = generate_relationships(profiles, seed)
    document = {
        "schema_version": 1,
        "profiles": [
            {
                **profile.model_dump(mode="json", exclude={"traits"}),
                "traits": profile.traits.model_dump(mode="json"),
            }
            for profile in profiles
        ],
        "relationships": [relationship.model_dump(mode="json") for relationship in relationships],
    }
    if out is not None:
        from pathlib import Path

        Path(out).write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return
    if output_format == "json":
        emit_json(document)
        return
    print(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), end="")
