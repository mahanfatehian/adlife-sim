"""The persistence contract every :class:`RunStore` implementation must satisfy.

This module is deliberately written against the PORT and not against SQLite. The single
shared assertion - :func:`assert_run_store_contract` - is the definition of "a run store
works"; :mod:`tests.integration.test_sqlite_store` covers what is specific to the SQLite
adapter, and a future adapter is admitted by pointing the ``store`` fixture at it.

The contract is mostly about REFUSAL, because durability is mostly about refusal. A store
that accepts a duplicate event, a sequence gap, a cause that does not exist, an append to
a finished run, or a run identifier that climbs out of the project root has not stored a
replayable run - it has stored something that reads back without complaining.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.ports.cognition import ProviderUsage
from adlife.core.ports.run_store import (
    MAX_CHECKPOINTS,
    MAX_LOADED_EVENTS,
    MAX_PROVIDER_USAGE_RECORDS,
    CorruptRunArtifact,
    DuplicateRun,
    ExportNotExtended,
    InvalidEventBatch,
    ProviderUsageLog,
    RunAlreadyComplete,
    RunCheckpoint,
    RunNotFound,
    RunStore,
    RunTooLargeToLoad,
    StorageError,
    StoredRun,
    UnsafeRunLocation,
    batch_run_id,
    parse_stable_event_id,
)
from adlife.core.simulation.engine import stable_event_id


@pytest.fixture
def store(tmp_path: Path) -> SQLiteRunStore:
    return SQLiteRunStore(tmp_path)


@pytest.fixture
def domain_events(event_factory: Callable[..., DomainEvent]) -> tuple[DomainEvent, ...]:
    return (
        event_factory(0, event_type=EventType.RUN_STARTED, agent_id=None),
        event_factory(
            1,
            simulated_minute=15,
            event_type=EventType.CAMPAIGN_IMPRESSION,
            campaign_id="campaign-phone",
            channel="mobile-feed",
            caused_by_event_ids=("run-storage:event-00000000",),
        ),
        event_factory(
            2,
            simulated_minute=15,
            event_type=EventType.CAMPAIGN_NOTICED,
            campaign_id="campaign-phone",
            channel="mobile-feed",
            payload={"notice_probability": 0.62},
            caused_by_event_ids=("run-storage:event-00000001",),
        ),
    )


def assert_run_store_contract(
    store: RunStore,
    manifest: RunManifest,
    scenario: Scenario,
    domain_events: Sequence[DomainEvent],
) -> None:
    """The shared round trip: what goes in comes back, in order and unchanged."""
    store.create_run(manifest, scenario=scenario)
    store.append_events(domain_events)
    loaded = store.load_run(manifest.run_id)
    assert loaded.manifest == manifest
    assert loaded.events == tuple(domain_events)
    assert loaded.status == "running"
    assert loaded.result is None


def test_a_sqlite_store_satisfies_the_shared_run_store_contract(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
) -> None:
    assert_run_store_contract(store, run_manifest, valid_scenario, domain_events)


def test_a_sqlite_store_satisfies_the_run_store_protocol(store: SQLiteRunStore) -> None:
    assert isinstance(store, RunStore)


def test_every_storage_failure_is_one_typed_family() -> None:
    """The CLI maps a corrupted artifact to exit code 4, so one base class must cover it."""
    for error in (
        RunNotFound,
        DuplicateRun,
        RunAlreadyComplete,
        InvalidEventBatch,
        CorruptRunArtifact,
        ExportNotExtended,
        RunTooLargeToLoad,
        UnsafeRunLocation,
    ):
        assert issubclass(error, StorageError)


def test_an_appended_batch_is_all_or_nothing_when_a_statement_fails(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
    failing_connection: Callable[[int], None],
) -> None:
    """One transaction contains the whole tick: a mid-batch failure stores nothing.

    Three statements succeed - ``BEGIN IMMEDIATE``, the single status-and-sequence read,
    and the first event insert - so the failure lands with a row already written inside
    the transaction. Without the rollback that first row would survive.
    """
    store.create_run(run_manifest, scenario=valid_scenario)
    failing_connection(3)

    with pytest.raises(StorageError):
        store.append_events(domain_events)

    assert store.load_run(run_manifest.run_id).events == ()


def test_a_failed_append_leaves_the_portable_export_untouched(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
    failing_connection: Callable[[int], None],
) -> None:
    """The database commits first, so a database failure can never leak into the JSONL."""
    store.create_run(run_manifest, scenario=valid_scenario)
    failing_connection(3)

    with pytest.raises(StorageError):
        store.append_events(domain_events)

    assert store.events_jsonl_path(run_manifest.run_id).read_bytes() == b""


def test_a_duplicate_event_identifier_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """The IDENTIFIER rule, tripped on its own.

    A batch that merely repeats a stored sequence trips the contiguity rule first, so it
    says nothing about this one. Here the sequence continues correctly and only the
    identifier is the one an already-stored event carries.
    """
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events([event_factory(0)])

    with pytest.raises(InvalidEventBatch, match="stable event identifier"):
        store.append_events([event_factory(1, event_id=stable_event_id(run_manifest.run_id, 0))])


def test_re_appending_an_already_stored_sequence_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events([event_factory(0)])

    with pytest.raises(InvalidEventBatch, match="contiguous"):
        store.append_events([event_factory(0)])


def test_a_duplicate_event_identifier_inside_one_batch_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch):
        store.append_events([event_factory(0), event_factory(0)])


def test_a_non_monotonic_sequence_inside_one_batch_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch, match="contiguous"):
        store.append_events([event_factory(1), event_factory(0)])


def test_a_gap_in_the_sequence_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A gap means an event was lost; replay from this artifact would be a different run."""
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events([event_factory(0)])

    with pytest.raises(InvalidEventBatch, match="contiguous"):
        store.append_events([event_factory(2)])


