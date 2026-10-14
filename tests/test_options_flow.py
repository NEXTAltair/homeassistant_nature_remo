"""外部センサーの設定と再読み込み。 / External sensor settings and reloads."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.config_validation import custom_serializer
import probatio
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nature_remo.const import DOMAIN
from .conftest import make_aircon, make_device, mock_api
from .test_coordinator import setup


async def start(hass, aioclient_mock, config_entry, *, aircon=True):
    """二台の本体と一台のエアコンを設定。 / Set up two devices and one optional AC."""
    second = {**make_device({}), "id": "device-2", "name": "Remo E"}
    mock_api(aioclient_mock, devices=[make_device(), second], appliances=[make_aircon()] if aircon else [])
    await setup(hass, config_entry)
    devices = dr.async_get(hass)
    parent = devices.async_get_device_by_identifier((DOMAIN, "device-1"), config_entry.entry_id)
    other = devices.async_get_device_by_identifier((DOMAIN, "device-2"), config_entry.entry_id)
    flow = await hass.config_entries.options.async_init(config_entry.entry_id)
    return flow, parent, other


async def select(hass, flow, device):
    """選んだ本体のフォームへ進む。 / Open the selected hardware form."""
    return await hass.config_entries.options.async_configure(flow["flow_id"], {"update_interval": 90, "device": device.id})


async def test_sections_and_translation_keys(hass, aioclient_mock, config_entry):
    """本体別の欄と静的な翻訳キー。 / Device-specific fields and static translation keys."""
    flow, parent, other = await start(hass, aioclient_mock, config_entry)
    for device, fields in [(parent, {"ip_address", "external_temperature", "external_humidity"}), (other, {"ip_address"})]:
        flow = await hass.config_entries.options.async_init(config_entry.entry_id)
        result = await select(hass, flow, device)
        serialized = probatio.to_field_list(result["data_schema"], custom_serializer=custom_serializer)
        assert len(serialized) == 1
        assert serialized[0]["name"] == "device"
        assert serialized[0]["type"] == "expandable"
        assert {field["name"] for field in serialized[0]["schema"]} == fields
        assert result["description_placeholders"] == {"device_name": device.name}
        for language in ("en", "ja"):
            translations = json.loads(Path(f"custom_components/nature_remo/translations/{language}.json").read_text())
            section = translations["options"]["step"]["device"]["sections"]["device"]
            assert fields <= section["data"].keys()


@pytest.mark.parametrize(("kind", "value", "unit", "error"), [
    ("temperature", None, "°C", "entity_not_found"),
    ("temperature", "unknown", "°C", "invalid_number"),
    ("temperature", "unavailable", "°C", "invalid_number"),
    ("temperature", "nan", "°C", "invalid_number"),
    ("temperature", "inf", "°C", "invalid_number"),
    ("temperature", "23", "%", "invalid_unit"),
    ("humidity", "50", "°C", "invalid_unit"),
    ("humidity", "50", None, "invalid_unit"),
])
async def test_invalid_sensor_selection(hass, aioclient_mock, config_entry, kind, value, unit, error):
    """誤った選択を保存しない。 / Reject invalid selections without saving."""
    flow, parent, _ = await start(hass, aioclient_mock, config_entry)
    flow = await select(hass, flow, parent)
    if value is not None:
        hass.states.async_set("sensor.external", value, {"unit_of_measurement": unit})
    field = f"external_{kind}"
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"device": {field: "sensor.external"}})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"device": f"{kind}_{error}"}
    rendered_fields = {field["name"] for field in probatio.to_field_list(result["data_schema"], custom_serializer=custom_serializer)}
    assert result["errors"].keys() <= rendered_fields
    assert not config_entry.options


@pytest.mark.parametrize("temperature_unit", ["°C", "°F"])
async def test_valid_selection_and_automatic_reload(hass, aioclient_mock, config_entry, temperature_unit):
    """単位を検証して保存し、一度だけ再読み込みする。 / Save valid units and reload exactly once."""
    flow, parent, _ = await start(hass, aioclient_mock, config_entry)
    flow = await select(hass, flow, parent)
    hass.states.async_set("sensor.external_temp", "0", {"unit_of_measurement": temperature_unit})
    hass.states.async_set("sensor.external_humidity", "50", {"unit_of_measurement": "%"})
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock(return_value=True)) as reload:
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"device": {"ip_address": "192.0.2.1", "external_temperature": "sensor.external_temp", "external_humidity": "sensor.external_humidity"}})
        await hass.async_block_till_done()
        reload.assert_awaited_once_with(config_entry.entry_id)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(config_entry.options) == {"update_interval": 90, parent.id: "192.0.2.1", "external_temperature_device-1": "sensor.external_temp", "external_humidity_device-1": "sensor.external_humidity"}
    assert hass.states.get("persistent_notification.nature_remo_reload_needed") is None


async def test_legacy_options_and_clear_selection(hass, aioclient_mock, config_entry):
    """既存値の表示・解除・他本体の保存値維持。 / Suggest legacy values, clear choices and preserve other devices."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "token"}, options={"update_interval": 30, "external_temperature_device-1": "sensor.old_temp", "external_humidity_device-1": "sensor.old_humidity", "external_temperature_device-2": "sensor.keep", "legacy_other_key": "keep"})
    flow, parent, _ = await start(hass, aioclient_mock, config_entry)
    assert flow["data_schema"]({})["update_interval"] == 30
    flow = await select(hass, flow, parent)
    fields = flow["data_schema"].schema["device"].schema.schema
    assert {str(key): key.description.get("suggested_value") for key in fields}["external_temperature"] == "sensor.old_temp"
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock(return_value=True)):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"device": {}})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "external_temperature_device-1" not in config_entry.options
    assert "external_humidity_device-1" not in config_entry.options
    assert config_entry.options["external_temperature_device-2"] == "sensor.keep"
    assert config_entry.options["legacy_other_key"] == "keep"


async def test_interval_only_preserves_device_options(hass, aioclient_mock, config_entry):
    """本体未選択では共通設定だけ変更。 / Update only general settings without selecting hardware."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "token"}, options={"external_temperature_device-1": "sensor.keep"})
    flow, _, _ = await start(hass, aioclient_mock, config_entry)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock(return_value=True)):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"update_interval": 30})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options["external_temperature_device-1"] == "sensor.keep"
    assert config_entry.options["update_interval"] == 30
