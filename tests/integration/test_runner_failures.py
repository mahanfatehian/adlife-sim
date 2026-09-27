from pathlib import Path

import pytest

from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.run_store import StorageError
from adlife.core.simulation.runner import InterruptedRun, RunnerRefused, run_id_from
from tests.integration.test_rule_run import build_runner


def test_default_core_run_ids_preserve_seed_identity_for_long_scenarios(valid_scenario):
    scenario = valid_scenario.model_copy(update={"scenario_id": "s" * 40})
    first = run_id_from(scenario, 42)
    assert first != run_id_from(scenario, 43)
    assert first == run_id_from(scenario, 42)
    assert len(first) <= 40


@pytest.mark.parametrize("tamper", ["scenario", "manifest", "parameters", "tail"])
async def test_resume_refuses_unmatched_inputs_or_uncheckpointed_tail(
    valid_scenario, run_manifest, tmp_path, tamper, event_factory
):
    from adlife.core.simulation.engine import AdLifeModel
    from adlife.core.simulation.parameters import ModelParameters

    store = SQLiteRunStore(tmp_path)
    store.create_run(run_manifest, scenario=valid_scenario)
    model = AdLifeModel(scenario=valid_scenario, seed=42)
    store.append_events(model.start_events(run_manifest.run_id))
    scenario, manifest, parameters = valid_scenario, run_manifest, None
    if tamper == "scenario":
        scenario = scenario.model_copy(update={"name": "Altered frozen input"})
    elif tamper == "manifest":
        manifest = manifest.model_copy(update={"model_id": "another-model"})
    elif tamper == "parameters":
        parameters = ModelParameters().with_("notice_scale", 0.5)
    else:
        store.append_events((event_factory(1),))
    before = store.load_run(run_manifest.run_id)
    runner, _ = build_runner(store, [])
    with pytest.raises(RunnerRefused):
        await runner.resume(
            scenario,
            seed=42,
            store=store,
            sinks=(),
            run_id=run_manifest.run_id,
            manifest=manifest,
            parameters=parameters,
        )
    assert store.load_run(run_manifest.run_id) == before


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, RuntimeError])
async def test_failure_before_first_tick_is_recorded(small_scenario, tmp_path, error_type):
    store = SQLiteRunStore(tmp_path)

    def broken_factory():
        raise error_type("opaque-private-value")

    runner, _ = build_runner(store, [], resolve_factory=broken_factory)
    expected = InterruptedRun if error_type is KeyboardInterrupt else RuntimeError
    with pytest.raises(expected):
        await runner.run(small_scenario, seed=42, store=store, sinks=(), run_id="early-stop")
    stored = store.load_run("early-stop")
    assert stored.status == ("interrupted" if error_type is KeyboardInterrupt else "failed")
    assert stored.result.final_minute == 0
    assert "opaque-private-value" not in stored.result.model_dump_json()


async def test_storage_failure_cannot_be_returned_as_a_successful_call(
    small_scenario: Scenario, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteRunStore(tmp_path)
    append = store.append_events
    failed = False

    def fail_tick(self, events):
        nonlocal failed
        if events and events[0].sequence > 0:
            failed = True
            raise StorageError("injected disk failure")
        append(events)

    monkeypatch.setattr(SQLiteRunStore, "append_events", fail_tick)
    runner, _ = build_runner(store, [])
    with pytest.raises(StorageError, match="injected disk failure"):
        await runner.run(small_scenario, seed=42, store=store, sinks=(), run_id="disk-failure")
    assert failed
    stored = store.load_run("disk-failure")
    assert stored.status == "running"
    assert len(stored.events) == 1


async def test_observer_exception_cannot_change_a_run(
    small_scenario: Scenario, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SQLiteRunStore(tmp_path / "observed")
    runner, _ = build_runner(store, [])

    async def broken_view(plan, outcome, model):
        raise RuntimeError("opaque-private-value")

    result = await runner.run(
        small_scenario,
        seed=42,
        store=store,
        sinks=(),
        run_id="observed",
        tick_observer=broken_view,
    )
    plain_store = SQLiteRunStore(tmp_path / "plain")
    plain, _ = build_runner(plain_store, [])
    expected = await plain.run(
        small_scenario, seed=42, store=plain_store, sinks=(), run_id="observed"
    )
    assert result == expected
    assert store.load_run("observed").events == plain_store.load_run("observed").events
    assert "opaque-private-value" not in capsys.readouterr().err


async def test_interrupt_after_day_commit_keeps_checkpoint_and_boundary_time(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    store = SQLiteRunStore(tmp_path)
    runner, _ = build_runner(store, [])

    async def interrupt_after_day(plan, outcome, model):
        if outcome.ends_simulated_day:
            raise KeyboardInterrupt()

    with pytest.raises(InterruptedRun):
        await runner.run(
            small_scenario.model_copy(update={"days": 2}),
            seed=42,
            store=store,
            sinks=(),
            run_id="interrupted-day",
            tick_observer=interrupt_after_day,
        )
    stored = store.load_run("interrupted-day")
    assert stored.status == "interrupted"
    assert stored.result.final_minute == 1440
    assert stored.checkpoints[-1].simulated_minute == 1440
    assert stored.checkpoints[-1].next_event_sequence == len(stored.events)
