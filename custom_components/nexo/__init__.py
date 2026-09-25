"""The Nexwell Nexo integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import (
    DEFAULT_PORT,
    ITEM_ID,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_BUTTONS,
    OPT_COVERS,
    OPT_THERMOMETERS,
)
from .coordinator import NexoCoordinator
from .hub import NexoHub
from .nexo_client import NexoAuthError, NexoError

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.COVER, Platform.SENSOR]


@dataclass
class NexoData:
    hub: NexoHub
    coordinator: NexoCoordinator


NexoConfigEntry = ConfigEntry[NexoData]


async def async_setup_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    hub = NexoHub(
        hass,
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data[CONF_PASSWORD],
    )
    try:
        await hub.async_connect()
    except NexoAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except NexoError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = NexoCoordinator(hass, entry, hub)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await hub.async_disconnect()
        raise

    entry.runtime_data = NexoData(hub, coordinator)
    _remove_deselected_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.hub.async_disconnect()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: NexoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_deselected_entities(hass: HomeAssistant, entry: NexoConfigEntry) -> None:
    """Drop registry entries for resources no longer imported.

    Without this, deselecting a resource in the options would leave its entity
    behind as permanently unavailable.
    """
    options = entry.options
    keys = {
        *(f"binary_sensor_{name}" for name in options.get(OPT_BINARY_SENSORS, [])),
        *(f"thermometer_{name}" for name in options.get(OPT_THERMOMETERS, [])),
        *(f"analog_{name}" for name in options.get(OPT_ANALOG_SENSORS, [])),
        *(f"cover_{item[ITEM_ID]}" for item in options.get(OPT_COVERS, [])),
        *(f"button_{item[ITEM_ID]}" for item in options.get(OPT_BUTTONS, [])),
    }
    expected = {f"{entry.entry_id}_{key}" for key in keys}

    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in expected:
            registry.async_remove(entity.entity_id)
