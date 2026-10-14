import logging
import time
import aiohttp

from datetime import datetime
from decimal import Decimal

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)
NATURE_REMO_URL = "https://api.nature.global/1"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# 低圧スマート電力量メータ（ECHONET Lite クラス 0x0288）のプロパティ
# Properties of the low-voltage smart electric energy meter (ECHONET Lite class 0x0288)
EPC_COEFFICIENT = 0xD3  # 係数 / coefficient
EPC_CUMULATIVE_BUY = 0xE0  # 積算電力量計測値（正方向） / cumulative energy, normal direction
EPC_CUMULATIVE_UNIT = 0xE1  # 積算電力量単位 / cumulative energy unit
EPC_CUMULATIVE_SELL = 0xE3  # 積算電力量計測値（逆方向） / cumulative energy, reverse direction
EPC_INSTANT_POWER = 0xE7  # 瞬時電力計測値（W） / instantaneous power (W)

# 規格上の有効範囲. 範囲外（0xFFFFFFFE=計測値なし など）は欠測として扱う
# Valid ranges from the spec. Anything outside (e.g. 0xFFFFFFFE = no measurement) is missing
CUMULATIVE_RANGE = (0, 99_999_999)  # 0x00000000–0x05F5E0FF
INSTANT_POWER_RANGE = (-2_147_483_647, 2_147_483_645)  # 0x80000001–0x7FFFFFFD
COEFFICIENT_RANGE = (1, 999_999)  # 0x00000001–0x000F423F

# 積算電力量単位（0xE1）→ kWh への倍率 / Cumulative energy unit (0xE1) -> kWh multiplier
UNIT_TABLE = {
    0x00: Decimal("1"),
    0x01: Decimal("0.1"),
    0x02: Decimal("0.01"),
    0x03: Decimal("0.001"),
    0x04: Decimal("0.0001"),
    0x0A: Decimal("10"),
    0x0B: Decimal("100"),
    0x0C: Decimal("1000"),
    0x0D: Decimal("10000"),
}


class NatureRemoError(HomeAssistantError):
    """
    Nature Remo APIの呼び出しに失敗した.
    A Nature Remo API call failed.
    """


class NatureRemoAuthError(NatureRemoError):
    """
    アクセストークンが拒否された（無効・失効）.
    The access token was rejected (invalid or revoked).
    """


class NatureRemoRateLimitError(NatureRemoError):
    """
    APIの回数制限に達した. retry_afterは制限が解除されるまでの秒数（不明ならNone）.
    The API rate limit was reached. retry_after is the seconds until it resets (None if unknown).
    """

    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _log_rate_limit(headers) -> float | None:
    """
    レート制限系のヘッダをログ出力し、リセット時刻（epoch秒）を返す. ヘッダが無ければNone.
    Log the rate limit headers and return the reset time (epoch seconds), or None if absent.
    """
    try:
        reset_at = float(headers["X-Rate-Limit-Reset"])
    except (KeyError, TypeError, ValueError):
        reset_at = None

    _LOGGER.debug(
        "NatureRemo RateLimit → Limit: %s, Remaining: %s, Reset: %s",
        headers.get("X-Rate-Limit-Limit"),
        headers.get("X-Rate-Limit-Remaining"),
        datetime.fromtimestamp(reset_at) if reset_at is not None else None,
    )
    return reset_at


def _in_range(value: int | None, bounds: tuple[int, int]) -> int | None:
    """範囲内の値だけを返す. / Return the value only if it lies within bounds."""
    if value is None or not bounds[0] <= value <= bounds[1]:
        return None
    return value


