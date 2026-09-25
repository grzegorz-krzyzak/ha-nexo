"""Fixtures: a fake central unit in place of the network client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.nexo.nexo_client import ImportTypes, NexoClient


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
        }
        self.listing: dict[ImportTypes, list[str]] = {
            ImportTypes.SENSOR: ["KON DOOR", "KON GATE", "PIR HALL"],
            ImportTypes.THERMOMETER: ["TMP HALL", "TMP OUTSIDE"],
            ImportTypes.ANALOGSENSOR: ["HUMIDITY"],
        }
        self.trigger_logic = MagicMock(return_value="")
        self.disconnect = MagicMock()

    def ping(self) -> bool:
        return True

    def get_state(self, name: str) -> int:
        return self.states[name]

    def list_resources(self, resource_type: ImportTypes) -> list[str]:
        return list(self.listing.get(resource_type, []))


@pytest.fixture
def fake_nexo():
    fake = FakeNexo()
    factory = MagicMock(spec=NexoClient, return_value=fake)
    with (
        patch("custom_components.nexo.hub.NexoClient", factory),
        patch("custom_components.nexo.config_flow.NexoClient", factory),
    ):
        yield fake
