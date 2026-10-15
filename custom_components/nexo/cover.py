"""Gates and doors driven by Nexo logic commands, and blind outputs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .const import (
    BLIND_DEFAULT_CLASS,
    BOOST_BLIND,
    BOOST_GATE_DEFAULT,
    CONFIRM_MOVING,
    CONFIRM_POLL,
    CONFIRM_START,
    COVER_CLOSE_COMMAND,
    COVER_DEVICE_CLASS,
    COVER_OPEN_COMMAND,
    COVER_OPEN_ONLY_WHEN_CLOSED,
    COVER_REED_SENSOR,
    COVER_RUN_SENSOR,
    COVER_TRAVEL_TIME,
    ITEM_ID,
    ITEM_NAME,
    OPT_BLIND_CLASSES,
    OPT_BLINDS,
    OPT_COVERS,
    SENSOR_INTACT,
    SENSOR_VIOLATED,
)
from .coordinator import NexoCoordinator
from .entity import NexoEntity, NexoResourceEntity
from .motion import Motion, step
from .nexo_client import NexoClient, NexoError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NexoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        NexoLogicCover(data.coordinator, item, data.motions.get(item[ITEM_ID]))
        for item in entry.options.get(OPT_COVERS, [])
    )
    classes = entry.options.get(OPT_BLIND_CLASSES, {})
    async_add_entities(
        NexoBlind(data.coordinator, name, classes.get(name, BLIND_DEFAULT_CLASS))
        for name in data.options.get(OPT_BLINDS, [])
    )


class _NotClosed(Exception):
    """The guard found the gate not confirmed closed and did not open it."""

    def __init__(self, state: int) -> None:
        super().__init__(state)
        self.state = state


def _send(client: NexoClient, command: str, reed_sensor: str | None) -> None:
    """Send an open or close command; with reed_sensor, only if it reads closed.

    Read immediately before the command, never from the last poll: the gate
    has other triggers (remotes, wall switches, intercoms) that may have moved
    it since.
    """
    if reed_sensor:
        state = client.get_state(reed_sensor)
        if state != SENSOR_INTACT:
            raise _NotClosed(state)
    client.trigger_logic(command)


def _read_all(client: NexoClient, names: list[str]) -> dict[str, int | None]:
    """Each resource's state, None where it cannot be read."""
    states: dict[str, int | None] = {}
    for name in names:
        try:
            states[name] = client.get_state(name)
        except NexoError:
            states[name] = None
    return states


