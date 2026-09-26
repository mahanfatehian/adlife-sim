"""The performance envelope: 30 agents for seven days, timed and bounded.

The plan's target is 10 seconds on a four-core development laptop; CI asserts a hard
20-second regression threshold so runner noise cannot flake the gate. The gate is on
the WHOLE orchestrated run - the real runner, store and sinks - not on the engine in
isolation. Set ``ADLIFE_SKIP_LONG_TESTS=1`` to skip it during quick iteration.

Timing and memory are two separate passes, and the timed pass is untraced: both
:mod:`tracemalloc` and :mod:`coverage` roughly triple the run's wall time, so a
ceiling asserted under either would measure the tracer, not the engine. The timed
pass suspends the active coverage collector (if any); a second traced pass records
peak memory for the evidence line.
"""

from __future__ import annotations

import contextlib
import os
import time
import tracemalloc
from pathlib import Path

import pytest

from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.cli.cognition import RuleCognitionPort, rule_fallback_for
from adlife.core.domain.scenario import Scenario
from adlife.core.simulation.runner import RunIdentity, SimulationRunner
from tests.builders import maximum_thirty_agent_scenario

if os.environ.get("ADLIFE_SKIP_LONG_TESTS") == "1":
    pytest.skip("ADLIFE_SKIP_LONG_TESTS is set", allow_module_level=True)

HARD_CEILING_SECONDS = 20.0
TARGET_SECONDS = 10.0


def _ceiling_for_this_machine() -> float:
    """Scale the hard ceiling to the machine's core count, never to wall noise.

    The 20-second ceiling measures a four-core development laptop (the 10-second target
    machine, with margin). A two-vCPU GitHub runner legitimately needs about twice the
    wall time for the same work; failing that machine for being slower - not for a
    regression - makes the gate noise, and a noisy gate protects nothing. The per-core
    scale keeps the gate honest: a true regression shows up on every machine, while a
    slower machine gets the budget its cores justify. The floor is the documented
    ceiling; the environment variable overrides upward for machines known to be slow.
    """
    base = HARD_CEILING_SECONDS * max(1.0, 4.0 / max(1, os.cpu_count() or 1))
    override = os.environ.get("ADLIFE_PERF_CEILING_SECONDS")
    if override:
        with contextlib.suppress(ValueError):
            base = max(base, float(override))
    return base


def _runner(tmp_path: Path) -> SimulationRunner:
    return SimulationRunner(
        cognition_factory=lambda model: RuleCognitionPort(),
        fallback_provider_factory=rule_fallback_for,
        identity=RunIdentity.for_project(
            project_root=tmp_path,
            provider="rules",
            model_id="rule-baseline",
            overrides={"git_sha": "uncommitted", "platform": "perf-platform"},
        ),
    )


async def _drive(
    scenario: Scenario,
    tmp_path: Path,
    *,
    run_id: str,
) -> tuple[SQLiteRunStore, object]:
    store = SQLiteRunStore(tmp_path)
    sinks = [JsonlEventSink(tmp_path / f"{run_id}.jsonl")]
    result = await _runner(tmp_path).run(
        scenario,
        seed=42,
        store=store,
        sinks=sinks,
        run_id=run_id,
        provider_name="rules",
        model_id="rule-baseline",
    )
    return store, result


class _SuspendedCoverage:
    """Stop the active coverage collector for the block, then restart it.

    A no-op when coverage is not running, so the test behaves identically in a plain
    ``pytest`` session and under ``--cov``.
    """

    def __enter__(self) -> None:
        self._collector: object | None = None
        try:
            import coverage

            instance = coverage.Coverage.current()
            if instance is not None and instance._started:
                self._collector = instance
                instance.stop()
        except Exception:
            self._collector = None

    def __exit__(self, *exc_info: object) -> None:
        if self._collector is not None:
            self._collector.start()


async def test_maximum_run_30_agents_7_days_within_the_performance_ceiling(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = maximum_thirty_agent_scenario(valid_scenario)
    assert len(scenario.population) == 30

    # Pass one: the timed gate, untraced even under --cov.
    with _SuspendedCoverage():
        started = time.perf_counter()
        store, result = await _drive(scenario, tmp_path, run_id="max-run")
        elapsed = time.perf_counter() - started

    assert result.status == "completed"
    assert result.final_minute == 7 * 1440
    assert result.event_count > 0

    # Pass two: the memory evidence. Traced, in a fresh store so pass one's artifact
    # is not overwritten.
    tracemalloc.start()
    _store, _traced_result = await _drive(scenario, tmp_path, run_id="max-run-traced")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # The recorded evidence: what actually ran, and what it cost.
    ceiling = _ceiling_for_this_machine()
    print(
        f"\nmaximum run: {elapsed:.2f}s (target <{TARGET_SECONDS}s, ceiling "
        f"<{ceiling:.1f}s), {result.event_count} events, "
        f"peak traced memory {peak / (1024 * 1024):.0f} MiB"
    )
    assert elapsed < ceiling, (
        f"the maximum run took {elapsed:.2f}s, over the {ceiling:.1f}s ceiling"
    )

    # The artifact is complete, not merely fast.
    stored = store.load_run("max-run")
    assert stored.status == "completed"
    assert stored.events[-1].event_type.value == "run.completed"
