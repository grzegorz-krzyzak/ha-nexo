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
    "valves": [
        {
            "id": "lawn",
            "name": "Lawn",
            "open_command": "PST",
            "close_command": "PPR",
            "sections": ["S1", "S2"],
            "main_valve": "ZG",
            "auto_close": 30,
        },
        {"id": "pump", "name": "Pump", "open_command": "PO", "close_command": "PC"},
    ],
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
    # Background work too: the name check after setup lists every type
    await hass.async_block_till_done(wait_background_tasks=True)
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


async def test_step_button_icon_follows_reed_switch(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    entity_id = "button.nexo_garage_step"
    assert hass.states.get(entity_id).attributes["icon"] == "mdi:garage"
    for reed, icon in ((102, "mdi:garage-open"), (0, "mdi:garage-alert"), (101, "mdi:garage")):
        fake_nexo.states["KON DOOR"] = reed
        await _tick(hass, freezer, 11)
        assert hass.states.get(entity_id).attributes["icon"] == icon


async def test_step_button_icon_without_reed_switch(hass: HomeAssistant, fake_nexo) -> None:
    covers = [dict(OPTIONS["covers"][1])]
    del covers[0]["reed_sensor"]
    await _setup(hass, {**OPTIONS, "covers": covers})
    assert "icon" not in hass.states.get("button.nexo_garage_step").attributes


async def test_keepalive_pings_only_a_quiet_connection(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    pings: list[None] = []

    def ping() -> bool:
        pings.append(None)
        return True

    fake_nexo.ping = ping
    await _tick(hass, freezer, 1)
    await _tick(hass, freezer, 1)
    assert pings == []  # the setup sweep was traffic enough
    await _tick(hass, freezer, 1)
    await _tick(hass, freezer, 1)
    assert len(pings) == 1
    # From then on no silence reaches the card's 5 s limit
    for _ in range(20):
        await _tick(hass, freezer, 1)
    assert 4 <= len(pings) <= 8


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_valve_state_follows_sections(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    assert hass.states.get("valve.nexo_lawn").state == "closed"
    assert hass.states.get("valve.nexo_pump").state == "unknown"

    fake_nexo.states.update(ZG=1, S1=1)
    await _tick(hass, freezer, 11)
    assert hass.states.get("valve.nexo_lawn").state == "open"
    fake_nexo.states.update(S1=0)  # pause between sections
    await _tick(hass, freezer, 11)
    assert hass.states.get("valve.nexo_lawn").state == "open"
    fake_nexo.states.update(ZG=0)
    await _tick(hass, freezer, 11)
    assert hass.states.get("valve.nexo_lawn").state == "closed"


async def test_valve_commands(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    for service, command in (("open_valve", "PO"), ("close_valve", "PC")):
        await hass.services.async_call("valve", service, {"entity_id": "valve.nexo_pump"}, blocking=True)
        fake_nexo.trigger_logic.assert_called_with(command)


async def test_valve_auto_close(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    await hass.services.async_call("valve", "open_valve", {"entity_id": "valve.nexo_lawn"}, blocking=True)
    fake_nexo.trigger_logic.assert_called_with("PST")
    fake_nexo.states.update(ZG=1, S1=1)
    await _tick(hass, freezer, 29 * 60)
    fake_nexo.trigger_logic.assert_called_with("PST")  # not yet
    await _tick(hass, freezer, 2 * 60)
    fake_nexo.trigger_logic.assert_called_with("PPR")


async def test_valve_auto_close_cancelled_when_it_ends(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    fake_nexo.states.update(ZG=1, S1=1)  # started elsewhere
    await _tick(hass, freezer, 11)
    fake_nexo.states.update(ZG=0, S1=0)  # the central unit ended it
    await _tick(hass, freezer, 11)
    await _tick(hass, freezer, 31 * 60)
    fake_nexo.trigger_logic.assert_not_called()


def _count_reads(fake_nexo) -> list[str]:
    reads: list[str] = []
    real = fake_nexo.get_state

    def get_state(name: str) -> int:
        reads.append(name)
        return real(name)

    fake_nexo.get_state = get_state
    return reads


async def test_groups_are_polled_at_their_intervals(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    reads = _count_reads(fake_nexo)
    for _ in range(4):
        await _tick(hass, freezer, 1)
    assert reads == []  # all read at setup, nothing due for 5 s
    await _tick(hass, freezer, 1)
    assert set(reads) == {"KON DOOR", "KON GATE", "PIR HALL"}  # inputs
    reads.clear()
    for _ in range(5):
        await _tick(hass, freezer, 1)
    assert {"S1", "S2", "ZG"} <= set(reads)  # outputs at 10 s
    assert "TMP HALL" not in reads
    for _ in range(50):
        await _tick(hass, freezer, 1)
    assert {"TMP HALL", "TMP OUTSIDE", "HUMIDITY"} <= set(reads)  # measurements at 60 s
    assert reads.count("TMP HALL") == 1


async def test_a_tick_reads_the_most_urgent_first(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    reads = _count_reads(fake_nexo)
    await hass.services.async_call("valve", "open_valve", {"entity_id": "valve.nexo_lawn"}, blocking=True)
    for _ in range(59):
        await _tick(hass, freezer, 1)
    reads.clear()
    await _tick(hass, freezer, 1)  # 60 s: inputs, outputs and measurements all due
    first = {name: reads.index(name) for name in reads}
    assert first["KON DOOR"] < first["S2"] < first["TMP HALL"]  # inputs, outputs, measurements
    # alphabetically HUMIDITY would come first; it is a measurement, so it comes last
    assert first["HUMIDITY"] > first["S2"]


async def test_boosted_resources_are_read_before_the_groups(
    hass: HomeAssistant, fake_nexo, freezer
) -> None:
    await _setup(hass)
    reads = _count_reads(fake_nexo)
    for _ in range(4):
        await _tick(hass, freezer, 1)
    await hass.services.async_call("valve", "open_valve", {"entity_id": "valve.nexo_lawn"}, blocking=True)
    reads.clear()
    await _tick(hass, freezer, 1)  # 5 s: inputs due, the valve's sections boosted
    assert reads.index("S1") < reads.index("KON DOOR")


async def test_intervals_from_options(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, {**OPTIONS, "interval_inputs": 2, "interval_measurements": 300})
    reads = _count_reads(fake_nexo)
    await _tick(hass, freezer, 1)
    await _tick(hass, freezer, 1)
    assert set(reads) == {"KON DOOR", "KON GATE", "PIR HALL"}


async def test_gate_command_boosts_its_reed_switch(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    reads = _count_reads(fake_nexo)
    await hass.services.async_call("cover", "open_cover", {"entity_id": "cover.nexo_garage"}, blocking=True)
    reads.clear()
    for _ in range(3):
        await _tick(hass, freezer, 1)
    assert reads.count("KON DOOR") == 3  # every tick, not every 5 s
    assert "KON GATE" not in reads  # the other gate keeps its interval
    for _ in range(30):  # past the 27 s travel time
        await _tick(hass, freezer, 1)
    reads.clear()
    for _ in range(4):
        await _tick(hass, freezer, 1)
    assert reads.count("KON DOOR") <= 1  # back to the inputs' interval


async def test_valve_command_boosts_its_sections(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass)
    reads = _count_reads(fake_nexo)
    await hass.services.async_call("valve", "open_valve", {"entity_id": "valve.nexo_lawn"}, blocking=True)
    reads.clear()
    for _ in range(3):
        await _tick(hass, freezer, 1)
    assert reads.count("S1") == 3
    assert reads.count("ZG") == 3


async def test_a_tick_without_reads_keeps_the_failure(hass: HomeAssistant, fake_nexo, freezer) -> None:
    from custom_components.nexo.nexo_client import NexoConnectionError

    await _setup(hass)
    entity_id = "binary_sensor.nexo_connection_to_central_unit"

    def down(name):
        raise NexoConnectionError("gone")

    fake_nexo.get_state = down
    for _ in range(5):
        await _tick(hass, freezer, 1)
    assert hass.states.get(entity_id).state == "off"
    await _tick(hass, freezer, 1)  # nothing due: proves nothing
    assert hass.states.get(entity_id).state == "off"


async def test_analog_input_raw_by_default(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass)
    state = hass.states.get("sensor.nexo_humidity")
    assert state.state == "55"
    assert "unit_of_measurement" not in state.attributes
    assert "device_class" not in state.attributes


async def test_analog_input_kind_and_offset(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, {**OPTIONS, "analog_settings": {"HUMIDITY": {"kind": "humidity", "offset": -3}}})
    state = hass.states.get("sensor.nexo_humidity")
    assert state.state == "52"
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["device_class"] == "humidity"
    fake_nexo.states["HUMIDITY"] = 2
    await _tick(hass, freezer, 61)
    assert hass.states.get("sensor.nexo_humidity").state == "0"  # clamped, not -1


async def test_analog_percent_clamps_high(hass: HomeAssistant, fake_nexo) -> None:
    fake_nexo.states["HUMIDITY"] = 95
    await _setup(hass, {**OPTIONS, "analog_settings": {"HUMIDITY": {"kind": "percent", "offset": 10}}})
    state = hass.states.get("sensor.nexo_humidity")
    assert state.state == "100"
    assert "device_class" not in state.attributes


async def test_output_read_as_sensor(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, {**OPTIONS, "output_sensors": ["S7"]})
    entity_id = "binary_sensor.nexo_s7"
    assert hass.states.get(entity_id).state == "off"
    fake_nexo.states["S7"] = 1
    await _tick(hass, freezer, 5)
    assert hass.states.get(entity_id).state == "off"  # outputs: every 10 s
    await _tick(hass, freezer, 5)
    assert hass.states.get(entity_id).state == "on"


async def test_deselected_output_sensor_is_removed(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, {**OPTIONS, "output_sensors": ["S7"]})
    assert hass.states.get("binary_sensor.nexo_s7")
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "output_sensors": []})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get("binary_sensor.nexo_s7") is None