class NexoLogicCover(NexoEntity, CoverEntity):
    """A gate or door with a reed switch, opened and closed by logic commands.

    The reed switch only tells closed from not closed, so there is no
    position, and no is_opening / is_closing either - guessing them from a
    timer drifts from reality every time the gate stops halfway. The one
    exception is a run signal from the drive itself (an input on while the
    drive runs): then the gate shows opening and closing, with the direction
    taken from the reed switch as the drive starts.

    A logic rule that refuses to run does so silently, and a mistyped command
    is acknowledged exactly like a real one. Nothing the central unit returns
    confirms that the gate moved; only the reed switch changing does.
    """

    _group = "logic"
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(
        self,
        coordinator: NexoCoordinator,
        item: dict[str, Any],
        motion: Motion | None,
    ) -> None:
        super().__init__(coordinator, f"cover_{item[ITEM_ID]}", item[ITEM_NAME])
        self._item = item
        self._motion = motion
        self._open_command: str = item[COVER_OPEN_COMMAND]
        self._close_command: str = item[COVER_CLOSE_COMMAND]
        # Optional: without a reed switch the state is simply unknown.
        self._reed_sensor: str | None = item.get(COVER_REED_SENSOR)
        self._guarded: bool = item[COVER_OPEN_ONLY_WHEN_CLOSED] and bool(self._reed_sensor)
        self._run_sensor: str | None = item.get(COVER_RUN_SENSOR)
        # Direction of the current run, fixed when the drive starts; None
        # while the drive is still or the start was not seen (after a restart)
        self._direction: str | None = None
        self._last_run: int | None = None
        self._last_reed: int | None = None
        self._attr_device_class = CoverDeviceClass(item[COVER_DEVICE_CLASS])
        # Without the guard, an open command sent mid-travel is how the door
        # is stopped part-way, so both buttons must stay usable in every state.
        # Without a reed switch there is no state to disable either by.
        self._attr_assumed_state = not self._guarded

    @property
    def is_closed(self) -> bool | None:
        if self._reed_sensor is None:
            return None
        state = (self.coordinator.data or {}).get(self._reed_sensor)
        if state == SENSOR_INTACT:
            return True
        if state == SENSOR_VIOLATED:
            return False
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The reads before the first update: a start is the change from them
        self._track_run()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track_run()
        super()._handle_coordinator_update()

    def _track_run(self) -> None:
        """Remember the direction of a run when the drive starts.

        On opening the reed switch may leave closed before the run signal
        comes on (1.2 s measured on the entry gate), so the direction comes
        from the reed switch as read before the start, not with it. Mid-way
        an opening gate also reads not closed, so the direction is kept for
        the whole run rather than worked out again at each read.
        """
        if self._run_sensor is None:
            return
        data = self.coordinator.data or {}
        run = data.get(self._run_sensor)
        reed = data.get(self._reed_sensor) if self._reed_sensor else None
        if run != SENSOR_VIOLATED:
            self._direction = None
        elif self._last_run == SENSOR_INTACT:
            self._direction = "opening" if self._last_reed == SENSOR_INTACT else "closing"
        elif (
            self._direction == "opening"
            and self._last_reed == SENSOR_VIOLATED
            and reed == SENSOR_INTACT
        ):
            # Reached closed while taken for opening: a stop between two
            # runs fell between reads, and this run was a close
            self._direction = "closing"
        self._last_run, self._last_reed = run, reed

    def _run_direction(self) -> str | None:
        """opening / closing while the run signal reads running, else None."""
        if self._run_sensor is None:
            return None
        data = self.coordinator.data or {}
        if data.get(self._run_sensor) != SENSOR_VIOLATED:
            return None
        if self._direction is not None:
            return self._direction
        # Start not seen (a restart mid-run): a gate still reading closed has
        # just left; otherwise closing is the likelier guess
        if self._reed_sensor and data.get(self._reed_sensor) == SENSOR_INTACT:
            return "opening"
        return "closing"

    @property
    def is_opening(self) -> bool | None:
        if self._run_sensor is None:
            return None
        return self._run_direction() == "opening"

    @property
    def is_closing(self) -> bool | None:
        if self._run_sensor is None:
            return None
        return self._run_direction() == "closing"

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_command(self._open_command, opening=True)
        if self._motion:
            self._motion.record("up", time.monotonic(), self.is_closed is True)
        await async_boost_reed(self.coordinator, self._item)

    async def async_close_cover(self, **kwargs: Any) -> None:
        # Unconditional: closing an already closed gate is harmless, and it
        # works even when the reed switch cannot be read - the safe recovery
        # action for a gate in an unknown state.
        await self._async_command(self._close_command, opening=False)
        if self._motion:
            self._motion.record("down", time.monotonic())
        await async_boost_reed(self.coordinator, self._item)

    async def _async_command(self, command: str, opening: bool) -> None:
        """Send the command; with a run signal, confirm that the drive obeyed.

        The central unit acknowledges a logic command the same way whether a
        rule carried it out or not, and never says why not. With a run signal
        Home Assistant sees the result instead: the drive starts, or it does
        not - and then the error says what the gate was doing, read before
        the command. The rules' conditions stay in the central unit only.
        """
        verb = "opened" if opening else "closed"
        hub = self.coordinator.hub
        watched = [n for n in (self._run_sensor, self._reed_sensor) if n]
        before: dict[str, int | None] = {}
        try:
            if self._run_sensor:
                before = await hub.async_call(_read_all, hub.client, watched)
            await hub.async_call(
                _send,
                hub.client,
                command,
                self._reed_sensor if opening and self._guarded else None,
            )
        except _NotClosed as err:
            # Refusing silently would be indistinguishable from a failure,
            # especially from a voice assistant.
            raise HomeAssistantError(
                f"{self.name} was not opened: it is not confirmed closed "
                f"({self._reed_sensor} reads {err.state})"
            ) from err
        except NexoError as err:
            raise HomeAssistantError(f"{self.name} was not {verb}: {err}") from err
        if self._run_sensor:
            await self._async_confirm(command, opening, verb, before)

    async def _async_confirm(
        self, command: str, opening: bool, verb: str, before: dict[str, int | None]
    ) -> None:
        run_sensor = self._run_sensor
        assert run_sensor is not None
        run_before = before.get(run_sensor)
        reed_before = before.get(self._reed_sensor) if self._reed_sensor else None
        if run_before is None:
            _LOGGER.debug("%s unreadable before %s; not confirmed", run_sensor, command)
            return
        moving = run_before == SENSOR_VIOLATED
        if not opening and not moving and reed_before == SENSOR_INTACT:
            return  # already closed: the drive has nothing to do
        hub = self.coordinator.hub
        wait = CONFIRM_MOVING if moving else CONFIRM_START
        deadline = time.monotonic() + wait
        # Opening from closed, the reed switch leaving closed counts as the start
        # too: it moves before the run signal comes on (1.2 s on the entry gate).
        # Closing has nothing like it - the reed switch reads closed only at the end.
        reed_counts = opening and not moving and reed_before == SENSOR_INTACT
        watched = [run_sensor] + ([self._reed_sensor] if reed_counts else [])
        while time.monotonic() < deadline:
            await asyncio.sleep(CONFIRM_POLL)
            states = await hub.async_call(_read_all, hub.client, watched)
            run = states.get(run_sensor)
            if not moving and run == SENSOR_VIOLATED:
                return  # started
            if reed_counts and states.get(self._reed_sensor) == SENSOR_VIOLATED:
                return  # left closed: started
            if run is None:
                continue
            if moving and run == SENSOR_INTACT:
                raise HomeAssistantError(
                    f"{self.name} stopped right after {command}: it was moving "
                    f"({run_sensor} read {SENSOR_VIOLATED}), and a command during travel "
                    "stops this drive - most likely it is now halfway. The central "
                    f"unit's rule for {command} needs \"{run_sensor} at rest\" as a condition"
                )
        if moving:
            raise HomeAssistantError(
                f"{self.name} was not {verb}: it is moving ({run_sensor} reads "
                f"{SENSOR_VIOLATED}); the central unit did not carry out {command}"
            )
        if opening and reed_before is not None and reed_before != SENSOR_INTACT:
            raise HomeAssistantError(
                f"{self.name} was not opened: it is not closed ({self._reed_sensor} "
                f"reads {reed_before}); the central unit did not carry out {command}"
            )
        raise HomeAssistantError(
            f"{self.name} was not {verb}: the drive did not start within {wait:g} s - "
            f"the central unit did not carry out {command}. Check the rule's spelling "
            "and conditions in the Nexo configurator"
        )

    async def async_toggle(self, **kwargs: Any) -> None:
        # With a travel time set, toggle behaves like the remote: up, stop,
        # down, stop. Home Assistant's own toggle always closes a gate that
        # is not closed, so after stopping it on the way down no press could
        # send it back up.
        if self._motion is None:
            await super().async_toggle(**kwargs)
            return
        await async_step(self, self._item, self._motion)


