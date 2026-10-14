"""アカウントの重複通知。 / Repairs for duplicate Nature accounts."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

DATA_ACCOUNT_IDS = f"{DOMAIN}_account_ids"
ISSUE_PREFIX = "duplicate_account_"


@callback
def async_track_account(hass: HomeAssistant, entry: ConfigEntry, user_id: str) -> None:
    """unload 後も識別情報を保持。 / Keep verified identity until the entry is removed."""
    hass.data.setdefault(DATA_ACCOUNT_IDS, {})[entry.entry_id] = user_id
    async_refresh_account_repairs(hass)


@callback
def async_refresh_account_repairs(hass: HomeAssistant) -> None:
    """削除後に重複を再評価。 / Reevaluate duplicates after either entry is removed."""
    entries = hass.config_entries.async_entries(DOMAIN)
    entry_ids = {entry.entry_id for entry in entries}
    known_ids = hass.data.setdefault(DATA_ACCOUNT_IDS, {})
    for entry_id in list(known_ids):
        if entry_id not in entry_ids:
            del known_ids[entry_id]

    accounts: dict[str, list[ConfigEntry]] = {}
    for entry in entries:
        if user_id := entry.unique_id or known_ids.get(entry.entry_id):
            accounts.setdefault(user_id, []).append(entry)

    active_issues = set()
    for user_id, duplicates in accounts.items():
        if len(duplicates) < 2:
            continue
        issue_id = f"{ISSUE_PREFIX}{user_id}"
        active_issues.add(issue_id)
        ir.async_create_issue(
            hass, DOMAIN, issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="duplicate_account",
            translation_placeholders={"entries": "\n".join(f"- {entry.title}" for entry in duplicates)},
        )

    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(ISSUE_PREFIX) and issue_id not in active_issues:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
