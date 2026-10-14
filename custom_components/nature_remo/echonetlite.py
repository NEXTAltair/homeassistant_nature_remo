"""エコキュートの読み取り専用データ。 / Read-only ECHONET Lite water heater data."""

from datetime import timedelta
import logging
import re

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from .api import NatureRemoAuthError, NatureRemoError, NatureRemoRateLimitError
from .device import appliance_device_info

_LOGGER = logging.getLogger(__name__)
WATER_HEATER_TYPE = "EL_ELECTRIC_WATER_HEATER"

EPC_LENGTHS = {"80": 2, "85": 8, "b0": 2, "b2": 2, "c0": 2, "c3": 2, "e1": 4, "e3": 2, "ea": 2}
EPC_CODES = {
    "80": {0x30: True, 0x31: False},
    "b0": {0x41: "automatic", 0x42: "manual_heating", 0x43: "manual_stop"},
    "b2": {0x41: True, 0x42: False},
    "c0": {0x41: True, 0x42: False},
    "c3": {0x41: True, 0x42: False},
    "e3": {0x41: True, 0x42: False},
    "ea": {0x41: "filling", 0x42: "stopped", 0x43: "keeping_warm"},
}


def parse_water_heater_properties(properties):
    """厳密に16進値を検証し欠測を保持。 / Validate hexadecimal values and preserve missing readings."""
    result = {}
    for prop in properties:
        if not isinstance(prop, dict) or not isinstance(epc := prop.get("epc"), str):
            continue
        epc = epc.lower()
        if epc not in EPC_LENGTHS:
            continue
        result[epc] = None
        value = prop.get("val")
        if not isinstance(value, str) or re.fullmatch(rf"[0-9a-fA-F]{{{EPC_LENGTHS[epc]}}}", value) is None:
            continue
        raw = int(value, 16)
        if epc == "85":
            result[epc] = raw / 1000 if raw <= 999_999_999 else None
        elif epc == "e1":
            result[epc] = raw if raw <= 65_533 else None
        else:
            result[epc] = EPC_CODES[epc].get(raw)
    return result


@callback
def async_setup_water_heater_entities(entry, coordinator, descriptions, entity_type, async_add_entities):
    """最初の成功時にだけエンティティを追加。 / Add entities from the first successful snapshot."""
    if coordinator is None:
        return
    unsubscribe = None

    @callback
    def async_remove_listener():
        """復帰時とアンロード時に一度だけ解除。 / Unsubscribe once on recovery or unload."""
        nonlocal unsubscribe
        if unsubscribe is not None:
            unsubscribe()
            unsubscribe = None

    @callback
    def async_add_first_snapshot():
        """初回失敗後も再読み込みなしで復帰。 / Recover from initial failure without reloading."""
        if not coordinator.last_update_success:
            return
        appliances = coordinator.appliances
        async_add_entities(
            entity_type(coordinator, appliances[appliance_id], description)
            for appliance_id, properties in coordinator.data.items() if appliance_id in appliances
            for description in descriptions if description.key in properties
        )
        async_remove_listener()

    if coordinator.last_update_success:
        async_add_first_snapshot()
    else:
        unsubscribe = coordinator.async_add_listener(async_add_first_snapshot)
        entry.async_on_unload(async_remove_listener)


class NatureRemoEchonetLiteCoordinator(DataUpdateCoordinator):
    """対象機器があるときだけ5分ごとに取得。 / Poll every five minutes only with supported heaters."""

    def __init__(self, hass, entry, main_coordinator):
        """通常のポーリングと分離。 / Keep this poll separate from normal appliance updates."""
        super().__init__(
            hass, _LOGGER, config_entry=entry, name="Nature Remo ECHONET Lite",
            update_interval=timedelta(minutes=5),
        )
        self.main_coordinator = main_coordinator

    @property
    def appliances(self):
        """通常の一覧から対象機器を選ぶ。 / Select heaters from the normal appliance snapshot."""
        return {key: appliance for key, appliance in self.main_coordinator.data.items()
                if appliance.get("type") == WATER_HEATER_TYPE}

    async def _async_update_data(self):
        """全体を検証してからスナップショットを返す。 / Validate before returning a new snapshot."""
        heaters = self.appliances
        if not heaters:
            return {}
        try:
            response = await self.main_coordinator.api.get_echonetlite_appliances()
        except NatureRemoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NatureRemoRateLimitError as err:
            raise UpdateFailed(str(err), retry_after=err.retry_after) from err
        except NatureRemoError as err:
            raise UpdateFailed(str(err)) from err

        if not isinstance(response, dict) or not isinstance(response.get("appliances"), list):
            raise UpdateFailed("ECHONET Lite の応答形式が不正です / Invalid ECHONET Lite response")
        snapshot = {}
        for appliance in response["appliances"]:
            if not isinstance(appliance, dict):
                raise UpdateFailed("家電の応答形式が不正です / Invalid appliance response")
            appliance_id = appliance.get("id")
            if not isinstance(appliance_id, str) or appliance_id not in heaters:
                continue
            if not isinstance(appliance.get("properties"), list):
                raise UpdateFailed("プロパティの応答形式が不正です / Invalid properties response")
            snapshot[appliance_id] = parse_water_heater_properties(appliance["properties"])
        return snapshot


class NatureRemoWaterHeaterEntity(CoordinatorEntity[NatureRemoEchonetLiteCoordinator]):
    """エコキュートの家電デバイスに紐付く共通処理。 / Shared appliance identity for heater entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, appliance, description):
        """EPC ごとの安定 ID を設定。 / Set a stable ID for each EPC."""
        super().__init__(coordinator)
        self.entity_description = description
        self._appliance_id = appliance["id"]
        self._attr_unique_id = f"nature_remo_water_heater_{self._appliance_id}_{description.key}"
        self._attr_device_info = appliance_device_info(appliance)

    @property
    def available(self):
        """取得成功と機器の存在を確認。 / Require a successful poll and a present appliance."""
        return super().available and self._appliance_id in self.coordinator.data

    @property
    def value(self):
        """欠けた EPC は不明。 / Missing EPCs are unknown."""
        return self.coordinator.data.get(self._appliance_id, {}).get(self.entity_description.key)
