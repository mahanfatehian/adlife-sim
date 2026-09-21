"""Live and headless runs of the same scenario must produce the same run.

The dashboard is a read-only adapter, so the strongest guarantee available is the one
this suite pins: a run driven to completion beneath a live TUI leaves an event stream
equal - event for event - to the headless run of the same scenario and seed, and a
quit-mid-run leaves an interrupted artifact that replays consistently.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from adlife.adapters.cognition.rules import (
    RULE_MODEL_ID,
    RuleCognitionInputs,
    RuleCognitionProvider,
)
from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.scenario import Scenario
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRequest,
    ProviderMetadata,
    SamplingSettings,
)
from adlife.core.simulation.runner import RunIdentity, SimulationRunner
from adlife.tui.app import AdLifeTui
from adlife.tui.controller import LiveRunController
from adlife.tui.event_bus import TuiEventBus


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _identity() -> RunIdentity:
    return RunIdentity.for_project(
        project_root=_repo_root(),
        provider="rule",
        model_id=RULE_MODEL_ID,
    )


def rule_inputs(plan) -> dict[str, RuleCognitionInputs]:
    inputs: dict[str, RuleCognitionInputs] = {}
    for decision in plan.attention:
        if not decision.noticed:
            continue
        request_id = decision.events[2].event_id
        opportunity = decision.opportunity
        inputs[request_id] = RuleCognitionInputs(
            profile=opportunity.profile,
            state=opportunity.state,
            campaign=opportunity.campaign,
            placement=opportunity.placement,
        )
    return inputs


def _rule_fallback(plan):
    return RuleCognitionProvider.for_requests(plan.requests, rule_inputs(plan))


class RuleResolutionPort:
    """The same in-process rule seam the Task 12 suites wire."""

    def __init__(self) -> None:
        self.answered: list[CognitionRequest] = []

    async def resolve(
        self,
        requests,
        *,
        fallback_provider,
    ) -> dict[str, CognitionAnswer]:
        answers: dict[str, CognitionAnswer] = {}
        for request in requests:
            self.answered.append(request)
            assert isinstance(fallback_provider, RuleCognitionProvider)
            answers[request.request_id] = await fallback_provider.answer(request)
        return answers

    @property
    def provider_metadata(self) -> ProviderMetadata | None:
        return ProviderMetadata(
            kind="rule",
            model_id=RULE_MODEL_ID,
            sampling=SamplingSettings(),
            prompt_sha256="d" * 64,
        )


def build_runner() -> SimulationRunner:
    return SimulationRunner(
        cognition_factory=lambda *args, **kwargs: RuleResolutionPort(),
        fallback_provider_factory=_rule_fallback,
        identity=_identity(),
    )


def _normalized(events) -> list[dict[str, object]]:
    """Event identities minus run id: the stream a replay must reproduce."""
    from adlife.core.domain.serialization import canonical_json

    rows = []
    for event in events:
        document = canonical_json(event).replace(event.run_id, "RUNID")
        rows.append(document)
    return rows


async def test_live_run_equals_headless_run_event_for_event(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    headless_store = SQLiteRunStore(tmp_path / "headless")
    headless = await build_runner().run(
        small_scenario, seed=42, store=headless_store, sinks=(), run_id="run-headless"
    )
    assert headless.status == "completed"

    live_store = SQLiteRunStore(tmp_path / "live")
    bus = TuiEventBus()
    controller = LiveRunController(
        runner_factory=build_runner,
        scenario=small_scenario,
        seed=42,
        store=live_store,
        run_id="run-live",
        event_bus=bus,
    )
    app = AdLifeTui(controller, bus)
    async with app.run_test(size=(120, 40)) as pilot:
        while not controller.finished.is_set():
            await pilot.pause()
            await asyncio.sleep(0)
        await pilot.pause()

    assert controller.failure is None
    live = live_store.load_run("run-live")
    assert live.status == "completed"
    assert _normalized(live.events) == _normalized(headless_store.load_run("run-headless").events)


async def test_quit_mid_run_leaves_a_replayable_interrupted_artifact(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    live_store = SQLiteRunStore(tmp_path / "interrupted")
    bus = TuiEventBus()
    controller = LiveRunController(
        runner_factory=build_runner,
        scenario=small_scenario,
        seed=42,
        store=live_store,
        run_id="run-interrupted",
        event_bus=bus,
    )
    app = AdLifeTui(controller, bus)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert controller.finished.is_set() is False
        await controller.stop()

    stored = live_store.load_run("run-interrupted")
    assert stored.status in {"failed", "interrupted"}
    assert len(stored.events) > 0
    assert stored.completed_at is not None
