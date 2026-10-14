"""本体ごとのオプション設定。 / Per-hardware options flow."""

from math import isfinite

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, PERCENTAGE, UnitOfTemperature
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import DOMAIN
from .device import remo_registry_devices


class NatureRemoOptionsFlowHandler(config_entries.OptionsFlow):
    """固定キーで翻訳し、既存の保存キーを維持する。 / Translate fixed fields and preserve storage keys."""

    async def async_step_init(self, user_input=None):
        """共通設定と対象本体を選ぶ。 / Choose general settings and hardware."""
        self._devices = {device.id: device for device in remo_registry_devices(self.hass, self.config_entry)}
        if user_input is not None:
            self._options = dict(self.config_entry.options)
            self._options["update_interval"] = user_input["update_interval"]
            if device_id := user_input.get("device"):
                self._device = self._devices[device_id]
                self._remo_id = next(identifier for domain, identifier in self._device.identifiers if domain == DOMAIN)
                coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {}).get("coordinator")
                self._has_aircon = coordinator is not None and any(
                    appliance["device"]["device_id"] == self._remo_id
                    for appliance in coordinator.aircons.values()
                )
                return await self.async_step_device()
            return self.async_create_entry(title="", data=self._options)

        schema = {
            vol.Required("update_interval", default=self.config_entry.options.get("update_interval", 60)): vol.In([30, 60, 90]),
        }
        if self._devices:
            schema[vol.Optional("device")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=[
                    {"value": device.id, "label": device.name_by_user or device.name or "Nature Remo"}
                    for device in self._devices.values()
                ], mode=selector.SelectSelectorMode.DROPDOWN)
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))

    async def async_step_device(self, user_input=None):
        """選んだ本体の設定を検証する。 / Validate settings for the selected hardware."""
        errors = {}
        values = user_input.get("device", {}) if user_input is not None else {}
        if user_input is not None:
            if self._has_aircon:
                for kind in ("temperature", "humidity"):
                    field = f"external_{kind}"
                    if entity_id := values.get(field):
                        if error := self._sensor_error(entity_id, kind):
                            # セクション内の欄にエラーは渡されないため、セクションに表示。
                            # Nested fields do not receive errors; show them on the section.
                            errors["device"] = f"{kind}_{error}"
                            break
            if not errors:
                self._options[self._device.id] = values.get("ip_address", "")
                if self._has_aircon:
                    for kind in ("temperature", "humidity"):
                        field = f"external_{kind}"
                        key = f"{field}_{self._remo_id}"
                        if entity_id := values.get(field):
                            self._options[key] = entity_id
                        else:
                            self._options.pop(key, None)
                return self.async_create_entry(title="", data=self._options)

        # suggested_value を使い、選択解除が既定値で上書きされないようにする。
        # Use suggestions so clearing a selection does not reinsert a default.
        fields = {
            vol.Optional("ip_address", description={"suggested_value": values.get("ip_address", self._options.get(self._device.id, ""))}): str,
        }
        if self._has_aircon:
            for kind in ("temperature", "humidity"):
                field = f"external_{kind}"
                selected = values.get(field) if user_input is not None else self._options.get(f"{field}_{self._remo_id}")
                fields[vol.Optional(field, description={"suggested_value": selected})] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class=kind, multiple=False)
                )
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required("device"): section(vol.Schema(fields), {"collapsed": False})}),
            errors=errors,
            description_placeholders={"device_name": self._device.name_by_user or self._device.name or "Nature Remo"},
        )

    def _sensor_error(self, entity_id, kind):
        """存在・有限数値・単位を検証。 / Validate existence, a finite number and units."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return "entity_not_found"
        try:
            if not isfinite(float(state.state)):
                return "invalid_number"
        except (TypeError, ValueError):
            return "invalid_number"
        units = (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT) if kind == "temperature" else (PERCENTAGE,)
        if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) not in units:
            return "invalid_unit"
        return None