class NatureRemoAPI:
    """
    Nature RemoのAPIを管理するクラス.
    Class to handle Nature Remo API communication.
    """

    def __init__(self, token, session: aiohttp.ClientSession) -> None:
        """
        Nature Remo APIの初期化. sessionはHomeAssistantの共有セッションを渡す.
        Initialize the Nature Remo API with Home Assistant's shared session.
        """
        self._token = token
        self._session = session

    async def _request(self, method: str, path: str, data=None, *, version: int = 1):
        """
        Nature Remo APIへリクエストを送り、レスポンスのJSONを返す. 失敗時は例外を送出する.
        Send a request to the Nature Remo API and return the JSON body; raise on failure.
        """
        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{NATURE_REMO_URL.rsplit('/', 1)[0]}/{version}{path}"
        async with self._session.request(
            method, url, headers=headers, data=data, timeout=REQUEST_TIMEOUT
        ) as response:
            reset_at = _log_rate_limit(response.headers)

            if response.status in (401, 403):
                raise NatureRemoAuthError(
                    f"Nature Remo API rejected the access token ({response.status})"
                )

            if response.status == 429:
                _LOGGER.warning("API制限に達しました! 429 Too Many Requests.")
                retry_after = (
                    max(reset_at - time.time(), 1) if reset_at is not None else None
                )
                raise NatureRemoRateLimitError(
                    "Nature Remo API rate limit reached", retry_after
                )

            if response.status != 200:
                error_text = await response.text()
                raise NatureRemoError(
                    f"Nature Remo API Error: {response.status} - {error_text[:200]}"
                )

            try:
                return await response.json()
            except ValueError as err:
                # aiohttp.ContentTypeError / json.JSONDecodeError
                raise NatureRemoError(
                    f"Nature Remo API returned invalid JSON: {err}"
                ) from err

    async def get_appliances(self):
        """
        Nature Remo API からすべての家電情報を取得.
        Fetch all appliance information from the Nature Remo API.
        """
        return await self._request("GET", "/appliances")

    async def get_devices(self):
        """
        Nature Remoのデバイス一覧を取得（温湿度センサー含む）.
        Retrieve the list of devices from Nature Remo, including temperature and humidity sensors.
        """
        return await self._request("GET", "/devices")

    async def send_command_climate(self, payload, appliance_id):
        """
        Nature Remo APIを使ってエアコンを操作.
        Control the air conditioner using the Nature Remo API.
        """
        _LOGGER.info("Setting payload: %s", payload)
        response_json = await self._request(
            "POST", f"/appliances/{appliance_id}/aircon_settings", data=payload, version=2
        )
        _LOGGER.info("エアコンの操作に成功しました: %s", response_json)
        return response_json

    async def send_light_command(self, appliance_id: str, command: str):
        """
        Nature Remo LightのON/OFFを送信.
        Send ON/OFF commands to Nature Remo Light.
        """
        _LOGGER.info(f"Send Light applicance_id:{appliance_id} command:{command}")
        response_json = await self._request(
            "POST", f"/appliances/{appliance_id}/light", data={"button": command}
        )
        _LOGGER.info("照明の操作に成功しました： %s", response_json)
        return response_json

    def parse_smart_meter_properties(self, properties: list[dict] | None) -> dict:
        """
        Nature Remo E / E Liteのechonetlite_propertiesを元に買電・売電・瞬時電力をパースして返却する.
        欠測・不正な値はNone（HAではunknown）とし、0で埋めない.
        Parse echonetlite_properties from Nature Remo E / E Lite to extract buy/sell power and instantaneous power.
        Missing or invalid values become None (unknown in HA) and are never replaced with 0.
        """
        # EPC → 値. 値が読めないものはNone（プロパティ自体が無いものはキー無し）
        # EPC -> value. Unreadable values are None; absent properties have no key
        raw: dict[int, int | None] = {}
        for prop in properties or []:
            if not isinstance(prop, dict):
                continue
            try:
                epc = int(prop["epc"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                raw[epc] = int(prop.get("val"))
            except (TypeError, ValueError):
                raw[epc] = None

        # 係数プロパティが無い機器は規格上の既定値1を使う. あっても不正なら換算しない
        # A meter without the coefficient uses the spec default 1; an invalid one blocks conversion
        coefficient = _in_range(raw.get(EPC_COEFFICIENT, 1), COEFFICIENT_RANGE)
        unit = UNIT_TABLE.get(raw.get(EPC_CUMULATIVE_UNIT))
        factor = (
            coefficient * unit if coefficient is not None and unit is not None else None
        )
        if factor is None:
            _LOGGER.debug(
                "スマートメーターの係数・単位が不正なため積算値を換算しません / "
                "Invalid coefficient or unit, skipping cumulative energy: %s",
                raw,
            )

        def to_kwh(epc: int) -> float | None:
            value = _in_range(raw.get(epc), CUMULATIVE_RANGE)
            if value is None or factor is None:
                return None
            return float(value * factor)

        # センサー用データとして返却
        return {
            "buy_power": to_kwh(EPC_CUMULATIVE_BUY),
            "sold_power": to_kwh(EPC_CUMULATIVE_SELL),
            "instant_power": _in_range(raw.get(EPC_INSTANT_POWER), INSTANT_POWER_RANGE),
        }

    async def send_command_signal(self, signal_id: str) -> None:
        """
        指定されたシグナルIDを使ってNature Remo APIを送信する.
        Send a signal by its ID using the Nature Remo API.
        """
        await self._request("POST", f"/signals/{signal_id}/send")
