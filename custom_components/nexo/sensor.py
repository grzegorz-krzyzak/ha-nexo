"""Nexo THERMOMETER and ANALOGSENSOR resources."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    ANALOG_KIND,
    ANALOG_KIND_RAW,
    ANALOG_OFFSET,
    OPT_ANALOG_SENSORS,
    OPT_ANALOG_SETTINGS,
    OPT_THERMOMETERS,
)
from .coordinator import NexoCoordinator
from .entity import NexoResourceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            *(
                NexoThermometer(coordinator, "thermometer", name)
                for name in entry.options.get(OPT_THERMOMETERS, [])
            ),
            *(
                NexoAnalogSensor(
                    coordinator,
                    name,
                    entry.options.get(OPT_ANALOG_SETTINGS, {}).get(name, {}),
                )
                for name in entry.options.get(OPT_ANALOG_SENSORS, [])
            ),
        ]
    )


class NexoThermometer(NexoResourceEntity, SensorEntity):
    """Temperature, read numerically in tenths of a degree (233 = 23.3 °C)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        state = self.raw_state
        if state is None:
            return None
        # Assumed to be a signed 16-bit value; only positive temperatures have
        # been seen so far, so the sign handling is unverified.
        if state >= 0x8000:
            state -= 0x10000
        return state / 10


# Kind -> (device class, unit); every kind but raw is a percentage of range
ANALOG_KIND_SPECS: dict[str, tuple[SensorDeviceClass | None, str | None]] = {
    ANALOG_KIND_RAW: (None, None),
    "humidity": (SensorDeviceClass.HUMIDITY, PERCENTAGE),
    "moisture": (SensorDeviceClass.MOISTURE, PERCENTAGE),
    "percent": (None, PERCENTAGE),
}


class NexoAnalogSensor(NexoResourceEntity, SensorEntity):
    """An analogue input: 0-100 of its sensor's configured range.

    The central unit does not report what is wired in - humidity, light
    level, or a resistor-ladder switch that is not a measurement at all -
    so the kind chosen in the options decides the device class and unit.
    Kinds in percent are clamped to 0-100 after the offset.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: NexoCoordinator, resource: str, settings: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, "analog", resource)
        kind = settings.get(ANALOG_KIND, ANALOG_KIND_RAW)
        self._attr_device_class, self._attr_native_unit_of_measurement = (
            ANALOG_KIND_SPECS.get(kind, ANALOG_KIND_SPECS[ANALOG_KIND_RAW])
        )
        # The input reads whole numbers, so the offset is a whole number too
        self._offset: int = int(settings.get(ANALOG_OFFSET, 0))

    @property
    def native_value(self) -> int | None:
        state = self.raw_state
        if state is None:
            return None
        value = state + self._offset
        if self._attr_native_unit_of_measurement == PERCENTAGE:
            value = min(100, max(0, value))
        return value
