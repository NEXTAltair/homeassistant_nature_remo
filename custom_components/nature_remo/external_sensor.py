"""外部センサーの中継。 / Relay external sensor readings."""

from math import isfinite

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from .device import appliance_device_info


class NatureRemoExternalSensor(SensorEntity):
    """元センサーの値と属性をエアコンのデバイスに表示。 / Show source readings on the AC device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_native_value = None
    _attr_native_unit_of_measurement = None
    _attr_device_class = None
    _attr_state_class = None

    def __init__(self, appliance, kind, source_entity_id, source_state):
        self._source_entity_id = source_entity_id
        self._attr_unique_id = f"nature_remo_external_{kind}_{appliance['id']}"
        self._attr_translation_key = f"external_{kind}"
        self._attr_device_info = appliance_device_info(appliance)
        self._attr_extra_state_attributes = {"source_entity_id": source_entity_id}
        self._read_source(source_state)
        # 初期表示単位は元センサーを優先し、以降の表示設定は HA に任せる。
        # Suggest the source unit initially, then respect HA display preferences.
        self._attr_suggested_unit_of_measurement = self.native_unit_of_measurement

    @property
    def available(self):
        return self.native_value is not None

    @callback
    def _read_source(self, state):
        """数値と属性を取り込み、欠測は None とする。 / Copy numeric data and metadata; missing is None."""
        self._attr_native_value = None
        if state is None:
            return
        self._attr_device_class = state.attributes.get("device_class")
        self._attr_state_class = state.attributes.get("state_class")
        self._attr_native_unit_of_measurement = state.attributes.get("unit_of_measurement")
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return
        if isfinite(value):
            self._attr_native_value = value

    @callback
    def _source_changed(self, event):
        """元センサーの変更を即時反映。 / Immediately reflect source changes."""
        self._read_source(event.data["new_state"])
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """登録中だけ元センサーを監視。 / Track the source while the entity is registered."""
        await super().async_added_to_hass()
        self._read_source(self.hass.states.get(self._source_entity_id))
        self.async_on_remove(async_track_state_change_event(
            self.hass, [self._source_entity_id], self._source_changed
        ))
