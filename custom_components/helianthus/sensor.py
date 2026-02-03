"""Diagnostic sensors for Helianthus device inventory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import build_device_id
from .energy import compute_total


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
    semantic_coordinator = data.get("semantic_coordinator")
    energy_coordinator = data.get("energy_coordinator")

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

    if semantic_coordinator and semantic_coordinator.data:
        zones = semantic_coordinator.data.get("zones", []) or []
        for zone in zones:
            zone_id = zone.get("id")
            if zone_id:
                sensors.append(
                    HelianthusDemandSensor(
                        semantic_coordinator,
                        f"Zone {zone_id}",
                        ("zone", zone_id),
                    )
                )
        sensors.append(
            HelianthusDemandSensor(
                semantic_coordinator,
                "DHW",
                ("dhw", None),
            )
        )

    if energy_coordinator and energy_coordinator.data:
        sensors.extend(
            [
                HelianthusEnergySensor(energy_coordinator, "gas", "dhw"),
                HelianthusEnergySensor(energy_coordinator, "gas", "climate"),
                HelianthusEnergySensor(energy_coordinator, "electric", "dhw"),
                HelianthusEnergySensor(energy_coordinator, "electric", "climate"),
                HelianthusEnergySensor(energy_coordinator, "solar", "dhw"),
                HelianthusEnergySensor(energy_coordinator, "solar", "climate"),
            ]
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


class HelianthusDemandSensor(CoordinatorEntity, SensorEntity):
    """Heating demand sensor (percentage)."""

    entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, label: str, target: tuple[str, str | None]) -> None:
        super().__init__(coordinator)
        self._target = target
        self._attr_name = f"{label} Heating Demand"
        self._attr_unique_id = f"{target[0]}-{target[1] or 'dhw'}-heating-demand"

    @property
    def device_info(self) -> DeviceInfo:
        if self._target[0] == "zone":
            identifier = (DOMAIN, f"zone-{self._target[1]}")
        else:
            identifier = (DOMAIN, "dhw")
        return DeviceInfo(identifiers={identifier})

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        kind, zone_id = self._target
        if kind == "zone":
            for zone in self.coordinator.data.get("zones", []) or []:
                if zone.get("id") == zone_id:
                    return zone.get("heatingDemand")
            return None
        dhw = self.coordinator.data.get("dhw") or {}
        return dhw.get("heatingDemand")


class HelianthusEnergySensor(CoordinatorEntity, SensorEntity):
    """Energy total sensor (kWh)."""

    entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, source: str, usage: str) -> None:
        super().__init__(coordinator)
        self._source = source
        self._usage = usage
        self._attr_name = f"{source.capitalize()} {usage.upper()} Energy"
        self._attr_unique_id = f"energy-{source}-{usage}"

    @property
    def device_info(self) -> DeviceInfo:
        identifier = (DOMAIN, "energy")
        return DeviceInfo(
            identifiers={identifier},
            manufacturer="Helianthus",
            model="Virtual Energy",
            name="Energy",
        )

    def _series(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        totals = payload.get("energyTotals") or {}
        channel = totals.get(self._source, {}) if isinstance(totals, dict) else {}
        return channel.get(self._usage, {}) if isinstance(channel, dict) else {}

    @property
    def native_value(self) -> Any:
        series = self._series()
        yearly = series.get("yearly", []) if isinstance(series, dict) else []
        today = series.get("today", 0.0) if isinstance(series, dict) else 0.0
        return compute_total(yearly, today)
