"""Constants for the Nexwell Nexo integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "nexo"
MANUFACTURER: Final = "Nexwell"

DEFAULT_PORT: Final = 1024
DEFAULT_SCAN_INTERVAL: Final = 10  # seconds
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 300

# Options: which resources are imported, and the logic-driven entities
OPT_BINARY_SENSORS: Final = "binary_sensors"
OPT_THERMOMETERS: Final = "thermometers"
OPT_ANALOG_SENSORS: Final = "analog_sensors"
OPT_COVERS: Final = "covers"
OPT_BUTTONS: Final = "buttons"
OPT_SCAN_INTERVAL: Final = "scan_interval"

# Keys of a configured cover or button
ITEM_ID: Final = "id"
ITEM_NAME: Final = "name"
ITEM_COMMAND: Final = "command"
COVER_DEVICE_CLASS: Final = "device_class"
COVER_OPEN_COMMAND: Final = "open_command"
COVER_CLOSE_COMMAND: Final = "close_command"
COVER_REED_SENSOR: Final = "reed_sensor"
COVER_OPEN_ONLY_WHEN_CLOSED: Final = "open_only_when_closed"

# A reed switch (SENSOR) reads 101 when intact - for a door or gate, closed -
# and 102 when violated. Nothing else is distinguishable: open, ajar and in
# motion all read 102.
SENSOR_INTACT: Final = 101
SENSOR_VIOLATED: Final = 102

# Logic commands ("Komenda zewnetrzna") are limited to 7 characters by the
# NexoTalk specification.
MAX_LOGIC_COMMAND: Final = 7
