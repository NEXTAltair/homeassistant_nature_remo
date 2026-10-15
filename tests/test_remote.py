"""状態を持たない学習 remote。 / Stateless learned remotes and visibility migration."""

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.const import STATE_UNKNOWN
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar, entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.nature_remo.const import DOMAIN
from custom_components.nature_remo.remote_visibility import ISSUE_PREFIX, VISIBILITY_REVIEWED
from .conftest import make_aircon, make_ir, make_light, make_tv, mock_api
from .test_coordinator import setup


def remote_id(hass, appliance_id="ir-1"):
    return er.async_get(hass).async_get_entity_id("remote", DOMAIN, f"nature_remo_remote_{appliance_id}")


def make_stateless(appliance_id="ir-1", nickname="Circulator"):
    return {**make_ir(), "id": appliance_id, "nickname": nickname, "signals": [
        {"id": f"{appliance_id}-{name}", "name": name} for name in ("電源", "風量", "左右", "上下")
    ]}


async def send(hass, service="send_command", appliance_id="ir-1", **data):
    await hass.services.async_call("remote", service, {
        "entity_id": remote_id(hass, appliance_id), **data,
    }, blocking=True)


async def start_repair(hass, entry):
    assert await async_setup_component(hass, "repairs", {})
    return await repairs_flow_manager(hass).async_init(
        DOMAIN, context={"issue_id": f"{ISSUE_PREFIX}{entry.entry_id}"},
    )


async def test_new_remotes_hidden_enabled_unknown_with_visible_buttons(hass, aioclient_mock, config_entry):
    appliances = [make_stateless(), make_stateless("kvm", "KVM"), make_aircon(), make_light(), make_tv()]
    for appliance in appliances[2:4]:
        appliance["signals"] = [{"id": appliance["id"] + "-extra", "name": "Extra"}]
    mock_api(aioclient_mock, appliances=appliances)
    await setup(hass, config_entry)
    registry = er.async_get(hass)
    for appliance in appliances[:-1]:
        entry = registry.async_get(remote_id(hass, appliance["id"]))
        assert entry.hidden_by is er.RegistryEntryHider.INTEGRATION
        assert entry.disabled_by is None
        assert entry.options[DOMAIN][VISIBILITY_REVIEWED]
        assert hass.states.get(entry.entity_id).state == STATE_UNKNOWN
        assert hass.states.get(entry.entity_id).attributes["last_command"] is None
        for signal in appliance["signals"]:
            button_id = registry.async_get_entity_id("button", DOMAIN, f"nature_remo_signal_{signal['id']}")
            assert registry.async_get(button_id).hidden_by is None
    assert registry.async_get(remote_id(hass, "tv-1")).hidden_by is None
    assert hass.states.async_all("climate")[0].state == "cool"
    assert hass.states.async_all("light")[0].state == "on"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PREFIX}{config_entry.entry_id}") is None
    assert aioclient_mock.call_count == 2


async def test_commands_and_buttons_do_not_claim_power_state(hass, aioclient_mock, config_entry):
    appliance = make_stateless()
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    for signal in appliance["signals"]:
        url = f"https://api.nature.global/1/signals/{signal['id']}/send"
        aioclient_mock.post(url, json={})
        before = aioclient_mock.call_count
        await send(hass, command=signal["name"])
        assert aioclient_mock.call_count == before + 1
        state = hass.states.get(remote_id(hass))
        assert state.state == STATE_UNKNOWN
        assert state.attributes["last_command"] == signal["name"]
        assert state.attributes["command"] == signal["name"]
        button_id = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"nature_remo_signal_{signal['id']}")
        await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
        assert aioclient_mock.call_count == before + 2
        assert hass.states.get(remote_id(hass)).state == STATE_UNKNOWN
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(remote_id(hass)).state == STATE_UNKNOWN
    assert hass.states.get(remote_id(hass)).attributes["last_command"] is None


