"""Nexo alarm partitions: armed and disarmed with the user's code, or read only (24h)."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    BOOST_PARTITION,
    DOMAIN,
    EVENT_WRONG_CODE,
    OPT_PARTITIONS,
    PARTITION_ALARMING,
    PARTITION_ARMED,
    PARTITION_DEFAULT_MODE,
    PARTITION_MODE,
    PARTITION_NAME,
)
from .coordinator import NexoCoordinator
from .entity import NexoResourceEntity
from .nexo_client import ImportTypes, NexoClient, NexoCommandError, NexoError

# The Home Assistant label of "armed" -> the feature that offers it
MODES = {
    "armed_away": (AlarmControlPanelState.ARMED_AWAY, AlarmControlPanelEntityFeature.ARM_AWAY),
    "armed_home": (AlarmControlPanelState.ARMED_HOME, AlarmControlPanelEntityFeature.ARM_HOME),
    "armed_night": (
        AlarmControlPanelState.ARMED_NIGHT, AlarmControlPanelEntityFeature.ARM_NIGHT
    ),
    "armed_vacation": (
        AlarmControlPanelState.ARMED_VACATION, AlarmControlPanelEntityFeature.ARM_VACATION
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    items = entry.runtime_data.options.get(OPT_PARTITIONS, [])
    if not items:
        return
    coordinator = entry.runtime_data.coordinator
    # The type is in the data: a 24h partition is listed under its own type
    always_on = set(await coordinator.hub.async_resources(ImportTypes.PARTITION24H))
    async_add_entities(
        NexoPartition(
            coordinator,
            item[PARTITION_NAME],
            item.get(PARTITION_MODE, PARTITION_DEFAULT_MODE),
            read_only=item[PARTITION_NAME] in always_on,
        )
        for item in items
    )


class NexoPartition(NexoResourceEntity, AlarmControlPanelEntity):
    """A partition: disarmed, armed (in the mode picked for it) or alarming.

    The state comes from the state word only - bit 0 armed, bit 1 the alarm
    scheme running; the central unit reports no exit or entry delay. Arming
    and disarming take the user's code each time; it goes to the central
    unit once, never retried and never stored, and stays out of the logs.
    A 24h partition is shown without buttons: it is not disarmed.
    """

    _attr_code_arm_required = True

    def __init__(
        self, coordinator: NexoCoordinator, resource: str, mode: str, read_only: bool
    ) -> None:
        super().__init__(coordinator, "partition", resource)
        self._armed_state, feature = MODES.get(mode, MODES[PARTITION_DEFAULT_MODE])
        self._read_only = read_only
        if read_only:
            self._attr_supported_features = AlarmControlPanelEntityFeature(0)
            self._attr_code_format = None
            # Home Assistant has no "24h" state: armed shows as "watching (24h)",
            # not "armed away" - the state underneath stays armed_away
            self._attr_translation_key = "partition_24h"
        else:
            self._attr_supported_features = feature
            self._attr_code_format = CodeFormat.NUMBER

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        state = self.raw_state
        if state is None:
            return None
        if state & PARTITION_ALARMING:
            return AlarmControlPanelState.TRIGGERED
        if state & PARTITION_ARMED:
            return self._armed_state
        return AlarmControlPanelState.DISARMED

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._async_arm(code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._async_arm(code)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._async_arm(code)

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        await self._async_arm(code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._async_command("disarm", code)

    async def _async_arm(self, code: str | None) -> None:
        await self._async_command("arm", code)

    async def _async_command(self, action: str, code: str | None) -> None:
        if self._read_only:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="partition_read_only",
                translation_placeholders={"partition": self.resource},
            )
        if not code:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="code_required",
                translation_placeholders={"partition": self.resource},
            )
        hub = self.coordinator.hub
        func = hub.client.arm if action == "arm" else hub.client.disarm
        try:
            await hub.async_call(func, code, self.resource)
        except NexoCommandError as err:
            if NexoClient.WRONG_PASSWORD in str(err):
                self.hass.bus.async_fire(
                    EVENT_WRONG_CODE, {"partition": self.resource, "action": action}
                )
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="wrong_code",
                    translation_placeholders={"partition": self.resource},
                ) from err
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="partition_refused",
                translation_placeholders={"partition": self.resource, "reply": str(err)},
            ) from err
        except NexoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="partition_not_confirmed",
                translation_placeholders={"partition": self.resource},
            ) from err
        finally:
            # Read back even after an error: the state comes from the reads
            await self.coordinator.async_boost([self.resource], BOOST_PARTITION)
