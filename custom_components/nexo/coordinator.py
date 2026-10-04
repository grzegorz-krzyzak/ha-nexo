"""Polls the state of every imported resource."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    COVER_REED_SENSOR,
    DEFAULT_INTERVAL_INPUTS,
    DEFAULT_INTERVAL_LIGHTS,
    DEFAULT_INTERVAL_MEASUREMENTS,
    DEFAULT_INTERVAL_OUTPUTS,
    DOMAIN,
    FAILED_CYCLES_BEFORE_RELOAD,
    OPT_ANALOG_OUTPUTS,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_COVERS,
    OPT_DIMMERS,
    OPT_INTERVAL_INPUTS,
    OPT_INTERVAL_LIGHTS,
    OPT_INTERVAL_MEASUREMENTS,
    OPT_INTERVAL_OUTPUTS,
    OPT_LIGHTS,
    OPT_OUTPUT_SENSORS,
    OPT_SWITCHES,
    OPT_THERMOMETERS,
    OPT_THERMOSTATS,
    OPT_VALVES,
    OPT_WEATHER,
    POLL_TICK,
    THERMOSTAT_NAME,
    THERMOSTAT_THERMOMETER,
    VALVE_MAIN,
    VALVE_SECTIONS,
)
from .hub import NexoHub
from .nexo_client import NexoConnectionError, NexoError
from .valve_state import valve_states

_LOGGER = logging.getLogger(__name__)


class NexoCoordinator(DataUpdateCoordinator[dict[str, int]]):
    """Reads each resource with the numeric 'system C' query.

    Resources fall into groups polled at their own intervals: inputs,
    outputs, lights and measurements. The coordinator wakes every POLL_TICK
    seconds and reads only the groups that are due, plus any resource boosted
    after a command. Each read runs under the hub lock on its own, so a
    command from an entity waits for at most one read rather than a whole
    sweep.

    The central unit answers one read at a time, about 20 a second however
    many connections ask, so a tick reads in order of urgency: boosted
    resources, inputs, outputs, lights, measurements. A reed switch then
    never waits behind a sweep of lights.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, hub: NexoHub) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_TICK),
            # A tick that reads nothing, or reads what was there before,
            # does not wake the entities.
            always_update=False,
        )
        self.hub = hub
        options = entry.options
        inputs = {
            *options.get(OPT_BINARY_SENSORS, []),
            *(
                cover[COVER_REED_SENSOR]
                for cover in options.get(OPT_COVERS, [])
                if cover.get(COVER_REED_SENSOR)
            ),
        }
        outputs = {
            *options.get(OPT_OUTPUT_SENSORS, []),
            *(
                section
                for valve in options.get(OPT_VALVES, [])
                for section in valve.get(VALVE_SECTIONS, [])
            ),
            *(
                valve[VALVE_MAIN]
                for valve in options.get(OPT_VALVES, [])
                if valve.get(VALVE_MAIN)
            ),
        } - inputs
        lights = {
            *options.get(OPT_LIGHTS, []),
            *options.get(OPT_DIMMERS, []),
            *options.get(OPT_SWITCHES, []),
            # Analogue outputs change on command, from the logic or a wall
            # button - as lights do
            *options.get(OPT_ANALOG_OUTPUTS, []),
        } - inputs - outputs
        measurements = {
            *options.get(OPT_THERMOMETERS, []),
            *options.get(OPT_ANALOG_SENSORS, []),
            # A thermostat and the thermometer it reads: the threshold changes
            # only on command, and what the output does acts over minutes
            *(t[THERMOSTAT_NAME] for t in options.get(OPT_THERMOSTATS, [])),
            *(t[THERMOSTAT_THERMOMETER] for t in options.get(OPT_THERMOSTATS, [])),
            # The weather station: rain or a frost warning a minute late is
            # fine, the central unit acts on them itself
            *options.get(OPT_WEATHER, []),
        } - inputs - outputs - lights
        # (interval in seconds, resources) in order of urgency; a resource in
        # two roles is read with the more urgent group
        self._groups: list[tuple[float, list[str]]] = [
            (options.get(OPT_INTERVAL_INPUTS, DEFAULT_INTERVAL_INPUTS), sorted(inputs)),
            (options.get(OPT_INTERVAL_OUTPUTS, DEFAULT_INTERVAL_OUTPUTS), sorted(outputs)),
            (options.get(OPT_INTERVAL_LIGHTS, DEFAULT_INTERVAL_LIGHTS), sorted(lights)),
            (
                options.get(OPT_INTERVAL_MEASUREMENTS, DEFAULT_INTERVAL_MEASUREMENTS),
                sorted(measurements),
            ),
        ]
        self.resources = sorted(inputs | outputs | lights | measurements)
        self._next_due = [0.0] * len(self._groups)  # monotonic time
        self._boosted: dict[str, float] = {}  # resource -> boost ends
        self._failed_cycles = 0
        self._valves = options.get(OPT_VALVES, [])
        self._last_active: dict[str, str] = {}
        self.valve_states: dict[str, bool | None] = {}

    async def async_boost(self, resources: Iterable[str], seconds: float) -> None:
        """Read these resources on every tick for a while, starting now.

        Called after a command, so the effect - a gate leaving its closed
        position, a watering section switching on - shows within a second
        instead of at the group's next turn.
        """
        until = time.monotonic() + seconds
        for name in resources:
            if name in self.resources:
                self._boosted[name] = max(self._boosted.get(name, 0.0), until)
        await self.async_request_refresh()

    def _due(self) -> list[str] | None:
        """The resources to read this tick, most urgent first, or None if
        nothing is due.

        With no resources at all, an empty list is due at the inputs'
        interval: the tick then only pings.
        """
        now = time.monotonic()
        if not self.resources:
            if now < self._next_due[0]:
                return None
            self._next_due[0] = now + self._groups[0][0]
            return []
        self._boosted = {name: until for name, until in self._boosted.items() if until > now}
        # dict keeps the first position of a resource due for two reasons
        due: dict[str, None] = dict.fromkeys(sorted(self._boosted))
        for index, (interval, names) in enumerate(self._groups):
            if names and now >= self._next_due[index]:
                due.update(dict.fromkeys(names))
                self._next_due[index] = now + interval
        return list(due) if due else None

    async def _async_update_data(self) -> dict[str, int]:
        due = self._due()
        if due is None:
            # Nothing to read this tick. Keep the last outcome as it was:
            # a tick with no reads proves nothing about the connection.
            if not self.last_update_success:
                raise UpdateFailed("The central unit is still not answering")
            return self.data or {}
        try:
            data = await self._async_poll(due)
        except UpdateFailed:
            self._failed_cycles += 1
            if self._failed_cycles == FAILED_CYCLES_BEFORE_RELOAD:
                # Setting up again fails while the central unit is away, and
                # Home Assistant then shows the entry as retrying - visible on
                # the integrations page, and it keeps retrying on its own.
                _LOGGER.warning(
                    "No answer from the central unit in %d polling cycles; reloading",
                    self._failed_cycles,
                )
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            raise
        self._failed_cycles = 0
        self.valve_states = valve_states(self._valves, data, self._last_active)
        return data

    async def _async_poll(self, due: list[str]) -> dict[str, int]:
        if not due:
            # No resources imported: a ping at the inputs' interval keeps the
            # connection sensor meaningful.
            if not await self.hub.async_call(self.hub.client.ping):
                raise UpdateFailed("The central unit did not answer a ping")
            return {}

        # A single failed read is dropped and the previous value kept - it is
        # an unanswered or out-of-step reply, not a change of state. Only a
        # sweep in which nothing could be read counts as a failure.
        data = dict(self.data or {})
        failures = 0
        for name in due:
            try:
                data[name] = await self.hub.async_call(self.hub.client.get_state, name)
            except NexoConnectionError as err:
                raise UpdateFailed(f"Lost the connection to the central unit: {err}") from err
            except NexoError as err:
                failures += 1
                _LOGGER.debug("Skipped a failed read of %r: %s", name, err)

        if failures == len(due):
            raise UpdateFailed("The central unit did not answer any read")
        if failures:
            _LOGGER.debug("%d of %d reads failed this sweep", failures, len(due))
        return data
