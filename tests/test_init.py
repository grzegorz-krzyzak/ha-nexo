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
            "travel_time": 27,
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
        title="Nexo 192.0.2.10",  # the 0.1.x default, migrated at setup
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


async def test_legacy_title_migrated(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    assert entry.title == "Nexo · 192.0.2.10:1024"


async def test_connection_sensor(hass: HomeAssistant, fake_nexo, freezer) -> None:
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry = await _setup(hass)
    entity_id = "binary_sensor.nexo_connection_to_central_unit"
    assert hass.states.get(entity_id).state == "on"

    from custom_components.nexo.nexo_client import NexoConnectionError

    def down(name):
        raise NexoConnectionError("gone")

    fake_nexo.get_state = down
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "off"  # still available, reporting the loss
    assert hass.states.get("sensor.nexo_tmp_hall").state == "unavailable"
    assert entry.state.name == "LOADED"


async def test_reload_after_failed_cycles(hass: HomeAssistant, fake_nexo, freezer) -> None:
    from datetime import timedelta
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.nexo.nexo_client import NexoConnectionError

    entry = await _setup(hass)

    def down(name):
        raise NexoConnectionError("gone")

    fake_nexo.get_state = down
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        for _ in range(3):
            freezer.tick(timedelta(seconds=11))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
    reload.assert_called_once_with(entry.entry_id)


async def test_step_button_cycles_like_a_remote(hass: HomeAssistant, fake_nexo) -> None:
    from unittest.mock import patch

    await _setup(hass)
    clock = iter([0.0, 10.0, 15.0, 20.0])
    entity_id = "button.nexo_garage_step"
    assert hass.states.get(entity_id)
    with patch("custom_components.nexo.motion.monotonic", side_effect=lambda: next(clock)):
        for expected, reed in (("GGO", 101), ("GGC", 102), ("GGC", 102), ("GGO", 102)):
            fake_nexo.states["KON DOOR"] = reed
            await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
            assert fake_nexo.trigger_logic.call_args.args == (expected,)


async def test_toggle_steps_when_travel_time_is_set(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    fake_nexo.states["KON DOOR"] = 101
    await hass.services.async_call("cover", "toggle", {"entity_id": "cover.nexo_garage"}, blocking=True)
    fake_nexo.trigger_logic.assert_called_with("GGO")
    fake_nexo.states["KON DOOR"] = 102
    await hass.services.async_call("cover", "toggle", {"entity_id": "cover.nexo_garage"}, blocking=True)
    fake_nexo.trigger_logic.assert_called_with("GGC")  # stop
    await hass.services.async_call("cover", "toggle", {"entity_id": "cover.nexo_garage"}, blocking=True)
    fake_nexo.trigger_logic.assert_called_with("GGC")  # down


async def test_no_step_button_without_travel_time(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    assert hass.states.get("button.nexo_entry_gate_step") is None
