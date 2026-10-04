"""One role per resource, whatever the stored options say.

The options screens keep the roles apart - a resource picked twice is a
form error. Options can still arrive with a resource in two roles: edited
by hand, restored from an old backup, written by another version. Setup
then keeps the safest role, warns in the log and raises a repair issue,
so an output driving a gate never becomes a switch by accident.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import OPT_DIMMERS, OPT_EXCLUDED, OPT_LIGHTS, OPT_OUTPUT_SENSORS, OPT_SWITCHES

# Safest first: never offered, read only, then controlled
PRIORITY = (OPT_EXCLUDED, OPT_OUTPUT_SENSORS, OPT_LIGHTS, OPT_DIMMERS, OPT_SWITCHES)


def resolve_roles(
    options: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, tuple[str, list[str]]]]:
    """The options with each resource in its safest role only, and per
    resource in more than one role: (the role kept, the roles dropped)."""
    resolved = dict(options)
    taken: dict[str, str] = {}
    conflicts: dict[str, tuple[str, list[str]]] = {}
    for key in PRIORITY:
        kept = []
        for name in options.get(key, []):
            if name in taken:
                conflicts.setdefault(name, (taken[name], []))[1].append(key)
                continue
            taken[name] = key
            kept.append(name)
        if key in options:
            resolved[key] = kept
    return resolved, conflicts
