"""学習済み信号ボタンの検証。 / Tests for learned signal buttons."""

from copy import deepcopy

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.nature_remo.const import DOMAIN
from .conftest import make_aircon, make_device, make_ir, make_light, mock_api
from .test_coordinator import setup


def entity_id(hass, signal_id):
    """信号の安定 ID から検索。 / Look up a signal's stable ID."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"nature_remo_signal_{signal_id}")


async def test_buttons_for_all_appliance_types(hass, aioclient_mock, config_entry):
    """種類に関係なく各信号が所属家電に追加される。 / Add every signal to its appliance."""
    appliances = [make_ir(), make_aircon(), make_light(),
                  {"id": "tv-1", "type": "TV", "nickname": "TV", "device": make_device()}]
    for appliance in appliances:
        appliance["signals"] = [{"id": f"{appliance['id']}-{i}", "name": f"Signal {i}"} for i in range(2)]
    mock_api(aioclient_mock, appliances=appliances)
    await setup(hass, config_entry)
    registry = er.async_get(hass)
    assert len(hass.states.async_all("button")) == 8
    for appliance in appliances:
        device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, appliance["id"]), config_entry.entry_id)
        for signal in appliance["signals"]:
            button = entity_id(hass, signal["id"])
            assert registry.async_get(button).device_id == device.id
            assert registry.async_get(button).original_name == signal["name"]
            url = f"https://api.nature.global/1/signals/{signal['id']}/send"
            aioclient_mock.post(url, json={})
            await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
            assert str(aioclient_mock.mock_calls[-1][1]) == url
            assert aioclient_mock.mock_calls[-1][0] == "POST"
    assert aioclient_mock.call_count == 10


async def test_failed_press_surfaces_error_without_changing_appliance(hass, aioclient_mock, config_entry):
    """失敗は UI に返り家電の状態を変更しない。 / Surface errors without changing appliance state."""
    appliance = make_aircon()
    appliance["signals"] = [{"id": "clean-1", "name": "Internal clean"}]
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    snapshot = deepcopy(coordinator.data)
    climate_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, "nature_remo_climate_aircon-1")
    before = hass.states.get(climate_id)
    aioclient_mock.post("https://api.nature.global/1/signals/clean-1/send", status=500)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": entity_id(hass, "clean-1")}, blocking=True)
    assert coordinator.data == snapshot
    assert hass.states.get(climate_id) == before


async def test_existing_remote_still_sends_signals(hass, aioclient_mock, config_entry):
    """従来の remote サービスも同じ信号を送る。 / Keep existing remote services working."""
    mock_api(aioclient_mock, appliances=[make_ir()])
    await setup(hass, config_entry)
    remote_id = er.async_get(hass).async_get_entity_id("remote", DOMAIN, "nature_remo_remote_ir-1")
    url = "https://api.nature.global/1/signals/signal-on/send"
    aioclient_mock.post(url, json={})
    await hass.services.async_call("remote", "send_command", {"entity_id": remote_id, "command": "ON"}, blocking=True)
    assert str(aioclient_mock.mock_calls[-1][1]) == url
    assert hass.states.get(remote_id).state == STATE_UNKNOWN
    assert hass.states.get(remote_id).attributes["last_command"] == "on"
    assert entity_id(hass, "signal-on") is not None


async def test_removed_signal_becomes_unavailable(hass, aioclient_mock, config_entry):
    """削除と再取得を既存のポーリングで反映。 / Reflect removal and recovery through existing polling."""
    appliance = make_ir()
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    button_id = entity_id(hass, "signal-on")
    for snapshot in [{**appliance, "signals": []}, None, appliance]:
        mock_api(aioclient_mock, appliances=[snapshot] if snapshot else [])
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert (hass.states.get(button_id).state == STATE_UNAVAILABLE) == (snapshot != appliance)
        assert aioclient_mock.call_count == 2
