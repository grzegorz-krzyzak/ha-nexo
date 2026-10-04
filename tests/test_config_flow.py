"""Config and options flow."""

from __future__ import annotations

import pytest

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.nexo.config_flow import PIN_MASK
from custom_components.nexo.const import DOMAIN
from custom_components.nexo.nexo_client import NexoAuthError

from .test_init import OPTIONS, _setup

USER_INPUT = {"host": "192.0.2.10", "port": 1024, "password": "pw"}


async def test_user_flow(hass: HomeAssistant, fake_nexo) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT


async def test_user_flow_bad_password(hass: HomeAssistant, fake_nexo) -> None:
    from custom_components.nexo import config_flow

    config_flow.NexoClient.side_effect = NexoAuthError("LOGIN FAILED")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def _pick(hass: HomeAssistant, flow_id: str, step: str):
    return await hass.config_entries.options.async_configure(flow_id, {"next_step_id": step})


async def _submit(hass: HomeAssistant, flow_id: str, data: dict):
    return await hass.config_entries.options.async_configure(flow_id, data)


GATE = {
    "name": "Gate",
    "device_class": "gate",
    "open_command": "GO",
    "close_command": "GC",
    "reed_sensor": "KON GATE",
    "open_only_when_closed": True,
}


async def test_user_flow_title(hass: HomeAssistant, fake_nexo) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["title"] == "Nexo · 192.0.2.10:1024"


async def test_menu_summary(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "connection", "sensors", "analog", "thermometers", "lights", "dimmers", "outputs",
        "analog_outputs", "thermostats", "logic", "weather", "settings", "save",
    ]
    placeholders = result["description_placeholders"]
    assert placeholders["address"] == "192.0.2.10:1024"
    assert result["step_id"] == "menu"
    assert placeholders["alert_open"] == '<ha-alert alert-type="success">'
    assert placeholders["binary_sensors"] == "2"
    assert placeholders["logic_items"] == "6"  # 3 gates and doors, 1 button, 2 programs
    result = await _pick(hass, result["flow_id"], "logic")
    assert result["menu_options"] == ["covers", "buttons", "valves", "back"]
    assert result["description_placeholders"]["covers"] == "Entry gate, Garage, Shed"


async def test_add_edit_and_delete_cover(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]

    await _pick(hass, flow_id, "logic")
    result = await _pick(hass, flow_id, "covers")
    assert result["menu_options"] == ["add_cover", "logic"]
    result = await _pick(hass, flow_id, "add_cover")
    result = await _submit(hass, flow_id, {**GATE, "reed_sensor": "TYPO"})
    assert result["errors"] == {"reed_sensor": "unknown_resource"}
    result = await _submit(hass, flow_id, {**GATE, "open_command": "TOOLONG1"})
    assert result["errors"] == {"open_command": "command_too_long"}
    no_reed = {k: v for k, v in GATE.items() if k != "reed_sensor"}
    result = await _submit(hass, flow_id, no_reed)
    assert result["errors"] == {"open_only_when_closed": "guard_needs_reed_sensor"}
    result = await _submit(hass, flow_id, {**GATE, "travel_time": 27})
    assert result["errors"] == {"travel_time": "step_needs_unguarded"}
    result = await _submit(hass, flow_id, GATE)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["cover_0", "add_cover", "logic"]
    assert result["description_placeholders"]["cover_0_info"] == "GO / GC · KON GATE"

    # Edit: the form comes prefilled, the change keeps the entity's id
    result = await _pick(hass, flow_id, "cover_0")
    assert result["step_id"] == "cover_0"
    result = await _submit(hass, flow_id, {**GATE, "close_command": "GZ"})
    assert result["description_placeholders"]["cover_0_info"] == "GO / GZ · KON GATE"

    result = await _pick(hass, flow_id, "logic")
    result = await _pick(hass, flow_id, "back")
    assert result["step_id"] == "menu"
    assert entry.options == {}  # nothing stored before saving
    result = await _pick(hass, flow_id, "save")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("cover.nexo_gate").state == "closed"
    cover_id = entry.options["covers"][0]["id"]
    assert entry.options["covers"][0]["close_command"] == "GZ"

    # Delete
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "covers")
    await _pick(hass, flow_id, "cover_0")
    result = await _submit(hass, flow_id, {**GATE, "delete": True})
    assert result["menu_options"] == ["add_cover", "logic"]
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "back")
    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert entry.options["covers"] == []
    assert hass.states.get("cover.nexo_gate") is None
    assert cover_id


