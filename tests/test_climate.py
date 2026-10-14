"""エアコン v2 制御の回帰テスト。 / Regression tests for v2 aircon control."""

from copy import deepcopy

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.nature_remo.const import DOMAIN
from .conftest import make_aircon, mock_api
from .test_coordinator import setup

URL = "https://api.nature.global/2/appliances/aircon-1/aircon_settings"


def entity_id(hass, domain):
    """安定した ID から検索。 / Resolve the stable unique ID."""
    unique = "nature_remo_climate_aircon-1" if domain == "climate" else "nature_remo_temperature_offset_aircon-1"
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique)


async def call(hass, service, data=None, domain="climate"):
    """HA の公開サービスを実行。 / Call the public HA service."""
    await hass.services.async_call(domain, service, {"entity_id": entity_id(hass, domain), **(data or {})}, blocking=True)
    await hass.async_block_till_done()


@pytest.mark.parametrize(("mode", "temp", "volume"), [
    (HVACMode.COOL, "26.0", "auto"), (HVACMode.HEAT, "26.0", "auto"),
    (HVACMode.AUTO, "0", "auto"), (HVACMode.DRY, "0", "auto"),
    (HVACMode.FAN_ONLY, "", "1"),
])
async def test_modes_send_all_v2_fields(hass, aioclient_mock, config_entry, mode, temp, volume):
    """各モードの全項目と応答を検証。 / Verify all fields and response for each mode."""
    appliance = make_aircon()
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    remo_mode = {HVACMode.HEAT: "warm", HVACMode.FAN_ONLY: "blow"}.get(mode, str(mode))
    response = deepcopy(appliance)
    response["settings"].update(mode=remo_mode, temp=temp, vol=volume)
    aioclient_mock.post(URL, json=response)
    await call(hass, "set_hvac_mode", {"hvac_mode": mode})
    assert aioclient_mock.mock_calls[-1][2] == {
        "operation_mode": remo_mode, "temperature": temp, "temperature_unit": "c",
        "air_volume": volume, "air_direction": "auto", "air_direction_h": "1", "button": "",
    }
    state = hass.states.get(entity_id(hass, "climate"))
    assert state.state == mode
    if mode in (HVACMode.AUTO, HVACMode.DRY, HVACMode.FAN_ONLY):
        assert state.attributes.get("temperature") is None
        assert not state.attributes["supported_features"] & ClimateEntityFeature.TARGET_TEMPERATURE


@pytest.mark.parametrize(("service", "service_data", "field", "response_field", "returned"), [
    ("set_temperature", {"temperature": 25.5}, "temperature", "temp", "25"),
    ("set_fan_mode", {"fan_mode": "2"}, "air_volume", "vol", "1"),
    ("set_swing_mode", {"swing_mode": "swing"}, "air_direction", "dir", "2"),
    ("set_swing_horizontal_mode", {"swing_horizontal_mode": "swing"}, "air_direction_h", "dirh", "3"),
])
async def test_commands_use_response(hass, aioclient_mock, config_entry, service, service_data, field, response_field, returned):
    """要求値ではなく応答を表示。 / Display confirmed response values, not requested ones."""
    appliance = make_aircon()
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    response = deepcopy(appliance)
    response["settings"][response_field] = returned
    aioclient_mock.post(URL, json=response)
    await call(hass, service, service_data)
    payload = aioclient_mock.mock_calls[-1][2]
    assert len(payload) == 7
    assert payload[field] == str(next(iter(service_data.values())))
    attribute = next(iter(service_data))
    value = hass.states.get(entity_id(hass, "climate")).attributes[attribute]
    assert value == (float(returned) if attribute == "temperature" else returned)


async def test_turn_on_uses_auto_and_remembered_offset(hass, aioclient_mock, config_entry):
    """補正値を保持し停止後は必ず自動で起動。 / Retain offset and always start in auto."""
    appliance = make_aircon()
    appliance["settings"].update(mode="dry", temp="-1.5")
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    number = entity_id(hass, "number")
    assert float(hass.states.get(number).state) == -1.5

    response = deepcopy(appliance)
    response["settings"].update(mode="warm", temp="26", button="power-off")
    aioclient_mock.post(URL, json=response)
    await call(hass, "turn_off")
    assert aioclient_mock.mock_calls[-1][2]["button"] == "power-off"
    assert hass.states.get(number).state == STATE_UNAVAILABLE
    assert hass.states.get(entity_id(hass, "climate")).state == HVACMode.OFF

    mock_api(aioclient_mock, appliances=[response])
    response["settings"].update(mode="auto", temp="-1.5", button="")
    aioclient_mock.post(URL, json=response)
    await call(hass, "turn_on")
    payload = aioclient_mock.mock_calls[-1][2]
    assert payload["operation_mode"] == "auto"
    assert payload["temperature"] == "-1.5"
    assert payload["button"] == ""
    assert float(hass.states.get(number).state) == -1.5
    count = aioclient_mock.call_count
    await call(hass, "turn_on")
    assert aioclient_mock.call_count == count


async def test_no_auto_disables_turn_on(hass, aioclient_mock, config_entry):
    """自動がなければ起動操作を公開しない。 / Do not expose turn on without auto."""
    appliance = make_aircon()
    appliance["aircon"]["range"]["modes"].pop("auto")
    appliance["settings"]["button"] = "power-off"
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    features = hass.states.get(entity_id(hass, "climate")).attributes["supported_features"]
    assert not features & ClimateEntityFeature.TURN_ON
    assert features & ClimateEntityFeature.TURN_OFF
    with pytest.raises(HomeAssistantError):
        await call(hass, "turn_on")


