import pytest

from adlife.core.simulation.engine import stable_event_id


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        (0, "run-alpha:event-00000000"),
        (7, "run-alpha:event-00000007"),
        (12_345_678, "run-alpha:event-12345678"),
    ],
)
def test_stable_event_id_uses_eight_digit_sequence(
    sequence: int,
    expected: str,
) -> None:
    assert stable_event_id("run-alpha", sequence) == expected
