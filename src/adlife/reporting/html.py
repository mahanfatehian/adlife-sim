"""The self-contained HTML report: one stored run (or comparison) in, one file out.

The module splits in two. :func:`build_report_document` is the pure data layer - it
folds the stored events through :class:`~adlife.core.experiments.metrics.MetricsCalculator`
and assembles every number, distribution, diary, and reproducibility field the page
shows, so a report's claims are re-derivable from the artifact it names. The render
functions then turn that document into HTML with Jinja2 autoescape, an inlined
stylesheet, one inlined Plotly runtime, and chart payloads serialized through
:func:`_safe_json`, which escapes ``<`` so no data can close its own script element.
No model is called; every narrative string is a deterministic template.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment

from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.experiments.design import ComparisonResult
from adlife.core.experiments.metrics import METRIC_NAMES, MetricsCalculator, RunMetrics
from adlife.core.ports.run_store import ProviderUsageLog, StoredRun
from adlife.reporting.resources import plotly_runtime, report_css, report_template

DISCLOSURE = (
    "This report describes a synthetic and exploratory simulation of fictional "
    "consumers. No real person is modeled, no real behavior is observed, and nothing "
    "here is a forecast of any real market."
)

LIMITATIONS: tuple[str, ...] = (
    "Outcomes are simulations of a fictional society; they are not a forecast of real "
    "consumer behavior and carry no external validity claims.",
    "The population is generated from documented routines and trait ranges; it is "
    "plausible, not representative, of any real population.",
    "Cognition in the rules mode is a documented formula, and in other modes is a "
    "language model whose behavior may drift; both are recorded in the manifest.",
    "Paired comparisons and sensitivity sweeps derive their honesty from same-seed "
    "pairs; differences are relative to this simulator only.",
    "Purchases are a proxy signal, not a transaction ledger.",
)


def _safe_json(payload: Any) -> str:
    """Serialize a chart payload so it cannot close its own script element."""
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _distribution(values: Sequence[float], buckets: int = 5) -> list[dict[str, object]]:
    """Histogram a bounded [0, 1] metric into ``buckets`` labeled bins."""
    if not values:
        return []
    width = 1.0 / buckets
    labels = [f"{index * width:.0%}-{(index + 1) * width:.0%}" for index in range(buckets)]
    counts: list[int] = [0] * buckets
    for value in values:
        index = min(buckets - 1, max(0, int(value * buckets)))
        counts[index] += 1
    return [{"bucket": label, "count": count} for label, count in zip(labels, counts, strict=True)]


def _counts(series: Sequence[str]) -> list[dict[str, object]]:
    return [{"bucket": label, "count": count} for label, count in Counter(series).most_common()]


class ReportBuilder:
    """Assemble the report document from one stored run."""

    def __init__(self, stored_run: StoredRun, usage: ProviderUsageLog | None = None) -> None:
        self._stored = stored_run
        self._usage = usage
        self._calculator = MetricsCalculator()

    def build(self) -> dict[str, Any]:
        manifest = self._stored.manifest
        scenario = self._stored.scenario
        final_states = self._final_states()
        overall = self._metrics(None)
        per_campaign = {
            campaign.campaign_id: self._metrics(campaign.campaign_id)
            for campaign in scenario.campaigns
        }
        campaign_names = {campaign.campaign_id: campaign.name for campaign in scenario.campaigns}
        return {
            "disclosure": DISCLOSURE,
            "run_id": manifest.run_id,
            "manifest": self._manifest_block(manifest),
            "executive_summary": self._summary(overall),
            "population": self._population_block(scenario),
            "campaigns": [
                {
                    "campaign_id": campaign_id,
                    "name": campaign_names.get(campaign_id, campaign_id),
                    "metrics": metrics.as_mapping(),
                    "details": {
                        name: {
                            "numerator": value.numerator,
                            "denominator": value.denominator,
                            "sources": list(value.sources),
                        }
                        for name, value in metrics.details.items()
                    },
                }
                for campaign_id, metrics in per_campaign.items()
            ],
            "overall": overall.as_mapping(),
            "state_distributions": self._state_blocks(final_states),
            "word_of_mouth": self._word_of_mouth_block(),
            "diaries": self._diaries(),
            "provider_usage": self._usage_block(),
            "limitations": list(LIMITATIONS),
            "metrics_catalog": list(METRIC_NAMES),
        }

    # -- blocks ------------------------------------------------------------

    def _manifest_block(self, manifest: RunManifest) -> dict[str, Any]:
        return {
            "run_id": manifest.run_id,
            "scenario_id": manifest.scenario_id,
            "scenario_hash": manifest.scenario_hash,
            "seed": manifest.seed,
            "package_version": manifest.package_version,
            "git_sha": manifest.git_sha,
            "lockfile_sha256": manifest.lockfile_sha256,
            "provider": manifest.provider,
            "model_id": manifest.model_id,
            "prompt_version": manifest.prompt_version,
            "platform": manifest.platform,
            "created_at": self._stored.created_at,
            "status": self._stored.status,
            "final_minute": self._stored.result.final_minute if self._stored.result else None,
            "event_count": len(self._stored.events),
        }

    def _summary(self, overall: RunMetrics) -> str:
        campaigns = len(self._stored.scenario.campaigns)
        agents = len(self._stored.scenario.population)
        return (
            f"{agents} fictional agents encountered {overall.eligible:.0f} eligible "
            f"placements from {campaigns} campaign(s); {overall.reach:.0f} distinct "
            f"agents were reached at an average frequency of {overall.frequency:.2f}, "
            f"and {overall.noticed:.0f} impressions cut through attention "
            f"({overall.notice_rate:.0%} notice rate)."
        )

    def _metrics(self, campaign_id: str | None) -> RunMetrics:
        states = self._stored.scenario.initial_states
        return self._calculator.calculate(
            self._stored.events,
            campaign_id=campaign_id,
            initial_states=states,
            final_states=self._final_states(),
            usage=self._usage,
        )

    def _final_states(self) -> tuple[ConsumerState, ...]:
        checkpoints = self._stored.checkpoints
        return checkpoints[-1].states if checkpoints else ()

    def _population_block(self, scenario: Scenario) -> dict[str, Any]:
        profiles = scenario.population
        return {
            "size": len(profiles),
            "occupations": _counts([profile.occupation for profile in profiles]),
            "income_bands": _counts([profile.income_band for profile in profiles]),
            "names": [
                {"agent_id": profile.agent_id, "display_name": profile.display_name}
                for profile in profiles
            ],
        }

    def _state_blocks(
        self, final_states: Sequence[ConsumerState]
    ) -> dict[str, list[dict[str, object]]]:
        if not final_states:
            return {}
        return {
            "sentiment": _distribution([state.brand_sentiment for state in final_states]),
            "recall": _distribution([state.recall_strength for state in final_states]),
            "intention": _distribution([state.purchase_intention for state in final_states]),
            "fatigue": _distribution([state.ad_fatigue for state in final_states]),
        }

    def _word_of_mouth_block(self) -> dict[str, Any]:
        shared = [
            event for event in self._stored.events if event.event_type.value == "social.shared"
        ]
        received = [
            event for event in self._stored.events if event.event_type.value == "social.received"
        ]
        return {
            "shares": len(shared),
            "received": len(received),
            "edges": [
                {
                    "sender": event.agent_id or "unknown",
                    "receiver": str(event.payload.get("receiver_id", "unknown")),
                    "campaign": event.campaign_id or "-",
                }
                for event in shared
            ],
        }

    def _diaries(self) -> list[dict[str, Any]]:
        """Selected end-of-day reflections, explicitly fictional."""
        diaries: list[dict[str, Any]] = []
        for event in self._stored.events:
            if event.event_type.value != "day.reflected" or event.agent_id is None:
                continue
            profile = next(
                (
                    candidate
                    for candidate in self._stored.scenario.population
                    if candidate.agent_id == event.agent_id
                ),
                None,
            )
            diaries.append(
                {
                    "agent_id": event.agent_id,
                    "display_name": profile.display_name if profile else event.agent_id,
                    "day": event.simulated_minute // 1440 + 1,
                    "summary": str(event.payload.get("summary", "")),
                }
            )
        return diaries[:6]

    def _usage_block(self) -> dict[str, Any]:
        records: tuple[Any, ...] = self._usage.records if self._usage else ()
        fallbacks = [
            str(record.fallback_reason) for record in records if record.fallback_reason is not None
        ]
        return {
            "available": self._usage is not None,
            "requests": len(records),
            "cache_hits": sum(1 for record in records if record.cache_hit),
            "failures": len(fallbacks),
            "fallback_reasons": dict(Counter(fallbacks)),
            "prompt_tokens": sum(record.prompt_tokens for record in records),
            "completion_tokens": sum(record.completion_tokens for record in records),
        }


class _SafeLoader(BaseLoader):
    """A Jinja2 loader that serves the packaged template source."""


def _environment() -> Environment:
    return Environment(autoescape=True, loader=BaseLoader())


def render_report(
    stored_run: StoredRun,
    destination: Path,
    *,
    usage: ProviderUsageLog | None = None,
) -> Path:
    """Render one stored run into a self-contained HTML report."""
    document = ReportBuilder(stored_run, usage).build()
    template = _environment().from_string(report_template())
    html = template.render(
        **document,
        chart_payload=_safe_json(
            {
                "overall": document["overall"],
                "campaigns": [
                    {"campaign_id": item["campaign_id"], "metrics": item["metrics"]}
                    for item in document["campaigns"]
                ],
                "distributions": document["state_distributions"],
            }
        ),
        css=report_css(),
        plotly_js=plotly_runtime(),
        chart_json=_safe_json,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return destination


def render_comparison(comparison: ComparisonResult, destination: Path) -> Path:
    """Render one paired comparison into a self-contained HTML report."""
    rows = [
        {
            "metric": statistic.metric,
            "n": statistic.n_seeds,
            "mean": statistic.mean_paired_difference,
            "std": statistic.std_paired_difference,
            "median": statistic.median_paired_difference,
            "ci_low": statistic.ci_low,
            "ci_high": statistic.ci_high,
            "effect": statistic.effect_size,
            "agreement": statistic.agreement_fraction,
            "direction": statistic.direction,
        }
        for statistic in comparison.metrics.values()
    ]
    payload = _safe_json(
        {
            "metrics": [
                {
                    "metric": row["metric"],
                    "mean": row["mean"],
                    "ci_low": row["ci_low"],
                    "ci_high": row["ci_high"],
                }
                for row in rows
            ]
        }
    )
    template = _environment().from_string(report_template())
    html = template.render(
        comparison_mode=True,
        label=comparison.label,
        seeds=list(comparison.seeds),
        rows=rows,
        disclosure=DISCLOSURE,
        limitations=list(LIMITATIONS),
        chart_payload=payload,
        css=report_css(),
        plotly_js=plotly_runtime(),
        chart_json=_safe_json,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return destination