async def test_blank_swing_choices_are_not_exposed(hass, aioclient_mock, config_entry):
    """空文字のみの風向は非対応。 / A single empty swing option means unsupported."""
    appliance = make_aircon()
    appliance["aircon"]["range"]["modes"]["cool"].update(dir=[""], dirh=[""])
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    state = hass.states.get(entity_id(hass, "climate"))
    assert not state.attributes["supported_features"] & (ClimateEntityFeature.SWING_MODE | ClimateEntityFeature.SWING_HORIZONTAL_MODE)
    aioclient_mock.post(URL, json=appliance)
    await call(hass, "set_fan_mode", {"fan_mode": "1"})
    assert aioclient_mock.mock_calls[-1][2]["air_direction"] == ""
    assert aioclient_mock.mock_calls[-1][2]["air_direction_h"] == ""


@pytest.mark.parametrize("mode", ["auto", "dry", "cool", "warm", "blow"])
async def test_offset_availability_and_value(hass, aioclient_mock, config_entry, mode):
    """自動・除湿でだけ補正値を操作。 / Offset is available only in auto/dry."""
    appliance = make_aircon()
    appliance["settings"].update(mode=mode, temp="0" if mode in ("auto", "dry") else "26")
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    state = hass.states.get(entity_id(hass, "number"))
    if mode not in ("auto", "dry"):
        assert state.state == STATE_UNAVAILABLE
        return
    assert state.attributes["min"] == -3
    assert state.attributes["max"] == 3
    assert state.attributes["step"] == 0.5
    response = deepcopy(appliance)
    response["settings"]["temp"] = "2.5"
    aioclient_mock.post(URL, json=response)
    await call(hass, "set_value", {"value": 2.5}, "number")
    assert aioclient_mock.mock_calls[-1][2]["temperature"] == "2.5"
    assert float(hass.states.get(entity_id(hass, "number")).state) == 2.5


@pytest.mark.parametrize("invalid", [4, 0.25])
async def test_invalid_offset_not_sent(hass, aioclient_mock, config_entry, invalid):
    """範囲外・刻み外の値を送らない。 / Reject values outside the range or step."""
    appliance = make_aircon()
    appliance["settings"].update(mode="auto", temp="0")
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    count = aioclient_mock.call_count
    with pytest.raises(HomeAssistantError):
        await call(hass, "set_value", {"value": invalid}, "number")
    assert aioclient_mock.call_count == count


@pytest.mark.parametrize(("domain", "service", "data"), [
    ("climate", "turn_off", {}),
    ("climate", "set_fan_mode", {"fan_mode": "1"}),
    ("climate", "set_swing_mode", {"swing_mode": "swing"}),
    ("climate", "set_swing_horizontal_mode", {"swing_horizontal_mode": "swing"}),
    ("number", "set_value", {"value": -2.5}),
])
@pytest.mark.parametrize("status", [500, 200])
async def test_failure_preserves_state(hass, aioclient_mock, config_entry, domain, service, data, status):
    """HTTP エラーや不正応答では状態を変えない。 / Errors and malformed responses keep state."""
    appliance = make_aircon()
    appliance["settings"].update(mode="auto", temp="1.5")
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    before = {kind: hass.states.get(entity_id(hass, kind)) for kind in ("climate", "number")}
    aioclient_mock.post(URL, status=status, json={"message": "bad"})
    with pytest.raises(HomeAssistantError):
        await call(hass, service, data, domain)
    for kind, old in before.items():
        current = hass.states.get(entity_id(hass, kind))
        assert current.state == old.state
        assert current.attributes == old.attributes


async def test_absolute_auto_has_no_offset_number(hass, aioclient_mock, config_entry):
    """絶対温度の自動モードは通常の目標温度。 / Absolute auto uses the climate target."""
    appliance = make_aircon()
    modes = appliance["aircon"]["range"]["modes"]
    modes["auto"] = modes["cool"]
    modes.pop("dry")
    appliance["settings"]["mode"] = "auto"
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    assert entity_id(hass, "number") is None
    assert hass.states.get(entity_id(hass, "climate")).attributes["temperature"] == 26


async def test_temperature_unit_from_aircon(hass, aioclient_mock, config_entry):
    """API の華氏設定を送信に使う。 / Use the appliance Fahrenheit unit when sending."""
    appliance = make_aircon()
    appliance["aircon"]["tempUnit"] = "f"
    appliance["aircon"]["range"]["modes"]["cool"]["temp"] = ["68", "77", "86"]
    appliance["settings"].update(temp="77", temp_unit="f")
    mock_api(aioclient_mock, appliances=[appliance])
    await setup(hass, config_entry)
    # HA の表示はユーザーの単位に変換される。 / HA converts display to the user's unit.
    assert hass.states.get(entity_id(hass, "climate")).attributes["temperature"] == 25
    assert hass.states.get(entity_id(hass, "climate")).attributes["current_temperature"] == 24.5
    aioclient_mock.post(URL, json=appliance)
    await call(hass, "turn_off")
    assert aioclient_mock.mock_calls[-1][2]["temperature_unit"] == "f"