def test_an_unknown_causal_reference_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch, match="causal"):
        store.append_events([event_factory(0, caused_by_event_ids=("run-storage:event-00000044",))])


def test_a_causal_reference_to_another_run_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events([event_factory(0)])

    with pytest.raises(InvalidEventBatch, match="causal"):
        store.append_events([event_factory(1, caused_by_event_ids=("run-other:event-00000000",))])


def test_a_causal_reference_that_is_not_an_event_identifier_at_all_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A cause is checked by READING it, so text that is not an identifier is a refusal.

    ``caused_by_event_ids`` is 160 characters of free text per entry as far as the domain
    model is concerned: the model checks uniqueness and self-reference, not shape. The
    clause that refuses an unreadable one was the only thing standing between that text
    and an index into a parse result that is ``None``.
    """
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch, match="causal"):
        store.append_events([event_factory(0, caused_by_event_ids=("not-an-event-identifier",))])


def test_a_causal_reference_to_an_earlier_event_of_the_same_batch_is_accepted(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(
        [event_factory(0), event_factory(1, caused_by_event_ids=("run-storage:event-00000000",))]
    )

    assert len(store.load_run(run_manifest.run_id).events) == 2


def test_a_causal_reference_to_a_later_event_of_the_same_batch_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A cause must already have happened; forward causality is not causality."""
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch, match="causal"):
        store.append_events(
            [
                event_factory(0, caused_by_event_ids=("run-storage:event-00000001",)),
                event_factory(1),
            ]
        )


def test_an_event_identifier_that_is_not_the_stable_identifier_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """``(run_id, sequence)`` addresses the event; a free-form id breaks causal lookup."""
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch, match="stable"):
        store.append_events([event_factory(0, event_id="something-else")])


