"""Create an offline, persisted synthetic city mobility run."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.catalog import CityCatalogError, UnknownCatalogCity, select_catalog_city
from adlife.city.loader import CityPackError, load_city_pack
from adlife.city.place_loader import CityPlaceSetError, load_city_place_set
from adlife.city.runs import create_city_run
from adlife.city.spatial_loader import SpatialCampaignError, load_spatial_campaign_scenario
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.domain.city_run import CityRunManifestV3, CityRunManifestV4
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id


@command_boundary
def command(
    output_root: Annotated[Path, typer.Option("--output-root", help="Artifact output root.")],
    run_id: Annotated[str, typer.Option("--run-id", help="New portable city run ID.")],
    pack: Annotated[
        Path | None,
        typer.Argument(help="Local validated city-pack JSON file; omit with --city-id."),
    ] = None,
    city_id: Annotated[
        str | None,
        typer.Option("--city-id", help="Verified packaged city ID from `city-catalog list`."),
    ] = None,
    places: Annotated[
        Path | None,
        typer.Option("--places", help="Local synthetic city place-set JSON file."),
    ] = None,
    spatial_campaign: Annotated[
        Path | None,
        typer.Option(
            "--spatial-campaign",
            help="Local validated spatial campaign JSON to freeze and evaluate.",
        ),
    ] = None,
    agents: Annotated[int, typer.Option("--agents", min=1, max=30)] = 20,
    days: Annotated[int, typer.Option("--days", min=1, max=7)] = 7,
    seed: Annotated[int, typer.Option("--seed", min=0, max=2**63 - 1)] = 42,
) -> None:
    """Save a replayable mobility trace with optional synthetic opportunity evidence."""
    if (pack is None) == (city_id is None):
        raise CommandError("choose exactly one city pack path or --city-id")
    try:
        validate_run_id(run_id)
    except UnsafeRunLocation:
        raise CommandError("invalid city run identifier") from None
    try:
        city_pack = select_catalog_city(city_id) if city_id is not None else load_city_pack(pack)
    except UnknownCatalogCity as error:
        raise CommandError(str(error)) from None
    except CityCatalogError as error:
        raise CommandError(str(error), ExitCode.ARTIFACT_ERROR) from None
    except CityPackError as error:
        raise CommandError(str(error)) from None
    try:
        place_set = None if places is None else load_city_place_set(places)
    except CityPlaceSetError as error:
        raise CommandError(str(error)) from None
    try:
        spatial_scenario = (
            None if spatial_campaign is None else load_spatial_campaign_scenario(spatial_campaign)
        )
    except SpatialCampaignError as error:
        raise CommandError(str(error)) from None
    stored = create_city_run(
        city_pack,
        root=output_root,
        run_id=run_id,
        seed=seed,
        agent_count=agents,
        days=days,
        places=place_set,
        spatial_scenario=spatial_scenario,
    )
    manifest = stored.manifest
    document = {
        "run_id": manifest.run_id,
        "city_id": stored.pack.city_id,
        "city_sha256": manifest.city_sha256,
        "trace_sha256": manifest.trace_sha256,
        "frame_count": manifest.frame_count,
        "position_count": manifest.position_count,
        "run_schema_version": manifest.schema_version,
        "directory": str(stored.directory),
    }
    if stored.mobility.places is not None and isinstance(
        manifest, CityRunManifestV3 | CityRunManifestV4
    ):
        document.update(
            place_set_sha256=stored.mobility.places.fingerprint,
            place_assignments_sha256=manifest.place_assignments_sha256,
        )
    if isinstance(manifest, CityRunManifestV4):
        if stored.opportunity_evaluation is None:
            raise RuntimeError("completed spatial city run is missing its evaluation")
        document.update(
            scenario_sha256=manifest.scenario_sha256,
            opportunity_stream_sha256=manifest.opportunity_stream_sha256,
            opportunity_summary_sha256=manifest.opportunity_summary_sha256,
            opportunity_stream_bytes=manifest.opportunity_stream_bytes,
            opportunity_count=manifest.opportunity_count,
            opportunity_counts=stored.opportunity_evaluation.counts.model_dump(mode="json"),
            claim_scope="synthetic-opportunity-not-impression",
        )
    if output_format() == "human":
        suffix = (
            ""
            if not isinstance(manifest, CityRunManifestV4)
            else f", {manifest.opportunity_count} synthetic opportunities"
        )
        document["_lines"] = [
            f"saved city run: {manifest.run_id} "
            f"({manifest.frame_count} verified minute frames{suffix})"
        ]
    emit_result(document)


__all__ = ["command"]
