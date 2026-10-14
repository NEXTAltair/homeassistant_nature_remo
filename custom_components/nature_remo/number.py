"""自動・除湿の温度補正。 / Temperature offset for auto and dry modes."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device import appliance_device_info


async def async_setup_entry(hass, entry, async_add_entities):
    """対応機種だけ追加。 / Add entities only for supported air conditioners."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TemperatureOffset(data["coordinator"], control)
        for control in data["aircon_controls"].values()
        if any(control.relative_mode(mode) for mode in ("auto", "dry"))
    )


class TemperatureOffset(CoordinatorEntity, NumberEntity):
    """相対温度の設定値。 / Relative temperature setting."""

    _attr_has_entity_name = True
    _attr_translation_key = "temperature_offset"
    _attr_native_min_value = -3
    _attr_native_max_value = 3
    _attr_native_step = 0.5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, control):
        super().__init__(coordinator)
        self.control = control
        self._attr_device_info = appliance_device_info(control.appliance)
        self._attr_unique_id = f"nature_remo_temperature_offset_{control.appliance_id}"

    @property
    def available(self):
        return (
            super().available
            and self.control.settings.get("button") != "power-off"
            and self.control.relative_mode(self.control.settings.get("mode"))
        )

    @property
    def native_value(self):
        return self.control.offset

    async def async_set_native_value(self, value):
        """有効な補正値だけ送信。 / Send only supported offsets."""
        choices = self.control.modes.get(self.control.settings.get("mode"), {}).get("temp", [])
        if not self.available or value not in [float(item) for item in choices if item]:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
        await self.control.async_send(temperature=f"{value:g}")
