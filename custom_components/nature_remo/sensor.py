from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import NatureRemoCoordinator
from .const import DOMAIN
from .device import appliance_device_info, remo_device_info
from .external_sensor import NatureRemoExternalSensor, external_relay_ids


SENSOR_TYPES = {
    "te": {
        "name": "Temperature",
        "unit": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
    },
    "hu": {
        "name": "Humidity",
        "unit": "%",
        "device_class": "humidity",
        "state_class": "measurement",
    },
    "il": {
        "name": "Illuminance",
        "unit": None,  # 単位は指定しない
        "device_class": "illuminance",
        "state_class": "measurement",
    },
    "buy_power": {
        "name": "Buy Power",
        "unit": "kWh",
        "device_class": "energy",
        "state_class": "total_increasing",
    },
    "sold_power": {
        "name": "Sold Power",
        "unit": "kWh",
        "device_class": "energy",
        "state_class": "total_increasing",
    },
    "current_power": {
        "name": "Current Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
    },
}


async def async_setup_entry(hass, entry, async_add_entities):
    """
    インテグレーション初期化時に呼ばれるセットアップ関数
    Nature Remoから取得した情報を元にセンサーエンティティを追加する

    Called when the integration is initialized.
    Adds sensor entities based on information retrieved from Nature Remo.
    """
    coordinator: NatureRemoCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    entities = []

    # 設定済みの外部センサーを各エアコンに中継する。
    # Relay configured external sensors on each air conditioner.
    external_sensors = {}
    relay_ids = external_relay_ids(hass)
    for appliance_id, data in coordinator.aircons.items():
        for kind in ("temperature", "humidity"):
            if source_id := entry.options.get(f"external_{kind}_{data['device']['device_id']}"):
                if source_id in relay_ids:
                    continue
                relay = NatureRemoExternalSensor(
                    coordinator.data[appliance_id], kind, source_id, hass.states.get(source_id)
                )
                entities.append(relay)
                external_sensors.setdefault(appliance_id, {})[kind] = relay
    hass.data[DOMAIN][entry.entry_id]["external_sensors"] = external_sensors

    # 電気使用量センサー
    for appliance_id, data in coordinator.smart_meters.items():
        for key, desc in SENSOR_TYPES.items():
            if key in data:
                entities.append(
                    NatureRemoSensor(
                        coordinator,
                        appliance_id,
                        data["name"],
                        data["device"],
                        key,
                        desc,
                    )
                )

    # 温度、湿度、照度センサー
    for device_id, data in coordinator.devices.items():
        for key in ("te", "hu", "il"):
            if key in data["events"]:
                entities.append(
                    NatureRemoSensor(
                        coordinator,
                        device_id,
                        data["name"],
                        coordinator.devices[device_id],
                        key,
                        SENSOR_TYPES[key],
                    )
                )

    # モーションセンサー
    for device_id, data in coordinator.motion_sensors.items():
        # モーション検出センサー（検出時刻）の追加
        entities.append(
            NatureRemoMotionTimeSensor(
                coordinator,
                device_id,
                data["name"],
                coordinator.devices[device_id],
            )
        )

    async_add_entities(entities)


class NatureRemoSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, appliance_id, name, device, key, description):
        """
        センサークラスの初期化
        Initialize a base sensor entity for Nature Remo.
        """
        super().__init__(coordinator)
        self._attr_unique_id = f"nature_remo_sensor_{appliance_id}_{key}"
        self._attr_name = f"Nature Remo {name} {description['name']}"
        self._device = device
        self._appliance_id = appliance_id
        self._attr_native_unit_of_measurement = description["unit"]
        self._attr_device_class = description["device_class"]
        self._attr_state_class = description["state_class"]
        self._key = key
        if key not in ("te", "hu", "il"):
            self._appliance_device_info = appliance_device_info(coordinator.data[appliance_id])

    @property
    def device_info(self):
        """
        デバイス情報を返却する
        Return device info to be shown in Home Assistant UI.
        """
        if self._key not in ("te", "hu", "il"):
            return self._appliance_device_info
        return remo_device_info(self._device)

    @property
    def native_value(self):
        """
        センサーの現在値を返却する
        Return the current value of the sensor.
        """
        if self._key in ("te", "hu", "il"):
            # 温度、湿度、照度（最新の取得結果にイベントが無ければunknown）
            # Temperature, humidity, illuminance (unknown if the latest poll lacks the event)
            device = self.coordinator.devices.get(self._appliance_id, {})
            return device.get("events", {}).get(self._key, {}).get("val")

        # 電気使用量（欠測時はNone=unknown） / Electricity (None = unknown when missing)
        return self.coordinator.smart_meters.get(self._appliance_id, {}).get(self._key)

    @property
    def extra_state_attributes(self):
        """
        センサーに追加の属性（Attributes）を付与する
        照度センサー（il）の場合、Nature Remoが返す0〜200の相対的な明るさスケールを補足情報として返す。

        Adds extra attributes to the sensor.
        For illuminance sensors ("il"), it returns supplemental info about Nature Remo's relative brightness scale (0–200).
        """
        attributes = {}

        if self._key == "il":
            attributes["raw_sensor_scale"] = "0-200"
            attributes["note"] = "This is a relative scale used by Nature Remo."

        return attributes


class NatureRemoMotionTimeSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_id, name, device):
        """
        モーション検出時刻センサーの初期化
        Initialize the motion detection timestamp sensor.
        """
        super().__init__(coordinator)
        self._device_id = device_id
        self._device = device
        self._attr_name = f"Nature Remo {name} Last Motion"
        self._attr_unique_id = f"{device_id}_last_motion"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_native_unit_of_measurement = None

    @property
    def device_info(self):
        """
        モーション検出時刻センサーのデバイス情報
        Return device info for the motion time sensor.
        """
        return remo_device_info(self._device)

    @property
    def native_value(self):
        """
        最新のモーション検出時刻を datetime で返す
        Return the last motion detection timestamp as a datetime.
        """
        motion = self.coordinator.motion_sensors.get(self._device_id)
        if motion and "last_motion" in motion:
            return motion["last_motion"]
        return None
