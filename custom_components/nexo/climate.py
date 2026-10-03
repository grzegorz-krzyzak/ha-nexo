"""Nexo thermostats.

The hysteresis sign set in the central unit decides how a thermostat works
(the NXW299.2 manual): positive - the default - is heating control, its
output on above the threshold meaning "warm enough"; negative is cooling,
the output on below the threshold. No query reveals the sign, so each
thermostat is set to heat (the default) or cool in the options. Either way
the output on means the room has reached the threshold, and the thermostat
asks for heat or cold while it is off. What the output does physically is up
to the wiring.
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
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    BOOST_THERMOSTAT,
    THERMOSTAT_COOL,
    THERMOSTAT_DIRECTION,
    THERMOSTAT_HEAT,
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

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # The threshold is in tenths both ways: read in the state word, written as
    # NexoVision writes it (captured; 'ustaw' would drop the fraction)
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = 0.1

    def __init__(self, coordinator: NexoCoordinator, item: dict[str, Any]) -> None:
        super().__init__(coordinator, "thermostat", item[THERMOSTAT_NAME])
        self._thermometer: str = item[THERMOSTAT_THERMOMETER]
        self._attr_min_temp = item[THERMOSTAT_MIN]
        self._attr_max_temp = item[THERMOSTAT_MAX]
        cooling = item.get(THERMOSTAT_DIRECTION, THERMOSTAT_HEAT) == THERMOSTAT_COOL
        self._mode = HVACMode.COOL if cooling else HVACMode.HEAT
        self._working = HVACAction.COOLING if cooling else HVACAction.HEATING
        self._attr_hvac_modes = [self._mode, HVACMode.OFF]

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
        return self._mode if state.active else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        state = self._state
        if state is None:
            return None
        if not state.active:
            return HVACAction.OFF
        # The output on means the threshold is reached; off, the thermostat
        # asks for heat (or cold) - whether any flows is up to its source.
        return HVACAction.IDLE if state.output_on else self._working

    async def async_set_temperature(self, **kwargs: Any) -> None:
        mode = kwargs.get(ATTR_HVAC_MODE)
        if ATTR_TEMPERATURE not in kwargs:
            if mode is not None:
                await self.async_set_hvac_mode(mode)
            return
        if mode is not None and mode not in self.hvac_modes:
            raise HomeAssistantError(f"{self.name}: unsupported mode {mode}")
        # One command sets the threshold and whether the thermostat is on: the
        # mode stays as it is unless one is given with the temperature
        active = self.hvac_mode != HVACMode.OFF if mode is None else mode != HVACMode.OFF
        threshold = round(float(kwargs[ATTR_TEMPERATURE]), 1)
        await self._async_write(threshold, active)

    async def _async_write(self, threshold: float, active: bool) -> None:
        try:
            await self.coordinator.hub.async_call(
                self.coordinator.hub.client.write_thermostat, self.resource, threshold, active
            )
        except NexoError as err:
            raise HomeAssistantError(
                f"{self.name}: the central unit did not accept the command: {err}"
            ) from err
        finally:
            await self.coordinator.async_boost([self.resource], BOOST_THERMOSTAT)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        client = self.coordinator.hub.client
        if hvac_mode == HVACMode.OFF:
            await self._async_command(client.thermostat_off)
        elif hvac_mode == self._mode:
            await self._async_command(client.thermostat_on)
        else:
            raise HomeAssistantError(f"{self.name}: unsupported mode {hvac_mode}")

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(self._mode)

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
