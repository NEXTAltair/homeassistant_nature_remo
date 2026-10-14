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
ECHONETLITE_URL = f"{NATURE_REMO_URL}/echonetlite/appliances"
NORMAL_WATER_HEATER = {"80": "30", "85": "0012d687", "b0": "41", "b2": "42", "c0": "41",
                       "c3": "42", "e1": "0172", "e3": "41", "ea": "43"}

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
    mode = {"temp": temps, "dir": [str(i) for i in range(1, 8)] + ["auto", "swing"],
            "dirh": [str(i) for i in range(1, 10)] + ["swing"],
            "vol": [str(i) for i in range(1, 6)] + ["auto"]}
    offsets = [f"{i / 2:g}" for i in range(-6, 7)]
    modes = {
        "cool": mode, "warm": mode,
        "auto": {**mode, "temp": offsets, "vol": ["1", "2", "auto"]},
        "dry": {**mode, "temp": offsets},
        "blow": {**mode, "temp": [""], "vol": [str(i) for i in range(1, 6)]},
    }
    return {
        "id": "aircon-1",
        "type": "AC",
        "nickname": "Aircon",
        "device": {"id": "device-1", "name": "Remo", "firmware_version": "Remo/1.0"},
        "aircon": {
            "range": {"modes": modes, "fixedButtons": ["power-off"]},
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
    echonetlite: Any = None,
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
    if any(isinstance(appliance, dict) and appliance.get("type") == "EL_ELECTRIC_WATER_HEATER"
           for appliance in appliances or []):
        aioclient_mock.get(ECHONETLITE_URL, json=echonetlite if echonetlite is not None else {"appliances": []})


def make_light() -> dict[str, Any]:
    """架空の照明。 / A synthetic preset light."""
    return {
        "id": "light-1", "type": "LIGHT", "nickname": "Light",
        "device": make_device(),
        "model": {"manufacturer": "Example", "name": "Lamp 1"},
        "light": {"state": {"power": "on", "last_button": "on"},
                  "buttons": [{"name": name, "label": name} for name in ("on", "off", "night")]},
        "signals": [],
    }


def make_ir() -> dict[str, Any]:
    """架空の学習リモコン。 / A synthetic learned remote."""
    return {
        "id": "ir-1", "type": "IR", "nickname": "Fan", "device": make_device(),
        "model": None,
        "signals": [{"id": "signal-on", "name": "On"}, {"id": "signal-off", "name": "Off"}],
    }


def make_tv() -> dict[str, Any]:
    """実データと同じ形の架空の TV。 / A synthetic TV with the real response shape."""
    return {
        "id": "tv-1", "type": "TV", "nickname": "TV", "device": make_device(),
        "model": {"manufacturer": "Example", "name": "TV 1"},
        "tv": {
            "buttons": [
                {"name": name, "label": label, "image": "ico_tv"}
                for name, label in [("power", "電源"), ("select-input-src", "入力切替"),
                                    ("vol-up", "Volume Up"), ("mute", "Mute"), ("", "空欄")]
            ],
            "state": {"input": "t"},
        },
        "signals": [{"id": "tv-signal-1", "name": "Learned HDMI"}],
    }


def make_water_heater() -> dict[str, Any]:
    """通常の一覧にある架空のエコキュート。 / Synthetic heater metadata from the normal endpoint."""
    return {
        "id": "heater-1", "type": "EL_ELECTRIC_WATER_HEATER", "nickname": "Water heater",
        "device": make_device({}), "model": {"manufacturer": "Example", "name": "Heater 1"}, "signals": [],
    }


def make_echonetlite(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    """実応答の形だけを再現。 / Reproduce the response shape using synthetic data only."""
    return {"appliances": [{
        "id": "heater-1", "nickname": "Water heater", "type": "EL_ELECTRIC_WATER_HEATER",
        "Device": make_device({}),
        "properties": [{"epc": epc, "val": value, "updated_at": "2020-01-01T00:00:00Z"}
                       for epc, value in (NORMAL_WATER_HEATER if properties is None else properties).items()],
    }]}
