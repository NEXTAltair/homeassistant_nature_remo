"""本体と家電のデバイス情報。 / Device information for hardware and appliances."""

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


def remo_device_info(device):
    """Remo 本体の情報を作る。 / Build hardware device information."""
    return dr.DeviceInfo(
        identifiers={(DOMAIN, device.get("id") or device["device_id"])},
        name=device.get("name", "Nature Remo"),
        manufacturer="Nature",
        model=device.get("model") or "Nature Remo",
        sw_version=device.get("firmware_version"),
    )


def appliance_device_info(appliance):
    """家電の共通情報を作る。 / Build common appliance information."""
    model = appliance.get("model") or {}
    info = dr.DeviceInfo(
        identifiers={(DOMAIN, appliance["id"])},
        name=appliance.get("nickname", "Unnamed"),
        manufacturer=model.get("manufacturer"),
        model=model.get("name"),
    )
    return info


@callback
def async_register_devices(hass, entry, coordinator):
    """エンティティのない親も保持。 / Keep parents even when they have no entities."""
    registry = dr.async_get(hass)
    devices = dict(coordinator.devices)
    for appliance in coordinator.data.values():
        device = appliance.get("device") or {}
        if device.get("id"):
            devices.setdefault(device["id"], device)
    parents = {
        device_id: registry.async_get_or_create(
            config_entry_id=entry.entry_id, **remo_device_info(device)
        )
        for device_id, device in devices.items()
    }
    for appliance in coordinator.data.values():
        child = registry.async_get_or_create(
            config_entry_id=entry.entry_id, **appliance_device_info(appliance)
        )
        if parent := parents.get((appliance.get("device") or {}).get("id")):
            # 2025.12 と最新 HA の両方で有効な API。 / Supported on both 2025.12 and current HA.
            registry.async_update_device(child.id, via_device_id=parent.id)


def remo_registry_devices(hass, entry):
    """オプションの対象を本体に限定。 / Limit options to hardware devices."""
    return [
        device for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if device.via_device_id is None
        and any(domain == DOMAIN for domain, _ in device.identifiers)
    ]
