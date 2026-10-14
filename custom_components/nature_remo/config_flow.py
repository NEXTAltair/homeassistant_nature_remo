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

    def __init__(self) -> None:
        self.api_key: str = ""
        self.appliances: list[dict[str, Any]] = []

    # ユーザーにAPIキーを入力してもらう
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """ユーザー入力によるセットアップ初期ステップ。"""
        if user_input is not None:
            # 入力されたAPIキーを保持
            self.api_key = user_input["api_key"]
            self.name = user_input.get("name", "Nature Remo")

            return self.async_create_entry(
                title=self.name, data={"api_key": self.api_key}
            )

        # APIキー入力フォームを表示
        return self.async_show_form(step_id="user", data_schema=API_SCHEMA)

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
                await api.get_devices()
            except NatureRemoAuthError:
                errors["base"] = "invalid_auth"
            except (NatureRemoError, ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={"api_key": user_input["api_key"]},
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NatureRemoOptionsFlowHandler()
