"""既存エンティティの移行。 / Migrations for existing entities."""

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import DOMAIN


@callback
def async_migrate_motion_entities(hass, entry):
    """人感の設定を引き継ぎドメインを変更。 / Move motion to binary_sensor with user settings."""
    registry = er.async_get(hass)
    changes = []
    for old in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            old.domain != "sensor"
            or old.platform != DOMAIN
            or not old.unique_id.endswith("_motion")
            or old.unique_id.endswith("_last_motion")
        ):
            continue
        new = registry.async_get_or_create(
            "binary_sensor", DOMAIN, old.unique_id,
            config_entry=entry,
            suggested_object_id=old.entity_id.split(".", 1)[1],
            device_id=old.device_id,
            original_device_class="motion",
            original_name=old.original_name,
            original_icon=old.original_icon,
            disabled_by=old.disabled_by,
            hidden_by=old.hidden_by,
        )
        registry.async_update_entity(
            new.entity_id,
            name=old.name,
            icon=old.icon,
            area_id=old.area_id,
            disabled_by=old.disabled_by,
            hidden_by=old.hidden_by,
            aliases=old.aliases,
            labels=old.labels,
        )
        changes.append(f"- `{old.entity_id}` → `{new.entity_id}`")
        registry.async_remove(old.entity_id)

    if changes:
        # 再起動後も残り、ユーザーが無視できる通知。 / Persist a repair the user can dismiss.
        ir.async_create_issue(
            hass, DOMAIN, f"motion_entity_migration_{entry.entry_id}",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="motion_entity_migration",
            translation_placeholders={"entities": "\n".join(changes)},
        )
