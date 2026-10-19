"""Open a validated saved city run in the read-only loopback viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, TypedDict

import typer
import uvicorn

from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.web import create_city_app
from adlife.city.workbench_cognition_views import (
    WorkbenchCognitionRecordsViewV2,
    WorkbenchCognitionSummaryViewV2,
    build_workbench_cognition_records_view,
    build_workbench_cognition_summary_view,
    build_workbench_run_input_view_v4,
)
from adlife.city.workbench_transport import WorkbenchRunInputViewV4
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import info
from adlife.core.domain.city_cognition_v2 import CityCognitionEvaluationV2, CityCognitionInputV2
from adlife.core.domain.city_run import CityRunManifestV10
from adlife.core.ports.run_store import CorruptRunArtifact, UnsafeRunLocation, validate_run_id
from adlife.core.simulation.spatial_response import SpatialResponseEvaluation
from adlife.core.simulation.spatial_response_v2 import SpatialResponseEvaluationV2


class _BoundedCognitionViews(TypedDict, total=False):
    cognition_input: CityCognitionInputV2
    cognition_evaluation: CityCognitionEvaluationV2
    workbench_input_view: WorkbenchRunInputViewV4
    cognition_summary_view: WorkbenchCognitionSummaryViewV2
    cognition_records_view: WorkbenchCognitionRecordsViewV2


def _bounded_cognition_views(stored: StoredCityRun) -> _BoundedCognitionViews:
    """Build the frozen V10 projections the standalone viewer serves, before binding."""
    if (
        not isinstance(stored.response_evaluation, SpatialResponseEvaluationV2)
        or not isinstance(stored.cognition_input, CityCognitionInputV2)
        or not isinstance(stored.cognition_evaluation, CityCognitionEvaluationV2)
    ):
        raise CorruptRunArtifact("city viewer requires complete bounded cognition evidence")
    try:
        workbench_input_view = build_workbench_run_input_view_v4(stored)
        cognition_summary_view = build_workbench_cognition_summary_view(stored)
        cognition_records_view = build_workbench_cognition_records_view(stored)
    except ValueError:
        raise CorruptRunArtifact("city viewer could not verify bounded cognition views") from None
    if not isinstance(cognition_summary_view, WorkbenchCognitionSummaryViewV2) or not isinstance(
        cognition_records_view, WorkbenchCognitionRecordsViewV2
    ):
        raise CorruptRunArtifact("city viewer requires bounded cognition views")
    return _BoundedCognitionViews(
        cognition_input=stored.cognition_input,
        cognition_evaluation=stored.cognition_evaluation,
        workbench_input_view=workbench_input_view,
        cognition_summary_view=cognition_summary_view,
        cognition_records_view=cognition_records_view,
    )


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    run_id: Annotated[str, typer.Argument(help="Saved city run ID.")],
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """View a fully verified mobility run; the HTTP API cannot switch artifacts."""
    if output_format() != "human":
        raise CommandError("city dashboard is interactive; use human output format")
    try:
        validate_run_id(run_id)
    except UnsafeRunLocation:
        raise CommandError("invalid city run identifier") from None
    stored = CityRunStore(root).load(run_id)
    bounded = isinstance(stored.manifest, CityRunManifestV10)
    if (
        not bounded
        and stored.response_evaluation is not None
        and not isinstance(stored.response_evaluation, SpatialResponseEvaluation)
    ):
        raise CorruptRunArtifact("city viewer requires a supported response model")
    # V10 projections are built from the same verified evidence as the workbench
    # inspector; V1-V9 viewers keep their exact historical surface.
    bounded_views = _bounded_cognition_views(stored) if bounded else _BoundedCognitionViews()
    spatial_metrics = (
        metrics_for_stored_city_run(stored)
        if stored.manifest.schema_version in {5, 6, 7, 8, 9, 10}
        else None
    )
    spatial_response_metrics = (
        response_metrics_for_stored_city_run(stored)
        if stored.manifest.schema_version in {6, 7, 8, 9, 10}
        else None
    )
    application = create_city_app(
        stored.mobility,
        run_id=stored.manifest.run_id,
        run_schema_version=stored.manifest.schema_version,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        spatial_metrics=spatial_metrics,
        response_input=stored.response_input,
        response_evaluation=stored.response_evaluation,
        spatial_response_metrics=spatial_response_metrics,
        **bounded_views,
    )
    info(f"saved city run: http://127.0.0.1:{port} ({stored.manifest.run_id})")
    uvicorn.run(
        application,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )


__all__ = ["command"]
