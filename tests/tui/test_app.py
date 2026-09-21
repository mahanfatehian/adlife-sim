"""The four-panel dashboard, driven through Textual's test pilot.

The pilot presses the documented keys and reads the widgets back, so the layout, the
key bindings, the bounded stream, and the accessibility constraints (text labels, no
emoji-only status, NO_COLOR) are pinned here while the engine side is a stubbed
controller - the equivalence suite proves the whole wired loop.
"""

from __future__ import annotations

from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.state import ConsumerState
from adlife.core.simulation.movement import Snapshot
from adlife.tui.app import AdLifeTui
from adlife.tui.controller import RunController
from adlife.tui.event_bus import TuiEventBus


def _profile(agent_id: str) -> PersonProfile:
    return PersonProfile(
        agent_id=agent_id,
        display_name=agent_id.replace("person-", "Agent ").title(),
        age=30,
        occupation="office-worker",
        income_band="middle",
        household_type="single",
        home_zone="home-north",
        work_or_study_zone="office",
        interests=frozenset({"coffee"}),
        traits=ConsumerTraits(
            price_sensitivity=0.5,
            novelty_seeking=0.5,
            social_susceptibility=0.5,
            advertising_skepticism=0.5,
            mobile_attention=0.5,
            outdoor_attention=0.5,
            brand_loyalty=0.5,
            impulsivity=0.5,
        ),
        initial_brand_sentiment=0.1,
        routine_template="office-worker",
    )


def _state(agent_id: str) -> ConsumerState:
    return ConsumerState(
        agent_id=agent_id,
        location="home-north",
        activity="sleep",
        mood=0.0,
        fatigue=0.0,
        brand_sentiment=0.1,
        recall_strength=0.2,
        purchase_intention=0.1,
        exposure_counts=(),
        cognition_budget_remaining=6,
    )


class StubController(RunController):
    """The engine seam replaced by a stub: the app tests never run an engine."""

    def __init__(self) -> None:
        super().__init__()
        self._paused = False
        self.stepped = 0
        self._speed = 1.0
        self.stopped = False

    def start(self) -> None:
        return None

    async def pause(self) -> None:
        self._paused = not self._paused

    def is_paused(self) -> bool:
        return self._paused

    async def step(self) -> None:
        self.stepped += 1

    async def set_speed(self, multiplier: float) -> None:
        self._speed = multiplier

    def current_speed(self) -> float:
        return self._speed

    def run_id_label(self) -> str:
        return "run-stub"

    def provider_label(self) -> str:
        return "rules"

    def seed_label(self) -> str:
        return "42"

    async def stop(self) -> None:
        self.stopped = True


def _tui() -> tuple[AdLifeTui, StubController, TuiEventBus]:
    bus = TuiEventBus()
    controller = StubController()
    app = AdLifeTui(controller, bus)
    return app, controller, bus


def _publish_agents(bus: TuiEventBus, minute: int = 0) -> None:
    snapshot = Snapshot(
        agents={
            agent_id: (_profile(agent_id), _state(agent_id))
            for agent_id in ("person-001", "person-002", "person-003")
        },
        simulated_minute=minute,
        run_id="run-tui",
    )
    bus.publish(snapshot, ())


def _publish_one_agent(bus: TuiEventBus, minute: int = 0) -> None:
    snapshot = Snapshot(
        agents={"person-001": (_profile("person-001"), _state("person-001"))},
        simulated_minute=minute,
        run_id="run-tui",
    )
    bus.publish(snapshot, ())


def _committed(event_type: str, *, sequence: int):
    from adlife.core.domain.events import DomainEvent, EventSource, EventType

    return DomainEvent(
        event_id=f"run-tui:{sequence:04d}",
        run_id="run-tui",
        simulated_minute=15,
        sequence=sequence,
        event_type=EventType(event_type),
        payload={},
        source=EventSource.RULE,
    )


