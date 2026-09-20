"""The metrics fold: one run's events, boundary states and usage, to RunMetrics.

A metric here is a NAMED ANSWER WITH ITS RECEIPT: every entry of ``RunMetrics.details``
carries the numerator, the denominator, the value and the sources the value was read
from, so a report never shows a number whose provenance cannot be re-derived from the
artifact. ``calculate`` is a pure function of its arguments - it reads no live model,
no SQLite, no clock - which is what makes it usable on a stored artifact, on a
synthesized stream in a test, or on a live run's outcome without changing shape.

THE THREE SOURCES. Most metrics are event-derived: the fixed catalog's payloads carry
the sentiment and recall deltas of every cognition answer, the social receipts, and the
purchase-proxy memory kind. Two cognition facts are NOT events - cache hits and provider
failures live only in the persisted usage log - so they are folded from caller-supplied
usage records, or zero when none are given. Three reactions are only observable in
agent state - delayed recall, advertising fatigue, purchase-intention change and the
high-intention count - so they are folded from caller-supplied boundary states, or zero
when none are given. No metric ever has two meanings under one name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.state import ConsumerState
from adlife.core.ports.cognition import ProviderUsage
from adlife.core.ports.run_store import ProviderUsageLog

PURCHASE_PROXY_KIND = "purchase-proxy"
"""The memory kind the engine records a committed purchase proxy under."""

_COGNITION_ANSWER_TYPES: tuple[EventType, ...] = (
    EventType.COGNITION_COMPLETED,
    EventType.COGNITION_FALLBACK,
)

HIGH_INTENTION_THRESHOLD = 0.70
"""The documented purchase-intention threshold; ``decision.PURCHASE_INTENTION_THRESHOLD``."""

EVENT = "event"
STATE = "state"
USAGE = "usage"

METRIC_NAMES: tuple[str, ...] = (
    "eligible",
    "impressions",
    "reach",
    "frequency",
    "noticed",
    "notice_rate",
    "sentiment",
    "recall",
    "fatigue",
    "direct_awareness",
    "indirect_awareness",
    "word_of_mouth_reach",
    "intention_delta",
    "high_intention",
    "purchases",
    "cognition_requests",
    "cognition_cache_hits",
    "cognition_failures",
    "cognition_fallbacks",
)


@dataclass(frozen=True, slots=True)
class MetricValue:
    """One metric with its receipt: what was counted, over what, from where."""

    name: str
    numerator: float
    denominator: float
    value: float
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError(f"metric {self.name} must name at least one source")


@dataclass(frozen=True)
class RunMetrics:
    """Every metric of one run, as named attributes and as a detail mapping."""

    eligible: float = 0.0
    impressions: float = 0.0
    reach: float = 0.0
    frequency: float = 0.0
    noticed: float = 0.0
    notice_rate: float = 0.0
    sentiment: float = 0.0
    recall: float = 0.0
    fatigue: float = 0.0
    direct_awareness: float = 0.0
    indirect_awareness: float = 0.0
    word_of_mouth_reach: float = 0.0
    intention_delta: float = 0.0
    high_intention: float = 0.0
    purchases: float = 0.0
    cognition_requests: float = 0.0
    cognition_cache_hits: float = 0.0
    cognition_failures: float = 0.0
    cognition_fallbacks: float = 0.0
    details: Mapping[str, MetricValue] = field(default_factory=dict)

    def as_mapping(self) -> dict[str, float]:
        """The result document's view: metric name to plain float."""
        return {name: float(getattr(self, name)) for name in METRIC_NAMES}

    @classmethod
    def averaged(cls, runs: Sequence[RunMetrics]) -> RunMetrics:
        """The mean of several runs' metrics, receipts averaged the same way."""
        if not runs:
            return cls(
                details={
                    name: MetricValue(
                        name=name, numerator=0.0, denominator=0.0, value=0.0, sources=(STATE,)
                    )
                    for name in METRIC_NAMES
                }
            )
        count = len(runs)
        values = {name: sum(getattr(run, name) for run in runs) / count for name in METRIC_NAMES}
        details: dict[str, MetricValue] = {}
        for name in METRIC_NAMES:
            first = runs[0].details[name]
            details[name] = MetricValue(
                name=name,
                numerator=sum(run.details[name].numerator for run in runs) / count,
                denominator=sum(run.details[name].denominator for run in runs) / count,
                value=values[name],
                sources=first.sources,
            )
        return cls(**values, details=details)


def _detail(name: str, numerator: float, denominator: float, *sources: str) -> MetricValue:
    value = numerator / denominator if denominator else 0.0
    return MetricValue(
        name=name, numerator=numerator, denominator=denominator, value=value, sources=sources
    )


