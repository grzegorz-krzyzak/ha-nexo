"""Base entity for the Nexwell Nexo integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BOOST_LIGHT, DOMAIN, MANUFACTURER
from .coordinator import NexoCoordinator
from .nexo_client import NexoError


def thermometer_celsius(state: int | None) -> float | None:
    """A thermometer's state in degrees: tenths, assumed a signed 16-bit value
    (only positive temperatures have been seen, so the sign is unverified)."""
    if state is None:
        return None
    if state >= 0x8000:
        state -= 0x10000
    return state / 10


class NexoEntity(CoordinatorEntity[NexoCoordinator]):
    """An entity attached to the central unit's device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: NexoCoordinator, key: str, name: str | None = None
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        # The entry id, not the host: the address can change through
        # reconfiguration, and the entities must survive it.
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        if name is not None:
            self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model="Nexo",
            name="Nexo",
        )


class NexoResourceEntity(NexoEntity):
    """An entity backed by one polled resource of the central unit."""

    def __init__(self, coordinator: NexoCoordinator, kind: str, resource: str) -> None:
        super().__init__(coordinator, f"{kind}_{resource}", resource)
        self.resource = resource

    @property
    def raw_state(self) -> int | None:
        return (self.coordinator.data or {}).get(self.resource)

    @property
    def available(self) -> bool:
        return super().available and self.raw_state is not None


class NexoSwitchedEntity(NexoResourceEntity):
    """An output, light or dimmer that Home Assistant switches.

    The state always comes from reading the resource, never from the command:
    a command the central unit refuses, or one lost with the connection,
    raises and leaves the entity as it was. The resource is then read every
    second for a few seconds, so a command that works shows at once.
    """

    @property
    def is_on(self) -> bool | None:
        state = self.raw_state
        return None if state is None else state != 0

    async def _async_command(self, func: Callable[..., Any], *args: Any) -> None:
        hub = self.coordinator.hub
        try:
            await hub.async_call(func, *args)
        except NexoError as err:
            raise HomeAssistantError(
                f"{self.name}: the central unit did not accept the command: {err}"
            ) from err
        finally:
            # Read back even after an error: a command may have worked
            # although its reply was lost.
            await self.coordinator.async_boost([self.resource], BOOST_LIGHT)
