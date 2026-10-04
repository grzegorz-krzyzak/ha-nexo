"""Analogue (0-10 V) outputs: a level 0-100 %, written and read as a dimmer's."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.nexo.nexo_client import NexoClient, NexoCommandError
from custom_components.nexo.number import level_percent, percent_level

from .test_client_replies import Card
from .test_init import OPTIONS, _setup, _tick
from .test_thermostats import Commands
from .test_weather import WEATHER

OUTPUTS = {**OPTIONS, "analog_outputs": ["SPEED", "ROOF LEVEL"]}
SPEED, ROOF = "number.nexo_speed", "number.nexo_roof_level"


@pytest.mark.parametrize(("state", "percent"), [(0x8001, 50), (0x4001, 25), (0xBF01, 74), (0xFF01, 100), (0, 0)])
def test_level_reads_as_the_central_unit_words_it(state: int, percent: int) -> None:
    """Measured on the terrace roof output, 2026-10-04."""
    assert level_percent(state) == percent


def test_every_percentage_reads_back_unchanged() -> None:
    for percent in range(101):
        level = percent_level(percent)
        assert level_percent(level << 8 | 1 if level else 0) == percent


async def test_levels_come_from_reads(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, OUTPUTS)
    speed = hass.states.get(SPEED)
    assert speed.state == "50"
    assert speed.attributes["unit_of_measurement"] == "%"
    assert speed.attributes["mode"] == "slider"
    assert hass.states.get(ROOF).state == "0"


async def test_level_is_set_and_read_back(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, OUTPUTS)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": ROOF, "value": 75}, blocking=True
    )
    fake_nexo.set_analog_level.assert_called_once_with("ROOF LEVEL", 192)
    await _tick(hass, freezer, 1)
    assert hass.states.get(ROOF).state == "75"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": ROOF, "value": 0}, blocking=True
    )
    fake_nexo.set_analog_level.assert_called_with("ROOF LEVEL", 0)
    await _tick(hass, freezer, 1)
    assert hass.states.get(ROOF).state == "0"


async def test_refusal_is_an_error(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, OUTPUTS)
    fake_nexo.set_analog_level.side_effect = NexoCommandError("Nieznany obiekt")
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": SPEED, "value": 30}, blocking=True
        )


async def test_options_pick_outputs(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, OPTIONS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "analog_outputs"}
    )
    assert result["step_id"] == "analog_outputs"
    await hass.config_entries.options.async_configure(flow_id, {"analog_outputs": ["SPEED"]})
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "save"})
    assert entry.options["analog_outputs"] == ["SPEED"]


async def test_setup_keeps_imported_entities(hass: HomeAssistant, fake_nexo) -> None:
    """The weather station's and analogue outputs' entities were missing from
    the list of what setup keeps (0.9.0): removed at each setup, recreated."""
    entry = await _setup(hass, {**WEATHER, "analog_outputs": ["SPEED"]})
    registry = er.async_get(hass)
    before = {e.entity_id: e.id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    after = {e.entity_id: e.id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert after == before
    assert "sensor.weather_station_temperature" in after and SPEED in after


# ------------------------------------------------------------------ the client


@pytest.fixture
def client():
    with patch("custom_components.nexo.nexo_client.time.sleep"):
        yield NexoClient("192.0.2.1", "pw", auto_connect=False)


def test_analog_level_wire_format(client) -> None:
    card = Commands()
    with patch.object(client, "_command_retrying", card):
        client.set_analog_level("JAS ZADASZ TARAS", 64)
        client.set_analog_level("JAS ZADASZ TARAS", 0)
    assert card.sent == ["system C 'JAS ZADASZ TARAS' 16385", "system C 'JAS ZADASZ TARAS' 0"]


@pytest.mark.parametrize("level", [-1, 256, True, 1.5])
def test_analog_level_out_of_range(client, level) -> None:
    with pytest.raises(ValueError):
        client.set_analog_level("X", level)
