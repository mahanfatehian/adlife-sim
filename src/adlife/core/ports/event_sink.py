"""The event-sink port: what an OBSERVER of a run may be.

A sink watches a run; it never owns it. Durability belongs to
:class:`~adlife.core.ports.run_store.RunStore`, and specification section 13 requires an
event to be persisted BEFORE it is published to any observer, so nothing a sink does can
change what a run recorded. That is also why a sink is allowed to fail loudly: by the
time it is called the authoritative write has already happened.

What a sink owes its caller is narrow, and :func:`validate_sink_batch` is all of it. The
batch belongs to one named run, every member is a real :class:`DomainEvent`, and the
order the caller passed is the order the sink keeps - the tick order is the only order a
reader can reconstruct a run from.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from adlife.core.domain.events import DomainEvent

RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
"""The same shape :class:`~adlife.core.domain.results.RunManifest` validates."""


class EventSinkError(RuntimeError):
    """Raised when a sink is handed something it must not publish."""


def validate_sink_batch(run_id: str, events: Sequence[DomainEvent]) -> tuple[DomainEvent, ...]:
    """Check one batch and return it in the order it was handed over.

    Nothing here sorts, deduplicates or renumbers. A sink that quietly reordered a tick
    would show a reader a run that never happened.
    """
    if not isinstance(run_id, str) or RUN_ID_PATTERN.match(run_id) is None:
        raise EventSinkError(f"{run_id!r} is not a run identifier")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise EventSinkError("a batch must be a sequence of DomainEvent")
    batch = tuple(events)
    for index, event in enumerate(batch):
        if not isinstance(event, DomainEvent):
            raise EventSinkError(f"batch member {index} is not a DomainEvent")
        if event.run_id != run_id:
            raise EventSinkError(
                f"batch member {index} belongs to run {event.run_id!r}, not {run_id!r}"
            )
    return batch


@runtime_checkable
class EventSink(Protocol):
    """Publish one committed tick's events, in order, for a named run."""

    def append_many(self, run_id: str, events: Sequence[DomainEvent]) -> None: ...


__all__ = ["RUN_ID_PATTERN", "EventSink", "EventSinkError", "validate_sink_batch"]
