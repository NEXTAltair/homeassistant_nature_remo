"""外部センサーの中継とイベント追従。 / External sensor relays and event updates."""

from copy import deepcopy

import pytest
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nature_remo.const import DOMAIN
from .conftest import make_aircon, mock_api
from .test_coordinator import setup


TEMPERATURE_ATTRIBUTES = {"device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C"}
HUMIDITY_ATTRIBUTES = {"device_class": "humidity", "state_class": "measurement", "unit_of_measurement": "%"}


def relay_state(hass, kind="temperature", appliance_id="aircon-1"):
    """中継エンティティを unique_id で取得。 / Find the relay by its unique ID."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"nature_remo_external_{kind}_{appliance_id}")
    return hass.states.get(entity_id) if entity_id else None


async def setup_external(hass, aioclient_mock, options=None, appliances=None):
    """架空の設定で中継を準備。 / Set up relays with synthetic options."""
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "token"}, options=options or {}, unique_id="user-1")
    mock_api(aioclient_mock, appliances=appliances if appliances is not None else [make_aircon()])
    await setup(hass, entry)
    return entry


async def test_only_configured_kinds_have_relays(hass, aioclient_mock):
    """設定のない種類を作らない。 / Do not create unconfigured relay kinds."""
    hass.states.async_set("sensor.source", "25", TEMPERATURE_ATTRIBUTES)
    await setup_external(hass, aioclient_mock, {"external_temperature_device-1": "sensor.source"})
    assert relay_state(hass).state == "25.0"
    assert relay_state(hass, "humidity") is None


async def test_no_configuration_has_no_relays(hass, aioclient_mock):
    """未設定なら何も追加しない。 / Do not add relays without options."""
    await setup_external(hass, aioclient_mock)
    assert relay_state(hass) is None
    assert relay_state(hass, "humidity") is None


async def test_no_aircon_has_no_relays(hass, aioclient_mock):
    """AC がない本体には中継を作らない。 / Do not relay sensors without an AC."""
    await setup_external(hass, aioclient_mock, {"external_temperature_device-1": "sensor.source"}, appliances=[])
    assert relay_state(hass) is None


@pytest.mark.parametrize("unit", ["°C", "°F"])
async def test_source_values_units_classes_and_device(hass, aioclient_mock, unit, caplog):
    """値・クラス・単位と各 AC への所属。 / Values, classes, units and each AC device."""
    hass.states.async_set("sensor.temperature", "72.5", {**TEMPERATURE_ATTRIBUTES, "unit_of_measurement": unit})
    hass.states.async_set("sensor.humidity", "0", HUMIDITY_ATTRIBUTES)
    second = {**deepcopy(make_aircon()), "id": "aircon-2", "nickname": "Second AC"}
    entry = await setup_external(hass, aioclient_mock, {"external_temperature_device-1": "sensor.temperature", "external_humidity_device-1": "sensor.humidity"}, [make_aircon(), second])
    for appliance_id in ("aircon-1", "aircon-2"):
        parent = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, appliance_id), entry.entry_id)
        for kind, expected_value, expected_unit in [("temperature", "72.5", unit), ("humidity", "0.0", "%")]:
            state = relay_state(hass, kind, appliance_id)
            assert state.state == expected_value
            assert state.attributes["unit_of_measurement"] == expected_unit
            assert state.attributes["device_class"] == kind
            assert state.attributes["state_class"] == "measurement"
            assert state.attributes["source_entity_id"] == f"sensor.{kind}"
            assert er.async_get(hass).async_get(state.entity_id).device_id == parent.id
    assert aioclient_mock.call_count == 2
    assert not any(record.name == "homeassistant.helpers.frame" and record.levelno >= 30 for record in caplog.records)


@pytest.mark.parametrize("value", ["unavailable", "unknown", "not-a-number", "nan", "inf", "-inf", None])
async def test_invalid_source_then_recovery(hass, aioclient_mock, value):
    """欠測を unavailable にし、状態変化で即復帰。 / Mark missing as unavailable and recover immediately."""
    if value is not None:
        hass.states.async_set("sensor.source", value, TEMPERATURE_ATTRIBUTES)
    await setup_external(hass, aioclient_mock, {"external_temperature_device-1": "sensor.source"})
    assert relay_state(hass).state == "unavailable"
    hass.states.async_set("sensor.source", "0", TEMPERATURE_ATTRIBUTES)
    await hass.async_block_till_done()
    assert relay_state(hass).state == "0.0"
    assert aioclient_mock.call_count == 2


async def test_source_changes_metadata_removal_and_unload(hass, aioclient_mock):
    """値と属性の追従、削除と監視の解除。 / Follow value and metadata changes, deletion and unload."""
    hass.states.async_set("sensor.source", "21", TEMPERATURE_ATTRIBUTES)
    entry = await setup_external(hass, aioclient_mock, {"external_temperature_device-1": "sensor.source"})
    entity_id = relay_state(hass).entity_id
    hass.states.async_set("sensor.source", "23.5", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    state = relay_state(hass)
    assert state.state == "23.5"
    assert "device_class" not in state.attributes
    assert "state_class" not in state.attributes
    hass.states.async_remove("sensor.source")
    await hass.async_block_till_done()
    assert relay_state(hass).state == "unavailable"
    hass.states.async_set("sensor.source", "25", TEMPERATURE_ATTRIBUTES)
    await hass.async_block_till_done()
    assert relay_state(hass).state == "25.0"
    await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set("sensor.source", "30", TEMPERATURE_ATTRIBUTES)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize("kind", ["temperature", "humidity"])
async def test_options_reject_own_and_other_relays(hass, aioclient_mock, kind):
    """中継自身や別 AC の中継を選択できない。 / Reject self and other AC relay sources."""
    attributes = TEMPERATURE_ATTRIBUTES if kind == "temperature" else HUMIDITY_ATTRIBUTES
    hass.states.async_set("sensor.source", "25", attributes)
    second = {**deepcopy(make_aircon()), "id": "aircon-2", "nickname": "Second AC"}
    entry = await setup_external(hass, aioclient_mock, {f"external_{kind}_device-1": "sensor.source"}, [make_aircon(), second])
    parent = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "device-1"), entry.entry_id)
    for appliance_id in ("aircon-1", "aircon-2"):
        source_id = relay_state(hass, kind, appliance_id).entity_id
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"update_interval": 60, "device": parent.id})
        fields = flow["data_schema"].schema["device"].schema.schema
        assert source_id in fields[f"external_{kind}"].config["exclude_entities"]
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(flow["flow_id"], {"device": {f"external_{kind}": source_id}})
        # フォームを開いた後の改名も保存時の検証で拒否する。
        # Also reject a relay renamed after the form's exclusions were generated.
        renamed_id = f"sensor.renamed_{kind}_{appliance_id.replace('-', '_')}"
        er.async_get(hass).async_update_entity(source_id, new_entity_id=renamed_id)
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"device": {f"external_{kind}": renamed_id}})
        assert result["errors"] == {"device": f"{kind}_relay_source"}
        assert entry.options[f"external_{kind}_device-1"] == "sensor.source"


async def test_saved_relay_cycle_is_not_created(hass, aioclient_mock):
    """保存済みの循環でも値を中継しない。 / Do not instantiate relays from a saved cycle."""
    hass.states.async_set("sensor.source", "25", TEMPERATURE_ATTRIBUTES)
    second = deepcopy(make_aircon())
    second.update(id="aircon-2", nickname="Second AC")
    second["device"]["id"] = "device-2"
    entry = await setup_external(hass, aioclient_mock, {"external_temperature_device-1": "sensor.source", "external_temperature_device-2": "sensor.source"}, [make_aircon(), second])
    first_id = relay_state(hass).entity_id
    second_id = relay_state(hass, appliance_id="aircon-2").entity_id
    hass.config_entries.async_update_entry(entry, options={"external_temperature_device-1": second_id, "external_temperature_device-2": first_id})
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][entry.entry_id]["external_sensors"] == {}
    assert hass.states.get(first_id).state == "unavailable"
    assert hass.states.get(second_id).state == "unavailable"