async def test_buttons_and_sensors(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "buttons")
    await _pick(hass, flow_id, "add_button")
    result = await _submit(hass, flow_id, {"name": "Wicket", "command": "WK"})
    assert result["description_placeholders"] == {"button_0": "Wicket", "button_0_info": "WK"}
    result = await _pick(hass, flow_id, "logic")
    assert result["description_placeholders"]["buttons"] == "Wicket"
    await _pick(hass, flow_id, "back")
    await _pick(hass, flow_id, "sensors")
    result = await _submit(hass, flow_id, {"binary_sensors": ["PIR HALL"]})
    assert result["description_placeholders"]["binary_sensors"] == "1"
    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert hass.states.get("button.nexo_wicket")
    assert hass.states.get("binary_sensor.nexo_pir_hall").state == "on"


async def test_options_closed_without_saving(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "buttons")
    await _pick(hass, flow_id, "add_button")
    await _submit(hass, flow_id, {"name": "Wicket", "command": "WK"})
    hass.config_entries.options.async_abort(flow_id)
    assert entry.options == {}


async def test_connection_from_options(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    registry = er.async_get(hass)
    before = registry.async_get("sensor.nexo_tmp_hall").unique_id

    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "connection")
    result = await _submit(hass, flow_id, {"host": "192.0.2.20", "port": 1024})
    assert result["description_placeholders"]["address"] == "192.0.2.20:1024"
    assert result["step_id"] == "menu_unsaved"
    assert entry.data["host"] == "192.0.2.10"  # not before saving

    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert entry.data == {"host": "192.0.2.20", "port": 1024, "password": "pw"}
    assert entry.unique_id == "192.0.2.20"
    assert entry.title == "Nexo · 192.0.2.20:1024"
    assert registry.async_get("sensor.nexo_tmp_hall").unique_id == before
    assert hass.states.get("sensor.nexo_tmp_hall").state == "23.3"


async def test_renamed_title_is_kept(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    hass.config_entries.async_update_entry(entry, title="Dom")
    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.20", "port": 1024}
    )
    await hass.async_block_till_done()
    assert entry.title == "Dom"


async def test_reconfigure_keeps_entities(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    registry = er.async_get(hass)
    before = registry.async_get("sensor.nexo_tmp_hall").unique_id

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    # Empty password keeps the stored one
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.20", "port": 1024}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert entry.data == {"host": "192.0.2.20", "port": 1024, "password": "pw"}
    assert entry.unique_id == "192.0.2.20"
    assert entry.title == "Nexo · 192.0.2.20:1024"
    assert registry.async_get("sensor.nexo_tmp_hall").unique_id == before
    assert hass.states.get("sensor.nexo_tmp_hall").state == "23.3"


async def test_empty_menu_lists(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["description_placeholders"]["logic_items"] == "0"
    result = await _pick(hass, result["flow_id"], "logic")
    assert result["description_placeholders"]["covers"] == "—"
    assert result["description_placeholders"]["buttons"] == "—"


async def test_forms_lead_back(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]

    # Connection submitted unchanged: back to the menu, no connection test
    await _pick(hass, flow_id, "connection")
    with patch("custom_components.nexo.config_flow._validate") as validate:
        result = await _submit(
            hass, flow_id, {"host": "192.0.2.10", "port": 1024, "password": PIN_MASK}
        )
    validate.assert_not_called()
    assert result["step_id"] == "menu"
    assert result["step_id"] == "menu"

    # An empty add form: back to the submenu, nothing added
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "covers")
    await _pick(hass, flow_id, "add_cover")
    result = await _submit(hass, flow_id, {"device_class": "gate", "open_only_when_closed": False})
    assert result["step_id"] == "covers"
    assert result["menu_options"] == ["add_cover", "logic"]

    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "buttons")
    await _pick(hass, flow_id, "add_button")
    result = await _submit(hass, flow_id, {})
    assert result["step_id"] == "buttons"

    # Partly filled: the missing fields are reported, not taken as "back"
    await _pick(hass, flow_id, "add_button")
    result = await _submit(hass, flow_id, {"command": "WK"})
    assert result["errors"] == {"name": "required"}


