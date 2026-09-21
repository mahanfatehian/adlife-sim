"""The bounded event bus, pinned against hand-built committed event streams.

The bus is the read-only adapter between the runner's committed events and the
dashboard: ``publish`` folds one tick's events after persistence, ``latest`` exposes
immutable projections, and a subscriber that raises must be logged and skipped without
touching the engine's outcome.
"""

from __future__ import annotations

import io
import sys

import pytest

from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.simulation.movement import Snapshot
from adlife.tui.event_bus import TuiEventBus

RUN = "run-bus"


def _event(
    event_type: EventType,
    *,
    sequence: int,
    agent_id: str | None = None,
    campaign_id: str | None = None,
    payload: dict[str, object] | None = None,
    minute: int = 0,
) -> DomainEvent:
    return DomainEvent(
        event_id=f"{RUN}:{sequence:04d}",
        run_id=RUN,
        simulated_minute=minute,
        sequence=sequence,
        event_type=event_type,
        agent_id=agent_id,
        campaign_id=campaign_id,
        payload=payload or {},
        source=EventSource.RULE,
    )


def _profile(agent_id: str = "person-001") -> PersonProfile:
    return PersonProfile(
        agent_id=agent_id,
        display_name=agent_id.replace("person-", "Agent ").title(),
        age=34,
        occupation="office-worker",
        income_band="middle",
        household_type="single",
        home_zone="home-north",
        work_or_study_zone="office",
        interests=frozenset({"coffee"}),
        traits=ConsumerTraits(
            price_sensitivity=0.5,
            novelty_seeking=0.5,
            social_susceptibility=0.5,
            advertising_skepticism=0.5,
            mobile_attention=0.5,
            outdoor_attention=0.5,
            brand_loyalty=0.5,
            impulsivity=0.5,
        ),
        initial_brand_sentiment=0.1,
        routine_template="office-worker",
    )


def _state(agent_id: str = "person-001") -> ConsumerState:
    return ConsumerState(
        agent_id=agent_id,
        location="home-north",
        activity="sleep",
        mood=0.0,
        fatigue=0.0,
        brand_sentiment=0.1,
        recall_strength=0.2,
        purchase_intention=0.1,
        exposure_counts=(),
        cognition_budget_remaining=6,
    )


def _snapshot(minute: int = 0) -> Snapshot:
    return Snapshot(
        agents={"person-001": (_profile(), _state())},
        simulated_minute=minute,
        run_id=RUN,
    )


def test_display_stream_is_bounded_to_one_hundred_events() -> None:
    bus = TuiEventBus()
    events = [_event(EventType.CAMPAIGN_IMPRESSION, sequence=n) for n in range(1, 151)]

    bus.publish(_snapshot(), tuple(events))

    display = bus.display_events()
    assert len(display) == 100
    assert display[-1].sequence == 150
    assert display[0].sequence == 51


def test_display_events_are_returned_oldest_to_newest() -> None:
    bus = TuiEventBus()
    bus.publish(
        _snapshot(),
        tuple(
            _event(EventType.CAMPAIGN_IMPRESSION, sequence=n, campaign_id="campaign-alpha")
            for n in (1, 2)
        ),
    )
    bus.publish(
        _snapshot(minute=15),
        (_event(EventType.CAMPAIGN_NOTICED, sequence=3, campaign_id="campaign-alpha"),),
    )

    display = bus.display_events()
    assert [event.sequence for event in display] == [1, 2, 3]


def test_agent_projection_follows_the_snapshot() -> None:
    bus = TuiEventBus()

    bus.publish(_snapshot(minute=0), ())
    _profile, state = bus.latest.agents["person-001"]
    assert state.activity == "sleep"

    moved = _snapshot(minute=15)
    bus.publish(moved, (_event(EventType.LOCATION_CHANGED, sequence=1),))
    assert bus.latest.simulated_minute == 15


def test_campaign_projections_fold_committed_events() -> None:
    bus = TuiEventBus()
    bus.publish(
        _snapshot(),
        (
            _event(EventType.CAMPAIGN_IMPRESSION, sequence=1, campaign_id="campaign-alpha"),
            _event(EventType.CAMPAIGN_NOTICED, sequence=2, campaign_id="campaign-alpha"),
            _event(EventType.CAMPAIGN_IMPRESSION, sequence=3, campaign_id="campaign-alpha"),
            _event(EventType.SOCIAL_SHARED, sequence=4, campaign_id="campaign-alpha"),
        ),
    )

    campaign = bus.latest.campaigns["campaign-alpha"]
    assert campaign.reach == 2
    assert campaign.notices == 1
    assert campaign.shares == 1


def test_memory_projection_records_the_latest_memory_per_agent() -> None:
    bus = TuiEventBus()
    bus.publish(
        _snapshot(),
        (
            _event(
                EventType.MEMORY_CREATED,
                sequence=1,
                agent_id="person-001",
                campaign_id="campaign-alpha",
                payload={
                    "memory_id": "m1",
                    "kind": "advertising",
                    "summary": "first",
                    "salience": 0.4,
                },
            ),
            _event(
                EventType.MEMORY_CREATED,
                sequence=2,
                agent_id="person-001",
                payload={"memory_id": "m2", "kind": "social", "summary": "second", "salience": 0.9},
            ),
        ),
    )

    memories = bus.latest.memories["person-001"]
    assert memories[-1]["memory_id"] == "m2"
    assert len(memories) == 2


def test_subscriber_exception_is_logged_and_skipped() -> None:
    bus = TuiEventBus()
    seen: list[tuple[object, tuple[DomainEvent, ...]]] = []
    buffer = io.StringIO()
    original = sys.stderr
    sys.stderr = buffer
    try:

        def boom(_snapshot: object, _events: tuple[DomainEvent, ...]) -> None:
            raise RuntimeError("subscriber exploded")

        bus.subscribe(boom)
        bus.subscribe(lambda snapshot, events: seen.append((snapshot, events)))
        bus.publish(_snapshot(), (_event(EventType.CAMPAIGN_IMPRESSION, sequence=1),))
    finally:
        sys.stderr = original

    assert "subscriber exploded" in buffer.getvalue()
    assert len(seen) == 1


def test_publish_after_persist_ordering_is_preserved() -> None:
    bus = TuiEventBus()
    order: list[int] = []
    bus.subscribe(lambda _snapshot, events: order.extend(event.sequence for event in events))

    bus.publish(_snapshot(), (_event(EventType.CAMPAIGN_IMPRESSION, sequence=7),))
    bus.publish(_snapshot(minute=15), (_event(EventType.CAMPAIGN_NOTICED, sequence=8),))

    assert order == [7, 8]


def test_event_with_no_agent_or_campaign_is_counted_but_projected_nowhere() -> None:
    bus = TuiEventBus()
    bus.publish(_snapshot(), (_event(EventType.RUN_STARTED, sequence=1),))

    assert bus.display_events()[-1].event_type is EventType.RUN_STARTED
    assert "person-001" not in bus.latest.memories


@pytest.mark.parametrize("payload", [{"memory_id": "m1"}, {"summary": "s"}])
def test_memory_payloads_missing_keys_are_projected_as_they_are(payload: dict[str, object]) -> None:
    bus = TuiEventBus()
    bus.publish(
        _snapshot(),
        (
            _event(
                EventType.MEMORY_CREATED,
                sequence=1,
                agent_id="person-001",
                payload=payload,
            ),
        ),
    )

    assert bus.latest.memories["person-001"][0] == payload
