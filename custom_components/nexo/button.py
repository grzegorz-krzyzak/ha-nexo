"""Logic commands exposed as buttons - for instance a wicket gate's strike."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    COVER_DEVICE_CLASS,
    COVER_REED_SENSOR,
    COVER_TRAVEL_TIME,
    ITEM_COMMAND,
    ITEM_ID,
    ITEM_NAME,
    OPT_BUTTONS,
    OPT_COVERS,
    SENSOR_INTACT,
    SENSOR_VIOLATED,
)
from .coordinator import NexoCoordinator
from .cover import async_step
from .entity import NexoEntity
from .motion import Motion
from .nexo_client import NexoError

# Per gate type: closed, not closed, reed switch unreadable. Only what the
# reed switch can tell: no opening or closing guessed from the travel time.
STEP_ICONS: dict[str, tuple[str, str, str]] = {
    "garage": ("mdi:garage", "mdi:garage-open", "mdi:garage-alert"),
    "gate": ("mdi:gate", "mdi:gate-open", "mdi:gate-alert"),
    "door": ("mdi:door-closed", "mdi:door-open", "mdi:door"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        [
            *(
                NexoLogicButton(data.coordinator, item)
                for item in entry.options.get(OPT_BUTTONS, [])
            ),
            *(
                NexoStepButton(data.coordinator, item, data.motions[item[ITEM_ID]])
                for item in entry.options.get(OPT_COVERS, [])
                if item.get(COVER_TRAVEL_TIME)
            ),
        ]
    )


class NexoLogicButton(NexoEntity, ButtonEntity):
    """Sends one logic command.

    A button rather than a lock or a switch because nothing reports the
    result: an output pulsed by a logic sequence is over before the command
    returns, and a door strike releasing does not move the reed switch until
    someone pushes the door. All Home Assistant can know is that the command
    was sent.
    """

    _group = "logic"

    def __init__(self, coordinator: NexoCoordinator, item: dict[str, Any]) -> None:
        super().__init__(coordinator, f"button_{item[ITEM_ID]}", item[ITEM_NAME])
        self._command: str = item[ITEM_COMMAND]

    async def async_press(self) -> None:
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.trigger_logic, self._command)
        except NexoError as err:
            raise HomeAssistantError(f"{self.name}: command not sent: {err}") from err


class NexoStepButton(NexoEntity, ButtonEntity):
    """One press of a remote for a gate: up, stop, down, stop.

    Unlike a toggle there is nothing for a dashboard or car widget to
    invert: every press is the same action, and the direction is decided
    here from the reed switch and the last movement.

    A button's state is always the time of its last press, so the gate's
    state shows in the icon instead - for a car widget that offers only this
    button.
    """

    _group = "logic"
    _attr_translation_key = "step"

    def __init__(
        self, coordinator: NexoCoordinator, item: dict[str, Any], motion: Motion
    ) -> None:
        super().__init__(coordinator, f"step_{item[ITEM_ID]}")
        self._suggest_entity_id(f"{item[ITEM_NAME]} step")  # named by translation
        self._item = item
        self._motion = motion
        self._attr_translation_placeholders = {"name": item[ITEM_NAME]}

    @property
    def icon(self) -> str | None:
        reed_sensor = self._item.get(COVER_REED_SENSOR)
        icons = STEP_ICONS.get(self._item[COVER_DEVICE_CLASS])
        if not reed_sensor or icons is None:
            return None
        state = (self.coordinator.data or {}).get(reed_sensor)
        if state == SENSOR_INTACT:
            return icons[0]
        if state == SENSOR_VIOLATED:
            return icons[1]
        return icons[2]

    async def async_press(self) -> None:
        await async_step(self, self._item, self._motion)
