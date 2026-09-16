"""What is specific to the SQLite adapter: its schema, its pragmas, and its refusals.

The shared behaviour lives in :mod:`tests.contract.test_run_store`. Here the artifact is
attacked directly - the database file is overwritten, rows are edited behind the store's
back, the schema version is moved, the export is truncated - because the whole point of a
durable artifact is that it either reads back as what was written or refuses to read back
at all. "Reads back without complaining" is the failure mode this module exists to catch.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import re
import sqlite3
import tracemalloc
from collections.abc import Callable
from pathlib import Path

import pytest

from adlife.adapters.storage import schema as schema_module
from adlife.adapters.storage import sqlite_store as sqlite_store_module
from adlife.adapters.storage.schema import (
    BUSY_TIMEOUT_MS,
    SCHEMA_VERSION,
    TABLE_NAMES,
    connect_to_database,
)
from adlife.adapters.storage.sqlite_store import (
    MAX_INPUT_DOCUMENT_BYTES,
    MAX_STORED_DOCUMENT_CHARS,
    READ_CHUNK_CHARS,
    SQLiteRunStore,
    iter_export_lines,
)
from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.serialization import (
    MAX_EVENT_LINE_CHARS,
    canonical_event_line,
    canonical_json,
)
from adlife.core.domain.state import ConsumerState, Memory
from adlife.core.ports.cognition import ProviderUsage
from adlife.core.ports.run_store import (
    MAX_CHECKPOINTS,
    MINUTES_PER_DAY,
    CorruptRunArtifact,
    ExportNotExtended,
    InvalidEventBatch,
    ProviderUsageLog,
    RunCheckpoint,
    SchemaVersionMismatch,
    StorageError,
    UnsafeRunLocation,
)
from adlife.core.simulation.engine import canonical_sha256

PERSIAN_SUMMARY = "آگهی بیلبورد را در بزرگراه شمالی دیدم"


@pytest.fixture
def store(tmp_path: Path) -> SQLiteRunStore:
    return SQLiteRunStore(tmp_path)


@pytest.fixture
def started_run(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> SQLiteRunStore:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(
        [
            event_factory(0, event_type=EventType.RUN_STARTED, agent_id=None),
            event_factory(1, simulated_minute=15),
            event_factory(2, simulated_minute=30),
        ]
    )
    return store


def raw(store: SQLiteRunStore, run_id: str) -> sqlite3.Connection:
    return sqlite3.connect(store.database_path(run_id))


def stored_sequences(store: SQLiteRunStore, run_id: str) -> list[int]:
    """What the authoritative database really holds, whatever the store just reported."""
    with raw(store, run_id) as connection:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT sequence FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
            )
        ]


def files_carrying(store: SQLiteRunStore, run_id: str, secret: str) -> list[str]:
    """Every artifact byte of the run, the write-ahead log included."""
    return [
        path.name
        for path in sorted(store.run_directory(run_id).rglob("*"))
        if path.is_file() and secret.encode("utf-8") in path.read_bytes()
    ]


def test_the_schema_creates_exactly_the_documented_tables(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert names == set(TABLE_NAMES)


def test_the_schema_version_is_recorded_exactly_once(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        rows = connection.execute("SELECT version FROM schema_meta").fetchall()

    assert rows == [(SCHEMA_VERSION,)]


def test_an_unknown_schema_version_is_refused_rather_than_read(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """A future version may mean anything; guessing is how silent corruption happens."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION + 1,))

    with pytest.raises(SchemaVersionMismatch, match=str(SCHEMA_VERSION + 1)):
        started_run.load_run(run_manifest.run_id)


def test_a_missing_schema_version_row_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DELETE FROM schema_meta")

    with pytest.raises(CorruptRunArtifact, match="schema"):
        started_run.load_run(run_manifest.run_id)


ORPHAN_ROWS: tuple[tuple[str, str, tuple[object, ...]], ...] = (
    (
        "events",
        "INSERT INTO events (event_id, run_id, sequence, simulated_minute, "
        "event_type, event_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("x:event-00000000", "run-unknown", 0, 0, "run.started", "{}"),
    ),
    (
        "checkpoints",
        "INSERT INTO checkpoints (run_id, simulated_minute, checkpoint_json) VALUES (?, ?, ?)",
        ("run-unknown", 0, "{}"),
    ),
    (
        "metrics",
        "INSERT INTO metrics (run_id, metric_name, metric_value) VALUES (?, ?, ?)",
        ("run-unknown", "notice_rate", 0.5),
    ),
)
"""One row per table that references ``runs``, each naming a run that does not exist."""


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [(statement, parameters) for _, statement, parameters in ORPHAN_ROWS],
    ids=[table for table, _, _ in ORPHAN_ROWS],
)
def test_foreign_keys_are_enforced_by_the_connection_the_store_opens(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    """``PRAGMA foreign_keys`` is a silent no-op inside a transaction, so test the effect.

    Every table that references ``runs`` is covered: a row orphaned from its run would
    not be seen by the store's own cross-checks either, because each of them filters by
    ``run_id`` and so cannot notice a row belonging to no run at all.
    """
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, parameters)
    finally:
        connection.close()


