"""Create an offline, persisted synthetic city mobility run."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.catalog import CityCatalogError, UnknownCatalogCity, select_catalog_city
from adlife.city.loader import CityPackError, load_city_pack
from adlife.city.place_loader import CityPlaceSetError, load_city_place_set
from adlife.city.response_loader import (
    SpatialResponseInputError,
    load_spatial_response_input,
)
from adlife.city.runs import create_city_run
from adlife.city.spatial_loader import SpatialCampaignError, load_spatial_campaign_scenario
from adlife.cli.errors import CommandError, ExitCode, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.domain.city_run import (
    CityRunManifestV3,
    CityRunManifestV4,
    CityRunManifestV5,
    CityRunManifestV6,
)
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
    spatial_response: Annotated[
        Path | None,
        typer.Option(
            "--spatial-response",
            help=(
                "Local validated synthetic response assumptions JSON; requires --spatial-campaign."
            ),
        ),
    ] = None,
    agents: Annotated[int, typer.Option("--agents", min=1, max=30)] = 20,
    days: Annotated[int, typer.Option("--days", min=1, max=7)] = 7,
    seed: Annotated[int, typer.Option("--seed", min=0, max=2**63 - 1)] = 42,
) -> None:
    """Save a replayable mobility trace with optional attention and response evidence."""
    if (pack is None) == (city_id is None):
        raise CommandError("choose exactly one city pack path or --city-id")
    if spatial_response is not None and spatial_campaign is None:
        raise CommandError("--spatial-response requires --spatial-campaign")
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
    try:
        response_input = (
            None if spatial_response is None else load_spatial_response_input(spatial_response)
        )
    except SpatialResponseInputError as error:
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
        spatial_response=response_input,
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
        manifest,
        CityRunManifestV3 | CityRunManifestV4 | CityRunManifestV5 | CityRunManifestV6,
    ):
        document.update(
            place_set_sha256=stored.mobility.places.fingerprint,
            place_assignments_sha256=manifest.place_assignments_sha256,
        )
    if isinstance(manifest, CityRunManifestV4 | CityRunManifestV5 | CityRunManifestV6):
        if stored.opportunity_evaluation is None:
            raise RuntimeError("completed spatial city run is missing its evaluation")
        document.update(
            scenario_sha256=manifest.scenario_sha256,
            opportunity_stream_sha256=manifest.opportunity_stream_sha256,
            opportunity_summary_sha256=manifest.opportunity_summary_sha256,
            opportunity_stream_bytes=manifest.opportunity_stream_bytes,
            opportunity_count=manifest.opportunity_count,
            opportunity_counts=stored.opportunity_evaluation.counts.model_dump(mode="json"),
            opportunity_claim_scope="synthetic-opportunity-not-impression",
        )
    if isinstance(manifest, CityRunManifestV5 | CityRunManifestV6):
        if stored.attention_evaluation is None:
            raise RuntimeError("completed spatial city run is missing attention evidence")
        document.update(
            attention_model_id=manifest.spatial_attention_model_id,
            attention_claim_scope=stored.attention_evaluation.claim_scope,
            attention_notice_probability=stored.attention_evaluation.notice_probability,
            attention_stream_sha256=manifest.attention_stream_sha256,
            attention_summary_sha256=manifest.attention_summary_sha256,
            attention_stream_bytes=manifest.attention_stream_bytes,
            impression_count=manifest.impression_count,
            noticed_count=manifest.noticed_count,
            attention_counts=stored.attention_evaluation.counts.model_dump(mode="json"),
        )
    if isinstance(manifest, CityRunManifestV6):
        if stored.response_input is None or stored.response_evaluation is None:
            raise RuntimeError("completed response city run is missing response evidence")
        document.update(
            response_model_id=manifest.spatial_response_model_id,
            response_claim_scope=stored.response_evaluation.claim_scope,
            response_input_sha256=manifest.response_input_sha256,
            response_stream_sha256=manifest.response_stream_sha256,
            response_state_sha256=manifest.response_state_sha256,
            response_summary_sha256=manifest.response_summary_sha256,
            response_stream_bytes=manifest.response_stream_bytes,
            response_count=manifest.response_count,
            state_update_count=manifest.state_update_count,
            response_campaign_count=manifest.response_campaign_count,
            final_state_count=manifest.final_state_count,
            response_counts=stored.response_evaluation.counts.model_dump(mode="json"),
        )
    if output_format() == "human":
        suffix = ""
        if isinstance(manifest, CityRunManifestV5 | CityRunManifestV6):
            suffix = (
                f", {manifest.opportunity_count} synthetic opportunities, "
                f"{manifest.impression_count} impressions, {manifest.noticed_count} notices"
            )
        elif isinstance(manifest, CityRunManifestV4):
            suffix = f", {manifest.opportunity_count} synthetic opportunities"
        lines = [
            f"saved city run: {manifest.run_id} "
            f"({manifest.frame_count} verified minute frames{suffix})"
        ]
        if isinstance(manifest, CityRunManifestV6):
            if stored.response_evaluation is None:
                raise RuntimeError("completed response city run is missing response evidence")
            lines.extend(
                (
                    f"response evidence: {manifest.response_count} rule responses, "
                    f"{manifest.state_update_count} state updates, "
                    f"{manifest.response_campaign_count} campaigns, "
                    f"{manifest.final_state_count} final campaign states",
                    f"response contract: {manifest.spatial_response_model_id}; "
                    f"{stored.response_evaluation.claim_scope}",
                    f"response input SHA-256: {manifest.response_input_sha256}",
                    f"response stream SHA-256: {manifest.response_stream_sha256} "
                    f"({manifest.response_stream_bytes} bytes)",
                    f"response state SHA-256: {manifest.response_state_sha256}",
                    f"response summary SHA-256: {manifest.response_summary_sha256}",
                )
            )
        document["_lines"] = lines
    emit_result(document)


__all__ = ["command"]
