"""既存アカウントの識別と修理通知。 / Legacy account identity and duplicate repairs."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nature_remo.const import DOMAIN

from .conftest import APPLIANCES_URL, DEVICES_URL, USERS_URL, mock_api


def legacy_entry(hass, title="Legacy", token="token"):
    entry = MockConfigEntry(domain=DOMAIN, title=title, data={"api_key": token})
    entry.add_to_hass(hass)
    return entry


def account_requests(aioclient_mock):
    return sum(str(call[1]) == USERS_URL for call in aioclient_mock.mock_calls)


async def test_legacy_setup_migrates_only_once(hass, aioclient_mock):
    """The optional identity request follows normal refresh and is skipped after migration."""
    entry = legacy_entry(hass)
    mock_api(aioclient_mock, devices=[])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == "user-1"
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [DEVICES_URL, APPLIANCES_URL, USERS_URL]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert account_requests(aioclient_mock) == 1
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize("status, user", [(401, {}), (403, {}), (429, {}), (503, {}), (200, {}), (200, {"id": ""}), (200, {"id": 7})])
async def test_optional_identity_failure_does_not_block_setup(hass, aioclient_mock, status, user, caplog):
    """Even optional authentication failure permits setup, then retries on the next setup."""
    entry = legacy_entry(hass, token="secret-migration-token")
    mock_api(aioclient_mock, devices=[], user=user, user_status=status)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id is None
    assert not hass.config_entries.flow.async_progress()
    assert account_requests(aioclient_mock) == 1
    assert "secret-migration-token" not in caplog.text

    mock_api(aioclient_mock, devices=[])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == "user-1"


async def test_failed_normal_refresh_does_not_migrate(hass, aioclient_mock):
    """No extra migration request is made when the primary API fetch fails."""
    entry = legacy_entry(hass)
    mock_api(aioclient_mock, status=503)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id is None
    assert account_requests(aioclient_mock) == 0


@pytest.mark.parametrize("remove_owner", [False, True])
async def test_duplicate_legacy_entries_repair_and_removal(hass, aioclient_mock, remove_owner, caplog):
    """Both entries survive; deleting either entry clears the dismissible repair."""
    first = legacy_entry(hass, "First home", "first-token")
    mock_api(aioclient_mock, devices=[])
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    second = legacy_entry(hass, "Second home", "second-token")
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert first.unique_id == "user-1"
    assert second.unique_id is None
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    assert "already in use" not in caplog.text
    registry = ir.async_get(hass)
    repair = registry.async_get_issue(DOMAIN, "duplicate_account_user-1")
    assert repair is not None
    assert repair.is_fixable is False
    assert repair.translation_key == "duplicate_account"
    assert repair.translation_placeholders == {"entries": "- First home\n- Second home"}
    assert "first-token" not in str(repair)
    assert "second-token" not in str(repair)

    # unload だけでは登録は残る。 / Unloading keeps the registration and the repair.
    assert await hass.config_entries.async_unload(second.entry_id)
    assert registry.async_get_issue(DOMAIN, repair.issue_id) is not None
    await hass.config_entries.async_remove(first.entry_id if remove_owner else second.entry_id)
    await hass.async_block_till_done()
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert registry.async_get_issue(DOMAIN, repair.issue_id) is None


async def test_three_duplicates_keep_repair_until_last_duplicate_removed(hass, aioclient_mock):
    """Removing the original owner cannot hide two remaining duplicate entries."""
    entries = []
    mock_api(aioclient_mock, devices=[])
    for i in range(3):
        entry = legacy_entry(hass, f"Home {i}", f"token-{i}")
        entries.append(entry)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    registry = ir.async_get(hass)
    await hass.config_entries.async_remove(entries[0].entry_id)
    await hass.async_block_till_done()
    repair = registry.async_get_issue(DOMAIN, "duplicate_account_user-1")
    assert repair.translation_placeholders == {"entries": "- Home 1\n- Home 2"}
    await hass.config_entries.async_remove(entries[1].entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, "duplicate_account_user-1") is None


async def test_concurrent_legacy_migration_assigns_one_owner(hass, aioclient_mock, caplog):
    """Startup with both legacy entries already present safely assigns only one ID."""
    entries = [legacy_entry(hass, f"Home {i}", f"token-{i}") for i in range(2)]
    mock_api(aioclient_mock, devices=[])
    assert await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done()
    assert all(entry.state is ConfigEntryState.LOADED for entry in entries)
    assert sum(entry.unique_id == "user-1" for entry in entries) == 1
    assert "already in use" not in caplog.text
    assert ir.async_get(hass).async_get_issue(DOMAIN, "duplicate_account_user-1") is not None
