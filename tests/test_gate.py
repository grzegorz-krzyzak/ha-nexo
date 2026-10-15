"""A gate's sensors - closed, open, run: the state they give, and commands
that go out without conditions."""

from __future__ import annotations

import copy

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from custom_components.nexo.nexo_client import ImportTypes, NexoError

from .test_init import OPTIONS, _setup, _tick

GATE = "cover.nexo_entry_gate"
RUN = "RUN GATE"
REED = "KON GATE"
OPEN = "OPEN GATE"


def _options(**sensors: str | None) -> dict:
    options = copy.deepcopy(OPTIONS)
    entry_gate = next(c for c in options["covers"] if c["id"] == "entry")
    entry_gate["run_sensor"] = RUN
    for key, name in sensors.items():
        if name is None:
            entry_gate.pop(key, None)
        else:
            entry_gate[key] = name
    return options


@pytest.fixture
def gate(fake_nexo):
    fake_nexo.states[RUN] = 101
    fake_nexo.states[OPEN] = 102
    fake_nexo.listing[ImportTypes.SENSOR] = [*fake_nexo.listing[ImportTypes.SENSOR], RUN, OPEN]
    return fake_nexo


async def _read(hass: HomeAssistant, freezer, nexo, reed: int, run: int, opened: int = 102) -> str:
    """Set what the central unit reads, let the inputs group poll, return the state."""
    nexo.states[REED], nexo.states[RUN], nexo.states[OPEN] = reed, run, opened
    await _tick(hass, freezer, 5)
    return hass.states.get(GATE).state


async def test_open_run(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass, _options())
    assert hass.states.get(GATE).state == "closed"
    # The reed switch leaves closed before the run sensor comes on (1.2 s on
    # the entry gate): the start is still from closed
    assert await _read(hass, freezer, gate, 102, 101) == "open"
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    # Mid-way the gate reads not closed - still opening, not closing
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    assert await _read(hass, freezer, gate, 102, 101) == "open"


async def test_open_run_seen_before_the_reed_switch(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass, _options())
    assert await _read(hass, freezer, gate, 101, 102) == "opening"
    assert await _read(hass, freezer, gate, 102, 102) == "opening"


async def test_close_run(hass: HomeAssistant, gate, freezer) -> None:
    gate.states[REED] = 102
    await _setup(hass, _options())
    assert hass.states.get(GATE).state == "open"
    assert await _read(hass, freezer, gate, 102, 102) == "closing"
    # Closed is read about 2 s before the drive stops: still closing
    assert await _read(hass, freezer, gate, 101, 102) == "closing"
    assert await _read(hass, freezer, gate, 101, 101) == "closed"


async def test_after_a_full_open_the_next_run_closes(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass, _options())
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    assert await _read(hass, freezer, gate, 102, 101) == "open"
    assert await _read(hass, freezer, gate, 102, 102) == "closing"


async def test_stopped_halfway_is_open(hass: HomeAssistant, gate, freezer) -> None:
    gate.states[REED] = 102
    await _setup(hass, _options())
    assert await _read(hass, freezer, gate, 102, 102) == "closing"
    assert await _read(hass, freezer, gate, 102, 101) == "open"


async def test_missed_stop_between_runs(hass: HomeAssistant, gate, freezer) -> None:
    """Opened, then closed with a pause shorter than a read: the run sensor
    never reads still, but reaching closed shows the second run was a close."""
    await _setup(hass, _options())
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    assert await _read(hass, freezer, gate, 101, 102) == "closing"
    assert await _read(hass, freezer, gate, 101, 101) == "closed"


@pytest.mark.parametrize(("reed", "state"), [(101, "closed"), (102, "open")])
async def test_start_not_seen(hass: HomeAssistant, gate, freezer, reed: int, state: str) -> None:
    """Home Assistant started while the gate runs: no direction is guessed."""
    gate.states[REED], gate.states[RUN] = reed, 102
    await _setup(hass, _options())
    assert hass.states.get(GATE).state == state
    assert await _read(hass, freezer, gate, 102, 102) == "open"


async def test_unread_run_sensor_keeps_the_direction(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass, _options())
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    gate.states.pop(RUN)
    await _tick(hass, freezer, 5)
    assert await _read(hass, freezer, gate, 102, 102) == "opening"


async def test_without_run_sensor_no_direction(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass)
    assert await _read(hass, freezer, gate, 102, 102) == "open"


