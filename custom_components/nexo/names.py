"""Resource names shared by two resource types.

The numeric query 'system C' reads a resource by name. With two resources
of different types under one name it reads one of them - measured: a fence
light's analog output and a "Ventilation (0-10V)" overlay named alike,
'system C' read the overlay's 0 while the output ran at 94 % (2026-10-04).
Nothing in a read shows it, so after setup the integration lists every
type, in the background, and raises a repair issue for the resources it
uses whose name another type shares.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    COVER_REED_SENSOR,
    OPT_ANALOG_OUTPUTS,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_COVERS,
    OPT_DIMMERS,
    OPT_LIGHTS,
    OPT_OUTPUT_SENSORS,
    OPT_SWITCHES,
    OPT_THERMOMETERS,
    OPT_THERMOSTATS,
    OPT_VALVES,
    OPT_WEATHER,
    THERMOSTAT_NAME,
    THERMOSTAT_THERMOMETER,
    VALVE_MAIN,
    VALVE_SECTIONS,
)


def used_names(options: Mapping[str, Any]) -> set[str]:
    """Every resource the integration reads or drives by name."""
    return {
        *(n for key in (
            OPT_BINARY_SENSORS, OPT_THERMOMETERS, OPT_ANALOG_SENSORS, OPT_OUTPUT_SENSORS,
            OPT_LIGHTS, OPT_DIMMERS, OPT_SWITCHES, OPT_ANALOG_OUTPUTS, OPT_WEATHER,
        ) for n in options.get(key, [])),
        *(c[COVER_REED_SENSOR] for c in options.get(OPT_COVERS, []) if c.get(COVER_REED_SENSOR)),
        *(s for v in options.get(OPT_VALVES, []) for s in v.get(VALVE_SECTIONS, [])),
        *(v[VALVE_MAIN] for v in options.get(OPT_VALVES, []) if v.get(VALVE_MAIN)),
        *(t[k] for t in options.get(OPT_THERMOSTATS, [])
          for k in (THERMOSTAT_NAME, THERMOSTAT_THERMOMETER)),
    }


def shared_names(by_type: Mapping[str, list[str]], used: set[str]) -> dict[str, list[str]]:
    """The used names listed under more than one type, with those types."""
    types: dict[str, list[str]] = {}
    for type_name, names in by_type.items():
        for name in set(names):
            types.setdefault(name, []).append(type_name)
    return {name: sorted(t) for name, t in types.items() if len(t) > 1 and name in used}
