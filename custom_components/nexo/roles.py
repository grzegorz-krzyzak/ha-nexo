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

from .const import (
    OPT_BLINDS,
    OPT_DIMMERS,
    OPT_EXCLUDED,
    OPT_LIGHTS,
    OPT_OUTPUT_SENSORS,
    OPT_SWITCHES,
)

# "Never offer" keeps a resource from being controlled - reading it stays
# fine: the sleep-mode output, say, excluded and read as a sensor. So a
# conflict is only a controlled role next to any other; of the controlled
# roles, the first listed wins.
CONTROLLED = (OPT_LIGHTS, OPT_DIMMERS, OPT_SWITCHES, OPT_BLINDS)
NOT_CONTROLLED = (OPT_EXCLUDED, OPT_OUTPUT_SENSORS)


def resolve_roles(
    options: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, tuple[str, list[str]]]]:
    """The options with no resource both controlled and in another role, and
    per such resource: (the role kept, the roles dropped)."""
    resolved = dict(options)
    held: dict[str, str] = {}  # resource -> the safer role it already has
    for key in NOT_CONTROLLED:
        for name in options.get(key, []):
            held.setdefault(name, key)
    conflicts: dict[str, tuple[str, list[str]]] = {}
    for key in CONTROLLED:
        kept = []
        for name in options.get(key, []):
            if name in held:
                conflicts.setdefault(name, (held[name], []))[1].append(key)
                continue
            held[name] = key
            kept.append(name)
        if key in options:
            resolved[key] = kept
    return resolved, conflicts
