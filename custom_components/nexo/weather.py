"""The weather station card: what its five resources hold.

The card (an Elsner P03/3-RS485 station behind it) exposes five resources of
type 21, always in this order - their names are fixed by the card, the
configurator offers no way to rename them. Each is read with 'system C' like
any other resource. The decoding below was matched against the
configurator's weather tab (2026-10-03).
"""

from __future__ import annotations

# Card order of the type 21 list
AURA, TEMPERATURE, LIGHT, WIND, SUN = range(5)
RESOURCES = 5

# Aura bits in the order of the card manual's list of states; matched with
# the weather tab in sunshine (sunny, calm, no rain) and at dusk (twilight,
# calm, no rain). Bit 6, no rain, is not an entity: it is the opposite of
# bit 7. Calm and strong wind are not opposites - the manual's thresholds
# are below 0.3 m/s and above 14.9 m/s, so both are off in between.
AURA_BITS: dict[str, int] = {
    "frost": 0,
    "heat": 1,
    "twilight": 2,
    "sunny": 3,
    "calm": 4,
    "strong_wind": 5,
    "rain": 7,
}

# Sun from the three directions, one byte each, from the top; the low byte
# was 0 in every read
SUN_BYTES: dict[str, int] = {"west": 24, "south": 16, "east": 8}

# The keys of the station's sensors (entity unique ids end with them)
SENSOR_KEYS = ("temperature", "daylight", "wind_speed", *(f"sun_{d}" for d in SUN_BYTES), "aura")


def temperature_celsius(state: int) -> float:
    """Tenths of a kelvin, in whole tenths: 2821 is 9.0 °C, as the weather tab
    shows it (subtracting 273.15 would give 8.95)."""
    return (state - 2731) / 10


def wind_speed(state: int) -> float:
    """Metres per second, from tenths. Confirmed with the station's own calm
    bit (below 0.3 m/s): on at every reading up to 0.2 m/s, off from 0.3
    (2026-10-04 to 05, wind up to 1.8 m/s)."""
    return state / 10


def sun_klx(state: int, direction: str) -> int:
    """Sun from one direction in klx, 0-99."""
    return state >> SUN_BYTES[direction] & 0xFF


def aura_bit(state: int, condition: str) -> bool:
    return bool(state >> AURA_BITS[condition] & 1)
