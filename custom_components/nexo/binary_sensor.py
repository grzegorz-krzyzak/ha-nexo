"""Nexo SENSOR resources: reed switches, motion detectors and other inputs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import OPT_BINARY_SENSORS, SENSOR_INTACT, SENSOR_VIOLATED
from .entity import NexoResourceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        NexoBinarySensor(coordinator, "binary_sensor", name)
        for name in entry.options.get(OPT_BINARY_SENSORS, [])
    )


class NexoBinarySensor(NexoResourceEntity, BinarySensorEntity):
    """On when the input is violated (102): a door open, motion detected.

    No device class is set - the central unit does not say what a sensor is.
    Pick one in Home Assistant under "Show as".
    """

    @property
    def is_on(self) -> bool | None:
        state = self.raw_state
        if state == SENSOR_VIOLATED:
            return True
        if state == SENSOR_INTACT:
            return False
        return None
