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

STATUS_FIELDS = [
    InventoryField("status", "Status"),
    InventoryField("firmwareVersion", "Firmware Version"),
    InventoryField("updatesAvailable", "Updates Available"),
]


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinator = data["device_coordinator"]
    status_coordinator = data["status_coordinator"]

    sensors: list[HelianthusInventorySensor] = []
    for device in device_coordinator.data or []:
        sensors.extend(
            HelianthusInventorySensor(device_coordinator, device, field)
            for field in INVENTORY_FIELDS
        )

    daemon_identifier = (DOMAIN, "daemon")
    adapter_identifier = (DOMAIN, f"adapter-{entry.entry_id}")

    status_entries = status_coordinator.data or {}
    daemon_status = status_entries.get("daemon", {})
    adapter_status = status_entries.get("adapter", {})

    sensors.extend(
        HelianthusStatusSensor(status_coordinator, "Daemon", daemon_status, daemon_identifier, field)
        for field in STATUS_FIELDS
    )
    sensors.extend(
        HelianthusStatusSensor(status_coordinator, "Adapter", adapter_status, adapter_identifier, field)
        for field in STATUS_FIELDS
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


class HelianthusStatusSensor(CoordinatorEntity, SensorEntity):
    """Daemon/adapter status sensor."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        target_name: str,
        status: dict[str, Any],
        identifier: tuple[str, str],
        field: InventoryField,
    ) -> None:
        super().__init__(coordinator)
        self._status = status
        self._field = field
        self._identifier = identifier
        self._attr_name = f"{target_name} {field.name}"
        self._attr_unique_id = f"{identifier[1]}-{field.key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._identifier})

    @property
    def native_value(self) -> Any:
        return self._status.get(self._field.key)
