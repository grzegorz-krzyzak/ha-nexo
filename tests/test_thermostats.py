"""Thermostats: heating (the default) or cooling as set per thermostat, state from reads only."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from custom_components.nexo.nexo_client import (
    NexoClient,
    NexoCommandError,
    ThermostatInfo,
    ThermostatState,
)

from .test_client_replies import EMPTY, Card
from .test_init import OPTIONS, _setup, _tick

THERMOSTATS = {
    **OPTIONS,
    "thermostats": [
        {"name": "TRS HALL", "thermometer": "TMP HALL", "min": 15, "max": 30},
        {"name": "TRS GARAGE", "thermometer": "TMP OUTSIDE", "min": 10, "max": 30},
    ],
}
HALL, GARAGE = "climate.nexo_trs_hall", "climate.nexo_trs_garage"


def test_state_unpacks_as_measured() -> None:
    # TRS GARAZ, 2026-10-03: threshold 30 active output off; 10 with output on; off
    assert ThermostatState.unpack(0x012C0100) == ThermostatState(30.0, True, False)
    assert ThermostatState.unpack(0x00640101) == ThermostatState(10.0, True, True)
    assert ThermostatState.unpack(0x012C0000) == ThermostatState(30.0, False, False)


async def test_states_come_from_reads(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, THERMOSTATS)
    hall = hass.states.get(HALL)
    assert hall.state == "heat"  # no direction stored: heating, as in the central unit
    assert hall.attributes["hvac_action"] == "idle"  # 23.3 is above 21: output on, warm enough
    assert hall.attributes["temperature"] == 21.0
    assert hall.attributes["current_temperature"] == 23.3
    assert (hall.attributes["min_temp"], hall.attributes["max_temp"]) == (15, 30)
    assert hall.attributes["hvac_modes"] == ["heat", "off"]
    garage = hass.states.get(GARAGE)
    assert garage.state == "off"
    assert garage.attributes["hvac_action"] == "off"
    assert garage.attributes["current_temperature"] == -2.0
    assert garage.attributes["min_temp"] == 10


async def test_threshold_is_set_and_read_back(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, THERMOSTATS)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": HALL, "temperature": 24.6}, blocking=True
    )
    # in tenths, one command, as NexoVision does; the thermostat stays on
    fake_nexo.write_thermostat.assert_called_once_with("TRS HALL", 24.6, True)
    fake_nexo.set_thermostat.assert_not_called()
    await _tick(hass, freezer, 1)  # boosted, read within a second
    hall = hass.states.get(HALL)
    assert hall.attributes["temperature"] == 24.6
    assert hall.attributes["hvac_action"] == "heating"  # 23.3 is below 25: output off


async def test_threshold_in_tenths_is_shown_as_read(hass: HomeAssistant, fake_nexo, freezer) -> None:
    """NexoVision can set 18.2; Home Assistant must not round it to 18."""
    await _setup(hass, THERMOSTATS)
    fake_nexo.states["TRS HALL"] = 182 << 16 | 0x0101
    await _tick(hass, freezer, 60)
    assert hass.states.get(HALL).attributes["temperature"] == 18.2


async def test_threshold_keeps_an_off_thermostat_off(
    hass: HomeAssistant, fake_nexo, freezer
) -> None:
    """Home Assistant expects the mode to stay: the threshold goes with the
    thermostat's active flag off, in the same command - never on, even briefly."""
    await _setup(hass, THERMOSTATS)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": GARAGE, "temperature": 12.5}, blocking=True
    )
    fake_nexo.write_thermostat.assert_called_once_with("TRS GARAGE", 12.5, False)
    fake_nexo.thermostat_on.assert_not_called()
    fake_nexo.thermostat_off.assert_not_called()
    await _tick(hass, freezer, 1)
    garage = hass.states.get(GARAGE)
    assert garage.state == "off"
    assert garage.attributes["temperature"] == 12.5


async def test_threshold_with_a_mode_is_one_command(
    hass: HomeAssistant, fake_nexo, freezer
) -> None:
    await _setup(hass, THERMOSTATS)
    await hass.services.async_call(
        "climate", "set_temperature",
        {"entity_id": GARAGE, "temperature": 21, "hvac_mode": "heat"}, blocking=True,
    )
    fake_nexo.write_thermostat.assert_called_once_with("TRS GARAGE", 21.0, True)
    fake_nexo.thermostat_on.assert_not_called()
    await _tick(hass, freezer, 1)
    assert hass.states.get(GARAGE).state == "heat"


async def test_mode_switches_the_thermostat(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, THERMOSTATS)
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": GARAGE, "hvac_mode": "heat"}, blocking=True
    )
    fake_nexo.thermostat_on.assert_called_once_with("TRS GARAGE")
    await hass.services.async_call("climate", "turn_off", {"entity_id": HALL}, blocking=True)
    fake_nexo.thermostat_off.assert_called_once_with("TRS HALL")
    await _tick(hass, freezer, 1)
    assert hass.states.get(GARAGE).state == "heat"
    assert hass.states.get(GARAGE).attributes["hvac_action"] == "heating"  # -2.0 below 30
    assert hass.states.get(HALL).state == "off"


