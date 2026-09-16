"""The human-readable event sink: one terse line per event, on any text stream.

The line carries the fields a person reads a run by - when it happened in simulated time,
where it sits in the causal sequence, what kind of event it was, and which agent, campaign
and channel it concerned - and it STRUCTURALLY OMITS THE PAYLOAD.

That omission is a decision, not an oversight. The payload is the field on an event that
most often carries text from outside this repository: campaign copy a user wrote, or a
paraphrase a language model produced. A terminal line is a log, a log is copied into issue
reports, and a log is exactly where a leak becomes permanent.

OMISSION IS NOT THE WHOLE CONTROL, AND THIS MODULE ONCE CLAIMED IT WAS. Most of what the
line prints is shaped by the domain model - an agent identifier, a campaign identifier, an
event type, a source, two integers - but ``channel`` is eighty characters of free
placement text, and it was printed verbatim while the paragraph above it said the sink
could not publish anything unscreened. It could. So this sink is the THIRD publication
path and carries the same published screen as the other two: the line is rendered from
:func:`printed_fields`, that whole mapping is screened by
:func:`~adlife.core.domain.serialization.persisted_text_objection` before a character is
written, and a field added to the line is therefore screened without a second edit here.

The screen stays BEST EFFORT. Refusing a line is not a promise that a printed line carries
no credential: an opaque token under no label is indistinguishable from an order number.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from adlife.core.domain.events import DomainEvent
from adlife.core.domain.serialization import persisted_text_objection
from adlife.core.ports.event_sink import EventSinkError, validate_sink_batch

MINUTES_PER_DAY = 1440
MISSING = "-"
"""A field an event does not carry is NAMED rather than dropped, so columns line up."""


def printed_fields(event: DomainEvent) -> dict[str, str]:
    """Exactly what one line publishes, named, in the order the line renders it.

    Simulated time is rendered from the minute alone: day one begins at minute zero, so
    minute 1935 is 08:15 on day two. Nothing here consults a wall clock.

    This mapping is the single description of the sink's published surface: the line is
    joined from its values and the screen is applied to the whole of it.
    """
    if not isinstance(event, DomainEvent):
        raise TypeError("printed_fields needs a DomainEvent")
    day = event.simulated_minute // MINUTES_PER_DAY + 1
    minute_of_day = event.simulated_minute % MINUTES_PER_DAY
    clock = f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"
    return {
        "simulated_minute": f"day {day} {clock}",
        "sequence": f"#{event.sequence}",
        "event_type": event.event_type.value,
        "agent_id": event.agent_id or MISSING,
        "campaign_id": event.campaign_id or MISSING,
        "channel": event.channel or MISSING,
        "source": event.source.value,
    }


def event_line_objection(event: DomainEvent) -> str | None:
    """Name the printed field that must not be published, or return None."""
    return persisted_text_objection(printed_fields(event), label=f"event {event.sequence}")


def format_event_line(event: DomainEvent) -> str:
    """Render one event as the line a person reads."""
    return " | ".join(printed_fields(event).values())


class PlainEventSink:
    """Write human-readable event lines to a text stream."""

    __slots__ = ("_stream",)

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def append_many(self, run_id: str, events: Sequence[DomainEvent]) -> None:
        """Print one line per event, in the order handed over.

        The whole batch is validated AND screened before anything is printed, so a batch
        this sink refuses leaves no half-published tick on the stream.
        """
        batch = validate_sink_batch(run_id, events)
        if not batch:
            return
        for event in batch:
            objection = event_line_objection(event)
            if objection is not None:
                raise EventSinkError(objection)
        self._stream.write("".join(f"{format_event_line(event)}\n" for event in batch))
        self._stream.flush()


__all__ = [
    "MISSING",
    "PlainEventSink",
    "event_line_objection",
    "format_event_line",
    "printed_fields",
]
