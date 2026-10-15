"""A gate's run signal: opening / closing states and no commands while it runs."""

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


def _options() -> dict:
    options = copy.deepcopy(OPTIONS)
    entry_gate = next(c for c in options["covers"] if c["id"] == "entry")
    entry_gate["run_sensor"] = RUN
    return options


@pytest.fixture
def gate(fake_nexo):
    fake_nexo.states[RUN] = 101
    fake_nexo.listing[ImportTypes.SENSOR] = [*fake_nexo.listing[ImportTypes.SENSOR], RUN]
    return fake_nexo


async def _read(hass: HomeAssistant, freezer, reed: int, run: int, nexo) -> str:
    """Set what the central unit reads, let the inputs group poll, return the state."""
    nexo.states[REED], nexo.states[RUN] = reed, run
    await _tick(hass, freezer, 5)
    return hass.states.get(GATE).state


async def test_open_run(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass, _options())
    assert hass.states.get(GATE).state == "closed"
    # The reed switch leaves closed before the run signal comes on; both in one read
    assert await _read(hass, freezer, 102, 102, gate) == "opening"
    # Mid-way the gate reads not closed - still opening, not closing
    assert await _read(hass, freezer, 102, 102, gate) == "opening"
    assert await _read(hass, freezer, 102, 101, gate) == "open"


async def test_open_run_seen_before_the_reed_switch(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass, _options())
    assert await _read(hass, freezer, 101, 102, gate) == "opening"
    assert await _read(hass, freezer, 102, 102, gate) == "opening"


async def test_close_run(hass: HomeAssistant, gate, freezer) -> None:
    gate.states[REED] = 102
    await _setup(hass, _options())
    assert hass.states.get(GATE).state == "open"
    assert await _read(hass, freezer, 102, 102, gate) == "closing"
    # Closed is read about 2 s before the drive stops: still closing
    assert await _read(hass, freezer, 101, 102, gate) == "closing"
    assert await _read(hass, freezer, 101, 101, gate) == "closed"


async def test_stopped_halfway_is_open(hass: HomeAssistant, gate, freezer) -> None:
    gate.states[REED] = 102
    await _setup(hass, _options())
    assert await _read(hass, freezer, 102, 102, gate) == "closing"
    assert await _read(hass, freezer, 102, 101, gate) == "open"


async def test_missed_stop_between_runs(hass: HomeAssistant, gate, freezer) -> None:
    """Opened, then closed with a pause shorter than a read: the run signal
    never reads still, but reaching closed shows the second run was a close."""
    await _setup(hass, _options())
    assert await _read(hass, freezer, 102, 102, gate) == "opening"
    assert await _read(hass, freezer, 102, 102, gate) == "opening"
    assert await _read(hass, freezer, 101, 102, gate) == "closing"
    assert await _read(hass, freezer, 101, 101, gate) == "closed"


@pytest.mark.parametrize(("reed", "state"), [(101, "opening"), (102, "closing")])
async def test_start_not_seen(hass: HomeAssistant, gate, reed: int, state: str) -> None:
    """Home Assistant started while the gate runs: a guess, no error."""
    gate.states[REED], gate.states[RUN] = reed, 102
    await _setup(hass, _options())
    assert hass.states.get(GATE).state == state


async def test_without_run_signal_nothing_changes(hass: HomeAssistant, gate, freezer) -> None:
    await _setup(hass)
    gate.states[RUN] = 102
    gate.states[REED] = 102
    await _tick(hass, freezer, 5)
    assert hass.states.get(GATE).state == "open"
    await hass.services.async_call("cover", "close_cover", {"entity_id": GATE}, blocking=True)
    gate.trigger_logic.assert_called_once_with("GC")


@pytest.fixture
def quick(monkeypatch):
    """Confirmation waits shortened from seconds to milliseconds."""
    from custom_components.nexo import cover

    monkeypatch.setattr(cover, "CONFIRM_START", 0.05)
    monkeypatch.setattr(cover, "CONFIRM_MOVING", 0.05)
    monkeypatch.setattr(cover, "CONFIRM_POLL", 0.01)


def _drive_starts(gate) -> None:
    gate.trigger_logic.side_effect = lambda command: gate.states.__setitem__(RUN, 102)


def _unguarded() -> dict:
    options = _options()
    next(c for c in options["covers"] if c["id"] == "entry")["open_only_when_closed"] = False
    return options


