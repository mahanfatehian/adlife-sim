"""The run-store port: the authoritative, replayable record of one simulation run.

A store is not a log. Specification section 13 makes the event stream the source of truth
- current state is a projection of the events plus the versioned initial inputs - so an
artifact that accepts a duplicate event, a gap in the sequence, a cause that never
happened, or an append to a finished run is not a slightly imperfect record. It is a
record of a run that did not occur, and it reads back without complaining.

Everything this module refuses is therefore refused BEFORE anything is written, and the
rules live here rather than in the SQLite adapter so a second adapter inherits them:

* one batch belongs to one run, and that run exists and is still open;
* an event is addressed by ``(run_id, sequence)`` and its ``event_id`` is the stable
  identifier built from exactly that pair;
* sequences are contiguous - the batch begins where the run left off;
* every ``caused_by_event_ids`` entry names an EARLIER event of the SAME run;
* nothing persisted carries text that the repository's published screens object to.

Checkpoints are day-scoped on purpose. The social layer carries two accumulators across
the ticks of one simulated day and resets both at the daily reflection, so a checkpoint
taken anywhere else would have to carry them to be resumable. Requiring a day boundary
makes "the accumulators are empty" a checked property instead of an assumption.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from adlife.core.domain.events import DomainEvent
from adlife.core.domain.person import DomainModel
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.serialization import persisted_text_objection
from adlife.core.domain.state import ConsumerState
from adlife.core.ports.cognition import ProviderUsage

RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
"""The same shape :class:`~adlife.core.domain.results.RunManifest` validates."""

STABLE_EVENT_ID_PATTERN = re.compile(r"^([a-z0-9][a-z0-9-]{0,39}):event-([0-9]{8})$")
"""``adlife.core.simulation.engine.stable_event_id`` spelled as a reader."""

ISO_UTC_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
"""Wall-clock metadata is ISO-8601 UTC and is never mixed with simulated minutes."""

MINUTES_PER_DAY = 1440

MAX_LOADED_EVENTS = 500_000
"""Default ceiling for reading a whole run into memory. Streaming has no ceiling."""

MAX_PROVIDER_USAGE_RECORDS = 1_000
"""Specification section 7 caps cognition at 6 per agent for at most 30 agents."""

MAX_CHECKPOINTS = 8
"""One per simulated day boundary, for the 7-day maximum, plus the start of the run."""

WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{digit}" for digit in range(1, 10))}
    | {f"lpt{digit}" for digit in range(1, 10)}
)
"""Names that match the run-identifier shape but cannot be a directory on Windows.

