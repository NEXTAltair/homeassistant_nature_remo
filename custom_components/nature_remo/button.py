"""学習済み信号のボタン。 / Buttons for learned infrared signals."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NatureRemoCoordinator
from .device import appliance_device_info


async def async_setup_entry(hass, entry, async_add_entities):
    """家電の種類を問わず信号を公開。 / Expose signals for every appliance type."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        NatureRemoSignalButton(coordinator, appliance, signal)
        for appliance in coordinator.data.values()
        for signal in appliance.get("signals", [])
    )


class NatureRemoSignalButton(CoordinatorEntity[NatureRemoCoordinator], ButtonEntity):
    """1つの学習済み信号を送信。 / Send one learned signal."""

    def __init__(self, coordinator, appliance, signal):
        """信号 ID と所属家電を保持。 / Keep the signal ID and owning appliance."""
        super().__init__(coordinator)
        self._appliance_id = appliance["id"]
        self._signal_id = signal["id"]
        self._attr_unique_id = f"nature_remo_signal_{self._signal_id}"
        self._attr_name = signal["name"]
        self._attr_device_info = appliance_device_info(appliance)

    @property
    def available(self):
        """削除済みの信号は送信不可。 / Do not offer removed signals."""
        appliance = self.coordinator.data.get(self._appliance_id, {})
        return super().available and any(
            signal["id"] == self._signal_id for signal in appliance.get("signals", [])
        )

    async def async_press(self):
        """API のエラーをそのまま通知。 / Propagate API errors to the service caller."""
        await self.coordinator.api.send_command_signal(self._signal_id)