async def _call(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call("cover", service, {"entity_id": GATE}, blocking=True)


async def test_open_confirmed_when_the_drive_starts(hass: HomeAssistant, gate, quick) -> None:
    await _setup(hass, _options())
    _drive_starts(gate)
    await _call(hass, "open_cover")
    gate.trigger_logic.assert_called_once_with("GO")


async def test_open_confirmed_by_the_reed_switch_first(hass: HomeAssistant, gate, quick) -> None:
    """Opening from closed: the reed switch leaves closed before the run signal comes on."""
    await _setup(hass, _options())
    gate.trigger_logic.side_effect = lambda command: gate.states.__setitem__(REED, 102)
    await _call(hass, "open_cover")
    gate.trigger_logic.assert_called_once_with("GO")


async def test_close_not_confirmed_by_the_reed_switch(hass: HomeAssistant, gate, quick) -> None:
    """Closing: only the run signal counts - the reed switch changes at the end."""
    gate.states[REED] = 102
    await _setup(hass, _options())
    gate.trigger_logic.side_effect = lambda command: gate.states.__setitem__(REED, 101)
    with pytest.raises(HomeAssistantError, match="did not start"):
        await _call(hass, "close_cover")


async def test_rule_not_carried_out(hass: HomeAssistant, gate, quick) -> None:
    """Closed, still, the command sent - and the drive does not start."""
    await _setup(hass, _options())
    with pytest.raises(HomeAssistantError, match="did not start within .* did not carry out GO"):
        await _call(hass, "open_cover")
    gate.trigger_logic.assert_called_once_with("GO")


async def test_not_closed_is_the_reason(hass: HomeAssistant, gate, quick) -> None:
    """Without the guard the rule in the central unit refuses; the error says why."""
    gate.states[REED] = 102
    await _setup(hass, _unguarded())
    with pytest.raises(HomeAssistantError, match="was not opened: it is not closed"):
        await _call(hass, "open_cover")
    gate.trigger_logic.assert_called_once_with("GO")


@pytest.mark.parametrize(("service", "verb"), [("open_cover", "opened"), ("close_cover", "closed")])
async def test_moving_is_the_reason(
    hass: HomeAssistant, gate, quick, service: str, verb: str
) -> None:
    await _setup(hass, _unguarded())
    gate.states[REED], gate.states[RUN] = 102, 102  # started by a remote since the last poll
    with pytest.raises(HomeAssistantError, match=f"was not {verb}: it is moving"):
        await _call(hass, service)


async def test_command_stopped_a_moving_gate(hass: HomeAssistant, gate, quick) -> None:
    """A rule without the run condition: the command halts the gate - said so."""
    await _setup(hass, _unguarded())
    gate.states[REED], gate.states[RUN] = 102, 102
    gate.trigger_logic.side_effect = lambda command: gate.states.__setitem__(RUN, 101)
    with pytest.raises(HomeAssistantError, match="stopped right after GC"):
        await _call(hass, "close_cover")


async def test_close_when_closed_needs_no_start(hass: HomeAssistant, gate, quick) -> None:
    await _setup(hass, _options())
    await _call(hass, "close_cover")
    gate.trigger_logic.assert_called_once_with("GC")


def _unreadable_run(gate) -> None:
    read = gate.get_state

    def get_state(name: str) -> int:
        if name == RUN:
            raise NexoError("no answer")
        return read(name)

    gate.get_state = get_state


@pytest.mark.parametrize("service", ["open_cover", "close_cover"])
async def test_unreadable_run_signal_is_not_confirmed(
    hass: HomeAssistant, gate, quick, service: str
) -> None:
    await _setup(hass, _options())
    _unreadable_run(gate)
    await _call(hass, service)
    gate.trigger_logic.assert_called_once()


async def test_run_signal_polled_and_boosted(hass: HomeAssistant, gate) -> None:
    entry = await _setup(hass, _options())
    coordinator = entry.runtime_data.coordinator
    assert RUN in coordinator.resources
    await hass.services.async_call("cover", "close_cover", {"entity_id": GATE}, blocking=True)
    assert RUN in coordinator._boosted


async def test_options_run_signal_must_differ_from_reed(hass: HomeAssistant, gate) -> None:
    entry = await _setup(hass, _options())
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "logic"})
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "covers"})
    result = await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "cover_0"})
    assert result["type"] is FlowResultType.FORM
    form = {
        "name": "Entry gate", "device_class": "gate", "open_command": "GO",
        "close_command": "GC", "reed_sensor": REED, "open_only_when_closed": True,
    }
    result = await hass.config_entries.options.async_configure(
        flow_id, {**form, "run_sensor": REED}
    )
    assert result["errors"] == {"run_sensor": "run_sensor_is_reed"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {**form, "run_sensor": RUN}
    )
    assert result["type"] is FlowResultType.MENU
