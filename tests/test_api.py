"""Tests for the Nature Remo API client."""

from __future__ import annotations

import time
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.nature_remo.api import (
    NATURE_REMO_URL,
    NatureRemoAPI,
    NatureRemoAuthError,
    NatureRemoError,
    NatureRemoRateLimitError,
)

from .conftest import DEVICES_URL, NORMAL_METER

MISSING = {"buy_power": None, "sold_power": None, "instant_power": None}


def parse(values: dict[int, Any]) -> dict[str, Any]:
    """Run the smart meter parser on EPC -> val pairs."""
    props = [{"epc": epc, "val": val} for epc, val in values.items()]
    return NatureRemoAPI("token", None).parse_smart_meter_properties(props)


def without(values: dict[int, Any], epc: int) -> dict[int, Any]:
    """Copy of the properties with one EPC removed."""
    return {k: v for k, v in values.items() if k != epc}


def test_parse_normal_reading() -> None:
    """A real Remo E reading converts to kWh without float noise."""
    assert parse(NORMAL_METER) == {
        "buy_power": 56224.8,
        "sold_power": 19380.1,
        "instant_power": -888,
    }


@pytest.mark.parametrize("raw", ["4294967294", "4294967295", "100000000", "-1"])
def test_parse_cumulative_out_of_range_is_missing(raw: str) -> None:
    """0xFFFFFFFE (no measurement) must not become a 429,496 MWh reading."""
    result = parse({**NORMAL_METER, 224: raw, 227: raw})
    assert result["buy_power"] is None
    assert result["sold_power"] is None
    assert result["instant_power"] == -888


@pytest.mark.parametrize("raw", [None, "", "abc", "1.5"])
def test_parse_unreadable_value_is_missing(raw: Any) -> None:
    """Null or malformed values become None instead of 0 or a TypeError."""
    result = parse({**NORMAL_METER, 224: raw})
    assert result["buy_power"] is None
    assert result["sold_power"] == 19380.1


def test_parse_absent_property_is_missing() -> None:
    """A meter that stops reporting export energy reads unknown, not 0 kWh."""
    result = parse(without(NORMAL_METER, 227))
    assert result["sold_power"] is None
    assert result["buy_power"] == 56224.8


def test_parse_zero_is_kept() -> None:
    """Legitimate zero readings stay zero."""
    assert parse({**NORMAL_METER, 224: "0", 227: "0", 231: "0"}) == {
        "buy_power": 0.0,
        "sold_power": 0.0,
        "instant_power": 0,
    }


@pytest.mark.parametrize("unit", [None, "abc", "5"])
def test_parse_unknown_unit_is_missing(unit: Any) -> None:
    """Without a valid unit the cumulative scale is unknown."""
    result = parse({**NORMAL_METER, 225: unit})
    assert result["buy_power"] is None
    assert result["sold_power"] is None
    assert result["instant_power"] == -888


def test_parse_absent_unit_is_missing() -> None:
    """A missing unit property no longer defaults to 1 kWh."""
    result = parse(without(NORMAL_METER, 225))
    assert result["buy_power"] is None
    assert result["sold_power"] is None


def test_parse_absent_coefficient_defaults_to_one() -> None:
    """Meters without the coefficient property use the spec default of 1."""
    assert parse(without(NORMAL_METER, 211))["buy_power"] == 56224.8


@pytest.mark.parametrize("coefficient", [None, "abc", "0", "1000000"])
def test_parse_invalid_coefficient_is_missing(coefficient: Any) -> None:
    """A coefficient that is present but invalid must not produce a plausible number."""
    result = parse({**NORMAL_METER, 211: coefficient})
    assert result["buy_power"] is None
    assert result["sold_power"] is None


@pytest.mark.parametrize(
    ("coefficient", "unit", "expected"),
    [("10", "10", 56224800.0), ("1", "4", 56.2248), ("1", "0", 562248.0)],
)
def test_parse_coefficient_and_unit_scale(
    coefficient: str, unit: str, expected: float
) -> None:
    """Coefficient and unit (0x0A = x10, 0x04 = x0.0001) are both applied."""
    result = parse({**NORMAL_METER, 211: coefficient, 225: unit})
    assert result["buy_power"] == expected


