"""Logic commands exposed as buttons - for instance a wicket gate's strike."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import ITEM_COMMAND, ITEM_ID, ITEM_NAME, OPT_BUTTONS
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
        NexoLogicButton(coordinator, item) for item in entry.options.get(OPT_BUTTONS, [])
    )


class NexoLogicButton(NexoEntity, ButtonEntity):
    """Sends one logic command.

    A button rather than a lock or a switch because nothing reports the
    result: an output pulsed by a logic sequence is over before the command
    returns, and a door strike releasing does not move the reed switch until
    someone pushes the door. All Home Assistant can know is that the command
    was sent.
    """

    def __init__(self, coordinator: NexoCoordinator, item: dict[str, Any]) -> None:
        super().__init__(coordinator, f"button_{item[ITEM_ID]}", item[ITEM_NAME])
        self._command: str = item[ITEM_COMMAND]

    async def async_press(self) -> None:
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.trigger_logic, self._command)
        except NexoError as err:
            raise HomeAssistantError(f"{self.name}: command not sent: {err}") from err
