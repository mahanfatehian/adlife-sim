"""The portable JSONL export sink: one canonical event document per line.

Specification section 13 makes SQLite the durable store and JSONL the portable export, so
this sink is a second, plainer copy of the same stream - the one a reader can tail, diff
or hand to another tool without a database driver.

Two properties make it usable for that. The line is CANONICAL, so the same event written
by two processes is the same bytes and two runs can be diffed line by line. And the batch
is FLUSHED before ``append_many`` returns, so a live reader sees a committed tick instead
of whatever the operating system felt like writing.

Unlike the plain sink this one must write the payload - an export that dropped it would
not be an export - so it screens what it writes with the same published rule the campaign
copy passes, and refuses the whole batch rather than writing a line and complaining after.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Self

from adlife.core.domain.events import DomainEvent
from adlife.core.domain.serialization import canonical_event_line, persisted_text_objection
from adlife.core.ports.event_sink import EventSinkError, validate_sink_batch


class JsonlEventSink:
    """Append canonical event lines to a UTF-8 file, one line per event."""

    __slots__ = ("_handle", "_path")

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be a Path")
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: IO[str] | None = path.open("a", encoding="utf-8", newline="\n")

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def append_many(self, run_id: str, events: Sequence[DomainEvent]) -> None:
        """Write and flush one tick.

        Every line is rendered and screened before a byte is written, so a refused batch
        leaves the export exactly as it was.
        """
        if self._handle is None:
            raise EventSinkError("this event sink is closed")
        batch = validate_sink_batch(run_id, events)
        if not batch:
            return
        lines = []
        for event in batch:
            objection = persisted_text_objection(
                dict(event.payload), label=f"the payload of event {event.sequence}"
            )
            if objection is not None:
                raise EventSinkError(objection)
            lines.append(f"{canonical_event_line(event)}\n")
        self._handle.write("".join(lines))
        self._handle.flush()

    def close(self) -> None:
        """Close the underlying file. Closing twice is deliberately harmless."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


__all__ = ["JsonlEventSink"]
