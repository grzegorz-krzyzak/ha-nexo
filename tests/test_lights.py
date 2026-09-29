"""Lights, dimmers and switches: state from reads only, commands, levels."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.nexo.nexo_client import NexoCommandError, NexoConnectionError

from .test_init import OPTIONS, _setup, _tick

LIGHTS = {**OPTIONS, "lights": ["L1"], "dimmers": ["DIM A"], "switches": ["VENT"]}
LIGHT, DIMMER, SWITCH = "light.nexo_l1", "light.nexo_dim_a", "switch.nexo_vent"


async def test_states_come_from_reads(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, LIGHTS)
    assert hass.states.get(LIGHT).state == "off"
    assert hass.states.get(SWITCH).state == "off"
    dimmer = hass.states.get(DIMMER)
    assert dimmer.state == "on"
    assert dimmer.attributes["brightness"] == 128  # high byte of 0x8001


async def test_turn_on_waits_for_the_read(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, LIGHTS)
    await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT}, blocking=True)
    fake_nexo.turn_on.assert_called_once_with("L1")
    # Not optimistic: nothing read yet, still off
    assert hass.states.get(LIGHT).state == "off"
    fake_nexo.states["L1"] = 0xFF01
    await _tick(hass, freezer, 1)  # boosted, read within a second
    assert hass.states.get(LIGHT).state == "on"


async def test_switch_turns_on_and_off(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, LIGHTS)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    fake_nexo.turn_on.assert_called_once_with("VENT")
    fake_nexo.turn_off.assert_called_once_with("VENT")


async def test_dimmer_brightness_is_written_as_a_level(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, LIGHTS)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": DIMMER, "brightness": 64}, blocking=True
    )
    fake_nexo.set_level.assert_called_once_with("DIM A", 64)
    fake_nexo.turn_on.assert_not_called()  # 'wlacz' would mean full level


async def test_dimmer_on_without_brightness_is_the_central_units_own(
    hass: HomeAssistant, fake_nexo, freezer
) -> None:
    await _setup(hass, LIGHTS)
    fake_nexo.states["DIM A"] = 0  # switched off at the wall
    await _tick(hass, freezer, 10)
    await hass.services.async_call("light", "turn_on", {"entity_id": DIMMER}, blocking=True)
    fake_nexo.turn_on.assert_called_once_with("DIM A")
    fake_nexo.set_level.assert_not_called()


async def test_dimmer_level_is_at_least_one(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, LIGHTS)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": DIMMER, "brightness": 1}, blocking=True
    )
    fake_nexo.set_level.assert_called_once_with("DIM A", 1)


async def test_dimmer_off_uses_the_text_command(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, LIGHTS)
    await hass.services.async_call("light", "turn_off", {"entity_id": DIMMER}, blocking=True)
    fake_nexo.turn_off.assert_called_once_with("DIM A")


@pytest.mark.parametrize("error", [NexoCommandError("refused"), NexoConnectionError("gone")])
async def test_a_failed_command_leaves_the_state(
    hass: HomeAssistant, fake_nexo, freezer, error
) -> None:
    await _setup(hass, LIGHTS)
    fake_nexo.turn_on.side_effect = error
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT}, blocking=True)
    await _tick(hass, freezer, 1)
    assert hass.states.get(LIGHT).state == "off"


async def test_lights_are_read_with_their_group(hass: HomeAssistant, fake_nexo, freezer) -> None:
    from .test_init import _count_reads

    await _setup(hass, {**LIGHTS, "interval_lights": 7})
    reads = _count_reads(fake_nexo)
    for _ in range(6):
        await _tick(hass, freezer, 1)
    assert "L1" not in reads
    await _tick(hass, freezer, 1)
    assert {"L1", "DIM A", "VENT"} <= set(reads)