async def test_pin_is_masked_not_sent(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await _pick(hass, flow_id, "connection")
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested["password"] == PIN_MASK
    assert "pw" not in suggested.values()

    # A new PIN replaces the stored one
    await _submit(hass, flow_id, {"host": "192.0.2.10", "port": 1024, "password": "new"})
    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert entry.data["password"] == "new"


async def test_add_and_edit_valve(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "valves")
    await _pick(hass, flow_id, "add_valve")
    lawn = {
        "name": "Lawn", "open_command": "PST", "close_command": "PPR",
        "sections": ["S1", "S2"], "main_valve": "S1",
    }
    result = await _submit(hass, flow_id, lawn)
    assert result["errors"] == {"main_valve": "main_valve_is_a_section"}
    result = await _submit(hass, flow_id, {**lawn, "main_valve": "NO SUCH"})
    assert result["errors"] == {"main_valve": "unknown_resource"}
    result = await _submit(hass, flow_id, {**lawn, "main_valve": "ZG"})
    assert result["menu_options"] == ["valve_0", "add_valve", "logic"]
    assert result["description_placeholders"] == {
        "valve_0": "Lawn", "valve_0_info": "PST / PPR · S1, S2"
    }
    result = await _pick(hass, flow_id, "valve_0")
    assert result["step_id"] == "valve_0"
    await _submit(hass, flow_id, {**lawn, "main_valve": "ZG", "auto_close": 20})
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "back")
    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert entry.options["valves"][0]["auto_close"] == 20
    assert hass.states.get("valve.nexo_lawn").state == "closed"


def test_sections_summary() -> None:
    from custom_components.nexo.config_flow import _sections_summary

    six = [f"NAWODNIENIE S{i}" for i in range(1, 7)]
    assert _sections_summary(six) == "NAWODNIENIE S1 … S6"
    assert _sections_summary(["NAWODNIENIE S7", "NAWODNIENIE S8"]) == "NAWODNIENIE S7, S8"
    assert _sections_summary(["PUMP"]) == "PUMP"
    assert _sections_summary(["A 1", "B 2", "C 3"]) == "A 1 … C 3"


