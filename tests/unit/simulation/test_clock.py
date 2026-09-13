import pytest

from adlife.core.simulation.clock import SimClock


def test_clock_rolls_over_at_midnight() -> None:
    clock = SimClock(duration_minutes=2880)

    for _ in range(96):
        clock.advance()

    assert clock.current_minute == 1440
    assert clock.minute_of_day == 0
    assert clock.day_index == 1
    assert clock.finished is False


def test_clock_marks_terminal_minute_finished_after_final_tick() -> None:
    clock = SimClock(duration_minutes=1440)

    for _ in range(95):
        clock.advance()

    assert clock.current_minute == 1425
    assert clock.minute_of_day == 1425
    assert clock.day_index == 0
    assert clock.finished is False

    clock.advance()

    assert clock.current_minute == 1440
    assert clock.minute_of_day == 0
    assert clock.day_index == 1
    assert clock.finished is True


def test_clock_refuses_to_advance_past_terminal_minute() -> None:
    clock = SimClock(duration_minutes=15)
    clock.advance()

    with pytest.raises(RuntimeError, match="finished"):
        clock.advance()

    assert clock.current_minute == 15


def test_clock_rejects_non_fifteen_minute_ticks() -> None:
    with pytest.raises(ValueError, match="tick_minutes must be 15"):
        SimClock(duration_minutes=1440, tick_minutes=30)


@pytest.mark.parametrize("duration_minutes", [0, 14, 1439])
def test_clock_rejects_nonpositive_or_misaligned_duration(duration_minutes: int) -> None:
    with pytest.raises(ValueError, match="positive multiple of 15"):
        SimClock(duration_minutes=duration_minutes)
