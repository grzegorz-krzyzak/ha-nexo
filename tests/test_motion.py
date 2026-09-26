"""Remote-style stepping: up, stop, down, stop."""

import pytest

from custom_components.nexo.motion import Motion

CLOSED, NOT_CLOSED = 101, 102


def press(motion: Motion, reed: int, now: float) -> str:
    direction = motion.next_step(reed, now)
    motion.record(direction, now)
    return direction


def test_full_cycle_like_a_remote() -> None:
    m = Motion(travel_time=27)
    assert press(m, CLOSED, 0) == "up"
    assert press(m, NOT_CLOSED, 10) == "down"  # stops it on the way up
    assert press(m, NOT_CLOSED, 15) == "down"  # now moves down
    assert press(m, NOT_CLOSED, 20) == "up"  # stops it on the way down
    assert press(m, NOT_CLOSED, 25) == "up"  # moves up again


def test_stop_right_after_starting_while_reed_still_reads_closed() -> None:
    m = Motion(travel_time=27)
    assert press(m, CLOSED, 0) == "up"
    # The reed switch needs ~2.6 s to leave closed; a press now must stop.
    assert press(m, CLOSED, 1.5) == "down"
    assert m.stopped


def test_end_of_travel_reverses() -> None:
    m = Motion(travel_time=27)
    press(m, CLOSED, 0)
    assert press(m, NOT_CLOSED, 40) == "down"  # fully open by now


def test_closed_resets_the_cycle() -> None:
    m = Motion(travel_time=27)
    press(m, NOT_CLOSED, 0)  # unknown history, not closed: down
    assert m.direction == "down"
    # It reached closed within the travel time: the next press opens.
    assert press(m, CLOSED, 20) == "up"


def test_unknown_history_open_gate_closes() -> None:
    assert Motion(travel_time=27).next_step(NOT_CLOSED, 0) == "down"


def test_without_reed_switch() -> None:
    m = Motion(travel_time=27)
    assert press(m, None, 0) == "down"
    assert press(m, None, 5) == "up"  # stop
    assert press(m, None, 8) == "up"


@pytest.mark.parametrize(("first", "second", "stopped"), [("up", "down", True), ("up", "up", False)])
def test_direct_commands_are_recorded(first: str, second: str, stopped: bool) -> None:
    m = Motion(travel_time=27)
    m.record(first, 0)
    m.record(second, 5)
    assert m.stopped is stopped
