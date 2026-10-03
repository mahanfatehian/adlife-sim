"""Compare two matched verified spatial city runs without causal overclaiming."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.city.analysis import compare_stored_city_runs
from adlife.city.run_store import CityRunStore
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.experiments.spatial_comparison import SpatialMetricDeltaSeries
from adlife.core.ports.run_store import UnsafeRunLocation, validate_run_id


def _delta_line(series: SpatialMetricDeltaSeries) -> str:
    def delta(name: str) -> str:
        return format(getattr(series, name), "+g")

    return (
        f"{series.channel} deltas: opportunities {delta('opportunity_count')}; "
        f"impressions {delta('impression_count')}; noticed {delta('noticed_count')}; "
        f"opportunity reach {delta('opportunity_reach')}; "
        f"impression frequency {delta('impression_frequency')}; "
        f"notice rate {delta('notice_rate')}"
    )


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    control_run_id: Annotated[str, typer.Argument(help="Control schema-v5 city run ID.")],
    treatment_run_id: Annotated[str, typer.Argument(help="Treatment schema-v5 city run ID.")],
) -> None:
    """Emit treatment-minus-control metrics for two matched saved city runs."""
    for run_id in (control_run_id, treatment_run_id):
        try:
            validate_run_id(run_id)
        except UnsafeRunLocation:
            raise CommandError("invalid city run identifier") from None
    store = CityRunStore(root)
    comparison = compare_stored_city_runs(
        store.load(control_run_id),
        store.load(treatment_run_id),
    )
    document = comparison.model_dump(mode="json")
    if output_format() == "human":
        document["_lines"] = [
            "synthetic comparison, not a causal or observed-world result: "
            f"{control_run_id} -> {treatment_run_id}",
            f"classification: {comparison.classification}",
            comparison.interpretation,
            _delta_line(comparison.overall),
            *(_delta_line(series) for series in comparison.channels),
        ]
    emit_result(document)


__all__ = ["command"]
