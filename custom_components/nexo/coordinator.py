"""Polls the state of every imported resource."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    COVER_REED_SENSOR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FAILED_CYCLES_BEFORE_RELOAD,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_COVERS,
    OPT_SCAN_INTERVAL,
    OPT_THERMOMETERS,
    OPT_VALVES,
    VALVE_MAIN,
    VALVE_SECTIONS,
)
from .hub import NexoHub
from .nexo_client import NexoConnectionError, NexoError
from .valve_state import valve_states

_LOGGER = logging.getLogger(__name__)


class NexoCoordinator(DataUpdateCoordinator[dict[str, int]]):
    """Reads each resource with the numeric 'system C' query.

    Resources are read one at a time, each under the hub lock, so a command
    from an entity waits for at most one read rather than a whole sweep.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, hub: NexoHub) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.hub = hub
        options = entry.options
        names = {
            *options.get(OPT_BINARY_SENSORS, []),
            *options.get(OPT_THERMOMETERS, []),
            *options.get(OPT_ANALOG_SENSORS, []),
            *(
                cover[COVER_REED_SENSOR]
                for cover in options.get(OPT_COVERS, [])
                if cover.get(COVER_REED_SENSOR)
            ),
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
        }
        self.resources = sorted(names)
        self._failed_cycles = 0
        self._valves = options.get(OPT_VALVES, [])
        self._last_active: dict[str, str] = {}
        self.valve_states: dict[str, bool | None] = {}

    async def _async_update_data(self) -> dict[str, int]:
        try:
            data = await self._async_poll()
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

    async def _async_poll(self) -> dict[str, int]:
        if not self.resources:
            # The LAN card drops a connection idle for about 20 s. Keep it
            # open, so a button press does not pay for a reconnect - and so
            # the connection sensor has something to report.
            if not await self.hub.async_call(self.hub.client.ping):
                raise UpdateFailed("The central unit did not answer a ping")
            return {}

        # A single failed read is dropped and the previous value kept - it is
        # an unanswered or out-of-step reply, not a change of state. Only a
        # sweep in which nothing could be read counts as a failure.
        data = dict(self.data or {})
        failures = 0
        for name in self.resources:
            try:
                data[name] = await self.hub.async_call(self.hub.client.get_state, name)
            except NexoConnectionError as err:
                raise UpdateFailed(f"Lost the connection to the central unit: {err}") from err
            except NexoError as err:
                failures += 1
                _LOGGER.debug("Skipped a failed read of %r: %s", name, err)

        if failures == len(self.resources):
            raise UpdateFailed("The central unit did not answer any read")
        if failures:
            _LOGGER.debug("%d of %d reads failed this sweep", failures, len(self.resources))
        return data
