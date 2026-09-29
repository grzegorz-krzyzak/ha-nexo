"""Constants for the Nexwell Nexo integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "nexo"
MANUFACTURER: Final = "Nexwell"

DEFAULT_PORT: Final = 1024
# Polling. Each group of resources is read at its own interval, in seconds:
# inputs (reed switches, motion) change often and matter at once; outputs
# (watering sections) change on command; lights change on command or at a wall
# switch; measurements (temperatures, analog inputs) drift slowly.
OPT_INTERVAL_INPUTS: Final = "interval_inputs"
OPT_INTERVAL_OUTPUTS: Final = "interval_outputs"
OPT_INTERVAL_LIGHTS: Final = "interval_lights"
OPT_INTERVAL_MEASUREMENTS: Final = "interval_measurements"
DEFAULT_INTERVAL_INPUTS: Final = 5
DEFAULT_INTERVAL_OUTPUTS: Final = 10
DEFAULT_INTERVAL_LIGHTS: Final = 10
DEFAULT_INTERVAL_MEASUREMENTS: Final = 60
MIN_INTERVAL: Final = 2
MAX_INTERVAL: Final = 300
# The coordinator wakes this often and reads what is due.
POLL_TICK: Final = 1
# After a command, the resources that show its effect are read on every tick
# for this long: a gate's reed switch for its travel time (or the default),
# a valve's sections and main valve for the valve time.
BOOST_GATE_DEFAULT: Final = 60
BOOST_VALVE: Final = 30
# A light or switch answers its command within a second or two
BOOST_LIGHT: Final = 5

# The LAN card closes a connection that stays silent longer than the
# tolerance for communication breaks in its settings (5 s by default), and
# the next command then fails. Between polling sweeps the connection is kept
# alive with a ping - answered by the card itself, so no load on the central
# unit - once it has been quiet this long.
KEEPALIVE_IDLE: Final = 3  # seconds
KEEPALIVE_CHECK: Final = 1  # seconds between checks

# Options: which resources are imported, and the logic-driven entities
OPT_BINARY_SENSORS: Final = "binary_sensors"
OPT_THERMOMETERS: Final = "thermometers"
OPT_ANALOG_SENSORS: Final = "analog_sensors"
# Outputs imported read-only, as binary sensors: whatever the user maps onto
# an output in the central unit (a mode, a state of a rule) becomes visible
OPT_OUTPUT_SENSORS: Final = "output_sensors"
# Per analog input, by resource name: {"kind": ..., "offset": ...}
OPT_ANALOG_SETTINGS: Final = "analog_settings"
# Lights (LIGHT, on/off), dimmers (DIMMER, with a level) and switches (OUTPUT
# or LIGHT) the user picked to control. Nothing is picked by default.
OPT_LIGHTS: Final = "lights"
OPT_DIMMERS: Final = "dimmers"
OPT_SWITCHES: Final = "switches"
# Resources the user excluded from every list of outputs and lights to pick
# from - the ones that drive gates or locks, which a switch would fire in one
# click. Kept by the user, not named in the code.
OPT_EXCLUDED: Final = "excluded"
OPT_COVERS: Final = "covers"
OPT_BUTTONS: Final = "buttons"
OPT_VALVES: Final = "valves"
# The single polling interval of 0.1.x and 0.2.x, replaced by the groups above
LEGACY_OPT_SCAN_INTERVAL: Final = "scan_interval"

# An analog input reads 0-100 of the range its sensor is configured for in
# the central unit (0-10 V or a resistive sensor); the unit is not reported.
# The kind picks how Home Assistant shows it; a whole-number offset
# calibrates it, as the central unit itself only offers for thermometers.
ANALOG_KIND: Final = "kind"
ANALOG_OFFSET: Final = "offset"
ANALOG_KIND_RAW: Final = "raw"
ANALOG_KINDS: Final = ["raw", "humidity", "moisture", "percent"]
ANALOG_OFFSET_LIMIT: Final = 10

# Keys of a configured cover or button
ITEM_ID: Final = "id"
ITEM_NAME: Final = "name"
ITEM_COMMAND: Final = "command"
COVER_DEVICE_CLASS: Final = "device_class"
COVER_OPEN_COMMAND: Final = "open_command"
COVER_CLOSE_COMMAND: Final = "close_command"
COVER_REED_SENSOR: Final = "reed_sensor"
COVER_OPEN_ONLY_WHEN_CLOSED: Final = "open_only_when_closed"
# Full travel time plus a margin, in seconds; set, it enables the Step button
COVER_TRAVEL_TIME: Final = "travel_time"

# Keys of a configured valve; its commands use COVER_OPEN_COMMAND and
# COVER_CLOSE_COMMAND like a gate's.
VALVE_SECTIONS: Final = "sections"
VALVE_MAIN: Final = "main_valve"
VALVE_AUTO_CLOSE: Final = "auto_close"  # minutes

# A reed switch (SENSOR) reads 101 when intact - for a door or gate, closed -
# and 102 when violated. Nothing else is distinguishable: open, ajar and in
# motion all read 102.
SENSOR_INTACT: Final = 101
SENSOR_VIOLATED: Final = 102

# Logic commands (external commands) are limited to 7 characters by the
# NexoTalk specification.
MAX_LOGIC_COMMAND: Final = 7

# Gates, doors and buttons each; the options menu has one entry per item.
MAX_ITEMS: Final = 20

# After this many polling cycles in a row without an answer the entry
# reloads, so Home Assistant shows it as retrying setup - the one place the
# integrations page can show a connection problem.
FAILED_CYCLES_BEFORE_RELOAD: Final = 3


def entry_title(host: str, port: int) -> str:
    return f"Nexo · {host}:{port}"


def is_default_title(title: str, host: str, port: int) -> bool:
    """True unless the user renamed the entry; earlier versions used the
    first two forms."""
    return title in ("Nexo", f"Nexo {host}", entry_title(host, port))