async def test_a_cooling_thermostat(hass: HomeAssistant, fake_nexo, freezer) -> None:
    """Negative hysteresis in the central unit: set to cool in the options."""
    options = {**THERMOSTATS, "thermostats": [
        {**THERMOSTATS["thermostats"][1], "direction": "cool"},
    ]}
    await _setup(hass, options)
    assert hass.states.get(GARAGE).attributes["hvac_modes"] == ["cool", "off"]
    # Home Assistant itself refuses a mode the entity does not offer
    with pytest.raises(HomeAssistantError, match="not valid"):
        await hass.services.async_call(
            "climate", "set_hvac_mode", {"entity_id": GARAGE, "hvac_mode": "heat"}, blocking=True
        )
    await hass.services.async_call("climate", "turn_on", {"entity_id": GARAGE}, blocking=True)
    fake_nexo.thermostat_on.assert_called_once_with("TRS GARAGE")
    await _tick(hass, freezer, 1)
    garage = hass.states.get(GARAGE)
    assert garage.state == "cool"
    assert garage.attributes["hvac_action"] == "cooling"  # output off: asks for cold


async def test_refusal_is_an_error_and_changes_nothing(
    hass: HomeAssistant, fake_nexo, freezer
) -> None:
    await _setup(hass, THERMOSTATS)
    fake_nexo.write_thermostat.side_effect = NexoCommandError("Nieznany obiekt")
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": HALL, "temperature": 20}, blocking=True
        )
    await _tick(hass, freezer, 1)
    assert hass.states.get(HALL).attributes["temperature"] == 21.0


async def test_options_store_thermometer_and_range(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, OPTIONS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "thermostats"}
    )
    assert result["step_id"] == "thermostats"
    result = await hass.config_entries.options.async_configure(
        flow_id, {"thermostats": ["TRS GARAGE", "TRS HALL"]}
    )
    assert result["step_id"] == "thermostats_direction"
    assert [str(field) for field in result["data_schema"].schema] == ["TRS GARAGE", "TRS HALL"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"TRS GARAGE": "cool", "TRS HALL": "heat"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["description_placeholders"]["thermostats"] == "2"
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "save"})
    assert entry.options["thermostats"] == [
        {"name": "TRS GARAGE", "thermometer": "TMP OUTSIDE", "min": 10, "max": 30,
         "direction": "cool"},
        {"name": "TRS HALL", "thermometer": "TMP HALL", "min": 15, "max": 30,
         "direction": "heat"},
    ]


# ------------------------------------------------------------------ the client


@pytest.fixture
def client():
    with patch("custom_components.nexo.nexo_client.time.sleep"):
        yield NexoClient("192.0.2.1", "pw", auto_connect=False)


class Commands(Card):
    """Records the commands sent, besides the polls."""

    def __init__(self, replies: list[str] | None = None) -> None:
        super().__init__(replies)
        self.sent: list[str] = []

    def __call__(self, data: str, log_as: str | None = None) -> str:
        if data != "get":
            self.sent.append(data)
        return super().__call__(data, log_as)


@pytest.mark.parametrize(
    ("call", "wire"),
    [
        (lambda c: c.set_thermostat(29, "TRS GARAZ"), "system command ustaw 29 'TRS GARAZ'"),
        (lambda c: c.thermostat_on("TRS GARAZ"), "system command ustaw + 'TRS GARAZ'"),
        (lambda c: c.thermostat_off("TRS GARAZ"), "system command ustaw - 'TRS GARAZ'"),
    ],
)
def test_thermostat_commands(client, call, wire) -> None:
    """The threshold goes without a sign: a bare + / - switches on / off."""
    card = Commands()
    with patch.object(client, "_command_retrying", card):
        call(client)
    assert card.sent == [wire]
    assert card.gets == NexoClient.CONTROL_REPLY_POLLS


def test_thermostat_written_in_tenths_with_the_active_flag(client) -> None:
    """Captured from NexoVision: 18.4 went as system C 'TRS GARAZ' 47105 (0xB801)."""
    card = Commands()
    with patch.object(client, "_command_retrying", card):
        client.write_thermostat("TRS GARAZ", 18.4, True)
        client.write_thermostat("TRS GARAZ", 18.5, False)
    assert card.sent == ["system C 'TRS GARAZ' 47105", "system C 'TRS GARAZ' 47360"]


def test_thermostat_list_entries(client) -> None:
    card = Commands([
        EMPTY + "~T 17 0 TRS LAZ PARTER\nTMP LAZ PARTER\n15\n30",
        EMPTY + "~T 17 1 TRS GARAZ\nTMP GARAZ\n10\n30",
        EMPTY + "~T 17 2",
    ])
    with patch.object(client, "_command_retrying", card):
        assert client.list_thermostats() == [
            ThermostatInfo("TRS LAZ PARTER", "TMP LAZ PARTER", 15, 30),
            ThermostatInfo("TRS GARAZ", "TMP GARAZ", 10, 30),
        ]
    assert card.sent == [f"system T 17 {i} ?" for i in range(3)]


def test_list_resources_still_returns_names(client) -> None:
    card = Commands([EMPTY + "~T 17 0 TRS GARAZ\nTMP GARAZ\n10\n30", EMPTY + "~T 17 1"])
    with patch.object(client, "_command_retrying", card):
        assert client.list_resources("THERMOSTAT") == ["TRS GARAZ"]
