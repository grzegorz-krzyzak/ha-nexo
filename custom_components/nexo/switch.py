"""Nexo outputs switched from Home Assistant (a ventilation unit, an air purifier)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import OPT_SWITCHES
from .entity import NexoSwitchedEntity
from .nexo_client import ImportTypes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    names = entry.runtime_data.options.get(OPT_SWITCHES, [])
    # A switch is set on the screen of its resource type: lighting outputs on
    # Lighting, outputs on Outputs - and is grouped the same way
    lighting = set(await coordinator.hub.async_resources(ImportTypes.LIGHT)) if names else set()
    async_add_entities(
        NexoSwitch(coordinator, "switch", name, "lights" if name in lighting else "outputs")
        for name in names
    )


class NexoSwitch(NexoSwitchedEntity, SwitchEntity):
    """An output or lighting output, switched on and off."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_command(self.coordinator.hub.client.turn_on, self.resource)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_command(self.coordinator.hub.client.turn_off, self.resource)