# ------------------------------------------------------------ open sensor


async def test_open_sensor_gives_open(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass, _options(open_sensor=OPEN))
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    assert await _read(hass, freezer, gate, 102, 101, opened=101) == "open"
    # From open the next run closes
    assert await _read(hass, freezer, gate, 102, 102, opened=102) == "closing"
    assert await _read(hass, freezer, gate, 101, 101) == "closed"


async def test_open_sensor_stopped_part_way_has_no_direction(
    hass: HomeAssistant, gate, freezer
) -> None:
    """With both end sensors, a gate stopped part-way may go either way next."""
    await _setup(hass, _options(open_sensor=OPEN))
    assert await _read(hass, freezer, gate, 102, 102) == "opening"
    assert await _read(hass, freezer, gate, 102, 101) == "open"
    assert await _read(hass, freezer, gate, 102, 102) == "open"


async def test_reaching_open_while_taken_for_closing(hass: HomeAssistant, gate, freezer) -> None:
    gate.states[REED], gate.states[OPEN] = 102, 101
    await _setup(hass, _options(open_sensor=OPEN))
    assert await _read(hass, freezer, gate, 102, 102, opened=102) == "closing"
    assert await _read(hass, freezer, gate, 102, 102, opened=101) == "opening"


async def test_open_sensor_alone(hass: HomeAssistant, gate, freezer) -> None:
    """No closed sensor: open is known, closed is not."""
    gate.states[OPEN] = 101
    await _setup(hass, _options(open_sensor=OPEN, reed_sensor=None, run_sensor=None))
    assert hass.states.get(GATE).state == "open"
    gate.states[OPEN] = 102
    await _tick(hass, freezer, 5)
    assert hass.states.get(GATE).state == "unknown"


# --------------------------------------------------------------- commands


@pytest.mark.parametrize(
    ("reed", "run", "service", "command"),
    [
        (102, 101, "open_cover", "GO"),  # not closed: the rule decides
        (101, 102, "close_cover", "GC"),  # running
        (101, 101, "close_cover", "GC"),  # already closed
    ],
)
async def test_commands_without_conditions(
    hass: HomeAssistant, gate, reed: int, run: int, service: str, command: str
) -> None:
    gate.states[REED], gate.states[RUN] = reed, run
    await _setup(hass, _options())
    await hass.services.async_call("cover", service, {"entity_id": GATE}, blocking=True)
    gate.trigger_logic.assert_called_once_with(command)


async def test_command_not_accepted(hass: HomeAssistant, gate) -> None:
    await _setup(hass, _options())
    gate.trigger_logic.side_effect = NexoError("no answer")
    with pytest.raises(HomeAssistantError, match="Entry gate was not opened: no answer"):
        await hass.services.async_call("cover", "open_cover", {"entity_id": GATE}, blocking=True)


async def test_both_commands_always_usable(hass: HomeAssistant, gate) -> None:
    await _setup(hass, _options())
    assert hass.states.get(GATE).attributes["assumed_state"] is True


async def test_sensors_polled_and_boosted(hass: HomeAssistant, gate) -> None:
    entry = await _setup(hass, _options(open_sensor=OPEN))
    coordinator = entry.runtime_data.coordinator
    assert {REED, RUN, OPEN} <= set(coordinator.resources)
    await hass.services.async_call("cover", "close_cover", {"entity_id": GATE}, blocking=True)
    assert {REED, RUN, OPEN} <= set(coordinator._boosted)


# ---------------------------------------------------------------- options


async def test_options_sensors_must_differ(hass: HomeAssistant, gate) -> None:
    entry = await _setup(hass, _options())
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "logic"})
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "covers"})
    result = await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "cover_0"})
    assert result["type"] is FlowResultType.FORM
    form = {
        "name": "Entry gate", "device_class": "gate", "open_command": "GO",
        "close_command": "GC", "reed_sensor": REED,
    }
    result = await hass.config_entries.options.async_configure(
        flow_id, {**form, "run_sensor": REED}
    )
    assert result["errors"] == {"run_sensor": "sensor_used_twice"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {**form, "open_sensor": RUN, "run_sensor": RUN}
    )
    assert result["errors"] == {"run_sensor": "sensor_used_twice"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {**form, "open_sensor": OPEN, "run_sensor": RUN}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["description_placeholders"]["cover_0_info"] == (
        f"GO / GC · {REED} · {OPEN} · {RUN}"
    )
