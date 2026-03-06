"""Binary schedule/state mirror entities for Helianthus semantic targets."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import (
    build_radio_bus_key,
    circuit_identifier,
    dhw_identifier,
    radio_device_identifier,
    solar_identifier,
    zone_identifier,
)
from .zone_parent import zone_via_device

_BINARY_SENSOR_DEVICE_CLASS_PROBLEM = getattr(BinarySensorDeviceClass, "PROBLEM", None)
_RADIO_STALE_GRACE_CYCLES = 3


def _normalize_preset(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"auto", "schedule"}:
        return "schedule"
    if token in {"manual"}:
        return "manual"
    if token in {"quickveto", "quick_veto", "qv"}:
        return "quickveto"
    if token in {"away", "holiday"}:
        return "away"
    return token


def _parse_optional_int(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _radio_slot(device: dict[str, Any]) -> tuple[int, int] | None:
    group = _parse_optional_int(device.get("group"))
    instance = _parse_optional_int(device.get("instance"))
    if group is None or instance is None:
        return None
    if group < 0 or group > 0xFF or instance < 0 or instance > 0xFF:
        return None
    return (group, instance)


def _radio_bus_key(device: dict[str, Any]) -> str | None:
    slot = _radio_slot(device)
    if slot is None:
        return None
    explicit = str(device.get("radioBusKey") or "").strip()
    if explicit:
        return explicit
    return build_radio_bus_key(slot[0], slot[1])


def _normalize_zone_id(zone_id: object | None) -> str | None:
    if zone_id is None:
        return None
    token = str(zone_id).strip().lower()
    if not token:
        return None
    if token.startswith("zone-"):
        suffix = token[5:]
    else:
        suffix = token
    if suffix.isdigit():
        value = int(suffix, 10)
        if value > 0:
            return f"zone-{value}"
    return token


def _zone_instance(zone_id: object | None) -> int | None:
    normalized = _normalize_zone_id(zone_id)
    if normalized is None:
        return None
    token = normalized[5:] if normalized.startswith("zone-") else normalized
    if not token.isdigit():
        return None
    value = int(token, 10)
    if value <= 0:
        return None
    return value - 1


def _zone_parent_device_id(
    entry_id: str,
    zone: dict[str, Any],
    radio_payload: dict[str, Any] | None,
    regulator_device_id: tuple[str, str] | None,
) -> tuple[str, str] | None:
    zone_instance = _zone_instance(zone.get("id"))
    if zone_instance is None:
        return regulator_device_id

    raw_candidates = radio_payload.get("radioZoneCandidates") if isinstance(radio_payload, dict) else None
    radio_zone_candidates: dict[int, list[dict[str, Any]]] = {}
    if isinstance(raw_candidates, dict):
        for raw_zone_instance, raw_items in raw_candidates.items():
            parsed_zone_instance = _parse_optional_int(raw_zone_instance)
            if parsed_zone_instance is None or not isinstance(raw_items, list):
                continue
            normalized_items: list[dict[str, Any]] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                group = _parse_optional_int(item.get("group"))
                instance = _parse_optional_int(item.get("instance"))
                if group is None or instance is None:
                    continue
                normalized_items.append(
                    {
                        "group": group,
                        "instance": instance,
                        "remote_control_address": _parse_optional_int(item.get("remote_control_address")),
                    }
                )
            if normalized_items:
                radio_zone_candidates[parsed_zone_instance] = normalized_items

    radio_devices = [
        item
        for item in (radio_payload.get("radioDevices", []) or [] if isinstance(radio_payload, dict) else [])
        if isinstance(item, dict)
    ]
    radio_device_ids: dict[tuple[int, int], tuple[str, str]] = {}
    for device in radio_devices:
        slot = _radio_slot(device)
        bus_key = _radio_bus_key(device)
        if slot is None or bus_key is None:
            continue
        radio_device_ids[slot] = radio_device_identifier(entry_id, bus_key)

    mapping = None
    config = zone.get("config")
    if isinstance(config, dict):
        mapping = _parse_optional_int(config.get("roomTemperatureZoneMapping"))
    return zone_via_device(
        zone_instance,
        mapping,
        radio_zone_candidates,
        radio_devices,
        radio_device_ids,
        regulator_device_id,
    )


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["semantic_coordinator"]
    radio_coordinator = data.get("radio_coordinator")
    system_coordinator = data.get("system_coordinator")
    boiler_coordinator = data.get("boiler_coordinator")
    boiler_device_id = data.get("boiler_device_id")
    circuit_coordinator = data.get("circuit_coordinator")
    fm5_coordinator = data.get("fm5_coordinator")
    regulator_device_id = data.get("regulator_device_id")
    vr71_device_id = data.get("vr71_device_id") or regulator_device_id
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"

    zones = coordinator.data.get("zones", []) if coordinator.data else []
    entities: list[HelianthusScheduleBinarySensor] = []
    radio_payload = radio_coordinator.data if radio_coordinator else None
    for zone in zones:
        zone_id = zone.get("id")
        if zone_id is None:
            continue
        zone_name = str(zone.get("name") or f"Zone {zone_id}")
        parent_device_id = _zone_parent_device_id(
            entry.entry_id,
            zone,
            radio_payload if isinstance(radio_payload, dict) else None,
            regulator_device_id,
        )
        for schedule_key, schedule_label in [
            ("schedule", "Daily Schedule Active"),
            ("quickveto", "Quick Veto Active"),
            ("away", "Away Schedule Active"),
        ]:
            entities.append(
                HelianthusScheduleBinarySensor(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    target_kind="zone",
                    target_id=str(zone_id),
                    target_name=zone_name,
                    target_device_id=parent_device_id,
                    schedule_key=schedule_key,
                    schedule_label=schedule_label,
                )
            )

    dhw = coordinator.data.get("dhw") if coordinator.data else None
    if dhw is not None:
        for schedule_key, schedule_label in [
            ("schedule", "Daily Schedule Active"),
            ("quickveto", "Quick Veto Active"),
            ("away", "Away Schedule Active"),
        ]:
            entities.append(
                HelianthusScheduleBinarySensor(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    target_kind="dhw",
                    target_id=None,
                    target_name="Domestic Hot Water",
                    schedule_key=schedule_key,
                    schedule_label=schedule_label,
                )
            )

    if boiler_coordinator and boiler_device_id:
        for key, label in [
            ("flameActive", "Burner Flame Active"),
            ("gasValveActive", "Burner Gas Valve Active"),
            ("centralHeatingPumpActive", "Hydraulics CH Pump"),
            ("externalPumpActive", "Hydraulics External Pump"),
            ("circulationPumpActive", "Hydraulics Circulation Pump"),
        ]:
            entities.append(
                HelianthusBoilerStateBinarySensor(
                    coordinator=boiler_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    boiler_device_id=boiler_device_id,
                    key=key,
                    label=label,
                )
            )

    if circuit_coordinator and circuit_coordinator.data:
        for circuit in circuit_coordinator.data.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            circuit_index = _parse_optional_int(circuit.get("index"))
            if circuit_index is None or circuit_index < 0:
                continue
            circuit_type = str(circuit.get("circuitType") or "").strip().lower()
            label = circuit_type.replace("_", " ").title() if circuit_type else f"Circuit {circuit_index + 1}"
            entities.append(
                HelianthusCircuitPumpBinarySensor(
                    coordinator=circuit_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    circuit_index=circuit_index,
                    initial_name=f"Circuit {circuit_index + 1} ({label})",
                )
            )

    if fm5_coordinator and isinstance(fm5_coordinator.data, dict):
        payload = fm5_coordinator.data
        mode = str(payload.get("fm5SemanticMode") or "ABSENT").strip().upper()
        solar = payload.get("solar") if isinstance(payload.get("solar"), dict) else None
        if mode == "INTERPRETED":
            solar_device_id = data.get("solar_device_id") or solar_identifier(entry.entry_id)
            for key, label in [
                ("pumpActive", "Solar Pump Active"),
                ("solarEnabled", "Solar Enabled"),
                ("functionMode", "Solar Function Mode"),
            ]:
                entities.append(
                    HelianthusSolarBinarySensor(
                        coordinator=fm5_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        solar_device_id=solar_device_id,
                        parent_device_id=vr71_device_id,
                        key=key,
                        label=label,
                        enabled_by_default=bool(isinstance(solar, dict) and solar.get(key) is not None),
                    )
                )

    if system_coordinator and system_coordinator.data and regulator_device_id:
        entities.extend(
            [
                HelianthusSystemBinarySensor(
                    coordinator=system_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    regulator_device_id=regulator_device_id,
                    source="state",
                    key="maintenanceDue",
                    label="Maintenance Due",
                    device_class=_BINARY_SENSOR_DEVICE_CLASS_PROBLEM,
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
                HelianthusSystemBinarySensor(
                    coordinator=system_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    regulator_device_id=regulator_device_id,
                    source="config",
                    key="adaptiveHeatingCurve",
                    label="Adaptive Heating Curve",
                    device_class=None,
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
            ]
        )

    if radio_coordinator and radio_coordinator.data:
        for radio in radio_coordinator.data.get("radioDevices", []) or []:
            if not isinstance(radio, dict):
                continue
            slot = _radio_slot(radio)
            bus_key = _radio_bus_key(radio)
            if slot is None or bus_key is None:
                continue
            group, instance = slot
            entities.append(
                HelianthusRadioConnectedBinarySensor(
                    coordinator=radio_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    radio_device_id=radio_device_identifier(entry.entry_id, bus_key),
                    group=group,
                    instance=instance,
                )
            )

    async_add_entities(entities)


class HelianthusScheduleBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Read-only schedule mirror binary sensor."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        target_kind: str,
        target_id: str | None,
        target_name: str,
        target_device_id: tuple[str, str] | None,
        schedule_key: str,
        schedule_label: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._target_kind = target_kind
        self._target_id = target_id
        self._target_name = target_name
        self._target_device_id = target_device_id
        self._schedule_key = schedule_key
        self._attr_name = f"{target_name} {schedule_label}"
        unique_target = target_id or "dhw"
        self._attr_unique_id = f"{entry_id}-{target_kind}-{unique_target}-schedule-{schedule_key}"

    def _target_payload(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        if self._target_kind == "zone":
            for zone in self.coordinator.data.get("zones", []) or []:
                if str(zone.get("id")) == str(self._target_id):
                    return zone
            return {}
        return self.coordinator.data.get("dhw") or {}

    @property
    def is_on(self) -> bool:
        payload = self._target_payload()
        config = payload.get("config") or {}
        return _normalize_preset(config.get("preset")) == self._schedule_key

    def _dynamic_zone_name(self) -> str:
        if self.coordinator.data:
            for zone in self.coordinator.data.get("zones", []) or []:
                if str(zone.get("id")) == str(self._target_id):
                    zone_name = zone.get("name")
                    if zone_name and str(zone_name).strip():
                        return str(zone_name).strip()
        return self._target_name

    @property
    def device_info(self) -> DeviceInfo:
        if self._target_kind == "zone":
            identifier = self._target_device_id or zone_identifier(self._entry_id, str(self._target_id))
            return DeviceInfo(
                identifiers={identifier},
                manufacturer=self._manufacturer,
            )
        else:
            identifier = dhw_identifier(self._entry_id)
            return DeviceInfo(
                identifiers={identifier},
                manufacturer=self._manufacturer,
                model="Virtual DHW",
                name="Domestic Hot Water",
            )


class HelianthusBoilerStateBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Boiler read-only boolean state exposed on the physical boiler device."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        boiler_device_id: tuple[str, str],
        key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._boiler_device_id = boiler_device_id
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}-boiler-binary-{key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._boiler_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def is_on(self) -> bool | None:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boilerStatus") or {}
        state = boiler_status.get("state") if isinstance(boiler_status, dict) else {}
        value = state.get(self._key) if isinstance(state, dict) else None
        if isinstance(value, bool):
            return value
        return None


class HelianthusCircuitPumpBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Per-circuit pump active state."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        circuit_index: int,
        initial_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._circuit_index = circuit_index
        self._initial_name = initial_name
        self._attr_name = f"{initial_name} Pump Active"
        self._attr_unique_id = f"{entry_id}-circuit-{circuit_index}-binary-pumpActive"

    def _circuit(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for circuit in payload.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            if _parse_optional_int(circuit.get("index")) == self._circuit_index:
                return circuit
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={circuit_identifier(self._entry_id, self._circuit_index)},
            manufacturer=self._manufacturer,
        )

    @property
    def is_on(self) -> bool | None:
        circuit = self._circuit()
        state = circuit.get("state") if isinstance(circuit.get("state"), dict) else {}
        value = state.get("pumpActive") if isinstance(state, dict) else None
        if isinstance(value, bool):
            return value
        return None


class HelianthusSolarBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Sparse solar binary sensor that auto-enables when live data appears."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
        enabled_by_default: bool,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._solar_device_id = solar_device_id
        self._parent_device_id = parent_device_id
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}-solar-binary-{key}"
        self._attr_entity_registry_enabled_default = enabled_by_default

    @property
    def available(self) -> bool:
        payload = self.coordinator.data if isinstance(self.coordinator.data, dict) else None
        return str(payload.get("fm5SemanticMode") or "ABSENT").strip().upper() == "INTERPRETED" if isinstance(payload, dict) else False

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
    def is_on(self) -> bool | None:
        payload = self.coordinator.data or {}
        solar = payload.get("solar") if isinstance(payload, dict) else None
        if not isinstance(solar, dict):
            return None
        value = solar.get(self._key)
        if isinstance(value, bool):
            return value
        return None


class HelianthusBoilerPumpBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Reduced-profile central heating pump state on physical BAI00."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        boiler_device_id: tuple[str, str],
    ) -> None:
        super().__init__(coordinator)
        self._boiler_device_id = boiler_device_id
        self._attr_name = "Boiler Central Heating Pump Active"
        self._attr_unique_id = f"{entry_id}-boiler-central-heating-pump-active"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._boiler_device_id})

    @property
    def is_on(self) -> bool | None:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boilerStatus") or {}
        state = boiler_status.get("state") or {}
        value = state.get("centralHeatingPumpActive")
        if isinstance(value, bool):
            return value
        return None


class HelianthusSystemBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """System-level BASV2 binary sensor."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        regulator_device_id: tuple[str, str],
        source: str,
        key: str,
        label: str,
        device_class: str | None,
        entity_category: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._regulator_device_id = regulator_device_id
        self._source = source
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}-system-binary-{key}"
        if device_class is not None:
            self._attr_device_class = device_class
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._regulator_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def is_on(self) -> bool | None:
        payload = self.coordinator.data or {}
        source = payload.get(self._source)
        if not isinstance(source, dict):
            return None
        value = source.get(self._key)
        if isinstance(value, bool):
            return value
        return None


class HelianthusRadioConnectedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Remote-slot connected-state binary sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        radio_device_id: tuple[str, str],
        group: int,
        instance: int,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._radio_device_id = radio_device_id
        self._group = group
        self._instance = instance
        self._attr_name = "Device Connected"
        self._attr_unique_id = f"{entry_id}-radio-{group:02x}-{instance:02d}-connected"

    def _device(self) -> dict[str, Any] | None:
        payload = self.coordinator.data or {}
        for device in payload.get("radioDevices", []) or []:
            if not isinstance(device, dict):
                continue
            if _radio_slot(device) == (self._group, self._instance):
                return device
        return None

    @property
    def available(self) -> bool:
        device = self._device()
        if device is None:
            return False
        stale = _parse_optional_int(device.get("staleCycles")) or 0
        return stale < _RADIO_STALE_GRACE_CYCLES

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._radio_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def is_on(self) -> bool | None:
        device = self._device()
        if not isinstance(device, dict):
            return None
        value = device.get("deviceConnected")
        if isinstance(value, bool):
            return value
        return None
