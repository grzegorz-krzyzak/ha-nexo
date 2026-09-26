"""Gates and doors driven by Nexo logic commands."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    COVER_CLOSE_COMMAND,
    COVER_DEVICE_CLASS,
    COVER_OPEN_COMMAND,
    COVER_OPEN_ONLY_WHEN_CLOSED,
    COVER_REED_SENSOR,
    ITEM_ID,
    ITEM_NAME,
    OPT_COVERS,
    SENSOR_INTACT,
    SENSOR_VIOLATED,
)
from .coordinator import NexoCoordinator
from .entity import NexoEntity
from .motion import Motion, step
from .nexo_client import NexoClient, NexoError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        NexoLogicCover(data.coordinator, item, data.motions.get(item[ITEM_ID]))
        for item in entry.options.get(OPT_COVERS, [])
    )


class _NotClosed(Exception):
    """The guard found the gate not confirmed closed and did not open it."""

    def __init__(self, state: int) -> None:
        super().__init__(state)
        self.state = state


def _open_if_closed(client: NexoClient, reed_sensor: str, command: str) -> None:
    # Read immediately before the command, never from the last poll: the gate
    # has other triggers (remotes, wall switches, intercoms) that may have
    # moved it since.
    state = client.get_state(reed_sensor)
    if state != SENSOR_INTACT:
        raise _NotClosed(state)
    client.trigger_logic(command)


class NexoLogicCover(NexoEntity, CoverEntity):
    """A gate or door with a reed switch, opened and closed by logic commands.

    The reed switch only tells closed from not closed, so there is no
    is_opening / is_closing and no position - guessing them from a timer
    drifts from reality every time the gate stops halfway.

    A logic rule that refuses to run does so silently, and a mistyped command
    is acknowledged exactly like a real one. Nothing the central unit returns
    confirms that the gate moved; only the reed switch changing does.
    """

    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(
        self,
        coordinator: NexoCoordinator,
        item: dict[str, Any],
        motion: Motion | None,
    ) -> None:
        super().__init__(coordinator, f"cover_{item[ITEM_ID]}", item[ITEM_NAME])
        self._item = item
        self._motion = motion
        self._open_command: str = item[COVER_OPEN_COMMAND]
        self._close_command: str = item[COVER_CLOSE_COMMAND]
        # Optional: without a reed switch the state is simply unknown.
        self._reed_sensor: str | None = item.get(COVER_REED_SENSOR)
        self._guarded: bool = item[COVER_OPEN_ONLY_WHEN_CLOSED] and bool(self._reed_sensor)
        self._attr_device_class = CoverDeviceClass(item[COVER_DEVICE_CLASS])
        # Without the guard, an open command sent mid-travel is how the door
        # is stopped part-way, so both buttons must stay usable in every state.
        # Without a reed switch there is no state to disable either by.
        self._attr_assumed_state = not self._guarded

    @property
    def is_closed(self) -> bool | None:
        if self._reed_sensor is None:
            return None
        state = (self.coordinator.data or {}).get(self._reed_sensor)
        if state == SENSOR_INTACT:
            return True
        if state == SENSOR_VIOLATED:
            return False
        return None

    async def async_open_cover(self, **kwargs: Any) -> None:
        hub = self.coordinator.hub
        try:
            if self._guarded:
                await hub.async_call(
                    _open_if_closed, hub.client, self._reed_sensor, self._open_command
                )
            else:
                await hub.async_call(hub.client.trigger_logic, self._open_command)
        except _NotClosed as err:
            # Refusing silently would be indistinguishable from a failure,
            # especially from a voice assistant.
            raise HomeAssistantError(
                f"{self.name} was not opened: it is not confirmed closed "
                f"({self._reed_sensor} reads {err.state})"
            ) from err
        except NexoError as err:
            raise HomeAssistantError(
                f"{self.name} was not opened: {err}"
            ) from err
        if self._motion:
            self._motion.record("up", time.monotonic())
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        # Unconditional: closing an already closed gate is harmless, and it
        # works even when the reed switch cannot be read - the safe recovery
        # action for a gate in an unknown state.
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.trigger_logic, self._close_command)
        except NexoError as err:
            raise HomeAssistantError(f"{self.name} was not closed: {err}") from err
        if self._motion:
            self._motion.record("down", time.monotonic())
        await self.coordinator.async_request_refresh()

    async def async_toggle(self, **kwargs: Any) -> None:
        # With a travel time set, toggle behaves like the remote: up, stop,
        # down, stop. Home Assistant's own toggle always closes a gate that
        # is not closed, so after stopping it on the way down no press could
        # send it back up.
        if self._motion is None:
            await super().async_toggle(**kwargs)
            return
        await async_step(self, self._item, self._motion)


async def async_step(entity: NexoEntity, item: dict[str, Any], motion: Motion) -> None:
    """Run one remote-style step of the gate described by item."""
    hub = entity.coordinator.hub
    try:
        await hub.async_call(
            step,
            hub.client,
            motion,
            item.get(COVER_REED_SENSOR),
            item[COVER_OPEN_COMMAND],
            item[COVER_CLOSE_COMMAND],
        )
    except NexoError as err:
        raise HomeAssistantError(f"{entity.name}: command not sent: {err}") from err
    await entity.coordinator.async_request_refresh()
