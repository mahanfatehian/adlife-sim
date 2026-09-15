"""The observer contract every event sink implements.

A sink is an OBSERVER: it never owns durability and never decides what a run may do. The
authoritative store is :class:`~adlife.core.ports.run_store.RunStore`. What a sink owes
its caller is narrow and therefore testable: it preserves the order it was handed, it
refuses a batch that belongs to another run, and - for the portable JSONL export - it
writes one canonical line per event that reads back as the same event, byte for byte.

The two sinks differ in what they are allowed to show. ``PlainEventSink`` renders a
human-readable line and structurally omits the payload, because a payload is the one
field on an event whose text came from outside this repository. ``JsonlEventSink`` must
write the payload - it is an export - so it screens what it writes with the same narrow
rule the campaign copy carries.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.output.plain import PlainEventSink, format_event_line
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.serialization import (
    MAX_EVENT_LINE_CHARS,
    canonical_event_line,
    canonical_json,
    parse_event_line,
)
from adlife.core.ports.event_sink import EventSink, EventSinkError

PERSIAN_SUMMARY = "آگهی تلفن خیالی را در مسیر دیدم"


def test_a_plain_sink_satisfies_the_event_sink_protocol() -> None:
    assert isinstance(PlainEventSink(io.StringIO()), EventSink)


def test_a_jsonl_sink_satisfies_the_event_sink_protocol(tmp_path: Path) -> None:
    with JsonlEventSink(tmp_path / "events.jsonl") as sink:
        assert isinstance(sink, EventSink)


def test_a_plain_line_derives_the_simulated_day_and_clock_time_from_the_minute(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """Minute 1935 is 08:15 on the second simulated day; the arithmetic is the point."""
    event = event_factory(
        7,
        simulated_minute=1935,
        event_type=EventType.CAMPAIGN_NOTICED,
        campaign_id="campaign-phone",
        channel="mobile-feed",
    )

    assert format_event_line(event) == (
        "day 2 08:15 | #7 | campaign.noticed | person-001 | campaign-phone | mobile-feed | rule"
    )


def test_a_plain_line_renders_the_first_minute_of_the_run_as_day_one_midnight(
    event_factory: Callable[..., DomainEvent],
) -> None:
    event = event_factory(0, simulated_minute=0, event_type=EventType.RUN_STARTED)

    assert format_event_line(event).startswith("day 1 00:00 | #0 | run.started | ")


def test_a_plain_line_names_a_missing_optional_field_rather_than_omitting_it(
    event_factory: Callable[..., DomainEvent],
) -> None:
    event = event_factory(3, agent_id=None, campaign_id=None, channel=None)

    assert format_event_line(event) == "day 1 00:00 | #3 | agent.state_updated | - | - | - | rule"


def test_a_plain_sink_preserves_the_order_it_was_handed(
    event_factory: Callable[..., DomainEvent],
) -> None:
    stream = io.StringIO()
    PlainEventSink(stream).append_many(
        "run-storage",
        [event_factory(5, simulated_minute=75), event_factory(2, simulated_minute=30)],
    )

    printed = stream.getvalue().splitlines()
    assert [line.split(" | ")[1] for line in printed] == ["#5", "#2"]


def test_a_plain_sink_never_prints_the_payload_of_an_event(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The payload is the one event field carrying text from outside this repository."""
    stream = io.StringIO()
    PlainEventSink(stream).append_many(
        "run-storage",
        [event_factory(1, payload={"interpretation": "zzz-distinctive-payload-text"})],
    )

    assert "zzz-distinctive-payload-text" not in stream.getvalue()


def test_a_plain_sink_refuses_a_batch_belonging_to_another_run(
    event_factory: Callable[..., DomainEvent],
) -> None:
    stream = io.StringIO()

    with pytest.raises(EventSinkError, match="run-other"):
        PlainEventSink(stream).append_many("run-storage", [event_factory(0, run_id="run-other")])

    assert stream.getvalue() == ""


def test_a_jsonl_sink_refuses_a_batch_belonging_to_another_run(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="run-other"):
        sink.append_many("run-storage", [event_factory(0, run_id="run-other")])

    assert path.read_bytes() == b""


