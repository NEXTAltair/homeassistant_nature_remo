"""エコキュートの読み取りとライフサイクル。 / Heater parsing and lifecycle tests."""

from copy import deepcopy
from datetime import timedelta
import time

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nature_remo.const import DOMAIN
from custom_components.nature_remo.echonetlite import parse_water_heater_properties
from .conftest import (
    APPLIANCES_URL, DEVICES_URL, ECHONETLITE_URL, NORMAL_METER, NORMAL_WATER_HEATER,
    make_aircon, make_device, make_echonetlite, make_meter, make_water_heater, mock_api,
)
from .test_coordinator import setup


@pytest.mark.parametrize(("epc", "value", "expected"), [
    ("80", "30", True), ("80", "31", False),
    ("85", "00000000", 0), ("85", "0012d687", 1234.567), ("85", "3B9AC9FF", 999999.999),
    ("b0", "41", "automatic"), ("B0", "42", "manual_heating"), ("b0", "43", "manual_stop"),
    ("b2", "41", True), ("b2", "42", False), ("c0", "41", True), ("c0", "42", False),
    ("c3", "41", True), ("c3", "42", False), ("e1", "0000", 0), ("E1", "0172", 370),
    ("e1", "fffd", 65533), ("e3", "41", True), ("e3", "42", False),
    ("ea", "41", "filling"), ("ea", "42", "stopped"), ("ea", "43", "keeping_warm"),
])
def test_all_supported_values(epc, value, expected):
    """各コード、上下限とゼロを確認。 / Check each code, boundaries and legitimate zeros."""
    assert parse_water_heater_properties([{"epc": epc, "val": value}]) == {epc.lower(): expected}


@pytest.mark.parametrize(("epc", "value"), [
    *[(epc, "00") for epc in ("80", "b0", "b2", "c0", "c3", "e3", "ea")],
    ("80", "41"), ("b0", "44"), ("ea", "44"), ("85", "3b9aca00"), ("85", "fffffffe"),
    ("85", "ffffffff"), ("e1", "fffe"), ("e1", "ffff"),
    ("85", "0000"), ("85", "0000000000"), ("e1", "172"), ("e1", "000172"),
    ("80", "030"), ("80", "3"), ("e1", "-001"), ("e1", "+001"), ("e1", "0x10"),
    ("e1", " 172"), ("e1", "0172\n"), ("e1", "GGGG"), ("85", "12.34567"),
    *[("e1", value) for value in (None, "", 370, True, {}, [])],
])
def test_invalid_values_are_none(epc, value):
    """不明値をゼロや OFF にしない。 / Never replace invalid readings with zero or off."""
    assert parse_water_heater_properties([{"epc": epc, "val": value}]) == {epc: None}


def test_missing_unknown_and_malformed_properties():
    """未対応 EPC は公開せず既知の欠測を保持。 / Skip unsupported EPCs and keep known missing values."""
    assert parse_water_heater_properties([]) == {}
    assert parse_water_heater_properties([
        None, {}, {"epc": "ff", "val": "41"}, {"epc": 0x80, "val": "30"},
        {"epc": "0x80", "val": "30"}, {"epc": "80"},
    ]) == {"80": None}


def coordinator_of(hass, entry):
    """追加 coordinator を取得。 / Get the optional coordinator."""
    return hass.data[DOMAIN][entry.entry_id]["echonetlite_coordinator"]


def entity_id(hass, epc):
    """EPC に対応する安定 ID。 / Stable entity ID for an EPC."""
    domain = "sensor" if epc in ("85", "b0", "e1", "ea") else "binary_sensor"
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"nature_remo_water_heater_heater-1_{epc}")


async def setup_heater(hass, aioclient_mock, entry, properties=None):
    """架空の機器だけで初期化。 / Set up using only synthetic devices."""
    mock_api(aioclient_mock, devices=[make_device({})], appliances=[make_water_heater()], echonetlite=make_echonetlite(properties))
    await setup(hass, entry)
    return coordinator_of(hass, entry)


