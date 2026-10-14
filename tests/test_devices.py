"""本体・家電のデバイス分離。 / Hardware and appliance device separation."""

from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.nature_remo.const import DOMAIN
from .conftest import NORMAL_METER, make_aircon, make_device, make_ir, make_light, make_meter, mock_api
from .test_coordinator import setup


def appliances():
    """各種家電の架空データ。 / Synthetic appliances of each type."""
    aircon = make_aircon()
    aircon["model"] = {"manufacturer": "Hitachi", "name": "Example AC"}
    return [aircon, make_light(), make_ir(), make_meter(NORMAL_METER),
            {"id": "tv-1", "type": "TV", "nickname": "TV", "device": make_device()},
            {"id": "heater-1", "type": "EL_ELECTRIC_WATER_HEATER", "nickname": "Water heater", "device": make_device()}]


async def test_entities_attach_to_appliance_or_hardware(hass, aioclient_mock, config_entry, caplog):
    """正しいデバイスと親子関係を検証。 / Verify entity grouping and parent links."""
    device = make_device({"te": {"val": 24}, "hu": {"val": 50}, "il": {"val": 100},
                          "mo": {"val": 1, "created_at": "2026-10-14T12:00:00Z"}})
    mock_api(aioclient_mock, devices=[device], appliances=appliances())
    await setup(hass, config_entry)
    assert not any(record.name == "homeassistant.helpers.frame" and record.levelno >= 30 for record in caplog.records)
    devices = dr.async_get(hass)
    registry = er.async_get(hass)
    hardware = devices.async_get_device_by_identifier((DOMAIN, "device-1"), config_entry.entry_id)
    assert hardware.via_device_id is None
    for appliance in appliances():
        child = devices.async_get_device_by_identifier((DOMAIN, appliance["id"]), config_entry.entry_id)
        assert child.name == appliance["nickname"]
        assert child.via_device_id == hardware.id
        if model := appliance.get("model"):
            assert child.manufacturer == model["manufacturer"]
            assert child.model == model["name"]
    for domain, unique, device_id in [
        ("climate", "nature_remo_climate_aircon-1", "aircon-1"),
        ("number", "nature_remo_temperature_offset_aircon-1", "aircon-1"),
        ("light", "nature_remo_light_light-1", "light-1"),
        ("remote", "nature_remo_remote_ir-1", "ir-1"),
        ("sensor", "nature_remo_sensor_meter-1_buy_power", "meter-1"),
        ("sensor", "nature_remo_sensor_device-1_te", "device-1"),
        ("sensor", "nature_remo_sensor_device-1_hu", "device-1"),
        ("sensor", "nature_remo_sensor_device-1_il", "device-1"),
        ("sensor", "device-1_last_motion", "device-1"),
        ("binary_sensor", "device-1_motion", "device-1"),
    ]:
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique)
        assert entity_id is not None
        assert registry.async_get(entity_id).device_id == devices.async_get_device_by_identifier((DOMAIN, device_id), config_entry.entry_id).id
        assert hass.states.get(entity_id) is not None


async def test_existing_entity_ids_are_preserved(hass, aioclient_mock, config_entry):
    """既存の entity_id を変えずに所属を移す。 / Move devices without changing entity IDs."""
    config_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    parent = devices.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "device-1")}, name="Remo")
    registry = er.async_get(hass)
    old_ids = {}
    for domain, unique in [
        ("climate", "nature_remo_climate_aircon-1"), ("number", "nature_remo_temperature_offset_aircon-1"),
        ("light", "nature_remo_light_light-1"), ("remote", "nature_remo_remote_ir-1"),
        ("sensor", "nature_remo_sensor_meter-1_buy_power"),
    ]:
        old = registry.async_get_or_create(domain, DOMAIN, unique, config_entry=config_entry, device_id=parent.id, suggested_object_id=f"custom_{domain}")
        old_ids[old.entity_id] = old.unique_id
    mock_api(aioclient_mock, appliances=appliances())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for entity_id, unique in old_ids.items():
        migrated = registry.async_get(entity_id)
        assert migrated.unique_id == unique
        assert migrated.device_id != parent.id
        assert devices.async_get(migrated.device_id).via_device_id == parent.id
        assert hass.states.get(entity_id) is not None


async def test_sensorless_hardware_is_registered(hass, aioclient_mock, config_entry):
    """Remo E のような親は空のまま消えない。 / Keep a sensorless Remo E parent registered."""
    mock_api(aioclient_mock, devices=[make_device({})], appliances=[make_meter(NORMAL_METER)])
    await setup(hass, config_entry)
    devices = dr.async_get(hass)
    parent = devices.async_get_device_by_identifier((DOMAIN, "device-1"), config_entry.entry_id)
    assert parent.config_entry_id == config_entry.entry_id
    assert devices.async_get_device_by_identifier((DOMAIN, "meter-1"), config_entry.entry_id).via_device_id == parent.id
    assert not er.async_entries_for_device(er.async_get(hass), parent.id)
    await hass.config_entries.async_unload(config_entry.entry_id)
    mock_api(aioclient_mock, devices=[make_device({})], appliances=[])
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert devices.async_get(parent.id) is not None


async def test_options_show_hardware_only(hass, aioclient_mock, config_entry):
    """家電をオプション欄に重複表示しない。 / Exclude appliances from hardware options."""
    mock_api(aioclient_mock, appliances=appliances())
    await setup(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    keys = [str(key) for key in result["data_schema"].schema]
    assert len(keys) == 4
    assert all("Remo" in key for key in keys if "Interval" not in key)
