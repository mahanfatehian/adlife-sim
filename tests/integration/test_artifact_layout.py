"""The on-disk run directory: its exact shape, how it appears, and where it may not go.

Specification section 14 fixes the layout of a completed run, so this module asserts the
layout literally. The rest is about the two ways an artifact directory goes wrong: it is
published half-built, or it is addressed by something that is not a run identifier and
ends up outside the project root. Both are tested by making them happen.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from adlife.adapters.storage import sqlite_store as sqlite_store_module
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.events import DomainEvent, EventSource, EventType
from adlife.core.domain.results import RunManifest, SimulationResult
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.cognition import ProviderUsage
from adlife.core.ports.run_store import ProviderUsageLog, StorageError, UnsafeRunLocation
from adlife.core.simulation.engine import canonical_sha256

RUN_DIRECTORY_ENTRIES = {
    "run.json",
    "inputs",
    "events.jsonl",
    "results.sqlite3",
    "metrics.json",
    "provider-usage.json",
}


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def store(project_root: Path) -> SQLiteRunStore:
    return SQLiteRunStore(project_root)


@pytest.fixture
def recorded_events(event_factory: Callable[..., DomainEvent]) -> tuple[DomainEvent, ...]:
    """A run that reached the fallback path and then failed: replay must reproduce both."""
    return (
        event_factory(0, event_type=EventType.RUN_STARTED, agent_id=None),
        event_factory(
            1,
            simulated_minute=15,
            event_type=EventType.COGNITION_REQUESTED,
            campaign_id="campaign-phone",
            channel="mobile-feed",
            caused_by_event_ids=("run-storage:event-00000000",),
        ),
        event_factory(
            2,
            simulated_minute=15,
            event_type=EventType.COGNITION_FALLBACK,
            source=EventSource.FALLBACK,
            campaign_id="campaign-phone",
            channel="mobile-feed",
            payload={"fallback_reason": "provider-unavailable", "attempts": 3},
            caused_by_event_ids=("run-storage:event-00000001",),
        ),
        event_factory(3, simulated_minute=30, event_type=EventType.RUN_FAILED, agent_id=None),
    )


def write_a_complete_run(
    store: SQLiteRunStore,
    manifest: RunManifest,
    scenario: Scenario,
    events: tuple[DomainEvent, ...],
) -> SimulationResult:
    store.create_run(manifest, scenario=scenario)
    store.append_events(events)
    store.save_provider_usage(
        ProviderUsageLog(
            run_id=manifest.run_id,
            records=(
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
    )
    result = SimulationResult(
        run_id=manifest.run_id,
        status="failed",
        final_minute=30,
        event_count=len(events),
        metrics={"notice_rate": 0.0, "fallbacks": 1.0},
        failure_reason="the configured provider was unreachable",
    )
    store.complete_run(result)
    return result


def test_a_created_run_directory_holds_exactly_the_documented_entries(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    directory = store.root / "runs" / run_manifest.run_id
    assert {path.name for path in directory.iterdir()} == RUN_DIRECTORY_ENTRIES


def test_a_completed_run_directory_still_holds_exactly_the_documented_entries(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    write_a_complete_run(store, run_manifest, valid_scenario, recorded_events)

    directory = store.root / "runs" / run_manifest.run_id
    names = {path.name for path in directory.iterdir()}
    assert names >= RUN_DIRECTORY_ENTRIES
    assert names - RUN_DIRECTORY_ENTRIES <= {"results.sqlite3-wal", "results.sqlite3-shm"}


def test_run_json_is_the_canonical_manifest_and_nothing_else(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """Wall-clock metadata lives in the database, so run.json stays byte-reproducible."""
    store.create_run(run_manifest, scenario=valid_scenario)

    text = store.run_json_path(run_manifest.run_id).read_text(encoding="utf-8")
    assert text == canonical_json(run_manifest)
    assert "created_at" not in text


def test_the_stored_inputs_are_the_inputs_the_manifest_addresses(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)

    stored = Scenario.model_validate_json(
        store.scenario_json_path(run_manifest.run_id).read_text(encoding="utf-8")
    )
    assert stored == valid_scenario
    assert canonical_sha256(stored) == run_manifest.scenario_hash


def test_the_export_and_the_two_json_reports_exist_from_the_first_moment(
    store: SQLiteRunStore, run_manifest: RunManifest, valid_scenario: Scenario
) -> None:
    """An interrupted run still leaves a complete, readable directory."""
    store.create_run(run_manifest, scenario=valid_scenario)

    assert store.events_jsonl_path(run_manifest.run_id).read_bytes() == b""
    metrics = json.loads(store.metrics_json_path(run_manifest.run_id).read_text("utf-8"))
    assert metrics["metrics"] == {}
    usage = json.loads(store.provider_usage_json_path(run_manifest.run_id).read_text("utf-8"))
    assert usage["records"] == []


def test_the_export_grows_by_one_line_per_appended_event(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    store.append_events(recorded_events[:2])
    store.append_events(recorded_events[2:])

    lines = store.events_jsonl_path(run_manifest.run_id).read_text("utf-8").splitlines()
    assert len(lines) == len(recorded_events)


def test_metrics_json_records_the_completed_result_with_sorted_keys(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    result = write_a_complete_run(store, run_manifest, valid_scenario, recorded_events)

    text = store.metrics_json_path(run_manifest.run_id).read_text(encoding="utf-8")
    assert text == canonical_json(result)
    assert text.index('"fallbacks"') < text.index('"notice_rate"')


def test_provider_usage_json_records_every_answer_the_run_paid_for(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    write_a_complete_run(store, run_manifest, valid_scenario, recorded_events)

    usage = json.loads(store.provider_usage_json_path(run_manifest.run_id).read_text("utf-8"))
    assert [record["fallback_reason"] for record in usage["records"]] == ["provider-unavailable"]


def test_a_run_directory_is_never_published_before_it_is_complete(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The directory is built as a sibling and moved into place in one step."""

    def refuse(temporary: Path, final: Path) -> None:
        raise OSError("the move failed")

    monkeypatch.setattr(sqlite_store_module, "publish_directory", refuse)

    with pytest.raises(StorageError):
        store.create_run(run_manifest, scenario=valid_scenario)

    assert not (store.root / "runs" / run_manifest.run_id).exists()
    assert list((store.root / "runs").iterdir()) == []


