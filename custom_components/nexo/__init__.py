"""The Nexwell Nexo integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    DEFAULT_PORT,
    DOMAIN,
    COVER_TRAVEL_TIME,
    ITEM_ID,
    MANUFACTURER,
    entry_title,
    is_default_title,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_BUTTONS,
    OPT_COVERS,
    OPT_THERMOMETERS,
    OPT_VALVES,
)
from .coordinator import NexoCoordinator
from .motion import Motion
from .hub import NexoHub
from .nexo_client import NexoAuthError, NexoError

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.SENSOR,
    Platform.VALVE,
]


@dataclass
class NexoData:
    hub: NexoHub
    coordinator: NexoCoordinator
    # Per gate with a travel time: shared by its cover and its Step button
    motions: dict[str, Motion]


NexoConfigEntry = ConfigEntry[NexoData]


async def async_setup_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    if is_default_title(entry.title, host, port) and entry.title != entry_title(host, port):
        hass.config_entries.async_update_entry(entry, title=entry_title(host, port))

    hub = NexoHub(hass, host, port, entry.data[CONF_PASSWORD])
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

    motions = {
        item[ITEM_ID]: Motion(travel_time=item[COVER_TRAVEL_TIME])
        for item in entry.options.get(OPT_COVERS, [])
        if item.get(COVER_TRAVEL_TIME)
    }
    entry.runtime_data = NexoData(hub, coordinator, motions)
    await _async_register_device(hass, entry, hub)
    _remove_deselected_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.hub.async_disconnect()
    return unloaded


async def _async_register_device(
    hass: HomeAssistant, entry: NexoConfigEntry, hub: NexoHub
) -> None:
    """Create the central unit's device with its firmware version.

    The version comes from the 'system' command, e.g. 'Nexo 5.53 R1PLX1H2.
    Czas dzialania: ...'. Failing to read it only leaves the field empty.
    """
    try:
        info = await hub.async_call(hub.client.system_info)
    except NexoError:
        info = ""
    sw_version = _firmware_version(info)

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model="Nexo",
        name="Nexo",
        sw_version=sw_version,
    )


def _firmware_version(info: str) -> str | None:
    """'Nexo 5.53 R1PLX1H2. Czas dzialania: ...' -> '5.53 R1PLX1H2'."""
    first = info.split(". ", 1)[0].rstrip(".")
    if not first.startswith("Nexo "):
        return None
    return first.removeprefix("Nexo ").strip() or None


def _remove_deselected_entities(hass: HomeAssistant, entry: NexoConfigEntry) -> None:
    """Drop registry entries for resources no longer imported.

    Without this, deselecting a resource in the options would leave its entity
    behind as permanently unavailable.
    """
    options = entry.options
    keys = {
        "connection",
        *(f"binary_sensor_{name}" for name in options.get(OPT_BINARY_SENSORS, [])),
        *(f"thermometer_{name}" for name in options.get(OPT_THERMOMETERS, [])),
        *(f"analog_{name}" for name in options.get(OPT_ANALOG_SENSORS, [])),
        *(f"cover_{item[ITEM_ID]}" for item in options.get(OPT_COVERS, [])),
        *(
            f"step_{item[ITEM_ID]}"
            for item in options.get(OPT_COVERS, [])
            if item.get(COVER_TRAVEL_TIME)
        ),
        *(f"button_{item[ITEM_ID]}" for item in options.get(OPT_BUTTONS, [])),
        *(f"valve_{item[ITEM_ID]}" for item in options.get(OPT_VALVES, [])),
    }
    expected = {f"{entry.entry_id}_{key}" for key in keys}

    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in expected:
            registry.async_remove(entity.entity_id)