async def test_menu_variant_when_not_answering(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    entry.runtime_data.coordinator.last_update_success = False
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "menu_offline"
    assert result["description_placeholders"]["alert_open"] == '<ha-alert alert-type="warning">'
    result = await _pick(hass, result["flow_id"], "settings")
    assert result["step_id"] == "settings"


async def test_polling_settings(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, {**OPTIONS, "scan_interval": 10})  # as saved by 0.2.x
    result = await hass.config_entries.options.async_init(entry.entry_id)
    placeholders = result["description_placeholders"]
    assert (placeholders["interval_inputs"], placeholders["interval_outputs"],
            placeholders["interval_lights"], placeholders["interval_measurements"]) == (
        "5", "10", "10", "60")
    result = await _pick(hass, result["flow_id"], "settings")
    result = await _submit(hass, result["flow_id"], {
        "interval_inputs": 3, "interval_outputs": 10, "interval_lights": 7,
        "interval_measurements": 300,
    })
    result = await _pick(hass, result["flow_id"], "save")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["interval_inputs"] == 3
    assert entry.options["interval_lights"] == 7
    assert entry.options["interval_measurements"] == 300
    assert "scan_interval" not in entry.options


async def test_analog_input_settings(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _pick(hass, result["flow_id"], "analog")
    assert result["menu_options"] == ["analog_pick", "analog_0", "back"]
    assert result["description_placeholders"]["analog_0"] == "HUMIDITY"
    placeholders = result["description_placeholders"]
    assert (placeholders["analog_0_unit"], placeholders["analog_0_offset"]) == ("—", "+0")
    result = await _pick(hass, result["flow_id"], "analog_0")
    assert result["type"] is FlowResultType.FORM
    result = await _submit(hass, result["flow_id"], {"kind": "moisture", "offset": 2})
    placeholders = result["description_placeholders"]
    assert (placeholders["analog_0_unit"], placeholders["analog_0_offset"]) == ("%", "+2")
    result = await _pick(hass, result["flow_id"], "back")
    result = await _pick(hass, result["flow_id"], "save")
    assert entry.options["analog_settings"] == {"HUMIDITY": {"kind": "moisture", "offset": 2}}


async def test_analog_default_and_deselected_leave_no_settings(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, {**OPTIONS, "analog_settings": {"HUMIDITY": {"kind": "percent", "offset": 0}}})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _pick(hass, result["flow_id"], "analog")
    result = await _pick(hass, result["flow_id"], "analog_0")
    result = await _submit(hass, result["flow_id"], {"kind": "raw", "offset": 0})
    result = await _pick(hass, result["flow_id"], "back")
    result = await _pick(hass, result["flow_id"], "save")
    assert entry.options["analog_settings"] == {}

    await hass.async_block_till_done()  # the save reloads the entry
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _pick(hass, result["flow_id"], "analog")
    result = await _pick(hass, result["flow_id"], "analog_0")
    result = await _submit(hass, result["flow_id"], {"kind": "humidity", "offset": -3})
    # Deselected: its settings go with it
    result = await _pick(hass, result["flow_id"], "analog_pick")
    result = await _submit(hass, result["flow_id"], {"analog_sensors": []})
    assert result["menu_options"] == ["analog_pick", "back"]
    result = await _pick(hass, result["flow_id"], "back")
    result = await _pick(hass, result["flow_id"], "save")
    assert entry.options["analog_settings"] == {}


async def test_outputs_screen(hass: HomeAssistant, fake_nexo) -> None:
    """Outputs: switch, read only, never offered - the program's sections not listed."""
    entry = await _setup(hass)  # the program Lawn uses S1 and S2
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await _pick(hass, flow_id, "outputs")
    assert sorted(_choices(result, "switches")) == ["S7", "VENT"]
    assert sorted(_choices(result, "output_sensors")) == ["S7", "VENT"]
    result = await _submit(hass, flow_id, {
        "switches": ["VENT"], "output_sensors": ["S7"], "excluded": [],
    })
    assert result["description_placeholders"]["output_sensors"] == "1"
    await _pick(hass, flow_id, "save")
    assert entry.options["output_sensors"] == ["S7"]
    assert entry.options["switches"] == ["VENT"]


def _choices(result, key: str) -> list[str]:
    for field in result["data_schema"].schema:
        if str(field) == key:
            return result["data_schema"].schema[field].config["options"]
    raise KeyError(key)


async def test_lighting_dimmers_and_thermometers(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass)  # the program Lawn uses ZG as its main valve
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]

    result = await _pick(hass, flow_id, "lights")
    assert result["step_id"] == "lights"
    assert sorted(_choices(result, "lights")) == ["GATE PULSE", "L1"]
    result = await _submit(hass, flow_id, {
        "lights": ["L1"], "switches": [], "excluded": ["GATE PULSE"],
    })
    assert result["type"] is FlowResultType.MENU

    result = await _pick(hass, flow_id, "dimmers")
    assert _choices(result, "dimmers") == ["DIM A"]
    await _submit(hass, flow_id, {"dimmers": ["DIM A"]})
    result = await _pick(hass, flow_id, "thermometers")
    await _submit(hass, flow_id, {"thermometers": ["TMP HALL"]})

    await _pick(hass, flow_id, "save")
    assert entry.options["lights"] == ["L1"]
    assert entry.options["excluded"] == ["GATE PULSE"]
    assert entry.options["dimmers"] == ["DIM A"]
    assert entry.options["thermometers"] == ["TMP HALL"]


async def test_a_light_as_a_switch(hass: HomeAssistant, fake_nexo) -> None:
    """A fan wired as a light: picked as a switch on the Lighting screen."""
    entry = await _setup(hass, {**OPTIONS, "valves": []})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "lights")
    await _submit(hass, flow_id, {"lights": ["L1"], "switches": ["ZG"], "excluded": []})
    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert entry.options["switches"] == ["ZG"]
    assert hass.states.get("switch.nexo_zg")