@pytest.mark.parametrize("raw", ["2147483646", "2147483647", "-2147483648"])
def test_parse_instant_power_special_codes_are_missing(raw: str) -> None:
    """Overflow, underflow and no-data codes of the signed power value are dropped."""
    result = parse({**NORMAL_METER, 231: raw})
    assert result["instant_power"] is None
    assert result["buy_power"] == 56224.8


@pytest.mark.parametrize("properties", [[], None])
def test_parse_no_properties(properties: Any) -> None:
    """No properties at all means every reading is unknown."""
    assert NatureRemoAPI("token", None).parse_smart_meter_properties(properties) == MISSING


def test_parse_skips_malformed_entries() -> None:
    """Entries without a usable EPC are ignored."""
    props = [{"val": "1"}, "junk", {"epc": "x", "val": "1"}] + [
        {"epc": epc, "val": val} for epc, val in NORMAL_METER.items()
    ]
    assert NatureRemoAPI("token", None).parse_smart_meter_properties(props)[
        "buy_power"
    ] == 56224.8


async def test_request_without_rate_limit_headers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Responses without X-Rate-Limit-* headers are accepted."""
    aioclient_mock.get(DEVICES_URL, json=[{"id": "device-1"}])
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    assert await api.get_devices() == [{"id": "device-1"}]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer token"


@pytest.mark.parametrize("status", [401, 403])
async def test_request_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """A rejected token raises an auth error."""
    aioclient_mock.get(DEVICES_URL, status=status, json={"message": "unauthorized"})
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    with pytest.raises(NatureRemoAuthError):
        await api.get_devices()


async def test_request_rate_limited(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """429 raises a rate limit error that knows when the limit resets."""
    reset = int(time.time()) + 120
    aioclient_mock.get(
        DEVICES_URL, status=429, json={}, headers={"X-Rate-Limit-Reset": str(reset)}
    )
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    with pytest.raises(NatureRemoRateLimitError) as exc_info:
        await api.get_devices()
    assert 100 < exc_info.value.retry_after <= 120


async def test_request_rate_limited_without_reset_header(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """429 without a reset header leaves the retry delay to the coordinator."""
    aioclient_mock.get(DEVICES_URL, status=429, json={})
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    with pytest.raises(NatureRemoRateLimitError) as exc_info:
        await api.get_devices()
    assert exc_info.value.retry_after is None


async def test_get_server_error_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A non-200 GET raises instead of returning None."""
    aioclient_mock.get(DEVICES_URL, status=503, json={"message": "unavailable"})
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    with pytest.raises(NatureRemoError):
        await api.get_devices()


async def test_get_invalid_json_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 response that is not JSON raises."""
    aioclient_mock.get(DEVICES_URL, text="<html>maintenance</html>")
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    with pytest.raises(NatureRemoError):
        await api.get_devices()


async def test_aircon_command_server_error_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failed aircon command raises instead of returning the error JSON."""
    aioclient_mock.post(
        "https://api.nature.global/2/appliances/aircon-1/aircon_settings",
        status=500,
        json={"message": "failed"},
    )
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    with pytest.raises(NatureRemoError):
        await api.send_command_climate({"button": "power-off"}, "aircon-1")


async def test_signal_send_error_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failed IR signal send raises a Nature Remo error."""
    aioclient_mock.post(f"{NATURE_REMO_URL}/signals/signal-1/send", status=500, json={})
    api = NatureRemoAPI("token", async_get_clientsession(hass))

    with pytest.raises(NatureRemoError):
        await api.send_command_signal("signal-1")


@pytest.mark.parametrize("payload", [{}, {"id": ""}, {"id": "   "}, {"id": None}, {"id": 1}, {"id": []}, [], None])
async def test_invalid_user_identity_raises(hass, aioclient_mock, payload):
    """Missing, empty and non-string user IDs never become config entry identifiers."""
    aioclient_mock.get(f"{NATURE_REMO_URL}/users/me", json=payload)
    api = NatureRemoAPI("token", async_get_clientsession(hass))
    with pytest.raises(NatureRemoError):
        await api.get_user_id()


async def test_user_identity_uses_authenticated_request(hass, aioclient_mock):
    """The shared request path authenticates and returns the stable user ID."""
    aioclient_mock.get(f"{NATURE_REMO_URL}/users/me", json={"id": "user-1", "nickname": "Example"})
    api = NatureRemoAPI("token", async_get_clientsession(hass))
    assert await api.get_user_id() == "user-1"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer token"
