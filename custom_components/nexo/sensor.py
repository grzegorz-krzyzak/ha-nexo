"""Nexo THERMOMETER and ANALOGSENSOR resources."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import OPT_ANALOG_SENSORS, OPT_THERMOMETERS
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
                NexoAnalogSensor(coordinator, "analog", name)
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


class NexoAnalogSensor(NexoResourceEntity, SensorEntity):
    """A raw analogue input value.

    The unit depends on what is wired in - humidity, light level, or a
    resistor-ladder switch that is not a measurement at all - and the central
    unit does not report it, so none is set.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.raw_state
