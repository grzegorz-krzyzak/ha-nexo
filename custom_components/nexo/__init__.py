"""The Nexwell Nexo integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from collections.abc import Mapping
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DEFAULT_PORT,
    DOMAIN,
    KEEPALIVE_CHECK,
    COVER_TRAVEL_TIME,
    ITEM_ID,
    MANUFACTURER,
    entry_title,
    is_default_title,
    OPT_ANALOG_OUTPUTS,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_BUTTONS,
    OPT_COVERS,
    OPT_DIMMERS,
    OPT_LIGHTS,
    OPT_OUTPUT_SENSORS,
    OPT_SWITCHES,
    OPT_THERMOMETERS,
    OPT_THERMOSTATS,
    OPT_VALVES,
    OPT_WEATHER,
    THERMOSTAT_NAME,
)
from . import weather
from .coordinator import NexoCoordinator
from .motion import Motion
from .hub import NexoHub
from .roles import resolve_roles
from .nexo_client import NexoAuthError, NexoError

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]


@dataclass
class NexoData:
    hub: NexoHub
    coordinator: NexoCoordinator
    # Per gate with a travel time: shared by its cover and its Step button
    motions: dict[str, Motion]
    # The options with every resource in one role (roles.py) - what the
    # platforms build their entities from
    options: dict[str, Any]


NexoConfigEntry = ConfigEntry[NexoData]

_LOGGER = logging.getLogger(__name__)


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

    options, conflicts = resolve_roles(entry.options)
    _report_role_conflicts(hass, entry, conflicts)
    coordinator = NexoCoordinator(hass, entry, hub, options)
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
    entry.runtime_data = NexoData(hub, coordinator, motions, options)
    await _async_register_device(hass, entry, hub)
    _remove_deselected_entities(hass, entry, options)

    async def _async_keepalive(now: datetime) -> None:
        await hub.async_keepalive()

    entry.async_on_unload(
        async_track_time_interval(
            hass, _async_keepalive, timedelta(seconds=KEEPALIVE_CHECK),
            cancel_on_shutdown=True,
        )
    )

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


def _remove_deselected_entities(
    hass: HomeAssistant, entry: NexoConfigEntry, options: Mapping[str, Any]
) -> None:
    """Drop registry entries for resources no longer imported.

    Without this, deselecting a resource in the options would leave its entity
    behind as permanently unavailable.
    """
    keys = {
        "connection",
        *(f"binary_sensor_{name}" for name in options.get(OPT_BINARY_SENSORS, [])),
        *(f"thermometer_{name}" for name in options.get(OPT_THERMOMETERS, [])),
        *(f"analog_{name}" for name in options.get(OPT_ANALOG_SENSORS, [])),
        *(f"output_{name}" for name in options.get(OPT_OUTPUT_SENSORS, [])),
        *(f"light_{name}" for name in options.get(OPT_LIGHTS, [])),
        *(f"dimmer_{name}" for name in options.get(OPT_DIMMERS, [])),
        *(f"switch_{name}" for name in options.get(OPT_SWITCHES, [])),
        *(f"thermostat_{item[THERMOSTAT_NAME]}" for item in options.get(OPT_THERMOSTATS, [])),
        *(f"cover_{item[ITEM_ID]}" for item in options.get(OPT_COVERS, [])),
        *(
            f"step_{item[ITEM_ID]}"
            for item in options.get(OPT_COVERS, [])
            if item.get(COVER_TRAVEL_TIME)
        ),
        *(f"button_{item[ITEM_ID]}" for item in options.get(OPT_BUTTONS, [])),
        *(f"valve_{item[ITEM_ID]}" for item in options.get(OPT_VALVES, [])),
        *(f"analog_output_{name}" for name in options.get(OPT_ANALOG_OUTPUTS, [])),
        # The weather station: its readings and conditions, by role
        *(
            f"weather_{key}"
            for key in (*weather.SENSOR_KEYS, *weather.AURA_BITS)
            if options.get(OPT_WEATHER)
        ),
    }
    expected = {f"{entry.entry_id}_{key}" for key in keys}

    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in expected:
            registry.async_remove(entity.entity_id)


def _report_role_conflicts(
    hass: HomeAssistant, entry: NexoConfigEntry, conflicts: dict[str, tuple[str, list[str]]]
) -> None:
    """A warning and a repair issue while the stored options give a resource
    two roles; the issue goes once they are fixed."""
    issue_id = f"role_conflict_{entry.entry_id}"
    if not conflicts:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    for name, (kept, dropped) in conflicts.items():
        _LOGGER.warning(
            "%s is set as %s and as %s in the options; using %s only",
            name, kept, ", ".join(dropped), kept,
        )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="role_conflict",
        translation_placeholders={
            "resources": ", ".join(
                f"{name} ({kept})" for name, (kept, _) in sorted(conflicts.items())
            )
        },
    )
