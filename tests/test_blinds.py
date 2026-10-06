"""Blind outputs: raise, lower and stop with 'system C' 2 / 1 / 0, the motion
from the state word (measured on BRAMA WJ, 2026-10-06)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.nexo.nexo_client import NexoClient, NexoCommandError

from .test_config_flow import _choices, _pick, _submit
from .test_init import OPTIONS, _setup, _tick
from .test_thermostats import Commands

BLINDS = {**OPTIONS, "blinds": ["SHUTTER", "AWNING"], "blind_classes": {"AWNING": "awning"}}
SHUTTER, AWNING = "cover.nexo_shutter", "cover.nexo_awning"


async def test_blind_state_from_the_word(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, BLINDS)
    shutter = hass.states.get(SHUTTER)
    # No position: stopped reads as unknown, both buttons usable
    assert shutter.state == "unknown"
    assert shutter.attributes["assumed_state"] is True
    assert shutter.attributes["device_class"] == "shutter"
    assert hass.states.get(AWNING).attributes["device_class"] == "awning"
    fake_nexo.states["SHUTTER"] = 2
    await _tick(hass, freezer, 10)
    assert hass.states.get(SHUTTER).state == "opening"
    fake_nexo.states["SHUTTER"] = 1
    await _tick(hass, freezer, 10)
    assert hass.states.get(SHUTTER).state == "closing"


@pytest.mark.parametrize(
    ("service", "value", "state"),
    [("open_cover", 2, "opening"), ("close_cover", 1, "closing"), ("stop_cover", 0, "unknown")],
)
async def test_commands_and_read_back(
    hass: HomeAssistant, fake_nexo, freezer, service: str, value: int, state: str
) -> None:
    await _setup(hass, BLINDS)
    await hass.services.async_call("cover", service, {"entity_id": SHUTTER}, blocking=True)
    fake_nexo.move_blind.assert_called_once_with("SHUTTER", value)
    # Boosted: read on the next tick, not at the outputs' interval
    await _tick(hass, freezer, 1)
    assert hass.states.get(SHUTTER).state == state


async def test_refusal_is_an_error(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, BLINDS)
    fake_nexo.move_blind.side_effect = NexoCommandError("Nieznany obiekt")
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await hass.services.async_call("cover", "open_cover", {"entity_id": SHUTTER}, blocking=True)


async def test_never_offered_wins_over_blind(hass: HomeAssistant, fake_nexo) -> None:
    """Hand-edited options with a blind also never offered: no entity."""
    await _setup(hass, {**BLINDS, "excluded": ["SHUTTER"]})
    assert hass.states.get(SHUTTER) is None
    assert hass.states.get(AWNING) is not None


async def test_options_pick_blinds_and_their_class(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, OPTIONS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await _pick(hass, flow_id, "blinds")
    assert sorted(_choices(result, "blinds")) == ["AWNING", "SHUTTER"]
    result = await _submit(hass, flow_id, {"excluded": ["AWNING"], "blinds": ["SHUTTER"]})
    assert result["step_id"] == "blinds_class"
    result = await _submit(hass, flow_id, {"SHUTTER": "gate"})
    assert result["description_placeholders"]["blinds"] == "1"
    await _pick(hass, flow_id, "save")
    assert entry.options["blinds"] == ["SHUTTER"]
    assert entry.options["blind_classes"] == {"SHUTTER": "gate"}
    assert "AWNING" in entry.options["excluded"]


async def test_default_class_is_not_stored(hass: HomeAssistant, fake_nexo) -> None:
    """A class set back to the default, or of a blind no longer picked, goes."""
    entry = await _setup(hass, BLINDS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "blinds")
    await _submit(hass, flow_id, {"excluded": [], "blinds": ["SHUTTER"]})
    await _submit(hass, flow_id, {"SHUTTER": "shutter"})
    await _pick(hass, flow_id, "save")
    assert entry.options["blinds"] == ["SHUTTER"]
    assert entry.options["blind_classes"] == {}


async def test_no_blinds_skips_the_class_form(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, BLINDS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "blinds")
    result = await _submit(hass, flow_id, {"excluded": [], "blinds": []})
    assert result["type"] == "menu"
    await _pick(hass, flow_id, "save")
    assert entry.options["blinds"] == []
    assert entry.options["blind_classes"] == {}


# ------------------------------------------------- names the central unit lost


async def test_saving_a_screen_drops_names_the_central_unit_lost(
    hass: HomeAssistant, fake_nexo
) -> None:
    """Deleted in the central unit, a never-offered output stayed in the
    options for good - no list shows it to untick (2026-10-06)."""
    entry = await _setup(hass, {**OPTIONS, "excluded": ["GONE PULSE", "GATE PULSE", "SHUTTER"]})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "outputs")
    await _submit(hass, flow_id, {"excluded": [], "output_sensors": [], "switches": []})
    await _pick(hass, flow_id, "save")
    # Other types' names kept, the lost one dropped
    assert entry.options["excluded"] == ["GATE PULSE", "SHUTTER"]


async def test_setup_drops_nothing(hass: HomeAssistant, fake_nexo) -> None:
    """Only a save from the menu drops a lost name: a list read wrong at
    setup must not delete configuration."""
    entry = await _setup(hass, {**OPTIONS, "excluded": ["GONE PULSE"]})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options["excluded"] == ["GONE PULSE"]


async def test_a_list_that_cannot_be_read_drops_nothing(hass: HomeAssistant, fake_nexo) -> None:
    from custom_components.nexo.nexo_client import ImportTypes, NexoError

    entry = await _setup(hass, {**OPTIONS, "excluded": ["GONE PULSE"]})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "outputs")
    hub = entry.runtime_data.hub
    hub._resources.pop(ImportTypes.BLIND, None)
    real = fake_nexo.list_resources

    def failing(resource_type):
        if resource_type is ImportTypes.BLIND:
            raise NexoError("no answer")
        return real(resource_type)

    with patch.object(fake_nexo, "list_resources", side_effect=failing):
        result = await _submit(hass, flow_id, {"excluded": [], "output_sensors": [], "switches": []})
    assert result["type"] == "abort"
    assert entry.options["excluded"] == ["GONE PULSE"]


# ------------------------------------------------------------------ the client


@pytest.fixture
def client():
    with patch("custom_components.nexo.nexo_client.time.sleep"):
        yield NexoClient("192.0.2.1", "pw", auto_connect=False)


def test_blind_wire_format(client) -> None:
    card = Commands()
    with patch.object(client, "_command_retrying", card):
        client.move_blind("BRAMA WJ", 2)
        client.move_blind("BRAMA WJ", 1)
        client.move_blind("BRAMA WJ", 0)
    assert card.sent == [
        "system C 'BRAMA WJ' 2", "system C 'BRAMA WJ' 1", "system C 'BRAMA WJ' 0",
    ]


@pytest.mark.parametrize("value", [-1, 3, True, 1.0])
def test_blind_value_out_of_range(client, value) -> None:
    with pytest.raises(ValueError):
        client.move_blind("X", value)
