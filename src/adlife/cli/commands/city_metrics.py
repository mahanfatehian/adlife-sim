"""Inspect receipt-backed synthetic metrics for a verified schema-v5 or v6 city run."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.run_store import CityRunStore
from adlife.cli.errors import CommandError, command_boundary, output_format
from adlife.cli.output import emit_result
from adlife.core.experiments.spatial_metrics import SpatialMetricSeries
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseAggregateSeries,
    SpatialResponseCampaignSeries,
    SpatialResponseChannelSeries,
)
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


def _response_series_line(
    label: str,
    series: (
        SpatialResponseAggregateSeries
        | SpatialResponseChannelSeries
        | SpatialResponseCampaignSeries
    ),
) -> str:
    def receipt(name: str) -> str:
        item = getattr(series, name)
        return f"{_number(item.numerator)}/{item.denominator}={_number(item.value)}"

    parts = [
        f"{label}: responses {receipt('response_count')}",
        f"response reach {receipt('response_reach')}",
        f"response frequency {receipt('response_frequency')}",
        f"rule sentiment delta {receipt('mean_rule_sentiment_delta')}",
        f"rule recall delta {receipt('mean_rule_recall_delta')}",
    ]
    if isinstance(series, (SpatialResponseAggregateSeries, SpatialResponseCampaignSeries)):
        for field, display in (
            ("brand_sentiment", "brand sentiment"),
            ("recall_strength", "recall strength"),
            ("purchase_intention_proxy", "purchase intention proxy"),
        ):
            state = getattr(series, field)
            parts.append(
                f"{display} {_number(state.initial_mean)} -> {_number(state.final_mean)} "
                f"(change {_number(state.mean_change)})"
            )
    return "; ".join(parts)


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    run_id: Annotated[str, typer.Argument(help="Saved schema-v5 or schema-v6 city run ID.")],
    layer: Annotated[
        Literal["attention", "response"],
        typer.Option("--layer", help="Metric evidence layer to derive."),
    ] = "attention",
) -> None:
    """Derive synthetic reach, frequency and notice metrics from verified artifacts."""
    try:
        validate_run_id(run_id)
    except UnsafeRunLocation:
        raise CommandError("invalid city run identifier") from None
    stored = CityRunStore(root).load(run_id)
    if layer == "response":
        response_metrics = response_metrics_for_stored_city_run(stored)
        document = response_metrics.model_dump(mode="json")
        if output_format() == "human":
            document["_lines"] = [
                f"synthetic response metrics, not observed outcomes: {run_id}",
                _response_series_line("overall", response_metrics.overall),
                *(
                    _response_series_line(series.channel, series)
                    for series in response_metrics.channels
                ),
                *(
                    _response_series_line(series.campaign_id, series)
                    for series in response_metrics.campaigns
                ),
            ]
        emit_result(document)
        return

    metrics = metrics_for_stored_city_run(stored)
    document = metrics.model_dump(mode="json")
    if output_format() == "human":
        document["_lines"] = [
            f"synthetic metrics, not observed outcomes: {run_id}",
            _series_line(metrics.overall),
            *(_series_line(series) for series in metrics.channels),
        ]
    emit_result(document)


__all__ = ["command"]
