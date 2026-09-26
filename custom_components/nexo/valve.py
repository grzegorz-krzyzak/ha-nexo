"""Watering programs and similar start/stop pairs of logic commands."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import NexoConfigEntry
from .const import (
    COVER_CLOSE_COMMAND,
    COVER_OPEN_COMMAND,
    ITEM_ID,
    ITEM_NAME,
    OPT_VALVES,
    VALVE_AUTO_CLOSE,
    VALVE_SECTIONS,
)
from .coordinator import NexoCoordinator
from .entity import NexoEntity
from .nexo_client import NexoError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        NexoLogicValve(coordinator, item) for item in entry.options.get(OPT_VALVES, [])
    )


class NexoLogicValve(NexoEntity, ValveEntity):
    """A watering program: opened and closed by logic commands, its state
    read from the section outputs it switches (see valve_state)."""

    _attr_device_class = ValveDeviceClass.WATER
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_reports_position = False

    def __init__(self, coordinator: NexoCoordinator, item: dict[str, Any]) -> None:
        super().__init__(coordinator, f"valve_{item[ITEM_ID]}", item[ITEM_NAME])
        self._id: str = item[ITEM_ID]
        self._open_command: str = item[COVER_OPEN_COMMAND]
        self._close_command: str = item[COVER_CLOSE_COMMAND]
        # Without sections there is no state, so both commands stay available
        self._attr_assumed_state = not item.get(VALVE_SECTIONS)
        minutes = item.get(VALVE_AUTO_CLOSE)
        self._auto_close = timedelta(minutes=minutes) if minutes else None
        self._cancel_auto_close: CALLBACK_TYPE | None = None

    @property
    def is_closed(self) -> bool | None:
        state = self.coordinator.valve_states.get(self._id)
        return None if state is None else not state

    async def async_open_valve(self) -> None:
        await self._send(self._open_command, "opened")
        self._schedule_auto_close()

    async def async_close_valve(self) -> None:
        self._cancel_timer()
        await self._send(self._close_command, "closed")

    async def _send(self, command: str, done: str) -> None:
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.trigger_logic, command)
        except NexoError as err:
            raise HomeAssistantError(f"{self.name} was not {done}: {err}") from err
        await self.coordinator.async_request_refresh()

    # ------------------------------------------------------------ auto close
    #
    # A safety net for programs the central unit does not end on its own.
    # The timer lives in Home Assistant: a restart during watering loses it,
    # so a rule that must end should carry its own duration in the central
    # unit. It also starts when watering is seen starting elsewhere.

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._auto_close:
            if self.is_closed is False and self._cancel_auto_close is None:
                self._schedule_auto_close()
            elif self.is_closed is True:
                self._cancel_timer()
        super()._handle_coordinator_update()

    def _schedule_auto_close(self) -> None:
        if not self._auto_close:
            return
        self._cancel_timer()
        self._cancel_auto_close = async_call_later(
            self.hass, self._auto_close, self._async_auto_close
        )

    async def _async_auto_close(self, _now: Any) -> None:
        self._cancel_auto_close = None
        _LOGGER.info("%s: auto close after %s", self.name, self._auto_close)
        try:
            await self.async_close_valve()
        except HomeAssistantError as err:
            _LOGGER.warning("%s: auto close failed: %s", self.name, err)

    def _cancel_timer(self) -> None:
        if self._cancel_auto_close:
            self._cancel_auto_close()
            self._cancel_auto_close = None

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_timer()
        await super().async_will_remove_from_hass()
