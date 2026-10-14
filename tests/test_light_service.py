"""照明サービスの所属と寿命。 / Light service routing and lifecycle."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nature_remo.api import NatureRemoAPI, NatureRemoError
from custom_components.nature_remo.const import DOMAIN

from .conftest import make_light


def make_account(name):
    """架空の別アカウントと照明。 / A synthetic separate account and light."""
    entry = MockConfigEntry(domain=DOMAIN, title=name, data={"api_key": f"token-{name}"}, unique_id=f"user-{name}")
    appliance = make_light()
    appliance.update(id=f"light-{name}", nickname=f"Light {name}")
    appliance["device"].update(id=f"device-{name}", name=f"Remo {name}")
    api = AsyncMock(spec=NatureRemoAPI)
    api.get_devices.return_value = [appliance["device"]]
    api.get_appliances.return_value = [appliance]
    api.send_light_command.return_value = {"power": "on", "last_button": "night"}
    return entry, api


async def setup_light(hass, name="A"):
    entry, api = make_account(name)
    entry.add_to_hass(hass)
    with patch("custom_components.nature_remo.NatureRemoAPI", return_value=api):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("light", DOMAIN, f"nature_remo_light_light-{name}")
    assert entity_id is not None
    return entry, api, entity_id


async def send(hass, entity_id, mode="night", response=True):
    return await hass.services.async_call(
        DOMAIN, "send_light_mode", {"entity_id": entity_id, "mode": mode},
        blocking=True, return_response=response,
    )


@pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
async def test_multi_entry_routing_and_unload(hass, order):
    """両順序で自分の API へ送信し、他方の unload に影響されない。 / Route independently of setup and unload order."""
    accounts = {}
    for name in order:
        accounts[name] = await setup_light(hass, name)
    for name, (entry, api, entity_id) in accounts.items():
        assert await send(hass, entity_id) == {"status": "success", "appliance_id": f"light-{name}"}
        api.send_light_command.assert_awaited_once_with(f"light-{name}", "night")
        api.send_light_command.reset_mock()
        assert hass.states.get(entity_id).attributes["mode"] == "night"

    entry_b, api_b, entity_b = accounts["B"]
    old_lights = hass.data[DOMAIN][entry_b.entry_id]["lights"]
    assert await hass.config_entries.async_unload(entry_b.entry_id)
    assert old_lights == {}
    assert hass.services.has_service(DOMAIN, "send_light_mode")
    await send(hass, accounts["A"][2], "off")
    accounts["A"][1].send_light_command.assert_awaited_once_with("light-A", "off")
    assert hass.states.get(accounts["A"][2]).state == "off"
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, entity_b)
    assert error.value.translation_key == "light_not_loaded"
    api_b.send_light_command.assert_not_awaited()


async def test_last_entry_unload_keeps_service_without_stale_light(hass):
    """最後の entry を外してもサービスは残り、古い照明には送信しない。 / Keep validation after the last entry unload."""
    entry, api, entity_id = await setup_light(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.services.has_service(DOMAIN, "send_light_mode")
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, entity_id)
    assert error.value.translation_key == "light_not_loaded"
    api.send_light_command.assert_not_awaited()


async def test_reload_routes_once_to_new_instance(hass):
    """reload 後は新インスタンスに一度だけ送信。 / Send once to the replacement instance after reload."""
    entry, old_api, entity_id = await setup_light(hass)
    old_lights = hass.data[DOMAIN][entry.entry_id]["lights"]
    old_entity = next(iter(old_lights.values()))
    _, new_api = make_account("A")
    with patch("custom_components.nature_remo.NatureRemoAPI", return_value=new_api):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert old_lights == {}
    assert next(iter(hass.data[DOMAIN][entry.entry_id]["lights"].values())) is not old_entity
    await send(hass, entity_id)
    new_api.send_light_command.assert_awaited_once_with("light-A", "night")
    old_api.send_light_command.assert_not_awaited()
    assert old_entity.extra_state_attributes["mode"] == "on"


async def test_disabled_light_cannot_send(hass):
    """無効化した照明は、entry がロード済みでも拒否。 / Reject a disabled light on a loaded entry."""
    entry, api, entity_id = await setup_light(hass)
    er.async_get(hass).async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    # イベント処理前でもレジストリの無効状態を検証。 / Validate the registry before removal finishes.
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, entity_id)
    assert error.value.translation_key == "light_not_loaded"
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][entry.entry_id]["lights"] == {}
    assert entry.state is ConfigEntryState.LOADED
    api.send_light_command.assert_not_awaited()


async def test_disabled_entry_cannot_send(hass):
    """entry を無効化した場合も送信を拒否。 / Reject a disabled config entry."""
    entry, api, entity_id = await setup_light(hass)
    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, entity_id)
    assert error.value.translation_key == "light_not_loaded"
    api.send_light_command.assert_not_awaited()


async def test_failed_setup_keeps_service_and_rejects_target(hass):
    """setup 失敗時にも登録済みのサービスが対象を検証。 / Validate targets even when setup fails."""
    entry, api = make_account("A")
    entry.add_to_hass(hass)
    registry_entry = er.async_get(hass).async_get_or_create(
        "light", DOMAIN, "nature_remo_light_light-A", config_entry=entry,
    )
    api.get_devices.side_effect = NatureRemoError("synthetic failure")
    with patch("custom_components.nature_remo.NatureRemoAPI", return_value=api):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.services.has_service(DOMAIN, "send_light_mode")
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, registry_entry.entity_id)
    assert error.value.translation_key == "light_not_loaded"
    api.send_light_command.assert_not_awaited()


@pytest.mark.parametrize("target", ["missing", "other_platform", "wrong_domain"])
async def test_unknown_target(hass, target):
    """存在しない照明や別統合・ドメインには送信しない。 / Reject unknown and foreign targets."""
    entry, api, entity_id = await setup_light(hass)
    if target == "missing":
        entity_id = "light.missing"
    elif target == "other_platform":
        entity_id = er.async_get(hass).async_get_or_create("light", "other", "foreign").entity_id
    else:
        entity_id = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "sensor", config_entry=entry).entity_id
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, entity_id)
    assert error.value.translation_key == "light_not_found"
    assert error.value.translation_domain == DOMAIN
    api.send_light_command.assert_not_awaited()


async def test_unsupported_mode_does_not_send(hass):
    """未対応モードは翻訳付きエラーで、状態も送信数も変えない。 / Reject unsupported modes before sending."""
    _, api, entity_id = await setup_light(hass)
    before = hass.states.get(entity_id)
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, entity_id, "nonexistent")
    assert error.value.translation_key == "unsupported_light_mode"
    assert hass.states.get(entity_id) == before
    api.send_light_command.assert_not_awaited()


async def test_api_failure_preserves_state(hass):
    """API が失敗した場合は状態を変えない。 / Preserve state on API failure."""
    _, api, entity_id = await setup_light(hass)
    before = hass.states.get(entity_id)
    api.send_light_command.side_effect = NatureRemoError("synthetic failure")
    with pytest.raises(NatureRemoError):
        await send(hass, entity_id, "off")
    assert hass.states.get(entity_id) == before
    api.send_light_command.assert_awaited_once_with("light-A", "off")


async def test_renamed_light_uses_current_registry_record(hass):
    """改名後の ID を解決し、古い ID は拒否。 / Resolve a renamed light and reject its old ID."""
    entry, api, entity_id = await setup_light(hass)
    registry = er.async_get(hass)
    old_registry_id = registry.async_get(entity_id).id
    registry.async_update_entity(entity_id, new_entity_id="light.renamed")
    await hass.async_block_till_done()
    assert set(hass.data[DOMAIN][entry.entry_id]["lights"]) == {old_registry_id}
    await send(hass, "light.renamed")
    api.send_light_command.assert_awaited_once_with("light-A", "night")
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, entity_id)
    assert error.value.translation_key == "light_not_found"
    assert api.send_light_command.await_count == 1


async def test_removed_light_unregisters_instance(hass):
    """エンティティ削除時に参照を解放。 / Release the runtime reference when the entity is removed."""
    entry, api, entity_id = await setup_light(hass)
    er.async_get(hass).async_remove(entity_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][entry.entry_id]["lights"] == {}
    with pytest.raises(ServiceValidationError):
        await send(hass, entity_id)
    api.send_light_command.assert_not_awaited()


async def test_service_available_without_entries(hass):
    """entry がなくてもサービスの定義と検証が使える。 / Register service definitions without entries."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "send_light_mode")
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, "light.missing")
    assert error.value.translation_key == "light_not_found"


async def test_service_can_be_called_without_response(hass):
    """既存の応答不要の呼び出しも維持。 / Preserve calls that do not request a response."""
    _, api, entity_id = await setup_light(hass)
    assert await send(hass, entity_id, response=False) is None
    api.send_light_command.assert_awaited_once_with("light-A", "night")
