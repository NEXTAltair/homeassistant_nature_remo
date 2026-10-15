"""学習 remote の表示選択。 / Visibility choices for learned remotes."""

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import entity_registry as er, selector

from .const import DOMAIN
from .remote_visibility import (
    ISSUE_PREFIX, async_mark_visibility_reviewed, async_unreviewed_remotes,
)


class RemoteVisibilityRepairFlow(RepairsFlow):
    """送信機能を残して主操作から外す。 / Hide controls without disabling command services."""

    async def async_step_init(self, user_input=None):
        """対象を確認する。 / Review the affected entities."""
        self._offered_ids = None
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        """選んだ remote だけを非表示にする。 / Hide only the selected remotes."""
        entry_id = self.data["entry_id"]
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id, {}).get("coordinator")
        if coordinator is None:
            return self.async_abort(reason="entry_not_loaded")
        pending = {
            entity.id: entity
            for entity in async_unreviewed_remotes(self.hass, entry_id, coordinator)
            if not entity.hidden and not entity.disabled
        }
        errors = {}
        if user_input is not None:
            selected = set(user_input.get("remotes", []))
            if not selected <= pending.keys() or not selected <= self._offered_ids:
                errors["base"] = "entities_changed"
            else:
                registry = er.async_get(self.hass)
                for registry_id in self._offered_ids & pending.keys():
                    entity = pending[registry_id]
                    if registry_id in selected:
                        registry.async_update_entity(
                            entity.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION,
                        )
                    # 非選択も明示的な表示の選択として記憶する。
                    # Remember an unchecked entity as an explicit choice to keep it visible.
                    async_mark_visibility_reviewed(self.hass, entity)
                return self.async_create_entry(data={})
        if not pending:
            return self.async_create_entry(data={})
        self._offered_ids = set(pending)
        return self.async_show_form(
            step_id="confirm", errors=errors,
            data_schema=vol.Schema({
                vol.Optional("remotes", description={"suggested_value": list(pending)}): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": entity.id, "label": f"{entity.name or entity.original_name or entity.entity_id} ({entity.entity_id})"}
                                 for entity in pending.values()],
                        multiple=True, mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
        )


async def async_create_fix_flow(hass, issue_id, data):
    """表示移行の修復フローを作成。 / Create the visibility migration flow."""
    if issue_id.startswith(ISSUE_PREFIX):
        return RemoteVisibilityRepairFlow()
    raise ValueError(f"Unknown repair: {issue_id}")
