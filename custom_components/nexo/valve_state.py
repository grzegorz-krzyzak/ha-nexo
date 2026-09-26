"""Which valve - which watering program - is running, from the outputs.

A program switches its sections on one after another, with short pauses in
between, while a main valve stays open for the whole run. Section outputs
alone would read as "stopped" in every pause, so:

- a valve is open while any of its sections is on;
- in a pause - no section on, main valve still on - the valve whose section
  was on last stays open;
- otherwise it is closed.

Only the outputs are observed, not the commands, so it does not matter
whether watering was started from Home Assistant, the Nexo app, a remote or
the central unit's own schedule. The main valve closing ends every run, so a
wrong guess cannot outlive one watering.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import ITEM_ID, VALVE_MAIN, VALVE_SECTIONS


def is_on(value: int | None) -> bool:
    """Relay and lighting outputs read 0 when off."""
    return bool(value)


def valve_states(
    valves: list[dict[str, Any]],
    data: Mapping[str, int],
    last_active: dict[str, str],
) -> dict[str, bool | None]:
    """Return open (True), closed (False) or unknown (None) per valve id.

    last_active maps a main valve to the id of the valve whose section was
    last seen on; it is updated in place and must be kept between calls.
    """
    # Remember whose section is on, per main valve
    for valve in valves:
        main = valve.get(VALVE_MAIN)
        if main and any(is_on(data.get(s)) for s in valve.get(VALVE_SECTIONS, [])):
            last_active[main] = valve[ITEM_ID]

    states: dict[str, bool | None] = {}
    for valve in valves:
        sections = valve.get(VALVE_SECTIONS, [])
        if not sections:
            states[valve[ITEM_ID]] = None
            continue
        if any(data.get(s) is None for s in sections):
            states[valve[ITEM_ID]] = None  # not read yet
            continue
        if any(is_on(data[s]) for s in sections):
            states[valve[ITEM_ID]] = True
            continue
        main = valve.get(VALVE_MAIN)
        if main and is_on(data.get(main)) and last_active.get(main) == valve[ITEM_ID]:
            states[valve[ITEM_ID]] = True  # pause between sections
            continue
        states[valve[ITEM_ID]] = False

    # The main valve closing ends the run for everyone behind it
    for main in list(last_active):
        if data.get(main) is not None and not is_on(data[main]):
            del last_active[main]
    return states
