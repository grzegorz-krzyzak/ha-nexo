"""Remote-style stepping: up, stop, down, stop."""

import pytest

from custom_components.nexo.motion import Motion

CLOSED, NOT_CLOSED = 101, 102


def press(motion: Motion, reed: int | None, now: float) -> str:
    """One press, as step() does it."""
    direction = motion.next_step(reed, now)
    motion.record(direction, now, reed == CLOSED)
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


def test_closed_from_part_way_then_opened_within_the_travel_time() -> None:
    # The garage door at home, 2026-10-10 20:02-20:04 (travel time 27 s): it
    # closed from part-way in 14 s and was opened again 22.6 s after the
    # close command. That up was recorded as a stop of the closing, so at
    # fully open the next press sent up again and nothing moved.
    m = Motion(travel_time=27)
    assert press(m, NOT_CLOSED, 0) == "down"  # 20:02:52.7, closes from part-way
    assert press(m, CLOSED, 22.6) == "up"  # 20:03:15.3, closed at 14 s
    assert not m.stopped  # a new motion, not a stop
    assert press(m, NOT_CLOSED, 56.7) == "down"  # 20:03:49.4, fully open
    assert press(m, NOT_CLOSED, 65.7) == "up"  # stops it on the way down


def test_second_press_while_leaving_closed_after_closing_from_part_way() -> None:
    # 2026-10-11 20:34:58-20:35:14 (travel time 25 s): closed 11.6 s after
    # the command, opened at 13.3 s; a press 2.6 s later, the reed switch
    # still reading closed, must stop the door, not send up again.
    m = Motion(travel_time=25)
    press(m, NOT_CLOSED, 0)
    assert press(m, CLOSED, 13.3) == "up"
    assert press(m, CLOSED, 15.9) == "down"
    assert m.stopped


def test_open_button_on_a_closed_gate_starts_a_motion() -> None:
    m = Motion(travel_time=25)
    m.record("down", 0)
    m.record("up", 10, closed=True)  # the entity's open, gate reads closed
    assert m.moving(11) and m.direction == "up"
