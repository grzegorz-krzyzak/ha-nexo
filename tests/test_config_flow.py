"""Config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.nexo.const import DOMAIN
from custom_components.nexo.nexo_client import NexoAuthError

from .test_init import _setup

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
        "connection", "sensors", "covers", "buttons", "settings", "save"
    ]
    placeholders = result["description_placeholders"]
    assert placeholders["address"] == "192.0.2.10:1024"
    assert placeholders["status"] == "✅"
    assert placeholders["binary_sensors"] == "2"
    assert placeholders["covers"] == "Entry gate, Garage, Shed"


async def test_add_edit_and_delete_cover(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]

    result = await _pick(hass, flow_id, "covers")
    assert result["menu_options"] == ["add_cover", "back"]
    result = await _pick(hass, flow_id, "add_cover")
    result = await _submit(hass, flow_id, {**GATE, "open_command": "TOOLONG1"})
    assert result["errors"] == {"open_command": "command_too_long"}
    no_reed = {k: v for k, v in GATE.items() if k != "reed_sensor"}
    result = await _submit(hass, flow_id, no_reed)
    assert result["errors"] == {"open_only_when_closed": "guard_needs_reed_sensor"}
    result = await _submit(hass, flow_id, GATE)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["cover_0", "add_cover", "back"]
    assert result["description_placeholders"]["cover_0_info"] == "GO / GC · KON GATE · 🔒"

    # Edit: the form comes prefilled, the change keeps the entity's id
    result = await _pick(hass, flow_id, "cover_0")
    assert result["step_id"] == "cover_0"
    result = await _submit(hass, flow_id, {**GATE, "close_command": "GZ"})
    assert result["description_placeholders"]["cover_0_info"] == "GO / GZ · KON GATE · 🔒"

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
    await _pick(hass, flow_id, "covers")
    await _pick(hass, flow_id, "cover_0")
    result = await _submit(hass, flow_id, {**GATE, "delete": True})
    assert result["menu_options"] == ["add_cover", "back"]
    await _pick(hass, flow_id, "back")
    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert entry.options["covers"] == []
    assert hass.states.get("cover.nexo_gate") is None
    assert cover_id


async def test_buttons_and_sensors(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    await _pick(hass, flow_id, "buttons")
    await _pick(hass, flow_id, "add_button")
    result = await _submit(hass, flow_id, {"name": "Wicket", "command": "WK"})
    assert result["description_placeholders"] == {"button_0": "Wicket", "button_0_info": "WK"}
    await _pick(hass, flow_id, "back")
    await _pick(hass, flow_id, "sensors")
    result = await _submit(
        hass, flow_id, {"binary_sensors": ["PIR HALL"], "thermometers": [], "analog_sensors": []}
    )
    assert result["description_placeholders"]["buttons"] == "Wicket"
    await _pick(hass, flow_id, "save")
    await hass.async_block_till_done()
    assert hass.states.get("button.nexo_wicket")
    assert hass.states.get("binary_sensor.nexo_pir_hall").state == "on"


async def test_options_closed_without_saving(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
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
    assert result["description_placeholders"]["status"] == "✏️"
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
