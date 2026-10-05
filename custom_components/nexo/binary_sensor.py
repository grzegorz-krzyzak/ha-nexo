"""Nexo SENSOR resources (reed switches, motion detectors), outputs read as sensors,
and the weather station's conditions."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from . import weather
from .const import (
    OPT_BINARY_SENSORS,
    OPT_OUTPUT_SENSORS,
    OPT_WEATHER,
    SENSOR_INTACT,
    SENSOR_VIOLATED,
)
from .entity import NexoEntity, NexoResourceEntity, NexoWeatherEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    weather_names = entry.options.get(OPT_WEATHER, [])
    async_add_entities(
        [
            NexoConnectionSensor(coordinator),
            *(
                NexoBinarySensor(coordinator, "binary_sensor", name)
                for name in entry.options.get(OPT_BINARY_SENSORS, [])
            ),
            *(
                NexoOutputSensor(coordinator, "output", name)
                for name in entry.runtime_data.options.get(OPT_OUTPUT_SENSORS, [])
            ),
            *(
                WeatherCondition(coordinator, condition, weather_names[weather.AURA])
                for condition in weather.AURA_BITS
                if len(weather_names) == weather.RESOURCES
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
        # The id of 0.12 and before; the name is now short, after "Central unit"
        self._suggest_entity_id("connection to central unit")

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

    _group = "sensors"

    @property
    def is_on(self) -> bool | None:
        state = self.raw_state
        if state == SENSOR_VIOLATED:
            return True
        if state == SENSOR_INTACT:
            return False
        return None


class NexoOutputSensor(NexoResourceEntity, BinarySensorEntity):
    """An output, read only: on while it is switched on (non-zero).

    Nothing is assumed about what the output means - a rule in the central
    unit may switch it to mirror a mode or a variable. Name it and pick a
    class in Home Assistant.
    """

    _group = "outputs"

    @property
    def is_on(self) -> bool | None:
        state = self.raw_state
        return None if state is None else state != 0



# A class only where its words fit: "unsafe" for strong wind or "light" for
# twilight (on when it is dark) would read wrong in the interface and in Assist
WEATHER_CLASSES: dict[str, BinarySensorDeviceClass] = {
    "frost": BinarySensorDeviceClass.COLD,
    "heat": BinarySensorDeviceClass.HEAT,
    "rain": BinarySensorDeviceClass.MOISTURE,
}
WEATHER_ICONS: dict[str, str] = {
    "twilight": "mdi:weather-night",
    "sunny": "mdi:weather-sunny",
    "calm": "mdi:weather-windy-variant",
    "strong_wind": "mdi:weather-windy",
}


class WeatherCondition(NexoWeatherEntity, BinarySensorEntity):
    """One bit of the station's aura: frost, heat, twilight, sunny, calm,
    strong wind or rain, as the station itself judges them."""

    def __init__(self, coordinator, condition: str, resource: str) -> None:
        super().__init__(coordinator, condition, resource)
        self._condition = condition
        self._attr_device_class = WEATHER_CLASSES.get(condition)
        self._attr_icon = WEATHER_ICONS.get(condition)

    @property
    def is_on(self) -> bool | None:
        state = self.raw_state
        return None if state is None else weather.aura_bit(state, self._condition)
