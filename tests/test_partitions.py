"""Alarm partitions: states from the word, a code each time, 24h read only."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.nexo.nexo_client import NexoClient, NexoTimeoutError

from .test_client_replies import Card
from .test_init import OPTIONS, _setup, _tick
from .test_thermostats import Commands

PARTS = {**OPTIONS, "partitions": [
    {"name": "HOUSE", "mode": "armed_away"},
    {"name": "NIGHT", "mode": "armed_night"},
    {"name": "FIRE", "mode": "armed_away"},
]}
HOUSE, NIGHT, FIRE = "alarm_control_panel.nexo_house", "alarm_control_panel.nexo_night", "alarm_control_panel.nexo_fire"


async def test_states_from_the_word(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, PARTS)
    assert hass.states.get(HOUSE).state == "disarmed"
    assert hass.states.get(NIGHT).state == "armed_night"  # the mode picked, not "away"
    assert hass.states.get(FIRE).state == "armed_away"
    fake_nexo.states["NIGHT"] = 3  # "jest uzbrojona alarmuje"
    await _tick(hass, freezer, 5)  # read with the inputs
    assert hass.states.get(NIGHT).state == "triggered"


async def test_one_button_and_a_code(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, PARTS)
    house, night, fire = (hass.states.get(e).attributes for e in (HOUSE, NIGHT, FIRE))
    assert house["supported_features"] == 2  # arm away only
    assert night["supported_features"] == 4  # arm night only
    assert house["code_format"] == "number" and house["code_arm_required"]
    assert fire["supported_features"] == 0  # 24h: no buttons, not even disarm
    assert fire["code_format"] is None


async def test_arm_and_disarm_with_the_code(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, PARTS)
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": HOUSE, "code": "2468"}, blocking=True
    )
    fake_nexo.arm.assert_called_once_with("2468", "HOUSE")
    await _tick(hass, freezer, 1)
    assert hass.states.get(HOUSE).state == "armed_away"
    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": HOUSE, "code": "2468"}, blocking=True
    )
    await _tick(hass, freezer, 1)
    assert hass.states.get(HOUSE).state == "disarmed"


async def test_wrong_code_is_said_and_fired(hass: HomeAssistant, fake_nexo, freezer) -> None:
    await _setup(hass, PARTS)
    events = []
    hass.bus.async_listen("nexo_wrong_code", lambda e: events.append(e.data))
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_away", {"entity_id": HOUSE, "code": "1234"},
            blocking=True,
        )
    assert err.value.translation_key == "wrong_code"
    assert fake_nexo.arm.call_count == 1  # never retried
    assert events == [{"partition": "HOUSE", "action": "arm"}]
    await _tick(hass, freezer, 1)
    assert hass.states.get(HOUSE).state == "disarmed"


async def test_24h_partition_is_not_disarmed(hass: HomeAssistant, fake_nexo) -> None:
    await _setup(hass, PARTS)
    with pytest.raises((HomeAssistantError, ServiceValidationError)):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_disarm", {"entity_id": FIRE, "code": "2468"},
            blocking=True,
        )
    fake_nexo.disarm.assert_not_called()


async def test_options_pick_partitions_and_modes(hass: HomeAssistant, fake_nexo) -> None:
    entry = await _setup(hass, OPTIONS)
    flow_id = (await hass.config_entries.options.async_init(entry.entry_id))["flow_id"]
    result = await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "partitions"})
    assert result["step_id"] == "partitions"
    result = await hass.config_entries.options.async_configure(flow_id, {"partitions": ["NIGHT", "FIRE"]})
    assert result["step_id"] == "partitions_mode"
    assert [str(f) for f in result["data_schema"].schema] == ["NIGHT"]  # 24h has no mode
    result = await hass.config_entries.options.async_configure(flow_id, {"NIGHT": "armed_night"})
    assert result["type"] is FlowResultType.MENU
    await hass.config_entries.options.async_configure(flow_id, {"next_step_id": "save"})
    assert entry.options["partitions"] == [
        {"name": "NIGHT", "mode": "armed_night"}, {"name": "FIRE", "mode": "armed_away"},
    ]


# ------------------------------------------------------------------ the client


@pytest.fixture
def client():
    with patch("custom_components.nexo.nexo_client.time.sleep"):
        yield NexoClient("192.0.2.1", "pw", auto_connect=False)


def test_arming_is_sent_once_and_the_code_kept_out(client, caplog) -> None:
    """A lost confirmation is an error, not a second try: three wrong codes
    start the alarm scheme."""
    sent = []

    def command(data):
        sent.append(data)
        raise NexoTimeoutError("no answer")

    client._sock = object()
    with patch.object(client, "_command", command), patch.object(client, "_close_socket"):
        with pytest.raises(Exception, match="not sent again"):
            client.arm("2468", "NOCNA OBWODOWA")
    assert len(sent) == 1
    assert "2468" not in caplog.text


def test_wrong_password_reply(client) -> None:
    card = Commands(["PARTYCJE; proba modyfikacji stanu - haslo niepoprawne"])
    # The command goes once; the reply is then polled with "get"
    with patch.object(client, "_command_once", card), patch.object(client, "_command_retrying", card):
        with pytest.raises(Exception) as err:
            client.arm("1234", "NOCNA OBWODOWA")
    assert NexoClient.WRONG_PASSWORD in str(err.value)
    assert "1234" not in str(err.value)
