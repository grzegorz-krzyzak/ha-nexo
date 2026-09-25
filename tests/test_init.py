"""Entities, commands and the gate guard, against a fake central unit."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.nexo import _firmware_version
from custom_components.nexo.const import DOMAIN

OPTIONS = {
    "binary_sensors": ["KON DOOR", "PIR HALL"],
    "thermometers": ["TMP HALL", "TMP OUTSIDE"],
    "analog_sensors": ["HUMIDITY"],
    "covers": [
        {
            "id": "entry",
            "name": "Entry gate",
            "device_class": "gate",
            "open_command": "GO",
            "close_command": "GC",
            "reed_sensor": "KON GATE",
            "open_only_when_closed": True,
        },
        {
            "id": "garage",
            "name": "Garage",
            "device_class": "garage",
            "open_command": "GGO",
            "close_command": "GGC",
            "reed_sensor": "KON DOOR",
            "open_only_when_closed": False,
        },
        {
            "id": "shed",
            "name": "Shed",
            "device_class": "door",
            "open_command": "SO",
            "close_command": "SC",
            "open_only_when_closed": False,
        },
    ],
    "buttons": [{"id": "wicket", "name": "Wicket", "command": "WK"}],
}


async def _setup(hass: HomeAssistant, options=OPTIONS) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.0.2.10",
        title="Nexo 192.0.2.10",
        data={CONF_HOST: "192.0.2.10", CONF_PORT: 1024, CONF_PASSWORD: "pw"},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_states(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    assert hass.states.get("binary_sensor.nexo_kon_door").state == "off"
    assert hass.states.get("binary_sensor.nexo_pir_hall").state == "on"
    assert hass.states.get("sensor.nexo_tmp_hall").state == "23.3"
    assert hass.states.get("sensor.nexo_tmp_outside").state == "-2.0"
    assert hass.states.get("sensor.nexo_humidity").state == "55"
    assert hass.states.get("cover.nexo_entry_gate").state == "closed"
    assert hass.states.get("cover.nexo_garage").state == "closed"


async def test_guarded_open_when_closed(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.nexo_entry_gate"}, blocking=True
    )
    fake_nexo.trigger_logic.assert_called_once_with("GO")


async def test_guarded_open_refused_when_not_closed(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    # Moved by a remote since the last poll: the guard must read it fresh.
    fake_nexo.states["KON GATE"] = 102
    with pytest.raises(HomeAssistantError, match="not confirmed closed"):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": "cover.nexo_entry_gate"}, blocking=True
        )
    fake_nexo.trigger_logic.assert_not_called()


async def test_unguarded_open_while_open(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    fake_nexo.states["KON DOOR"] = 102
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.nexo_garage"}, blocking=True
    )
    fake_nexo.trigger_logic.assert_called_once_with("GGO")


async def test_close_is_unconditional(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": "cover.nexo_entry_gate"}, blocking=True
    )
    fake_nexo.trigger_logic.assert_called_once_with("GC")


async def test_button(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.nexo_wicket"}, blocking=True
    )
    fake_nexo.trigger_logic.assert_called_once_with("WK")


async def test_deselected_entities_are_removed(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    registry = er.async_get(hass)
    assert registry.async_get("sensor.nexo_humidity")

    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "analog_sensors": []})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("sensor.nexo_humidity") is None


async def test_cover_without_reed_switch(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    assert hass.states.get("cover.nexo_shed").state == "unknown"
    for service, command in (("open_cover", "SO"), ("close_cover", "SC")):
        await hass.services.async_call(
            "cover", service, {"entity_id": "cover.nexo_shed"}, blocking=True
        )
        fake_nexo.trigger_logic.assert_called_with(command)


async def test_device_shows_firmware(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.sw_version == "5.53 R1PLX1H2"
    assert device.manufacturer == "Nexwell"


@pytest.mark.parametrize(
    ("info", "version"),
    [
        ("Nexo 5.53 R1PLX1H2. Czas dzialania: 48 dn. 5 godz. 49 min", "5.53 R1PLX1H2"),
        ("Nexo 5.53 R1PLX1H2.", "5.53 R1PLX1H2"),
        ("Nexo 6.0", "6.0"),
        ("", None),
        ("something else", None),
    ],
)
def test_firmware_version_parsing(info: str, version: str | None) -> None:
    assert _firmware_version(info) == version