@pytest.mark.parametrize(
    ("step", "data", "resource"),
    [
        ("lights", {"lights": ["L1"], "switches": [], "excluded": ["L1"]}, "L1"),
        ("outputs", {"switches": ["VENT"], "output_sensors": ["VENT"], "excluded": []}, "VENT"),
    ],
)
async def test_one_role_per_resource(
    hass: HomeAssistant, fake_nexo, step: str, data: dict, resource: str
) -> None:
    """The one new mechanism of 0.11: a resource in two fields is refused."""
    entry = await _setup(hass, {**OPTIONS, "valves": []})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, step)
    result = await _submit(hass, flow_id, data)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "role_conflict"}
    assert result["description_placeholders"]["resource"] == resource
    await _pick_menu_after_fix(hass, flow_id, step)
    await _pick(hass, flow_id, "save")
    assert resource not in entry.options.get("switches", [])


async def _pick_menu_after_fix(hass: HomeAssistant, flow_id: str, step: str) -> None:
    empty = {"lights": [], "switches": [], "excluded": []} if step == "lights" else {
        "switches": [], "output_sensors": [], "excluded": []
    }
    result = await _submit(hass, flow_id, empty)
    assert result["type"] is FlowResultType.MENU


async def test_screens_edit_only_their_type(hass: HomeAssistant, fake_nexo) -> None:
    """switches and excluded hold lights and outputs: each screen keeps the
    other type's part."""
    entry = await _setup(hass, {
        **OPTIONS, "valves": [], "switches": ["ZG", "VENT"], "excluded": ["GATE PULSE", "S7"],
    })
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await _pick(hass, flow_id, "outputs")
    field = next(f for f in result["data_schema"].schema if str(f) == "switches")
    assert field.default() == ["VENT"]
    await _submit(hass, flow_id, {"switches": [], "output_sensors": [], "excluded": ["S7"]})
    await _pick(hass, flow_id, "save")
    assert entry.options["switches"] == ["ZG"]
    assert entry.options["excluded"] == ["GATE PULSE", "S7"]


async def test_valve_form_hides_excluded_outputs(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, {**OPTIONS, "valves": [], "excluded": ["GATE PULSE"]})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "logic")
    await _pick(hass, flow_id, "valves")
    result = await _pick(hass, flow_id, "add_valve")
    assert "GATE PULSE" not in _choices(result, "sections")
    assert "L1" in _choices(result, "sections")


async def test_select_all_then_untick(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, {**OPTIONS, "valves": []})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "lights")
    # Ticking "select all" saves nothing: the same form comes back, the first
    # field holding everything the other two do not
    result = await _submit(hass, flow_id, {
        "lights": [], "switches": [], "excluded": ["GATE PULSE"], "select_all": True,
    })
    assert result["step_id"] == "lights"
    field = next(f for f in result["data_schema"].schema if str(f) == "lights")
    assert sorted(field.default()) == ["L1", "ZG"]
    result = await _submit(hass, flow_id, {
        "lights": ["L1"], "switches": [], "excluded": ["GATE PULSE"],
    })  # untick ZG
    assert result["type"] is FlowResultType.MENU
    await _pick(hass, flow_id, "save")
    assert entry.options["lights"] == ["L1"]


async def test_read_only_may_also_be_excluded(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, {**OPTIONS, "valves": []})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "outputs")
    result = await _submit(hass, flow_id, {
        "switches": [], "output_sensors": ["S7"], "excluded": ["S7"],
    })
    assert result["type"] is FlowResultType.MENU
    await _pick(hass, flow_id, "save")
    assert entry.options["output_sensors"] == ["S7"] and entry.options["excluded"] == ["S7"]


async def test_never_offered_left_out_of_controlled_fields(hass: HomeAssistant, fake_nexo) -> None:
    """The field says "never offer": the switch and light fields must not list them."""
    entry = await _setup(hass, {**OPTIONS, "valves": [], "excluded": ["VENT", "GATE PULSE"]})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await _pick(hass, flow_id, "outputs")
    assert "VENT" not in _choices(result, "switches")
    assert "VENT" in _choices(result, "excluded")
    assert "VENT" in _choices(result, "output_sensors")
    await _submit(hass, flow_id, {"switches": [], "output_sensors": [], "excluded": ["VENT"]})
    result = await _pick(hass, flow_id, "lights")
    assert "GATE PULSE" not in _choices(result, "lights")
    assert "GATE PULSE" not in _choices(result, "switches")
