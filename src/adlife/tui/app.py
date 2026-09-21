"""The four-panel live dashboard: a read-only Textual adapter over committed events.

Layout (spec 15): header with run/day-time/provider/seed/speed, the agent table on the
left 60%, the selected agent's details and memories at right-top, the bounded live
event stream at right-bottom, the six-value metrics strip along the bottom, and the
documented key bindings in the footer. Every value shown comes from
:class:`~adlife.tui.event_bus.TuiEventBus` projections of events the runner has already
persisted; the keys control the run through :class:`~adlife.tui.controller.RunController`
and nothing else. Textual itself honors NO_COLOR; the stylesheet keeps text labels and
accessible contrast so the display degrades cleanly without color.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Input, Static

from adlife.core.domain.events import DomainEvent
from adlife.tui.event_bus import TuiEventBus
from adlife.tui.widgets import AgentDetail, AgentTable, EventStream, MetricsStrip

if TYPE_CHECKING:
    from adlife.core.simulation.movement import Snapshot
    from adlife.tui.controller import RunController


class AdLifeTui(App[None]):
    """The live synthetic-society dashboard."""

    CSS_PATH = "styles.tcss"
    BINDINGS: ClassVar = [
        ("space", "toggle_pause", "Pause"),
        ("n", "step", "Step"),
        ("plus", "speed_up", "Faster"),
        ("minus", "slow_down", "Slower"),
        ("f", "filter", "Filter"),
        ("q", "quit_run", "Quit"),
    ]

    def __init__(self, controller: RunController, bus: TuiEventBus) -> None:
        super().__init__()
        self.controller = controller
        self.bus = bus
        self._selected_agent: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-bar"):
            yield Static("", id="run-info")
            yield Static("", id="clock")
        with Horizontal(id="main"):
            yield AgentTable()
            with Vertical(id="right"):
                yield AgentDetail()
                yield EventStream()
        with Horizontal(id="filter-bar"):
            yield Input(placeholder="filter event types (empty clears)", id="filter-input")
        yield MetricsStrip()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#filter-bar").display = False
        self.controller.start()
        self.bus.subscribe(self._on_committed)
        self.set_interval(0.5, self._refresh_header)

    def _on_committed(self, snapshot: Snapshot, events: tuple[DomainEvent, ...]) -> None:
        table = self.query_one("#agent-table", AgentTable)
        table.project(snapshot)
        strip = self.query_one("#metrics-strip", MetricsStrip)
        strip.project(self.bus)
        stream = self.query_one("#event-stream", EventStream)
        for event in events:
            stream.append_event(event)
        if self._selected_agent is None and snapshot.agents:
            self._selected_agent = next(iter(snapshot.agents))
            self.query_one("#agent-detail", AgentDetail).show_agent(self._selected_agent, self.bus)
        self._refresh_header()

    def _refresh_header(self) -> None:
        minute = self.bus.latest.simulated_minute
        day, time_of_day = divmod(minute, 1440)
        clock = self.query_one("#clock", Static)
        clock.update(f"Day {day + 1} {time_of_day // 60:02d}:{time_of_day % 60:02d}")
        paused = self.controller.is_paused()
        clock.set_class(paused, "paused")
        speed = self.controller.current_speed()
        provider = self.controller.provider_label()
        seed = self.controller.seed_label()
        run_id = self.controller.run_id_label()
        self.query_one("#run-info", Static).update(
            f"run {run_id} | {provider} | seed {seed} | speed x{speed:g} | "
            + ("PAUSED" if paused else "running")
        )

    async def action_toggle_pause(self) -> None:
        await self.controller.pause()
        self._refresh_header()

    async def action_step(self) -> None:
        await self.controller.step()
        self._refresh_header()

    async def action_speed_up(self) -> None:
        await self.controller.set_speed(self.controller.current_speed() * 2.0)
        self._refresh_header()

    async def action_slow_down(self) -> None:
        await self.controller.set_speed(self.controller.current_speed() * 0.5)
        self._refresh_header()

    def action_filter(self) -> None:
        self.query_one("#filter-bar").display = True
        self.query_one("#filter-input", Input).focus()

    async def action_quit_run(self) -> None:
        await self.controller.stop()
        self.exit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            value = event.value.strip()
            self.apply_filter(value or None)
            self.query_one("#filter-bar").display = False
            self.set_focus(None)

    def apply_filter(self, substring: str | None) -> None:
        self.query_one("#event-stream", EventStream).set_filter(substring)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not isinstance(event, DataTable.RowHighlighted):
            return
        if event.row_key is not None and event.row_key.value is not None:
            self._selected_agent = event.row_key.value
            self.query_one("#agent-detail", AgentDetail).show_agent(self._selected_agent, self.bus)