def mock_echonetlite(aioclient_mock, response, status=200, headers=None):
    """通常の取得は成功させ、追加取得だけを置換。 / Replace the extra poll while normal polling succeeds."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(DEVICES_URL, json=[make_device({})])
    aioclient_mock.get(APPLIANCES_URL, json=[make_water_heater()])
    aioclient_mock.get(ECHONETLITE_URL, json=response, status=status, headers=headers)


async def test_entities_values_metadata_and_devices(hass, aioclient_mock, config_entry, caplog):
    """全 EPC を正しい種類・単位・家電で公開。 / Expose all EPCs with correct classes, units and device."""
    coordinator = await setup_heater(hass, aioclient_mock, config_entry)
    expected = {"80": "on", "85": "1234.567", "b0": "automatic", "b2": "off", "c0": "on",
                "c3": "off", "e1": "370", "e3": "on", "ea": "keeping_warm"}
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "heater-1"), config_entry.entry_id)
    assert device.manufacturer == "Example"
    assert device.model == "Heater 1"
    assert len(hass.states.async_all("sensor")) == 4
    assert len(hass.states.async_all("binary_sensor")) == 5
    for epc, value in expected.items():
        entity = entity_id(hass, epc)
        assert hass.states.get(entity).state == value
        assert er.async_get(hass).async_get(entity).device_id == device.id
        assert er.async_get(hass).async_get(entity).original_name
    energy = hass.states.get(entity_id(hass, "85"))
    assert energy.attributes["device_class"] == "energy"
    assert energy.attributes["state_class"] == "total_increasing"
    assert energy.attributes["unit_of_measurement"] == "kWh"
    water = hass.states.get(entity_id(hass, "e1"))
    assert water.attributes["device_class"] == "volume_storage"
    assert water.attributes["state_class"] == "measurement"
    assert water.attributes["unit_of_measurement"] == "L"
    assert hass.states.get(entity_id(hass, "b0")).attributes["options"] == ["automatic", "manual_heating", "manual_stop"]
    assert coordinator.update_interval == timedelta(minutes=5)
    assert aioclient_mock.call_count == 3
    assert not any(record.name == "homeassistant.helpers.frame" and record.levelno >= 30 for record in caplog.records)


async def test_only_present_supported_epcs_create_entities(hass, aioclient_mock, config_entry):
    """存在する既知 EPC だけを追加。 / Create only reported, supported EPCs."""
    await setup_heater(hass, aioclient_mock, config_entry, {"e1": "ffff", "80": "31", "ff": "41"})
    assert hass.states.get(entity_id(hass, "e1")).state == STATE_UNKNOWN
    assert hass.states.get(entity_id(hass, "80")).state == "off"
    assert entity_id(hass, "85") is None
    assert len(hass.states.async_all("sensor")) == 1
    assert len(hass.states.async_all("binary_sensor")) == 1


async def test_missing_invalid_and_recovered_epcs(hass, aioclient_mock, config_entry):
    """欠測や不正値から正常なゼロへ復帰。 / Recover from missing or invalid readings to valid zeros."""
    coordinator = await setup_heater(hass, aioclient_mock, config_entry)
    mock_echonetlite(aioclient_mock, make_echonetlite({"85": "fffffffe", "80": "00"}))
    await coordinator.async_refresh()
    for epc in NORMAL_WATER_HEATER:
        assert hass.states.get(entity_id(hass, epc)).state == STATE_UNKNOWN
    mock_echonetlite(aioclient_mock, make_echonetlite({"85": "00000000", "e1": "0000", "80": "31"}))
    await coordinator.async_refresh()
    assert float(hass.states.get(entity_id(hass, "85")).state) == 0
    assert float(hass.states.get(entity_id(hass, "e1")).state) == 0
    assert hass.states.get(entity_id(hass, "80")).state == "off"


@pytest.mark.parametrize("appliances", [[], [make_meter(NORMAL_METER)]])
async def test_no_heater_does_not_fetch_extra_endpoint(hass, aioclient_mock, config_entry, appliances):
    """対象なしでは追加 API を呼ばない。 / Do not call the extra endpoint without a heater."""
    mock_api(aioclient_mock, appliances=appliances)
    await setup(hass, config_entry)
    assert coordinator_of(hass, config_entry) is None
    await hass.data[DOMAIN][config_entry.entry_id]["coordinator"].async_refresh()
    assert aioclient_mock.call_count == 4
    assert all(str(call[1]) != ECHONETLITE_URL for call in aioclient_mock.mock_calls)


@pytest.mark.parametrize("properties", [NORMAL_WATER_HEATER, {"ff": "41"}])
async def test_five_minute_poll_and_unload(hass, aioclient_mock, config_entry, freezer, properties):
    """通常周期で追加取得せず5分で取得し、解除で停止。 / Poll at five minutes and stop on unload."""
    coordinator = await setup_heater(hass, aioclient_mock, config_entry, properties)
    start = dt_util.utcnow()
    def extra_calls():
        return sum(str(call[1]) == ECHONETLITE_URL for call in aioclient_mock.mock_calls)
    assert extra_calls() == 1
    # HA の丸めとテスト時計の余裕を境界の前後に取る。
    # Allow for HA's timer rounding and the test clock's half-second tolerance.
    for seconds, expected in [(61, 1), (298, 1), (301, 2), (602, 3)]:
        freezer.move_to(start + timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert extra_calls() == expected
    assert coordinator.update_interval == timedelta(seconds=300)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    before = aioclient_mock.call_count
    freezer.move_to(start + timedelta(seconds=1000))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == before


@pytest.mark.parametrize(("response", "status"), [
    ({"message": "unavailable"}, 503), ([], 200), ({}, 200), ({"appliances": None}, 200),
    ({"appliances": [None]}, 200), ({"appliances": [{"id": "heater-1", "properties": {}}]}, 200),
])
async def test_failed_poll_preserves_snapshot_and_recovers(hass, aioclient_mock, config_entry, response, status):
    """不正な応答でも前回値を破壊せず復帰できる。 / Preserve the snapshot on errors and recover."""
    coordinator = await setup_heater(hass, aioclient_mock, config_entry)
    before = deepcopy(coordinator.data)
    mock_echonetlite(aioclient_mock, response, status=status)
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data == before
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert hass.states.get(entity_id(hass, "85")).state == STATE_UNAVAILABLE
    mock_echonetlite(aioclient_mock, make_echonetlite())
    await coordinator.async_refresh()
    assert hass.states.get(entity_id(hass, "85")).state == "1234.567"


async def test_removed_heater_stops_extra_requests(hass, aioclient_mock, config_entry):
    """通常の一覧から消えた機器のために取得しない。 / Stop fetching for removed heaters."""
    coordinator = await setup_heater(hass, aioclient_mock, config_entry)
    main = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    mock_api(aioclient_mock, devices=[make_device({})], appliances=[])
    await main.async_refresh()
    count = aioclient_mock.call_count
    await coordinator.async_refresh()
    assert coordinator.data == {}
    assert aioclient_mock.call_count == count
    assert hass.states.get(entity_id(hass, "85")).state == STATE_UNAVAILABLE


async def test_rate_limit_keeps_snapshot_and_requests_backoff(hass, aioclient_mock, config_entry):
    """429 の解除時刻を coordinator に渡す。 / Honor rate-limit reset times."""
    coordinator = await setup_heater(hass, aioclient_mock, config_entry)
    before = deepcopy(coordinator.data)
    mock_echonetlite(aioclient_mock, {}, status=429, headers={"X-Rate-Limit-Reset": str(int(time.time()) + 400)})
    await coordinator.async_refresh()
    assert coordinator.data == before
    assert coordinator.last_exception.retry_after > 350


async def test_auth_failure_starts_reauth(hass, aioclient_mock, config_entry):
    """追加 API の認証失敗も再認証を開始。 / Start reauth for authentication failures on the extra endpoint."""
    coordinator = await setup_heater(hass, aioclient_mock, config_entry)
    before = deepcopy(coordinator.data)
    mock_echonetlite(aioclient_mock, {}, status=401)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data == before
    assert [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()] == [SOURCE_REAUTH]


async def test_mixed_response_filters_meters_and_empty_snapshot_is_valid(hass, aioclient_mock, config_entry):
    """同居するメーターを除外し正当な空一覧を反映。 / Exclude meters and accept a valid empty list."""
    response = make_echonetlite()
    response["appliances"].append({
        "id": "meter-1", "type": "EL_SMART_METER", "nickname": "Meter", "Device": make_device({}),
        "properties": [{"epc": "85", "val": "00000000"}],
    })
    mock_api(aioclient_mock, appliances=[make_water_heater(), make_meter(NORMAL_METER)], echonetlite=response)
    await setup(hass, config_entry)
    coordinator = coordinator_of(hass, config_entry)
    assert set(coordinator.data) == {"heater-1"}
    meter_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "nature_remo_sensor_meter-1_buy_power")
    assert float(hass.states.get(meter_id).state) == 56224.8
    mock_echonetlite(aioclient_mock, {"appliances": []})
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data == {}
    assert hass.states.get(entity_id(hass, "85")).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(("language", "name", "state"), [
    ("en", "Bath operation", "Keeping warm"), ("ja", "風呂の動作状態", "保温中"),
])
async def test_entity_and_enum_translations_load(hass, aioclient_mock, config_entry, language, name, state):
    """英日でエンティティ名と列挙状態の翻訳を読み込む。 / Load entity and enum translations in both languages."""
    await setup_heater(hass, aioclient_mock, config_entry)
    translations = await async_get_translations(hass, language, "entity", {DOMAIN})
    prefix = "component.nature_remo.entity.sensor.water_heater_bath_operation"
    assert translations[f"{prefix}.name"] == name
    assert translations[f"{prefix}.state.keeping_warm"] == state


@pytest.mark.parametrize(("response", "status"), [({}, 503), ({"appliances": None}, 200)])
async def test_initial_failure_does_not_block_normal_entities_and_recovers(hass, aioclient_mock, config_entry, freezer, response, status):
    """追加取得が失敗しても通常機能を起動し最初の成功時に追加。 / Start normal entities and add heater entities on recovery."""
    aioclient_mock.get(DEVICES_URL, json=[make_device()])
    aioclient_mock.get(APPLIANCES_URL, json=[make_aircon(), make_water_heater()])
    aioclient_mock.get(ECHONETLITE_URL, json=response, status=status)
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    coordinator = coordinator_of(hass, config_entry)
    assert not coordinator.last_update_success
    assert coordinator.data == {}
    assert entity_id(hass, "85") is None
    climate_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, "nature_remo_climate_aircon-1")
    assert hass.states.get(climate_id).state == "cool"
    mock_api(aioclient_mock, appliances=[make_aircon(), make_water_heater()], echonetlite=make_echonetlite())
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(climate_id).state == "cool"
    assert hass.states.get(entity_id(hass, "85")).state == "1234.567"
    assert hass.states.get(entity_id(hass, "80")).state == "on"
    assert all(entity_id(hass, epc) is not None for epc in NORMAL_WATER_HEATER)
    assert sum(str(call[1]) == ECHONETLITE_URL for call in aioclient_mock.mock_calls) == 1
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len([entry for entry in er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
                if entry.unique_id.startswith("nature_remo_water_heater_")]) == 9


async def test_initial_auth_failure_still_starts_reauth(hass, aioclient_mock, config_entry):
    """初回の認証失敗は隠さず再認証する。 / Do not suppress initial authentication failures."""
    aioclient_mock.get(DEVICES_URL, json=[make_device()])
    aioclient_mock.get(APPLIANCES_URL, json=[make_water_heater()])
    aioclient_mock.get(ECHONETLITE_URL, status=401, json={})
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()] == [SOURCE_REAUTH]


async def test_unload_after_initial_failure_removes_pending_discovery(hass, aioclient_mock, config_entry, freezer):
    """初回取得を待つリスナーも解除。 / Remove discovery listeners while awaiting the first snapshot."""
    mock_echonetlite(aioclient_mock, {}, status=503)
    await setup(hass, config_entry)
    coordinator = coordinator_of(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    count = aioclient_mock.call_count
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == count
    assert not coordinator._listeners