def test_the_connection_runs_in_write_ahead_logging_mode(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """WAL is what lets the live interface read a run while the engine is writing it."""
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        connection.close()

    assert mode.lower() == "wal"


def test_the_connection_waits_rather_than_failing_immediately_on_a_busy_database(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        connection.close()

    assert busy_timeout == BUSY_TIMEOUT_MS


def test_the_busy_timeout_is_the_one_this_module_sets_and_not_the_driver_default(
    started_run: SQLiteRunStore, run_manifest: RunManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The driver's own default is five seconds, which is also the value we want.

    Asserting five seconds therefore cannot fail: it is what a connection with no pragma
    at all reports. Moving the configured value somewhere the driver would never choose
    makes the assertion bite on the line that sets it.
    """
    monkeypatch.setattr(schema_module, "BUSY_TIMEOUT_MS", 1234)
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        connection.close()

    assert busy_timeout == 1234


def test_the_connection_uses_the_documented_synchronous_level(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """NORMAL: a crash cannot corrupt the file, but a power loss may drop recent ticks."""
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
    finally:
        connection.close()

    assert synchronous == 1


def test_a_second_reader_sees_a_committed_tick(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_manifest.run_id,)
        ).fetchone()[0]

    assert count == 3


def test_a_database_file_that_is_not_a_database_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.database_path(run_manifest.run_id).write_bytes(b"not a database at all" * 64)

    with pytest.raises(CorruptRunArtifact):
        started_run.load_run(run_manifest.run_id)


def test_a_missing_database_file_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.database_path(run_manifest.run_id).unlink()

    with pytest.raises(CorruptRunArtifact):
        started_run.load_run(run_manifest.run_id)


def test_an_event_document_that_is_not_json_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE events SET event_json = ? WHERE sequence = 1", ("{truncated",))

    with pytest.raises(CorruptRunArtifact, match="event"):
        started_run.load_run(run_manifest.run_id)


def test_an_event_document_that_disagrees_with_its_own_row_is_refused(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The indexed columns and the document are one record; a tamper moves only one."""
    forged = canonical_event_line(event_factory(1, simulated_minute=999))
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE events SET event_json = ? WHERE sequence = 1", (forged,))

    with pytest.raises(CorruptRunArtifact, match="row"):
        started_run.load_run(run_manifest.run_id)


def test_a_deleted_event_row_is_refused_rather_than_silently_shortening_the_run(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DELETE FROM events WHERE sequence = 1")

    with pytest.raises(CorruptRunArtifact, match="contiguous"):
        started_run.load_run(run_manifest.run_id)


def test_events_are_returned_in_sequence_order_whatever_their_physical_order(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """Nothing in this store may depend on rowid or insertion order."""
    with raw(started_run, run_manifest.run_id) as connection:
        rows = connection.execute(
            "SELECT event_id, run_id, sequence, simulated_minute, event_type, event_json, "
            "export_offset FROM events ORDER BY sequence"
        ).fetchall()
        connection.execute("DELETE FROM events")
        connection.executemany(
            "INSERT INTO events (event_id, run_id, sequence, simulated_minute, event_type, "
            "event_json, export_offset) VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(reversed(rows)),
        )

    loaded = started_run.load_run(run_manifest.run_id)

    assert [event.sequence for event in loaded.events] == [0, 1, 2]


def test_untrusted_text_is_bound_as_a_parameter_rather_than_interpolated(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """Campaign copy is untrusted input and reaches SQLite on every tick."""
    hostile = "'); DROP TABLE events; --"
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events([event_factory(0, payload={"message": hostile})])

    loaded = store.load_run(run_manifest.run_id)
    assert loaded.events[0].payload["message"] == hostile
    with raw(store, run_manifest.run_id) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_persian_text_survives_the_database_round_trip(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(
        [
            event_factory(
                0,
                event_type=EventType.MEMORY_CREATED,
                payload={"summary": PERSIAN_SUMMARY},
            )
        ]
    )

    loaded = store.load_run(run_manifest.run_id)

    assert loaded.events[0].payload["summary"] == PERSIAN_SUMMARY
    export = store.events_jsonl_path(run_manifest.run_id).read_bytes()
    assert PERSIAN_SUMMARY.encode("utf-8") in export


def test_a_truncated_export_line_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """A half-written last line must never read back as a valid run.

    THE DAMAGE IS WRITTEN AS BYTES, and the refusal has to name it. A text-mode write
    translates every newline into a carriage return and a newline on Windows, so the
    export stops matching the database at LINE 1 - and the run is refused for a line
    ending this test never meant to inject, before the reader ever reaches the
    truncation it did. A test that cannot tell those apart passes, on this platform,
    for a reason that has nothing to do with the damage it describes.
    """
    path = started_run.events_jsonl_path(run_manifest.run_id)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) - 20])

    with pytest.raises(CorruptRunArtifact, match="unterminated"):
        started_run.load_run(run_manifest.run_id)


def test_an_export_missing_a_line_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The refusal must COUNT the lines rather than trip over a rewritten line ending."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    kept = path.read_bytes().split(b"\n")[:2]
    path.write_bytes(b"\n".join(kept) + b"\n")

    with pytest.raises(CorruptRunArtifact, match="holds 2 lines where the database holds 3"):
        started_run.load_run(run_manifest.run_id)


def test_an_export_line_that_disagrees_with_the_database_is_refused(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The refusal must name the line the damage is on, not the one in front of it."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    lines = path.read_bytes().split(b"\n")
    lines[1] = canonical_event_line(event_factory(1, simulated_minute=777)).encode("utf-8")
    path.write_bytes(b"\n".join(lines))

    with pytest.raises(CorruptRunArtifact, match="line 2 is not the event"):
        started_run.load_run(run_manifest.run_id)


def test_an_export_whose_last_line_has_no_newline_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """An append interrupted by a kill leaves exactly this shape."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))

    with pytest.raises(CorruptRunArtifact, match=re.escape("events.jsonl")):
        started_run.load_run(run_manifest.run_id)


def test_an_export_rewritten_with_this_platform_s_line_endings_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The export is byte-exact, so a copy that gained carriage returns is not it.

    This is the platform trap the damage tests above were falling into, pinned from the
    reader's side: opened in universal-newline mode the reader would strip the carriage
    returns and accept a file the writer never wrote, and every byte-for-byte claim this
    module makes about the export would hold only where a line ending is one byte.
    """
    path = started_run.events_jsonl_path(run_manifest.run_id)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    with pytest.raises(CorruptRunArtifact, match="line 1 is not the event"):
        started_run.load_run(run_manifest.run_id)


def test_an_export_line_beyond_the_documented_bound_is_refused_without_reading_it_all(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    path = started_run.events_jsonl_path(run_manifest.run_id)
    with path.open("ab") as handle:
        handle.write(b"x" * 2_000_000 + b"\n")

    with pytest.raises(CorruptRunArtifact, match="exceeds"):
        started_run.load_run(run_manifest.run_id)


def test_a_tampered_manifest_document_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    path = started_run.run_json_path(run_manifest.run_id)
    path.write_bytes(path.read_bytes().replace(b'"seed":42', b'"seed":43'))

    with pytest.raises(CorruptRunArtifact, match=re.escape("run.json")):
        started_run.load_run(run_manifest.run_id)


def test_a_tampered_input_scenario_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    path = started_run.scenario_json_path(run_manifest.run_id)
    path.write_bytes(path.read_bytes().replace(b"Contract fixture", b"Tampered fixture"))

    with pytest.raises(CorruptRunArtifact, match="scenario"):
        started_run.load_run(run_manifest.run_id)


def test_a_metric_edited_behind_the_store_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The metrics table and the stored result are two copies of one number."""
    started_run.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id,
            status="completed",
            final_minute=30,
            event_count=3,
            metrics={"notice_rate": 0.5},
        )
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE metrics SET metric_value = 0.9")

    with pytest.raises(CorruptRunArtifact, match="metric"):
        started_run.load_run(run_manifest.run_id)


@pytest.mark.parametrize("tampered", ["not-a-number", b"\x00\x01"], ids=["text", "blob"])
def test_a_metric_value_that_is_not_a_number_stays_inside_the_storage_family(
    started_run: SQLiteRunStore, run_manifest: RunManifest, tampered: object
) -> None:
    """``REAL NOT NULL`` is an affinity, not a type: SQLite stores what it is given.

    The cross-check coerced the column with a bare ``float()``, so a tampered row escaped
    as a raw ``ValueError`` from inside ``load_run`` - straight past the ``StorageError``
    family the command line maps to exit code 4, and reported as an unexpected defect
    rather than as the corrupt artifact it is.
    """
    started_run.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id,
            status="completed",
            final_minute=30,
            event_count=3,
            metrics={"notice_rate": 0.5},
        )
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE metrics SET metric_value = ?", (tampered,))

    with pytest.raises(CorruptRunArtifact, match="metric"):
        started_run.load_run(run_manifest.run_id)


def test_metrics_are_stored_one_row_per_metric_in_a_stable_order(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The order the WRITER left is the claim, so the rows are read in the order it wrote.

    ``ORDER BY metric_name`` in this test's own query re-sorted whatever the writer had
    done, so the assertion held with ``sorted()`` deleted from the insert: it pinned the
    query the test wrote rather than the order the store stores. ``rowid`` is insertion
    order, and the metrics are handed over in an order that is NOT the sorted one, so a
    writer that stopped sorting is visible here - which is what makes two runs with the
    same metrics two identical files.
    """
    started_run.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id,
            status="completed",
            final_minute=30,
            event_count=3,
            metrics={"notice_rate": 0.5, "average_recall": 0.125, "shares": 2.0},
        )
    )

    with raw(started_run, run_manifest.run_id) as connection:
        rows = connection.execute(
            "SELECT metric_name, metric_value FROM metrics ORDER BY rowid"
        ).fetchall()

    assert rows == [("average_recall", 0.125), ("notice_rate", 0.5), ("shares", 2.0)]


def test_an_interrupted_checkpoint_write_stores_no_partial_checkpoint(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    consumer_state: ConsumerState,
    failing_connection: Callable[[int], None],
) -> None:
    failing_connection(2)

    with pytest.raises(StorageError):
        started_run.save_checkpoint(
            RunCheckpoint(
                run_id=run_manifest.run_id,
                simulated_minute=1440,
                next_event_sequence=3,
                states=(consumer_state,),
            )
        )

    assert started_run.load_run(run_manifest.run_id).checkpoints == ()


def test_a_checkpoint_document_that_is_not_a_checkpoint_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    started_run.save_checkpoint(
        RunCheckpoint(
            run_id=run_manifest.run_id,
            simulated_minute=1440,
            next_event_sequence=3,
            states=(consumer_state,),
        )
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE checkpoints SET checkpoint_json = ?", ("{}",))

    with pytest.raises(CorruptRunArtifact, match="checkpoint"):
        started_run.load_run(run_manifest.run_id)


def test_a_stored_status_that_no_run_can_have_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET status = ?", ("victorious",))

    with pytest.raises(CorruptRunArtifact, match="status"):
        started_run.load_run(run_manifest.run_id)


def test_a_completed_status_without_a_result_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET status = ?", ("completed",))

    with pytest.raises(CorruptRunArtifact, match="result"):
        started_run.load_run(run_manifest.run_id)


def test_a_result_that_contradicts_the_stored_status_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id, status="completed", final_minute=30, event_count=3
        )
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET status = ?", ("interrupted",))

    with pytest.raises(CorruptRunArtifact, match="status"):
        started_run.load_run(run_manifest.run_id)


def test_streaming_refuses_a_tampered_document_as_loudly_as_loading_does(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE events SET event_json = ? WHERE sequence = 2", ("{",))

    with pytest.raises(CorruptRunArtifact):
        list(started_run.iter_events(run_manifest.run_id))


# --- reading an artifact this process did not write -----------------------------------
#
# A resumed run, the replay command and the report command all open a run directory with a
# cold in-memory export counter, which is a different code path from the one a writing
# store takes. These exercise it.


def test_a_second_store_continues_an_existing_export(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    reopened = SQLiteRunStore(started_run.root)

    reopened.append_events([event_factory(3, simulated_minute=45)])

    lines = reopened.events_jsonl_path(run_manifest.run_id).read_text("utf-8").splitlines()
    assert len(lines) == 4


def test_a_second_store_refuses_to_continue_an_export_that_lost_a_line(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The two artifacts must agree BEFORE the tick is committed to either of them.

    A refusal evaluated after its own effect is not a refusal. It widens the divergence
    it reports, and a caller that retries the tick it was told was rejected is then told
    the sequence is taken - a run that can neither continue nor be read.
    """
    path = started_run.events_jsonl_path(run_manifest.run_id)
    kept = path.read_bytes().split(b"\n")[:2]
    path.write_bytes(b"\n".join(kept) + b"\n")
    reopened = SQLiteRunStore(started_run.root)

    for _ in range(2):
        with pytest.raises(CorruptRunArtifact, match="holds 2 lines"):
            reopened.append_events([event_factory(3, simulated_minute=45)])

    assert stored_sequences(reopened, run_manifest.run_id) == [0, 1, 2]
    assert path.read_bytes() == b"\n".join(kept) + b"\n"


def test_a_second_store_refuses_to_continue_an_unterminated_export(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    path = started_run.events_jsonl_path(run_manifest.run_id)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    reopened = SQLiteRunStore(started_run.root)

    with pytest.raises(CorruptRunArtifact, match="unterminated"):
        reopened.append_events([event_factory(3, simulated_minute=45)])


def test_an_export_that_shrank_behind_a_live_store_is_still_detected(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The guard must MEASURE the export, not recall what this instance last wrote to it.

    ``started_run`` is the instance that wrote every one of those three lines, so a
    remembered count says three and the database says three and the two agree - about a
    file that no longer holds three lines. The divergence then widens with every tick,
    which is the failure the guard exists to prevent, arriving by a different door.
    """
    path = started_run.events_jsonl_path(run_manifest.run_id)
    kept = path.read_bytes().split(b"\n")[:2]
    path.write_bytes(b"\n".join(kept) + b"\n")

    with pytest.raises(CorruptRunArtifact, match="lines"):
        started_run.append_events([event_factory(3, simulated_minute=45)])

    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2]
    assert path.read_bytes().count(b"\n") == 2


def test_a_run_another_writer_extended_consistently_is_not_condemned(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The other half of the same defect: a remembered count also condemns a healthy run.

    A second store appends a tick to BOTH artifacts, so they agree exactly. The first
    instance's memory of the file is now stale-low, and a guard evaluated against it
    reports a divergence that does not exist - refusing a run whose two artifacts are
    line-for-line identical.
    """
    second = SQLiteRunStore(started_run.root)
    second.append_events([event_factory(3, simulated_minute=45)])

    started_run.append_events([event_factory(4, simulated_minute=60)])

    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2, 3, 4]
    assert started_run.load_run(run_manifest.run_id).events[4].simulated_minute == 60


class ExportReadTally:
    """Count the characters the store reads back out of ``events.jsonl``."""

    def __init__(self) -> None:
        self.characters = 0


def count_export_reads(monkeypatch: pytest.MonkeyPatch) -> ExportReadTally:
    """Tally every character read from any events.jsonl, whoever opens it and however."""
    tally = ExportReadTally()
    real_open = Path.open

    class _CountingHandle:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        def read(self, *arguments: object) -> str:
            chunk = self._handle.read(*arguments)  # type: ignore[attr-defined]
            tally.characters += len(chunk)
            return chunk

        def __iter__(self) -> object:
            return iter(self._handle)  # type: ignore[call-overload]

        def __enter__(self) -> _CountingHandle:
            return self

        def __exit__(self, *details: object) -> None:
            self._handle.close()  # type: ignore[attr-defined]

        def __getattr__(self, name: str) -> object:
            return getattr(self._handle, name)

    def counting_open(self: Path, mode: str = "r", *arguments: object, **keywords: object):
        handle = real_open(self, mode, *arguments, **keywords)  # type: ignore[arg-type]
        if self.name == "events.jsonl" and "r" in mode and "+" not in mode:
            return _CountingHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", counting_open)
    return tally


def test_the_divergence_guard_does_not_grow_with_the_number_of_stored_events(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must stay honest AND stay affordable; it was only the first.

    Measuring the export by streaming it is a measurement, and it was the right answer to
    a remembered count. It is also quadratic in the length of a run: every tick re-reads
    every line already written. On a maximum run - thirty agents over seven simulated days,
    672 ticks - that is roughly ninety seconds of pure reading, against the ten seconds
    specification section 20 allows the WHOLE rule-mode run.

    So the cost model is pinned here: what an append reads from the export does not grow
    with what the export already holds.
    """
    tally = count_export_reads(monkeypatch)

    before = tally.characters
    started_run.append_events([event_factory(3, simulated_minute=45)])
    early = tally.characters - before

    for sequence in range(4, 60):
        started_run.append_events([event_factory(sequence, simulated_minute=60)])

    before = tally.characters
    started_run.append_events([event_factory(60, simulated_minute=75)])
    late = tally.characters - before

    assert late <= early
    assert late <= MAX_EVENT_LINE_CHARS
    assert started_run.events_jsonl_path(run_manifest.run_id).stat().st_size > 0


def test_the_divergence_guard_still_measures_the_export_it_guards(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constant-time guard that consulted nothing would be a remembered count again.

    The file's own length is what the fast path compares, so a ``stat`` that never happens
    is the failure this pins.
    """
    consulted: list[str] = []
    real_stat = Path.stat

    def counting_stat(self: Path, **keywords: object) -> object:
        if self.name == "events.jsonl":
            consulted.append(self.name)
        return real_stat(self, **keywords)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", counting_stat)

    started_run.append_events([event_factory(3, simulated_minute=45)])

    assert consulted


def test_a_tick_is_refused_onto_a_database_with_a_hole_in_it(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """``MAX(sequence) + 1`` is where the next event goes, not proof of a run.

    With sequence one deleted the database still reports three events and the export still
    holds three lines, so both halves of the old guard agreed - about a stream with a hole
    in it. The tick was accepted, and every read of the run refused it afterwards.
    """
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DELETE FROM events WHERE sequence = 1")

    with pytest.raises(CorruptRunArtifact, match="contiguous"):
        started_run.append_events([event_factory(3, simulated_minute=45)])

    assert stored_sequences(started_run, run_manifest.run_id) == [0, 2]


def test_a_checkpoint_is_refused_onto_a_database_with_a_hole_in_it(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    consumer_state: ConsumerState,
) -> None:
    """Every writer asks the same question, so every writer inherits the same answer."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DELETE FROM events WHERE sequence = 1")

    with pytest.raises(CorruptRunArtifact, match="contiguous"):
        started_run.save_checkpoint(
            RunCheckpoint(
                run_id=run_manifest.run_id,
                simulated_minute=0,
                next_event_sequence=3,
                states=(consumer_state,),
            )
        )


def test_a_stored_sequence_below_zero_is_refused(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A count that matches the maximum is not contiguity; the run has to start at zero.

    ``sequence`` carries no CHECK constraint, so a tampered row can hold a negative one.
    Three rows numbered -1, 1 and 2 give a count of three and a maximum of two, which is
    exactly what an unbroken run of three events gives.
    """
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE events SET sequence = -1 WHERE sequence = 0")

    with pytest.raises(CorruptRunArtifact, match="contiguous"):
        started_run.append_events([event_factory(3, simulated_minute=45)])


def test_a_missing_export_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.events_jsonl_path(run_manifest.run_id).unlink()

    with pytest.raises(CorruptRunArtifact, match=re.escape("events.jsonl")):
        started_run.load_run(run_manifest.run_id)


def test_a_stored_manifest_that_is_not_a_manifest_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET manifest_json = ?", ("{}",))

    with pytest.raises(CorruptRunArtifact, match="manifest"):
        started_run.load_run(run_manifest.run_id)


def test_a_stored_manifest_naming_another_run_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    forged = canonical_json(run_manifest.model_copy(update={"run_id": "run-other"}))
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET manifest_json = ?", (forged,))

    with pytest.raises(CorruptRunArtifact, match="another run"):
        started_run.load_run(run_manifest.run_id)


def test_a_stored_result_that_is_not_a_result_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET status = ?, result_json = ?", ("completed", "{}"))

    with pytest.raises(CorruptRunArtifact, match="result"):
        started_run.load_run(run_manifest.run_id)


def test_a_stored_result_naming_another_run_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    forged = canonical_json(
        SimulationResult(run_id="run-other", status="completed", final_minute=30, event_count=3)
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET status = ?, result_json = ?", ("completed", forged))

    with pytest.raises(CorruptRunArtifact, match="another run"):
        started_run.load_run(run_manifest.run_id)


def test_a_missing_manifest_document_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.run_json_path(run_manifest.run_id).unlink()

    with pytest.raises(CorruptRunArtifact, match=re.escape("run.json")):
        started_run.load_run(run_manifest.run_id)


def test_a_missing_metrics_document_is_refused_once_a_result_exists(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id, status="completed", final_minute=30, event_count=3
        )
    )
    started_run.metrics_json_path(run_manifest.run_id).unlink()

    with pytest.raises(CorruptRunArtifact, match=re.escape("metrics.json")):
        started_run.load_run(run_manifest.run_id)


def test_a_missing_input_scenario_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.scenario_json_path(run_manifest.run_id).unlink()

    with pytest.raises(CorruptRunArtifact, match="scenario"):
        started_run.load_run(run_manifest.run_id)


def test_an_input_document_beyond_the_documented_bound_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """A file claiming to be a scenario is bounded before it is parsed.

    The refusal must name the BOUND. An ordinary parse failure also mentions the
    scenario, so a test that matched only that could not tell the bound from its
    absence - and the bound is the half that keeps the read from materialising 4 MiB
    of anything a file happens to hold.
    """
    started_run.scenario_json_path(run_manifest.run_id).write_bytes(
        b"x" * (MAX_INPUT_DOCUMENT_BYTES + 1)
    )

    with pytest.raises(CorruptRunArtifact, match="exceeds"):
        started_run.load_run(run_manifest.run_id)


def test_a_provider_usage_document_naming_another_run_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.provider_usage_json_path(run_manifest.run_id).write_bytes(
        canonical_json(ProviderUsageLog(run_id="run-other")).encode("utf-8")
    )

    with pytest.raises(CorruptRunArtifact, match="another run"):
        started_run.load_run(run_manifest.run_id)


def test_a_provider_usage_document_that_is_not_a_usage_log_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.provider_usage_json_path(run_manifest.run_id).write_bytes(b"{")

    with pytest.raises(CorruptRunArtifact, match=re.escape("provider-usage.json")):
        started_run.load_run(run_manifest.run_id)


def test_a_checkpoint_document_that_disagrees_with_its_own_row_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    checkpoint = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=1440,
        next_event_sequence=3,
        states=(consumer_state,),
    )
    started_run.save_checkpoint(checkpoint)
    forged = canonical_json(checkpoint.model_copy(update={"simulated_minute": 2880}))
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE checkpoints SET checkpoint_json = ?", (forged,))

    with pytest.raises(CorruptRunArtifact, match="disagrees"):
        started_run.load_run(run_manifest.run_id)


# --- a driver error is a persistence failure, and the family has no holes in it --------
#
# ``results.sqlite3`` is user-supplied data on the same footing as the files beside it, so
# a table can be missing from it, and a lock can outlast the busy timeout on a machine
# doing real work. Both arrived as a raw ``sqlite3.Error`` out of the store - neither a
# ``StorageError`` nor anything the command line maps to exit code 4, and reported as an
# unexpected defect rather than as the artifact failure it is. This is the escape finding
# I4 reported for a tampered metric row, one layer further out.


def test_a_database_that_cannot_answer_a_read_is_refused_as_a_typed_failure(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """A load reads four tables; a database missing one of them is a corrupt artifact."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DROP TABLE metrics")

    with pytest.raises(CorruptRunArtifact, match=re.escape("results.sqlite3")):
        started_run.load_run(run_manifest.run_id)


def test_streaming_from_a_database_that_cannot_answer_is_refused_as_a_typed_failure(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The streaming reader feeds replay and the live interface, and fails the same way."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DROP TABLE events")

    with pytest.raises(CorruptRunArtifact, match=re.escape("results.sqlite3")):
        list(started_run.iter_events(run_manifest.run_id))


def test_a_write_that_cannot_read_the_run_row_is_refused_as_a_typed_failure(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """Every writer asks the same question first: is this run open, and where is it?"""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DROP TABLE runs")

    with pytest.raises(StorageError, match=re.escape("results.sqlite3")):
        started_run.save_provider_usage(ProviderUsageLog(run_id=run_manifest.run_id))


def test_a_tick_whose_transaction_cannot_open_is_refused_as_a_typed_failure(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
    failing_connection: Callable[[int], None],
) -> None:
    """``BEGIN IMMEDIATE`` takes the write lock, so it is the statement that waits.

    A lock held past the busy timeout fails exactly here, outside the try that translates
    every other statement of the tick, and the run must not record it either way.
    """
    failing_connection(1)

    with pytest.raises(StorageError):
        started_run.append_events([event_factory(3, simulated_minute=45)])

    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2]
    assert started_run.events_jsonl_path(run_manifest.run_id).read_bytes().count(b"\n") == 3


def test_a_checkpoint_whose_transaction_cannot_open_is_refused_as_a_typed_failure(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    consumer_state: ConsumerState,
    failing_connection: Callable[[int], None],
) -> None:
    """The same statement, on the checkpoint writer, with the same obligation."""
    failing_connection(1)

    with pytest.raises(StorageError):
        started_run.save_checkpoint(
            RunCheckpoint(
                run_id=run_manifest.run_id,
                simulated_minute=1440,
                next_event_sequence=3,
                states=(consumer_state,),
            )
        )

    assert started_run.load_run(run_manifest.run_id).checkpoints == ()


def test_completing_a_run_whose_transaction_cannot_open_is_refused_as_a_typed_failure(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    failing_connection: Callable[[int], None],
) -> None:
    """And on the writer that closes the run: the schema read and the status read pass."""
    failing_connection(2)

    with pytest.raises(StorageError):
        started_run.complete_run(
            SimulationResult(
                run_id=run_manifest.run_id, status="completed", final_minute=30, event_count=3
            )
        )

    assert started_run.load_run(run_manifest.run_id).status == "running"


# --- every clause of the row-agreement checks, tripped one at a time -------------------
#
# A stored event is two copies of one record: the document, and the columns the store
# indexes it by. Four of the five clauses that hold those two to each other, and the
# checkpoint check's run clause, could each be deleted with the whole suite green - so an
# artifact edited behind the store's back in exactly those ways read back as a run that
# never happened. One test per clause, each tampering with one thing only.


def test_an_event_document_that_is_not_the_row_s_own_event_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The ``event_id`` column addresses the document; a row may not hold another's."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute(
            "UPDATE events SET event_id = ? WHERE sequence = 1",
            (f"{run_manifest.run_id}:event-00000009",),
        )

    with pytest.raises(CorruptRunArtifact, match="disagrees"):
        started_run.load_run(run_manifest.run_id)


def test_an_event_document_naming_another_run_is_refused(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A run directory that reads back another run's events is not this run's record."""
    forged = canonical_event_line(
        event_factory(
            1,
            simulated_minute=15,
            run_id="run-other",
            event_id=f"{run_manifest.run_id}:event-00000001",
        )
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE events SET event_json = ? WHERE sequence = 1", (forged,))

    with pytest.raises(CorruptRunArtifact, match="disagrees"):
        started_run.load_run(run_manifest.run_id)


def test_an_event_document_whose_sequence_is_not_the_row_s_is_refused(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The refusal must name the disagreement, not the gap it would look like.

    With this clause deleted the contiguity check downstream still refuses the run, but
    for the wrong reason and with the wrong report: it calls a complete stream gapped.
    """
    forged = canonical_event_line(
        event_factory(7, simulated_minute=15, event_id=f"{run_manifest.run_id}:event-00000001")
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE events SET event_json = ? WHERE sequence = 1", (forged,))

    with pytest.raises(CorruptRunArtifact, match="disagrees"):
        started_run.load_run(run_manifest.run_id)


def test_an_event_document_whose_type_is_not_the_row_s_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """``event_type`` is the column a report groups by; it must be the document's own."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute(
            "UPDATE events SET event_type = ? WHERE sequence = 1", (EventType.RUN_FAILED.value,)
        )

    with pytest.raises(CorruptRunArtifact, match="disagrees"):
        started_run.load_run(run_manifest.run_id)


def test_a_checkpoint_document_naming_another_run_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    """The checkpoint's other row-agreement clause: a resume must not restore another run."""
    checkpoint = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=1440,
        next_event_sequence=3,
        states=(consumer_state,),
    )
    started_run.save_checkpoint(checkpoint)
    forged = canonical_json(checkpoint.model_copy(update={"run_id": "run-other"}))
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE checkpoints SET checkpoint_json = ?", (forged,))

    with pytest.raises(CorruptRunArtifact, match="disagrees"):
        started_run.load_run(run_manifest.run_id)


def test_rows_that_do_not_describe_one_consistent_run_stay_inside_the_storage_family(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The last-resort translation, driven rather than assumed.

    Every check above it passes here: the manifest, the events, the checkpoints, the
    metrics and the export all agree with their own rows. ``created_at`` is handed
    straight from the column to :class:`StoredRun`, which is the only thing that
    validates it, so a tampered timestamp is what reaches the model - and without the
    translation a raw pydantic ``ValidationError`` escapes ``load_run``, outside the
    ``StorageError`` family the command line maps to exit code 4.
    """
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("UPDATE runs SET created_at = ?", ("the day before yesterday",))

    with pytest.raises(CorruptRunArtifact, match="single consistent run"):
        started_run.load_run(run_manifest.run_id)


def test_inputs_that_read_as_a_credential_are_refused_before_the_run_exists(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """Campaign YAML is untrusted, and the inputs directory is a permanent artifact."""
    campaign = valid_scenario.campaigns[0].model_copy(
        update={"message": "Quote code api_key=0000abcdef1234567890 at the fictional checkout."}
    )
    scenario = valid_scenario.model_copy(update={"campaigns": (campaign,)})
    manifest = run_manifest.model_copy(update={"scenario_hash": canonical_sha256(scenario)})

    with pytest.raises(StorageError, match="credential"):
        store.create_run(manifest, scenario=scenario)

    assert not (store.root / "runs" / manifest.run_id).exists()


def test_a_failure_reason_that_reads_as_a_credential_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with pytest.raises(StorageError, match="credential"):
        started_run.complete_run(
            SimulationResult(
                run_id=run_manifest.run_id,
                status="failed",
                final_minute=30,
                event_count=3,
                failure_reason="rejected api_key=0000abcdef1234567890",
            )
        )

    assert started_run.load_run(run_manifest.run_id).status == "running"


def test_a_tampered_metrics_document_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The metrics document and the stored result are two copies of one outcome."""
    started_run.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id,
            status="completed",
            final_minute=30,
            event_count=3,
            metrics={"notice_rate": 0.5},
        )
    )
    path = started_run.metrics_json_path(run_manifest.run_id)
    path.write_bytes(path.read_bytes().replace(b'"notice_rate":0.5', b'"notice_rate":0.9'))

    with pytest.raises(CorruptRunArtifact, match=re.escape("metrics.json")):
        started_run.load_run(run_manifest.run_id)


def test_a_tampered_input_scenario_whose_shape_is_still_valid_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """A scenario that still parses but is a different scenario must not replay as this one."""
    other = valid_scenario.model_copy(update={"days": 2})
    started_run.scenario_json_path(run_manifest.run_id).write_bytes(
        canonical_json(other).encode("utf-8")
    )

    with pytest.raises(CorruptRunArtifact, match="addresses"):
        started_run.load_run(run_manifest.run_id)


@pytest.mark.parametrize("reserved", ["con", "prn", "aux", "nul", "com1", "lpt1"])
def test_a_reserved_device_name_is_refused_as_a_run_identifier(
    store: SQLiteRunStore, reserved: str
) -> None:
    """These match the run-identifier shape but cannot be a directory on Windows."""
    with pytest.raises(UnsafeRunLocation, match="reserved device name"):
        store.run_directory(reserved)


@pytest.mark.parametrize(
    "hostile",
    ["..", "../escape", "runs/../../escape", "a/b", "a\\b", "/absolute", "C:\\Windows", "", "RUN"],
)
def test_a_run_identifier_that_is_not_one_is_refused_before_a_path_is_built(
    store: SQLiteRunStore, hostile: str
) -> None:
    with pytest.raises(UnsafeRunLocation, match="not a run identifier"):
        store.run_directory(hostile)


# --- the two artifacts of one tick, and what happens when only one of them lands -------
#
# The database is authoritative and the export is derived from it, so the failure that
# matters is a committed tick whose export line never reached the device. These pin the
# three properties that makes survivable: the failure is TYPED, the divergence never
# widens, and the derived artifact can be re-derived.


def test_a_failing_export_append_is_refused_as_a_typed_storage_error(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full device on events.jsonl is a persistence failure, not an untyped crash."""

    def refuse(path: Path, text: str) -> None:
        raise OSError(28, "No space left on device", str(path))

    monkeypatch.setattr(sqlite_store_module, "append_export_lines", refuse)

    with pytest.raises(StorageError) as raised:
        started_run.append_events([event_factory(3, simulated_minute=45)])

    assert raised.value.__cause__ is None
    assert "No space left" not in str(raised.value)


def test_a_tick_the_export_could_not_take_is_reported_as_its_own_typed_failure(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal means the tick was not recorded - on every path but this one.

    The database has already committed when the export append fails, so the general
    postcondition is FALSE here and no amount of docstring makes it true. A caller that
    cannot tell the two cases apart either retries a tick that is already stored - and is
    told its sequence is taken - or abandons one that is not. The distinction is therefore
    carried by the type, where a caller can branch on it.
    """

    def refuse(path: Path, text: str) -> None:
        raise OSError("the device is full")

    monkeypatch.setattr(sqlite_store_module, "append_export_lines", refuse)

    with pytest.raises(ExportNotExtended) as raised:
        started_run.append_events([event_factory(3, simulated_minute=45)])

    assert isinstance(raised.value, StorageError)
    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2, 3]
    assert started_run.events_jsonl_path(run_manifest.run_id).read_bytes().count(b"\n") == 3


def test_every_other_refusal_of_a_tick_keeps_the_postcondition_it_documents(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The typed distinction is only worth carrying if the ordinary refusal is the other one."""
    with pytest.raises(StorageError) as raised:
        started_run.append_events([event_factory(3, model_id=CREDENTIAL_MODEL_ID)])

    assert not isinstance(raised.value, ExportNotExtended)
    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2]


def test_a_committed_tick_whose_export_line_was_lost_never_widens_the_divergence(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The crash case the brief names: a kill between the commit and the export fsync."""

    def refuse(path: Path, text: str) -> None:
        raise OSError("the device is full")

    monkeypatch.setattr(sqlite_store_module, "append_export_lines", refuse)
    with pytest.raises(StorageError):
        started_run.append_events([event_factory(3, simulated_minute=45)])
    monkeypatch.undo()

    with pytest.raises(CorruptRunArtifact, match="lines"):
        started_run.append_events([event_factory(4, simulated_minute=60)])

    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2, 3]


def test_an_export_lost_to_a_failed_append_is_rebuilt_from_the_database(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite is the durable store; the export is a derived artifact, so it is derivable."""

    def refuse(path: Path, text: str) -> None:
        raise OSError("the device is full")

    monkeypatch.setattr(sqlite_store_module, "append_export_lines", refuse)
    with pytest.raises(StorageError):
        started_run.append_events([event_factory(3, simulated_minute=45)])
    monkeypatch.undo()
    with pytest.raises(CorruptRunArtifact, match="lines"):
        started_run.load_run(run_manifest.run_id)

    assert started_run.rebuild_export(run_manifest.run_id) == 4

    loaded = started_run.load_run(run_manifest.run_id)
    assert [event.sequence for event in loaded.events] == [0, 1, 2, 3]
    started_run.append_events([event_factory(4, simulated_minute=60)])
    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2, 3, 4]


def test_a_rebuilt_export_is_byte_identical_to_the_one_the_writer_would_have_left(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """Re-deriving must reproduce the artifact, not merely something that parses."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    expected = path.read_bytes()
    path.write_bytes(b"")

    assert started_run.rebuild_export(run_manifest.run_id) == 3

    assert path.read_bytes() == expected


def test_a_failing_export_rebuild_is_refused_as_a_typed_storage_error(
    started_run: SQLiteRunStore, run_manifest: RunManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repair that fails must leave the previous export and a typed failure behind."""

    def refuse(source: object, target: object) -> None:
        raise OSError("the device is full")

    monkeypatch.setattr(sqlite_store_module.os, "replace", refuse)
    path = started_run.events_jsonl_path(run_manifest.run_id)
    before = path.read_bytes()

    with pytest.raises(StorageError) as raised:
        started_run.rebuild_export(run_manifest.run_id)

    assert raised.value.__cause__ is None
    assert path.read_bytes() == before
    directory = started_run.run_directory(run_manifest.run_id)
    assert [item.name for item in directory.iterdir() if item.name.endswith(".tmp")] == []


def test_rebuilding_an_export_from_a_gapped_database_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """A re-derive is only honest if the rows it derives from are themselves a run."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    before = path.read_bytes()
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DELETE FROM events WHERE sequence = 1")

    with pytest.raises(CorruptRunArtifact, match="contiguous"):
        started_run.rebuild_export(run_manifest.run_id)

    assert path.read_bytes() == before


# --- the two write paths that had no screen -------------------------------------------
#
# ``daily_reflection`` is provider paraphrase and carries no screen anywhere else in the
# repository, and ``ProviderUsage.model_id`` is free text a configuration supplies. Both
# reach a permanent artifact, so both are screened where they are written.

CREDENTIAL_TOKEN = "0000abcdef1234567890"
CREDENTIAL_TEXT = f"remember api_key={CREDENTIAL_TOKEN} for the fictional checkout"


def test_a_checkpoint_whose_reflection_reads_as_a_credential_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    """The same string is refused in an event payload; a checkpoint is no different."""
    checkpoint = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=1440,
        next_event_sequence=3,
        states=(consumer_state.model_copy(update={"daily_reflection": CREDENTIAL_TEXT}),),
    )

    with pytest.raises(StorageError, match="credential"):
        started_run.save_checkpoint(checkpoint)

    assert files_carrying(started_run, run_manifest.run_id, CREDENTIAL_TOKEN) == []
    assert started_run.load_run(run_manifest.run_id).checkpoints == ()


def test_a_checkpoint_memory_summary_that_reads_as_a_credential_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    """The screen walks the whole document, not one named field of it."""
    checkpoint = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=1440,
        next_event_sequence=3,
        states=(
            consumer_state.model_copy(
                update={
                    "memories": (
                        Memory(
                            memory_id="memory-0001",
                            created_minute=15,
                            kind="advertising",
                            summary=CREDENTIAL_TEXT,
                            salience=0.5,
                            caused_by_event_ids=("run-storage:event-00000001",),
                        ),
                    )
                }
            ),
        ),
    )

    with pytest.raises(StorageError, match="credential"):
        started_run.save_checkpoint(checkpoint)

    assert files_carrying(started_run, run_manifest.run_id, CREDENTIAL_TOKEN) == []


def test_provider_usage_whose_model_id_reads_as_a_credential_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """A model identifier is free configuration text on its way to a permanent document."""
    log = ProviderUsageLog(
        run_id=run_manifest.run_id,
        records=(
            ProviderUsage(
                provider_kind="mock",
                model_id=f"api_key={CREDENTIAL_TOKEN}",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
            ),
        ),
    )

    with pytest.raises(StorageError, match="credential"):
        started_run.save_provider_usage(log)

    assert files_carrying(started_run, run_manifest.run_id, CREDENTIAL_TOKEN) == []


CREDENTIAL_MODEL_ID = f"local-llama-3-api_key={CREDENTIAL_TOKEN}"
CREDENTIAL_CHANNEL = f"mobile-feed-api_key={CREDENTIAL_TOKEN}"


def test_an_event_model_identifier_that_reads_as_a_credential_never_reaches_an_artifact(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """``DomainEvent.model_id`` is the same free configuration text as the usage log's.

    It is 120 characters of anything, it is written verbatim into BOTH ``events.jsonl``
    and ``results.sqlite3``, and those are permanent, portable artifacts. Screening the
    payload alone left this field open on the authoritative write path while the
    identically shaped ``ProviderUsage.model_id`` was screened in the same module.
    """
    event = event_factory(3, simulated_minute=45, model_id=CREDENTIAL_MODEL_ID)

    with contextlib.suppress(InvalidEventBatch):
        started_run.append_events([event])

    assert files_carrying(started_run, run_manifest.run_id, CREDENTIAL_TOKEN) == []
    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2]
    with pytest.raises(InvalidEventBatch, match="credential"):
        started_run.append_events([event])


def test_an_event_channel_that_reads_as_a_credential_never_reaches_an_artifact(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """``channel`` is the other free-text field of an event: 80 characters of anything."""
    event = event_factory(3, simulated_minute=45, channel=CREDENTIAL_CHANNEL)

    with contextlib.suppress(InvalidEventBatch):
        started_run.append_events([event])

    assert files_carrying(started_run, run_manifest.run_id, CREDENTIAL_TOKEN) == []
    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2]
    with pytest.raises(InvalidEventBatch, match="credential"):
        started_run.append_events([event])


def test_an_ordinary_event_channel_and_model_identifier_are_still_accepted(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The widened screen must not refuse the configuration this simulator really runs."""
    started_run.append_events(
        [
            event_factory(
                3,
                simulated_minute=45,
                event_type=EventType.COGNITION_COMPLETED,
                campaign_id="campaign-phone",
                channel="mobile-feed",
                model_id="qwen2.5:7b-instruct",
            )
        ]
    )

    loaded = started_run.load_run(run_manifest.run_id)
    assert loaded.events[3].model_id == "qwen2.5:7b-instruct"
    assert loaded.events[3].channel == "mobile-feed"


# --- bounds, on the way in as well as on the way out ----------------------------------


def test_an_event_too_large_to_read_back_is_refused_before_it_is_written(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A writer that can create a document its own reader refuses corrupts at write time."""
    oversized = event_factory(
        3, simulated_minute=45, payload={"note": "x" * (MAX_EVENT_LINE_CHARS + 1)}
    )

    with pytest.raises(InvalidEventBatch, match="characters"):
        started_run.append_events([oversized])

    assert stored_sequences(started_run, run_manifest.run_id) == [0, 1, 2]
    assert len(started_run.load_run(run_manifest.run_id).events) == 3


def test_a_stored_event_document_beyond_the_line_bound_is_refused_as_too_large(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The bound covers a tampered database blob, and says so rather than 'not an event'."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute(
            "UPDATE events SET event_json = ? WHERE sequence = 1",
            ("x" * (MAX_EVENT_LINE_CHARS + 1),),
        )

    with pytest.raises(CorruptRunArtifact, match="exceeds"):
        started_run.load_run(run_manifest.run_id)


def test_the_export_is_read_in_bounded_chunks_rather_than_materialised(tmp_path: Path) -> None:
    """The chunk size is what makes the export's line bound a bound on the READ.

    Nothing named this number, and the docstring beside it claims a property the number
    is the whole of: at ten times its documented value the peak doubles, and at a
    thousand times the entire file is pulled into memory in one read and no refusal
    happens at all - the oversized line is yielded as though it were an event.
    """
    assert READ_CHUNK_CHARS == 65_536
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"x" * (8 * 1024 * 1024) + b"\n")

    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="exceeds"):
            list(iter_export_lines(path))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < 2 * MAX_EVENT_LINE_CHARS


def test_a_stored_manifest_beyond_the_document_bound_is_refused_unmaterialised(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """results.sqlite3 is user-supplied data; a blob in it is bounded as a file would be."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute(
            "UPDATE runs SET manifest_json = ?", ("x" * (8 * MAX_STORED_DOCUMENT_CHARS),)
        )

    tracemalloc.start()
    try:
        with pytest.raises(CorruptRunArtifact, match="exceeds"):
            started_run.load_run(run_manifest.run_id)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < 4 * MAX_STORED_DOCUMENT_CHARS


def test_a_stored_result_beyond_the_document_bound_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute(
            "UPDATE runs SET status = ?, result_json = ?",
            ("completed", "x" * (MAX_STORED_DOCUMENT_CHARS + 1)),
        )

    with pytest.raises(CorruptRunArtifact, match="exceeds"):
        started_run.load_run(run_manifest.run_id)


def test_a_stored_checkpoint_beyond_the_document_bound_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    started_run.save_checkpoint(
        RunCheckpoint(
            run_id=run_manifest.run_id,
            simulated_minute=1440,
            next_event_sequence=3,
            states=(consumer_state,),
        )
    )
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute(
            "UPDATE checkpoints SET checkpoint_json = ?",
            ("x" * (MAX_STORED_DOCUMENT_CHARS + 1),),
        )

    with pytest.raises(CorruptRunArtifact, match="exceeds"):
        started_run.load_run(run_manifest.run_id)


def test_a_run_at_the_documented_checkpoint_bound_is_still_read(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    """Eight is one checkpoint per day boundary of the longest run, plus its start.

    A bound that refused the largest legitimate run would be the wrong bound, so the read
    has to admit exactly ``MAX_CHECKPOINTS`` before the test below asks it to refuse one
    more.
    """
    for day in range(MAX_CHECKPOINTS):
        started_run.save_checkpoint(
            RunCheckpoint(
                run_id=run_manifest.run_id,
                simulated_minute=day * MINUTES_PER_DAY,
                next_event_sequence=3,
                states=(consumer_state,),
            )
        )

    loaded = started_run.load_run(run_manifest.run_id)

    assert len(loaded.checkpoints) == MAX_CHECKPOINTS


def test_more_checkpoints_than_a_run_can_hold_are_refused_unmaterialised(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """``MAX_CHECKPOINTS`` on the model bounds the VALUE; the READ has to be bounded too.

    A ``max_length`` on :class:`StoredRun` refuses the ninth checkpoint only after every
    row has been pulled out of the database and every document materialised - each one up
    to ``MAX_STORED_DOCUMENT_CHARS`` - so a tampered table of a thousand rows is read in
    full and rejected afterwards. The bound belongs in the query, one row past itself, so
    the extra row is what the refusal is built from rather than the whole table.
    """
    blob = "x" * (256 * 1024)
    with raw(started_run, run_manifest.run_id) as connection:
        connection.executemany(
            "INSERT INTO checkpoints (run_id, simulated_minute, checkpoint_json) VALUES (?, ?, ?)",
            [(run_manifest.run_id, minute, blob) for minute in range(64)],
        )

    tracemalloc.start()
    try:
        with pytest.raises(CorruptRunArtifact, match=f"more than the {MAX_CHECKPOINTS}"):
            started_run.load_run(run_manifest.run_id)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < (MAX_CHECKPOINTS + 8) * len(blob)


# --- deterministic ordering and the DDL constraints, proved by effect ------------------


def reshuffle_rows(store: SQLiteRunStore, run_id: str, table: str, columns: str) -> None:
    """Rewrite one table's rows in reverse physical order, leaving the data identical."""
    with raw(store, run_id) as connection:
        rows = connection.execute(f"SELECT {columns} FROM {table}").fetchall()
        connection.execute(f"DELETE FROM {table}")
        placeholders = ", ".join("?" for _ in columns.split(","))
        connection.executemany(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(reversed(rows))
        )


EVENT_COLUMNS = (
    "event_id, run_id, sequence, simulated_minute, event_type, event_json, export_offset"
)


def test_streaming_returns_events_in_sequence_order_whatever_their_physical_order(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """The streaming reader feeds replay and the live interface; rowid order is not order."""
    reshuffle_rows(started_run, run_manifest.run_id, "events", EVENT_COLUMNS)

    streamed = list(started_run.iter_events(run_manifest.run_id))

    assert [event.sequence for event in streamed] == [0, 1, 2]


def test_checkpoints_are_returned_in_minute_order_whatever_their_physical_order(
    started_run: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    for minute in (1440, 2880):
        started_run.save_checkpoint(
            RunCheckpoint(
                run_id=run_manifest.run_id,
                simulated_minute=minute,
                next_event_sequence=3,
                states=(consumer_state,),
            )
        )
    reshuffle_rows(
        started_run, run_manifest.run_id, "checkpoints", "run_id, simulated_minute, checkpoint_json"
    )

    loaded = started_run.load_run(run_manifest.run_id)

    assert [checkpoint.simulated_minute for checkpoint in loaded.checkpoints] == [1440, 2880]


MULTI_ROW_TABLES = ("events", "checkpoints", "metrics")
"""The three tables that can answer one run with more than one row."""

AGGREGATES = ("COUNT(", "MAX(")


def sql_literals(module: object) -> list[str]:
    """Every SQL string the module hands to a cursor, as the cursor receives it."""
    source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"execute", "executemany"} or not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            found.append(argument.value)
    return found


def test_every_select_that_can_return_two_rows_orders_them_explicitly() -> None:
    """The binding rule is that no result may depend on rowid or insertion order.

    An effect test can only show that the rows came back sorted, and they do come back
    sorted with the ORDER BY deleted: SQLite happens to answer these predicates from an
    index whose order is the one we want. That is the query planner's choice, not a
    promise, and it changes with a schema change, an ANALYZE, or a different build. So
    the clause is asserted where it actually lives - in the statement text - and the
    round-trip tests beside this one keep the ORDER BY COLUMN honest.
    """
    offenders = [
        statement
        for statement in sql_literals(sqlite_store_module)
        if statement.upper().startswith("SELECT")
        and any(f"FROM {table}" in statement for table in MULTI_ROW_TABLES)
        and not any(aggregate in statement.upper() for aggregate in AGGREGATES)
        and "ORDER BY" not in statement.upper()
    ]

    assert offenders == []


def test_the_guard_above_sees_the_statements_it_claims_to_guard() -> None:
    """A structural guard that matched nothing would pass on an empty repository."""
    ordered = [
        statement
        for statement in sql_literals(sqlite_store_module)
        if statement.upper().startswith("SELECT") and "ORDER BY" in statement.upper()
    ]

    assert len(ordered) == 4


def test_streaming_refuses_a_gap_as_loudly_as_loading_does(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """A replay driven from the streaming reader must not silently skip a lost event."""
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DELETE FROM events WHERE sequence = 1")

    with pytest.raises(CorruptRunArtifact, match="contiguous"):
        list(started_run.iter_events(run_manifest.run_id))


def test_streaming_from_a_sequence_refuses_a_gap_at_its_own_starting_point(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with raw(started_run, run_manifest.run_id) as connection:
        connection.execute("DELETE FROM events WHERE sequence = 1")

    with pytest.raises(CorruptRunArtifact, match="contiguous"):
        list(started_run.iter_events(run_manifest.run_id, start_sequence=1))


def test_the_schema_refuses_a_second_row_for_one_event_identifier(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """``event_id TEXT PRIMARY KEY`` is the last line of defence under the port's rules."""
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO events ({EVENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "run-storage:event-00000000",
                    run_manifest.run_id,
                    9,
                    0,
                    "state.updated",
                    "{}",
                    9_999,
                ),
            )
    finally:
        connection.close()


def test_the_schema_refuses_a_second_row_for_one_run_and_sequence(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """``UNIQUE(run_id, sequence)`` is what makes the contiguity read single-valued."""
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO events ({EVENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "run-storage:event-00000099",
                    run_manifest.run_id,
                    0,
                    0,
                    "state.updated",
                    "{}",
                    9_999,
                ),
            )
    finally:
        connection.close()
