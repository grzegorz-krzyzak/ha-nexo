"""One connection to the Nexo central unit, shared by every entity."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
import logging
from typing import Any, TypeVar

from homeassistant.core import HomeAssistant

from .nexo_client import ImportTypes, NexoClient, NexoError

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

# Listing replies are not tagged with the index they answer, so a reply that
# arrives late shifts the whole list by one without any error. A listing is
# only trusted once two consecutive reads agree.
_LISTING_ATTEMPTS = 4


class NexoHub:
    """Serialises every call to the blocking client and runs it off the loop.

    The client locks the socket per command, but several operations are more
    than one command - a guarded gate open is a read followed by a trigger -
    and must not have a poll interleaved between them. So every call goes
    through one asyncio lock here.
    """

    def __init__(self, hass: HomeAssistant, host: str, port: int, password: str) -> None:
        self.hass = hass
        self.host = host
        self.port = port
        self._password = password
        self._client: NexoClient | None = None
        self._lock = asyncio.Lock()
        self._resources: dict[ImportTypes, list[str]] = {}

    @property
    def client(self) -> NexoClient:
        if self._client is None:
            raise RuntimeError("hub is not connected")
        return self._client

    async def async_connect(self) -> None:
        """Connect and log in. Raises NexoAuthError or NexoConnectionError."""
        async with self._lock:
            self._client = await self.hass.async_add_executor_job(
                partial(NexoClient, self.host, self._password, port=self.port)
            )

    async def async_disconnect(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self.hass.async_add_executor_job(self._client.disconnect)
                self._client = None

    async def async_call(self, func: Callable[..., _T], *args: Any) -> _T:
        """Run a blocking function under the hub lock.

        func may be a client method or any function that takes the client as
        its first argument, for operations spanning several commands.
        """
        async with self._lock:
            return await self.hass.async_add_executor_job(func, *args)

    async def async_resources(self, resource_type: ImportTypes) -> list[str]:
        """Return the resource names of one type, read once and cached."""
        if resource_type not in self._resources:
            self._resources[resource_type] = await self.async_call(
                _stable_listing, self.client, resource_type
            )
        return self._resources[resource_type]


def _stable_listing(client: NexoClient, resource_type: ImportTypes) -> list[str]:
    previous: list[str] | None = None
    for _ in range(_LISTING_ATTEMPTS):
        current = client.list_resources(resource_type)
        if current == previous:
            return current
        previous = current
    raise NexoError(
        f"Listing {resource_type.name} gave a different answer every time; "
        "the reply queue keeps drifting"
    )
