"""The run controls the dashboard offers: pause, step, speed, stop.

``RunController`` is the abstract control seam the app binds to; ``LiveRunController``
owns the asyncio task driving :class:`~adlife.core.simulation.runner.SimulationRunner`
with the bus wired through the runner's ``tick_observer`` hook. Stop cancels the task,
which routes through the runner's interruption handling - the same path a Ctrl-C takes -
so a quit-mid-run leaves exactly the interrupted artifact the headless path leaves.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from adlife.tui.event_bus import TuiEventBus

if TYPE_CHECKING:
    from adlife.core.domain.results import SimulationResult
    from adlife.core.domain.scenario import Scenario
    from adlife.core.ports.run_store import RunStore
    from adlife.core.simulation.engine import AdLifeModel
    from adlife.core.simulation.movement import TickOutcome, TickPlan
    from adlife.core.simulation.runner import SimulationRunner

SPEED_STEP = 0.5
MIN_SPEED = 0.25
MAX_SPEED = 8.0
BASE_TICK_DELAY_SECONDS = 0.5


class RunController(Protocol):
    """The control seam the dashboard binds to; the app tests stub it."""

    def start(self) -> None: ...

    async def pause(self) -> None:
        """Toggle the pause state."""
        ...

    def is_paused(self) -> bool: ...

    async def step(self) -> None: ...

    async def set_speed(self, multiplier: float) -> None: ...

    def current_speed(self) -> float: ...

    def run_id_label(self) -> str: ...

    def provider_label(self) -> str: ...

    def seed_label(self) -> str: ...

    async def stop(self) -> None: ...


class LiveRunController:
    """Drive one SimulationRunner in a task, with the bus on the tick observer."""

    def __init__(
        self,
        *,
        runner_factory: Callable[[], SimulationRunner],
        scenario: Scenario,
        seed: int,
        store: RunStore,
        run_id: str,
        event_bus: TuiEventBus,
        tick_delay: float = BASE_TICK_DELAY_SECONDS,
    ) -> None:
        self._runner_factory = runner_factory
        self._scenario = scenario
        self._seed = seed
        self._store = store
        self._run_id = run_id
        self._bus = event_bus
        self._tick_delay = tick_delay
        self._speed = tick_delay
        self._paused = asyncio.Event()
        self._resume = asyncio.Event()
        self._resume.set()
        self._step_requests = 0
        self._task: asyncio.Task[SimulationResult | None] | None = None
        self.finished = asyncio.Event()
        self.failure: BaseException | None = None
        self.result: SimulationResult | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(self._drive(), name="adlife-run")

    async def pause(self) -> None:
        """Toggle the pause state."""
        if self._paused.is_set():
            self._paused.clear()
        else:
            self._paused.set()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    async def step(self) -> None:
        """Advance exactly one tick, parking again if the run is paused."""
        self._step_requests += 1
        self._resume.set()

    async def set_speed(self, multiplier: float) -> None:
        clamped = max(MIN_SPEED, min(MAX_SPEED, multiplier))
        self._speed = self._tick_delay / clamped

    def current_speed(self) -> float:
        return self._tick_delay / self._speed if self._speed > 0 else MAX_SPEED

    # The header labels the app reads; the run's own manifest values arrive with the
    # first tick, so the defaults describe the wiring until then.
    def run_id_label(self) -> str:
        return self._run_id

    def provider_label(self) -> str:
        return "rules"

    def seed_label(self) -> str:
        return str(self._seed)

    async def stop(self) -> None:
        if self._task is None:
            self.finished.set()
            return
        self._task.cancel()
        self._resume.set()
        # Wait for the runner to record the interrupted artifact before the caller
        # (or the app's exit) can read the store.
        with contextlib.suppress(BaseException):
            await self._task

    async def _observe_tick(self, plan: TickPlan, outcome: TickOutcome, model: AdLifeModel) -> None:
        del plan, model
        self._bus.publish(outcome.snapshot, outcome.events)
        if self._step_requests > 0:
            # This tick was stepped: consume the request and re-park if paused.
            self._step_requests -= 1
            if self._paused.is_set():
                self._resume.clear()
        if self._paused.is_set():
            await self._resume.wait()
        elif self._speed > 0:
            # The throttle: the live run's real-time pace. This await is also the
            # cancellation point stop() relies on.
            await asyncio.sleep(self._speed)

    async def _drive(self) -> SimulationResult | None:
        try:
            runner: SimulationRunner = self._runner_factory()
            result = await runner.run(
                self._scenario,
                seed=self._seed,
                store=self._store,
                sinks=(),
                run_id=self._run_id,
                tick_observer=self._observe_tick,
            )
            self.result = result
            return result
        except BaseException as error:
            # The failure is surfaced through ``self.failure``; re-raising here would
            # only produce an unretrieved-task-exception warning at interpreter exit.
            self.failure = error
            return None
        finally:
            self.finished.set()
