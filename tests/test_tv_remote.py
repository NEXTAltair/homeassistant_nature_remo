"""TV remote のサービス経由テスト。 / Service-level tests for TV remotes."""

from copy import deepcopy
from unittest.mock import AsyncMock, call as mock_call, patch

import pytest
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.nature_remo.const import DOMAIN
from .conftest import make_tv, mock_api
from .test_coordinator import setup

URL = "https://api.nature.global/1/appliances/tv-1/tv"
SIGNAL_URL = "https://api.nature.global/1/signals/tv-signal-1/send"


def entity_id(hass):
    """TV の安定 ID。 / Stable TV identifier."""
    return er.async_get(hass).async_get_entity_id("remote", DOMAIN, "nature_remo_remote_tv-1")


async def call(hass, service="send_command", **data):
    """公開サービスを呼ぶ。 / Call the public service."""
    await hass.services.async_call("remote", service, {"entity_id": entity_id(hass), **data}, blocking=True)


async def test_tv_without_learned_signals_and_device_grouping(hass, aioclient_mock, config_entry):
    """学習信号なしでも家電に remote が付く。 / Attach a remote even without learned signals."""
    appliance = make_tv()
    appliance["signals"] = []
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    state = hass.states.get(entity_id(hass))
    assert state.state == STATE_UNKNOWN
    assert state.attributes["assumed_state"] is True
    assert state.attributes["input"] == "t"
    assert "" not in state.attributes["available_commands"]
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "tv-1"), config_entry.entry_id)
    assert er.async_get(hass).async_get(entity_id(hass)).device_id == device.id
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize(("command", "canonical"), [
    ("VOL-UP", "vol-up"), ("volume up", "vol-up"), ("入力切替", "select-input-src"),
    ("MuTe", "mute"), ("電源", "power"),
])
async def test_name_or_label_sends_canonical_name(hass, aioclient_mock, config_entry, command, canonical):
    """名前と表示名を区別せず正式名を送る。 / Resolve names and labels to canonical buttons."""
    mock_api(aioclient_mock, appliances=[make_tv()])
    await setup(hass, config_entry)
    aioclient_mock.post(URL, json={"input": "h"})
    await call(hass, command=command)
    assert aioclient_mock.mock_calls[-1][0] == "POST"
    assert str(aioclient_mock.mock_calls[-1][1]) == URL
    assert aioclient_mock.mock_calls[-1][2] == {"button": canonical}
    assert hass.states.get(entity_id(hass)).attributes["input"] == "h"
    assert hass.states.get(entity_id(hass)).state == STATE_UNKNOWN
    assert aioclient_mock.call_count == 3


async def test_name_wins_over_label_and_learned_signal(hass, aioclient_mock, config_entry):
    """名前の衝突でも正式名を優先。 / Prefer canonical names on collisions."""
    appliance = make_tv()
    appliance["tv"]["buttons"][0]["label"] = "mute"
    appliance["signals"][0]["name"] = "mute"
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    aioclient_mock.post(URL, json={"input": "t"})
    await call(hass, command="MUTE")
    assert aioclient_mock.mock_calls[-1][2] == {"button": "mute"}


async def test_learned_command_uses_signal_endpoint_and_button(hass, aioclient_mock, config_entry):
    """学習信号は remote と button の両方から送れる。 / Send learned signals through both entities."""
    mock_api(aioclient_mock, appliances=[make_tv()])
    await setup(hass, config_entry)
    aioclient_mock.post(SIGNAL_URL, json={})
    await call(hass, command="LEARNED hdmi")
    assert str(aioclient_mock.mock_calls[-1][1]) == SIGNAL_URL
    button = er.async_get(hass).async_get_entity_id("button", DOMAIN, "nature_remo_signal_tv-signal-1")
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert str(aioclient_mock.mock_calls[-1][1]) == SIGNAL_URL
    assert aioclient_mock.call_count == 4


@pytest.mark.parametrize("command", ["unknown", "空欄", ["mute", "unknown"]])
async def test_unknown_command_is_error_without_send(hass, aioclient_mock, config_entry, command):
    """不明なコマンドを送信前に拒否。 / Reject unknown commands before sending."""
    mock_api(aioclient_mock, appliances=[make_tv()])
    await setup(hass, config_entry)
    with pytest.raises(ServiceValidationError) as error:
        await call(hass, command=command)
    assert error.value.translation_key == "unknown_tv_command"
    assert error.value.translation_domain == DOMAIN
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize("power_names", [["power"], ["power-on"], ["power-off"]])
async def test_missing_discrete_pair_rejects_turn_services(hass, aioclient_mock, config_entry, power_names):
    """片側だけでも離散電源操作を提供しない。 / Require both commands for turn services."""
    appliance = make_tv()
    appliance["tv"]["buttons"] = [{"name": name, "label": name, "image": "ico_power"} for name in power_names]
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    for service in ("turn_on", "turn_off", "toggle"):
        with pytest.raises(ServiceValidationError) as error:
            await call(hass, service)
        assert error.value.translation_key == "unsupported_tv_power"
    assert aioclient_mock.call_count == 2
    assert hass.states.get(entity_id(hass)).state == STATE_UNKNOWN


