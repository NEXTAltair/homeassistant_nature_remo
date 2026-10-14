"""Tests for the Nature Remo config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.nature_remo.const import DOMAIN

from .conftest import USERS_URL, mock_api


async def test_user_step_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The setup form stores the token under the given name."""
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Home", "api_key": "token"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {"api_key": "token"}
    assert result["result"].unique_id == "user-1"


async def test_reauth_updates_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A working new token replaces the old one and reloads the entry."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "new-token"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["api_key"] == "new-token"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer new-token"


async def test_reauth_rejects_bad_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A rejected token keeps the form open and the old token stored."""
    mock_api(aioclient_mock, devices={}, appliances={}, status=401)
    config_entry.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "bad-token"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data["api_key"] == "token"


async def test_reauth_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A server error during validation is reported as a connection problem."""
    mock_api(aioclient_mock, devices={}, appliances={}, status=503)
    config_entry.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "new-token"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("unique_id", [None, "user-1"])
async def test_duplicate_token_aborts_without_request(hass, aioclient_mock, unique_id):
    """An identical token is rejected even before a legacy entry is migrated."""
    entry = MockConfigEntry(domain=DOMAIN, title="Existing", data={"api_key": "token"}, unique_id=unique_id)
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"api_key": "token", "name": "New"}
    )
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert entry.title == "Existing"
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize("user_id, expected", [("user-1", "already_configured"), ("user-2", None)])
async def test_account_identity_with_different_token(hass, aioclient_mock, config_entry, user_id, expected):
    """Only a genuinely different account may create another entry."""
    config_entry.add_to_hass(hass)
    mock_api(aioclient_mock, user={"id": user_id})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"api_key": "other-token", "name": "Other"}
    )
    await hass.async_block_till_done()
    if expected:
        assert result["reason"] == expected
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1
        assert aioclient_mock.call_count == 1
    else:
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == "user-2"
        assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    assert config_entry.data["api_key"] == "token"
    assert config_entry.title == "Nature Remo"


@pytest.mark.parametrize("status, error", [(401, "invalid_auth"), (403, "invalid_auth"), (429, "cannot_connect"), (503, "cannot_connect")])
async def test_user_validation_failure(hass, aioclient_mock, status, error):
    """Validation failures keep the form open without creating an entry."""
    aioclient_mock.get(USERS_URL, status=status)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"api_key": "bad-token"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_concurrent_account_flow_aborts(hass, aioclient_mock):
    """An already identified flow prevents a concurrent second account flow."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER, "unique_id": "user-1"}
    )
    mock_api(aioclient_mock)
    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"api_key": "second-token"}
    )
    assert second["reason"] == "already_in_progress"
    assert hass.config_entries.async_entries(DOMAIN) == []
    result = await hass.config_entries.flow.async_configure(first["flow_id"], {"api_key": "first-token"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_rejects_other_account(hass, aioclient_mock, config_entry):
    """A valid token from a different account must never overwrite the entry."""
    config_entry.add_to_hass(hass)
    mock_api(aioclient_mock, user={"id": "other-user"})
    flow = await config_entry.start_reauth_flow(hass)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"api_key": "other-token"})
        await hass.async_block_till_done()
    assert result["reason"] == "unique_id_mismatch"
    assert config_entry.data["api_key"] == "token"
    assert config_entry.unique_id == "user-1"
    reload.assert_not_called()


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("loaded", [False, True])
@pytest.mark.parametrize("token", ["token", "new-token"])
async def test_legacy_reauth_identity_and_single_reload(hass, aioclient_mock, duplicate, loaded, token):
    """Legacy reauth assigns only a free ID and reloads once, including unchanged tokens."""
    from custom_components.nature_remo import async_reload_options
    from homeassistant.helpers import issue_registry as ir

    entry = MockConfigEntry(domain=DOMAIN, title="Legacy", data={"api_key": "token"})
    entry.add_to_hass(hass)
    if duplicate:
        owner = MockConfigEntry(domain=DOMAIN, title="Owner", data={"api_key": "owner-token"}, unique_id="user-1")
        owner.add_to_hass(hass)
    if loaded:
        entry.async_on_unload(entry.add_update_listener(async_reload_options))
    mock_api(aioclient_mock)
    flow = await entry.start_reauth_flow(hass)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock(return_value=True)) as reload:
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"api_key": token})
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == token
    assert entry.unique_id == (None if duplicate else "user-1")
    reload.assert_awaited_once_with(entry.entry_id)
    repair = ir.async_get(hass).async_get_issue(DOMAIN, "duplicate_account_user-1")
    assert (repair is not None) == duplicate


@pytest.mark.parametrize("reauth", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "network", "malformed", "json"])
async def test_validation_transport_and_response_failures(hass, aioclient_mock, config_entry, reauth, failure):
    """Network and malformed account responses leave credentials unchanged."""
    from aiohttp import ClientError

    if failure == "timeout":
        aioclient_mock.get(USERS_URL, exc=TimeoutError())
    elif failure == "network":
        aioclient_mock.get(USERS_URL, exc=ClientError())
    elif failure == "malformed":
        aioclient_mock.get(USERS_URL, json={"id": None})
    else:
        aioclient_mock.get(USERS_URL, text="not JSON")
    if reauth:
        config_entry.add_to_hass(hass)
        flow = await config_entry.start_reauth_flow(hass)
    else:
        flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"api_key": "new-token"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert config_entry.data["api_key"] == "token"
