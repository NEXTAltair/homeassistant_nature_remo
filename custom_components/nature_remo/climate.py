"""Nature Remo エアコン。 / Nature Remo air conditioners."""

import math

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import DOMAIN

MODE_MAP = {
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "warm",
    HVACMode.DRY: "dry",
    HVACMode.FAN_ONLY: "blow",
    HVACMode.AUTO: "auto",
}


async def async_setup_entry(hass, entry, async_add_entities):
    """エアコンを追加。 / Add air conditioners."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NatureRemoClimate(data["coordinator"], control, entry.entry_id)
        for control in data["aircon_controls"].values()
    )


class NatureRemoClimate(CoordinatorEntity, ClimateEntity):
    """家電応答から状態を表示する。 / Display state from the appliance response."""

    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator, control, entry_id):
        super().__init__(coordinator)
        self.control = control
        self._entry_id = entry_id
        self._device_id = control.appliance["device"]["id"]
        self._attr_unique_id = f"nature_remo_climate_{control.appliance_id}"
        self._attr_name = f"Nature Remo {control.appliance['nickname']}"

    @property
    def device_info(self):
        device = self.control.appliance["device"]
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": device["name"],
            "manufacturer": "Nature",
            "model": device.get("firmware_version", "Nature Remo"),
        }

    @property
    def available(self):
        return super().available and bool(self.control.appliance)

    @property
    def supported_features(self):
        features = ClimateEntityFeature.TURN_OFF
        if "auto" in self.control.modes:
            features |= ClimateEntityFeature.TURN_ON
        if self._temperatures:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self.fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self.swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        if self.swing_horizontal_modes:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        return features

    @property
    def temperature_unit(self):
        return UnitOfTemperature.FAHRENHEIT if self.control.temperature_unit == "f" else UnitOfTemperature.CELSIUS

    @property
    def hvac_modes(self):
        return [HVACMode.OFF, *(mode for mode, remo in MODE_MAP.items() if remo in self.control.modes)]

    @property
    def hvac_mode(self):
        if self.control.settings.get("button") == "power-off":
            return HVACMode.OFF
        return next((mode for mode, remo in MODE_MAP.items() if remo == self.control.settings.get("mode")), None)

    @property
    def _mode_options(self):
        return self.control.modes.get(self.control.settings.get("mode"), {})

    @property
    def _temperatures(self):
        if self.control.relative_mode(self.control.settings.get("mode")):
            return []
        return [float(value) for value in self._mode_options.get("temp", []) if value]

    @property
    def min_temp(self):
        return min(self._temperatures) if self._temperatures else super().min_temp

    @property
    def max_temp(self):
        return max(self._temperatures) if self._temperatures else super().max_temp

    @property
    def target_temperature_step(self):
        values = sorted(self._temperatures)
        return min((b - a for a, b in zip(values, values[1:]) if b > a), default=1.0)

    @property
    def target_temperature(self):
        if not self._temperatures:
            return None
        try:
            return float(self.control.settings.get("temp"))
        except (TypeError, ValueError):
            return None

    @property
    def fan_modes(self):
        return self._mode_options.get("vol", [])

    @property
    def fan_mode(self):
        return self.control.settings.get("vol")

    @property
    def swing_modes(self):
        choices = self._mode_options.get("dir", [])
        return choices if choices != [""] else []

    @property
    def swing_mode(self):
        return self.control.settings.get("dir") if self.swing_modes else None

    @property
    def swing_horizontal_modes(self):
        choices = self._mode_options.get("dirh", [])
        return choices if choices != [""] else []

    @property
    def swing_horizontal_mode(self):
        return self.control.settings.get("dirh") if self.swing_horizontal_modes else None

    def _external_entity_id(self, kind):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.options.get(f"external_{kind}_{self._device_id}") if entry else None

    def _current_value(self, kind, event):
        """外部値が有効なら優先。 / Prefer a valid external reading."""
        entity_id = self._external_entity_id(kind)
        if entity_id and (state := self.hass.states.get(entity_id)):
            try:
                value = float(state.state)
                if math.isfinite(value):
                    if kind == "temperature":
                        unit = state.attributes.get("unit_of_measurement", UnitOfTemperature.CELSIUS)
                        if unit in (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT):
                            return TemperatureConverter.convert(value, unit, self.temperature_unit)
                    return value
            except ValueError:
                pass
        value = self.coordinator.devices.get(self._device_id, {}).get("events", {}).get(event, {}).get("val")
        if kind == "temperature" and value is not None:
            return TemperatureConverter.convert(value, UnitOfTemperature.CELSIUS, self.temperature_unit)
        return value

    @property
    def current_temperature(self):
        return self._current_value("temperature", "te")

    @property
    def current_humidity(self):
        return self._current_value("humidity", "hu")

    @callback
    def _external_changed(self, event):
        """外部センサーを即時反映。 / Immediately reflect external sensor changes."""
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        entity_ids = [self._external_entity_id(kind) for kind in ("temperature", "humidity")]
        if sources := [entity_id for entity_id in entity_ids if entity_id]:
            self.async_on_remove(async_track_state_change_event(self.hass, sources, self._external_changed))

    async def async_set_hvac_mode(self, hvac_mode):
        """モードを変更。 / Change the operating mode."""
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.control.async_send(operation_mode=MODE_MAP[hvac_mode])

    async def async_turn_on(self):
        """停止中だけ自動で起動。 / Start in auto only when off."""
        if "auto" not in self.control.modes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
        if self.hvac_mode == HVACMode.OFF:
            await self.control.async_send(operation_mode="auto")

    async def async_turn_off(self):
        """停止する。 / Turn off the air conditioner."""
        await self.control.async_send(button="power-off")

    async def async_set_temperature(self, **kwargs):
        """絶対温度を設定。 / Set an absolute target temperature."""
        value = kwargs[ATTR_TEMPERATURE]
        if value not in self._temperatures:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
        await self.control.async_send(temperature=f"{value:g}")

    async def async_set_fan_mode(self, fan_mode):
        """風量を設定。 / Set the fan speed."""
        if fan_mode not in self.fan_modes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
        await self.control.async_send(air_volume=fan_mode)

    async def async_set_swing_mode(self, swing_mode):
        """上下風向を設定。 / Set vertical swing."""
        if swing_mode not in self.swing_modes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
        await self.control.async_send(air_direction=swing_mode)

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode):
        """左右風向を設定。 / Set horizontal swing."""
        if swing_horizontal_mode not in self.swing_horizontal_modes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_aircon_setting")
        await self.control.async_send(air_direction_h=swing_horizontal_mode)
