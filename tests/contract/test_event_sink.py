"""The observer contract every event sink implements.

A sink is an OBSERVER: it never owns durability and never decides what a run may do. The
authoritative store is :class:`~adlife.core.ports.run_store.RunStore`. What a sink owes
its caller is narrow and therefore testable: it preserves the order it was handed, it
refuses a batch that belongs to another run, and - for the portable JSONL export - it
writes one canonical line per event that reads back as the same event, byte for byte.

The two sinks differ in what they are allowed to show. ``PlainEventSink`` renders a
human-readable line and structurally omits the payload; it also SCREENS every field it
does print, because ``channel`` is free placement text and a printed line is a log.
``JsonlEventSink`` must write the payload - it is an export - so it screens the whole
document with the same narrow rule the campaign copy carries. Neither screen is a
guarantee that a published line carries no credential; both are best effort.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.output.plain import (
    PlainEventSink,
    event_line_objection,
    format_event_line,
    printed_fields,
)
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.serialization import (
    MAX_EVENT_LINE_CHARS,
    DocumentNotSerialisable,
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


def test_a_plain_sink_refuses_a_channel_that_reads_as_a_credential(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """``channel`` is eighty characters of free placement text on a PUBLICATION path.

    The plain sink is the third writer of the same event, and it printed this field
    verbatim while its module docstring claimed it could publish nothing unscreened.
    """
    stream = io.StringIO()
    event = event_factory(0, channel="mobile-feed-api_key=0000abcdef1234567890")

    with pytest.raises(EventSinkError, match="credential"):
        PlainEventSink(stream).append_many("run-storage", [event])

    assert stream.getvalue() == ""


def test_a_plain_sink_refuses_the_whole_batch_around_an_unpublishable_line(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A tick is refused whole: the first line must not survive the second's refusal."""
    stream = io.StringIO()
    batch = [
        event_factory(0),
        event_factory(1, channel="mobile-feed-api_key=0000abcdef1234567890"),
    ]

    with pytest.raises(EventSinkError, match="credential"):
        PlainEventSink(stream).append_many("run-storage", batch)

    assert stream.getvalue() == ""


