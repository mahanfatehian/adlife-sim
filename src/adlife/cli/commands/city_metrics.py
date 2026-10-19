"""Inspect receipt-backed synthetic metrics for a verified schema-v5 through v10 city run."""

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
from adlife.core.experiments.spatial_response_metrics_v2 import (
    SpatialCognitionMetricReceiptV2,
    SpatialCognitionStateReceiptV2,
    SpatialProviderDependentCognitionMetricsV2,
    SpatialResponseMetricsV2,
    SpatialRuleBaselineMetricsV2,
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
        | SpatialRuleBaselineMetricsV2
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
    if isinstance(
        series,
        (
            SpatialResponseAggregateSeries,
            SpatialResponseCampaignSeries,
            SpatialRuleBaselineMetricsV2,
        ),
    ):
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


def _rule_baseline_lines(baseline: SpatialRuleBaselineMetricsV2) -> list[str]:
    return [
        "Rule baseline (rule-owned)",
        _response_series_line("overall", baseline),
        *(_response_series_line(series.channel, series) for series in baseline.channels),
        *(_response_series_line(series.campaign_id, series) for series in baseline.campaigns),
        "purchase intention proxy: uncalibrated rule-owned proxy, not a probability or a "
        "forecast; purchases are not modeled",
    ]


def _cognition_mean(receipt: SpatialCognitionMetricReceiptV2) -> str:
    return f"{_number(receipt.numerator)}/{receipt.denominator}={_number(receipt.value)}"


def _cognition_state(receipt: SpatialCognitionStateReceiptV2) -> str:
    return (
        f"{_number(receipt.initial_mean)} -> {_number(receipt.final_mean)} "
        f"(change {_number(receipt.mean_change)})"
    )


def _counts(document: dict[str, int]) -> str:
    return "; ".join(f"{name.replace('_', '-')} {count}" for name, count in document.items())


def _cognition_lines(cognition: SpatialProviderDependentCognitionMetricsV2) -> list[str]:
    coverage = cognition.coverage
    reasons = {
        name: count
        for name, count in cognition.fallback_counts.model_dump(exclude={"schema_version"}).items()
        if count
    }
    neutralization = cognition.neutralization_counts
    categories = cognition.category_counts
    sources = cognition.source_counts
    # Category counts cover every selected terminal. Rules, fallback, and budget-skipped
    # terminals carry AdLife's fixed neutral vector, so say how many no provider chose.
    neutral_terminals = sources.rules + sources.fallback + sources.budget_skipped
    provider = (
        "provider: none (signals resolved offline)"
        if cognition.provider_profile_id is None
        else (
            f"provider: profile {cognition.provider_profile_id}; model "
            f"{cognition.provider_model_id}; revision {cognition.provider_revision_sha256}; "
            f"effective settings {cognition.effective_settings_sha256}"
        )
    )
    return [
        "Provider-dependent cognition (exploratory, not a campaign effect)",
        f"signal source: {cognition.mode}; effect scope bounded-signals-v1; "
        f"funded response limit {cognition.funded_response_limit}",
        provider,
        f"coverage: eligible responses {coverage.eligible_response_count}; "
        f"selected {coverage.selected_request_count}; funded {coverage.funded_request_count}; "
        f"budget-skipped {coverage.budget_skipped_count}; "
        f"unselected {coverage.unselected_response_count}; funded coverage "
        f"{cognition.funded_coverage.numerator}/{cognition.funded_coverage.denominator}="
        f"{_number(cognition.funded_coverage.value)}",
        "terminal sources: " + _counts(sources.model_dump(exclude={"schema_version"})),
        "fallback reasons: " + (_counts(reasons) if reasons else "none"),
        f"neutralization: neutralized responses {neutralization.neutralized_response_count}; "
        f"uncertain results {neutralization.uncertain_result_count}; "
        f"safety-neutralized results {neutralization.safety_neutralized_result_count}",
        f"terminal result categories: {categories.total} selected requests, of which "
        f"{neutral_terminals} are fixed neutral vectors from rules, fallback or "
        "budget-skipped terminals (not provider choices)",
        "affect categories: " + _counts(categories.affect.model_dump()),
        "relevance categories: " + _counts(categories.relevance.model_dump()),
        "credibility categories: " + _counts(categories.credibility.model_dump()),
        "salience categories: " + _counts(categories.salience.model_dump()),
        f"applied cognition per response: sentiment "
        f"{_cognition_mean(cognition.mean_applied_sentiment)}; recall "
        f"{_cognition_mean(cognition.mean_applied_recall)}",
        f"synthetic cognition state: sentiment {_cognition_state(cognition.synthetic_sentiment)}; "
        f"recall {_cognition_state(cognition.synthetic_recall)}",
        f"provenance: cognition input {cognition.cognition_input_sha256}; terminal stream "
        f"{cognition.cognition_terminal_stream_sha256}; cognition summary "
        f"{cognition.cognition_summary_sha256}; causal contract "
        f"{cognition.causal_contract_sha256}; influence policy {cognition.influence_policy_id} "
        f"{cognition.influence_policy_sha256}",
        "disclosure: bounded AI cognition signals are synthetic, provider-dependent "
        "exploratory evidence; they are not observed behavior, validated psychology, a "
        "prediction, or a campaign effect, and they change only the separate cognition track",
    ]


def _response_v2_lines(run_id: str, metrics: SpatialResponseMetricsV2) -> list[str]:
    return [
        f"synthetic bounded-cognition metrics, not observed outcomes: {run_id}",
        *_rule_baseline_lines(metrics.rule_baseline),
        *_cognition_lines(metrics.provider_dependent_cognition),
    ]


@command_boundary
def command(
    root: Annotated[Path, typer.Argument(help="Root containing city-runs/.")],
    run_id: Annotated[str, typer.Argument(help="Saved schema-v5 through schema-v10 run ID.")],
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
            if isinstance(response_metrics, SpatialResponseMetricsV2):
                document["_lines"] = _response_v2_lines(run_id, response_metrics)
            else:
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