@pytest.mark.parametrize("service", ["turn_on", "turn_off"])
async def test_no_discrete_power_never_sends_toggle(hass, aioclient_mock, config_entry, service):
    mock_api(aioclient_mock, appliances=[make_stateless()])
    await setup(hass, config_entry)
    before = aioclient_mock.call_count
    with pytest.raises(ServiceValidationError) as error:
        await send(hass, service)
    assert error.value.translation_key == "unsupported_ir_power"
    assert aioclient_mock.call_count == before
    assert hass.states.get(remote_id(hass)).state == STATE_UNKNOWN
    assert hass.states.get(remote_id(hass)).attributes["last_command"] is None


@pytest.mark.parametrize("names", [("On", "Off"), ("オン", "オフ")])
async def test_discrete_commands_keep_service_compatibility_without_assumed_state(hass, aioclient_mock, config_entry, names):
    appliance = make_ir()
    for signal, name in zip(appliance["signals"], names):
        signal["name"] = name
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    for signal, command in zip(appliance["signals"], ("on", "off")):
        aioclient_mock.post(f"https://api.nature.global/1/signals/{signal['id']}/send", json={})
        await send(hass, f"turn_{command}")
        assert hass.states.get(remote_id(hass)).state == STATE_UNKNOWN
        assert hass.states.get(remote_id(hass)).attributes["last_command"] == command
        await send(hass, command=signal["name"].upper())
        assert hass.states.get(remote_id(hass)).state == STATE_UNKNOWN


@pytest.mark.parametrize("service,data", [("send_command", {"command": "off"}), ("turn_on", {}), ("turn_off", {})])
async def test_failed_send_retains_last_success(hass, aioclient_mock, config_entry, service, data):
    mock_api(aioclient_mock, appliances=[make_ir()])
    await setup(hass, config_entry)
    aioclient_mock.post("https://api.nature.global/1/signals/signal-on/send", json={})
    await send(hass, command="ON")
    before = hass.states.get(remote_id(hass))
    aioclient_mock.clear_requests()
    for signal in ("on", "off"):
        aioclient_mock.post(f"https://api.nature.global/1/signals/signal-{signal}/send", status=500)
    with pytest.raises(HomeAssistantError):
        await send(hass, service, **data)
    assert hass.states.get(remote_id(hass)) == before


