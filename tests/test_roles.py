"""Options with a resource in two roles: setup keeps the safest one."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.nexo.roles import resolve_roles

from .test_init import OPTIONS, _setup


def test_the_safest_role_wins() -> None:
    options, conflicts = resolve_roles({
        "excluded": ["GATE PULSE"],
        "output_sensors": ["S7"],
        "lights": ["L1", "GATE PULSE"],
        "switches": ["S7", "VENT", "GATE PULSE", "L1"],
    })
    assert options["excluded"] == ["GATE PULSE"]
    assert options["output_sensors"] == ["S7"]
    assert options["lights"] == ["L1"]
    assert options["switches"] == ["VENT"]
    assert conflicts == {
        "GATE PULSE": ("excluded", ["lights", "switches"]),
        "S7": ("output_sensors", ["switches"]),
        "L1": ("lights", ["switches"]),
    }


def test_no_conflict_changes_nothing() -> None:
    options = {"lights": ["L1"], "switches": ["VENT"], "covers": []}
    assert resolve_roles(options) == (options, {})


async def test_setup_builds_only_the_safe_role(hass: HomeAssistant, fake_nexo, caplog) -> None:
    """An output driving a gate, excluded and yet listed as a switch (a
    restored backup, a hand edit): no switch, a warning and a repair issue."""
    entry = await _setup(hass, {
        **OPTIONS, "valves": [], "excluded": ["GATE PULSE"], "switches": ["GATE PULSE", "VENT"],
    })
    assert hass.states.get("switch.nexo_gate_pulse") is None
    assert hass.states.get("switch.nexo_vent")
    assert "GATE PULSE is set as excluded and as switches" in caplog.text
    issue = ir.async_get(hass).async_get_issue("nexo", f"role_conflict_{entry.entry_id}")
    assert issue.translation_placeholders == {"resources": "GATE PULSE (excluded)"}

    # Fixed in the options: the issue goes
    hass.config_entries.async_update_entry(entry, options={**entry.options, "switches": ["VENT"]})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue("nexo", f"role_conflict_{entry.entry_id}") is None
