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
# A thermostat's output followed a new threshold within about 4 s (measured)
BOOST_THERMOSTAT: Final = 10

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
# Thermostats the user picked, each with what its list entry carries: the
# thermometer it reads and the range of its threshold. Nothing by default.
OPT_THERMOSTATS: Final = "thermostats"
THERMOSTAT_NAME: Final = "name"
THERMOSTAT_THERMOMETER: Final = "thermometer"
THERMOSTAT_MIN: Final = "min"
THERMOSTAT_MAX: Final = "max"
# Which way a thermostat works. The hysteresis sign decides it in the central
# unit - positive (the default) is heating control, negative cooling (the
# NXW299.2 manual) - and no query reveals it, so the user says.
THERMOSTAT_DIRECTION: Final = "direction"
THERMOSTAT_HEAT: Final = "heat"
THERMOSTAT_COOL: Final = "cool"
THERMOSTAT_DIRECTIONS: Final = [THERMOSTAT_HEAT, THERMOSTAT_COOL]
# Alarm partitions the user picked: {"name": ..., "mode": ...}. Regular
# partitions are armed and disarmed with the user's code, asked each time and
# never stored; 24h ones (the central unit lists them under their own type)
# are read only. The mode is only the Home Assistant label of "armed" - Nexo
# has one way to arm a partition. Nothing by default.
OPT_PARTITIONS: Final = "partitions"
PARTITION_NAME: Final = "name"
PARTITION_MODE: Final = "mode"
PARTITION_MODES: Final = ["armed_away", "armed_home", "armed_night", "armed_vacation"]
PARTITION_DEFAULT_MODE: Final = "armed_away"
# The partition's state word: bit 0 armed, bit 1 the alarm scheme running
# (measured 2026-10-05; no value for the exit or entry delay)
PARTITION_ARMED: Final = 0x01
PARTITION_ALARMING: Final = 0x02
# After arming or disarming, the partition is read every second for a while
BOOST_PARTITION: Final = 10
# Fired when the central unit refuses a code as wrong: {"partition", "action"}
EVENT_WRONG_CODE: Final = "nexo_wrong_code"

# Analogue outputs (0-10 V) the user picked: each a level 0-100 %. Not
# composed with the output that powers the device - that differs per house.
OPT_ANALOG_OUTPUTS: Final = "analog_outputs"
# Blind outputs (roller-shutter modules) the user picked, and per blind the
# device class it shows as: {name: class}, absent for the default. Nexo knows
# no position and no travel time - the module holds its relay for the time
# set in the output - so neither is kept here. Nothing by default.
OPT_BLINDS: Final = "blinds"
OPT_BLIND_CLASSES: Final = "blind_classes"
BLIND_CLASSES: Final = ["shutter", "awning", "blind", "gate"]
BLIND_DEFAULT_CLASS: Final = "shutter"
# After a command a blind is read every second this long: its word shows
# the relay, held for as long as the output is set to run
BOOST_BLIND: Final = 60
# The weather station card's five resources, in card order (weather.py), when
# the user imports it; absent or empty, not imported. Nothing by default.
OPT_WEATHER: Final = "weather_station"
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
