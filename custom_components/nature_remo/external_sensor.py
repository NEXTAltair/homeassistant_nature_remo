"""外部センサーの中継。 / Relay external sensor readings."""

from math import isfinite

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .device import appliance_device_info


def external_relay_ids(hass):
    """循環を防ぐため全エントリの中継 ID を集める。 / Collect relays across entries to prevent cycles."""
    return {
        entity_id for entity_id in er.async_get(hass).entities
        if is_external_relay(hass, entity_id)
    }


def is_external_relay(hass, entity_id):
    """中継かどうかをレジストリで確認。 / Identify a relay using the registry."""
    entity = er.async_get(hass).async_get(entity_id)
    return entity is not None and entity.platform == DOMAIN and entity.unique_id.startswith((
        "nature_remo_external_temperature_", "nature_remo_external_humidity_"
    ))


class NatureRemoExternalSensor(SensorEntity):
    """元センサーの値と属性をエアコンのデバイスに表示。 / Show source readings on the AC device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_native_value = None
    _attr_native_unit_of_measurement = None
    _attr_device_class = None
    _attr_state_class = None

    def __init__(self, appliance, kind, source_entity_id, source_state):
        self._added = False
        self._listeners = set()
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
        return self._added and self.native_value is not None

    @callback
    def async_add_listener(self, listener):
        """中継値の参照元に変更を通知する。 / Notify consumers of relayed value changes."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify_listeners(self):
        """entity_id に依存せず通知。 / Notify independently of entity ID changes."""
        for listener in tuple(self._listeners):
            listener()

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
        state = None if is_external_relay(self.hass, self._source_entity_id) else event.data["new_state"]
        self._read_source(state)
        self.async_write_ha_state()
        self._notify_listeners()

    async def async_added_to_hass(self):
        """登録中だけ元センサーを監視。 / Track the source while the entity is registered."""
        await super().async_added_to_hass()
        self._added = True
        if is_external_relay(self.hass, self._source_entity_id):
            self._read_source(None)
        else:
            self._read_source(self.hass.states.get(self._source_entity_id))
            self.async_on_remove(async_track_state_change_event(
                self.hass, [self._source_entity_id], self._source_changed
            ))
        self._notify_listeners()

    async def async_will_remove_from_hass(self):
        """無効化・削除中は参照元を内蔵値へ戻す。 / Consumers fall back while disabled or removed."""
        self._added = False
        self._notify_listeners()
        await super().async_will_remove_from_hass()
