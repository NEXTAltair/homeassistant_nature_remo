"""人感の移行とタイムスタンプ。 / Motion migration and timestamp tests."""

from datetime import datetime, timezone

import pytest
from homeassistant.const import STATE_ON
from homeassistant.helpers import area_registry as ar, entity_registry as er, issue_registry as ir

from custom_components.nature_remo.const import DOMAIN
from custom_components.nature_remo.migration import async_migrate_motion_entities
from custom_components.nature_remo.sensor import NatureRemoMotionTimeSensor
from .conftest import make_device, mock_api
from .test_coordinator import setup, coordinator_of


def mock_motion(aioclient_mock):
    """現在の時刻を使う架空イベント。 / Synthetic event at the current time."""
    mock_api(aioclient_mock, devices=[make_device({"mo": {"val": 1, "created_at": datetime.now(timezone.utc).isoformat()}})])


@pytest.mark.parametrize("disabled", [False, True])
async def test_migration_preserves_user_settings(hass, aioclient_mock, config_entry, disabled):
    """ユーザー設定と object_id を移行。 / Preserve user settings and object ID."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Living room")
    old = registry.async_get_or_create(
        "sensor", DOMAIN, "device-1_motion", config_entry=config_entry,
        suggested_object_id="custom_motion", original_name="Old motion",
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
        hidden_by=er.RegistryEntryHider.USER,
    )
    registry.async_update_entity(old.entity_id, name="Custom motion", icon="mdi:walk", area_id=area.id)
    mock_motion(aioclient_mock)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    new = registry.async_get("binary_sensor.custom_motion")
    assert new.unique_id == old.unique_id
    assert new.name == "Custom motion"
    assert new.icon == "mdi:walk"
    assert new.area_id == area.id
    assert new.disabled_by == (er.RegistryEntryDisabler.USER if disabled else None)
    assert new.hidden_by == er.RegistryEntryHider.USER
    assert registry.async_get(old.entity_id) is None
    if not disabled:
        assert hass.states.get(new.entity_id).state == STATE_ON
    repair = ir.async_get(hass).async_get_issue(DOMAIN, f"motion_entity_migration_{config_entry.entry_id}")
    assert repair.is_persistent
    assert not repair.is_fixable
    assert repair.severity == ir.IssueSeverity.WARNING
    assert repair.translation_placeholders == {"entities": "- `sensor.custom_motion` → `binary_sensor.custom_motion`"}
    async_migrate_motion_entities(hass, config_entry)
    assert registry.async_get(new.entity_id) == new
    assert ir.async_get(hass).async_get_issue(DOMAIN, repair.issue_id) == repair


async def test_fresh_install_and_timestamp(hass, aioclient_mock, config_entry):
    """新規登録と datetime 型の時刻。 / New installation and datetime timestamp."""
    mock_motion(aioclient_mock)
    await setup(hass, config_entry)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, "device-1_motion")
    assert hass.states.get(entity_id).state == STATE_ON
    assert registry.async_get_entity_id("sensor", DOMAIN, "device-1_motion") is None
    time_id = registry.async_get_entity_id("sensor", DOMAIN, "device-1_last_motion")
    assert hass.states.get(time_id).attributes["device_class"] == "timestamp"
    coordinator = coordinator_of(hass, config_entry)
    entity = NatureRemoMotionTimeSensor(coordinator, "device-1", "Remo", {})
    assert isinstance(entity.native_value, datetime)
    assert entity.native_value.tzinfo is not None
    assert not ir.async_get(hass).issues


async def test_migration_keeps_last_motion_and_other_entry(hass, config_entry):
    """時刻や他の設定エントリには触れない。 / Leave timestamps and other entries intact."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    timestamp = registry.async_get_or_create("sensor", DOMAIN, "device-1_last_motion", config_entry=config_entry)
    other = registry.async_get_or_create("sensor", "other", "device-1_motion", config_entry=config_entry)
    async_migrate_motion_entities(hass, config_entry)
    assert registry.async_get(timestamp.entity_id) == timestamp
    assert registry.async_get(other.entity_id) == other


async def test_migration_does_not_overwrite_conflicting_entity_id(hass, config_entry):
    """衝突する別エンティティは保持。 / Keep an unrelated entity with the same object ID."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    existing = registry.async_get_or_create("binary_sensor", "other", "other", suggested_object_id="motion")
    registry.async_get_or_create("sensor", DOMAIN, "device-1_motion", config_entry=config_entry, suggested_object_id="motion")
    async_migrate_motion_entities(hass, config_entry)
    assert registry.async_get(existing.entity_id) == existing
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, "device-1_motion") == "binary_sensor.motion_2"
