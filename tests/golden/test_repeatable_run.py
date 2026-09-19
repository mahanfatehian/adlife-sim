"""Byte-level run repeatability: same seed and scenario, two roots, identical bytes.

The unit suites pin determinism per tick; this one pins it across a WHOLE orchestrated
run, through the real store, so what is byte-identical is the artifact a user keeps -
``events.jsonl`` and ``metrics.json`` - not an in-memory convenience.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from adlife.adapters.cognition.mock import MOCK_MODEL_ID, MockCognitionProvider
from adlife.adapters.output.jsonl import JsonlEventSink
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    SamplingSettings,
)
from adlife.core.simulation.runner import RunIdentity, SimulationRunner
from tests.builders import small_three_agent_scenario


class SeededMockPort:
    def __init__(self, seed: int) -> None:
        self.provider = MockCognitionProvider(seed=seed)

    async def resolve(
        self,
        requests: Sequence[CognitionRequest],
        *,
        fallback_provider: object,
    ) -> dict[str, CognitionAnswer]:
        del fallback_provider
        return {request.request_id: await self.provider.answer(request) for request in requests}

    @property
    def provider_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind="mock",
            model_id=MOCK_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="b" * 64,
        )


def build_runner(root: Path, run_id: str) -> tuple[SQLiteRunStore, SimulationRunner, list]:
    store = SQLiteRunStore(root)
    sinks: list[JsonlEventSink] = [JsonlEventSink(root / f"{run_id}.jsonl")]
    identity = RunIdentity.for_project(
        project_root=root,
        provider="mock",
        model_id=MOCK_MODEL_ID,
        overrides={"git_sha": "uncommitted", "platform": "golden-test-platform"},
    )
    runner = SimulationRunner(
        cognition_factory=lambda model: SeededMockPort(seed=0),
        fallback_provider_factory=lambda plan: _no_fallback(plan),
        identity=identity,
    )
    return store, runner, sinks


def _no_fallback(plan):
    from adlife.adapters.cognition.rules import RuleCognitionProvider

    del plan
    return RuleCognitionProvider({})


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def run_id_for(scenario: Scenario) -> str:
    return "run-golden"


async def test_a_rules_run_is_byte_repeatable_across_roots(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    from tests.builders import strip_campaigns

    scenario = strip_campaigns(small_three_agent_scenario(valid_scenario))
    await _run_once(scenario, tmp_path / "a")
    await _run_once(scenario, tmp_path / "b")

    first_events = read_bytes(tmp_path / "a" / "run-golden.jsonl")
    second_events = read_bytes(tmp_path / "b" / "run-golden.jsonl")
    assert first_events == second_events
    assert b"run-golden" in first_events
    assert b"run.completed" in second_events

    first_store = SQLiteRunStore(tmp_path / "a")
    second_store = SQLiteRunStore(tmp_path / "b")
    first_metrics = read_bytes(first_store.metrics_json_path("run-golden"))
    second_metrics = read_bytes(second_store.metrics_json_path("run-golden"))
    assert first_metrics == second_metrics


async def test_a_mock_run_is_byte_repeatable_across_roots(
    valid_scenario: Scenario, tmp_path: Path
) -> None:
    scenario = small_three_agent_scenario(valid_scenario)
    await _run_once(scenario, tmp_path / "a")
    await _run_once(scenario, tmp_path / "b")

    assert read_bytes(tmp_path / "a" / "run-golden.jsonl") == read_bytes(
        tmp_path / "b" / "run-golden.jsonl"
    )
    first_store = SQLiteRunStore(tmp_path / "a")
    second_store = SQLiteRunStore(tmp_path / "b")
    assert read_bytes(first_store.metrics_json_path("run-golden")) == read_bytes(
        second_store.metrics_json_path("run-golden")
    )


async def _run_once(scenario: Scenario, root: Path) -> None:
    store, runner, sinks = build_runner(root, "run-golden")
    await runner.run(scenario, seed=42, store=store, sinks=sinks, run_id="run-golden")
    # Touch nothing after the run: the artifact on disk IS the comparison target.
    del store
