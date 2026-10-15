"""How many times the client polls for a reply after each kind of command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.nexo.nexo_client import NexoClient, NexoCommandError, NexoProtocolError

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


class RecordingCard(Card):
    """A Card that also keeps every command sent, 'get' polls left out."""

    def __init__(self, replies: list[str] | None = None) -> None:
        super().__init__(replies)
        self.sent: list[str] = []

    def __call__(self, data: str, log_as: str | None = None) -> str:
        if data != "get":
            self.sent.append(data)
        return super().__call__(data, log_as)


# Another client's query answered on this session (2026-10-11 at home: a test script
# polled BRAMA GARAZ while a Step press asked for the reed switch)
FOREIGN = EMPTY + "BRAMA GARAZ 0"


def test_a_foreign_reply_is_asked_again_once(client):
    card = RecordingCard([FOREIGN, EMPTY + "KON BRAMA GARAZ 101"])
    with patch.object(client, "_command_retrying", card), patch.object(client, "_drain_queue"):
        assert client.get_state("KON BRAMA GARAZ") == 101
    assert card.sent == ["system C 'KON BRAMA GARAZ' ?"] * 2
    assert client.reply_mismatches == 1


def test_two_foreign_replies_in_a_row_fail_as_before(client):
    card = RecordingCard([FOREIGN, FOREIGN])
    with (
        patch.object(client, "_command_retrying", card),
        patch.object(client, "_drain_queue"),
        pytest.raises(NexoProtocolError, match="BRAMA GARAZ 0"),
    ):
        client.get_state("KON BRAMA GARAZ")
    assert len(card.sent) == 2  # asked twice, never a third time
