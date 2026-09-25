"""
nexo_client - a client for the NexoVision protocol (Nexwell Nexo).

Production rewrite based on pyNexo.py from https://github.com/Tymec/NexoAPI
(MIT, Copyright (c) Tymec). The wire protocol is unchanged; error handling,
message framing and the public API have been rewritten.

Verified against a Nexo 5.53 R1PLX1H2 central unit with TUKAN modules, port 1024.

The text command layer (trigger_logic, turn_on, resource_status and friends)
follows the manufacturer's NexoTalk specification:
https://github.com/nexwell-mk/nexo-api - see nexo-talk.md and karta-lan.md.
Note that the numeric layer this client also uses ('system C', behind
get_state/set_state) is left undocumented there and stays reverse-engineered.

Differences from the original, and why:
  1. The central terminates every response with a \\x00 byte. The original
     compared responses directly against plain strings ('LOGIN OK'), so every
     such comparison evaluated to False and failures passed silently.
  2. Framing: messages are read up to the \\x00 terminator instead of assuming
     that a single recv() returns exactly one complete response. Note that
     handshake messages ('Welcome to Nexo!', 'NO uSSL') are NOT terminated -
     only post-login responses are, so the handshake is read unframed.
  3. Consistent Cp1250 encoding both ways (the original sent Cp1250 but
     decoded UTF-8).
  4. Failures raise exceptions instead of being swallowed or returned as None.
  5. Every loop is bounded; no unbounded recursion.
  6. Context manager support with guaranteed disconnect.
  7. list_resources() accepts both ImportTypes.LIGHT and "LIGHT".

Example:
    with NexoClient("192.0.2.10", "1234") as nexo:
        print(nexo.get_state("KITCHEN LIGHT"))
        nexo.trigger_logic("OPEN")
"""

from __future__ import annotations

import logging
import socket
import time
from contextlib import contextmanager
from enum import Enum
from hashlib import md5
from threading import RLock
from typing import Dict, Iterator, List, Optional, Union

__all__ = [
    "NexoClient",
    "ImportTypes",
    "DeviceState",
    "NexoError",
    "NexoConnectionError",
    "NexoAuthError",
    "NexoTimeoutError",
    "NexoProtocolError",
    "NexoCommandError",
    "NexoResourceError",
    "connect",
]

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class NexoError(Exception):
    """Base exception - catch this to catch everything below."""


class NexoConnectionError(NexoError):
    """The TCP connection to the central unit could not be established or kept."""


class NexoAuthError(NexoError):
    """The central unit rejected the password or did not confirm the login."""


class NexoTimeoutError(NexoError):
    """The central unit did not respond within the configured timeout."""


class NexoProtocolError(NexoError):
    """The central unit replied with something unexpected - protocol mismatch."""


class NexoCommandError(NexoError):
    """The central unit rejected a command (e.g. CMD WRONG)."""


class NexoResourceError(NexoError):
    """The named resource does not exist or its state cannot be read."""


# --------------------------------------------------------------------------
# Resource types (as understood by the central unit)
# --------------------------------------------------------------------------

class ImportTypes(Enum):
    """Numeric resource type IDs used by the 'system T' command."""
    SENSOR = 1
    ANALOGSENSOR = 2
    PARTITION = 3
    PARTITION24H = 4
    OUTPUT = 5
    OUTPUT_GROUP = 6
    LIGHT = 7
    DIMMER = 8
    LIGHT_GROUP = 9
    ANALOG_OUTPUT = 10
    ANALOGOUTPUT_GROUP = 11
    RGBW = 12
    RGBW_GROUP = 13
    BLIND = 14
    BLIND_GROUP = 15
    THERMOMETER = 16
    THERMOSTAT = 17
    THERMOSTAT_GROUP = 18
    GATE = 257
    VENTILATOR = 258


