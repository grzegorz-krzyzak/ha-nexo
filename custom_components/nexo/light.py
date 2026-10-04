"""Nexo lighting outputs (on/off) and dimmers (with a level)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import OPT_DIMMERS, OPT_LIGHTS
from .entity import NexoSwitchedEntity
from .nexo_client import NexoClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            *(
                NexoLight(coordinator, "light", name)
                for name in entry.runtime_data.options.get(OPT_LIGHTS, [])
            ),
            *(
                NexoDimmer(coordinator, "dimmer", name)
                for name in entry.runtime_data.options.get(OPT_DIMMERS, [])
            ),
        ]
    )


class NexoLight(NexoSwitchedEntity, LightEntity):
    """A lighting output, switched on and off."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_command(self.coordinator.hub.client.turn_on, self.resource)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_command(self.coordinator.hub.client.turn_off, self.resource)


class NexoDimmer(NexoLight):
    """A dimmer: the state's high byte is the level (0-255), the low byte 01
    means on.

    Switched on without a brightness it gets the central unit's own 'wlacz',
    as from NexoVision - whatever level that means there. Only an explicit
    brightness is written as a level.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def brightness(self) -> int | None:
        state = self.raw_state
        if not state:
            return None
        return state >> 8

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS not in kwargs:
            await super().async_turn_on(**kwargs)
            return
        level = max(1, min(NexoClient.MAX_LEVEL, int(kwargs[ATTR_BRIGHTNESS])))
        await self._async_command(self.coordinator.hub.client.set_level, self.resource, level)
