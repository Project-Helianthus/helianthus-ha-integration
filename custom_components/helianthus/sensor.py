"""Diagnostic sensors for Helianthus device inventory."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .energy import compute_total
from .pv_m2m import (
    PVM2MDescriptor,
    PVM2MFact,
    build_pv_device_identifier,
)
from .sensor_descriptors import (
    ADAPTER_STATUS_FIELDS,
    BOILER_DIAGNOSTICS_SENSOR_FIELDS,
    BOILER_STATE_SENSOR_FIELDS,
    CIRCUIT_SENSOR_FIELDS,
    CYLINDER_CONFIG_SENSOR_FIELDS,
    DAEMON_STATUS_FIELDS,
    REDUCED_BOILER_TEMPERATURE_FIELDS,
    STATUS_FIELDS,
    SYSTEM_SENSOR_FIELDS,
    _FM5_MODE_INTERPRETED,
    _RADIO_ROOM_CLASSES,
    _RADIO_STALE_GRACE_CYCLES,
    _SENSOR_DEVICE_CLASS_DURATION,
    _SENSOR_DEVICE_CLASS_HUMIDITY,
    _SENSOR_DEVICE_CLASS_PRESSURE,
    _SENSOR_STATE_CLASS_TOTAL_INCREASING,
    BoilerTemperatureField,
    CircuitSensorField,
    InventoryField,
    SystemSensorField,
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up the sensor platform through the private factory."""
    from .sensor_setup import async_setup_entry as setup_sensor_entry

    await setup_sensor_entry(hass, entry, async_add_entities)


class HelianthusBusAddressSensor(CoordinatorEntity, SensorEntity):
    """eBUS address sensor for a physical bus device."""

    _attr_has_entity_name = True
    entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

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

    _attr_has_entity_name = True
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
        self._target_key = str(target_name or "").strip().lower()
        self._fallback_status = status
        self._field = field
        self._identifier = identifier or (DOMAIN, f"unknown-{target_name.lower()}")
        self._attr_name = field.name
        self._attr_unique_id = f"{self._identifier[1]}-{field.key}"
        if field.icon is not None:
            self._attr_icon = field.icon
        if field.optional:
            self._attr_entity_registry_enabled_default = self.native_value is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._identifier})

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        status = data.get(self._target_key)
        if not isinstance(status, dict):
            status = self._fallback_status
        return status.get(self._field.key)


class HelianthusEEBusAdminSensor(CoordinatorEntity, SensorEntity):
    """One scalar health diagnostic for the isolated eeBUS AdminV1 boundary."""

    _attr_has_entity_name = True
    _attr_name = "eeBUS Admin Available"
    _attr_icon = "mdi:shield-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry_id: str, origin: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._origin = origin
        self._attr_unique_id = f"{entry_id}-eebus-admin-available"

    @property
    def native_value(self) -> str:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        status = data.get("status") if isinstance(data.get("status"), dict) else {}
        readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
        value = readiness.get("eebus_readiness")
        return value.lower() if isinstance(value, str) else "unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        status = data.get("status") if isinstance(data.get("status"), dict) else {}
        diagnostic_error = data.get("diagnostic_error")
        if not status:
            return {
                **(
                    {"diagnostic_error": diagnostic_error}
                    if isinstance(diagnostic_error, str)
                    else {}
                ),
                "fresh": False,
            }
        readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
        counts = {
            key: status.get(key)
            for key in (
                "trusted_count",
                "connected_count",
                "discovered_count",
                "candidate_count",
            )
            if isinstance(status.get(key), int) and not isinstance(status.get(key), bool)
        }
        action = status.get("active_action") if isinstance(status.get("active_action"), dict) else {}
        return {
            "process_readiness": readiness.get("process_readiness")
            if isinstance(readiness.get("process_readiness"), str)
            else "NOT_READY",
            **(
                {"degraded_reason": readiness["eebus_degraded_reason"]}
                if isinstance(readiness.get("eebus_degraded_reason"), str)
                else {}
            ),
            "pairing_window": status.get("pairing_window")
            if isinstance(status.get("pairing_window"), str)
            else "unknown",
            "discovery": status.get("discovery") if isinstance(status.get("discovery"), str) else "unavailable",
            **counts,
            **(
                {
                    "active_action_kind": action.get("kind"),
                    "active_action_state": action.get("state"),
                    "active_action_outcome": action.get("outcome"),
                    "active_action_retryable": action.get("retryable"),
                }
                if action
                else {}
            ),
            "diagnostic_error": diagnostic_error
            if isinstance(diagnostic_error, str)
            else None,
            "fresh": bool(data.get("available")) and not bool(data.get("stale_views")),
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}-eebus-admin")},
            name="eeBUS Admin",
            configuration_url=self._origin.rstrip("/") + "/portal/eebus",
        )


class HelianthusBoilerTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Reduced-profile boiler temperature sensor on physical BAI00."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator,
        entry_id: str,
        boiler_device_id: tuple[str, str],
        field: BoilerTemperatureField,
    ) -> None:
        super().__init__(coordinator)
        self._boiler_device_id = boiler_device_id
        self._field = field
        self._attr_name = field.label
        self._attr_unique_id = f"{entry_id}-boiler-{field.key}"
        self._attr_entity_registry_enabled_default = self.native_value is not None

    def _boiler_state(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boiler_status") or {}
        return boiler_status.get("state") or {}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._boiler_device_id})

    @property
    def native_value(self) -> Any:
        state = self._boiler_state()
        return state.get(self._field.key)


class HelianthusBoilerStateSensor(CoordinatorEntity, SensorEntity):
    """Read-only boiler state sensor attached directly to the physical boiler."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        boiler_device_id: tuple[str, str],
        field: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._boiler_device_id = boiler_device_id
        self._field = field
        self._attr_name = str(field["label"])
        self._attr_unique_id = f"{entry_id}-boiler-sensor-{field['key']}"
        if field.get("native_unit") is not None:
            self._attr_native_unit_of_measurement = field["native_unit"]
        if field.get("state_class") is not None:
            self._attr_state_class = field["state_class"]
        if field.get("entity_category") is not None:
            self._attr_entity_category = field["entity_category"]
        if field.get("icon") is not None:
            self._attr_icon = field["icon"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._boiler_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boiler_status") or {}
        state = boiler_status.get("state") if isinstance(boiler_status, dict) else {}
        value = state.get(self._field["key"]) if isinstance(state, dict) else None
        if value is None:
            return None
        if self._field.get("cast_int"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


class HelianthusBoilerDiagnosticsSensor(CoordinatorEntity, SensorEntity):
    """Boiler diagnostic counter sensor (hours, starts, deactivations)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        boiler_device_id: tuple[str, str],
        field: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._boiler_device_id = boiler_device_id
        self._field = field
        self._attr_name = field['label']
        self._attr_unique_id = f"{entry_id}-boiler-diag-{field['key']}"
        if field.get("device_class") is not None:
            self._attr_device_class = field["device_class"]
        if field.get("native_unit") is not None:
            self._attr_native_unit_of_measurement = field["native_unit"]
        if field.get("state_class") is not None:
            self._attr_state_class = field["state_class"]
        if field.get("entity_category") is not None:
            self._attr_entity_category = field["entity_category"]
        if field.get("icon") is not None:
            self._attr_icon = field["icon"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._boiler_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boiler_status") or {}
        diagnostics = boiler_status.get("diagnostics") if isinstance(boiler_status, dict) else {}
        value = diagnostics.get(self._field["key"]) if isinstance(diagnostics, dict) else None
        if value is None:
            return None
        if self._field.get("cast_int"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


class HelianthusCircuitSensor(CoordinatorEntity, SensorEntity):
    """Per-circuit sensor values sourced from the circuit coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        circuit_index: int,
        initial_name: str,
        field: CircuitSensorField,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._circuit_index = circuit_index
        self._initial_name = initial_name
        self._field = field
        self._attr_unique_id = f"{entry_id}-circuit-{circuit_index}-sensor-{field.key}"
        self._attr_name = field.label
        if field.device_class is not None:
            self._attr_device_class = field.device_class
        if field.native_unit is not None:
            self._attr_native_unit_of_measurement = field.native_unit
        if field.state_class is not None:
            self._attr_state_class = field.state_class
        if field.entity_category is not None:
            self._attr_entity_category = field.entity_category
        if field.icon is not None:
            self._attr_icon = field.icon
        self._attr_entity_registry_enabled_default = self.native_value is not None

    def _circuit(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for circuit in payload.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            if _parse_circuit_index(circuit.get("index")) == self._circuit_index:
                return circuit
        return {}

    @property
    def name(self) -> str | None:
        return self._field.label

    def _device_name(self) -> str:
        circuit = self._circuit()
        if circuit:
            return _circuit_name(circuit, self._circuit_index)
        return self._initial_name

    @property
    def device_info(self) -> DeviceInfo:
        identifier = circuit_identifier(self._entry_id, self._circuit_index)
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model="Circuit",
            name=self._device_name(),
        )

    @property
    def native_value(self) -> Any:
        circuit = self._circuit()
        state = circuit.get("state") if isinstance(circuit.get("state"), dict) else {}
        value = state.get(self._field.key)
        if value is None:
            return None
        if self._field.cast_int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._field.include_circuit_attributes:
            return {}
        circuit = self._circuit()
        attrs: dict[str, Any] = {
            "circuit_index": self._circuit_index,
        }
        circuit_type = circuit.get("circuit_type")
        if circuit_type is not None and str(circuit_type).strip() != "":
            attrs["circuit_type"] = str(circuit_type)
        has_mixer = circuit.get("has_mixer")
        if isinstance(has_mixer, bool):
            attrs["has_mixer"] = has_mixer
        return attrs


class HelianthusSystemSensor(CoordinatorEntity, SensorEntity):
    """System-level BASV2 sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        regulator_device_id: tuple[str, str],
        field: SystemSensorField,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._regulator_device_id = regulator_device_id
        self._field = field
        self._attr_name = field.label
        self._attr_unique_id = f"{entry_id}-system-sensor-{field.key}"
        if field.device_class is not None:
            self._attr_device_class = field.device_class
        if field.native_unit is not None:
            self._attr_native_unit_of_measurement = field.native_unit
        if field.state_class is not None:
            self._attr_state_class = field.state_class
        if field.entity_category is not None:
            self._attr_entity_category = field.entity_category
        if field.icon is not None:
            self._attr_icon = field.icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._regulator_device_id},
            manufacturer=self._manufacturer,
        )

    def _bucket(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        source = payload.get(self._field.source)
        if isinstance(source, dict):
            return source
        return {}

    @property
    def native_value(self) -> Any:
        value = self._bucket().get(self._field.key)
        if value is None:
            return None
        if self._field.cast_int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            return {}
        return {
            key: metadata[key]
            for key in ("gateway_brand", "gateway_vendor")
            if key in metadata
        }


class HelianthusRadioSensor(CoordinatorEntity, SensorEntity):
    """Per-slot remote radio sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        radio_device_id: tuple[str, str],
        radio_name: str,
        group: int,
        instance: int,
        key: str,
        label: str,
        device_class: str | None = None,
        native_unit: str | None = None,
        state_class: str | None = None,
        entity_category: str | None = None,
        cast_int: bool = False,
        icon: str | None = None,
        zone_name: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._radio_device_id = radio_device_id
        self._radio_name = radio_name
        self._zone_name = zone_name
        self._group = group
        self._instance = instance
        self._key = key
        self._cast_int = cast_int
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}-radio-{group:02x}-{instance:02d}-sensor-{key}"
        if device_class is not None:
            self._attr_device_class = device_class
        if native_unit is not None:
            self._attr_native_unit_of_measurement = native_unit
        if state_class is not None:
            self._attr_state_class = state_class
        if entity_category is not None:
            self._attr_entity_category = entity_category
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = self._device_value_present()

    def _device(self) -> dict[str, Any] | None:
        payload = self.coordinator.data or {}
        for device in payload.get("radio_devices", []) or []:
            if not isinstance(device, dict):
                continue
            slot = _radio_slot(device)
            if slot == (self._group, self._instance):
                return device
        return None

    def _device_value_present(self) -> bool:
        device = self._device()
        return isinstance(device, dict) and device.get(self._key) is not None

    @property
    def available(self) -> bool:
        device = self._device()
        if device is None:
            return False
        stale = _parse_optional_int(device.get("stale_cycles")) or 0
        return stale < _RADIO_STALE_GRACE_CYCLES

    @property
    def device_info(self) -> DeviceInfo:
        device_name = self._zone_name if self._zone_name else self._radio_name
        return DeviceInfo(
            identifiers={self._radio_device_id},
            manufacturer=self._manufacturer,
            model=self._radio_name,
            name=device_name,
        )

    @property
    def icon(self) -> str | None:
        """Dynamic icon for signal quality (ADR-026); static for others."""
        if self._key == "reception_strength":
            value = self.native_value
            if value is None:
                return "mdi:signal-cellular-outline"
            try:
                strength = int(value)
            except (TypeError, ValueError):
                return "mdi:signal-cellular-outline"
            if strength < 33:
                return "mdi:signal-cellular-1"
            if strength < 67:
                return "mdi:signal-cellular-2"
            return "mdi:signal-cellular-3"
        return self._attr_icon

    @property
    def native_value(self) -> Any:
        device = self._device()
        if not isinstance(device, dict):
            return None
        value = device.get(self._key)
        if value is None:
            return None
        if self._cast_int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


class HelianthusFM5ModeSensor(CoordinatorEntity, SensorEntity):
    """FM5 semantic mode marker."""

    _attr_has_entity_name = True
    entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        parent_device_id: tuple[str, str],
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._parent_device_id = parent_device_id
        self._attr_name = "FM5 Semantic Mode"
        self._attr_unique_id = f"{entry_id}-fm5-mode"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._parent_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        return _fm5_mode(self.coordinator.data if isinstance(self.coordinator.data, dict) else None)


class HelianthusSolarSensor(CoordinatorEntity, SensorEntity):
    """Solar semantic sensor values (interpreted mode only)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        solar_device_id: tuple[str, str],
        parent_device_id: tuple[str, str] | None,
        key: str,
        label: str,
        device_class: str | None,
        native_unit: str | None,
        icon: str | None = None,
        state_class: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._solar_device_id = solar_device_id
        self._parent_device_id = parent_device_id
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}-solar-sensor-{key}"
        if device_class is not None:
            self._attr_device_class = device_class
        if native_unit is not None:
            self._attr_native_unit_of_measurement = native_unit
        if state_class is not None:
            self._attr_state_class = state_class
        if icon is not None:
            self._attr_icon = icon
        self._attr_entity_registry_enabled_default = self._solar_value_present()

    @property
    def available(self) -> bool:
        payload = self.coordinator.data or {}
        return _fm5_mode(payload if isinstance(payload, dict) else None) == _FM5_MODE_INTERPRETED

    def _solar_value_present(self) -> bool:
        payload = self.coordinator.data or {}
        solar = payload.get("solar") if isinstance(payload, dict) else None
        return isinstance(solar, dict) and solar.get(self._key) is not None

    @property
    def device_info(self) -> DeviceInfo:
        info = {
            "identifiers": {self._solar_device_id},
            "manufacturer": self._manufacturer,
            "model": "Solar Circuit",
            "name": "Solar Circuit",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        solar = payload.get("solar") if isinstance(payload, dict) else None
        if not isinstance(solar, dict):
            return None
        value = solar.get(self._key)
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


class HelianthusCylinderSensor(CoordinatorEntity, SensorEntity):
    """Cylinder temperature sensor (interpreted mode only)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        cylinder_index: int,
        parent_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._cylinder_index = cylinder_index
        self._parent_device_id = parent_device_id
        self._attr_name = "Temperature"
        self._attr_unique_id = f"{entry_id}-cylinder-{cylinder_index}-temperature"
        self._attr_entity_registry_enabled_default = self._cylinder().get("temperature_c") is not None

    def _cylinder(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for cylinder in payload.get("cylinders", []) if isinstance(payload, dict) else []:
            if not isinstance(cylinder, dict):
                continue
            index = _parse_optional_int(cylinder.get("index"))
            if index == self._cylinder_index:
                return cylinder
        return {}

    @property
    def available(self) -> bool:
        payload = self.coordinator.data or {}
        return _fm5_mode(payload if isinstance(payload, dict) else None) == _FM5_MODE_INTERPRETED

    @property
    def device_info(self) -> DeviceInfo:
        identifier = cylinder_identifier(self._entry_id, self._cylinder_index)
        info = {
            "identifiers": {identifier},
            "manufacturer": self._manufacturer,
            "model": "Cylinder",
            "name": f"Cylinder {self._cylinder_index + 1}",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def native_value(self) -> Any:
        value = self._cylinder().get("temperature_c")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class HelianthusCylinderConfigSensor(CoordinatorEntity, SensorEntity):
    """Read-only cylinder configuration sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        cylinder_index: int,
        parent_device_id: tuple[str, str] | None,
        field: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._cylinder_index = cylinder_index
        self._parent_device_id = parent_device_id
        self._field = field
        self._attr_name = field['label']
        self._attr_unique_id = f"{entry_id}-cylinder-{cylinder_index}-config-{field['key']}"
        if field.get("native_unit") is not None:
            self._attr_native_unit_of_measurement = field["native_unit"]
        if field.get("state_class") is not None:
            self._attr_state_class = field["state_class"]
        if field.get("entity_category") is not None:
            self._attr_entity_category = field["entity_category"]
        if field.get("icon") is not None:
            self._attr_icon = field["icon"]
        self._attr_entity_registry_enabled_default = self._cylinder().get(field["key"]) is not None

    def _cylinder(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for cylinder in payload.get("cylinders", []) if isinstance(payload, dict) else []:
            if not isinstance(cylinder, dict):
                continue
            index = _parse_optional_int(cylinder.get("index"))
            if index == self._cylinder_index:
                return cylinder
        return {}

    @property
    def available(self) -> bool:
        payload = self.coordinator.data or {}
        return _fm5_mode(payload if isinstance(payload, dict) else None) == _FM5_MODE_INTERPRETED

    @property
    def device_info(self) -> DeviceInfo:
        identifier = cylinder_identifier(self._entry_id, self._cylinder_index)
        info = {
            "identifiers": {identifier},
            "manufacturer": self._manufacturer,
            "model": "Cylinder",
            "name": f"Cylinder {self._cylinder_index + 1}",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def native_value(self) -> Any:
        value = self._cylinder().get(self._field["key"])
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class HelianthusZoneValvePositionSensor(CoordinatorEntity, SensorEntity):
    """Zone valve position attached directly to the physical parent device."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:valve"

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        zone_id: str,
        zone_name: str,
        target_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._target_device_id = target_device_id
        self._attr_name = "Valve Position"
        self._attr_unique_id = f"{entry_id}-zone-{zone_id}-sensor-valve_position_pct"
        self._attr_entity_registry_enabled_default = self.native_value is not None

    def _zone(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for zone in payload.get("zones", []) or []:
            if not isinstance(zone, dict):
                continue
            if str(zone.get("id")) == self._zone_id:
                return zone
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        identifier = self._target_device_id
        if identifier is None:
            raise RuntimeError("Zone valve sensor created without a physical parent device")
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        zone = self._zone()
        state = zone.get("state") if isinstance(zone.get("state"), dict) else {}
        value = state.get("valve_position_pct") if isinstance(state, dict) else None
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class HelianthusDemandSensor(CoordinatorEntity, SensorEntity):
    """Heating demand sensor (percentage)."""

    _attr_has_entity_name = True
    entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heat-wave"

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        manufacturer: str,
        label: str,
        target: tuple[str, str | None],
        target_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._target = target
        self._target_device_id = target_device_id
        self._device_name = label if target[0] == "zone" else "Domestic Hot Water"
        self._attr_name = "Heating Demand"
        self._attr_unique_id = (
            f"{entry_id}-{target[0]}-{target[1] or 'dhw'}-heating-demand"
        )
        self._attr_entity_registry_enabled_default = self.native_value is not None

    @property
    def device_info(self) -> DeviceInfo:
        if self._target[0] == "zone":
            identifier = self._target_device_id
            if identifier is None:
                raise RuntimeError("Zone demand sensor created without a physical parent device")
            return DeviceInfo(
                identifiers={identifier},
                manufacturer=self._manufacturer,
            )
        identifier = dhw_identifier(self._entry_id)
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model="Virtual DHW",
            name=self._device_name,
            via_device=self._via_device,
        )

    def _dynamic_zone_name(self) -> str:
        if self.coordinator.data:
            for zone in self.coordinator.data.get("zones", []) or []:
                if zone.get("id") == self._target[1]:
                    zone_name = zone.get("name")
                    if zone_name and str(zone_name).strip():
                        return str(zone_name).strip()
        return self._device_name

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        kind, zone_id = self._target
        if kind == "zone":
            for zone in self.coordinator.data.get("zones", []) or []:
                if zone.get("id") == zone_id:
                    state = zone.get("state") or {}
                    return state.get("heating_demand_pct")
            return None
        dhw = self.coordinator.data.get("dhw") or {}
        state = dhw.get("state") or {}
        return state.get("heating_demand_pct")


class HelianthusDHWStatusSensor(CoordinatorEntity, SensorEntity):
    """DHW charging/status sensor."""

    _attr_has_entity_name = True
    entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:water-boiler"

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        manufacturer: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._attr_name = "HWC Status"
        self._attr_unique_id = f"{entry_id}-dhw-hwc-status"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={dhw_identifier(self._entry_id)},
            manufacturer=self._manufacturer,
            model="Virtual DHW",
            name="Domestic Hot Water",
            via_device=self._via_device,
        )

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        dhw = payload.get("dhw") or {}
        state = dhw.get("state") if isinstance(dhw.get("state"), dict) else {}
        value = state.get("special_function")
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return str(value)


class HelianthusEnergySensor(CoordinatorEntity, SensorEntity):
    """Energy total sensor (kWh)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = _SENSOR_STATE_CLASS_TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        manufacturer: str,
        source: str,
        usage: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._source = source
        self._usage = usage
        self._attr_name = f"{source.capitalize()} {usage.upper()}"
        self._attr_unique_id = f"{entry_id}-energy-{source}-{usage}"

    @property
    def device_info(self) -> DeviceInfo:
        identifier = energy_identifier(self._entry_id)
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model="Virtual Energy",
            name="Energy",
            via_device=self._via_device,
        )

    def _series(self) -> dict[str, Any] | None:
        payload = self.coordinator.data or {}
        totals = payload.get("energy_totals")
        if not isinstance(totals, dict):
            return None
        channel = totals.get(self._source)
        if not isinstance(channel, dict):
            return None
        series = channel.get(self._usage)
        if not isinstance(series, dict):
            return None
        return series

    @property
    def native_value(self) -> Any:
        series = self._series()
        if not isinstance(series, dict):
            return None
        yearly = series.get("yearly") if isinstance(series.get("yearly"), list) else None
        today = series.get("today")
        return compute_total(yearly, today)


def _pv_sensor_metadata(fact_id: str, unit: str) -> tuple[str | None, str | None, str | None]:
    if fact_id in {"pv.energy.active_export_total", "pv.dc.energy.active_total"}:
        return SensorDeviceClass.ENERGY, "Wh", _SENSOR_STATE_CLASS_TOTAL_INCREASING
    if fact_id == "pv.ac.power.apparent":
        return getattr(SensorDeviceClass, "APPARENT_POWER", None), "VA", SensorStateClass.MEASUREMENT
    if fact_id == "pv.ac.power.reactive":
        return getattr(SensorDeviceClass, "REACTIVE_POWER", None), "var", SensorStateClass.MEASUREMENT
    if unit == "W":
        return getattr(SensorDeviceClass, "POWER", None), "W", SensorStateClass.MEASUREMENT
    if unit == "A":
        return getattr(SensorDeviceClass, "CURRENT", None), "A", SensorStateClass.MEASUREMENT
    if unit == "V":
        return getattr(SensorDeviceClass, "VOLTAGE", None), "V", SensorStateClass.MEASUREMENT
    if unit == "Hz":
        return getattr(SensorDeviceClass, "FREQUENCY", None), "Hz", SensorStateClass.MEASUREMENT
    if unit == "Cel":
        return SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT
    if unit == "1" and fact_id == "pv.ac.power_factor":
        return None, "1", SensorStateClass.MEASUREMENT
    return None, None, None


class HelianthusPVM2MSensor(CoordinatorEntity, SensorEntity):
    """One persisted canonical PV fact identity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        asset_ref: str,
        descriptor: PVM2MDescriptor,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._asset_ref = asset_ref
        self._descriptor = descriptor
        self._attr_unique_id = descriptor.unique_id
        dimension = f"{descriptor.dimension[0]} {descriptor.dimension[1]}"
        self._attr_name = f"{descriptor.fact_id.removeprefix('pv.').replace('.', ' ').replace('_', ' ').title()} {dimension}"
        fact = self._fact()
        unit = fact.unit if fact is not None else _pv_expected_unit(descriptor.fact_id)
        device_class, native_unit, state_class = _pv_sensor_metadata(
            descriptor.fact_id, unit
        )
        if device_class is not None:
            self._attr_device_class = device_class
        if native_unit is not None:
            self._attr_native_unit_of_measurement = native_unit
        if state_class is not None:
            self._attr_state_class = state_class

    def _fact(self) -> PVM2MFact | None:
        data = self.coordinator.data
        facts = getattr(data, "facts", {})
        fact = facts.get(self._descriptor.key) if isinstance(facts, Mapping) else None
        return fact if isinstance(fact, PVM2MFact) else None

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        fact = self._fact()
        return bool(
            getattr(super(), "available", True)
            and getattr(self.coordinator, "last_update_success", True)
            and getattr(data, "source_available", False)
            and fact is not None
            and fact.availability == "AVAILABLE"
            and fact.freshness in {"FRESH", "STALE"}
        )

    @property
    def native_value(self) -> Any:
        fact = self._fact()
        if fact is None or fact.availability != "AVAILABLE" or fact.freshness == "EXPIRED":
            return None
        if isinstance(fact.value, tuple):
            return ", ".join(fact.value)
        return fact.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        fact = self._fact()
        if fact is None:
            return {
                "fact_id": self._descriptor.fact_id,
                "dimension": {
                    self._descriptor.dimension[0]: self._descriptor.dimension[1]
                },
            }
        attributes: dict[str, Any] = {
            "fact_id": fact.fact_id,
            "dimension": {fact.dimension[0]: fact.dimension[1]},
            "quality": fact.quality,
            "availability": fact.availability,
            "freshness": fact.freshness,
            "freshness_policy": fact.freshness_policy,
        }
        if isinstance(fact.value, tuple):
            attributes["symbols"] = list(fact.value)
        return attributes

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={build_pv_device_identifier(self._entry_id, self._asset_ref)},
            manufacturer="Helianthus",
            model="Canonical PV Asset",
            name="Solar PV",
        )


def _pv_expected_unit(fact_id: str) -> str:
    if fact_id in {"pv.energy.active_export_total", "pv.dc.energy.active_total"}:
        return "Wh"
    if fact_id == "pv.ac.power.apparent":
        return "VA"
    if fact_id == "pv.ac.power.reactive":
        return "var"
    if fact_id in {"pv.ac.current", "pv.dc.current"}:
        return "A"
    if fact_id in {"pv.ac.voltage.line_to_neutral", "pv.ac.voltage.line_to_line", "pv.dc.voltage"}:
        return "V"
    if fact_id == "pv.ac.frequency":
        return "Hz"
    if fact_id == "pv.temperature":
        return "Cel"
    if fact_id in {"pv.ac.power_factor", "pv.operating.state", "pv.event.flags"}:
        return "1"
    return "W"


class HelianthusAdapterInfoSensor(CoordinatorEntity, SensorEntity):
    """Adapter hardware telemetry diagnostic sensor."""

    _attr_has_entity_name = True
    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        entry_id: str,
        adapter_device_id: tuple[str, str],
        *,
        key: str,
        label: str,
        device_class: str | None = None,
        native_unit: str | None = None,
        state_class: str | None = None,
        icon: str | None = None,
        scale: float | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._adapter_device_id = adapter_device_id
        self._key = key
        self._scale = scale
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}-adapter-hw-{key}"
        if device_class is not None:
            self._attr_device_class = device_class
        if native_unit is not None:
            self._attr_native_unit_of_measurement = native_unit
        if state_class is not None:
            self._attr_state_class = state_class
        if icon is not None:
            self._attr_icon = icon
        self._attr_entity_registry_enabled_default = self.native_value is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._adapter_device_id})

    @property
    def native_value(self) -> Any:
        info = self.coordinator.data
        if not isinstance(info, dict):
            return None
        value = info.get(self._key)
        if value is None:
            return None
        if self._scale is not None:
            try:
                return round(float(value) * self._scale, 1)
            except (TypeError, ValueError):
                return None
        return value


class HelianthusBoilerHoursTillServiceSensor(CoordinatorEntity, SensorEntity):
    """Hours until next boiler service (B509 read-only counter)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = _SENSOR_DEVICE_CLASS_DURATION
    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wrench-clock"

    def __init__(self, *, coordinator, entry_id, manufacturer, boiler_device_id) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._boiler_device_id = boiler_device_id
        self._attr_unique_id = f"{entry_id}-boiler-sensor-hours_till_service"
        self._attr_name = "Hours Till Service"
        self._attr_entity_registry_enabled_default = self.native_value is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._boiler_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def available(self) -> bool:
        return super().available and getattr(self.coordinator, "boiler_installer_available", True)

    @property
    def native_value(self) -> int | None:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boiler_status") if isinstance(payload, dict) else None
        config = boiler_status.get("config", {}) if isinstance(boiler_status, dict) else {}
        value = config.get("hours_till_service")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
