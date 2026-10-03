"""Fixtures: a fake central unit in place of the network client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.nexo.nexo_client import ImportTypes, NexoClient, ThermostatInfo


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeNexo:
    """Stands in for NexoClient; states and listings are plain dicts."""

    def __init__(self) -> None:
        self.states: dict[str, int] = {
            "KON DOOR": 101,
            "KON GATE": 101,
            "PIR HALL": 102,
            "TMP HALL": 233,
            "TMP OUTSIDE": 0xFFEC,  # -2.0 as a signed 16-bit value
            "HUMIDITY": 55,
            "S1": 0,
            "S2": 0,
            "S7": 0,
            "ZG": 0,
            "L1": 0,
            "GATE PULSE": 0,
            "VENT": 0,
            "DIM A": 0x8001,  # on at level 128
            "TRS HALL": 0x00D20101,  # threshold 21.0, active, output on (23.3 is warmer)
            "TRS GARAGE": 0x012C0000,  # threshold 30.0, off
        }
        self.listing: dict[ImportTypes, list[str]] = {
            ImportTypes.SENSOR: ["KON DOOR", "KON GATE", "PIR HALL"],
            ImportTypes.THERMOMETER: ["TMP HALL", "TMP OUTSIDE"],
            ImportTypes.ANALOGSENSOR: ["HUMIDITY"],
            ImportTypes.OUTPUT: ["S1", "S2", "S7", "VENT"],
            ImportTypes.LIGHT: ["ZG", "L1", "GATE PULSE"],
            ImportTypes.DIMMER: ["DIM A"],
        }
        self.trigger_logic = MagicMock(return_value="")
        self.turn_on = MagicMock()
        self.turn_off = MagicMock()
        self.set_level = MagicMock()
        self.thermostats = [
            ThermostatInfo("TRS HALL", "TMP HALL", 15, 30),
            ThermostatInfo("TRS GARAGE", "TMP OUTSIDE", 10, 30),
        ]
        self.set_thermostat = MagicMock(side_effect=self._set_thermostat)
        self.write_thermostat = MagicMock(side_effect=self._write_thermostat)
        self.thermostat_on = MagicMock(side_effect=lambda name: self._thermostat(name, active=1))
        self.thermostat_off = MagicMock(side_effect=lambda name: self._thermostat(name, active=0))
        self.disconnect = MagicMock()

    def ping(self) -> bool:
        return True

    def system_info(self) -> str:
        return "Nexo 5.53 R1PLX1H2. Czas dzialania: 48 dn. 5 godz. 49 min"

    def get_state(self, name: str) -> int:
        return self.states[name]

    def list_resources(self, resource_type: ImportTypes) -> list[str]:
        return list(self.listing.get(resource_type, []))

    def list_thermostats(self) -> list[ThermostatInfo]:
        return list(self.thermostats)

    # Like the central unit (measured): a new threshold switches the
    # thermostat on; off, its output is off. The output follows at once here.
    def _set_thermostat(self, temperature: int, name: str) -> None:
        self.states[name] = temperature * 10 << 16 | self.states[name] & 0xFFFF
        self._thermostat(name, active=1)

    def _write_thermostat(self, name: str, threshold: float, active: bool) -> None:
        self.states[name] = round(threshold * 10) << 16 | self.states[name] & 0xFFFF
        self._thermostat(name, active=int(active))

    def _thermostat(self, name: str, active: int) -> None:
        state = self.states[name]
        thermometer = next(t.thermometer for t in self.thermostats if t.name == name)
        reading = self.states[thermometer]
        reading = reading - 0x10000 if reading >= 0x8000 else reading
        warmer = reading > (state >> 16)
        self.states[name] = state & 0xFFFF0000 | active << 8 | (active and warmer)


@pytest.fixture
def fake_nexo():
    fake = FakeNexo()
    factory = MagicMock(spec=NexoClient, return_value=fake)
    with (
        patch("custom_components.nexo.hub.NexoClient", factory),
        patch("custom_components.nexo.config_flow.NexoClient", factory),
    ):
        yield fake
