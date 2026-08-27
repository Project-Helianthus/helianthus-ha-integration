"""Private sensor platform setup helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import DOMAIN
from .device_ids import (
    build_radio_bus_key,
    build_bus_device_key,
    cylinder_identifier,
    bus_identifier,
    circuit_display_name,
    circuit_identifier,
    dhw_identifier,
    energy_identifier,
    has_bus_identity_evidence,
    radio_device_identifier,
    resolve_bus_address,
    should_export_radio_device,
    solar_identifier,
    stable_bus_identity_model,
)
from .sensor_descriptors import *  # noqa: F403
def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _parse_circuit_index(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


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
    explicit = _clean_text(device.get("radio_bus_key"))
    if explicit:
        return explicit
    return build_radio_bus_key(slot[0], slot[1])


def _radio_model_name(device: dict[str, Any]) -> str:
    model = _clean_text(device.get("device_model"))
    if model:
        return model
    class_address = _parse_optional_int(device.get("device_class_address"))
    if class_address == 0x15:
        return "VRC720f/2"
    if class_address == 0x35:
        return "VR92f"
    if class_address == 0x26:
        return "VR71/FM5"
    if class_address is not None and class_address >= 0:
        return f"Unknown Radio (0x{class_address:02X})"
    return "Unknown Radio"


def _fm5_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "ABSENT"
    mode = str(payload.get("fm5_semantic_mode") or "ABSENT").strip().upper()
    if mode not in {"INTERPRETED", "GPIO_ONLY", "ABSENT"}:
        return "ABSENT"
    return mode


def _circuit_name(circuit: dict[str, Any], index: int) -> str:
    return circuit_display_name(circuit, index)


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


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    from .sensor import (
        HelianthusAdapterInfoSensor,
        HelianthusBoilerDiagnosticsSensor,
        HelianthusBoilerHoursTillServiceSensor,
        HelianthusBoilerStateSensor,
        HelianthusBoilerTemperatureSensor,
        HelianthusBusAddressSensor,
        HelianthusCircuitSensor,
        HelianthusCylinderConfigSensor,
        HelianthusCylinderSensor,
        HelianthusDemandSensor,
        HelianthusDHWStatusSensor,
        HelianthusEEBusAdminSensor,
        HelianthusEnergySensor,
        HelianthusFM5ModeSensor,
        HelianthusRadioSensor,
        HelianthusSolarSensor,
        HelianthusStatusSensor,
        HelianthusSystemSensor,
        HelianthusZoneValvePositionSensor,
    )

    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinator = data["device_coordinator"]
    status_coordinator = data["status_coordinator"]
    semantic_coordinator = data.get("semantic_coordinator")
    energy_coordinator = data.get("energy_coordinator")
    circuit_coordinator = data.get("circuit_coordinator")
    radio_coordinator = data.get("radio_coordinator")
    fm5_coordinator = data.get("fm5_coordinator")
    system_coordinator = data.get("system_coordinator")
    boiler_coordinator = data.get("boiler_coordinator")
    boiler_device_id = data.get("boiler_device_id")
    regulator_device_id = data.get("regulator_device_id")
    vr71_device_id = data.get("vr71_device_id")
    via_device = data.get("regulator_device_id") or data.get("adapter_device_id")
    zone_parent_device_ids = data.get("zone_parent_device_ids") or {}
    radio_device_zone_names: dict[tuple[str, str], str] = data.get("radio_device_zone_names") or {}
    b524_merge_targets: dict[str, tuple[str, str]] = data.get("b524_merge_targets") or {}
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"

    sensors: list[SensorEntity] = []
    admin_coordinator = data.get("eebus_admin_coordinator")
    if admin_coordinator is not None:
        origin = f"{entry.data.get('transport', 'http')}://{entry.data.get('host')}:{entry.data.get('port')}"
        sensors.append(HelianthusEEBusAdminSensor(admin_coordinator, entry.entry_id, origin))
    seen_bus_keys: set[str] = set()
    for device in device_coordinator.data or []:
        if not has_bus_identity_evidence(device):
            continue
        device_id = _clean_text(device.get("device_id")) or "unknown"
        address = resolve_bus_address(device.get("address"), device.get("addresses"))
        if address is None:
            continue
        model = stable_bus_identity_model(device.get("device_id"), device.get("product_model"))
        bus_key = build_bus_device_key(
            model=model,
            address=address,
            serial_number=_clean_text(device.get("serial_number")),
            mac_address=_clean_text(device.get("mac_address")),
            hardware_version=_clean_text(device.get("hardware_version")),
            software_version=_clean_text(device.get("software_version")),
        )
        if bus_key in seen_bus_keys:
            continue
        seen_bus_keys.add(bus_key)
        bus_id = bus_identifier(entry.entry_id, bus_key)
        sensors.append(HelianthusBusAddressSensor(device_coordinator, bus_id, address))

    status_entries = status_coordinator.data or {}
    daemon_status = status_entries.get("daemon", {})
    adapter_status = status_entries.get("adapter", {})

    for field in DAEMON_STATUS_FIELDS:
        if field.optional and daemon_status.get(field.key) is None:
            continue
        sensors.append(
            HelianthusStatusSensor(
                status_coordinator,
                "Daemon",
                daemon_status,
                data.get("daemon_device_id"),
                field,
            )
        )
    sensors.extend(
        HelianthusStatusSensor(
            status_coordinator,
            "Adapter",
            adapter_status,
            data.get("adapter_device_id"),
            field,
        )
        for field in ADAPTER_STATUS_FIELDS
    )

    if boiler_coordinator and boiler_device_id:
        sensors.extend(
            HelianthusBoilerTemperatureSensor(
                boiler_coordinator,
                entry.entry_id,
                boiler_device_id,
                field,
            )
            for field in REDUCED_BOILER_TEMPERATURE_FIELDS
        )
        sensors.extend(
            HelianthusBoilerStateSensor(
                coordinator=boiler_coordinator,
                entry_id=entry.entry_id,
                manufacturer=manufacturer,
                boiler_device_id=boiler_device_id,
                field=field,
            )
            for field in BOILER_STATE_SENSOR_FIELDS
        )
        sensors.extend(
            HelianthusBoilerDiagnosticsSensor(
                coordinator=boiler_coordinator,
                entry_id=entry.entry_id,
                manufacturer=manufacturer,
                boiler_device_id=boiler_device_id,
                field=field,
            )
            for field in BOILER_DIAGNOSTICS_SENSOR_FIELDS
        )
        sensors.append(
            HelianthusBoilerHoursTillServiceSensor(
                coordinator=boiler_coordinator,
                entry_id=entry.entry_id,
                manufacturer=manufacturer,
                boiler_device_id=boiler_device_id,
            )
        )

    if circuit_coordinator and circuit_coordinator.data:
        circuits = circuit_coordinator.data.get("circuits", []) or []
        for circuit in circuits:
            if not isinstance(circuit, dict):
                continue
            circuit_index = _parse_circuit_index(circuit.get("index"))
            if circuit_index is None:
                continue
            initial_name = _circuit_name(circuit, circuit_index)
            for field in CIRCUIT_SENSOR_FIELDS:
                sensors.append(
                    HelianthusCircuitSensor(
                        coordinator=circuit_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        circuit_index=circuit_index,
                        initial_name=initial_name,
                        field=field,
                    )
                )

    if system_coordinator and system_coordinator.data and regulator_device_id:
        for field in SYSTEM_SENSOR_FIELDS:
            if field.optional:
                source_data = system_coordinator.data.get(field.source, {})
                if not isinstance(source_data, dict) or source_data.get(field.key) is None:
                    continue
            sensors.append(
                HelianthusSystemSensor(
                    coordinator=system_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    regulator_device_id=regulator_device_id,
                    field=field,
                )
            )

    if radio_coordinator and radio_coordinator.data:
        radio_devices = radio_coordinator.data.get("radio_devices", []) or []
        for radio in radio_devices:
            if not isinstance(radio, dict):
                continue
            if not should_export_radio_device(radio):
                continue
            slot = _radio_slot(radio)
            bus_key = _radio_bus_key(radio)
            if slot is None or bus_key is None:
                continue
            group, instance = slot
            # ADR-027: skip all sensors for merged B524 function-module slots.
            if bus_key in b524_merge_targets:
                continue
            class_address = _parse_optional_int(radio.get("device_class_address"))
            is_room = class_address in _RADIO_ROOM_CLASSES
            radio_device_id = radio_device_identifier(entry.entry_id, bus_key)
            radio_name = _radio_model_name(radio)
            radio_zone_name = radio_device_zone_names.get(radio_device_id)
            if is_room or radio.get("reception_strength") is not None:
                sensors.append(
                    HelianthusRadioSensor(
                        coordinator=radio_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        radio_device_id=radio_device_id,
                        radio_name=radio_name,
                        group=group,
                        instance=instance,
                        key="reception_strength",
                        label="Signal Quality",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        cast_int=True,
                        zone_name=radio_zone_name,
                    )
                )
            if is_room:
                sensors.append(
                    HelianthusRadioSensor(
                        coordinator=radio_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        radio_device_id=radio_device_id,
                        radio_name=radio_name,
                        group=group,
                        instance=instance,
                        key="room_temperature_c",
                        label="Room Temperature",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit=UnitOfTemperature.CELSIUS,
                        state_class=SensorStateClass.MEASUREMENT,
                        zone_name=radio_zone_name,
                    )
                )
                sensors.append(
                    HelianthusRadioSensor(
                        coordinator=radio_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        radio_device_id=radio_device_id,
                        radio_name=radio_name,
                        group=group,
                        instance=instance,
                        key="room_humidity_pct",
                        label="Room Humidity",
                        device_class=_SENSOR_DEVICE_CLASS_HUMIDITY,
                        native_unit=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                        zone_name=radio_zone_name,
                    )
                )
            elif group == 0x0C:
                _radio_metadata_icons = {
                    "device_class_address": "mdi:identifier",
                    "hardware_identifier": "mdi:identifier",
                    "remote_control_address": "mdi:remote",
                    "zone_assignment": "mdi:home-map-marker",
                }
                for key, label in [
                    ("device_class_address", "Device Class Address"),
                    ("hardware_identifier", "Hardware Identifier"),
                    ("remote_control_address", "Remote Control Address"),
                    ("zone_assignment", "Zone Assignment"),
                ]:
                    if radio.get(key) is None:
                        continue
                    sensors.append(
                        HelianthusRadioSensor(
                            coordinator=radio_coordinator,
                            entry_id=entry.entry_id,
                            manufacturer=manufacturer,
                            radio_device_id=radio_device_id,
                            radio_name=radio_name,
                            group=group,
                            instance=instance,
                            key=key,
                            label=label,
                            entity_category=EntityCategory.DIAGNOSTIC,
                            cast_int=True,
                            icon=_radio_metadata_icons.get(key),
                            zone_name=radio_zone_name,
                        )
                    )

    if fm5_coordinator and fm5_coordinator.data:
        fm5_payload = fm5_coordinator.data
        mode = _fm5_mode(fm5_payload)
        marker_device_id = vr71_device_id or regulator_device_id
        if marker_device_id:
            sensors.append(
                HelianthusFM5ModeSensor(
                    coordinator=fm5_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    parent_device_id=marker_device_id,
                )
            )
        if mode == _FM5_MODE_INTERPRETED:
            solar = fm5_payload.get("solar")
            if isinstance(solar, dict):
                solar_device_id = solar_identifier(entry.entry_id)
                for key, label, device_class, unit, state_class, icon in [
                    (
                        "collector_temperature_c",
                        "Collector Temperature",
                        SensorDeviceClass.TEMPERATURE,
                        UnitOfTemperature.CELSIUS,
                        SensorStateClass.MEASUREMENT,
                        None,
                    ),
                    (
                        "return_temperature_c",
                        "Return Temperature",
                        SensorDeviceClass.TEMPERATURE,
                        UnitOfTemperature.CELSIUS,
                        SensorStateClass.MEASUREMENT,
                        None,
                    ),
                    ("current_yield", "Current Yield", None, None, None, "mdi:solar-power"),
                    ("pump_hours", "Pump Hours", _SENSOR_DEVICE_CLASS_DURATION, "h", _SENSOR_STATE_CLASS_TOTAL_INCREASING, None),
                ]:
                    sensors.append(
                        HelianthusSolarSensor(
                            coordinator=fm5_coordinator,
                            entry_id=entry.entry_id,
                            manufacturer=manufacturer,
                            solar_device_id=solar_device_id,
                            parent_device_id=vr71_device_id or regulator_device_id,
                            key=key,
                            label=label,
                            device_class=device_class,
                            native_unit=unit,
                            state_class=state_class,
                            icon=icon,
                        )
                    )

            for cylinder in fm5_payload.get("cylinders", []) or []:
                if not isinstance(cylinder, dict):
                    continue
                index = _parse_optional_int(cylinder.get("index"))
                if index is None or index < 0:
                    continue
                sensors.append(
                    HelianthusCylinderSensor(
                        coordinator=fm5_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        cylinder_index=index,
                        parent_device_id=vr71_device_id or regulator_device_id,
                    )
                )
                for field in CYLINDER_CONFIG_SENSOR_FIELDS:
                    sensors.append(
                        HelianthusCylinderConfigSensor(
                            coordinator=fm5_coordinator,
                            entry_id=entry.entry_id,
                            manufacturer=manufacturer,
                            cylinder_index=index,
                            parent_device_id=vr71_device_id or regulator_device_id,
                            field=field,
                        )
                    )

    if semantic_coordinator and semantic_coordinator.data:
        zones = semantic_coordinator.data.get("zones", []) or []
        for zone in zones:
            zone_id = zone.get("id")
            if zone_id:
                normalized_zone_id = _normalize_zone_id(zone_id)
                if normalized_zone_id is None:
                    continue
                config = zone.get("config")
                mapping = _parse_optional_int(config.get("room_temperature_zone_mapping")) if isinstance(config, dict) else None
                target_device_id = zone_parent_device_ids.get(normalized_zone_id)
                if target_device_id is None:
                    if mapping in (1, 2, 3, 4):
                        continue
                    target_device_id = regulator_device_id
                if target_device_id is None:
                    continue
                sensors.append(
                    HelianthusDemandSensor(
                        semantic_coordinator,
                        entry.entry_id,
                        via_device,
                        manufacturer,
                        zone.get("name") or f"Zone {zone_id}",
                        ("zone", str(zone_id)),
                        target_device_id=target_device_id,
                    )
                )
                sensors.append(
                    HelianthusZoneValvePositionSensor(
                        coordinator=semantic_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        zone_id=str(zone_id),
                        zone_name=str(zone.get("name") or f"Zone {zone_id}"),
                        target_device_id=target_device_id,
                    )
                )
        if semantic_coordinator.data.get("dhw") is not None:
            sensors.append(
                HelianthusDemandSensor(
                    semantic_coordinator,
                    entry.entry_id,
                    via_device,
                    manufacturer,
                    "DHW",
                    ("dhw", None),
                    target_device_id=None,
                )
            )
            sensors.append(
                HelianthusDHWStatusSensor(
                    semantic_coordinator,
                    entry.entry_id,
                    via_device,
                    manufacturer,
                )
            )

    if energy_coordinator and energy_coordinator.data:
        sensors.extend(
            [
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "gas", "dhw"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "gas", "climate"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "electric", "dhw"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "electric", "climate"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "solar", "dhw"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "solar", "climate"
                ),
            ]
        )

    pv_m2m_coordinator = data.get("pv_m2m_coordinator")
    if pv_m2m_coordinator is not None:
        pv_data = pv_m2m_coordinator.data
        descriptors = tuple(getattr(pv_data, "descriptors", ()) or ())
        known_pv_descriptor_keys = {descriptor.key for descriptor in descriptors}
        sensors.extend(
            HelianthusPVM2MSensor(
                coordinator=pv_m2m_coordinator,
                entry_id=entry.entry_id,
                asset_ref=pv_m2m_coordinator.asset_ref,
                descriptor=descriptor,
            )
            for descriptor in descriptors
        )

        def _add_discovered_pv_entities() -> None:
            current = pv_m2m_coordinator.data
            new_descriptors = [
                descriptor
                for descriptor in tuple(getattr(current, "descriptors", ()) or ())
                if descriptor.key not in known_pv_descriptor_keys
            ]
            if not new_descriptors:
                return
            known_pv_descriptor_keys.update(
                descriptor.key for descriptor in new_descriptors
            )
            async_add_entities(
                [
                    HelianthusPVM2MSensor(
                        coordinator=pv_m2m_coordinator,
                        entry_id=entry.entry_id,
                        asset_ref=pv_m2m_coordinator.asset_ref,
                        descriptor=descriptor,
                    )
                    for descriptor in new_descriptors
                ]
            )

        data.setdefault("unsub_listeners", []).append(
            pv_m2m_coordinator.async_add_listener(_add_discovered_pv_entities)
        )

    adapter_info_coordinator = data.get("adapter_info_coordinator")
    adapter_device_id = data.get("adapter_device_id")
    if adapter_info_coordinator and adapter_device_id:
        adapter_hw = adapter_info_coordinator.data
        is_wifi = isinstance(adapter_hw, dict) and adapter_hw.get("is_wifi") is True
        has_reset = isinstance(adapter_hw, dict) and adapter_hw.get("reset_cause") is not None
        sensors.append(
            HelianthusAdapterInfoSensor(
                adapter_info_coordinator, entry.entry_id, adapter_device_id,
                key="temperature_c", label="Adapter Temperature",
                device_class=SensorDeviceClass.TEMPERATURE,
                native_unit=UnitOfTemperature.CELSIUS,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:thermometer",
            )
        )
        sensors.append(
            HelianthusAdapterInfoSensor(
                adapter_info_coordinator, entry.entry_id, adapter_device_id,
                key="supply_voltage_mv", label="Adapter Supply Voltage",
                device_class=SensorDeviceClass.VOLTAGE,
                native_unit="mV",
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:flash",
            )
        )
        sensors.append(
            HelianthusAdapterInfoSensor(
                adapter_info_coordinator, entry.entry_id, adapter_device_id,
                key="bus_voltage_max_dv", label="eBUS Voltage Max",
                device_class=SensorDeviceClass.VOLTAGE,
                native_unit="V",
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:sine-wave",
                scale=0.1,
            )
        )
        sensors.append(
            HelianthusAdapterInfoSensor(
                adapter_info_coordinator, entry.entry_id, adapter_device_id,
                key="bus_voltage_min_dv", label="eBUS Voltage Min",
                device_class=SensorDeviceClass.VOLTAGE,
                native_unit="V",
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:sine-wave",
                scale=0.1,
            )
        )
        sensors.append(
            HelianthusAdapterInfoSensor(
                adapter_info_coordinator, entry.entry_id, adapter_device_id,
                key="restart_count", label="Adapter Restart Count",
                state_class=_SENSOR_STATE_CLASS_TOTAL_INCREASING,
                icon="mdi:counter",
            )
        )
        if has_reset:
            sensors.append(
                HelianthusAdapterInfoSensor(
                    adapter_info_coordinator, entry.entry_id, adapter_device_id,
                    key="reset_cause", label="Adapter Reset Cause",
                    icon="mdi:alert-circle-outline",
                )
            )
        if is_wifi:
            sensors.append(
                HelianthusAdapterInfoSensor(
                    adapter_info_coordinator, entry.entry_id, adapter_device_id,
                    key="wifi_rssi_dbm", label="Adapter WiFi Signal",
                    device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                    native_unit="dBm",
                    state_class=SensorStateClass.MEASUREMENT,
                    icon="mdi:wifi",
                )
            )

    async_add_entities(sensors)

    # M4_HA (execution-plans#19): Vaillant B503 diagnostic sensor.
    # Entity lifecycle follows plan AD11 (3-poll NOT_SUPPORTED hysteresis)
    # and AD15 (state=None on healthy, state=unavailable on transient reasons,
    # entity absent on persistent NOT_SUPPORTED). Production wiring is
    # gated on the M5 BENCH-REPLACE live-bus ratification for the MCP
    # stub dispatcher — until then, the capability probe resolves to
    # UNKNOWN and the sensor renders as unavailable rather than serving
    # stub data.
    try:
        from .vaillant_b503 import async_setup_b503

        b503_client = data.get("graphql_client")
        b503_device_id = boiler_device_id or regulator_device_id
        if b503_client is not None and b503_device_id is not None:
            await async_setup_b503(
                hass,
                entry,
                async_add_entities,
                client=b503_client,
                device_id=b503_device_id,
                scan_interval=30,
            )
    except Exception:  # noqa: BLE001
        # B503 sensor is optional diagnostic; any wiring failure must not
        # block the rest of the integration from loading.
        _b503_logger = __import__("logging").getLogger(__name__)
        _b503_logger.exception("Vaillant B503 sensor wiring failed; continuing without it")
