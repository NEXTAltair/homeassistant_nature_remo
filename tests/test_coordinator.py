"""Tests for the Nature Remo coordinator, setup and entity state."""

from __future__ import annotations

import time

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.nature_remo.api import NATURE_REMO_URL
from custom_components.nature_remo.const import DOMAIN
from custom_components.nature_remo.coordinator import NatureRemoCoordinator

from .conftest import NORMAL_METER, make_aircon, make_device, make_meter, mock_api

BUY = "nature_remo_sensor_meter-1_buy_power"
SOLD = "nature_remo_sensor_meter-1_sold_power"


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def coordinator_of(hass: HomeAssistant, entry: MockConfigEntry) -> NatureRemoCoordinator:
    """The coordinator of a loaded entry."""
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> NatureRemoCoordinator:
    """Poll once and let entities write their state."""
    coordinator = coordinator_of(hass, entry)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    return coordinator


def state(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    """State of the entity with the given unique ID."""
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return hass.states.get(entity_id).state


async def test_meter_missing_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """normal -> missing (0xFFFFFFFE) -> normal never yields a spike or a fake 0."""
    mock_api(aioclient_mock, appliances=[make_meter(NORMAL_METER)])
    await setup(hass, config_entry)
    assert float(state(hass, "sensor", BUY)) == 56224.8
    assert float(state(hass, "sensor", SOLD)) == 19380.1

    missing = {**NORMAL_METER, 224: "4294967294", 227: "4294967294"}
    mock_api(aioclient_mock, appliances=[make_meter(missing)])
    await refresh(hass, config_entry)
    assert state(hass, "sensor", BUY) == STATE_UNKNOWN
    assert state(hass, "sensor", SOLD) == STATE_UNKNOWN
    assert float(state(hass, "sensor", "nature_remo_sensor_meter-1_current_power")) == -888

    mock_api(aioclient_mock, appliances=[make_meter({**NORMAL_METER, 224: "562250"})])
    await refresh(hass, config_entry)
    assert float(state(hass, "sensor", BUY)) == 56225.0
    assert float(state(hass, "sensor", SOLD)) == 19380.1


@pytest.mark.parametrize("bad_devices", [None, {"unexpected": True}, "text"])
async def test_unexpected_response_keeps_previous_snapshot(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    bad_devices: object,
) -> None:
    """A response that is not a list fails the update without wiping known data."""
    mock_api(aioclient_mock, appliances=[make_meter(NORMAL_METER)])
    await setup(hass, config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{NATURE_REMO_URL}/devices", json=bad_devices)
    aioclient_mock.get(f"{NATURE_REMO_URL}/appliances", json=[])
    coordinator = await refresh(hass, config_entry)

    assert not coordinator.last_update_success
    assert coordinator.smart_meters["meter-1"]["buy_power"] == 56224.8
    assert state(hass, "sensor", BUY) == STATE_UNAVAILABLE


async def test_empty_lists_are_a_valid_snapshot(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Empty lists are a successful update, unlike a failed request."""
    mock_api(aioclient_mock, appliances=[make_meter(NORMAL_METER)])
    await setup(hass, config_entry)

    mock_api(aioclient_mock, devices=[], appliances=[])
    coordinator = await refresh(hass, config_entry)

    assert coordinator.last_update_success
    assert coordinator.smart_meters == {}
    assert coordinator.devices == {}


async def test_server_error_marks_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A 5xx response fails the update instead of crashing on None."""
    mock_api(aioclient_mock, appliances=[make_meter(NORMAL_METER)])
    await setup(hass, config_entry)

    mock_api(aioclient_mock, devices={}, appliances={}, status=503)
    coordinator = await refresh(hass, config_entry)

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert state(hass, "sensor", BUY) == STATE_UNAVAILABLE


async def test_rate_limit_defers_next_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """429 fails the update and asks the coordinator to wait until the reset."""
    mock_api(aioclient_mock)
    await setup(hass, config_entry)

    reset = int(time.time()) + 200
    mock_api(
        aioclient_mock,
        devices={},
        appliances={},
        status=429,
        headers={"X-Rate-Limit-Reset": str(reset)},
    )
    coordinator = await refresh(hass, config_entry)

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.retry_after > 150


async def test_auth_error_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A revoked token stops setup and asks the user for a new one."""
    mock_api(aioclient_mock, devices={}, appliances={}, status=401)
    await setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_device_events_follow_latest_snapshot(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Motion data and sensor events that disappear are not kept from old polls."""
    events = {
        "te": {"val": 24.5},
        "hu": {"val": 55},
        "mo": {"val": 1, "created_at": "2026-10-14T04:00:00Z"},
    }
    mock_api(aioclient_mock, devices=[make_device(events)])
    await setup(hass, config_entry)
    coordinator = coordinator_of(hass, config_entry)
    assert "device-1" in coordinator.motion_sensors
    assert float(state(hass, "sensor", "nature_remo_sensor_device-1_hu")) == 55

    mock_api(aioclient_mock, devices=[make_device({"te": {"val": 24.0}})])
    await refresh(hass, config_entry)

    assert coordinator.motion_sensors == {}
    assert state(hass, "sensor", "nature_remo_sensor_device-1_hu") == STATE_UNKNOWN
    assert float(state(hass, "sensor", "nature_remo_sensor_device-1_te")) == 24.0


async def test_failed_aircon_command_keeps_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A failed command reports an error and does not show the requested state."""
    mock_api(aioclient_mock, appliances=[make_aircon()])
    await setup(hass, config_entry)
    entity_id = er.async_get(hass).async_get_entity_id(
        "climate", DOMAIN, "nature_remo_climate_aircon-1"
    )
    assert hass.states.get(entity_id).state == HVACMode.COOL

    aioclient_mock.post(
        f"{NATURE_REMO_URL}/appliances/aircon-1/aircon_settings",
        status=500,
        json={"message": "failed"},
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": HVACMode.OFF},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == HVACMode.COOL
