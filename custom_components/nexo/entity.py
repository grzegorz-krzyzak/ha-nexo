"""Base entity for the Nexwell Nexo integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

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
    """An entity on a device of its group, linked to the central unit's.

    The groups are the items of the options menu - the central unit's
    resource types - so a long installation is split as it is configured.
    Without a group (the connection) the entity is on the central unit's own
    device. The unique id does not depend on the group: moving an entity to
    another device keeps its entity id, history and settings.
    """

    _attr_has_entity_name = True
    _group: str | None = None
    _model = "Nexo"

    def __init__(
        self,
        coordinator: NexoCoordinator,
        key: str,
        name: str | None = None,
        group: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        # The entry id, not the host: the address can change through
        # reconfiguration, and the entities must survive it.
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        if name is not None:
            self._attr_name = name
        group = group or self._group
        if group is None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                manufacturer=MANUFACTURER,
                model="Nexo",
                translation_key="central_unit",
            )
            return
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{group}")},
            manufacturer=MANUFACTURER,
            model=self._model,
            translation_key=group,
        )
        # The central unit's device is registered before the platforms set up
        if central := dr.async_get(coordinator.hass).async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        ):
            self._attr_device_info["via_device_id"] = central.id
        if name is not None and group != "weather_station":
            self._suggest_entity_id(name)

    def _suggest_entity_id(self, object_name: str) -> None:
        """Keep the entity ids of 0.12 and before, nexo_<name>, rather than
        ids made from the group's device name. Only a suggestion: an entity
        already in the registry keeps its id whatever is suggested."""
        domain = next(
            cls.__module__.split(".")[2]
            for cls in type(self).__mro__
            if cls.__module__.startswith("homeassistant.components.")
        )
        self.entity_id = f"{domain}.nexo_{slugify(object_name)}"


class NexoResourceEntity(NexoEntity):
    """An entity backed by one polled resource of the central unit."""

    def __init__(
        self, coordinator: NexoCoordinator, kind: str, resource: str, group: str | None = None
    ) -> None:
        super().__init__(coordinator, f"{kind}_{resource}", resource, group)
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


class NexoWeatherEntity(NexoEntity):
    """One reading of the weather station, on a device of its own linked to
    the central unit's. Named by translation, keyed by its role - the
    resource names are the card's, not the user's."""

    _group = "weather_station"
    _model = "Weather station"

    def __init__(self, coordinator: NexoCoordinator, key: str, resource: str) -> None:
        super().__init__(coordinator, f"weather_{key}")
        self.resource = resource
        self._attr_translation_key = key

    @property
    def raw_state(self) -> int | None:
        return (self.coordinator.data or {}).get(self.resource)

    @property
    def available(self) -> bool:
        return super().available and self.raw_state is not None
