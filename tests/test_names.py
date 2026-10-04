"""Resource names shared by two types: a repair issue after setup."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.nexo.names import shared_names, used_names
from custom_components.nexo.nexo_client import ImportTypes

from .test_init import OPTIONS, _setup

ANALOG = {**OPTIONS, "analog_outputs": ["SPEED"]}


def test_only_used_names_count() -> None:
    by_type = {"ANALOG_OUTPUT": ["SPEED", "X"], "VENTILATOR": ["SPEED", "X"], "LIGHT": ["L1"]}
    assert shared_names(by_type, {"SPEED", "L1"}) == {"SPEED": ["ANALOG_OUTPUT", "VENTILATOR"]}


def test_used_names_cover_every_role() -> None:
    names = used_names(OPTIONS)
    assert {"KON DOOR", "TMP HALL", "HUMIDITY", "KON GATE", "S1", "S2", "ZG"} <= names


async def test_shared_name_raises_an_issue(hass: HomeAssistant, fake_nexo, caplog) -> None:
    """As measured on a fence: an analog output and a ventilation overlay alike."""
    fake_nexo.listing[ImportTypes.VENTILATOR] = ["SPEED"]
    entry = await _setup(hass, ANALOG)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue("nexo", f"name_shared_{entry.entry_id}")
    assert issue.translation_placeholders == {"resources": "SPEED (ANALOG_OUTPUT, VENTILATOR)"}
    assert "SPEED is the name of resources of types ANALOG_OUTPUT, VENTILATOR" in caplog.text


async def test_no_shared_name_no_issue(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, ANALOG)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue("nexo", f"name_shared_{entry.entry_id}") is None
