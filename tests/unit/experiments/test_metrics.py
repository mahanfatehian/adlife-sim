"""The metrics fold, pinned against hand-built event streams.

``MetricsCalculator.calculate`` is a pure function of the inputs the caller hands it -
events, optional boundary states, optional provider usage records - so these tests can
build the exact streams a run would leave behind and pin every numerator, denominator,
value and source. The whole-run suites prove the fold agrees with real artifacts; here
the arithmetic itself is under test.
"""

from __future__ import annotations

from typing import Any

import pytest

from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.state import ConsumerState
from adlife.core.experiments.metrics import METRIC_NAMES, MetricsCalculator
from adlife.core.ports.cognition import ProviderUsage
from adlife.core.ports.run_store import ProviderUsageLog
from adlife.core.simulation.engine import stable_event_id

RUN_ID = "run-metrics"


def event(
    event_type: EventType,
    sequence: int,
    *,
    agent_id: str | None = "person-001",
    campaign_id: str | None = "campaign-phone",
    payload: dict[str, Any] | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_id=stable_event_id(RUN_ID, sequence),
        run_id=RUN_ID,
        simulated_minute=sequence,
        sequence=sequence,
        event_type=event_type,
        payload=payload or {},
        agent_id=agent_id,
        campaign_id=campaign_id,
        channel="mobile-feed" if campaign_id else None,
        source=EventSource.RULE,
    )


def state(
    agent_id: str,
    *,
    recall: float,
    fatigue: float,
    intention: float,
) -> ConsumerState:
    return ConsumerState(
        agent_id=agent_id,
        location="home-north",
        activity="sleep",
        mood=0.0,
        fatigue=0.0,
        brand_sentiment=0.0,
        recall_strength=recall,
        purchase_intention=intention,
        cognition_budget_remaining=6,
        ad_fatigue=fatigue,
    )


def test_no_campaign_control_has_zero_campaign_metrics() -> None:
    events = (
        event(EventType.RUN_STARTED, 0, agent_id=None, campaign_id=None),
        event(EventType.RUN_COMPLETED, 1, agent_id=None, campaign_id=None),
    )
    metrics = MetricsCalculator().calculate(events)

    assert metrics.reach == 0
    assert metrics.impressions == 0
    assert metrics.notice_rate == 0
    assert metrics.word_of_mouth_reach == 0
    assert metrics.purchases == 0


def test_reach_frequency_and_notice_rate_arithmetic() -> None:
    events = (
        event(EventType.CAMPAIGN_ELIGIBLE, 0),
        event(EventType.CAMPAIGN_IMPRESSION, 1),
        event(EventType.CAMPAIGN_NOTICED, 2),
        event(EventType.CAMPAIGN_ELIGIBLE, 3),
        event(EventType.CAMPAIGN_IMPRESSION, 4),
        event(EventType.CAMPAIGN_NOTICED, 5),
        event(
            EventType.CAMPAIGN_IMPRESSION,
            6,
            agent_id="person-002",
        ),
        event(EventType.CAMPAIGN_ELIGIBLE, 7, agent_id="person-002"),
    )
    metrics = MetricsCalculator().calculate(events)

    assert metrics.eligible == 3
    assert metrics.impressions == 3
    assert metrics.reach == 2
    assert metrics.frequency == pytest.approx(1.5)
    assert metrics.noticed == 2
    assert metrics.notice_rate == pytest.approx(2 / 3)
    detail = metrics.details["frequency"]
    assert detail.numerator == 3
    assert detail.denominator == 2


def test_sentiment_mean_comes_from_cognition_payloads() -> None:
    events = (
        event(
            EventType.COGNITION_COMPLETED,
            0,
            payload={"sentiment_delta": 0.10, "recall_delta": 0.20},
        ),
        event(
            EventType.COGNITION_COMPLETED,
            1,
            payload={"sentiment_delta": -0.10, "recall_delta": 0.10},
        ),
        event(
            EventType.COGNITION_FALLBACK,
            2,
            payload={"sentiment_delta": 0.30, "recall_delta": 0.30},
        ),
    )
    metrics = MetricsCalculator().calculate(events)

    assert metrics.sentiment == pytest.approx(0.10)
    detail = metrics.details["sentiment"]
    assert detail.numerator == pytest.approx(0.30)
    assert detail.denominator == 3
    assert EventType.COGNITION_COMPLETED.value in detail.sources
    assert EventType.COGNITION_FALLBACK.value in detail.sources


def test_awareness_distinguishes_direct_from_indirect() -> None:
    events = (
        # person-001 noticed campaign-phone directly.
        event(EventType.CAMPAIGN_NOTICED, 0),
        # person-002 received it socially: indirect awareness and word of mouth.
        event(
            EventType.SOCIAL_RECEIVED,
            1,
            agent_id="person-002",
            payload={"sender_id": "person-001"},
        ),
        # person-003 received a campaign they had ALSO noticed directly: not indirect.
        event(EventType.CAMPAIGN_NOTICED, 2, agent_id="person-003"),
        event(
            EventType.SOCIAL_RECEIVED,
            3,
            agent_id="person-003",
            payload={"sender_id": "person-001"},
        ),
    )
    metrics = MetricsCalculator().calculate(events)

    assert metrics.direct_awareness == 2
    assert metrics.indirect_awareness == 1
    assert metrics.word_of_mouth_reach == 2


