from datetime import timedelta, datetime
import logging

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NatureRemoAuthError, NatureRemoError, NatureRemoRateLimitError


_LOGGER = logging.getLogger(__name__)


class NatureRemoCoordinator(DataUpdateCoordinator):
    """
    Nature Remo API からデータを取得するコーディネーター.
    Coordinator to fetch data from the Nature Remo API.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api, update_interval: int = 60
    ) -> None:
        """初期化."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Nature Remo Coordinator",
            update_interval=timedelta(seconds=update_interval),
        )
        self.api = api
        self.devices = {}
        self.aircons = {}
        self.lights = {}
        self.ir_remotes = {}
        self.smart_meters = {}
        self.motion_sensors = {}  # motionセンサー用の辞書

    async def _async_update_data(self):
        """APIを1回だけ呼び、各アプライアンスの情報を取得."""
        _LOGGER.info("NatureRemoCoordinator.async_update_data start.")
        try:
            devices = await self.api.get_devices()
            appliances = await self.api.get_appliances()
        except NatureRemoAuthError as err:
            # トークンが無効・失効 → 再認証フローを開始
            # Invalid or revoked token -> start the reauth flow
            raise ConfigEntryAuthFailed(str(err)) from err
        except NatureRemoRateLimitError as err:
            # 制限が解除されるまで次の取得を遅らせる / Defer the next poll until the limit resets
            raise UpdateFailed(
                f"APIの回数制限に達しました: {err}", retry_after=err.retry_after
            ) from err
        except NatureRemoError as err:
            raise UpdateFailed(f"APIエラー: {err}") from err
        except ClientError as err:
            raise UpdateFailed(f"通信エラー: {err}") from err  # ネットワーク系のエラー
        except TimeoutError as err:
            raise UpdateFailed("APIの応答がタイムアウトしました") from err

        # 取得失敗と正当な空一覧を区別する. 想定外の形式なら前回の状態を残したまま失敗にする
        # Tell a failed fetch from a legitimately empty list; on an unexpected shape, fail and keep the previous state
        if not isinstance(devices, list) or not isinstance(appliances, list):
            raise UpdateFailed(
                "Nature Remo APIの応答形式が想定外です / Unexpected Nature Remo API response"
            )

        # 新しいスナップショットを組み立ててから一度に入れ替える（途中で失敗しても半端な状態にしない）
        # Build the new snapshot first and swap it in at once (no half-updated state on failure)
        new_devices = {}
        motion_sensors = {}
        aircons = {}
        lights = {}
        smart_meters = {}
        ir_remotes = {}

        # Remoデバイス本体（温湿度センサーなど）の処理
        for device in devices:
            if not isinstance(device, dict):
                continue
            device_id = device.get("id")
            name = device.get("name", "Unnamed")
            newest_events = device.get("newest_events") or {}

            # モーションセンサー辞書の追加
            motion_event = newest_events.get("mo")
            if motion_event:
                created_at_str = motion_event.get("created_at")
                if created_at_str:
                    try:
                        # UTCのISO8601文字列をdatetime型に変換して保存しておく
                        created_at = datetime.fromisoformat(
                            created_at_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        _LOGGER.warning(
                            "モーション検出時刻を解釈できません / Invalid motion timestamp: %s",
                            created_at_str,
                        )
                    else:
                        motion_sensors[device_id] = {
                            "name": name,
                            "device_id": device_id,
                            "last_motion": created_at,
                            "firmware_version": device.get("firmware_version", ""),
                        }

            # 温湿度センサー辞書の追加
            new_devices[device_id] = {
                "name": name,
                "device_id": device_id,
                "events": newest_events,
                "model": device.get("model"),
                "firmware_version": device.get("firmware_version", ""),
            }

        for appliance in appliances:
            if not isinstance(appliance, dict):
                continue
            appliance_type = appliance.get("type")
            appliance_id = appliance.get("id")
            nickname = appliance.get("nickname", "Unnamed")
            device = appliance.get("device") or {}
            device_info = {
                "name": device.get("name", "No Name"),
                "device_id": device.get("id", ""),
                "firmware_version": device.get("firmware_version", ""),
            }
            appliance_info = {
                "name": nickname,
                "appliance_id": appliance_id,
                "device": device_info,
            }

            # スマートメーターの処理
            if appliance_type == "EL_SMART_METER":
                properties = (appliance.get("smart_meter") or {}).get(
                    "echonetlite_properties"
                )
                parsed = self.api.parse_smart_meter_properties(properties)

                _LOGGER.debug(
                    f"[{nickname}]buy_power:{parsed["buy_power"]}, sold_power:{parsed["sold_power"]}, current_power:{parsed["instant_power"]}"
                )
                smart_meters[appliance_id] = {
                    "name": nickname,
                    "appliance_id": appliance_id,
                    "device": device_info,
                    "buy_power": parsed["buy_power"],
                    "sold_power": parsed["sold_power"],
                    "current_power": parsed["instant_power"],
                }

            # エアコン（AC）の処理
            elif appliance_type == "AC":
                aircons[appliance_id] = appliance_info
                # signalsにボタンが設定されていればリモートエンティティに追加
                signals = appliance.get("signals", [])
                if signals:
                    ir_remotes[appliance_id] = {
                        "name": nickname,
                        "appliance_id": appliance_id,
                        "device": device_info,
                        "signals": signals,
                    }

            # 照明（LIGHT）の処理
            elif appliance_type == "LIGHT":
                lights[appliance_id] = appliance_info
                # signalsにボタンが設定されていればリモートエンティティに追加
                signals = appliance.get("signals", [])
                if signals:
                    ir_remotes[appliance_id] = {
                        "name": nickname,
                        "appliance_id": appliance_id,
                        "device": device_info,
                        "signals": signals,
                    }

            # IRの処理
            elif appliance_type == "IR":
                signals = appliance.get("signals", [])
                if signals:
                    ir_remotes[appliance_id] = {
                        "name": nickname,
                        "appliance_id": appliance_id,
                        "device": device_info,
                        "signals": signals,
                    }

        self.devices = new_devices
        self.motion_sensors = motion_sensors
        self.aircons = aircons
        self.lights = lights
        self.smart_meters = smart_meters
        self.ir_remotes = ir_remotes

        return {
            appliance["id"]: appliance
            for appliance in appliances
            if isinstance(appliance, dict) and "id" in appliance
        }
