"""The weather station card: five resources decoded into readings and conditions."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.nexo import weather
from custom_components.nexo.nexo_client import ImportTypes

from .test_init import OPTIONS, _setup, _tick

STATION = ["SP:Aura", "SP:Temperatura", "SP:Światło", "SP:Wiatr", "SP:Słońce"]
WEATHER = {**OPTIONS, "weather_station": STATION}


@pytest.mark.parametrize(
    ("state", "celsius"),
    [
        (2821, 9.0),  # the weather tab showed +9 °C
        (2822, 9.1),  # and 9.1
        (3019, 28.8),
        (2331, -40.0),  # the station's range, -40 to +80: no sign in kelvin
        (3531, 80.0),
    ],
)
def test_temperature_in_tenths_of_a_kelvin(state: int, celsius: float) -> None:
    assert weather.temperature_celsius(state) == pytest.approx(celsius)


def test_sun_from_three_sides() -> None:
    assert {d: weather.sun_klx(0x40572400, d) for d in weather.SUN_BYTES} == {
        "west": 64, "south": 87, "east": 36,
    }


@pytest.mark.parametrize(
    ("aura", "on"),
    [
        (88, {"sunny", "calm"}),  # in sunshine; bit 6, no rain, is no entity
        (84, {"twilight", "calm"}),  # at dusk
        (0, set()),
    ],
)
def test_aura_as_matched_with_the_weather_tab(aura: int, on: set[str]) -> None:
    assert {c for c in weather.AURA_BITS if weather.aura_bit(aura, c)} == on


async def test_entities_on_their_own_device(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, WEATHER)
    states = {
        e: hass.states.get(f"{e}").state
        for e in (
            "sensor.weather_station_temperature",
            "sensor.weather_station_daylight",
            "sensor.weather_station_wind_speed",
            "sensor.weather_station_sun_south",
            "binary_sensor.weather_station_sunny",
            "binary_sensor.weather_station_calm",
            "binary_sensor.weather_station_strong_wind",
            "binary_sensor.weather_station_rain",
        )
    }
    assert states == {
        "sensor.weather_station_temperature": "28.8",
        "sensor.weather_station_daylight": "999",
        "sensor.weather_station_wind_speed": "0.0",  # km/h, Home Assistant's metric default
        "sensor.weather_station_sun_south": "87",
        "binary_sensor.weather_station_sunny": "on",
        "binary_sensor.weather_station_calm": "on",
        "binary_sensor.weather_station_strong_wind": "off",
        "binary_sensor.weather_station_rain": "off",
    }
    assert hass.states.get("binary_sensor.weather_station_rain").attributes["device_class"] == "moisture"
    assert "device_class" not in hass.states.get("binary_sensor.weather_station_strong_wind").attributes
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        ("nexo", f"{entry.entry_id}_weather_station"), entry.entry_id
    )
    central = registry.async_get_device_by_identifier(("nexo", entry.entry_id), entry.entry_id)
    assert device.via_device_id == central.id


async def test_readings_follow_the_measurements(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, WEATHER)
    fake_nexo.states["SP:Aura"] = 0b10000000  # rain
    fake_nexo.states["SP:Wiatr"] = 57
    await _tick(hass, freezer, 60)
    assert hass.states.get("binary_sensor.weather_station_rain").state == "on"
    assert hass.states.get("sensor.weather_station_wind_speed").state == "20.52"  # 5.7 m/s


async def test_nothing_without_the_option(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, OPTIONS)
    assert hass.states.get("sensor.weather_station_temperature") is None


async def test_options_switch(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, OPTIONS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "weather"})
    assert result["step_id"] == "weather"
    await hass.config_entries.options.async_configure(flow_id, {"weather_station": True})
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "save"})
    assert entry.options["weather_station"] == STATION


async def test_options_without_a_station(hass: HomeAssistant, fake_nexo) -> None:
    del fake_nexo.listing[ImportTypes.WEATHER_STATION]
    entry = await _setup(hass, OPTIONS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "weather"})
    assert result["errors"] == {"base": "no_weather_station"}
    result = await hass.config_entries.options.async_configure(flow_id, {})
    assert result["type"] == "menu"
