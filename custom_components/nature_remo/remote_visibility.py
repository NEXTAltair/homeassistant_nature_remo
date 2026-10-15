"""学習 remote の表示移行。 / Review visibility of legacy learned remotes."""

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import DOMAIN

VISIBILITY_REVIEWED = "remote_visibility_reviewed"
ISSUE_PREFIX = "stateless_remote_visibility_"


@callback
def async_unreviewed_remotes(hass, entry_id, coordinator):
    """現在の汎用 remote だけを取得。 / Find only current generic remotes awaiting review."""
    registry = er.async_get(hass)
    unique_ids = {f"nature_remo_remote_{appliance_id}" for appliance_id in coordinator.ir_remotes}
    return [
        entity for entity in er.async_entries_for_config_entry(registry, entry_id)
        if entity.domain == "remote" and entity.platform == DOMAIN
        and entity.unique_id in unique_ids
        and not entity.options.get(DOMAIN, {}).get(VISIBILITY_REVIEWED)
    ]


@callback
def async_mark_visibility_reviewed(hass, entity):
    """他の設定を残して確認済みとする。 / Record review while preserving other options."""
    er.async_get(hass).async_update_entity_options(
        entity.entity_id, DOMAIN,
        {**entity.options.get(DOMAIN, {}), VISIBILITY_REVIEWED: True},
    )


@callback
def async_remove_remote_visibility_repair(hass, entry_id):
    """不要になった通知を削除。 / Remove an obsolete repair."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_PREFIX}{entry_id}")


@callback
def async_offer_remote_visibility_repair(hass, entry, coordinator):
    """既存の表示設定は同意後に変更。 / Change existing visibility only after user selection."""
    pending = []
    for entity in async_unreviewed_remotes(hass, entry.entry_id, coordinator):
        if entity.hidden or entity.disabled:
            # 新規の既定非表示と、既存の利用者設定を維持する。
            # Preserve the new hidden default and existing user preferences.
            async_mark_visibility_reviewed(hass, entity)
        else:
            pending.append(entity)
    if not pending:
        async_remove_remote_visibility_repair(hass, entry.entry_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, f"{ISSUE_PREFIX}{entry.entry_id}",
        is_fixable=True, is_persistent=False, severity=ir.IssueSeverity.WARNING,
        translation_key="stateless_remote_visibility", data={"entry_id": entry.entry_id},
    )