class DeviceState(Enum):
    ON = 1
    OFF = 0


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class NexoClient:
    """
    A client for the NexoVision protocol.

    Protocol: raw TCP carrying text messages terminated by a \\x00 byte.
    Commands are prefixed with '@00000000:', responses with '~00000000:'.

    The class is thread-safe (a single lock guards the socket), but one
    connection handles one command at a time - concurrent calls are serialised.
    """

    DEFAULT_PORT = 1024
    COMMAND_PREFIX = b"@00000000:"
    RESPONSE_PREFIX = "~00000000:"
    TERMINATOR = b"\x00"
    ENCODING = "Cp1250"
    BUFFER_SIZE = 16384

    # Hard limits - guard against spinning forever on a misbehaving central unit
    MAX_QUEUE_DRAIN = 200
    MAX_RESOURCES_PER_TYPE = 500
    MAX_FRAME_BYTES = 1_048_576

    # From the manufacturer's NexoTalk spec
    MAX_COMMAND_DATA = 240
    MAX_LOGIC_COMMAND = 7

    # How long to keep polling 'get' for a reply, and how often
    REPLY_POLL_INTERVAL = 0.05
    QUERY_REPLY_TIMEOUT = 2.0
    CONTROL_REPLY_TIMEOUT = 0.5

    # How many times to re-ask for one list entry whose answer went astray
    LISTING_ATTEMPTS = 3

    def __init__(
        self,
        host: str,
        password: str,
        port: int = DEFAULT_PORT,
        timeout: float = 5.0,
        connect_timeout: float = 5.0,
        retries: int = 2,
        use_ssl: bool = False,
        auto_connect: bool = True,
    ) -> None:
        """
        :param host: IP address of the central unit
        :param password: central unit password/PIN (same as used by NexoVision)
        :param port: NexoVision service port (1024; port 1025 serves the remote
                     panel, a different binary protocol this client cannot speak)
        :param timeout: how long to wait for a response to a command [s]
        :param connect_timeout: how long to wait for the TCP connection [s]
        :param retries: how many times to retry a command after a connection error
        :param use_ssl: negotiate uSSL instead of a plaintext connection
        :param auto_connect: connect immediately in the constructor
        """
        if not host:
            raise ValueError("host must not be empty")
        if retries < 0:
            raise ValueError("retries must not be negative")

        self.host = host
        self.port = port
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.retries = retries
        self.use_ssl = use_ssl

        self._password = password
        self._sock: Optional[socket.socket] = None
        self._buffer = bytearray()
        self._lock = RLock()
        self._authenticated = False

        if auto_connect:
            self.connect()

    # ---------------------------------------------------------------- lifecycle

    def __enter__(self) -> "NexoClient":
        if not self.is_connected:
            self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        state = "connected" if self.is_connected else "disconnected"
        return f"<NexoClient {self.host}:{self.port} {state}>"

    @property
    def is_connected(self) -> bool:
        return self._sock is not None and self._authenticated

    def connect(self) -> None:
        """Open the connection, negotiate encryption and authenticate."""
        with self._lock:
            self._close_socket()
            self._buffer.clear()
            self._authenticated = False

            try:
                self._sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout
                )
                self._sock.settimeout(self.timeout)
                self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError as exc:
                self._sock = None
                raise NexoConnectionError(
                    f"Cannot connect to {self.host}:{self.port} ({exc})"
                ) from exc

            greeting = self._read_frame(framed=False)
            log.debug("Greeting from central unit: %r", greeting)
            if "Nexo" not in greeting:
                self._close_socket()
                raise NexoProtocolError(
                    f"Unexpected greeting from {self.host}:{self.port}: {greeting!r}. "
                    f"Is this the NexoVision service port (usually 1024)?"
                )

            self._negotiate_encryption()
            self._authenticate()
            self._drain_queue()
            log.info("Connected to Nexo central unit at %s:%s", self.host, self.port)

    def disconnect(self) -> None:
        """Close the connection. Safe to call repeatedly."""
        with self._lock:
            self._close_socket()
            self._authenticated = False
            self._buffer.clear()
            log.debug("Disconnected from %s:%s", self.host, self.port)

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None

    # ---------------------------------------------------------------- handshake

    def _negotiate_encryption(self) -> None:
        self._send_raw(b"uSSL\n" if self.use_ssl else b"plain\n")
        response = self._read_frame(framed=False)
        if response.startswith("uSSL OK"):
            self.use_ssl = True
            log.debug("Negotiated: uSSL")
        elif response.startswith("NO uSSL"):
            self.use_ssl = False
            log.debug("Negotiated: plaintext connection")
        else:
            self._close_socket()
            raise NexoProtocolError(
                f"Unexpected response to encryption negotiation: {response!r}"
            )

    def _authenticate(self) -> None:
        try:
            password_bytes = self._password.encode("ISO-8859-1")
        except UnicodeEncodeError as exc:
            raise NexoAuthError(
                "Password contains characters not representable in ISO-8859-1"
            ) from exc

        payload = bytearray(md5(password_bytes).digest())
        payload.append(0)   # terminator
        payload.append(10)  # newline the central unit expects

        self._send_raw(bytes(payload))
        response = self._read_frame(framed=False)

        if response.startswith("LOGIN OK"):
            self._authenticated = True
            log.debug("Login accepted")
        elif response.startswith("LOGIN FAILED"):
            self._close_socket()
            raise NexoAuthError("Central unit rejected the password (LOGIN FAILED)")
        else:
            self._close_socket()
            raise NexoProtocolError(f"Unexpected response to login: {response!r}")

    def _drain_queue(self) -> None:
        """Flush the central unit's pending event queue (bounded attempts)."""
        for _ in range(self.MAX_QUEUE_DRAIN):
            if self._command("get") == self.RESPONSE_PREFIX:
                return
        raise NexoProtocolError(
            f"Event queue did not drain after {self.MAX_QUEUE_DRAIN} attempts"
        )

    # ---------------------------------------------------------------- transport

    def _send_raw(self, payload: bytes) -> None:
        if self._sock is None:
            raise NexoConnectionError("No active connection")
        try:
            self._sock.sendall(payload)
        except socket.timeout as exc:
            raise NexoTimeoutError(f"Timed out sending to {self.host}") from exc
        except OSError as exc:
            self._close_socket()
            raise NexoConnectionError(f"Send failed to {self.host}: {exc}") from exc

    def _read_frame(self, framed: bool = True) -> str:
        """
        Read one message from the central unit.

        :param framed: when True, read up to and including the \\x00 terminator.
            The buffer is shared between calls, so coalesced responses (two in
            one packet) and split responses (one across two packets) are both
            handled.

            When False, return whatever arrives in a single read. The central
            unit does NOT terminate its handshake messages ('Welcome to Nexo!',
            'NO uSSL') with \\x00 - only post-login responses are framed. Waiting
            for a terminator during the handshake would block until timeout.
        """
        if self._sock is None:
            raise NexoConnectionError("No active connection")

        while self.TERMINATOR not in self._buffer:
            if not framed and self._buffer:
                break  # unframed handshake message - take what arrived
            if len(self._buffer) > self.MAX_FRAME_BYTES:
                self._close_socket()
                raise NexoProtocolError(
                    "Response exceeded the size limit without a terminator"
                )
            try:
                chunk = self._sock.recv(self.BUFFER_SIZE)
            except socket.timeout as exc:
                raise NexoTimeoutError(
                    f"Central unit {self.host} did not respond within {self.timeout}s"
                ) from exc
            except OSError as exc:
                self._close_socket()
                raise NexoConnectionError(
                    f"Read failed from {self.host}: {exc}"
                ) from exc

            if not chunk:
                self._close_socket()
                raise NexoConnectionError(
                    f"Central unit {self.host} closed the connection. "
                    f"Is another application (e.g. NexoVision) holding the session?"
                )
            self._buffer.extend(chunk)

        if self.TERMINATOR in self._buffer:
            raw, _, rest = bytes(self._buffer).partition(self.TERMINATOR)
            self._buffer = bytearray(rest)
        else:
            raw = bytes(self._buffer)
            self._buffer.clear()
        return raw.decode(self.ENCODING, errors="replace").strip()

    def _command(self, command: str) -> str:
        """Send a prefixed command and return the raw response (no retrying)."""
        payload = self.COMMAND_PREFIX + command.encode(self.ENCODING) + self.TERMINATOR
        self._send_raw(payload)
        return self._read_frame()

    def _command_retrying(self, command: str, log_as: Optional[str] = None) -> str:
        """
        Like _command, but reconnects and retries on connection failures.

        log_as stands in for the command in log lines and error messages, so
        commands carrying a user password never reach either.
        """
        label = command if log_as is None else log_as
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                with self._lock:
                    if self._sock is None:
                        self.connect()
                    return self._command(command)
            except (NexoConnectionError, NexoTimeoutError) as exc:
                last = exc
                if attempt < self.retries:
                    log.warning(
                        "Command %r failed (%s) - retrying (%d/%d)",
                        label, exc, attempt + 1, self.retries,
                    )
                    self._close_socket()
                    time.sleep(0.5 * (attempt + 1))
        raise NexoConnectionError(
            f"Command {label!r} failed after {self.retries + 1} attempts"
        ) from last

    # ---------------------------------------------------------------- public API

    def ping(self) -> bool:
        """Check whether the central unit responds. Never raises - returns a bool."""
        try:
            return self._command_retrying("ping") == f"{self.RESPONSE_PREFIX}pong"
        except NexoError:
            return False

    def get_state(self, name: str) -> int:
        """
        Return the current state of the named resource.

        :raises NexoResourceError: if the resource is unknown or the state is unreadable
        :raises NexoProtocolError: if the reply belongs to an earlier command
        """
        if not name:
            raise ValueError("resource name must not be empty")

        response = self._system_c(name, "?", reply_timeout=self.QUERY_REPLY_TIMEOUT)
        body = self._strip_prefix(response)

        if not body:
            raise NexoTimeoutError(
                f"Central unit did not answer the query for {name!r} within "
                f"{self.QUERY_REPLY_TIMEOUT}s - it may be busy running a sequence"
            )

        # The central unit echoes the resource name back. A reply naming
        # something else is an earlier command's answer arriving late, which
        # means every later read would be off by one - resynchronise and say so
        # rather than hand back another resource's state.
        if not body.startswith(name):
            self._drain_queue()
            raise NexoProtocolError(
                f"Asked for {name!r} but the card replied {body!r}. The reply "
                f"queue was out of step and has been resynchronised."
            )

        value = body.rsplit(" ", 1)[-1]
        try:
            return int(value)
        except ValueError as exc:
            raise NexoResourceError(
                f"Cannot read the state of {name!r} - central unit returned "
                f"{response!r}. Check that the resource name matches the system exactly."
            ) from exc

    def set_state(self, name: str, state: Union[int, bool, DeviceState]) -> int:
        """
        Set the state of a resource and return the state read back afterwards.

        :raises NexoResourceError: if the change could not be confirmed
        """
        if isinstance(state, DeviceState):
            value = state.value
        elif isinstance(state, bool):
            value = int(state)
        elif state in (0, 1):
            value = int(state)
        else:
            raise ValueError(
                f"state must be 0, 1, a bool or DeviceState, got {state!r}"
            )

        self._system_c(name, str(value))
        return self.get_state(name)

    def pulse(self, name: str, seconds: float = 1.0) -> None:
        """
        Momentary pulse: switch the resource on, wait, switch it off.
        Mirrors the gate-control sequence defined inside the Nexo system.

        The resource is switched off even if an error occurs mid-pulse.
        """
        if seconds <= 0:
            raise ValueError("seconds must be positive")

        log.info("Pulsing %r for %.2fs", name, seconds)
        self.set_state(name, 1)
        try:
            time.sleep(seconds)
        finally:
            try:
                self.set_state(name, 0)
            except NexoError:
                log.error(
                    "CRITICAL: failed to switch %r back off after the pulse - "
                    "the output may still be energised!", name,
                )
                raise

    def _system_c(self, name: str, argument: str, reply_timeout: float = 0.0) -> str:
        """The 'system C' command - read or write a single resource's state."""
        if "'" in name:
            raise ValueError("resource name must not contain an apostrophe")

        ack = self._command_retrying(f"system C '{name}' {argument}")
        if ack != "CMD OK":
            raise NexoCommandError(
                f"Central unit rejected the command for {name!r}: {ack!r}"
            )
        return self._await_reply(reply_timeout)

    def _strip_prefix(self, frame: str) -> str:
        if frame.startswith(self.RESPONSE_PREFIX):
            frame = frame[len(self.RESPONSE_PREFIX):]
        return frame.strip()

    def _await_reply(self, timeout: float) -> str:
        """
        Poll 'get' until the central unit has put its answer in the card's
        buffer, or the timeout runs out.

        The card acknowledges a command straight away, but the central unit
        needs a few to a few dozen milliseconds to answer - much longer while
        it is busy, for instance running a gate sequence. Re-sending the
        command instead of polling would queue a second answer and leave every
        later read reading the previous one.

        A timeout of 0 reads once, for commands whose answer is not needed.
        """
        attempts = max(1, int(timeout / self.REPLY_POLL_INTERVAL))
        for remaining in range(attempts, 0, -1):
            reply = self._command_retrying("get")
            if reply != self.RESPONSE_PREFIX:
                return reply
            if remaining > 1:
                time.sleep(self.REPLY_POLL_INTERVAL)
        return reply

    # ------------------------------------------------------- text commands

    def trigger_logic(self, command: str) -> str:
        """
        Send an external text command that Nexo logic can match with an
        external command condition, running a sequence defined in the
        system itself instead of driving resources from here.

        Returns whatever the logic sends back to the card, empty if nothing.
        """
        if not command:
            raise ValueError("logic command must not be empty")
        if len(command) > self.MAX_LOGIC_COMMAND:
            raise ValueError(
                f"logic command must be at most {self.MAX_LOGIC_COMMAND} "
                f"characters, got {len(command)}"
            )
        self._reject_control_characters(command, "logic command")
        return self._system("logic", command, reply_timeout=self.CONTROL_REPLY_TIMEOUT)

    def turn_on(self, name: str) -> None:
        """Switch on a relay, OC or lighting output."""
        self._control(f"wlacz {self._quote(name)}")

    def turn_off(self, name: str) -> None:
        """Switch off a relay, OC or lighting output."""
        self._control(f"wylacz {self._quote(name)}")

    def blind_up(self, name: str) -> None:
        self._control(f"podnies {self._quote(name)}")

    def blind_down(self, name: str) -> None:
        self._control(f"opusc {self._quote(name)}")

    def open_door(self) -> None:
        """Release the videodoorphone door strike."""
        self._control("otworz")

    def set_thermostat(self, temperature: int, name: str) -> None:
        """Set a thermostat's threshold, in whole degrees."""
        if isinstance(temperature, bool) or not isinstance(temperature, int):
            raise ValueError(f"temperature must be an integer, got {temperature!r}")
        self._control(f"ustaw {temperature:+d} {self._quote(name)}")

    def arm(self, password: str, partition: str) -> None:
        """Arm a partition with a user password, which is kept out of logs."""
        self._control(
            f"uzbroj {self._credential(password)} {self._quote(partition)}",
            shown_as=f"uzbroj <password> {self._quote(partition)}",
        )

    def disarm(self, password: str, partition: str) -> None:
        """Disarm a partition with a user password, which is kept out of logs."""
        self._control(
            f"rozbroj {self._credential(password)} {self._quote(partition)}",
            shown_as=f"rozbroj <password> {self._quote(partition)}",
        )

    def resource_status(self, name: str) -> str:
        """Ask the system for a resource's state as free-form text."""
        return self._system(
            "command",
            f"stan {self._quote(name)}",
            reply_timeout=self.QUERY_REPLY_TIMEOUT,
        )

    def system_info(self) -> str:
        """Firmware version and uptime, as the central unit reports them."""
        return self._system("command", "system", reply_timeout=self.QUERY_REPLY_TIMEOUT)

    # ------------------------------------------------- text command plumbing

    @staticmethod
    def _reject_control_characters(value: str, label: str) -> None:
        if any(char in value for char in "\x00\r\n"):
            raise ValueError(f"{label} must not contain control characters")

    @classmethod
    def _quote(cls, name: str) -> str:
        """Names containing a space are passed in apostrophes."""
        if not name:
            raise ValueError("resource name must not be empty")
        if "'" in name:
            raise ValueError("resource name must not contain an apostrophe")
        cls._reject_control_characters(name, "resource name")
        return f"'{name}'" if " " in name else name

    @classmethod
    def _credential(cls, password: str) -> str:
        if not password:
            raise ValueError("password must not be empty")
        if " " in password:
            raise ValueError("password must not contain a space")
        cls._reject_control_characters(password, "password")
        return password

    def _control(self, payload: str, shown_as: Optional[str] = None) -> None:
        """
        Run a control command. The central unit stays silent when it works and
        describes the failure in the reply the following 'get' picks up.
        """
        failure = self._system(
            "command",
            payload,
            shown_as=shown_as,
            reply_timeout=self.CONTROL_REPLY_TIMEOUT,
        )
        if failure:
            raise NexoCommandError(
                f"Central unit refused {shown_as or payload!r}: {failure}"
            )

    def _system(
        self,
        subcommand: str,
        argument: str = "",
        shown_as: Optional[str] = None,
        reply_timeout: float = 0.0,
    ) -> str:
        """Send 'system <subcommand> <argument>' and return the reply payload."""
        data = f"system {subcommand} {argument}".rstrip()
        if len(data) > self.MAX_COMMAND_DATA:
            raise ValueError(
                f"command data is {len(data)} characters, the card accepts at "
                f"most {self.MAX_COMMAND_DATA}"
            )

        safe = None if shown_as is None else f"system {subcommand} {shown_as}"
        ack = self._command_retrying(data, log_as=safe)
        if ack != "CMD OK":
            raise NexoCommandError(f"Card rejected {safe or data!r}: {ack!r}")

        return self._strip_prefix(self._await_reply(reply_timeout))

    # ---------------------------------------------------------------- resources

    def list_resources(self, resource_type: Union[ImportTypes, str]) -> List[str]:
        """
        Return the names of every resource of the given type.

        Accepts either ImportTypes.LIGHT or "LIGHT".
        """
        rtype = self._coerce_type(resource_type)
        names: List[str] = []

        for index in range(self.MAX_RESOURCES_PER_TYPE):
            name = self._list_entry(rtype, index)
            if name is None:
                break  # end of the list for this type
            names.append(name)
        else:
            log.warning(
                "Hit the %d-resource limit for type %s - the list may be truncated",
                self.MAX_RESOURCES_PER_TYPE, rtype.name,
            )

        log.debug("Type %s: %d resources", rtype.name, len(names))
        return names

    def _list_entry(self, rtype: ImportTypes, index: int) -> Optional[str]:
        """
        Return the name at one index of a type's list, or None past its end.

        The central unit echoes type and index back ('~T 1 0 PIR HALL', and a
        bare '~T 1 28' past the end), so an answer to another query can be
        told apart. Taking the first reply on trust used to truncate lists
        (an answer not there yet read as the end) or shift them by one (a late
        answer read as the next entry), without any error.
        """
        header = f"~T {rtype.value} {index}"
        for _ in range(self.LISTING_ATTEMPTS):
            ack = self._command_retrying(f"system T {rtype.value} {index} ?")
            if ack != "CMD OK":
                raise NexoCommandError(
                    f"Central unit rejected listing {rtype.name} at index {index}: {ack!r}"
                )

            reply = self._strip_prefix(self._await_reply(self.QUERY_REPLY_TIMEOUT))
            if reply == header:
                return None
            if reply.startswith(header + " "):
                # THERMOSTAT entries carry the linked thermometer and the
                # allowed temperature range on further lines.
                lines = reply[len(header) + 1:].splitlines()
                return lines[0].strip() if lines else ""

            log.debug(
                "Listing %s at index %d got %r - resynchronising and asking again",
                rtype.name, index, reply,
            )
            self._drain_queue()

        raise NexoProtocolError(
            f"No answer for {rtype.name} entry {index} after "
            f"{self.LISTING_ATTEMPTS} attempts"
        )

    def list_all_resources(
        self, skip_errors: bool = True
    ) -> Dict[ImportTypes, List[str]]:
        """
        Return every resource on the central unit, grouped by type.

        :param skip_errors: when True, a type that cannot be read is skipped
                            with a warning instead of aborting the whole scan
        """
        result: Dict[ImportTypes, List[str]] = {}
        for rtype in ImportTypes:
            try:
                names = self.list_resources(rtype)
            except NexoError as exc:
                if not skip_errors:
                    raise
                log.warning("Skipped type %s: %s", rtype.name, exc)
                continue
            if names:
                result[rtype] = names
        return result

    def find_resource(self, fragment: str) -> Dict[ImportTypes, List[str]]:
        """Find resources whose name contains the given fragment (case-insensitive)."""
        needle = fragment.casefold()
        matches: Dict[ImportTypes, List[str]] = {}
        for rtype, names in self.list_all_resources().items():
            hits = [n for n in names if needle in n.casefold()]
            if hits:
                matches[rtype] = hits
        return matches

    @staticmethod
    def _coerce_type(resource_type: Union[ImportTypes, str]) -> ImportTypes:
        if isinstance(resource_type, ImportTypes):
            return resource_type
        try:
            return ImportTypes[str(resource_type).upper()]
        except KeyError as exc:
            valid = ", ".join(t.name for t in ImportTypes)
            raise NexoResourceError(
                f"Unknown resource type {resource_type!r}. Valid types: {valid}"
            ) from exc


@contextmanager
def connect(host: str, password: str, **kwargs) -> Iterator[NexoClient]:
    """Shorthand: with connect(ip, password) as nexo: ..."""
    client = NexoClient(host, password, **kwargs)
    try:
        yield client
    finally:
        client.disconnect()
