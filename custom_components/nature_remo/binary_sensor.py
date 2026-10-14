"""人感センサー。 / Motion sensors."""

from datetime import datetime, timezone, timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device import remo_device_info


async def async_setup_entry(hass, entry, async_add_entities):
    """正しいドメインに人感センサーを登録。 / Register motion in its own domain."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        NatureRemoMotionBinarySensor(coordinator, device_id, data["name"], coordinator.devices[device_id])
        for device_id, data in coordinator.motion_sensors.items()
    )


class NatureRemoMotionBinarySensor(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, device_id, name, device):
        """
        モーション検出センサーの初期化
        Initialize the binary motion sensor entity.
        """
        super().__init__(coordinator)
        self._device = device
        self._device_id = device_id
        self._attr_name = f"Nature Remo {name} Motion"
        self._attr_unique_id = f"{device_id}_motion"
        self._attr_device_class = "motion"

    @property
    def device_info(self):
        """
        モーション検出センサーのデバイス情報
        Return device info for the motion sensor.
        """
        return remo_device_info(self._device)

    @property
    def is_on(self):
        """
        最後にモーション検出してから5分以内の場合は「ON」を返す
        Return True if motion was detected within the last 5 minutes.
        """
        motion = self.coordinator.motion_sensors.get(self._device_id)
        if motion and "last_motion" in motion:
            now = datetime.now(timezone.utc)
            return (now - motion["last_motion"]) < timedelta(minutes=5)
        return False
