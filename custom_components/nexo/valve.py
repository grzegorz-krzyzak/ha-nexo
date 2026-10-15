"""Watering programs and similar start/stop pairs of logic commands."""

from __future__ import annotations

from typing import Any

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    BOOST_VALVE,
    COVER_CLOSE_COMMAND,
    COVER_OPEN_COMMAND,
    ITEM_ID,
    ITEM_NAME,
    OPT_VALVES,
    VALVE_MAIN,
    VALVE_SECTIONS,
)
from .coordinator import NexoCoordinator
from .entity import NexoEntity
from .nexo_client import NexoError


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

    _group = "logic"
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
        self._watched = [*item.get(VALVE_SECTIONS, []), *filter(None, [item.get(VALVE_MAIN)])]

    @property
    def is_closed(self) -> bool | None:
        state = self.coordinator.valve_states.get(self._id)
        return None if state is None else not state

    async def async_open_valve(self) -> None:
        await self._send(self._open_command, "opened")

    async def async_close_valve(self) -> None:
        await self._send(self._close_command, "closed")

    async def _send(self, command: str, done: str) -> None:
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.trigger_logic, command)
        except NexoError as err:
            raise HomeAssistantError(f"{self.name} was not {done}: {err}") from err
        # Watch the sections closely while the program starts or stops
        await self.coordinator.async_boost(self._watched, BOOST_VALVE)
