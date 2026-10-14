import logging

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType
from .api import NatureRemoAPI, NatureRemoError
from .account import async_refresh_account_repairs, async_track_account
from .aircon import AirconControl
from .coordinator import NatureRemoCoordinator
from .const import DOMAIN
from .migration import async_migrate_motion_entities
from .device import async_register_devices
from .echonetlite import NatureRemoEchonetLiteCoordinator, WATER_HEATER_TYPE

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["climate", "light", "sensor", "remote", "number", "binary_sensor", "button"]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """サービスを一度だけ登録。 / Register the service independently of entry lifetimes."""
    async def handle_send_light_mode(call: ServiceCall) -> ServiceResponse:
        entity_id = call.data.get("entity_id")
        mode = call.data.get("mode", "on")
        registry_entry = er.async_get(hass).async_get(entity_id) if isinstance(entity_id, str) else None
        if registry_entry is None or registry_entry.domain != "light" or registry_entry.platform != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="light_not_found",
                translation_placeholders={"entity_id": str(entity_id)},
            )

        entry = hass.config_entries.async_get_entry(registry_entry.config_entry_id)
        if (
            entry is None
            or entry.state is not ConfigEntryState.LOADED
            or entry.disabled_by is not None
            or registry_entry.disabled
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="light_not_loaded",
                translation_placeholders={"entity_id": entity_id},
            )
        runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        light_entity = runtime.get("lights", {}).get(registry_entry.id)
        if light_entity is None or light_entity.entity_id != entity_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="light_not_loaded",
                translation_placeholders={"entity_id": entity_id},
            )
        return await light_entity.async_send_light_mode(mode)

    hass.services.async_register(
        DOMAIN, "send_light_mode", handle_send_light_mode,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    ConfigEntryから管理されるインテグレーションの起動時に呼ばれる処理
    Nature Remo APIやDataUpdateCoordinatorの初期化を行う

    Called when a config entry is set up.
    Initializes the Nature Remo API and DataUpdateCoordinator.
    """
    _LOGGER.debug("Setting up Nature Remo integration entry")

    hass.data.setdefault(DOMAIN, {})

    # APIラッパーの初期化 / Initialize the Nature Remo API wrapper
    api = NatureRemoAPI(entry.data["api_key"], async_get_clientsession(hass))

    # Coordinator作成 / Create the coordinator
    update_interval = entry.options.get("update_interval", 60)
    coordinator = NatureRemoCoordinator(hass, entry, api, update_interval)
    await coordinator.async_config_entry_first_refresh()
    if entry.unique_id is None:
        try:
            user_id = await api.get_user_id()
        except (NatureRemoError, ClientError, TimeoutError):
            # 任意の移行で起動や再認証を妨げない。応答本文やトークンは記録しない。
            # Optional migration must not block setup or trigger reauth; omit response bodies and tokens.
            _LOGGER.warning(
                "アカウント ID の移行を次回起動時に再試行します / "
                "Account identity migration will retry at the next setup"
            )
        else:
            if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, user_id) is None:
                hass.config_entries.async_update_entry(entry, unique_id=user_id)
            async_track_account(hass, entry, user_id)
    echonetlite_coordinator = None
    if any(appliance.get("type") == WATER_HEATER_TYPE for appliance in coordinator.data.values()):
        echonetlite_coordinator = NatureRemoEchonetLiteCoordinator(hass, entry, coordinator)
        try:
            await echonetlite_coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            # 任意の追加取得の失敗で通常の家電を止めない。
            # Do not block normal appliances on an optional endpoint failure.
            echonetlite_coordinator.data = {}
            _LOGGER.warning(
                "エコキュートの初回取得を再試行します / Retrying initial water heater fetch: %s",
                echonetlite_coordinator.last_exception,
            )
        # センサーなしの本体や未対応 EPC のみでも取得を継続する。
        # Keep discovery and heater polling active even without supported sensor entities.
        entry.async_on_unload(coordinator.async_add_listener(lambda: None))
        entry.async_on_unload(echonetlite_coordinator.async_add_listener(lambda: None))
    async_register_devices(hass, entry, coordinator)

    # 両プラットフォームで設定を共有する。 / Share settings between both platforms.
    aircon_controls = {
        appliance_id: AirconControl(coordinator, appliance_id)
        for appliance_id in coordinator.aircons
    }
    for control in aircon_controls.values():
        entry.async_on_unload(coordinator.async_add_listener(control.update_offset))

    # coordinator, apiをhassのデータ管理下に置く / Store coordinator, api in hass data for access in platforms
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "api": api,
        "lights": {},
        "aircon_controls": aircon_controls,
        "echonetlite_coordinator": echonetlite_coordinator,
    }

    async_migrate_motion_entities(hass, entry)

    # プラットフォームを起動 / Forward entry setup to the platform
    # climate が中継センサーを参照できる順序で準備する。
    # Set up relay sensors before climate subscribes to them.
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    await hass.config_entries.async_forward_entry_setups(entry, [platform for platform in PLATFORMS if platform != "sensor"])

    entry.async_on_unload(entry.add_update_listener(async_reload_options))

    return True


async def async_reload_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """保存したオプションを反映。 / Apply saved options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    ConfigEntryの削除時に呼ばれるクリーンアップ処理
    Nature Remo管理情報やプラットフォームを解放する

    Called when a config entry is unloaded.
    Cleans up Nature Remo-related data and platform entries.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """残った entry で重複通知を更新。 / Refresh repairs using the remaining entries."""
    async_refresh_account_repairs(hass)