async def test_existing_visibility_is_reviewed_without_losing_preferences(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Study")
    appliances = [make_stateless(key, key) for key in ("fan", "kvm", "hidden", "disabled")]
    originals = {}
    for appliance in appliances:
        key = appliance["id"]
        entity = registry.async_get_or_create("remote", DOMAIN, f"nature_remo_remote_{key}",
            config_entry=config_entry, suggested_object_id=f"saved_{key}")
        registry.async_update_entity(entity.entity_id, name=f"Custom {key}", area_id=area.id,
            hidden_by=er.RegistryEntryHider.USER if key == "hidden" else None,
            disabled_by=er.RegistryEntryDisabler.USER if key == "disabled" else None)
        registry.async_update_entity_options(entity.entity_id, DOMAIN, {"unrelated": 42})
        originals[key] = registry.async_get(entity.entity_id)
    mock_api(aioclient_mock, appliances=appliances)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for key, old in originals.items():
        current = registry.async_get(old.entity_id)
        assert current.id == old.id
        assert (current.name, current.area_id, current.hidden_by, current.disabled_by) == (
            old.name, old.area_id, old.hidden_by, old.disabled_by)
    result = await start_repair(hass, config_entry)
    assert result["type"] is FlowResultType.FORM
    choices = result["data_schema"].schema["remotes"].config["options"]
    assert {choice["value"] for choice in choices} == {originals[key].id for key in ("fan", "kvm")}
    before = aioclient_mock.call_count
    result = await repairs_flow_manager(hass).async_configure(result["flow_id"], {"remotes": [originals["fan"].id]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert aioclient_mock.call_count == before
    assert registry.async_get(originals["fan"].entity_id).hidden_by is er.RegistryEntryHider.INTEGRATION
    assert registry.async_get(originals["kvm"].entity_id).hidden_by is None
    for old in originals.values():
        current = registry.async_get(old.entity_id)
        assert current.options[DOMAIN] == {"unrelated": 42, VISIBILITY_REVIEWED: True}
        assert current.disabled_by == old.disabled_by
    # 非表示でも既存 ID のサービスは有効。 / Hidden entities retain their existing service ID.
    aioclient_mock.post("https://api.nature.global/1/signals/fan-電源/send", json={})
    await send(hass, appliance_id="fan", command="電源")
    assert hass.states.get(originals["fan"].entity_id).state == STATE_UNKNOWN
    # 再表示した利用者の選択も再読込で戻さない。 / Preserve a later explicit show choice.
    registry.async_update_entity(originals["fan"].entity_id, hidden_by=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(originals["fan"].entity_id).hidden_by is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PREFIX}{config_entry.entry_id}") is None


async def test_repair_rejects_foreign_or_changed_targets_without_partial_edits(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create("remote", DOMAIN, "nature_remo_remote_ir-1", config_entry=config_entry)
    mock_api(aioclient_mock, appliances=[make_ir(), make_tv()])
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await start_repair(hass, config_entry)
    tv = registry.async_get(remote_id(hass, "tv-1"))
    with pytest.raises(InvalidData):
        await repairs_flow_manager(hass).async_configure(result["flow_id"], {"remotes": [old.id, tv.id]})
    assert registry.async_get(old.entity_id).hidden_by is None
    assert registry.async_get(tv.entity_id).hidden_by is None
    # 改名は stable registry ID で追従する。 / Follow a rename using the stable registry ID.
    registry.async_update_entity(old.entity_id, new_entity_id="remote.renamed")
    await hass.async_block_till_done()
    result = await repairs_flow_manager(hass).async_configure(result["flow_id"], {"remotes": [old.id]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert registry.async_get("remote.renamed").hidden_by is er.RegistryEntryHider.INTEGRATION


async def test_repair_requires_loaded_entry_and_is_removed_with_entry(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("remote", DOMAIN, "nature_remo_remote_ir-1", config_entry=config_entry)
    mock_api(aioclient_mock, appliances=[make_ir()])
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await start_repair(hass, config_entry)
    await hass.config_entries.async_unload(config_entry.entry_id)
    result = await repairs_flow_manager(hass).async_configure(result["flow_id"], {"remotes": []})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"
    await hass.config_entries.async_remove(config_entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PREFIX}{config_entry.entry_id}") is None


@pytest.mark.parametrize("change", ["hide", "disable", "remove"])
async def test_repair_revalidates_selection_at_submission(hass, aioclient_mock, config_entry, change):
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create("remote", DOMAIN, "nature_remo_remote_ir-1", config_entry=config_entry)
    mock_api(aioclient_mock, appliances=[make_ir()])
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await start_repair(hass, config_entry)
    if change == "hide":
        registry.async_update_entity(old.entity_id, hidden_by=er.RegistryEntryHider.USER)
    elif change == "disable":
        registry.async_update_entity(old.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    else:
        registry.async_remove(old.entity_id)
    await hass.async_block_till_done()
    before = aioclient_mock.call_count
    result = await repairs_flow_manager(hass).async_configure(result["flow_id"], {"remotes": [old.id]})
    # 対象がなくなった場合は変更せず完了する。 / Finish without mutation when no candidates remain.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert aioclient_mock.call_count == before
    current = registry.async_get(old.entity_id)
    if change == "hide":
        assert current.hidden_by is er.RegistryEntryHider.USER
    elif change == "disable":
        assert current.disabled_by is er.RegistryEntryDisabler.USER
    else:
        assert current is None


async def test_empty_repair_selection_keeps_existing_remotes_visible(hass, aioclient_mock, config_entry):
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create("remote", DOMAIN, "nature_remo_remote_ir-1", config_entry=config_entry)
    mock_api(aioclient_mock, appliances=[make_ir()])
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await start_repair(hass, config_entry)
    result = await repairs_flow_manager(hass).async_configure(result["flow_id"], {"remotes": []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert registry.async_get(old.entity_id).hidden_by is None
    assert registry.async_get(old.entity_id).options[DOMAIN][VISIBILITY_REVIEWED]
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(old.entity_id).hidden_by is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PREFIX}{config_entry.entry_id}") is None
