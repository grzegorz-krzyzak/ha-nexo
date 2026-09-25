"""Nexo SENSOR resources: reed switches, motion detectors and other inputs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import OPT_BINARY_SENSORS, SENSOR_INTACT, SENSOR_VIOLATED
from .entity import NexoEntity, NexoResourceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            NexoConnectionSensor(coordinator),
            *(
                NexoBinarySensor(coordinator, "binary_sensor", name)
                for name in entry.options.get(OPT_BINARY_SENSORS, [])
            ),
        ]
    )


class NexoConnectionSensor(NexoEntity, BinarySensorEntity):
    """Whether the last polling cycle got an answer from the central unit."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connection"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        # It reports the connection, so it must not go unavailable with it.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


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