async def async_step(entity: NexoEntity, item: dict[str, Any], motion: Motion) -> None:
    """Run one remote-style step of the gate described by item."""
    hub = entity.coordinator.hub
    try:
        await hub.async_call(
            step,
            hub.client,
            motion,
            item.get(COVER_REED_SENSOR),
            item[COVER_OPEN_COMMAND],
            item[COVER_CLOSE_COMMAND],
        )
    except NexoError as err:
        raise HomeAssistantError(f"{entity.name}: command not sent: {err}") from err
    await async_boost_reed(entity.coordinator, item)


async def async_boost_reed(coordinator: NexoCoordinator, item: dict[str, Any]) -> None:
    """After a command, watch the gate's reed switch and run signal closely
    for its travel."""
    watched = [item[key] for key in (COVER_REED_SENSOR, COVER_RUN_SENSOR) if item.get(key)]
    if watched:
        seconds = item.get(COVER_TRAVEL_TIME) or BOOST_GATE_DEFAULT
        await coordinator.async_boost(watched, seconds)
    else:
        await coordinator.async_request_refresh()


class NexoBlind(NexoResourceEntity, CoverEntity):
    """A blind output of a roller-shutter module: raise, lower, stop.

    The state word shows the relay, not the blind: raising or lowering while
    the module holds the relay for the time set in the output, then stopped.
    For a blind whose time covers its travel that is the motion; for a device
    started by an impulse (a gate drive) it lasts only the impulse - Nexo has
    nothing more to tell. No position, so both directions always stay usable.
    """

    _group = "blinds"
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    _attr_assumed_state = True

    def __init__(self, coordinator: NexoCoordinator, name: str, device_class: str) -> None:
        super().__init__(coordinator, "blind", name)
        self._attr_device_class = CoverDeviceClass(device_class)

    @property
    def is_closed(self) -> bool | None:
        return None

    @property
    def is_opening(self) -> bool:
        return self.raw_state == NexoClient.BLIND_RAISING

    @property
    def is_closing(self) -> bool:
        return self.raw_state == NexoClient.BLIND_LOWERING

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_move(NexoClient.BLIND_RAISING)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_move(NexoClient.BLIND_LOWERING)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._async_move(NexoClient.BLIND_STOPPED)

    async def _async_move(self, value: int) -> None:
        hub = self.coordinator.hub
        try:
            await hub.async_call(hub.client.move_blind, self.resource, value)
        except NexoError as err:
            raise HomeAssistantError(
                f"{self.name}: the central unit did not accept the command: {err}"
            ) from err
        finally:
            # Read back even after an error: a command may have worked
            # although its reply was lost.
            await self.coordinator.async_boost([self.resource], BOOST_BLIND)
