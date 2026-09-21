"""The four dashboard widgets: agent table, agent detail, event stream, metrics strip.

Each widget is a thin read-only renderer over the bus's projections: the table shows
the snapshot's agents, the detail panel shows the selected agent's profile and latest
memories, the stream shows the bounded display events, and the strip shows the six
documented aggregates. None of them mutate state; none of them use emoji as the only
status indicator; all of them degrade to plain text labels that stay legible under
NO_COLOR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import DataTable, RichLog, Static
from textual.widgets.data_table import ColumnKey

from adlife.core.domain.events import DomainEvent
from adlife.tui.event_bus import TuiEventBus

if TYPE_CHECKING:
    from adlife.core.simulation.movement import Snapshot

STREAM_LIMIT = 100


class AgentTable(DataTable[object]):
    """The left panel: one row per agent, projected from the latest snapshot."""

    def __init__(self) -> None:
        super().__init__(id="agent-table", cursor_type="row")
        self._known_rows: set[str] = set()

    def on_mount(self) -> None:
        self.add_columns("Agent", "Name", "Activity", "Location", "Sentiment", "Recall", "Intent")
        self._column_keys = {str(column.label): key for key, column in self.columns.items()}

    def _cell_key(self, label: str) -> ColumnKey:
        return self._column_keys[label]

    def project(self, snapshot: Snapshot) -> None:
        for agent_id, (profile, state) in snapshot.agents.items():
            if agent_id not in self._known_rows:
                self._known_rows.add(agent_id)
                self.add_row(
                    agent_id,
                    profile.display_name,
                    state.activity,
                    state.location,
                    f"{state.brand_sentiment:+.2f}",
                    f"{state.recall_strength:.2f}",
                    f"{state.purchase_intention:.2f}",
                    key=agent_id,
                )
                continue
            self.update_cell(
                agent_id, self._cell_key("Activity"), state.activity, update_width=True
            )
            self.update_cell(
                agent_id, self._cell_key("Location"), state.location, update_width=True
            )
            self.update_cell(agent_id, self._cell_key("Sentiment"), f"{state.brand_sentiment:+.2f}")
            self.update_cell(agent_id, self._cell_key("Recall"), f"{state.recall_strength:.2f}")
            self.update_cell(agent_id, self._cell_key("Intent"), f"{state.purchase_intention:.2f}")


class AgentDetail(Static):
    """The right-top panel: the selected agent's profile and latest memories."""

    def __init__(self) -> None:
        super().__init__("", id="agent-detail")

    def show_agent(self, agent_id: str, bus: TuiEventBus) -> None:
        latest = bus.latest
        snapshot = latest.snapshot
        lines: list[str] = [f"Agent {agent_id}"]
        if snapshot is not None and agent_id in snapshot.agents:
            profile, state = snapshot.agents[agent_id]
            lines.append(f"Name: {profile.display_name}")
            lines.append(f"Occupation: {profile.occupation}")
            lines.append(f"Home: {profile.home_zone}")
            lines.append(f"Activity: {state.activity} at {state.location}")
            lines.append(f"Sentiment: {state.brand_sentiment:+.2f}")
            lines.append(f"Recall: {state.recall_strength:.2f}")
            lines.append(f"Intent: {state.purchase_intention:.2f}")
            lines.append(f"Fatigue: {state.ad_fatigue:.2f}")
        memories = latest.memories.get(agent_id, ())
        lines.append("Memories (latest last):")
        if not memories:
            lines.append("  (none yet)")
        for memory in list(memories)[-5:]:
            summary = str(memory.get("summary", "(summary unavailable)"))
            kind = str(memory.get("kind", "?"))
            lines.append(f"  [{kind}] {summary}")
        self.update("\n".join(lines))


class EventStream(RichLog):
    """The right-bottom panel: the bounded, optionally filtered committed stream."""

    def __init__(self) -> None:
        super().__init__(id="event-stream", max_lines=STREAM_LIMIT, markup=False, highlight=False)
        self._filter: str | None = None

    def set_filter(self, substring: str | None) -> None:
        self._filter = substring or None

    def append_event(self, event: DomainEvent) -> None:
        if self._filter is not None and self._filter not in event.event_type.value:
            return
        agent = event.agent_id or "-"
        campaign = event.campaign_id or "-"
        self.write(
            f"m{event.simulated_minute:>5} #{event.sequence:>4} "
            f"{event.event_type.value:<22} {agent} {campaign}"
        )


class MetricsStrip(Static):
    """The bottom strip: reach, notice, sentiment, recall, shares, high intent."""

    def __init__(self) -> None:
        super().__init__("", id="metrics-strip")

    def project(self, bus: TuiEventBus) -> None:
        latest = bus.latest
        snapshot = latest.snapshot
        agents = tuple(snapshot.agents.values()) if snapshot is not None else ()
        count = len(agents)
        if count:
            sentiment = sum(state.brand_sentiment for _profile, state in agents) / count
            recall = sum(state.recall_strength for _profile, state in agents) / count
            high_intent = sum(1 for _profile, state in agents if state.purchase_intention >= 0.7)
        else:
            sentiment = recall = 0.0
            high_intent = 0
        campaigns = latest.campaigns
        reach = sum(item.reach for item in campaigns.values())
        notices = sum(item.notices for item in campaigns.values())
        shares = sum(item.shares for item in campaigns.values())
        self.update(
            f"Reach {reach} | Notices {notices} | Sentiment {sentiment:+.2f} | "
            f"Recall {recall:.2f} | Shares {shares} | High intent {high_intent}/{count}"
        )
