"""Diagnostic sensors for Helianthus device inventory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import (
    build_bus_device_key,
    bus_identifier,
    dhw_identifier,
    energy_identifier,
    zone_identifier,
)
from .energy import compute_total


@dataclass(frozen=True)
class InventoryField:
    key: str
    name: str


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
    via_device = data.get("regulator_device_id") or data.get("adapter_device_id")

    sensors: list[SensorEntity] = []
    for device in device_coordinator.data or []:
        device_id = device.get("deviceId", "unknown")
        address = device.get("address")
        if address is None:
            continue
        bus_key = build_bus_device_key(model=str(device_id), address=int(address))
        bus_id = bus_identifier(entry.entry_id, bus_key)
        sensors.append(HelianthusBusAddressSensor(device_coordinator, bus_id, int(address)))

    status_entries = status_coordinator.data or {}
    daemon_status = status_entries.get("daemon", {})
    adapter_status = status_entries.get("adapter", {})

    sensors.extend(
        HelianthusStatusSensor(
            status_coordinator,
            "Daemon",
            daemon_status,
            data.get("daemon_device_id"),
            field,
        )
        for field in STATUS_FIELDS
    )
    sensors.extend(
        HelianthusStatusSensor(
            status_coordinator,
            "Adapter",
            adapter_status,
            data.get("adapter_device_id"),
            field,
        )
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
                        entry.entry_id,
                        via_device,
                        zone.get("name") or f"Zone {zone_id}",
                        ("zone", str(zone_id)),
                    )
                )
        sensors.append(
            HelianthusDemandSensor(
                semantic_coordinator,
                entry.entry_id,
                via_device,
                "DHW",
                ("dhw", None),
            )
        )

    if energy_coordinator and energy_coordinator.data:
        sensors.extend(
            [
                HelianthusEnergySensor(energy_coordinator, entry.entry_id, via_device, "gas", "dhw"),
                HelianthusEnergySensor(energy_coordinator, entry.entry_id, via_device, "gas", "climate"),
                HelianthusEnergySensor(energy_coordinator, entry.entry_id, via_device, "electric", "dhw"),
                HelianthusEnergySensor(energy_coordinator, entry.entry_id, via_device, "electric", "climate"),
                HelianthusEnergySensor(energy_coordinator, entry.entry_id, via_device, "solar", "dhw"),
                HelianthusEnergySensor(energy_coordinator, entry.entry_id, via_device, "solar", "climate"),
            ]
        )

    async_add_entities(sensors)


class HelianthusBusAddressSensor(CoordinatorEntity, SensorEntity):
    """eBUS address sensor for a physical bus device."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        device_id: tuple[str, str],
        address: int,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._address = address
        self._attr_name = "eBUS Address"
        self._attr_unique_id = f"{device_id[1]}-ebus-address"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._device_id})

    @property
    def native_value(self) -> Any:
        return f"0x{self._address:02x}"


class HelianthusStatusSensor(CoordinatorEntity, SensorEntity):
    """Daemon/adapter status sensor."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        target_name: str,
        status: dict[str, Any],
        identifier: tuple[str, str] | None,
        field: InventoryField,
    ) -> None:
        super().__init__(coordinator)
        self._status = status
        self._field = field
        self._identifier = identifier or (DOMAIN, f"unknown-{target_name.lower()}")
        self._attr_name = f"{target_name} {field.name}"
        self._attr_unique_id = f"{self._identifier[1]}-{field.key}"

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

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        label: str,
        target: tuple[str, str | None],
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._target = target
        self._attr_name = f"{label} Heating Demand"
        self._attr_unique_id = (
            f"{entry_id}-{target[0]}-{target[1] or 'dhw'}-heating-demand"
        )

    @property
    def device_info(self) -> DeviceInfo:
        if self._target[0] == "zone":
            identifier = zone_identifier(self._entry_id, str(self._target[1]))
            model = "Virtual Zone"
        else:
            identifier = dhw_identifier(self._entry_id)
            model = "Virtual DHW"
        return DeviceInfo(
            identifiers={identifier},
            manufacturer="Helianthus",
            model=model,
            via_device=self._via_device,
        )

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

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        source: str,
        usage: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._source = source
        self._usage = usage
        self._attr_name = f"{source.capitalize()} {usage.upper()} Energy"
        self._attr_unique_id = f"{entry_id}-energy-{source}-{usage}"

    @property
    def device_info(self) -> DeviceInfo:
        identifier = energy_identifier(self._entry_id)
        return DeviceInfo(
            identifiers={identifier},
            manufacturer="Helianthus",
            model="Virtual Energy",
            name="Energy",
            via_device=self._via_device,
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
