"""TV のプリセットと学習信号。 / Preset and learned TV commands."""

from asyncio import Lock, sleep

from homeassistant.components.remote import DEFAULT_DELAY_SECS, DEFAULT_NUM_REPEATS, RemoteEntity
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NatureRemoCoordinator
from .device import appliance_device_info


class NatureRemoTVRemote(CoordinatorEntity[NatureRemoCoordinator], RemoteEntity):
    """赤外線による TV 操作。 / Control a TV through infrared commands."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True
    _attr_is_on = None

    def __init__(self, coordinator, appliance):
        """家電に紐付け、電源状態は不明で開始。 / Attach to the appliance with unknown power."""
        super().__init__(coordinator)
        self._appliance_id = appliance["id"]
        self._attr_unique_id = f"nature_remo_remote_{self._appliance_id}"
        self._attr_device_info = appliance_device_info(appliance)
        self._lock = Lock()

    @property
    def appliance(self):
        """最新の家電情報。 / Current appliance data."""
        return self.coordinator.data.get(self._appliance_id, {})

    @property
    def buttons(self):
        """空のプレースホルダーを除外。 / Exclude empty placeholders."""
        return [button for button in (self.appliance.get("tv") or {}).get("buttons", [])
                if isinstance(button.get("name"), str) and button["name"]]

    @property
    def available(self):
        """家電が存在するときに利用可能。 / Available while the appliance exists."""
        return super().available and self.appliance.get("type") == "TV"

    @property
    def extra_state_attributes(self):
        """入力と送信候補を表示。 / Expose input and command choices."""
        return {
            "input": ((self.appliance.get("tv") or {}).get("state") or {}).get("input"),
            "available_commands": [button["name"] for button in self.buttons]
            + [signal["name"] for signal in self.appliance.get("signals", [])],
        }

    def _resolve(self, command):
        """正式名、表示名、学習信号の順で解決。 / Resolve canonical names, labels, then signals."""
        normalized = command.casefold()
        for key in ("name", "label"):
            for button in self.buttons:
                if isinstance(button.get(key), str) and button[key].casefold() == normalized:
                    return "button", button["name"]
        for signal in self.appliance.get("signals", []):
            if signal["name"].casefold() == normalized:
                return "signal", signal["id"]
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="unknown_tv_command",
            translation_placeholders={"command": command},
        )

    async def _send(self, kind, value):
        """成功した送信だけを反映。 / Apply only successful sends."""
        if kind == "signal":
            await self.coordinator.api.send_command_signal(value)
            # 学習信号の効果は分からない。 / The effect of a learned signal is unknown.
            self._attr_is_on = None
        else:
            response = await self.coordinator.api.send_tv_command(self._appliance_id, value)
            if not isinstance(response, dict) or not isinstance(response.get("input"), str):
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="invalid_tv_response")
            if value.casefold() in ("power-on", "power-off", "power"):
                self._attr_is_on = {"power-on": True, "power-off": False}.get(value.casefold())
            if appliance := self.appliance:
                self.coordinator.async_set_updated_data({
                    **self.coordinator.data,
                    self._appliance_id: {
                        **appliance, "tv": {**(appliance.get("tv") or {}), "state": response},
                    },
                })
        self.async_write_ha_state()

    async def async_send_command(self, command, **kwargs):
        """コマンドを検証して順に送信。 / Validate commands and send them in order."""
        if isinstance(command, str):
            command = [command]
        async with self._lock:
            commands = [self._resolve(value) for value in command]
            pending = commands * kwargs.get("num_repeats", DEFAULT_NUM_REPEATS)
            for index, (kind, value) in enumerate(pending):
                if index and (delay := kwargs.get("delay_secs", DEFAULT_DELAY_SECS)) > 0:
                    await sleep(delay)
                await self._send(kind, value)

    async def _send_power(self, turn_on):
        """オン・オフが両方ある機種だけ送信。 / Require both discrete power commands."""
        async with self._lock:
            names = {button["name"].casefold(): button["name"] for button in self.buttons}
            if not {"power-on", "power-off"} <= names.keys():
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_tv_power")
            await self._send("button", names["power-on" if turn_on else "power-off"])

    async def async_turn_on(self, **kwargs):
        """離散オンを送信。 / Send discrete power on."""
        await self._send_power(True)

    async def async_turn_off(self, **kwargs):
        """離散オフを送信。 / Send discrete power off."""
        await self._send_power(False)
