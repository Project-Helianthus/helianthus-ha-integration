"""Diagnostic sensors for Helianthus device inventory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import build_device_id


@dataclass(frozen=True)
class InventoryField:
    key: str
    name: str


INVENTORY_FIELDS = [
    InventoryField("manufacturer", "Manufacturer"),
    InventoryField("deviceId", "Model"),
    InventoryField("serialNumber", "Serial Number"),
    InventoryField("hardwareVersion", "Hardware Version"),
    InventoryField("softwareVersion", "Software Version"),
    InventoryField("macAddress", "MAC Address"),
    InventoryField("address", "Address"),
]


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    sensors: list[HelianthusInventorySensor] = []
    for device in coordinator.data or []:
        sensors.extend(
            HelianthusInventorySensor(coordinator, device, field)
            for field in INVENTORY_FIELDS
        )

    async_add_entities(sensors)


class HelianthusInventorySensor(CoordinatorEntity, SensorEntity):
    """Inventory field sensor."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device: dict[str, Any], field: InventoryField) -> None:
        super().__init__(coordinator)
        self._device = device
        self._field = field
        self._attr_name = f"{device.get('deviceId', 'Device')} {field.name}"
        self._attr_unique_id = self._build_unique_id()

    def _build_unique_id(self) -> str:
        device_id = build_device_id(
            model=self._device.get("deviceId"),
            serial_number=self._device.get("serialNumber"),
            mac_address=self._device.get("macAddress"),
            address=self._device.get("address"),
            hardware_version=self._device.get("hardwareVersion"),
            software_version=self._device.get("softwareVersion"),
        )
        return f"{device_id}-{self._field.key}"

    @property
    def device_info(self) -> DeviceInfo:
        device_id = build_device_id(
            model=self._device.get("deviceId"),
            serial_number=self._device.get("serialNumber"),
            mac_address=self._device.get("macAddress"),
            address=self._device.get("address"),
            hardware_version=self._device.get("hardwareVersion"),
            software_version=self._device.get("softwareVersion"),
        )
        return DeviceInfo(identifiers={(DOMAIN, device_id)})

    @property
    def native_value(self) -> Any:
        return self._device.get(self._field.key)
