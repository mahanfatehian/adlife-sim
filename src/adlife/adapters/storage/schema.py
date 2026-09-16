"""The versioned SQLite schema and the connection settings a run artifact depends on.

DURABILITY, STATED PLAINLY. The connection runs in write-ahead logging mode with
``synchronous = NORMAL``. That combination cannot corrupt the database: a crashed process,
or a killed one, leaves either the previous committed state or the fully committed tick,
and the write-ahead log is replayed on the next open. What it does NOT promise is that the
most recently committed ticks survive a power loss or an operating-system crash, because a
commit is not fsynced to the platter. This is the documented trade the plan asks for, and
it is the right one here: a simulation run is reproducible from its seed and its inputs, so
the cost of losing the last few ticks of an interrupted run is bounded, while fsyncing
every one of a 7-day run's 672 ticks is not.

WAL also buys the property the live interface needs: a reader can read a run while the
engine writes it, without either blocking the other.

Foreign keys are enabled per connection - SQLite defaults them OFF, and ``PRAGMA
foreign_keys`` is silently ignored inside a transaction - so they are set here, on a fresh
connection, before anything begins. The tests assert the EFFECT rather than the pragma.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2
"""Bump this when the stored shape changes. An artifact at any other version is refused.

Version 2 adds ``events.export_offset``: the byte length ``events.jsonl`` reaches once
that event's line has been appended. It is what lets the divergence guard compare the
export against the database in constant time instead of streaming the whole file on
every tick - a cost that grows with the run and, on a maximum run, exceeds the entire
performance budget specification section 20 gives the run.
"""

BUSY_TIMEOUT_MS = 5_000
"""Wait for a concurrent writer rather than failing the tick immediately.

This is applied in ONE place, the ``busy_timeout`` pragma below. ``sqlite3.connect``
accepts a ``timeout`` argument that sets the same thing, and passing both left the pragma
provably dead: its default is five seconds, so a connection that never ran the pragma
reported exactly the configured value and no test could tell the two apart.
"""

TABLE_NAMES: tuple[str, ...] = ("schema_meta", "runs", "events", "checkpoints", "metrics")

SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE schema_meta (version INTEGER PRIMARY KEY)",
    """
    CREATE TABLE runs (
      run_id TEXT PRIMARY KEY,
      status TEXT NOT NULL,
      manifest_json TEXT NOT NULL,
      result_json TEXT,
      created_at TEXT NOT NULL,
      completed_at TEXT
    )
    """,
    """
    CREATE TABLE events (
      event_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      sequence INTEGER NOT NULL,
      simulated_minute INTEGER NOT NULL,
      event_type TEXT NOT NULL,
      event_json TEXT NOT NULL,
      export_offset INTEGER NOT NULL,
      UNIQUE(run_id, sequence)
    )
    """,
    """
    CREATE TABLE checkpoints (
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      simulated_minute INTEGER NOT NULL,
      checkpoint_json TEXT NOT NULL,
      PRIMARY KEY(run_id, simulated_minute)
    )
    """,
    """
    CREATE TABLE metrics (
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      metric_name TEXT NOT NULL,
      metric_value REAL NOT NULL,
      PRIMARY KEY(run_id, metric_name)
    )
    """,
    "CREATE INDEX events_by_minute ON events(run_id, simulated_minute, sequence)",
    "CREATE INDEX events_by_export_offset ON events(run_id, export_offset)",
)
"""The brief's data definition, plus ``runs.result_json`` and ``events.export_offset``.

The result carries ``final_minute``, ``failure_reason`` and the metric map, and none of
those has a column. Without it a completed run could not be read back as the
:class:`~adlife.core.domain.results.SimulationResult` it finished with, and replay of a
FAILED run - which the task requires to be reproducible byte for byte - would lose the
reason it failed. The ``metrics`` table stays exactly as specified: it is the queryable
projection, and the store checks the two against each other on every load.

``events.export_offset`` records where each event's line ENDS in ``events.jsonl``. It is
derived data - the running total of the canonical lines the same rows hold - and it is
written inside the same transaction as the row it describes, so it cannot drift from it.
Its index exists for one query: ``MAX(export_offset)`` for one run, which is how the
divergence guard learns how long the export should be without opening it.
"""


def connect_to_database(path: Path) -> sqlite3.Connection:
    """Open one connection with the documented pragmas already applied.

    ``isolation_level=None`` turns off the driver's implicit transaction handling so the
    store can own transaction boundaries explicitly: one ``BEGIN IMMEDIATE`` ... ``COMMIT``
    per tick, and nothing started behind its back.

    The busy timeout is set by the pragma and nowhere else, so the pragma is the line a
    test can hold responsible for it; see :data:`BUSY_TIMEOUT_MS`. Nothing contends for a
    lock before it runs - the connection takes no lock until the first statement of the
    first transaction, which is several lines below.
    """
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL").fetchone()
        connection.execute("PRAGMA synchronous = NORMAL")
    except BaseException:
        connection.close()
        raise
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Create every table at :data:`SCHEMA_VERSION` inside one transaction."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
        connection.execute("COMMIT")
    except BaseException:
        connection.rollback()
        raise


def read_schema_version(connection: sqlite3.Connection) -> int:
    """Return the artifact's schema version, or raise ``ValueError`` if it has none.

    The caller translates. This function deliberately reports only the version it found:
    a stored artifact is user-supplied data and its contents do not belong in a message.
    """
    rows = connection.execute("SELECT version FROM schema_meta").fetchall()
    if len(rows) != 1:
        raise ValueError(f"expected exactly one schema version row, found {len(rows)}")
    version = rows[0][0]
    if not isinstance(version, int):
        raise ValueError("the stored schema version is not an integer")
    return version


__all__ = [
    "BUSY_TIMEOUT_MS",
    "SCHEMA_STATEMENTS",
    "SCHEMA_VERSION",
    "TABLE_NAMES",
    "connect_to_database",
    "create_schema",
    "read_schema_version",
]
