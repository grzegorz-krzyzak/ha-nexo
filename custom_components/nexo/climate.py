"""Nexo thermostats.

A Nexo thermostat switches its output on when the room is warmer than its
threshold - the direction is fixed in the central unit, measured. In Home
Assistant terms that is cooling, not heating, so the entity offers only the
cool and off modes: the output is on ("cooling") above the threshold. What
the output does - closing an underfloor heating loop, running a fan - is up
to the installation.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    BOOST_THERMOSTAT,
    OPT_THERMOSTATS,
    THERMOSTAT_MAX,
    THERMOSTAT_MIN,
    THERMOSTAT_NAME,
    THERMOSTAT_THERMOMETER,
)
from .coordinator import NexoCoordinator
from .entity import NexoResourceEntity, thermometer_celsius
from .nexo_client import NexoError, ThermostatState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        NexoThermostat(coordinator, item) for item in entry.options.get(OPT_THERMOSTATS, [])
    )


class NexoThermostat(NexoResourceEntity, ClimateEntity):
    """A thermostat: its threshold, whether it is active, and its output.

    The state always comes from reading the thermostat - one numeric value
    carries the threshold, the active flag and the output - never from the
    command.
    """

    _attr_hvac_modes = [HVACMode.COOL, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # The central unit takes the threshold in whole degrees
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: NexoCoordinator, item: dict[str, Any]) -> None:
        super().__init__(coordinator, "thermostat", item[THERMOSTAT_NAME])
        self._thermometer: str = item[THERMOSTAT_THERMOMETER]
        self._attr_min_temp = item[THERMOSTAT_MIN]
        self._attr_max_temp = item[THERMOSTAT_MAX]

    @property
    def _state(self) -> ThermostatState | None:
        raw = self.raw_state
        return None if raw is None else ThermostatState.unpack(raw)

    @property
    def current_temperature(self) -> float | None:
        return thermometer_celsius((self.coordinator.data or {}).get(self._thermometer))

    @property
    def target_temperature(self) -> float | None:
        state = self._state
        return None if state is None else state.threshold

    @property
    def hvac_mode(self) -> HVACMode | None:
        state = self._state
        if state is None:
            return None
        return HVACMode.COOL if state.active else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        state = self._state
        if state is None:
            return None
        if not state.active:
            return HVACAction.OFF
        return HVACAction.COOLING if state.output_on else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        mode = kwargs.get(ATTR_HVAC_MODE)
        if ATTR_TEMPERATURE in kwargs:
            # Setting the threshold switches the thermostat on in the central
            # unit; Home Assistant expects the mode to stay, so a thermostat
            # that was off is switched off again at once.
            was_off = self.hvac_mode == HVACMode.OFF
            client = self.coordinator.hub.client
            await self._async_command(client.set_thermostat, round(kwargs[ATTR_TEMPERATURE]))
            if mode is None and was_off:
                mode = HVACMode.OFF
        if mode is not None:
            await self.async_set_hvac_mode(mode)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        client = self.coordinator.hub.client
        if hvac_mode == HVACMode.OFF:
            await self._async_command(client.thermostat_off)
        elif hvac_mode == HVACMode.COOL:
            await self._async_command(client.thermostat_on)
        else:
            raise HomeAssistantError(f"{self.name}: unsupported mode {hvac_mode}")

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _async_command(self, func: Any, *args: Any) -> None:
        try:
            await self.coordinator.hub.async_call(func, *args, self.resource)
        except NexoError as err:
            raise HomeAssistantError(
                f"{self.name}: the central unit did not accept the command: {err}"
            ) from err
        finally:
            # Read back even after an error: a command may have worked although
            # its reply was lost.
            await self.coordinator.async_boost([self.resource], BOOST_THERMOSTAT)