def test_a_failed_document_write_leaves_the_previous_document_intact(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.create_run(run_manifest, scenario=valid_scenario)
    before = store.metrics_json_path(run_manifest.run_id).read_bytes()

    def refuse(path: Path, text: str) -> None:
        raise OSError("the device is full")

    monkeypatch.setattr(sqlite_store_module, "write_document_atomically", refuse)

    with pytest.raises(StorageError):
        store.complete_run(
            SimulationResult(
                run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
            )
        )

    assert store.metrics_json_path(run_manifest.run_id).read_bytes() == before


def test_a_leftover_temporary_sibling_is_not_part_of_any_run(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kill during create_run leaves a sibling; it must never be read as a run.

    The sibling's name is taken FROM THE STORE rather than invented here. A hand-written
    name only proves that a string with a dot in it is not a run identifier; it proves
    nothing about the name this store actually leaves behind, which is the name a reader
    would have to refuse.
    """
    store.create_run(run_manifest, scenario=valid_scenario)
    captured: list[Path] = []

    def never_publish(temporary: Path, final: Path) -> None:
        captured.append(temporary)

    monkeypatch.setattr(sqlite_store_module, "publish_directory", never_publish)
    unpublished = run_manifest.model_copy(update={"run_id": "run-interrupted"})
    store.create_run(unpublished, scenario=valid_scenario)
    monkeypatch.undo()
    leftover = captured[0]
    assert leftover.is_dir()

    with pytest.raises(StorageError):
        store.load_run(leftover.name)
    with pytest.raises(UnsafeRunLocation):
        store.run_directory(leftover.name)

    assert store.load_run(run_manifest.run_id).manifest == run_manifest


def test_the_temporary_sibling_a_kill_leaves_can_never_name_a_run_directory(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sibling is built INSIDE runs/, so its name must be one no run can have.

    A sibling named like a run identifier is not merely untidy. ``create_run`` builds its
    sibling first and removes it on any failure, so a sibling whose name collided with an
    existing published run would send the cleanup path at that run's directory and delete
    a completed artifact - and a leftover from a kill would reserve a run identifier that
    can then never be created. Both follow from the name alone.
    """
    captured: list[Path] = []

    def never_publish(temporary: Path, final: Path) -> None:
        captured.append(temporary)

    monkeypatch.setattr(sqlite_store_module, "publish_directory", never_publish)
    store.create_run(run_manifest, scenario=valid_scenario)
    leftover = captured[0]

    with pytest.raises(UnsafeRunLocation):
        store.run_directory(leftover.name)

    assert leftover.parent == store.root / "runs"


def test_a_failed_metrics_write_leaves_the_run_completable(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """metrics.json is published BEFORE the database records the outcome.

    Reversed, a failed document write would leave the database saying "completed" while
    metrics.json still held the placeholder, and every later read of the run would refuse
    it as corrupt. The run must instead still be open, and still be completable.
    """
    store.create_run(run_manifest, scenario=valid_scenario)
    result = SimulationResult(
        run_id=run_manifest.run_id, status="completed", final_minute=0, event_count=0
    )

    def refuse(path: Path, text: str) -> None:
        raise OSError("the device is full")

    monkeypatch.setattr(sqlite_store_module, "write_document_atomically", refuse)
    with pytest.raises(StorageError):
        store.complete_run(result)
    monkeypatch.undo()

    assert store.load_run(run_manifest.run_id).status == "running"

    store.complete_run(result)

    assert store.load_run(run_manifest.run_id).status == "completed"


@pytest.mark.parametrize(
    "hostile",
    ["..", "../escape", "runs/../../escape", "/etc", "C:\\Windows", "a\\b", "a/b", "RUN"],
)
def test_no_artifact_path_can_be_addressed_outside_the_project_root(
    store: SQLiteRunStore, hostile: str
) -> None:
    for accessor in (
        store.run_directory,
        store.run_json_path,
        store.database_path,
        store.events_jsonl_path,
        store.metrics_json_path,
        store.provider_usage_json_path,
        store.scenario_json_path,
    ):
        with pytest.raises(StorageError):
            accessor(hostile)


def test_the_store_writes_nothing_outside_the_project_root(
    store: SQLiteRunStore,
    project_root: Path,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    write_a_complete_run(store, run_manifest, valid_scenario, recorded_events)

    assert [path.name for path in project_root.parent.iterdir()] == ["project"]
    assert [path.name for path in project_root.iterdir()] == ["runs"]


def test_a_recorded_run_replays_into_a_byte_identical_artifact(
    store: SQLiteRunStore,
    tmp_path: Path,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    """Replay of a failed, fallback-carrying run reproduces every portable artifact byte."""
    result = write_a_complete_run(store, run_manifest, valid_scenario, recorded_events)
    recorded = store.load_run(run_manifest.run_id)

    replay_root = tmp_path / "replay"
    replay_root.mkdir()
    replay = SQLiteRunStore(replay_root)
    replay.create_run(recorded.manifest, scenario=recorded.scenario)
    replay.append_events(recorded.events)
    replay.save_provider_usage(store.load_provider_usage(run_manifest.run_id))
    replay.complete_run(result)

    for name in ("run.json", "events.jsonl", "metrics.json", "provider-usage.json"):
        original = (store.root / "runs" / run_manifest.run_id / name).read_bytes()
        replayed = (replay_root / "runs" / run_manifest.run_id / name).read_bytes()
        assert replayed == original, name
    original_inputs = store.scenario_json_path(run_manifest.run_id).read_bytes()
    assert replay.scenario_json_path(run_manifest.run_id).read_bytes() == original_inputs


def test_a_replayed_run_loads_back_as_the_same_run(
    store: SQLiteRunStore,
    tmp_path: Path,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    result = write_a_complete_run(store, run_manifest, valid_scenario, recorded_events)
    recorded = store.load_run(run_manifest.run_id)

    replay_root = tmp_path / "replay"
    replay_root.mkdir()
    replay = SQLiteRunStore(replay_root)
    replay.create_run(recorded.manifest, scenario=recorded.scenario)
    replay.append_events(recorded.events)
    replay.complete_run(result)
    replayed = replay.load_run(run_manifest.run_id)

    assert replayed.model_dump(exclude={"created_at", "completed_at"}) == recorded.model_dump(
        exclude={"created_at", "completed_at"}
    )


def test_the_wall_clock_metadata_is_iso_utc_and_kept_out_of_simulated_time(
    store: SQLiteRunStore,
    run_manifest: RunManifest,
    valid_scenario: Scenario,
    recorded_events: tuple[DomainEvent, ...],
) -> None:
    write_a_complete_run(store, run_manifest, valid_scenario, recorded_events)

    loaded = store.load_run(run_manifest.run_id)
    assert loaded.created_at.endswith("Z")
    assert loaded.completed_at is not None and loaded.completed_at.endswith("Z")
    assert loaded.result is not None and loaded.result.final_minute == 30