async def test_tui_starts_and_shows_agents() -> None:
    app, _controller, bus = _tui()
    async with app.run_test(size=(120, 40)) as pilot:
        _publish_agents(bus)
        await pilot.pause()
        assert app.query_one("#agent-table").row_count == 3
        assert "Day 1" in str(app.query_one("#clock").render())


async def test_pause_changes_rendering_not_engine_result() -> None:
    app, controller, bus = _tui()
    async with app.run_test(size=(120, 40)) as pilot:
        _publish_agents(bus)
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        assert controller.is_paused() is True
        assert app.query_one("#clock").has_class("paused")

        await pilot.press("space")
        await pilot.pause()
        assert controller.is_paused() is False
        assert not app.query_one("#clock").has_class("paused")


async def test_step_while_paused_advances_one_tick() -> None:
    app, controller, bus = _tui()
    async with app.run_test(size=(120, 40)) as pilot:
        _publish_agents(bus)
        await pilot.pause()

        await pilot.press("space")
        await pilot.press("n")
        await pilot.pause()
        assert controller.stepped == 1


async def test_speed_keys_update_the_multiplier() -> None:
    app, controller, _bus = _tui()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("plus")
        await pilot.pause()
        assert controller.current_speed() > 1.0

        await pilot.press("minus")
        await pilot.press("minus")
        await pilot.pause()
        assert controller.current_speed() < 1.0


async def test_agent_selection_fills_the_detail_panel() -> None:
    app, _controller, bus = _tui()
    async with app.run_test(size=(120, 40)) as pilot:
        _publish_agents(bus)
        await pilot.pause()

        table = app.query_one("#agent-table")
        table.focus()
        await pilot.press("down")
        await pilot.pause()

        detail = app.query_one("#agent-detail")
        assert "person-002" in str(detail.render())


async def test_filter_narrows_the_event_stream() -> None:
    app, _controller, bus = _tui()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.apply_filter("campaign")
        _publish_one_agent(bus)
        bus.publish(
            Snapshot(
                agents={"person-001": (_profile("person-001"), _state("person-001"))},
                simulated_minute=15,
                run_id="run-tui",
            ),
            (
                _committed("agent.location_changed", sequence=1),
                _committed("campaign.impression", sequence=2),
            ),
        )
        await pilot.pause()

        stream = app.query_one("#event-stream")
        rendered = "\n".join("".join(segment.text for segment in strip) for strip in stream.lines)
        assert "campaign.impression" in rendered
        assert "agent.location_changed" not in rendered


async def test_event_stream_is_bounded_to_one_hundred_lines() -> None:
    app, _controller, bus = _tui()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        for sequence in range(1, 121):
            bus.publish(
                Snapshot(
                    agents={"person-001": (_profile("person-001"), _state("person-001"))},
                    simulated_minute=sequence,
                    run_id="run-tui",
                ),
                (_committed("agent.location_changed", sequence=sequence),),
            )
        await pilot.pause()
        await app.animator.wait_until_complete()

        stream = app.query_one("#event-stream")
        assert stream.max_lines == 100
        rendered = "\n".join("".join(segment.text for segment in strip) for strip in stream.lines)
        # The newest events are shown; the oldest have fallen off the bounded buffer.
        assert "# 120 " in rendered
        assert "#   1 " not in rendered


async def test_layout_survives_an_eighty_by_twenty_four_terminal() -> None:
    app, _controller, bus = _tui()
    async with app.run_test(size=(80, 24)) as pilot:
        _publish_agents(bus)
        await pilot.pause()

        for selector in ("#agent-table", "#agent-detail", "#event-stream", "#metrics-strip"):
            assert app.query_one(selector) is not None


async def test_quit_stops_the_controller() -> None:
    app, controller, _bus = _tui()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("q")
        await pilot.pause()

    assert controller.stopped is True


async def test_no_color_is_read_at_construction(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    app, _controller, _bus = _tui()
    async with app.run_test(size=(80, 24)):
        assert app.no_color is True

    monkeypatch.delenv("NO_COLOR", raising=False)
    plain_app, _controller, _bus = _tui()
    async with plain_app.run_test(size=(80, 24)):
        assert plain_app.no_color is False
