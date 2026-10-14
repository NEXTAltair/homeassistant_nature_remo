from __future__ import annotations

import logging
import voluptuous as vol
from collections.abc import Mapping
from typing import Any
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .account import async_track_account
from .api import NatureRemoAPI, NatureRemoAuthError, NatureRemoError
from .const import DOMAIN
from .options_flow import NatureRemoOptionsFlowHandler

_LOGGER = logging.getLogger(__name__)

# APIキー入力用のスキーマ
API_SCHEMA = vol.Schema(
    {vol.Optional("name", default="Nature Remo"): str, vol.Required("api_key"): str}
)

# 再認証で新しいAPIキーを入力するスキーマ / Schema for entering a new API key on reauth
REAUTH_SCHEMA = vol.Schema({vol.Required("api_key"): str})


class NatureRemoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Nature Remo 統合のセットアップフロー。
    Setup flow for the Nature Remo integration.
    """

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    # ユーザーにAPIキーを入力してもらう
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """ユーザー入力によるセットアップ初期ステップ。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            # 古い entry も通信なしで照合。 / Match legacy entries without a request.
            self._async_abort_entries_match({"api_key": user_input["api_key"]})
            api = NatureRemoAPI(user_input["api_key"], async_get_clientsession(self.hass))
            try:
                user_id = await api.get_user_id()
            except NatureRemoAuthError:
                errors["base"] = "invalid_auth"
            except (NatureRemoError, ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input.get("name", "Nature Remo"),
                    data={"api_key": user_input["api_key"]},
                )

        # APIキー入力フォームを表示
        return self.async_show_form(step_id="user", data_schema=API_SCHEMA, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """
        アクセストークンが拒否されたときに開始される再認証フロー.
        Reauth flow started when the access token is rejected.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        新しいアクセストークンを確認してから保存し、統合を再読み込みする.
        Validate the new access token, store it and reload the integration.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            api = NatureRemoAPI(user_input["api_key"], async_get_clientsession(self.hass))
            try:
                user_id = await api.get_user_id()
            except NatureRemoAuthError:
                errors["base"] = "invalid_auth"
            except (NatureRemoError, ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                entry = self._get_reauth_entry()
                owner = await self.async_set_unique_id(user_id)
                if entry.unique_id is not None:
                    self._abort_if_unique_id_mismatch()
                # 重複 ID を保存しない。 / Do not assign an ID already held by another entry.
                unique_id = user_id if owner is None or owner is entry else entry.unique_id
                if entry.update_listeners:
                    # 更新時はリスナーが再読み込みする。同じ値なら明示的に一度だけ実行。
                    # The listener reloads changed data; explicitly reload unchanged data once.
                    changed = entry.data["api_key"] != user_input["api_key"] or entry.unique_id != unique_id
                    result = self.async_update_and_abort(
                        entry, unique_id=unique_id, data_updates={"api_key": user_input["api_key"]}
                    )
                    if not changed:
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
                else:
                    result = self.async_update_reload_and_abort(
                        entry, unique_id=unique_id,
                        data_updates={"api_key": user_input["api_key"]},
                    )
                async_track_account(self.hass, entry, user_id)
                return result

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NatureRemoOptionsFlowHandler()
