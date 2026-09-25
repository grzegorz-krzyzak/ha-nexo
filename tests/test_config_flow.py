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


async def _menu(hass: HomeAssistant, flow_id: str, step: str):
    return await hass.config_entries.options.async_configure(flow_id, {"next_step_id": step})


async def test_options_menu_loop(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    flow_id = result["flow_id"]

    result = await _menu(hass, flow_id, "add_cover")
    too_long = {
        "name": "Gate",
        "device_class": "gate",
        "open_command": "TOOLONG1",
        "close_command": "GC",
        "reed_sensor": "KON GATE",
        "open_only_when_closed": True,
    }
    result = await hass.config_entries.options.async_configure(flow_id, too_long)
    assert result["errors"] == {"open_command": "command_too_long"}
    no_reed = {k: v for k, v in too_long.items() if k != "reed_sensor"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {**no_reed, "open_command": "GO"}
    )
    assert result["errors"] == {"open_only_when_closed": "guard_needs_reed_sensor"}
    result = await hass.config_entries.options.async_configure(
        flow_id, {**too_long, "open_command": "GO"}
    )
    # Back at the menu, nothing stored yet
    assert result["type"] is FlowResultType.MENU
    assert result["description_placeholders"]["covers"] == "Gate"
    assert entry.options == {}

    result = await _menu(hass, flow_id, "resources")
    result = await hass.config_entries.options.async_configure(
        flow_id, {"binary_sensors": ["PIR HALL"], "thermometers": [], "analog_sensors": []}
    )
    assert result["type"] is FlowResultType.MENU

    result = await _menu(hass, flow_id, "save")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("cover.nexo_gate").state == "closed"
    assert hass.states.get("binary_sensor.nexo_pir_hall").state == "on"


async def test_options_closed_without_saving(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _menu(hass, result["flow_id"], "add_button")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Wicket", "command": "WK"}
    )
    hass.config_entries.options.async_abort(result["flow_id"])
    assert entry.options == {}


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
    assert registry.async_get("sensor.nexo_tmp_hall").unique_id == before
    assert hass.states.get("sensor.nexo_tmp_hall").state == "23.3"