def test_a_jsonl_sink_writes_one_canonical_line_per_event(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    events = [event_factory(0, simulated_minute=0), event_factory(1, simulated_minute=15)]

    with JsonlEventSink(path) as sink:
        sink.append_many("run-storage", events)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [canonical_event_line(event) for event in events]


def test_a_canonical_event_line_sorts_object_keys(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """Hand-written expected text: object keys ascend, and nothing is padded with spaces."""
    event = event_factory(
        1,
        simulated_minute=15,
        event_type=EventType.CAMPAIGN_IMPRESSION,
        campaign_id="campaign-phone",
        channel="mobile-feed",
        payload={"visibility": 0.8, "frequency_cap": 3},
        caused_by_event_ids=("run-storage:event-00000000",),
    )

    assert canonical_event_line(event) == (
        '{"agent_id":"person-001","campaign_id":"campaign-phone","caused_by_event_ids":'
        '["run-storage:event-00000000"],"channel":"mobile-feed",'
        '"event_id":"run-storage:event-00000001","event_type":"campaign.impression",'
        '"model_id":null,"payload":{"frequency_cap":3,"visibility":0.8},'
        '"prompt_hash":null,"run_id":"run-storage","schema_version":1,"sequence":1,'
        '"simulated_minute":15,"source":"rule"}'
    )


def test_a_canonical_event_line_preserves_array_element_order(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """Keys sort; ARRAYS do not. Reordering causal ids would rewrite the causal record."""
    causes = ("run-storage:event-00000009", "run-storage:event-00000002")
    event = event_factory(11, caused_by_event_ids=causes)

    assert json.loads(canonical_event_line(event))["caused_by_event_ids"] == list(causes)


def test_a_canonical_event_line_reads_back_as_the_same_event(
    event_factory: Callable[..., DomainEvent],
) -> None:
    event = event_factory(
        4,
        simulated_minute=60,
        event_type=EventType.MEMORY_CREATED,
        payload={"summary": PERSIAN_SUMMARY, "salience": 0.5, "tags": ["a", "b"]},
        source=EventSource.MOCK,
    )

    line = canonical_event_line(event)
    assert parse_event_line(line) == event
    assert canonical_event_line(parse_event_line(line)) == line


def test_a_jsonl_line_carries_persian_text_as_utf8_rather_than_escapes(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    with JsonlEventSink(path) as sink:
        sink.append_many(
            "run-storage",
            [event_factory(0, payload={"summary": PERSIAN_SUMMARY})],
        )

    assert PERSIAN_SUMMARY.encode("utf-8") in path.read_bytes()
    assert b"\\u" not in path.read_bytes()


def test_a_jsonl_sink_flushes_each_batch_before_it_is_closed(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    """A live tail must see a committed tick; buffering it until close would hide it."""
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)
    try:
        sink.append_many("run-storage", [event_factory(0)])
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        sink.close()


def test_a_jsonl_sink_appends_across_batches_rather_than_truncating(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    with JsonlEventSink(path) as sink:
        sink.append_many("run-storage", [event_factory(0), event_factory(1)])
        sink.append_many("run-storage", [event_factory(2)])

    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_a_jsonl_sink_reopened_on_an_existing_file_keeps_what_is_already_there(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    with JsonlEventSink(path) as sink:
        sink.append_many("run-storage", [event_factory(0)])
    with JsonlEventSink(path) as sink:
        sink.append_many("run-storage", [event_factory(1)])

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_an_empty_batch_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    with JsonlEventSink(path) as sink:
        sink.append_many("run-storage", [])

    assert path.read_bytes() == b""


def test_a_jsonl_sink_refuses_a_payload_member_named_for_a_credential(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    """The structural rule the cognition port already publishes, applied to an export."""
    path = tmp_path / "events.jsonl"
    event = event_factory(0, payload={"api_key": "0000abcdef1234567890"})

    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="credential"):
        sink.append_many("run-storage", [event])

    assert path.read_bytes() == b""


def test_a_jsonl_sink_refuses_a_payload_string_that_reads_as_a_credential(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    event = event_factory(0, payload={"note": "api_key=0000abcdef1234567890"})

    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="screened"):
        sink.append_many("run-storage", [event])

    assert path.read_bytes() == b""


def test_a_jsonl_sink_accepts_ordinary_campaign_copy(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    """The screen must not refuse the advertising copy this simulator exists to carry."""
    path = tmp_path / "events.jsonl"
    copy_text = "A fictional phone designed for a calmer daily routine."

    with JsonlEventSink(path) as sink:
        sink.append_many("run-storage", [event_factory(0, payload={"message": copy_text})])

    assert copy_text in path.read_text(encoding="utf-8")


def test_a_sink_refuses_anything_that_is_not_a_domain_event(tmp_path: Path) -> None:
    with (
        JsonlEventSink(tmp_path / "events.jsonl") as sink,
        pytest.raises(EventSinkError, match="DomainEvent"),
    ):
        sink.append_many("run-storage", ["not-an-event"])  # type: ignore[list-item]


def test_a_sink_refuses_a_run_id_that_is_not_a_run_identifier(tmp_path: Path) -> None:
    with (
        JsonlEventSink(tmp_path / "events.jsonl") as sink,
        pytest.raises(EventSinkError, match="run identifier"),
    ):
        sink.append_many("../escape", [])


def test_a_closed_jsonl_sink_refuses_a_further_batch(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    sink = JsonlEventSink(tmp_path / "events.jsonl")
    sink.close()

    with pytest.raises(EventSinkError, match="closed"):
        sink.append_many("run-storage", [event_factory(0)])


def test_closing_a_jsonl_sink_twice_is_harmless(tmp_path: Path) -> None:
    sink = JsonlEventSink(tmp_path / "events.jsonl")
    sink.close()
    sink.close()


def test_an_event_line_beyond_the_documented_bound_is_refused_before_it_is_parsed() -> None:
    """The bound is the reason a corrupt export or a tampered row cannot exhaust memory."""
    with pytest.raises(ValueError, match="exceeds"):
        parse_event_line("x" * (MAX_EVENT_LINE_CHARS + 1))


def test_an_event_line_that_is_not_text_is_refused() -> None:
    with pytest.raises(TypeError, match="string"):
        parse_event_line(b"{}")  # type: ignore[arg-type]


def test_canonical_json_refuses_a_document_it_cannot_render() -> None:
    with pytest.raises(TypeError, match="pydantic model or a mapping"):
        canonical_json(["not", "a", "document"])  # type: ignore[arg-type]


def test_a_canonical_event_line_needs_an_event() -> None:
    with pytest.raises(TypeError, match="DomainEvent"):
        canonical_event_line({"event_id": "x"})  # type: ignore[arg-type]


def test_a_plain_line_needs_an_event() -> None:
    with pytest.raises(TypeError, match="DomainEvent"):
        format_event_line("run-storage:event-00000000")  # type: ignore[arg-type]


def test_a_jsonl_sink_needs_a_path() -> None:
    with pytest.raises(TypeError, match="Path"):
        JsonlEventSink("events.jsonl")  # type: ignore[arg-type]
