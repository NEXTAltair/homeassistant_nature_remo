"""中継経由の climate 測定値。 / Climate readings through external relays."""

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nature_remo.const import DOMAIN
from .conftest import make_aircon, make_device, mock_api
from .test_climate import entity_id
from .test_coordinator import setup
from .test_external_sensor import HUMIDITY_ATTRIBUTES, TEMPERATURE_ATTRIBUTES, relay_state


async def setup_climate(hass, aioclient_mock, *, external=True, fahrenheit=False):
    """内蔵の温湿度と任意の外部設定を用意。 / Set up hardware readings and optional external sources."""
    options = {"external_temperature_device-1": "sensor.temperature", "external_humidity_device-1": "sensor.humidity"} if external else {}
    entry = MockConfigEntry(domain=DOMAIN, data={"api_key": "token"}, options=options)
    appliance = make_aircon()
    if fahrenheit:
        appliance["aircon"]["tempUnit"] = "f"
        appliance["aircon"]["range"]["modes"]["cool"]["temp"] = ["68", "77", "86"]
        appliance["settings"].update(temp="77", temp_unit="f")
    device = make_device({"te": {"val": 24.5}, "hu": {"val": 40}})
    mock_api(aioclient_mock, devices=[device], appliances=[appliance])
    await setup(hass, entry)
    return entry


def readings(hass):
    """HA の公開状態から測定値を取得。 / Read measurements from the public HA state."""
    attrs = hass.states.get(entity_id(hass, "climate")).attributes
    return attrs["current_temperature"], attrs["current_humidity"]


async def test_without_external_uses_hardware(hass, aioclient_mock):
    """未設定なら内蔵値を表示。 / Display hardware values without configured sources."""
    await setup_climate(hass, aioclient_mock, external=False)
    assert readings(hass) == (24.5, 40)
    assert relay_state(hass) is None


@pytest.mark.parametrize("fahrenheit", [False, True])
@pytest.mark.parametrize(("value", "unit"), [(25, "°C"), (77, "°F")])
async def test_uses_relay_and_converts_temperature(hass, aioclient_mock, fahrenheit, value, unit):
    """中継の単位を climate に合わせる。 / Convert relay units to the climate's native unit."""
    hass.states.async_set("sensor.temperature", str(value), {**TEMPERATURE_ATTRIBUTES, "unit_of_measurement": unit})
    hass.states.async_set("sensor.humidity", "55", HUMIDITY_ATTRIBUTES)
    await setup_climate(hass, aioclient_mock, fahrenheit=fahrenheit)
    assert readings(hass) == (25, 55)
    assert float(relay_state(hass).state) == value
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize("bad_value", ["unavailable", "unknown", "nan", "inf", "bad", None])
async def test_unavailable_relay_falls_back_then_recovers(hass, aioclient_mock, bad_value):
    """無効な外部値は内蔵へ戻り、ゼロの復帰も即反映。 / Fall back for invalid values and immediately recover to zero."""
    if bad_value is not None:
        hass.states.async_set("sensor.temperature", bad_value, TEMPERATURE_ATTRIBUTES)
        hass.states.async_set("sensor.humidity", bad_value, HUMIDITY_ATTRIBUTES)
    await setup_climate(hass, aioclient_mock)
    assert readings(hass) == (24.5, 40)
    assert relay_state(hass).state == "unavailable"
    hass.states.async_set("sensor.temperature", "0", TEMPERATURE_ATTRIBUTES)
    hass.states.async_set("sensor.humidity", "0", HUMIDITY_ATTRIBUTES)
    await hass.async_block_till_done()
    assert readings(hass) == (0, 0)
    hass.states.async_remove("sensor.temperature")
    hass.states.async_remove("sensor.humidity")
    await hass.async_block_till_done()
    assert readings(hass) == (24.5, 40)
    assert aioclient_mock.call_count == 2


async def test_relay_rename_preserves_climate_updates(hass, aioclient_mock):
    """中継を改名しても監視が続く。 / Keep updates after renaming the relay entity."""
    hass.states.async_set("sensor.temperature", "21", TEMPERATURE_ATTRIBUTES)
    hass.states.async_set("sensor.humidity", "50", HUMIDITY_ATTRIBUTES)
    await setup_climate(hass, aioclient_mock)
    er.async_get(hass).async_update_entity(relay_state(hass).entity_id, new_entity_id="sensor.renamed_relay")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.temperature", "23", TEMPERATURE_ATTRIBUTES)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.renamed_relay").state == "23.0"
    assert readings(hass) == (23, 50)
    assert aioclient_mock.call_count == 2


async def test_disabled_relay_falls_back_and_unload_removes_listeners(hass, aioclient_mock):
    """無効化時は本体へ戻り、unload で監視を解放。 / Fall back when disabled and release subscriptions on unload."""
    hass.states.async_set("sensor.temperature", "21", TEMPERATURE_ATTRIBUTES)
    hass.states.async_set("sensor.humidity", "50", HUMIDITY_ATTRIBUTES)
    entry = await setup_climate(hass, aioclient_mock)
    er.async_get(hass).async_update_entity(relay_state(hass).entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert readings(hass) == (24.5, 50)
    hass.states.async_set("sensor.temperature", "30", TEMPERATURE_ATTRIBUTES)
    await hass.async_block_till_done()
    assert readings(hass) == (24.5, 50)
    climate_id = entity_id(hass, "climate")
    await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set("sensor.humidity", "60", HUMIDITY_ATTRIBUTES)
    await hass.async_block_till_done()
    assert hass.states.get(climate_id).state == "unavailable"
