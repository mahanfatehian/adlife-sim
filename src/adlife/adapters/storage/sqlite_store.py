"""The local SQLite run store and the run directory it owns.

This adapter is the authoritative, replayable record of a run. Everything it does follows
from two rules.

ATOMIC. A directory is built as a hidden sibling and moved into place in one step, so a
run directory is never observed half-written. A document is written to a sibling file and
moved over its target, so a reader sees the previous version or the new one. A tick is one
``BEGIN IMMEDIATE`` ... ``COMMIT``, so a killed process leaves the tick either wholly
recorded or wholly absent. The portable export is appended only AFTER the database commit,
so a database failure can never leave a line in the export that no row backs, and it is
COUNTED and checked against the database's own next sequence BEFORE the transaction opens,
so a disagreement between the two artifacts refuses the tick instead of recording it and
reporting the refusal afterwards. A caller that sees a failure from
:meth:`SQLiteRunStore.append_events` knows the tick was not stored.

What two files cannot be is atomic TOGETHER. A kill or a full device between the commit
and the export's fsync leaves a committed tick whose exported line never landed. SQLite is
the durable store and the export is derived from it, so that state is refused on the next
append and on every load, and :meth:`SQLiteRunStore.rebuild_export` re-derives the export
from the rows that back it rather than leaving the run unreadable for the rest of its life.

REFUSED, NOT REPAIRED. Every read verifies what it reads: the schema version, the manifest
against ``run.json``, the stored inputs against the digest the manifest claims, each event
document against its own indexed columns, the export against the database line for line,
the metrics table against the stored result, and the status against the result it carries.
A corrupt, truncated, tampered or version-mismatched artifact raises
:class:`~adlife.core.ports.run_store.CorruptRunArtifact`; none of it is guessed at.

Failures are translated with ``from None`` throughout, on the discipline Task 10 settled
at the cognition boundary: a chained ``__cause__`` is printed by every formatted traceback,
and the exceptions translated here were built from campaign text, provider paraphrase and
stored documents. The message names the run and the artifact; the content stays on disk.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from pydantic import ValidationError

from adlife.adapters.storage.schema import (
    SCHEMA_VERSION,
    connect_to_database,
    create_schema,
    read_schema_version,
)
from adlife.config.paths import resolve_project_path
from adlife.core.domain.events import DomainEvent
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.serialization import (
    MAX_EVENT_LINE_CHARS,
    EventLineTooLong,
    canonical_event_line,
    canonical_json,
    parse_event_line,
    persisted_text_objection,
)
from adlife.core.ports.run_store import (
    MAX_LOADED_EVENTS,
    CorruptRunArtifact,
    DuplicateRun,
    InvalidEventBatch,
    ProviderUsageLog,
    RunAlreadyComplete,
    RunCheckpoint,
    RunNotFound,
    RunTooLargeToLoad,
    SchemaVersionMismatch,
    StorageError,
    StoredRun,
    UnsafeRunLocation,
    batch_run_id,
    validate_event_batch,
    validate_run_id,
)
from adlife.core.simulation.engine import canonical_sha256

RUNS_DIRECTORY = "runs"
RUN_DOCUMENT = "run.json"
INPUTS_DIRECTORY = "inputs"
SCENARIO_DOCUMENT = "scenario.json"
EVENTS_EXPORT = "events.jsonl"
DATABASE_FILE = "results.sqlite3"
METRICS_DOCUMENT = "metrics.json"
PROVIDER_USAGE_DOCUMENT = "provider-usage.json"

READ_CHUNK_CHARS = 65_536
MAX_INPUT_DOCUMENT_BYTES = 4_194_304
"""A scenario is bounded by the domain model; a file claiming to be one is bounded here."""

MAX_STORED_DOCUMENT_CHARS = 4_194_304
"""The same bound for a manifest, result or checkpoint blob read back out of the database.

``results.sqlite3`` is user-supplied data on exactly the same footing as the files beside
it, so the three columns that hold a whole document are read through ``substr`` and the
oversized case is refused on the truncation rather than after the whole blob has been
pulled into memory.
"""

VALID_STATUSES: frozenset[str] = frozenset({"running", "completed", "failed", "interrupted"})

_TEMPORARY_SUFFIXES = count()


def utc_now_iso() -> str:
    """Wall-clock metadata, kept strictly separate from simulated minutes."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def publish_directory(temporary: Path, final: Path) -> None:
    """Move a fully built directory into place in one filesystem operation."""
    os.replace(temporary, final)


