"""The bounded event bus: committed events in, display projections out.

The bus receives each tick's events from the runner's ``tick_observer`` hook - after
persistence has succeeded - and maintains exactly two things: a bounded display stream
of the most recent one hundred committed events, and immutable latest projections
(per-agent snapshot state, per-campaign aggregates, latest memories). Subscribers run
in isolation: an exception is logged to stderr and skipped, so no view can affect the
engine's outcome.
"""

from __future__ import annotations

import sys
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.simulation.movement import Snapshot

DISPLAY_LIMIT = 100

Subscriber = Callable[[Snapshot, tuple[DomainEvent, ...]], None]


@dataclass(frozen=True)
class CampaignProjection:
    """The aggregates the metrics strip shows for one campaign."""

    reach: int = 0
    notices: int = 0
    shares: int = 0


@dataclass(frozen=True)
class TuiProjections:
    """The immutable latest view of the run the dashboard renders."""

    snapshot: Snapshot | None = None
    campaigns: Mapping[str, CampaignProjection] = field(default_factory=dict)
    memories: Mapping[str, tuple[Mapping[str, object], ...]] = field(default_factory=dict)

    @property
    def simulated_minute(self) -> int:
        return self.snapshot.simulated_minute if self.snapshot is not None else 0

    @property
    def agents(self) -> Mapping[str, object]:
        """The latest snapshot's per-agent ``(profile, state)`` pairs, or empty."""
        if self.snapshot is None:
            return {}
        return self.snapshot.agents


class TuiEventBus:
    """Fold committed events into a bounded display stream and latest projections."""

    def __init__(self) -> None:
        self._display: deque[DomainEvent] = deque(maxlen=DISPLAY_LIMIT)
        self._snapshot: Snapshot | None = None
        self._campaigns: dict[str, CampaignProjection] = {}
        self._memories: dict[str, tuple[Mapping[str, object], ...]] = {}
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def publish(self, snapshot: Snapshot, events: tuple[DomainEvent, ...]) -> None:
        """Fold one committed tick: events are already persisted when this runs."""
        self._snapshot = snapshot
        for event in events:
            self._display.append(event)
            self._project(event)
        for subscriber in tuple(self._subscribers):
            try:
                subscriber(snapshot, events)
            except Exception as error:
                print(
                    f"adlife.tui: subscriber {type(subscriber).__name__} failed: "
                    f"{type(error).__name__}: {error}",
                    file=sys.stderr,
                )

    def display_events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._display)

    @property
    def latest(self) -> TuiProjections:
        return TuiProjections(
            snapshot=self._snapshot,
            campaigns=dict(self._campaigns),
            memories=dict(self._memories),
        )

    def _project(self, event: DomainEvent) -> None:
        if event.event_type is EventType.CAMPAIGN_IMPRESSION:
            self._bump(event.campaign_id, "reach")
        elif event.event_type is EventType.CAMPAIGN_NOTICED:
            self._bump(event.campaign_id, "notices")
        elif event.event_type is EventType.SOCIAL_SHARED:
            self._bump(event.campaign_id, "shares")
        elif event.event_type is EventType.MEMORY_CREATED and event.agent_id is not None:
            memories = self._memories.get(event.agent_id, ())
            self._memories[event.agent_id] = (*memories, event.payload)

    def _bump(self, campaign_id: str | None, name: str) -> None:
        if campaign_id is None:
            return
        current = self._campaigns.get(campaign_id, CampaignProjection())
        value = getattr(current, name) + 1
        self._campaigns[campaign_id] = CampaignProjection(
            reach=current.reach if name != "reach" else value,
            notices=current.notices if name != "notices" else value,
            shares=current.shares if name != "shares" else value,
        )
