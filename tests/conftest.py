"""Shared fixtures for the Nature Remo tests."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.nature_remo.api import NATURE_REMO_URL
from custom_components.nature_remo.const import DOMAIN

DEVICES_URL = f"{NATURE_REMO_URL}/devices"
APPLIANCES_URL = f"{NATURE_REMO_URL}/appliances"

# 実機の Remo E 応答（2026-10-14）と同じ形の ECHONET Lite プロパティ
# ECHONET Lite properties shaped like a real Remo E response (2026-10-14)
NORMAL_METER = {211: "1", 215: "6", 224: "562248", 225: "1", 227: "193801", 231: "-888"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/nature_remo in every test."""
    return


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Config entry with an access token."""
    return MockConfigEntry(domain=DOMAIN, title="Nature Remo", data={"api_key": "token"})


def make_device(events: dict[str, Any] | None = None) -> dict[str, Any]:
    """A Remo device as returned by GET /1/devices."""
    return {
        "id": "device-1",
        "name": "Remo",
        "firmware_version": "Remo/1.0",
        "newest_events": events if events is not None else {"te": {"val": 24.5}},
    }


def make_meter(values: dict[int, Any]) -> dict[str, Any]:
    """A smart meter appliance with the given EPC -> val pairs."""
    return {
        "id": "meter-1",
        "type": "EL_SMART_METER",
        "nickname": "Meter",
        "device": {"id": "device-1", "name": "Remo", "firmware_version": "Remo/1.0"},
        "smart_meter": {
            "echonetlite_properties": [
                {"epc": epc, "val": val} for epc, val in values.items()
            ]
        },
        "signals": [],
    }


def make_aircon() -> dict[str, Any]:
    """An air conditioner shaped like the Hitachi rar-9b1 preset (trimmed)."""
    temps = [str(t / 2) for t in range(32, 65)]  # 16.0 ... 32.0
    mode = {"temp": temps, "dir": ["1", "auto", "swing"], "dirh": ["1", "swing"], "vol": ["1", "auto"]}
    return {
        "id": "aircon-1",
        "type": "AC",
        "nickname": "Aircon",
        "device": {"id": "device-1", "name": "Remo", "firmware_version": "Remo/1.0"},
        "aircon": {
            "range": {"modes": {"cool": mode, "warm": mode}, "fixedButtons": ["power-off"]},
            "tempUnit": "c",
        },
        "settings": {
            "temp": "26",
            "temp_unit": "c",
            "mode": "cool",
            "vol": "auto",
            "dir": "auto",
            "dirh": "1",
            "button": "",
        },
        "signals": [],
    }


def mock_api(
    aioclient_mock: AiohttpClientMocker,
    devices: Any = None,
    appliances: Any = None,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    """Register GET /devices and GET /appliances responses (replacing earlier ones)."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        DEVICES_URL,
        status=status,
        json=[make_device()] if devices is None else devices,
        headers=headers,
    )
    aioclient_mock.get(
        APPLIANCES_URL,
        status=status,
        json=[] if appliances is None else appliances,
        headers=headers,
    )
