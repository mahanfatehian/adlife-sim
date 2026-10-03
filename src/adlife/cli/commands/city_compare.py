"""Compare two matched verified spatial city runs without causal overclaiming."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from adlife.city.analysis import (
    compare_stored_city_response_runs,
    compare_stored_city_runs,
)
from adlife.city.run_store import CityRunStore
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.experiments.spatial_comparison import SpatialMetricDeltaSeries
from adlife.core.experiments.spatial_response_comparison import (
    SpatialResponseAggregateDeltaSeries,
    SpatialResponseCampaignDeltaSeries,
    SpatialResponseChannelDeltaSeries,
)
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


def _response_delta_line(
    label: str,
    series: (
        SpatialResponseAggregateDeltaSeries
        | SpatialResponseChannelDeltaSeries
        | SpatialResponseCampaignDeltaSeries
    ),
) -> str:
    def delta(name: str) -> str:
        return format(getattr(series, name), "+g")

    parts = [
        f"{label} deltas: responses {delta('response_count')}",
        f"response reach {delta('response_reach')}",
        f"response frequency {delta('response_frequency')}",
        f"rule sentiment delta {delta('mean_rule_sentiment_delta')}",
        f"rule recall delta {delta('mean_rule_recall_delta')}",
    ]
    if isinstance(
        series,
        (SpatialResponseAggregateDeltaSeries, SpatialResponseCampaignDeltaSeries),
    ):
        for field, display in (
            ("brand_sentiment", "brand sentiment"),
            ("recall_strength", "recall strength"),
            ("purchase_intention_proxy", "purchase intention proxy"),
        ):
            state = getattr(series, field)
            parts.append(
                f"{display} initial {format(state.initial_mean, '+g')}; "
                f"final {format(state.final_mean, '+g')}; "
                f"change {format(state.mean_change, '+g')}"
            )
    return "; ".join(parts)


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    control_run_id: Annotated[
        str, typer.Argument(help="Control schema-v5 or schema-v6 city run ID.")
    ],
    treatment_run_id: Annotated[
        str, typer.Argument(help="Treatment schema-v5 or schema-v6 city run ID.")
    ],
    layer: Annotated[
        Literal["attention", "response"],
        typer.Option("--layer", help="Metric evidence layer to compare."),
    ] = "attention",
) -> None:
    """Emit treatment-minus-control metrics for two matched saved city runs."""
    for run_id in (control_run_id, treatment_run_id):
        try:
            validate_run_id(run_id)
        except UnsafeRunLocation:
            raise CommandError("invalid city run identifier") from None
    store = CityRunStore(root)
    control = store.load(control_run_id)
    treatment = store.load(treatment_run_id)
    if layer == "response":
        response_comparison = compare_stored_city_response_runs(control, treatment)
        document = response_comparison.model_dump(mode="json")
        if output_format() == "human":
            document["_lines"] = [
                "synthetic response comparison, not a causal or observed-world result: "
                f"{control_run_id} -> {treatment_run_id}",
                f"opportunity classification: {response_comparison.opportunity_classification}",
                "response assumption classification: "
                f"{response_comparison.response_assumption_classification}",
                _response_delta_line("overall", response_comparison.overall),
                *(
                    _response_delta_line(series.channel, series)
                    for series in response_comparison.channels
                ),
                *(
                    _response_delta_line(series.campaign_id, series)
                    for series in response_comparison.campaigns
                ),
            ]
        emit_result(document)
        return

    comparison = compare_stored_city_runs(
        control,
        treatment,
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
