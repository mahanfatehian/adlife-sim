"""The human-readable event sink: one terse line per event, on any text stream.

The line carries the fields a person reads a run by - when it happened in simulated time,
where it sits in the causal sequence, what kind of event it was, and which agent, campaign
and channel it concerned - and it STRUCTURALLY OMITS THE PAYLOAD.

That omission is a decision, not an oversight. The payload is the one field on an event
whose text came from outside this repository: campaign copy a user wrote, or a paraphrase
a language model produced. A terminal line is a log, a log is copied into issue reports,
and a log is exactly where a leak becomes permanent. This sink therefore prints only
fields whose shape the domain model already constrains to identifiers and enumerations.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from adlife.core.domain.events import DomainEvent
from adlife.core.ports.event_sink import validate_sink_batch

MINUTES_PER_DAY = 1440
MISSING = "-"
"""A field an event does not carry is NAMED rather than dropped, so columns line up."""


def format_event_line(event: DomainEvent) -> str:
    """Render one event as the line a person reads.

    Simulated time is rendered from the minute alone: day one begins at minute zero, so
    minute 1935 is 08:15 on day two. Nothing here consults a wall clock.
    """
    if not isinstance(event, DomainEvent):
        raise TypeError("format_event_line needs a DomainEvent")
    day = event.simulated_minute // MINUTES_PER_DAY + 1
    minute_of_day = event.simulated_minute % MINUTES_PER_DAY
    clock = f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"
    return " | ".join(
        (
            f"day {day} {clock}",
            f"#{event.sequence}",
            event.event_type.value,
            event.agent_id or MISSING,
            event.campaign_id or MISSING,
            event.channel or MISSING,
            event.source.value,
        )
    )


class PlainEventSink:
    """Write human-readable event lines to a text stream."""

    __slots__ = ("_stream",)

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def append_many(self, run_id: str, events: Sequence[DomainEvent]) -> None:
        """Print one line per event, in the order handed over.

        The whole batch is validated before anything is printed, so a batch this sink
        refuses leaves no half-published tick on the stream.
        """
        batch = validate_sink_batch(run_id, events)
        if not batch:
            return
        self._stream.write("".join(f"{format_event_line(event)}\n" for event in batch))
        self._stream.flush()


__all__ = ["MISSING", "PlainEventSink", "format_event_line"]
