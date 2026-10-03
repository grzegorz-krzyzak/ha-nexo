"""Nexo THERMOMETER and ANALOGSENSOR resources, and the weather station's readings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfSpeed,
    UnitOfTemperature,
)
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
    OPT_WEATHER,
)
from .coordinator import NexoCoordinator
from . import weather
from .entity import NexoResourceEntity, NexoWeatherEntity, thermometer_celsius


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
            *_weather_sensors(coordinator, entry.options.get(OPT_WEATHER, [])),
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
        return thermometer_celsius(self.raw_state)


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


def _weather_sensors(coordinator: NexoCoordinator, names: list[str]) -> list[SensorEntity]:
    if len(names) != weather.RESOURCES:
        return []
    return [
        WeatherTemperature(coordinator, "temperature", names[weather.TEMPERATURE]),
        WeatherLight(coordinator, "daylight", names[weather.LIGHT]),
        WeatherWind(coordinator, "wind_speed", names[weather.WIND]),
        *(
            WeatherSun(coordinator, f"sun_{direction}", names[weather.SUN], direction)
            for direction in weather.SUN_BYTES
        ),
        WeatherAura(coordinator, "aura", names[weather.AURA]),
    ]


class WeatherTemperature(NexoWeatherEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        state = self.raw_state
        return None if state is None else weather.temperature_celsius(state)


class WeatherLight(NexoWeatherEntity, SensorEntity):
    """Daylight, 0-999 lx: it saturates long before full daylight."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.raw_state


class WeatherWind(NexoWeatherEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.WIND_SPEED
    _attr_native_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND
    _attr_state_class = SensorStateClass.MEASUREMENT
    # As the station and the configurator show it, not converted to km/h
    _attr_suggested_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        state = self.raw_state
        return None if state is None else weather.wind_speed(state)


class WeatherSun(NexoWeatherEntity, SensorEntity):
    """Sun on one side of the station, in klx - beyond the lx unit of the
    illuminance class, so without it."""

    _attr_native_unit_of_measurement = "klx"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-sunny"

    def __init__(
        self, coordinator: NexoCoordinator, key: str, resource: str, direction: str
    ) -> None:
        super().__init__(coordinator, key, resource)
        self._direction = direction

    @property
    def native_value(self) -> int | None:
        state = self.raw_state
        return None if state is None else weather.sun_klx(state, self._direction)


class WeatherAura(NexoWeatherEntity, SensorEntity):
    """The raw state bits, for what the separate conditions do not show."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        return self.raw_state
