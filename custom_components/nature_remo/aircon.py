"""エアコン設定と v2 送信の共有処理。 / Shared aircon settings and v2 commands."""

import asyncio
import math

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN


class AirconControl:
    """climate と number の状態を共有する。 / Share state between climate and number."""

    def __init__(self, coordinator, appliance_id):
        self.coordinator = coordinator
        self.appliance_id = appliance_id
        self.offset = 0.0
        self._lock = asyncio.Lock()
        self.update_offset()

    @property
    def appliance(self):
        return (self.coordinator.data or {}).get(self.appliance_id, {})

    @property
    def settings(self):
        return self.appliance.get("settings") or {}

    @property
    def modes(self):
        return (self.appliance.get("aircon") or {}).get("range", {}).get("modes", {})

    @property
    def temperature_unit(self):
        return (self.appliance.get("aircon") or {}).get("tempUnit", "c")

    def relative_mode(self, mode):
        """相対温度のモードか判定。 / Detect modes with relative temperatures."""
        if mode not in ("auto", "dry"):
            return False
        values = self.modes.get(mode, {}).get("temp", [])
        try:
            numbers = [float(value) for value in values if value != ""]
        except (TypeError, ValueError):
            return False
        return bool(numbers) and min(numbers) < 0 and max(numbers) <= 3

    def update_offset(self):
        """最後に確認できた補正値を保持。 / Remember the last confirmed offset."""
        if self.relative_mode(self.settings.get("mode")):
            try:
                value = float(self.settings.get("temp"))
            except (TypeError, ValueError):
                return
            if math.isfinite(value) and -3 <= value <= 3:
                self.offset = value

    @staticmethod
    def _select(value, choices, fallback=""):
        """現在値が対象モードで使えなければ既定値にする。 / Select a valid mode value."""
        if not choices:
            return ""
        if value in choices:
            return value
        if fallback in choices:
            return fallback
        return choices[0]

    def _temperature(self, mode):
        """モードの温度範囲に合わせる。 / Choose a temperature valid for the mode."""
        choices = self.modes[mode].get("temp", [])
        if not choices or choices == [""]:
            return ""
        value = self.offset if self.relative_mode(mode) else self.settings.get("temp")
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 25.0
        if not self.relative_mode(mode) and self.relative_mode(self.settings.get("mode")):
            number = 25.0
        return min(choices, key=lambda choice: abs(float(choice) - number))

    async def async_send(self, **changes):
        """全項目を送信し成功応答だけを反映。 / Send all fields and apply only success."""
        async with self._lock:
            mode = changes.get("operation_mode", self.settings.get("mode"))
            if mode not in self.modes:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
            options = self.modes[mode]
            payload = {
                "operation_mode": mode,
                "temperature": self._temperature(mode),
                "temperature_unit": self.temperature_unit,
                "air_volume": self._select(self.settings.get("vol"), options.get("vol"), "auto"),
                "air_direction": self._select(self.settings.get("dir"), options.get("dir"), "auto"),
                "air_direction_h": self._select(self.settings.get("dirh"), options.get("dirh")),
                "button": "",
                **changes,
            }
            response = await self.coordinator.api.send_command_climate(payload, self.appliance_id)
            if not isinstance(response, dict) or not isinstance(response.get("settings"), dict):
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="invalid_aircon_response")
            # 応答全体を共有し、追加のポーリングはしない。 / Share the response without another poll.
            self.coordinator.async_set_updated_data({
                **self.coordinator.data, self.appliance_id: response,
            })
