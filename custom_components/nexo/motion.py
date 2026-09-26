"""Remote-style step control for a gate driven by separate up and down commands.

A remote cycles up -> stop -> down -> stop -> up. With separate up and down
inputs that means: a command against the current motion stops the drive, and
the next press moves the other way. Neither a plain toggle (always "close"
once not closed) nor an alternating one reproduces it, so the direction and
start time of the last movement are remembered here.

Nothing reports whether the gate is still moving, so a movement is assumed
over once the configured travel time has passed. The reed switch reading
closed resets the cycle: the next press always opens.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Literal

from .const import SENSOR_INTACT
from .nexo_client import NexoClient

Direction = Literal["up", "down"]

# After an up command the reed switch keeps reading closed until the gate
# has left the closed position - about 2.6 s on the tested garage door.
# Within this window a press stops the gate instead of starting it again.
LEAVING_CLOSED_WINDOW = 5.0


@dataclass
class Motion:
    """What the gate was last told to do."""

    travel_time: float
    direction: Direction | None = None
    started: float | None = None
    stopped: bool = False

    def moving(self, now: float) -> bool:
        return (
            self.direction is not None
            and self.started is not None
            and not self.stopped
            and now - self.started < self.travel_time
        )

    def record(self, direction: Direction, now: float) -> None:
        """A command was sent: either it stops the current motion, or it
        starts a new one."""
        if self.moving(now) and direction != self.direction:
            self.stopped = True
        else:
            self.direction = direction
            self.started = now
            self.stopped = False

    def next_step(self, reed_state: int | None, now: float) -> Direction:
        """The command a remote's next press would send."""
        just_opened = (
            self.direction == "up"
            and self.started is not None
            and now - self.started < LEAVING_CLOSED_WINDOW
            and not self.stopped
        )
        if reed_state == SENSOR_INTACT and not just_opened:
            return "up"
        if self.moving(now):
            return "down" if self.direction == "up" else "up"  # stop
        if self.direction is None:
            # Unknown history and not closed: most likely open, so close.
            return "down"
        return "down" if self.direction == "up" else "up"


def step(
    client: NexoClient,
    motion: Motion,
    reed_sensor: str | None,
    open_command: str,
    close_command: str,
) -> Direction:
    """Read the reed switch, send the next command in the cycle, record it."""
    reed_state = client.get_state(reed_sensor) if reed_sensor else None
    now = monotonic()
    direction = motion.next_step(reed_state, now)
    client.trigger_logic(open_command if direction == "up" else close_command)
    motion.record(direction, now)
    return direction
