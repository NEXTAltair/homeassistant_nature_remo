import logging


from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import NatureRemoAPI
from .aircon import AirconControl
from .coordinator import NatureRemoCoordinator
from .const import DOMAIN
from .migration import async_migrate_motion_entities
from .device import async_register_devices

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["climate", "light", "sensor", "remote", "number", "binary_sensor"]


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
        "aircon_controls": aircon_controls,
    }

    # カスタムサービスの登録
    async def handle_send_light_mode(call: ServiceCall):
        # サービスコールからエンティティIDと動作モードを取得する
        entity_id = call.data.get("entity_id")
        mode = call.data.get("mode", "on")

        # entity_idからappliance_idを取り出す
        _LOGGER.debug(f"coordinator.entity_map：{coordinator.entity_map}")
        light_entity = coordinator.entity_map.get(entity_id)
        if light_entity is None:
            raise ValueError(f"{entity_id} not found in coordinator.entity_map")

        if mode not in light_entity._supported_effects:
            raise ValueError(f"Effect '{mode}' is not supported by this light")

        # NatureRemo APIへリクエスト送信
        await api.send_light_command(light_entity._appliance_id, mode)

        # エンティティの内部状態を即時更新
        light_entity._last_mode = mode
        light_entity._is_on = mode != "off"
        light_entity.async_write_ha_state()

        return {"status": "success", "appliance_id": light_entity._appliance_id}

    hass.services.async_register(
        DOMAIN, "send_light_mode", handle_send_light_mode, supports_response=True
    )

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
