"""Inspect receipt-backed synthetic metrics for a verified schema-v5 city run."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.analysis import metrics_for_stored_city_run
from adlife.city.run_store import CityRunStore
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.experiments.spatial_metrics import SpatialMetricSeries
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id


def _number(value: float | int) -> str:
    return format(value, "g")


def _series_line(series: SpatialMetricSeries) -> str:
    def receipt(name: str) -> str:
        item = getattr(series, name)
        return f"{item.numerator}/{item.denominator}={_number(item.value)}"

    return (
        f"{series.channel}: opportunities {receipt('opportunity_count')}; "
        f"impressions {receipt('impression_count')}; noticed {receipt('noticed_count')}; "
        f"opportunity reach {receipt('opportunity_reach')}; "
        f"impression frequency {receipt('impression_frequency')}; "
        f"notice rate {receipt('notice_rate')}"
    )


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    run_id: Annotated[str, typer.Argument(help="Saved schema-v5 city run ID.")],
) -> None:
    """Derive synthetic reach, frequency and notice metrics from verified artifacts."""
    try:
        validate_run_id(run_id)
    except UnsafeRunLocation:
        raise CommandError("invalid city run identifier") from None
    metrics = metrics_for_stored_city_run(CityRunStore(root).load(run_id))
    document = metrics.model_dump(mode="json")
    if output_format() == "human":
        document["_lines"] = [
            f"synthetic metrics, not observed outcomes: {run_id}",
            _series_line(metrics.overall),
            *(_series_line(series) for series in metrics.channels),
        ]
    emit_result(document)


__all__ = ["command"]
