class SimClock:
    """A fixed-step clock with an exclusive terminal boundary."""

    __slots__ = ("_current_minute", "_duration_minutes")

    def __init__(self, duration_minutes: int, tick_minutes: int = 15) -> None:
        if tick_minutes != 15:
            raise ValueError("tick_minutes must be 15")
        if duration_minutes <= 0 or duration_minutes % tick_minutes != 0:
            raise ValueError("duration_minutes must be a positive multiple of 15")

        self._duration_minutes = duration_minutes
        self._current_minute = 0

    @property
    def current_minute(self) -> int:
        return self._current_minute

    @property
    def minute_of_day(self) -> int:
        return self._current_minute % 1440

    @property
    def day_index(self) -> int:
        return self._current_minute // 1440

    @property
    def finished(self) -> bool:
        return self._current_minute == self._duration_minutes

    def advance(self) -> None:
        if self.finished:
            raise RuntimeError("simulation clock is already finished")

        next_minute = self._current_minute + 15
        if next_minute > self._duration_minutes:
            raise RuntimeError("simulation clock cannot advance beyond its duration")
        self._current_minute = next_minute


__all__ = ["SimClock"]