The quality gates require the same artifact to be readable on Linux, macOS and Windows,
so a run identifier that only works on two of the three is refused on all three.
"""

RunStatus = Literal["running", "completed", "failed", "interrupted"]


class StorageError(RuntimeError):
    """Base class for every persistence failure. The CLI maps it to exit code 4."""


class RunNotFound(StorageError):
    """Raised when the addressed run does not exist."""


class DuplicateRun(StorageError):
    """Raised when a run identifier is already taken."""


class RunAlreadyComplete(StorageError):
    """Raised when a finished run is asked to change. A finished artifact is immutable."""


class InvalidEventBatch(StorageError):
    """Raised when a batch would make the stored event stream unreplayable."""


class CorruptRunArtifact(StorageError):
    """Raised when a stored artifact cannot be trusted. It is never silently accepted."""


class SchemaVersionMismatch(StorageError):
    """Raised when an artifact was written by a different schema version."""


class RunTooLargeToLoad(StorageError):
    """Raised rather than reading an unbounded artifact into memory."""


class UnsafeRunLocation(StorageError):
    """Raised when a run identifier would address something outside the project root."""


def validate_run_id(run_id: object) -> str:
    """Return the run identifier, or refuse it. Every artifact path is built from one."""
    if not isinstance(run_id, str) or RUN_ID_PATTERN.match(run_id) is None:
        raise UnsafeRunLocation(f"{run_id!r} is not a run identifier")
    if run_id in WINDOWS_RESERVED_NAMES:
        raise UnsafeRunLocation(f"{run_id!r} is a reserved device name on Windows")
    return run_id


def parse_stable_event_id(event_id: object) -> tuple[str, int] | None:
    """Read ``run-x:event-00000007`` back as ``("run-x", 7)``, or return None."""
    if not isinstance(event_id, str):
        return None
    match = STABLE_EVENT_ID_PATTERN.match(event_id)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


class RunCheckpoint(DomainModel):
    """A resumable picture of the population at one simulated day boundary."""

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    simulated_minute: int = Field(ge=0, le=10080)
    next_event_sequence: int = Field(ge=0)
    states: tuple[ConsumerState, ...] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> Self:
        if self.simulated_minute % MINUTES_PER_DAY != 0:
            raise ValueError("a checkpoint must be taken at a simulated day boundary")
        agent_ids = tuple(state.agent_id for state in self.states)
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("duplicate agent_id in checkpoint states")
        if agent_ids != tuple(sorted(agent_ids)):
            raise ValueError("checkpoint states must be ordered by agent_id")
        return self


class ProviderUsageLog(DomainModel):
    """Everything one run paid a provider for, in the order the answers were produced."""

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    records: tuple[ProviderUsage, ...] = Field(default=(), max_length=MAX_PROVIDER_USAGE_RECORDS)


class StoredRun(DomainModel):
    """One complete run as it was read back: inputs, events, checkpoints and outcome."""

    schema_version: Literal[1] = 1
    manifest: RunManifest
    scenario: Scenario
    status: RunStatus
    created_at: str = Field(pattern=ISO_UTC_PATTERN)
    completed_at: str | None = Field(default=None, pattern=ISO_UTC_PATTERN)
    events: tuple[DomainEvent, ...] = ()
    checkpoints: tuple[RunCheckpoint, ...] = Field(default=(), max_length=MAX_CHECKPOINTS)
    result: SimulationResult | None = None

    @model_validator(mode="after")
    def validate_stored_run(self) -> Self:
        run_id = self.manifest.run_id
        if self.manifest.scenario_id != self.scenario.scenario_id:
            raise ValueError("the stored inputs are not the scenario the manifest names")
        for index, event in enumerate(self.events):
            if event.run_id != run_id:
                raise ValueError(f"event {index} belongs to another run")
            if event.sequence != index:
                raise ValueError("stored events must be contiguous from zero")
        minutes = tuple(checkpoint.simulated_minute for checkpoint in self.checkpoints)
        if minutes != tuple(sorted(set(minutes))):
            raise ValueError("checkpoints must ascend by simulated minute")
        if any(checkpoint.run_id != run_id for checkpoint in self.checkpoints):
            raise ValueError("a checkpoint belongs to another run")
        if (self.status == "running") != (self.result is None):
            raise ValueError("a finished run carries a result and a running run does not")
        if (self.completed_at is None) != (self.result is None):
            raise ValueError("a finished run carries a completion time")
        if self.result is not None:
            if self.result.run_id != run_id:
                raise ValueError("the stored result belongs to another run")
            if self.result.status != self.status:
                raise ValueError("the stored status contradicts the stored result")
            if self.result.event_count != len(self.events):
                raise ValueError("the stored result disagrees with the stored event count")
        return self


def batch_run_id(events: Sequence[DomainEvent]) -> str:
    """Name the single run a batch belongs to, or refuse the batch.

    A store needs the run identifier BEFORE it can look up where that run left off, so
    this is the half of batch validation that does not depend on stored state.
    """
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise InvalidEventBatch("a batch must be a sequence of DomainEvent")
    batch = tuple(events)
    if not batch:
        raise InvalidEventBatch("an empty batch names no run")
    for index, event in enumerate(batch):
        if not isinstance(event, DomainEvent):
            raise InvalidEventBatch(f"batch member {index} is not a DomainEvent")
    run_id = batch[0].run_id
    if any(event.run_id != run_id for event in batch):
        raise InvalidEventBatch("a batch must belong to one run")
    return run_id


def validate_event_batch(
    events: Sequence[DomainEvent], *, next_sequence: int
) -> tuple[str, tuple[DomainEvent, ...]]:
    """Check one tick's events against the run they continue; return the run and batch.

    Causality is checked without reading a single stored row: an event is addressed by
    ``(run_id, sequence)`` and its identifier is built from exactly that pair, so a cause
    exists precisely when it names this run and a sequence below the caused event's own.
    """
    run_id = batch_run_id(events)
    batch = tuple(events)

    for offset, event in enumerate(batch):
        expected_sequence = next_sequence + offset
        if event.sequence != expected_sequence:
            raise InvalidEventBatch(
                f"event sequences must be contiguous: expected {expected_sequence}, "
                f"got {event.sequence}"
            )
        if parse_stable_event_id(event.event_id) != (run_id, event.sequence):
            raise InvalidEventBatch(
                f"event {event.sequence} does not carry the stable event identifier "
                f"for ({run_id}, {event.sequence})"
            )
        for cause in event.caused_by_event_ids:
            parsed = parse_stable_event_id(cause)
            if parsed is None or parsed[0] != run_id or parsed[1] >= event.sequence:
                raise InvalidEventBatch(
                    f"event {event.sequence} names a causal event that this run has not "
                    f"recorded: {cause}"
                )
        objection = persisted_text_objection(
            dict(event.payload), label=f"the payload of event {event.sequence}"
        )
        if objection is not None:
            raise InvalidEventBatch(objection)

    return run_id, batch


@runtime_checkable
class RunStore(Protocol):
    """The authoritative persistence port for one project's runs."""

    def create_run(self, manifest: RunManifest, *, scenario: Scenario) -> None: ...

    def append_events(self, events: Sequence[DomainEvent]) -> None: ...

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None: ...

    def save_provider_usage(self, log: ProviderUsageLog) -> None: ...

    def complete_run(self, result: SimulationResult) -> None: ...

    def load_run(self, run_id: str, *, max_events: int = MAX_LOADED_EVENTS) -> StoredRun: ...

    def iter_events(self, run_id: str, *, start_sequence: int = 0) -> Iterator[DomainEvent]: ...


__all__ = [
    "ISO_UTC_PATTERN",
    "MAX_CHECKPOINTS",
    "MAX_LOADED_EVENTS",
    "MAX_PROVIDER_USAGE_RECORDS",
    "MINUTES_PER_DAY",
    "RUN_ID_PATTERN",
    "STABLE_EVENT_ID_PATTERN",
    "WINDOWS_RESERVED_NAMES",
    "CorruptRunArtifact",
    "DuplicateRun",
    "InvalidEventBatch",
    "ProviderUsageLog",
    "RunAlreadyComplete",
    "RunCheckpoint",
    "RunNotFound",
    "RunStatus",
    "RunStore",
    "RunTooLargeToLoad",
    "SchemaVersionMismatch",
    "StorageError",
    "StoredRun",
    "UnsafeRunLocation",
    "batch_run_id",
    "parse_stable_event_id",
    "validate_event_batch",
    "validate_run_id",
]
