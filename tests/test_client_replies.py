"""How many times the client polls for a reply after each kind of command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.nexo.nexo_client import NexoClient, NexoCommandError

EMPTY = NexoClient.RESPONSE_PREFIX


class Card:
    """Acknowledges every command; 'get' hands out queued replies, then empty ones."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = list(replies or [])
        self.gets = 0

    def __call__(self, data: str, log_as: str | None = None) -> str:
        if data != "get":
            return "CMD OK"
        self.gets += 1
        return self.replies.pop(0) if self.replies else EMPTY


@pytest.fixture
def client():
    with patch("custom_components.nexo.nexo_client.time.sleep"):
        yield NexoClient("192.0.2.1", "pw", auto_connect=False)


def test_poll_counts():
    """Do not lower CONTROL_REPLY_POLLS below 3: refusals came by the 2nd poll."""
    assert NexoClient.CONTROL_REPLY_POLLS == 4
    assert NexoClient.LOGIC_REPLY_POLLS == 10
    assert NexoClient.COMMAND_REPLY_POLLS == 10
    assert NexoClient.QUERY_REPLY_POLLS == 40


@pytest.mark.parametrize(
    ("call", "polls"),
    [
        (lambda c: c.turn_on("L1"), NexoClient.CONTROL_REPLY_POLLS),
        (lambda c: c.turn_off("L1"), NexoClient.CONTROL_REPLY_POLLS),
        (lambda c: c.trigger_logic("BWZ"), NexoClient.LOGIC_REPLY_POLLS),
        (lambda c: c.blind_up("R1"), NexoClient.COMMAND_REPLY_POLLS),
    ],
)
def test_silent_success_polls_the_full_count(client, call, polls):
    card = Card()
    with patch.object(client, "_command_retrying", card):
        call(client)
    assert card.gets == polls


def test_set_level_polls_the_control_count(client):
    card = Card()
    with patch.object(client, "_command_retrying", card):
        client.set_level("DIM A", 128)
    assert card.gets == NexoClient.CONTROL_REPLY_POLLS


def test_refusal_on_the_last_poll_is_reported(client):
    replies = [EMPTY] * (NexoClient.CONTROL_REPLY_POLLS - 1)
    card = Card(replies + [EMPTY + "Nieznane wyjscie lub grupa wyjsc: L9."])
    with (
        patch.object(client, "_command_retrying", card),
        pytest.raises(NexoCommandError, match="Nieznane wyjscie"),
    ):
        client.turn_on("L9")


def test_refusal_stops_polling(client):
    card = Card([EMPTY + "Nieznane wyjscie lub grupa wyjsc: L9."])
    with (
        patch.object(client, "_command_retrying", card),
        pytest.raises(NexoCommandError),
    ):
        client.turn_off("L9")
    assert card.gets == 1