async def test_discrete_power_and_failure_keep_confirmed_state(hass, aioclient_mock, config_entry):
    """成功した離散送信だけを推定状態に反映。 / Change assumed power only after success."""
    appliance = make_tv()
    appliance["tv"]["buttons"] += [{"name": name, "label": name, "image": "ico_power"} for name in ("power-on", "power-off")]
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    aioclient_mock.post(URL, json={"input": "t"})
    await call(hass, "turn_on")
    assert aioclient_mock.mock_calls[-1][2] == {"button": "power-on"}
    assert hass.states.get(entity_id(hass)).state == "on"
    mock_api(aioclient_mock, appliances=[appliance])
    aioclient_mock.post(URL, status=500)
    with pytest.raises(HomeAssistantError):
        await call(hass, "turn_off")
    assert hass.states.get(entity_id(hass)).state == "on"
    mock_api(aioclient_mock, appliances=[appliance])
    aioclient_mock.post(URL, json={"input": "h"})
    await call(hass, "turn_off")
    assert aioclient_mock.mock_calls[-1][2] == {"button": "power-off"}
    assert hass.states.get(entity_id(hass)).state == "off"
    await call(hass, command="power")
    assert hass.states.get(entity_id(hass)).state == STATE_UNKNOWN


@pytest.mark.parametrize("response", [None, [], {}, {"input": 4}])
async def test_malformed_response_keeps_previous_snapshot(hass, aioclient_mock, config_entry, response):
    """不正な応答で input を破壊しない。 / Preserve input on malformed responses."""
    mock_api(aioclient_mock, appliances=[make_tv()])
    await setup(hass, config_entry)
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    before = deepcopy(coordinator.data)
    aioclient_mock.post(URL, json=response)
    with pytest.raises(HomeAssistantError):
        await call(hass, command="mute")
    assert coordinator.data == before
    assert hass.states.get(entity_id(hass)).attributes["input"] == "t"


async def test_poll_updates_input_and_commands_and_removal(hass, aioclient_mock, config_entry):
    """既存のポーリングで最新情報を反映。 / Update commands and input through existing polling."""
    appliance = make_tv()
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    appliance["tv"]["state"]["input"] = "h"
    appliance["tv"]["buttons"] = []
    appliance["signals"] = []
    mock_api(aioclient_mock, appliances=[appliance])
    await coordinator.async_refresh()
    assert hass.states.get(entity_id(hass)).attributes["input"] == "h"
    with pytest.raises(ServiceValidationError):
        await call(hass, command="mute")
    mock_api(aioclient_mock, appliances=[])
    await coordinator.async_refresh()
    assert hass.states.get(entity_id(hass)).state == STATE_UNAVAILABLE


async def test_command_sequence_repeats(hass, aioclient_mock, config_entry):
    """複数コマンドと繰り返しを順序通り送る。 / Send command sequences and repeats in order."""
    mock_api(aioclient_mock, appliances=[make_tv()])
    await setup(hass, config_entry)
    aioclient_mock.post(URL, json={"input": "t"})
    await call(hass, command=["mute", "vol-up"], num_repeats=2, delay_secs=0)
    assert [call[2] for call in aioclient_mock.mock_calls[2:]] == [
        {"button": "mute"}, {"button": "vol-up"}, {"button": "mute"}, {"button": "vol-up"},
    ]


@pytest.mark.parametrize(("options", "expected_delay"), [
    ({}, 0.4), ({"delay_secs": 0.2}, 0.2), ({"delay_secs": 0}, None),
])
async def test_sequence_uses_default_or_explicit_delay(hass, aioclient_mock, config_entry, options, expected_delay):
    """省略時は HA 標準の間隔、指定時はその値を使う。 / Honor the HA default or explicit delay."""
    mock_api(aioclient_mock, appliances=[make_tv()])
    await setup(hass, config_entry)
    aioclient_mock.post(URL, json={"input": "t"})
    with patch("custom_components.nature_remo.tv_remote.sleep", new_callable=AsyncMock) as delay:
        await call(hass, command=["mute", "vol-up"], num_repeats=2, **options)
    assert delay.await_args_list == ([mock_call(expected_delay)] * 3 if expected_delay else [])
    assert aioclient_mock.call_count == 6
