"""Validate local spatial campaign inputs against an immutable city pack."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.catalog import CityCatalogError, UnknownCatalogCity, select_catalog_city
from adlife.city.loader import CityPackError, load_city_pack
from adlife.city.spatial_loader import SpatialCampaignError, load_spatial_campaign_scenario
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.domain.spatial_campaign import validate_spatial_scenario_against_city

app = typer.Typer(
    name="city-campaign",
    help=(
        "Validate bounded spatial campaign inputs offline; no simulation or network "
        "request is made."
    ),
    no_args_is_help=True,
)


@app.command("validate")
@command_boundary
def validate_command(
    scenario: Annotated[Path, typer.Argument(help="Local spatial campaign JSON file.")],
    pack: Annotated[
        Path | None,
        typer.Argument(help="Local city-pack JSON file; omit with --city-id."),
    ] = None,
    city_id: Annotated[
        str | None,
        typer.Option("--city-id", help="Verified packaged city ID from `city-catalog list`."),
    ] = None,
) -> None:
    """Prove campaign references, windows, caps and exact road/coordinate binding."""
    if (pack is None) == (city_id is None):
        raise CommandError("choose exactly one city pack path or --city-id")
    try:
        city_pack = select_catalog_city(city_id) if city_id is not None else load_city_pack(pack)
        spatial_scenario = load_spatial_campaign_scenario(scenario)
    except UnknownCatalogCity as error:
        raise CommandError(str(error)) from None
    except CityCatalogError as error:
        raise CommandError(str(error), ExitCode.ARTIFACT_ERROR) from None
    except (CityPackError, SpatialCampaignError) as error:
        raise CommandError(str(error)) from None
    try:
        evidence = validate_spatial_scenario_against_city(spatial_scenario, city_pack)
    except ValueError as error:
        raise CommandError(str(error)) from None

    document: dict[str, object] = {
        "valid": True,
        "scenario_id": spatial_scenario.scenario_id,
        "scenario_sha256": evidence.scenario_sha256,
        "city_id": city_pack.city_id,
        "city_sha256": evidence.city_sha256,
        "campaign_count": evidence.campaign_count,
        "placement_count": evidence.placement_count,
        "billboard_count": evidence.billboard_count,
        "phone_count": evidence.phone_count,
        "max_billboard_binding_error_meters": (evidence.max_billboard_binding_error_meters),
    }
    if output_format() == "human":
        document["_lines"] = [
            f"valid spatial campaign scenario: {evidence.scenario_sha256}",
            (
                f"bound {evidence.placement_count} placements across "
                f"{evidence.campaign_count} fictional campaigns"
            ),
        ]
    emit_result(document)


__all__ = ["app", "validate_command"]
