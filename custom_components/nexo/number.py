"""Nexo analogue (0-10 V) outputs, as a level from 0 to 100 %."""

from __future__ import annotations

import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import OPT_ANALOG_OUTPUTS
from .entity import NexoSwitchedEntity
from .nexo_client import NexoClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        NexoAnalogOutput(coordinator, "analog_output", name)
        for name in entry.options.get(OPT_ANALOG_OUTPUTS, [])
    )


def level_percent(state: int) -> int:
    """The level as the central unit words it: 0x8001 'jest wlaczone (50%)',
    0xbf01 '(74%)' - rounded down."""
    return (state >> 8) * 100 // NexoClient.MAX_LEVEL if state & 1 else 0


def percent_level(percent: float) -> int:
    """The level for a percentage, rounded up, so that it reads back as the
    same percentage (75 % -> 192 -> 75 %, where 191 would read 74 %)."""
    return min(NexoClient.MAX_LEVEL, math.ceil(percent * NexoClient.MAX_LEVEL / 100))


class NexoAnalogOutput(NexoSwitchedEntity, NumberEntity):
    """The level of the output's signal - not the state of what it drives:
    a light dimmed through 0-10 V stays dark until its power output is on,
    and may glow at level 0. Compose the two in Home Assistant if wanted.
    """

    _group = "analog_outputs"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int | None:
        state = self.raw_state
        return None if state is None else level_percent(state)

    async def async_set_native_value(self, value: float) -> None:
        await self._async_command(
            self.coordinator.hub.client.set_analog_level, self.resource, percent_level(value)
        )