class MetricsCalculator:
    """Fold one run's records into :class:`RunMetrics`, purely, with receipts."""

    def calculate(
        self,
        events: Iterable[DomainEvent],
        *,
        campaign_id: str | None = None,
        initial_states: Sequence[ConsumerState] = (),
        final_states: Sequence[ConsumerState] = (),
        usage: ProviderUsageLog | Sequence[ProviderUsage] | None = None,
    ) -> RunMetrics:
        records = [
            event for event in events if campaign_id is None or event.campaign_id == campaign_id
        ]
        types = [event.event_type for event in records]

        eligible = types.count(EventType.CAMPAIGN_ELIGIBLE)
        impressions = types.count(EventType.CAMPAIGN_IMPRESSION)
        noticed = types.count(EventType.CAMPAIGN_NOTICED)
        reach_ids = {
            event.agent_id
            for event in records
            if event.event_type is EventType.CAMPAIGN_IMPRESSION and event.agent_id
        }
        reach = len(reach_ids)
        cognition_answers = [
            event for event in records if event.event_type in _COGNITION_ANSWER_TYPES
        ]
        sentiment_total = 0.0
        for event in cognition_answers:
            delta = event.payload.get("sentiment_delta", 0.0)
            if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                sentiment_total += float(delta)
        direct_pairs = {
            (event.agent_id, event.campaign_id)
            for event in records
            if event.event_type is EventType.CAMPAIGN_NOTICED and event.campaign_id
        }
        received_pairs = {
            (event.agent_id, event.campaign_id)
            for event in records
            if event.event_type is EventType.SOCIAL_RECEIVED and event.campaign_id
        }
        word_of_mouth_ids = {
            event.agent_id
            for event in records
            if event.event_type is EventType.SOCIAL_RECEIVED and event.agent_id
        }
        purchases = sum(
            1
            for event in records
            if event.event_type is EventType.MEMORY_CREATED
            and event.payload.get("kind") == PURCHASE_PROXY_KIND
        )
        fallbacks = types.count(EventType.COGNITION_FALLBACK)
        requests = len(cognition_answers)

        usage_records: Sequence[ProviderUsage]
        if usage is None:
            usage_records = ()
        elif isinstance(usage, ProviderUsageLog):
            usage_records = usage.records
        else:
            usage_records = usage
        cache_hits = sum(1 for record in usage_records if record.cache_hit)
        failures = sum(1 for record in usage_records if record.provider_kind == "fallback")

        final_by_id = {state.agent_id: state for state in final_states}
        initial_by_id = {state.agent_id: state for state in initial_states}
        paired_ids = [agent_id for agent_id in sorted(final_by_id) if agent_id in initial_by_id]
        recall_values = [state.recall_strength for state in final_by_id.values()]
        fatigue_values = [state.ad_fatigue for state in final_by_id.values()]
        intention_deltas = [
            final_by_id[agent_id].purchase_intention - initial_by_id[agent_id].purchase_intention
            for agent_id in paired_ids
        ]
        high_intention = sum(
            1
            for state in final_by_id.values()
            if state.purchase_intention >= HIGH_INTENTION_THRESHOLD
        )

        details = {
            "eligible": _detail("eligible", eligible, 1, EVENT),
            "impressions": _detail("impressions", impressions, 1, EVENT),
            "reach": _detail("reach", reach, 1, EVENT),
            "frequency": _detail(
                "frequency",
                impressions,
                reach,
                EventType.CAMPAIGN_IMPRESSION.value,
            ),
            "noticed": _detail("noticed", noticed, 1, EVENT),
            "notice_rate": _detail(
                "notice_rate",
                noticed,
                impressions,
                EventType.CAMPAIGN_NOTICED.value,
                EventType.CAMPAIGN_IMPRESSION.value,
            ),
            "sentiment": _detail(
                "sentiment",
                sentiment_total,
                len(cognition_answers),
                *[event_type.value for event_type in _COGNITION_ANSWER_TYPES],
            ),
            "recall": _detail("recall", sum(recall_values), len(recall_values), STATE),
            "fatigue": _detail("fatigue", sum(fatigue_values), len(fatigue_values), STATE),
            "direct_awareness": _detail(
                "direct_awareness",
                len(direct_pairs),
                1,
                EventType.CAMPAIGN_NOTICED.value,
            ),
            "indirect_awareness": _detail(
                "indirect_awareness",
                len(received_pairs - direct_pairs),
                1,
                EventType.SOCIAL_RECEIVED.value,
            ),
            "word_of_mouth_reach": _detail(
                "word_of_mouth_reach",
                len(word_of_mouth_ids),
                1,
                EventType.SOCIAL_RECEIVED.value,
            ),
            "intention_delta": _detail(
                "intention_delta",
                sum(intention_deltas),
                len(intention_deltas),
                STATE,
            ),
            "high_intention": _detail(
                "high_intention",
                high_intention,
                1,
                STATE,
            ),
            "purchases": _detail("purchases", purchases, 1, EVENT),
            "cognition_requests": _detail(
                "cognition_requests",
                requests,
                1,
                *[event_type.value for event_type in _COGNITION_ANSWER_TYPES],
            ),
            "cognition_cache_hits": _detail("cognition_cache_hits", cache_hits, 1, USAGE),
            "cognition_failures": _detail("cognition_failures", failures, 1, USAGE),
            "cognition_fallbacks": _detail(
                "cognition_fallbacks",
                fallbacks,
                1,
                EventType.COGNITION_FALLBACK.value,
            ),
        }
        return RunMetrics(**{name: details[name].value for name in METRIC_NAMES}, details=details)


__all__ = [
    "HIGH_INTENTION_THRESHOLD",
    "METRIC_NAMES",
    "PURCHASE_PROXY_KIND",
    "MetricValue",
    "MetricsCalculator",
    "RunMetrics",
]