def test_a_plain_sink_screens_every_field_its_line_renders(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The screen is applied to the mapping the line is built from, not to a copy of it.

    A field added to :func:`printed_fields` is therefore screened without a second edit,
    which is what the module docstring now claims. The screen remains best effort.
    """
    event = event_factory(0, channel="mobile-feed-api_key=0000abcdef1234567890")

    assert set(printed_fields(event)) == {
        "simulated_minute",
        "sequence",
        "event_type",
        "agent_id",
        "campaign_id",
        "channel",
        "source",
    }
    assert format_event_line(event) == " | ".join(printed_fields(event).values())
    assert event_line_objection(event) is not None
    assert event_line_objection(event_factory(0, channel="mobile-feed")) is None


def test_a_plain_sink_still_prints_the_channels_this_simulator_really_runs(
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A screen that refused ``highway-billboard`` would be a refusal of the product."""
    stream = io.StringIO()
    PlainEventSink(stream).append_many(
        "run-storage",
        [
            event_factory(0, channel="mobile-feed", campaign_id="campaign-phone"),
            event_factory(1, channel="highway-billboard", campaign_id="campaign-phone"),
        ],
    )

    printed = stream.getvalue()
    assert "mobile-feed" in printed
    assert "highway-billboard" in printed


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


def test_a_jsonl_sink_refuses_a_model_identifier_that_reads_as_a_credential(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    """The export writes every field of an event, so every field of one is screened."""
    path = tmp_path / "events.jsonl"
    event = event_factory(0, model_id="local-llama-3-api_key=0000abcdef1234567890")

    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="credential"):
        sink.append_many("run-storage", [event])

    assert path.read_bytes() == b""


def test_a_jsonl_sink_refuses_a_channel_that_reads_as_a_credential(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    event = event_factory(0, channel="mobile-feed-api_key=0000abcdef1234567890")

    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="credential"):
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


def test_a_jsonl_sink_refuses_a_line_its_own_reader_could_not_read_back(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    """``MAX_EVENT_LINE_CHARS`` is the reader's bound, so the writer has to apply it too.

    Enforced only on the read side it is worse than absent: the sink reports success, the
    oversized line is durably in the file, and every later read of an otherwise intact
    export refuses it with nothing to point at. The authoritative store already refuses
    such a batch before its transaction opens; this is the same bound in the sibling
    adapter that writes the same lines.
    """
    path = tmp_path / "events.jsonl"
    oversized = event_factory(0, payload={"note": "x" * (MAX_EVENT_LINE_CHARS + 1)})

    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="characters"):
        sink.append_many("run-storage", [oversized])

    assert path.read_bytes() == b""


def test_a_jsonl_sink_refuses_an_oversized_line_without_writing_the_batch_around_it(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    """A batch is refused whole: the first event must not survive the second's refusal."""
    path = tmp_path / "events.jsonl"
    batch = [
        event_factory(0),
        event_factory(1, payload={"note": "x" * (MAX_EVENT_LINE_CHARS + 1)}),
    ]

    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="characters"):
        sink.append_many("run-storage", batch)

    assert path.read_bytes() == b""


def bypass_constructed_deep_event(depth: int) -> DomainEvent:
    """An event the domain model would refuse, built without it.

    The depth bound closed in :mod:`adlife.core.domain.json_values` means an event like
    this cannot be validated into existence. A serializer must still not fail with a bare
    ``ValueError``, because ``model_construct`` is a real door and a future field could
    reopen the same one.
    """
    payload: dict[str, object] = {"leaf": 1}
    for _ in range(depth - 1):
        payload = {"nested": payload}
    return DomainEvent.model_construct(
        schema_version=1,
        event_id="run-storage:event-00000000",
        run_id="run-storage",
        simulated_minute=0,
        sequence=0,
        event_type=EventType.STATE_UPDATED,
        agent_id=None,
        campaign_id=None,
        channel=None,
        payload=payload,
        source=EventSource.RULE,
        model_id=None,
        prompt_hash=None,
        caused_by_event_ids=(),
    )


def test_a_jsonl_sink_refuses_a_document_its_serializer_cannot_render(tmp_path: Path) -> None:
    """A bare ``ValueError`` from pydantic-core escaped the whole EventSinkError family."""
    path = tmp_path / "events.jsonl"

    with JsonlEventSink(path) as sink, pytest.raises(EventSinkError, match="could not be rendered"):
        sink.append_many("run-storage", [bypass_constructed_deep_event(200)])

    assert path.read_bytes() == b""


def test_canonical_json_names_a_document_its_serializer_refuses(tmp_path: Path) -> None:
    """The refusal is a distinct type so a caller can tell it from "not a document"."""
    with pytest.raises(DocumentNotSerialisable, match="could not be rendered"):
        canonical_event_line(bypass_constructed_deep_event(200))


def test_a_document_that_cannot_be_rendered_carries_no_cause(tmp_path: Path) -> None:
    """The document pydantic refused is user-supplied text and must not reach a traceback."""
    with pytest.raises(DocumentNotSerialisable) as raised:
        canonical_event_line(bypass_constructed_deep_event(200))

    assert raised.value.__cause__ is None
    assert "leaf" not in str(raised.value)


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


def test_the_jsonl_export_line_ending_is_a_bare_line_feed_on_every_platform(
    event_factory: Callable[..., DomainEvent], tmp_path: Path
) -> None:
    """The export is a portable artifact, so its bytes are part of its contract.

    The file is opened with an explicit newline. Without it the platform translates every
    newline into a carriage return and a newline on Windows, so one run produced two
    different exports on two operating systems - and the store's byte-length check, which
    compares the export against the offsets it recorded, then refused the file this sink
    had just written. A reader that splits on lines cannot see the difference; only the
    bytes can, so only a byte-exact assertion pins it.
    """
    path = tmp_path / "events.jsonl"
    with JsonlEventSink(path) as sink:
        sink.append_many("run-storage", [event_factory(0), event_factory(1)])

    written = path.read_bytes()

    assert written.count(b"\n") == 2
    assert b"\r\n" not in written
    assert written.endswith(b"\n")