def test_the_store_agrees_with_the_engine_about_the_stable_event_identifier() -> None:
    """Guard against the two spellings of one identifier format drifting apart."""
    assert parse_stable_event_id(stable_event_id("run-storage", 7)) == ("run-storage", 7)
    assert parse_stable_event_id("run-storage:event-7") is None
    assert parse_stable_event_id("run-storage:event-00000007x") is None


def test_an_event_for_an_unknown_run_is_refused(
    store: SQLiteRunStore, event_factory: Callable[..., DomainEvent]
) -> None:
    with pytest.raises(RunNotFound):
        store.append_events([event_factory(0)])


def test_a_batch_mixing_two_runs_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch, match="one run"):
        store.append_events([event_factory(0), event_factory(1, run_id="run-other")])


def test_an_empty_batch_is_accepted_and_changes_nothing(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events([])

    assert store.load_run(run_manifest.run_id).events == ()


def test_a_payload_that_reads_as_a_credential_is_refused_before_it_is_stored(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(InvalidEventBatch, match="credential"):
        store.append_events([event_factory(0, payload={"api_key": "0000abcdef1234567890"})])

    assert store.load_run(run_manifest.run_id).events == ()


def test_creating_the_same_run_twice_is_refused(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(DuplicateRun):
        store.create_run(run_manifest, scenario=valid_scenario)


def test_a_manifest_whose_scenario_digest_does_not_address_its_inputs_is_refused(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """The stored inputs must provably be the inputs the manifest claims."""
    wrong = run_manifest.model_copy(update={"scenario_hash": "e" * 64})

    with pytest.raises(StorageError, match="scenario"):
        store.create_run(wrong, scenario=valid_scenario)

    assert not (store.root / "runs" / wrong.run_id).exists()


def test_loading_an_unknown_run_is_refused(store: SQLiteRunStore) -> None:
    with pytest.raises(RunNotFound):
        store.load_run("run-missing")


@pytest.mark.parametrize(
    "hostile_run_id",
    [
        "..",
        "../escape",
        "../../escape",
        "runs/../../escape",
        "a/b",
        "a\\b",
        "..\\escape",
        "/absolute",
        "C:\\Windows",
        "run-storage\x00",
        "RUN-UPPER",
        "-leading-dash",
        "con",
        "aux",
        "nul",
        "lpt1",
        "",
    ],
)
def test_a_hostile_run_identifier_never_reaches_the_filesystem(
    store: SQLiteRunStore, hostile_run_id: str
) -> None:
    """Every artifact path is a run identifier, so the identifier is the guard."""
    with pytest.raises(StorageError):
        store.load_run(hostile_run_id)

    assert sorted(path.name for path in store.root.iterdir()) in ([], ["runs"])


def test_a_checkpoint_is_stored_and_read_back_unchanged(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    consumer_state: ConsumerState,
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    checkpoint = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=1440,
        next_event_sequence=0,
        states=(consumer_state,),
    )

    store.save_checkpoint(checkpoint)

    assert store.load_run(run_manifest.run_id).checkpoints == (checkpoint,)


def test_a_checkpoint_away_from_a_day_boundary_is_refused(
    run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    """Resume is only safe where the day-scoped accumulators are known to be empty."""
    with pytest.raises(ValueError, match="day boundary"):
        RunCheckpoint(
            run_id=run_manifest.run_id,
            simulated_minute=1380,
            next_event_sequence=0,
            states=(consumer_state,),
        )


def test_a_checkpoint_for_an_unknown_run_is_refused(
    store: SQLiteRunStore, run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    checkpoint = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=0,
        next_event_sequence=0,
        states=(consumer_state,),
    )

    with pytest.raises(RunNotFound):
        store.save_checkpoint(checkpoint)


def test_a_checkpoint_never_claims_a_sequence_the_run_has_not_reached(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    consumer_state: ConsumerState,
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(StorageError, match="sequence"):
        store.save_checkpoint(
            RunCheckpoint(
                run_id=run_manifest.run_id,
                simulated_minute=1440,
                next_event_sequence=9,
                states=(consumer_state,),
            )
        )


def test_a_second_checkpoint_for_the_same_minute_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    consumer_state: ConsumerState,
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    checkpoint = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=1440,
        next_event_sequence=0,
        states=(consumer_state,),
    )
    store.save_checkpoint(checkpoint)

    with pytest.raises(StorageError, match="checkpoint"):
        store.save_checkpoint(checkpoint)


def test_completing_a_run_records_its_result_and_status(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(domain_events)
    result = SimulationResult(
        run_id=run_manifest.run_id,
        status="completed",
        final_minute=15,
        event_count=len(domain_events),
        metrics={"notice_rate": 0.5, "average_recall": 0.125},
    )

    store.complete_run(result)

    loaded = store.load_run(run_manifest.run_id)
    assert loaded.result == result
    assert loaded.status == "completed"
    assert loaded.completed_at is not None


def test_a_failed_run_is_recorded_with_its_reason(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """Replay has to reproduce the failure path, so the failure path is persisted too."""
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(
        [
            event_factory(0, event_type=EventType.RUN_STARTED, agent_id=None),
            event_factory(
                1,
                event_type=EventType.COGNITION_FALLBACK,
                source=EventSource.FALLBACK,
                payload={"fallback_reason": "provider-unavailable"},
            ),
            event_factory(2, event_type=EventType.RUN_FAILED, agent_id=None),
        ]
    )
    result = SimulationResult(
        run_id=run_manifest.run_id,
        status="failed",
        final_minute=15,
        event_count=3,
        failure_reason="the configured provider was unreachable",
    )

    store.complete_run(result)

    loaded = store.load_run(run_manifest.run_id)
    assert loaded.status == "failed"
    assert loaded.result is not None
    assert loaded.result.failure_reason == "the configured provider was unreachable"


def test_a_result_whose_event_count_disagrees_with_the_stored_events_is_refused(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(domain_events)

    with pytest.raises(StorageError, match="event_count"):
        store.complete_run(
            SimulationResult(
                run_id=run_manifest.run_id,
                status="completed",
                final_minute=15,
                event_count=99,
            )
        )


def test_a_result_for_another_run_is_refused(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(RunNotFound):
        store.complete_run(
            SimulationResult(run_id="run-other", status="completed", final_minute=0, event_count=0)
        )


def test_completing_a_run_twice_is_refused(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    result = SimulationResult(
        run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
    )
    store.complete_run(result)

    with pytest.raises(RunAlreadyComplete):
        store.complete_run(result)


def test_a_finished_run_accepts_no_further_events(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    event_factory: Callable[..., DomainEvent],
) -> None:
    """A completed artifact is immutable; otherwise its recorded result is a lie."""
    store.create_run(run_manifest, scenario=valid_scenario)
    store.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
        )
    )

    with pytest.raises(RunAlreadyComplete):
        store.append_events([event_factory(0)])


def test_a_finished_run_accepts_no_further_checkpoint(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    consumer_state: ConsumerState,
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.complete_run(
        SimulationResult(
            run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
        )
    )

    with pytest.raises(RunAlreadyComplete):
        store.save_checkpoint(
            RunCheckpoint(
                run_id=run_manifest.run_id,
                simulated_minute=0,
                next_event_sequence=0,
                states=(consumer_state,),
            )
        )


def test_provider_usage_is_stored_and_read_back_unchanged(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    log = ProviderUsageLog(
        run_id=run_manifest.run_id,
        records=(
            ProviderUsage(
                provider_kind="mock",
                model_id="mock-v1",
                prompt_tokens=120,
                completion_tokens=40,
                latency_ms=12,
            ),
            ProviderUsage(
                provider_kind="fallback",
                model_id="rules-v1",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                fallback_reason="provider-unavailable",
            ),
        ),
    )

    store.save_provider_usage(log)

    assert store.load_provider_usage(run_manifest.run_id) == log


def test_provider_usage_for_another_run_is_refused(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    with pytest.raises(RunNotFound):
        store.save_provider_usage(ProviderUsageLog(run_id="run-other", records=()))


def usage_records(count: int) -> tuple[ProviderUsage, ...]:
    """``count`` identical usage records, the cheapest shape the model accepts."""
    return tuple(
        ProviderUsage(
            provider_kind="mock",
            model_id="mock-v1",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        )
        for _ in range(count)
    )


def test_a_provider_usage_log_at_the_documented_record_bound_is_accepted() -> None:
    """The bound must admit the largest legitimate log, or it is the wrong bound."""
    log = ProviderUsageLog(run_id="run-storage", records=usage_records(MAX_PROVIDER_USAGE_RECORDS))

    assert len(log.records) == MAX_PROVIDER_USAGE_RECORDS


def test_a_provider_usage_log_beyond_the_documented_record_bound_is_refused() -> None:
    """Without the bound the writer can store a document its own reader then refuses."""
    with pytest.raises(ValidationError):
        ProviderUsageLog(
            run_id="run-storage",
            records=usage_records(MAX_PROVIDER_USAGE_RECORDS + 1),
        )


def test_a_stored_usage_document_beyond_the_record_bound_is_refused_on_read(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """The bound is the READER's too: an oversized document on disk is not a usage log.

    This is what makes the write-side bound load-bearing rather than decorative. A
    document with one record more than the model admits is a real file that a store
    without the bound could have written, and it reads back as a typed refusal.
    """
    store.create_run(run_manifest, scenario=valid_scenario)
    record = {
        "schema_version": 1,
        "provider_kind": "mock",
        "model_id": "mock-v1",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": 0,
        "fallback_reason": None,
    }
    store.provider_usage_json_path(run_manifest.run_id).write_bytes(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_manifest.run_id,
                "records": [record] * (MAX_PROVIDER_USAGE_RECORDS + 1),
            }
        ).encode("utf-8")
    )

    with pytest.raises(CorruptRunArtifact, match=r"provider-usage\.json"):
        store.load_provider_usage(run_manifest.run_id)


def test_the_documented_read_bounds_are_the_numbers_this_port_publishes() -> None:
    """Both are MEMORY bounds, and a memory bound that nothing states can be relaxed.

    Neither number was named anywhere in the tests: multiplying either by a thousand left
    the whole suite green, which makes the docstrings beside them a description of an
    intention rather than of this build. ``MAX_CHECKPOINTS`` is derived here from the
    scenario model's own day bound rather than restated, because that is where the eight
    comes from - one checkpoint per day boundary of the longest run the model admits,
    plus the start of the run.
    """
    assert MAX_LOADED_EVENTS == 500_000
    assert MAX_CHECKPOINTS == 8
    longest_run_in_days = next(
        constraint.le
        for constraint in Scenario.model_fields["days"].metadata
        if getattr(constraint, "le", None) is not None
    )
    assert longest_run_in_days + 1 == MAX_CHECKPOINTS


def test_the_event_ceiling_is_the_default_a_caller_that_names_none_gets() -> None:
    """The ceiling is only a ceiling if it is what an unqualified read applies.

    ``load_run(run_id)`` is how every caller in this repository reads a run, so a default
    wired to anything else would leave the bound tested and unused.
    """
    for read in (SQLiteRunStore.load_run, RunStore.load_run):
        assert inspect.signature(read).parameters["max_events"].default == MAX_LOADED_EVENTS


def test_loading_more_events_than_the_caller_allows_is_refused_rather_than_read(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
) -> None:
    """A stored run is unbounded; reading one into memory must not be."""
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(domain_events)

    with pytest.raises(RunTooLargeToLoad, match="2"):
        store.load_run(run_manifest.run_id, max_events=2)


def test_events_can_be_streamed_without_loading_the_whole_run(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(domain_events)

    assert list(store.iter_events(run_manifest.run_id)) == list(domain_events)


def test_streaming_can_resume_from_a_sequence(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    domain_events: tuple[DomainEvent, ...],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(domain_events)

    streamed = list(store.iter_events(run_manifest.run_id, start_sequence=2))

    assert [event.sequence for event in streamed] == [2]


# --- the port validators, tripped one clause at a time --------------------------------
#
# Every clause below is an anti-escape rule on a model that Task 12 and the replay command
# construct DIRECTLY, not only through the SQLite store. The store checks the same things
# first and reports them better, so without these tests each clause would be decoration:
# deleting it would leave the suite green while admitting an inconsistent stored run.

CREATED_AT = "2026-09-15T08:00:00.000000Z"


def a_stored_run(**overrides: object) -> StoredRun:
    return StoredRun.model_validate({"created_at": CREATED_AT, **overrides})


def test_a_checkpoint_with_two_states_for_one_agent_is_refused(
    run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    with pytest.raises(ValueError, match="duplicate agent_id"):
        RunCheckpoint(
            run_id=run_manifest.run_id,
            simulated_minute=0,
            next_event_sequence=0,
            states=(consumer_state, consumer_state),
        )


def test_a_checkpoint_whose_states_are_not_ordered_is_refused(
    run_manifest: RunManifest, consumer_state: ConsumerState
) -> None:
    """Unordered states would serialize differently run to run and break replay."""
    other = consumer_state.model_copy(update={"agent_id": "person-002"})

    with pytest.raises(ValueError, match="ordered by agent_id"):
        RunCheckpoint(
            run_id=run_manifest.run_id,
            simulated_minute=0,
            next_event_sequence=0,
            states=(other, consumer_state),
        )


def test_stored_inputs_that_are_not_the_scenario_the_manifest_names_are_refused(
    run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    with pytest.raises(ValueError, match="stored inputs"):
        a_stored_run(
            manifest=run_manifest.model_copy(update={"scenario_id": "scenario-other"}),
            scenario=valid_scenario,
            status="running",
        )


def test_a_stored_event_from_another_run_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario, event_factory: Callable[..., DomainEvent]
) -> None:
    with pytest.raises(ValueError, match="another run"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="running",
            events=(event_factory(0, run_id="run-other"),),
        )


def test_stored_events_with_a_gap_are_refused(
    run_manifest: RunManifest, valid_scenario: Scenario, event_factory: Callable[..., DomainEvent]
) -> None:
    with pytest.raises(ValueError, match="contiguous"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="running",
            events=(event_factory(0), event_factory(2)),
        )


def test_stored_checkpoints_out_of_order_are_refused(
    run_manifest: RunManifest, valid_scenario: Scenario, consumer_state: ConsumerState
) -> None:
    late = RunCheckpoint(
        run_id=run_manifest.run_id,
        simulated_minute=1440,
        next_event_sequence=0,
        states=(consumer_state,),
    )
    early = late.model_copy(update={"simulated_minute": 0})

    with pytest.raises(ValueError, match="ascend"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="running",
            checkpoints=(late, early),
        )


def test_a_stored_checkpoint_from_another_run_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario, consumer_state: ConsumerState
) -> None:
    with pytest.raises(ValueError, match="another run"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="running",
            checkpoints=(
                RunCheckpoint(
                    run_id="run-other",
                    simulated_minute=0,
                    next_event_sequence=0,
                    states=(consumer_state,),
                ),
            ),
        )


def test_a_finished_stored_run_without_a_result_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    with pytest.raises(ValueError, match="carries a result"):
        a_stored_run(manifest=run_manifest, scenario=valid_scenario, status="completed")


def test_a_running_stored_run_carrying_a_result_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    with pytest.raises(ValueError, match="carries a result"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="running",
            completed_at=CREATED_AT,
            result=SimulationResult(
                run_id=run_manifest.run_id, status="interrupted", final_minute=0, event_count=0
            ),
        )


def test_a_finished_stored_run_without_a_completion_time_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    with pytest.raises(ValueError, match="completion time"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="completed",
            result=SimulationResult(
                run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
            ),
        )


def test_a_stored_result_for_another_run_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    with pytest.raises(ValueError, match="another run"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="completed",
            completed_at=CREATED_AT,
            result=SimulationResult(
                run_id="run-other", status="completed", final_minute=0, event_count=0
            ),
        )


def test_a_stored_result_contradicting_the_stored_status_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    with pytest.raises(ValueError, match="contradicts"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="interrupted",
            completed_at=CREATED_AT,
            result=SimulationResult(
                run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
            ),
        )


def test_a_stored_result_disagreeing_with_the_stored_event_count_is_refused(
    run_manifest: RunManifest, valid_scenario: Scenario, event_factory: Callable[..., DomainEvent]
) -> None:
    with pytest.raises(ValueError, match="event count"):
        a_stored_run(
            manifest=run_manifest,
            scenario=valid_scenario,
            status="completed",
            completed_at=CREATED_AT,
            events=(event_factory(0),),
            result=SimulationResult(
                run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
            ),
        )


@pytest.mark.parametrize("not_a_batch", ["run-storage", b"run-storage", 7])
def test_a_batch_that_is_not_a_sequence_of_events_is_refused(not_a_batch: object) -> None:
    with pytest.raises(InvalidEventBatch, match="sequence of DomainEvent"):
        batch_run_id(not_a_batch)  # type: ignore[arg-type]


def test_an_empty_batch_names_no_run() -> None:
    with pytest.raises(InvalidEventBatch, match="names no run"):
        batch_run_id([])


def test_a_batch_member_that_is_not_an_event_is_refused() -> None:
    with pytest.raises(InvalidEventBatch, match="not a DomainEvent"):
        batch_run_id(["not-an-event"])  # type: ignore[list-item]


def test_the_store_refuses_a_batch_that_is_not_a_sequence(store: SQLiteRunStore) -> None:
    with pytest.raises(InvalidEventBatch, match="sequence of DomainEvent"):
        store.append_events("run-storage")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("method", "argument"),
    [
        ("create_run", "not-a-manifest"),
        ("save_checkpoint", "not-a-checkpoint"),
        ("save_provider_usage", "not-a-log"),
        ("complete_run", "not-a-result"),
    ],
)
def test_the_store_refuses_a_document_of_the_wrong_type(
    store: SQLiteRunStore, valid_scenario: Scenario, method: str, argument: object
) -> None:
    call = getattr(store, method)
    with pytest.raises(TypeError):
        if method == "create_run":
            call(argument, scenario=valid_scenario)
        else:
            call(argument)


def test_the_store_refuses_a_scenario_of_the_wrong_type(
    store: SQLiteRunStore, run_manifest: RunManifest
) -> None:
    with pytest.raises(TypeError, match="Scenario"):
        store.create_run(run_manifest, scenario="not-a-scenario")  # type: ignore[arg-type]


def test_the_store_refuses_a_root_that_is_not_a_path() -> None:
    with pytest.raises(TypeError, match="Path"):
        SQLiteRunStore("runs")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_bound", [-1, "many"])
def test_a_read_bound_that_is_not_a_count_is_refused(
    store: SQLiteRunStore, bad_bound: object
) -> None:
    with pytest.raises(ValueError, match="max_events"):
        store.load_run("run-storage", max_events=bad_bound)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_start", [-1, "first"])
def test_a_stream_start_that_is_not_a_sequence_number_is_refused(
    store: SQLiteRunStore, bad_start: object
) -> None:
    with pytest.raises(ValueError, match="start_sequence"):
        list(store.iter_events("run-storage", start_sequence=bad_start))  # type: ignore[arg-type]
