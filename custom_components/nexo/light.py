"""Nexo lighting outputs (on/off) and dimmers (with a level)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import OPT_DIMMERS, OPT_LIGHTS
from .coordinator import NexoCoordinator
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
            *(NexoLight(coordinator, "light", name) for name in entry.options.get(OPT_LIGHTS, [])),
            *(
                NexoDimmer(coordinator, "dimmer", name)
                for name in entry.options.get(OPT_DIMMERS, [])
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

    Switched on without a brightness, it comes back at the last level it was
    seen at, as it would from the wall switch - the central unit's own
    'wlacz' always means full level. Full level only when no level has been
    seen yet.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: NexoCoordinator, kind: str, resource: str) -> None:
        super().__init__(coordinator, kind, resource)
        self._last_level: int | None = None
        self._remember_level()

    @property
    def brightness(self) -> int | None:
        state = self.raw_state
        if not state:
            return None
        return state >> 8

    def _remember_level(self) -> None:
        level = self.brightness
        if level:
            self._last_level = level

    @callback
    def _handle_coordinator_update(self) -> None:
        self._remember_level()
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        level = kwargs.get(ATTR_BRIGHTNESS, self._last_level or NexoClient.MAX_LEVEL)
        level = max(1, min(NexoClient.MAX_LEVEL, int(level)))
        await self._async_command(self.coordinator.hub.client.set_level, self.resource, level)
