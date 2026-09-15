"""What is specific to the SQLite adapter: its schema, its pragmas, and its refusals.

The shared behaviour lives in :mod:`tests.contract.test_run_store`. Here the artifact is
attacked directly - the database file is overwritten, rows are edited behind the store's
back, the schema version is moved, the export is truncated - because the whole point of a
durable artifact is that it either reads back as what was written or refuses to read back
at all. "Reads back without complaining" is the failure mode this module exists to catch.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from adlife.adapters.storage.schema import (
    SCHEMA_VERSION,
    TABLE_NAMES,
    connect_to_database,
)
from adlife.adapters.storage.sqlite_store import MAX_INPUT_DOCUMENT_BYTES, SQLiteRunStore
from adlife.core.domain.events import DomainEvent, EventType
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.serialization import canonical_event_line, canonical_json
from adlife.core.domain.state import ConsumerState
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
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


def test_foreign_keys_are_enforced_by_the_connection_the_store_opens(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """``PRAGMA foreign_keys`` is a silent no-op inside a transaction, so test the effect."""
    connection = connect_to_database(started_run.database_path(run_manifest.run_id))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO events (event_id, run_id, sequence, simulated_minute, "
                "event_type, event_json) VALUES (?, ?, ?, ?, ?, ?)",
                ("x:event-00000000", "run-unknown", 0, 0, "run.started", "{}"),
            )
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

    assert busy_timeout >= 5000


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
            "SELECT event_id, run_id, sequence, simulated_minute, event_type, event_json "
            "FROM events ORDER BY sequence"
        ).fetchall()
        connection.execute("DELETE FROM events")
        connection.executemany(
            "INSERT INTO events (event_id, run_id, sequence, simulated_minute, event_type, "
            "event_json) VALUES (?, ?, ?, ?, ?, ?)",
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
    """A half-written last line must never read back as a valid run."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: len(text) - 20], encoding="utf-8")

    with pytest.raises(CorruptRunArtifact, match=re.escape("events.jsonl")):
        started_run.load_run(run_manifest.run_id)


def test_an_export_missing_a_line_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    path = started_run.events_jsonl_path(run_manifest.run_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")

    with pytest.raises(CorruptRunArtifact, match=re.escape("events.jsonl")):
        started_run.load_run(run_manifest.run_id)


def test_an_export_line_that_disagrees_with_the_database_is_refused(
    started_run: SQLiteRunStore,
    run_manifest: RunManifest,
    event_factory: Callable[..., DomainEvent],
) -> None:
    path = started_run.events_jsonl_path(run_manifest.run_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = canonical_event_line(event_factory(1, simulated_minute=777))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(CorruptRunArtifact, match=re.escape("events.jsonl")):
        started_run.load_run(run_manifest.run_id)


def test_an_export_whose_last_line_has_no_newline_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    """An append interrupted by a kill leaves exactly this shape."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))

    with pytest.raises(CorruptRunArtifact, match=re.escape("events.jsonl")):
        started_run.load_run(run_manifest.run_id)


def test_an_export_line_beyond_the_documented_bound_is_refused_without_reading_it_all(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    path = started_run.events_jsonl_path(run_manifest.run_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("x" * 2_000_000 + "\n")

    with pytest.raises(CorruptRunArtifact, match="exceeds"):
        started_run.load_run(run_manifest.run_id)


def test_a_tampered_manifest_document_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    path = started_run.run_json_path(run_manifest.run_id)
    path.write_text(path.read_text(encoding="utf-8").replace('"seed":42', '"seed":43'), "utf-8")

    with pytest.raises(CorruptRunArtifact, match=re.escape("run.json")):
        started_run.load_run(run_manifest.run_id)


def test_a_tampered_input_scenario_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    path = started_run.scenario_json_path(run_manifest.run_id)
    path.write_text(
        path.read_text(encoding="utf-8").replace("Contract fixture", "Tampered fixture"),
        encoding="utf-8",
    )

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


def test_metrics_are_stored_one_row_per_metric_in_a_stable_order(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
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
            "SELECT metric_name, metric_value FROM metrics ORDER BY metric_name"
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
    """The export and the database must agree before another tick is appended to either."""
    path = started_run.events_jsonl_path(run_manifest.run_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    reopened = SQLiteRunStore(started_run.root)

    with pytest.raises(CorruptRunArtifact, match="lines"):
        reopened.append_events([event_factory(3, simulated_minute=45)])


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
    """A file claiming to be a scenario is bounded before it is parsed."""
    started_run.scenario_json_path(run_manifest.run_id).write_text(
        "x" * (MAX_INPUT_DOCUMENT_BYTES + 1), encoding="utf-8"
    )

    with pytest.raises(CorruptRunArtifact, match="scenario"):
        started_run.load_run(run_manifest.run_id)


def test_a_provider_usage_document_naming_another_run_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.provider_usage_json_path(run_manifest.run_id).write_text(
        canonical_json(ProviderUsageLog(run_id="run-other")), encoding="utf-8"
    )

    with pytest.raises(CorruptRunArtifact, match="another run"):
        started_run.load_run(run_manifest.run_id)


def test_a_provider_usage_document_that_is_not_a_usage_log_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    started_run.provider_usage_json_path(run_manifest.run_id).write_text("{", encoding="utf-8")

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
    path.write_text(
        path.read_text(encoding="utf-8").replace('"notice_rate":0.5', '"notice_rate":0.9'),
        encoding="utf-8",
    )

    with pytest.raises(CorruptRunArtifact, match=re.escape("metrics.json")):
        started_run.load_run(run_manifest.run_id)


def test_a_tampered_input_scenario_whose_shape_is_still_valid_is_refused(
    started_run: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """A scenario that still parses but is a different scenario must not replay as this one."""
    other = valid_scenario.model_copy(update={"days": 2})
    started_run.scenario_json_path(run_manifest.run_id).write_text(
        canonical_json(other), encoding="utf-8"
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