def write_document_atomically(path: Path, text: str) -> None:
    """Replace one document without ever leaving a partially written file behind."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{next(_TEMPORARY_SUFFIXES)}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_export_lines(path: Path, text: str) -> None:
    """Append one committed tick to the portable export and push it to the device."""
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def iter_export_lines(path: Path) -> Iterator[str]:
    """Yield the export's lines without ever holding more than one bounded line.

    The file is read in fixed chunks, so a corrupt export carrying one enormous "line"
    is refused after :data:`MAX_EVENT_LINE_CHARS` characters rather than after all of
    them. A file whose last line has no newline - the exact shape an append interrupted
    by a kill leaves - is refused rather than treated as a complete record.
    """
    buffer = ""
    with path.open("r", encoding="utf-8", newline="\n") as handle:
        while True:
            chunk = handle.read(READ_CHUNK_CHARS)
            if not chunk:
                break
            buffer += chunk
            while (break_at := buffer.find("\n")) >= 0:
                yield buffer[:break_at]
                buffer = buffer[break_at + 1 :]
            if len(buffer) > MAX_EVENT_LINE_CHARS:
                raise ValueError(f"an export line exceeds {MAX_EVENT_LINE_CHARS} characters")
    if buffer:
        raise ValueError("the export ends with an unterminated line")


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    """End an open transaction explicitly rather than by implication.

    Every writer here closes its connection in a ``finally``, and closing a sqlite3
    connection rolls back an open transaction, so this is EXPLICITNESS rather than the
    load-bearing control - deleting it does not change any observable behaviour of this
    module today. It stays because the transaction boundaries are owned by hand
    (``isolation_level=None``), and a future caller that holds a connection across two
    operations would otherwise keep a RESERVED lock until garbage collection.
    """
    try:
        connection.rollback()
    except sqlite3.Error:
        return


class SQLiteRunStore:
    """Store and read runs beneath ``<project root>/runs/<run id>/``."""

    __slots__ = ("_exported_lines", "_root")

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a Path")
        self._root = root.resolve()
        self._exported_lines: dict[str, int] = {}

    @property
    def root(self) -> Path:
        return self._root

    # --- artifact paths ---------------------------------------------------------------

    def run_directory(self, run_id: str) -> Path:
        """Resolve one run's directory beneath the project root, or refuse the name."""
        validated = validate_run_id(run_id)
        try:
            return resolve_project_path(self._root, Path(RUNS_DIRECTORY) / validated)
        except ValueError:
            raise UnsafeRunLocation(f"{run_id!r} resolves outside the project root") from None

    def run_json_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / RUN_DOCUMENT

    def scenario_json_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / INPUTS_DIRECTORY / SCENARIO_DOCUMENT

    def database_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / DATABASE_FILE

    def events_jsonl_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / EVENTS_EXPORT

    def metrics_json_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / METRICS_DOCUMENT

    def provider_usage_json_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / PROVIDER_USAGE_DOCUMENT

    # --- writing ----------------------------------------------------------------------

    def create_run(self, manifest: RunManifest, *, scenario: Scenario) -> None:
        """Build the whole run directory as a sibling, then publish it in one step.

        ``scenario`` is REQUIRED and is not merely stored: its canonical digest must be
        the ``scenario_hash`` the manifest claims. A run directory whose inputs are not
        provably the inputs of its own manifest cannot be replayed, and a default would
        make that failure silent instead of loud.
        """
        if not isinstance(manifest, RunManifest):
            raise TypeError("manifest must be a RunManifest")
        if not isinstance(scenario, Scenario):
            raise TypeError("scenario must be a Scenario")
        directory = self.run_directory(manifest.run_id)
        if canonical_sha256(scenario) != manifest.scenario_hash:
            raise StorageError(
                f"the scenario handed to run {manifest.run_id} is not the scenario its "
                "manifest addresses"
            )
        for document, label in ((manifest, "the run manifest"), (scenario, "the run inputs")):
            objection = persisted_text_objection(document.model_dump(mode="json"), label=label)
            if objection is not None:
                raise StorageError(objection)
        if directory.exists():
            raise DuplicateRun(f"run {manifest.run_id} already exists")

        runs_root = self._root / RUNS_DIRECTORY
        runs_root.mkdir(parents=True, exist_ok=True)
        temporary = runs_root / (
            f".{manifest.run_id}.{os.getpid()}.{next(_TEMPORARY_SUFFIXES)}.tmp"
        )
        try:
            (temporary / INPUTS_DIRECTORY).mkdir(parents=True)
            (temporary / RUN_DOCUMENT).write_text(canonical_json(manifest), encoding="utf-8")
            (temporary / INPUTS_DIRECTORY / SCENARIO_DOCUMENT).write_text(
                canonical_json(scenario), encoding="utf-8"
            )
            (temporary / EVENTS_EXPORT).write_bytes(b"")
            (temporary / METRICS_DOCUMENT).write_text(
                canonical_json({"schema_version": 1, "run_id": manifest.run_id, "metrics": {}}),
                encoding="utf-8",
            )
            (temporary / PROVIDER_USAGE_DOCUMENT).write_text(
                canonical_json(ProviderUsageLog(run_id=manifest.run_id)), encoding="utf-8"
            )
            self._create_database(temporary / DATABASE_FILE, manifest)
            publish_directory(temporary, directory)
        except OSError as error:
            shutil.rmtree(temporary, ignore_errors=True)
            raise StorageError(
                f"run {manifest.run_id} could not be created: {type(error).__name__}"
            ) from None
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        self._exported_lines[manifest.run_id] = 0

    def _create_database(self, path: Path, manifest: RunManifest) -> None:
        connection = connect_to_database(path)
        try:
            create_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO runs (run_id, status, manifest_json, result_json, created_at, "
                    "completed_at) VALUES (?, ?, ?, NULL, ?, NULL)",
                    (manifest.run_id, "running", canonical_json(manifest), utc_now_iso()),
                )
                connection.execute("COMMIT")
            except BaseException:
                _rollback_quietly(connection)
                raise
        except sqlite3.Error as error:
            raise StorageError(
                f"run {manifest.run_id} could not be created: {type(error).__name__}"
            ) from None
        finally:
            connection.close()

    def append_events(self, events: Sequence[DomainEvent]) -> None:
        """Record one committed tick: one transaction, then one export append.

        EVERYTHING THAT CAN REFUSE THE TICK RUNS FIRST. The batch is serialised and
        bounded, the export is counted, and that count is checked against the database's
        own next sequence inside the transaction that will do the writing - all before a
        single row is inserted. A refusal therefore means the tick was not recorded, which
        is the only thing that lets a caller retry it.
        """
        if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
            raise InvalidEventBatch("a batch must be a sequence of DomainEvent")
        if len(events) == 0:
            return
        run_id = batch_run_id(events)
        lines = self._event_lines(events)
        connection = self._connect(run_id, self.database_path(run_id))
        try:
            exported = self._exported_lines.get(run_id)
            if exported is None:
                exported = self._count_export_lines(run_id, self.events_jsonl_path(run_id))
            connection.execute("BEGIN IMMEDIATE")
            try:
                _status, next_sequence = self._read_open_run(connection, run_id)
                if exported != next_sequence:
                    raise CorruptRunArtifact(
                        f"events.jsonl for run {run_id} holds {exported} lines where the "
                        f"database holds {next_sequence} events"
                    )
                _, batch = validate_event_batch(events, next_sequence=next_sequence)
                for event, line in zip(batch, lines, strict=True):
                    connection.execute(
                        "INSERT INTO events (event_id, run_id, sequence, simulated_minute, "
                        "event_type, event_json) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            event.event_id,
                            event.run_id,
                            event.sequence,
                            event.simulated_minute,
                            event.event_type.value,
                            line,
                        ),
                    )
                connection.execute("COMMIT")
            except sqlite3.Error as error:
                _rollback_quietly(connection)
                raise StorageError(
                    f"run {run_id} could not record a tick: {type(error).__name__}"
                ) from None
            except BaseException:
                _rollback_quietly(connection)
                raise
        finally:
            connection.close()
        self._append_to_export(run_id, exported, lines)

    def _event_lines(self, events: Sequence[DomainEvent]) -> tuple[str, ...]:
        """Serialise the batch, refusing a line this store could not read back.

        :data:`MAX_EVENT_LINE_CHARS` is the READER's bound. Enforced only there, a writer
        could store an event that its own reader refuses: the write would report success
        and every later read would call the intact run corrupt, with nothing to point at.
        It is checked here, before the transaction opens.
        """
        lines = []
        for event in events:
            line = canonical_event_line(event)
            if len(line) > MAX_EVENT_LINE_CHARS:
                raise InvalidEventBatch(
                    f"event {event.sequence} serialises to {len(line)} characters, above "
                    f"the {MAX_EVENT_LINE_CHARS} characters a stored event may hold"
                )
            lines.append(line)
        return tuple(lines)

    def _append_to_export(self, run_id: str, exported: int, lines: Sequence[str]) -> None:
        """Extend the derived artifact, translating a device failure like any other.

        An untranslated :class:`OSError` here would escape the family the port documents,
        reach the command line as an unexpected defect rather than an artifact failure,
        and carry the filename it was raised with into the traceback.
        """
        try:
            append_export_lines(
                self.events_jsonl_path(run_id), "".join(f"{line}\n" for line in lines)
            )
        except OSError as error:
            self._exported_lines.pop(run_id, None)
            raise StorageError(
                f"run {run_id} committed a tick but could not extend {EVENTS_EXPORT}: "
                f"{type(error).__name__}"
            ) from None
        self._exported_lines[run_id] = exported + len(lines)

    def _count_export_lines(self, run_id: str, export: Path) -> int:
        try:
            return sum(1 for _ in iter_export_lines(export))
        except (OSError, ValueError) as error:
            raise CorruptRunArtifact(f"events.jsonl for run {run_id}: {error}") from None

    def rebuild_export(self, run_id: str) -> int:
        """Re-derive ``events.jsonl`` from the database rows; return the lines written.

        Specification section 13 makes SQLite the durable event store and the JSONL file
        an append-only portable EXPORT of it. Two files cannot be written atomically
        together, so a kill or a full device between the commit and the export's fsync
        leaves a run that is whole in the store and short in the export - and every read
        then refuses it, correctly, but for the rest of its life.

        This is the way out, and it is deliberately something a caller asks for rather
        than something a read quietly does: the rows are streamed through the same
        contiguity and document checks a load applies, so a database that is not itself a
        run is refused instead of being copied into an export that agrees with it. The
        file is built beside its target and moved over it, so an interrupted repair leaves
        the previous export rather than a half-written one.
        """
        export = self.events_jsonl_path(run_id)
        temporary = export.with_name(
            f".{export.name}.{os.getpid()}.{next(_TEMPORARY_SUFFIXES)}.tmp"
        )
        written = 0
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                for event in self.iter_events(run_id):
                    handle.write(f"{canonical_event_line(event)}\n")
                    written += 1
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, export)
        except OSError as error:
            raise StorageError(
                f"run {run_id} could not rebuild {EVENTS_EXPORT}: {type(error).__name__}"
            ) from None
        finally:
            temporary.unlink(missing_ok=True)
        self._exported_lines[run_id] = written
        return written

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        """Store one day-boundary checkpoint inside its own transaction.

        The checkpoint is SCREENED like every other persisted document. It carries
        ``daily_reflection`` and each memory summary, which are provider paraphrase and
        are bounded but not screened where they are constructed, so this is the only
        boundary between an LLM sentence and a permanent artifact.
        """
        if not isinstance(checkpoint, RunCheckpoint):
            raise TypeError("checkpoint must be a RunCheckpoint")
        objection = persisted_text_objection(
            checkpoint.model_dump(mode="json"), label="the checkpoint"
        )
        if objection is not None:
            raise StorageError(objection)
        run_id = checkpoint.run_id
        connection = self._connect(run_id, self.database_path(run_id))
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _, next_sequence = self._read_open_run(connection, run_id)
                if checkpoint.next_event_sequence != next_sequence:
                    raise StorageError(
                        f"the checkpoint claims sequence {checkpoint.next_event_sequence} "
                        f"but run {run_id} has recorded {next_sequence} events"
                    )
                connection.execute(
                    "INSERT INTO checkpoints (run_id, simulated_minute, checkpoint_json) "
                    "VALUES (?, ?, ?)",
                    (run_id, checkpoint.simulated_minute, canonical_json(checkpoint)),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError:
                _rollback_quietly(connection)
                raise StorageError(
                    f"a checkpoint for run {run_id} at minute "
                    f"{checkpoint.simulated_minute} is already stored"
                ) from None
            except sqlite3.Error as error:
                _rollback_quietly(connection)
                raise StorageError(
                    f"run {run_id} could not record a checkpoint: {type(error).__name__}"
                ) from None
            except BaseException:
                _rollback_quietly(connection)
                raise
        finally:
            connection.close()

    def save_provider_usage(self, log: ProviderUsageLog) -> None:
        """Replace the run's provider-usage document atomically.

        ``model_id`` is free configuration text and reaches the document verbatim, so the
        log is screened on the same rules as the manifest beside it.
        """
        if not isinstance(log, ProviderUsageLog):
            raise TypeError("log must be a ProviderUsageLog")
        objection = persisted_text_objection(
            log.model_dump(mode="json"), label="the provider usage log"
        )
        if objection is not None:
            raise StorageError(objection)
        run_id = log.run_id
        connection = self._connect(run_id, self.database_path(run_id))
        try:
            self._read_open_run(connection, run_id)
        finally:
            connection.close()
        self._write_document(run_id, self.provider_usage_json_path(run_id), canonical_json(log))

    def complete_run(self, result: SimulationResult) -> None:
        """Close a run: publish its metrics document, THEN record the outcome.

        That order is deliberate. If the document write fails, the database still says
        ``running`` and the run can be completed again. If the order were reversed and the
        write failed, the database would say ``completed`` while ``metrics.json`` still
        held the placeholder, and :meth:`load_run` would then refuse the whole run as
        corrupt for the rest of its life.
        """
        if not isinstance(result, SimulationResult):
            raise TypeError("result must be a SimulationResult")
        run_id = result.run_id
        objection = persisted_text_objection(
            result.model_dump(mode="json"), label="the simulation result"
        )
        if objection is not None:
            raise StorageError(objection)
        connection = self._connect(run_id, self.database_path(run_id))
        try:
            _, next_sequence = self._read_open_run(connection, run_id)
            if result.event_count != next_sequence:
                raise StorageError(
                    f"the result reports event_count {result.event_count} but run {run_id} "
                    f"has recorded {next_sequence} events"
                )
            self._write_document(run_id, self.metrics_json_path(run_id), canonical_json(result))
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE runs SET status = ?, result_json = ?, completed_at = ? "
                    "WHERE run_id = ?",
                    (result.status, canonical_json(result), utc_now_iso(), run_id),
                )
                connection.execute("DELETE FROM metrics WHERE run_id = ?", (run_id,))
                for name in sorted(result.metrics):
                    connection.execute(
                        "INSERT INTO metrics (run_id, metric_name, metric_value) VALUES (?, ?, ?)",
                        (run_id, name, float(result.metrics[name])),
                    )
                connection.execute("COMMIT")
            except sqlite3.Error as error:
                _rollback_quietly(connection)
                raise StorageError(
                    f"run {run_id} could not be completed: {type(error).__name__}"
                ) from None
            except BaseException:
                _rollback_quietly(connection)
                raise
        finally:
            connection.close()

    def _write_document(self, run_id: str, path: Path, text: str) -> None:
        try:
            write_document_atomically(path, text)
        except OSError as error:
            raise StorageError(
                f"run {run_id} could not write {path.name}: {type(error).__name__}"
            ) from None

    # --- reading ----------------------------------------------------------------------

    def load_run(self, run_id: str, *, max_events: int = MAX_LOADED_EVENTS) -> StoredRun:
        """Read one run back, verifying every artifact against every other one."""
        if not isinstance(max_events, int) or max_events < 0:
            raise ValueError("max_events must be a non-negative integer")
        connection = self._connect(run_id, self.database_path(run_id))
        try:
            row = connection.execute(
                "SELECT status, substr(manifest_json, 1, ?), substr(result_json, 1, ?), "
                "created_at, completed_at FROM runs WHERE run_id = ?",
                (MAX_STORED_DOCUMENT_CHARS + 1, MAX_STORED_DOCUMENT_CHARS + 1, run_id),
            ).fetchone()
            if row is None:
                raise RunNotFound(f"run {run_id} is not stored here")
            status, manifest_json, result_json, created_at, completed_at = row
            manifest = self._read_manifest(run_id, manifest_json)
            result = self._read_result(run_id, status, result_json)
            events = self._read_events(connection, run_id, max_events)
            checkpoints = self._read_checkpoints(connection, run_id)
            self._verify_metrics_table(connection, run_id, result)
        finally:
            connection.close()

        scenario = self._read_scenario(run_id, manifest)
        self._verify_run_document(run_id, manifest)
        self._verify_export(run_id, events)
        self._verify_metrics_document(run_id, result)
        self._verify_provider_usage_document(run_id)
        try:
            return StoredRun(
                manifest=manifest,
                scenario=scenario,
                status=status,
                created_at=created_at,
                completed_at=completed_at,
                events=events,
                checkpoints=checkpoints,
                result=result,
            )
        except ValidationError:
            raise CorruptRunArtifact(
                f"the stored records for run {run_id} do not describe a single consistent run"
            ) from None

    def load_provider_usage(self, run_id: str) -> ProviderUsageLog:
        """Read the provider-usage document back, refusing one that is not this run's."""
        path = self.provider_usage_json_path(run_id)
        try:
            text = self._read_bounded_text(run_id, path, label=PROVIDER_USAGE_DOCUMENT)
            log = ProviderUsageLog.model_validate_json(text)
        except (OSError, ValueError) as error:
            raise CorruptRunArtifact(
                f"provider-usage.json for run {run_id} is not a usage log: {type(error).__name__}"
            ) from None
        if log.run_id != run_id:
            raise CorruptRunArtifact(f"provider-usage.json for run {run_id} names another run")
        return log

    def iter_events(self, run_id: str, *, start_sequence: int = 0) -> Iterator[DomainEvent]:
        """Stream a run's events in sequence order without loading the whole run.

        CONTIGUITY IS CHECKED HERE TOO. A load refuses a run with a hole in it, and a
        stream that accepted the same artifact would let replay and the live interface -
        the two documented consumers of this method - reproduce a run that never
        happened, quietly. The check costs one comparison per row and needs no second
        query, because the rows arrive in sequence order.
        """
        if not isinstance(start_sequence, int) or start_sequence < 0:
            raise ValueError("start_sequence must be a non-negative integer")
        connection = self._connect(run_id, self.database_path(run_id))
        try:
            cursor = connection.execute(
                "SELECT event_id, sequence, simulated_minute, event_type, "
                "substr(event_json, 1, ?) FROM events WHERE run_id = ? AND sequence >= ? "
                "ORDER BY sequence",
                (MAX_EVENT_LINE_CHARS + 1, run_id, start_sequence),
            )
            expected = start_sequence
            while rows := cursor.fetchmany(256):
                for event_row in rows:
                    event = self._read_event(run_id, event_row)
                    if event.sequence != expected:
                        raise CorruptRunArtifact(
                            f"the events stored for run {run_id} are not contiguous from "
                            f"{start_sequence}"
                        )
                    expected += 1
                    yield event
        finally:
            connection.close()

    # --- internals --------------------------------------------------------------------

    def _connect(self, run_id: str, path: Path) -> sqlite3.Connection:
        if not path.exists():
            if not self.run_directory(run_id).exists():
                raise RunNotFound(f"run {run_id} is not stored here")
            raise CorruptRunArtifact(f"run {run_id} has no results.sqlite3")
        try:
            connection = connect_to_database(path)
        except sqlite3.Error as error:
            raise CorruptRunArtifact(
                f"results.sqlite3 for run {run_id} could not be opened: {type(error).__name__}"
            ) from None
        try:
            version = read_schema_version(connection)
        except sqlite3.Error as error:
            connection.close()
            raise CorruptRunArtifact(
                f"results.sqlite3 for run {run_id} has no readable schema version: "
                f"{type(error).__name__}"
            ) from None
        except ValueError as error:
            connection.close()
            raise CorruptRunArtifact(
                f"results.sqlite3 for run {run_id} has no usable schema version: {error}"
            ) from None
        if version != SCHEMA_VERSION:
            connection.close()
            raise SchemaVersionMismatch(
                f"run {run_id} was written at schema version {version}; this build reads "
                f"version {SCHEMA_VERSION}"
            )
        return connection

    def _read_open_run(self, connection: sqlite3.Connection, run_id: str) -> tuple[str, int]:
        row = connection.execute(
            "SELECT r.status, (SELECT COALESCE(MAX(e.sequence) + 1, 0) FROM events e "
            "WHERE e.run_id = r.run_id) FROM runs r WHERE r.run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunNotFound(f"run {run_id} is not stored here")
        status, next_sequence = row
        if status != "running":
            raise RunAlreadyComplete(f"run {run_id} is {status} and accepts no further writes")
        return str(status), int(next_sequence)

    def _read_manifest(self, run_id: str, manifest_json: object) -> RunManifest:
        document = self._bounded_column(run_id, manifest_json, label="the stored manifest")
        try:
            manifest = RunManifest.model_validate_json(document)
        except ValueError:
            raise CorruptRunArtifact(
                f"the manifest stored for run {run_id} is not a manifest"
            ) from None
        if manifest.run_id != run_id:
            raise CorruptRunArtifact(f"the manifest stored for run {run_id} names another run")
        return manifest

    def _read_result(
        self, run_id: str, status: object, result_json: object
    ) -> SimulationResult | None:
        if status not in VALID_STATUSES:
            raise CorruptRunArtifact(f"{status!r} is not a run status for run {run_id}")
        if result_json is None:
            if status != "running":
                raise CorruptRunArtifact(
                    f"run {run_id} is recorded as {status} but carries no result"
                )
            return None
        document = self._bounded_column(run_id, result_json, label="the stored result")
        try:
            result = SimulationResult.model_validate_json(document)
        except ValueError:
            raise CorruptRunArtifact(
                f"the result stored for run {run_id} is not a result"
            ) from None
        if result.run_id != run_id:
            raise CorruptRunArtifact(f"the result stored for run {run_id} names another run")
        if result.status != status:
            raise CorruptRunArtifact(
                f"run {run_id} is recorded with status {status} but its result reports "
                f"status {result.status}"
            )
        return result

    def _read_events(
        self, connection: sqlite3.Connection, run_id: str, max_events: int
    ) -> tuple[DomainEvent, ...]:
        stored = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        if stored > max_events:
            raise RunTooLargeToLoad(
                f"run {run_id} holds {stored} events, above the {max_events} this read allows"
            )
        rows = connection.execute(
            "SELECT event_id, sequence, simulated_minute, event_type, "
            "substr(event_json, 1, ?) FROM events WHERE run_id = ? ORDER BY sequence",
            (MAX_EVENT_LINE_CHARS + 1, run_id),
        ).fetchall()
        events = tuple(self._read_event(run_id, row) for row in rows)
        for index, event in enumerate(events):
            if event.sequence != index:
                raise CorruptRunArtifact(
                    f"the events stored for run {run_id} are not contiguous from zero"
                )
        return events

    def _read_event(self, run_id: str, row: tuple[object, ...]) -> DomainEvent:
        event_id, sequence, simulated_minute, event_type, event_json = row
        try:
            event = parse_event_line(str(event_json))
        except EventLineTooLong:
            raise CorruptRunArtifact(
                f"the event document at sequence {sequence} of run {run_id} exceeds "
                f"{MAX_EVENT_LINE_CHARS} characters"
            ) from None
        except ValueError:
            raise CorruptRunArtifact(
                f"the event document at sequence {sequence} of run {run_id} is not an event"
            ) from None
        if (
            event.event_id != event_id
            or event.run_id != run_id
            or event.sequence != sequence
            or event.simulated_minute != simulated_minute
            or event.event_type.value != event_type
        ):
            raise CorruptRunArtifact(
                f"the event document at sequence {sequence} of run {run_id} disagrees with "
                "its own row"
            )
        return event

    def _read_checkpoints(
        self, connection: sqlite3.Connection, run_id: str
    ) -> tuple[RunCheckpoint, ...]:
        rows = connection.execute(
            "SELECT simulated_minute, substr(checkpoint_json, 1, ?) FROM checkpoints "
            "WHERE run_id = ? ORDER BY simulated_minute",
            (MAX_STORED_DOCUMENT_CHARS + 1, run_id),
        ).fetchall()
        checkpoints = []
        for simulated_minute, checkpoint_json in rows:
            document = self._bounded_column(
                run_id, checkpoint_json, label=f"the checkpoint at minute {simulated_minute}"
            )
            try:
                checkpoint = RunCheckpoint.model_validate_json(document)
            except ValueError:
                raise CorruptRunArtifact(
                    f"the checkpoint at minute {simulated_minute} of run {run_id} is not a "
                    "checkpoint"
                ) from None
            if checkpoint.run_id != run_id or checkpoint.simulated_minute != simulated_minute:
                raise CorruptRunArtifact(
                    f"the checkpoint at minute {simulated_minute} of run {run_id} disagrees "
                    "with its own row"
                )
            checkpoints.append(checkpoint)
        return tuple(checkpoints)

    def _verify_metrics_table(
        self, connection: sqlite3.Connection, run_id: str, result: SimulationResult | None
    ) -> None:
        rows = connection.execute(
            "SELECT metric_name, metric_value FROM metrics WHERE run_id = ? ORDER BY metric_name",
            (run_id,),
        ).fetchall()
        expected = (
            []
            if result is None
            else sorted((name, float(value)) for name, value in result.metrics.items())
        )
        if [(str(name), float(value)) for name, value in rows] != expected:
            raise CorruptRunArtifact(
                f"the metric rows stored for run {run_id} disagree with its recorded result"
            )

    def _bounded_column(self, run_id: str, value: object, *, label: str) -> str:
        """Refuse a whole-document column above the bound rather than parsing it.

        The column was selected through ``substr`` at one character above the bound, so
        the oversized case is recognised on a truncation and the rest of the blob is never
        pulled out of the database at all. ``results.sqlite3`` is user-supplied data on
        the same footing as the files beside it; nothing about being a database row makes
        a document safe to materialise unread.
        """
        document = str(value)
        if len(document) > MAX_STORED_DOCUMENT_CHARS:
            raise CorruptRunArtifact(
                f"{label} for run {run_id} exceeds {MAX_STORED_DOCUMENT_CHARS} characters"
            )
        return document

    def _read_bounded_text(self, run_id: str, path: Path, *, label: str) -> str:
        """Read one artifact document, refusing an oversized file as oversized.

        The bound raises a refusal of its own rather than a ``ValueError`` the caller
        would flatten into its ordinary "this is not a scenario" message. A test that
        cannot tell the bound from a parse failure cannot tell the bound from its own
        deletion either.
        """
        if path.stat().st_size > MAX_INPUT_DOCUMENT_BYTES:
            raise CorruptRunArtifact(
                f"{label} for run {run_id} exceeds {MAX_INPUT_DOCUMENT_BYTES} bytes"
            )
        return path.read_text(encoding="utf-8")

    def _read_scenario(self, run_id: str, manifest: RunManifest) -> Scenario:
        path = self.scenario_json_path(run_id)
        try:
            scenario = Scenario.model_validate_json(
                self._read_bounded_text(run_id, path, label="inputs/scenario.json")
            )
        except (OSError, ValueError) as error:
            raise CorruptRunArtifact(
                f"inputs/scenario.json for run {run_id} is not a scenario: {type(error).__name__}"
            ) from None
        if canonical_sha256(scenario) != manifest.scenario_hash:
            raise CorruptRunArtifact(
                f"inputs/scenario.json for run {run_id} is not the scenario its manifest addresses"
            )
        return scenario

    def _verify_run_document(self, run_id: str, manifest: RunManifest) -> None:
        try:
            text = self._read_bounded_text(run_id, self.run_json_path(run_id), label=RUN_DOCUMENT)
        except (OSError, ValueError) as error:
            raise CorruptRunArtifact(
                f"run.json for run {run_id} could not be read: {type(error).__name__}"
            ) from None
        if text != canonical_json(manifest):
            raise CorruptRunArtifact(
                f"run.json for run {run_id} is not the manifest the database records"
            )

    def _verify_export(self, run_id: str, events: tuple[DomainEvent, ...]) -> None:
        expected = [canonical_event_line(event) for event in events]
        try:
            index = -1
            for index, line in enumerate(iter_export_lines(self.events_jsonl_path(run_id))):
                if index >= len(expected) or line != expected[index]:
                    raise ValueError(f"line {index + 1} is not the event the database records")
        except (OSError, ValueError) as error:
            raise CorruptRunArtifact(f"events.jsonl for run {run_id}: {error}") from None
        if index + 1 != len(expected):
            raise CorruptRunArtifact(
                f"events.jsonl for run {run_id} holds {index + 1} lines where the database "
                f"holds {len(expected)} events"
            )
        self._exported_lines[run_id] = len(expected)

    def _verify_metrics_document(self, run_id: str, result: SimulationResult | None) -> None:
        if result is None:
            return
        try:
            text = self._read_bounded_text(
                run_id, self.metrics_json_path(run_id), label=METRICS_DOCUMENT
            )
        except (OSError, ValueError) as error:
            raise CorruptRunArtifact(
                f"metrics.json for run {run_id} could not be read: {type(error).__name__}"
            ) from None
        if text != canonical_json(result):
            raise CorruptRunArtifact(
                f"metrics.json for run {run_id} is not the result the database records"
            )

    def _verify_provider_usage_document(self, run_id: str) -> None:
        self.load_provider_usage(run_id)


__all__ = [
    "DATABASE_FILE",
    "EVENTS_EXPORT",
    "INPUTS_DIRECTORY",
    "METRICS_DOCUMENT",
    "PROVIDER_USAGE_DOCUMENT",
    "RUNS_DIRECTORY",
    "RUN_DOCUMENT",
    "SCENARIO_DOCUMENT",
    "SQLiteRunStore",
    "append_export_lines",
    "iter_export_lines",
    "publish_directory",
    "utc_now_iso",
    "write_document_atomically",
]
