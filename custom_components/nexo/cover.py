"""Gates and doors driven by Nexo logic commands, and blind outputs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    BLIND_DEFAULT_CLASS,
    BOOST_BLIND,
    BOOST_GATE_DEFAULT,
    COVER_CLOSE_COMMAND,
    COVER_CLOSED_SENSOR,
    COVER_DEVICE_CLASS,
    COVER_OPEN_COMMAND,
    COVER_OPEN_SENSOR,
    COVER_RUN_SENSOR,
    COVER_SENSORS,
    ITEM_ID,
    ITEM_NAME,
    OPT_BLIND_CLASSES,
    OPT_BLINDS,
    OPT_COVERS,
    SENSOR_INTACT,
    SENSOR_VIOLATED,
)
from .coordinator import NexoCoordinator
from .entity import NexoEntity, NexoResourceEntity
from .nexo_client import NexoClient, NexoError

CLOSED = "closed"
OPEN = "open"
OPENING = "opening"
CLOSING = "closing"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        NexoLogicCover(data.coordinator, item) for item in entry.options.get(OPT_COVERS, [])
    )
    classes = entry.options.get(OPT_BLIND_CLASSES, {})
    async_add_entities(
        NexoBlind(data.coordinator, name, classes.get(name, BLIND_DEFAULT_CLASS))
        for name in data.options.get(OPT_BLINDS, [])
    )


class NexoLogicCover(NexoEntity, CoverEntity):
    """A gate or door opened and closed by logic commands, its state read
    from the sensors wired to it - each optional:

    - closed sensor (a reed switch): 101 at the closed position;
    - open sensor: 101 at the open position;
    - run sensor: 102 while the drive runs.

    Commands go out as they are, with no conditions and no waiting: what the
    gate may do is up to the central unit's rules, which refuse silently -
    the sensors show what the gate did. Anything more (a remote-style step,
    notifications, timers) belongs in Home Assistant automations.

    Opening and closing need the run sensor: a gate's direction comes from
    where it started - from closed it opens, from open it closes; without an
    open sensor, from anywhere not closed it closes. A gate taken for opening
    that reaches closed was closing (a stop between two runs fell between
    reads), and the same the other way. A run whose start was not seen (Home
    Assistant started mid-run) has no direction: the state comes from the
    closed and open sensors alone.
    """

    _group = "logic"
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    # Nothing here refuses a command, and a command mid-travel is how many
    # drives are stopped part-way, so both stay usable in every state.
    _attr_assumed_state = True

    def __init__(self, coordinator: NexoCoordinator, item: dict[str, Any]) -> None:
        super().__init__(coordinator, f"cover_{item[ITEM_ID]}", item[ITEM_NAME])
        self._open_command: str = item[COVER_OPEN_COMMAND]
        self._close_command: str = item[COVER_CLOSE_COMMAND]
        self._closed_sensor: str | None = item.get(COVER_CLOSED_SENSOR)
        self._open_sensor: str | None = item.get(COVER_OPEN_SENSOR)
        self._run_sensor: str | None = item.get(COVER_RUN_SENSOR)
        self._watched = [item[key] for key in COVER_SENSORS if item.get(key)]
        self._attr_device_class = CoverDeviceClass(item[COVER_DEVICE_CLASS])
        # The end position the gate was last at, until a run ends elsewhere
        self._origin: str | None = None
        self._direction: str | None = None
        self._last_run: int | None = None
        self._last_end: str | None = None

    def _read(self, sensor: str | None) -> int | None:
        return (self.coordinator.data or {}).get(sensor) if sensor else None

    def _end(self) -> str | None:
        """closed / open while an end sensor says so, else None."""
        if self._read(self._closed_sensor) == SENSOR_INTACT:
            return CLOSED
        if self._read(self._open_sensor) == SENSOR_INTACT:
            return OPEN
        return None

    @property
    def is_closed(self) -> bool | None:
        closed = self._read(self._closed_sensor)
        if closed == SENSOR_INTACT:
            return True
        if closed == SENSOR_VIOLATED:
            return False
        # No closed sensor, or unread: only the open sensor can tell
        if self._read(self._open_sensor) == SENSOR_INTACT:
            return False
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The reads before the first update: a start is a change from them
        self._track()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track()
        super()._handle_coordinator_update()

    def _track(self) -> None:
        """Follow the run sensor's edges: the direction of each run."""
        if self._run_sensor is None:
            return
        run = self._read(self._run_sensor)
        if run is None:
            return  # an unread run sensor changes nothing
        end = self._end()
        if end is not None:
            self._origin = end
        if run != SENSOR_VIOLATED:
            if self._last_run == SENSOR_VIOLATED and end is None:
                self._origin = None  # stopped part-way
            self._direction = None
        elif self._last_run == SENSOR_INTACT:
            self._direction = self._start_direction()
        elif self._last_run == SENSOR_VIOLATED and end is not None and end != self._last_end:
            # Reached an end while running: it was heading there
            self._direction = CLOSING if end == CLOSED else OPENING
        self._last_run, self._last_end = run, end

    def _start_direction(self) -> str | None:
        if self._origin == CLOSED:
            return OPENING
        if self._origin == OPEN:
            return CLOSING
        if self._closed_sensor and not self._open_sensor:
            return CLOSING  # from not closed - the only other place it can be
        return None

    @property
    def is_opening(self) -> bool | None:
        if self._run_sensor is None:
            return None
        return self._direction == OPENING

    @property
    def is_closing(self) -> bool | None:
        if self._run_sensor is None:
            return None
        return self._direction == CLOSING

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_command(self._open_command, "opened")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_command(self._close_command, "closed")

    async def _async_command(self, command: str, done: str) -> None:
        """Send the command; the central unit acknowledges it whether or not a
        rule carried it out, so only the sensors, read closely afterwards,
        show the result."""
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.trigger_logic, command)
        except NexoError as err:
            raise HomeAssistantError(f"{self.name} was not {done}: {err}") from err
        if self._watched:
            await self.coordinator.async_boost(self._watched, BOOST_GATE_DEFAULT)
        else:
            await self.coordinator.async_request_refresh()


class NexoBlind(NexoResourceEntity, CoverEntity):
    """A blind output of a roller-shutter module: raise, lower, stop.

    The state word shows the relay, not the blind: raising or lowering while
    the module holds the relay for the time set in the output, then stopped.
    For a blind whose time covers its travel that is the motion; for a device
    started by an impulse (a gate drive) it lasts only the impulse - Nexo has
    nothing more to tell. No position, so both directions always stay usable.
    """

    _group = "blinds"
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    _attr_assumed_state = True

    def __init__(self, coordinator: NexoCoordinator, name: str, device_class: str) -> None:
        super().__init__(coordinator, "blind", name)
        self._attr_device_class = CoverDeviceClass(device_class)

    @property
    def is_closed(self) -> bool | None:
        return None

    @property
    def is_opening(self) -> bool:
        return self.raw_state == NexoClient.BLIND_RAISING

    @property
    def is_closing(self) -> bool:
        return self.raw_state == NexoClient.BLIND_LOWERING

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_move(NexoClient.BLIND_RAISING)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_move(NexoClient.BLIND_LOWERING)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._async_move(NexoClient.BLIND_STOPPED)

    async def _async_move(self, value: int) -> None:
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.move_blind, self.resource, value)
        except NexoError as err:
            raise HomeAssistantError(
                f"{self.name}: the central unit did not accept the command: {err}"
            ) from err
        finally:
            # Read back even after an error: a command may have worked
            # although its reply was lost.
            await self.coordinator.async_boost([self.resource], BOOST_BLIND)
