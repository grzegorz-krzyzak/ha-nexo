"""Config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

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


async def test_options_add_cover_and_resources(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, options={})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_cover"}
    )
    too_long = {
        "name": "Gate",
        "device_class": "gate",
        "open_command": "TOOLONG1",
        "close_command": "GC",
        "reed_sensor": "KON GATE",
        "open_only_when_closed": True,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], too_long)
    assert result["errors"] == {"open_command": "command_too_long"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**too_long, "open_command": "GO"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("cover.nexo_192_0_2_10_gate").state == "closed"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "resources"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"binary_sensors": ["PIR HALL"], "thermometers": [], "analog_sensors": []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.nexo_192_0_2_10_pir_hall").state == "on"
    assert len(entry.options["covers"]) == 1
