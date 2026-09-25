"""Base entity for the Nexwell Nexo integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import NexoCoordinator


class NexoEntity(CoordinatorEntity[NexoCoordinator]):
    """An entity attached to the central unit's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NexoCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            manufacturer=MANUFACTURER,
            model="Nexo",
            name=entry.title,
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