def test_purchase_proxy_count_reads_memory_kind() -> None:
    events = (
        event(
            EventType.MEMORY_CREATED,
            0,
            payload={"kind": "purchase-proxy", "memory_id": "m1"},
        ),
        event(
            EventType.MEMORY_CREATED,
            1,
            payload={"kind": "advertising", "memory_id": "m2"},
        ),
        event(
            EventType.MEMORY_CREATED,
            2,
            agent_id="person-002",
            payload={"kind": "purchase-proxy", "memory_id": "m3"},
        ),
    )
    metrics = MetricsCalculator().calculate(events)

    assert metrics.purchases == 2


def test_cognition_counts_split_events_from_usage() -> None:
    events = (
        event(EventType.COGNITION_COMPLETED, 0),
        event(EventType.COGNITION_FALLBACK, 1),
        event(EventType.COGNITION_COMPLETED, 2),
    )
    without_usage = MetricsCalculator().calculate(events)
    assert without_usage.cognition_requests == 3
    assert without_usage.cognition_fallbacks == 1
    assert without_usage.cognition_cache_hits == 0
    assert without_usage.cognition_failures == 0

    usage = ProviderUsageLog(
        run_id=RUN_ID,
        records=(
            ProviderUsage(
                provider_kind="rule",
                model_id="rule-baseline",
                prompt_tokens=1,
                completion_tokens=1,
                latency_ms=1,
            ),
            ProviderUsage(
                provider_kind="fallback",
                model_id="rule-baseline",
                prompt_tokens=1,
                completion_tokens=1,
                latency_ms=1,
                fallback_reason="provider-unavailable",
            ),
            ProviderUsage(
                provider_kind="rule",
                model_id="rule-baseline",
                prompt_tokens=1,
                completion_tokens=1,
                latency_ms=1,
                cache_hit=True,
            ),
        ),
    )
    with_usage = MetricsCalculator().calculate(events, usage=usage)
    assert with_usage.cognition_cache_hits == 1
    assert with_usage.cognition_failures == 1


def test_state_boundary_metrics_use_initial_and_final_states() -> None:
    initial = (
        state("person-001", recall=0.10, fatigue=0.20, intention=0.10),
        state("person-002", recall=0.20, fatigue=0.10, intention=0.30),
    )
    final = (
        state("person-001", recall=0.50, fatigue=0.10, intention=0.80),
        state("person-002", recall=0.60, fatigue=0.00, intention=0.30),
    )
    events = (event(EventType.RUN_STARTED, 0, agent_id=None, campaign_id=None),)
    metrics = MetricsCalculator().calculate(
        events,
        initial_states=initial,
        final_states=final,
    )

    assert metrics.recall == pytest.approx(0.55)
    assert metrics.fatigue == pytest.approx(0.05)
    assert metrics.intention_delta == pytest.approx((0.70 + 0.00) / 2)
    assert metrics.high_intention == 1

    without_states = MetricsCalculator().calculate(events)
    assert without_states.recall == 0
    assert without_states.fatigue == 0
    assert without_states.intention_delta == 0
    assert without_states.high_intention == 0


def test_campaign_filter_scopes_campaign_metrics() -> None:
    events = (
        event(EventType.CAMPAIGN_IMPRESSION, 0, campaign_id="campaign-phone"),
        event(EventType.CAMPAIGN_NOTICED, 1, campaign_id="campaign-phone"),
        event(EventType.CAMPAIGN_IMPRESSION, 2, campaign_id="campaign-billboard"),
        event(EventType.CAMPAIGN_IMPRESSION, 3, campaign_id="campaign-billboard"),
        event(
            EventType.CAMPAIGN_IMPRESSION,
            4,
            agent_id="person-002",
            campaign_id="campaign-billboard",
        ),
    )
    phone = MetricsCalculator().calculate(events, campaign_id="campaign-phone")
    billboard = MetricsCalculator().calculate(events, campaign_id="campaign-billboard")

    assert phone.impressions == 1
    assert phone.noticed == 1
    assert phone.reach == 1
    assert billboard.impressions == 3
    assert billboard.noticed == 0
    assert billboard.reach == 2


def test_every_metric_carries_its_details() -> None:
    events = (event(EventType.CAMPAIGN_IMPRESSION, 0),)
    metrics = MetricsCalculator().calculate(events)

    assert tuple(metrics.details) == METRIC_NAMES
    for name in METRIC_NAMES:
        detail = metrics.details[name]
        assert detail.name == name
        assert detail.sources, f"metric {name} must name its source"
        assert detail.value == getattr(metrics, name)
