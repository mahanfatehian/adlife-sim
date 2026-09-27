import asyncio
from pathlib import Path

from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.core.domain.scenario import Scenario
from adlife.tui.controller import LiveRunController
from adlife.tui.event_bus import TuiEventBus
from tests.integration.test_live_headless_equivalence import build_runner


async def test_pause_step_resume_preserves_exact_headless_result(
    small_scenario: Scenario, tmp_path: Path
) -> None:
    store = SQLiteRunStore(tmp_path / "live")
    bus = TuiEventBus()
    commits: asyncio.Queue[int] = asyncio.Queue()
    bus.subscribe(lambda snapshot, events: commits.put_nowait(snapshot.simulated_minute))
    controller = LiveRunController(
        runner_factory=build_runner,
        scenario=small_scenario,
        seed=42,
        store=store,
        run_id="controlled",
        event_bus=bus,
        tick_delay=0,
    )
    await controller.pause()
    controller.start()
    try:
        assert await asyncio.wait_for(commits.get(), timeout=5) == 0
        await asyncio.sleep(0)
        assert commits.empty(), "pause must park at the committed tick boundary"
        assert not controller.finished.is_set()
        await controller.step()
        assert await asyncio.wait_for(commits.get(), timeout=5) == 15
        await asyncio.sleep(0)
        assert commits.empty(), "one step must grant exactly one additional tick"
        await controller.step()
        await controller.step()
        assert await asyncio.wait_for(commits.get(), timeout=5) == 30
        assert await asyncio.wait_for(commits.get(), timeout=5) == 45
        await asyncio.sleep(0)
        assert commits.empty(), "two queued steps must grant exactly two additional ticks"
        await controller.pause()
        await asyncio.wait_for(controller.finished.wait(), timeout=10)
        assert controller.failure is None
    finally:
        await controller.stop()

    plain_store = SQLiteRunStore(tmp_path / "headless")
    await build_runner().run(
        small_scenario, seed=42, store=plain_store, sinks=(), run_id="controlled"
    )
    assert store.load_run("controlled").events == plain_store.load_run("controlled").events
