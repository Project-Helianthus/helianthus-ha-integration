"""Private sensor platform setup helpers."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTemperature

from .const import DOMAIN
from .device_ids import (
    build_radio_bus_key,
    build_bus_device_key,
    bus_identifier,
    circuit_display_name,
    has_bus_identity_evidence,
    radio_device_identifier,
    resolve_bus_address,
    should_export_radio_device,
    solar_identifier,
    stable_bus_identity_model,
)
from .sensor_descriptors import (
    ADAPTER_STATUS_FIELDS,
    BOILER_DIAGNOSTICS_SENSOR_FIELDS,
    BOILER_STATE_SENSOR_FIELDS,
    CIRCUIT_SENSOR_FIELDS,
    CYLINDER_CONFIG_SENSOR_FIELDS,
    DAEMON_STATUS_FIELDS,
    REDUCED_BOILER_TEMPERATURE_FIELDS,
    SYSTEM_SENSOR_FIELDS,
    _FM5_MODE_INTERPRETED,
    _RADIO_ROOM_CLASSES,
    _SENSOR_DEVICE_CLASS_DURATION,
    _SENSOR_DEVICE_CLASS_HUMIDITY,
    _SENSOR_STATE_CLASS_TOTAL_INCREASING,
)

_LOGGER = logging.getLogger(__name__)


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


def _daemon_field_wanted(has_reloaded, daemon_device_id, daemon_status, field) -> bool:
    """Return whether the daemon status sensor of ``field`` is created.

    An optional field is created while it has a value, and once its sparse
    entity has had its one reload whether or not the value is null then.
    ``has_reloaded(unique_id)`` reads the record kept by entry setup.
    """
    if not field.optional or daemon_status.get(field.key) is not None:
        return True
    return bool(daemon_device_id) and has_reloaded(f"{daemon_device_id[1]}-{field.key}")


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Add the sensor entities of every domain in one call, then wire B503."""
    from .sensor import (
        HelianthusEVSEM2MSensor,
        HelianthusPVM2MSensor,
        HelianthusStorageM2MSensor,
    )
    from .entry_setup import sparse_entity_has_reloaded
    from .semantic_snapshot_sensors import setup_semantic_snapshot_sensors

    data = hass.data[DOMAIN][entry.entry_id]
    entry_id = entry.entry_id
    has_reloaded = partial(sparse_entity_has_reloaded, hass, entry_id)
    device_coordinator = data["device_coordinator"]
    status_coordinator = data["status_coordinator"]
    boiler_device_id = data.get("boiler_device_id")
    regulator_device_id = data.get("regulator_device_id")
    adapter_device_id = data.get("adapter_device_id")
    via_device = regulator_device_id or adapter_device_id
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"

    sensors: list[SensorEntity] = []
    sensors.extend(setup_semantic_snapshot_sensors(data, entry_id, async_add_entities))
    sensors.extend(_build_eebus_admin_sensors(data.get("eebus_admin_coordinator"), entry))
    sensors.extend(_build_bus_address_sensors(device_coordinator, entry_id))
    sensors.extend(_build_status_sensors(
        status_coordinator, data.get("daemon_device_id"), adapter_device_id,
        has_reloaded))
    sensors.extend(_build_boiler_sensors(
        data.get("boiler_coordinator"), entry_id, manufacturer, boiler_device_id))
    sensors.extend(_build_circuit_sensors(
        data.get("circuit_coordinator"), entry_id, manufacturer))
    sensors.extend(_build_system_sensors(
        data.get("system_coordinator"), entry_id, manufacturer, regulator_device_id))
    sensors.extend(_build_radio_sensors(
        data.get("radio_coordinator"), entry_id, manufacturer,
        data.get("radio_device_zone_names") or {}, data.get("b524_merge_targets") or {},
        has_reloaded))
    sensors.extend(_build_fm5_sensors(
        data.get("fm5_coordinator"), entry_id, manufacturer,
        data.get("vr71_device_id") or regulator_device_id))
    sensors.extend(_build_zone_and_dhw_sensors(
        data.get("semantic_coordinator"), entry_id, manufacturer, via_device,
        regulator_device_id, data.get("zone_parent_device_ids") or {}))
    sensors.extend(_build_energy_sensors(
        data.get("energy_coordinator"), entry_id, via_device, manufacturer))
    for coordinator, entity_class in (
        (data.get("pv_m2m_coordinator"), HelianthusPVM2MSensor),
        (data.get("storage_m2m_coordinator"), HelianthusStorageM2MSensor),
        (data.get("evse_m2m_coordinator"), HelianthusEVSEM2MSensor),
    ):
        if coordinator is not None:
            sensors.extend(_build_descriptor_sensors(
                coordinator, entity_class, entry_id, async_add_entities,
                data.setdefault("unsub_listeners", [])))
    sensors.extend(_build_adapter_info_sensors(
        data.get("adapter_info_coordinator"), entry_id, adapter_device_id))

    async_add_entities(sensors)

    await _async_wire_b503_diagnostics(
        hass, entry, async_add_entities, data.get("graphql_client"),
        boiler_device_id or regulator_device_id)


def _build_eebus_admin_sensors(admin_coordinator, entry) -> list[SensorEntity]:
    """Return the eeBUS admin status sensor when its coordinator exists."""
    from .sensor import HelianthusEEBusAdminSensor

    if admin_coordinator is None:
        return []
    origin = (
        f"{entry.data.get('transport', 'http')}://"
        f"{entry.data.get('host')}:{entry.data.get('port')}"
    )
    return [HelianthusEEBusAdminSensor(admin_coordinator, entry.entry_id, origin)]


def _build_bus_address_sensors(device_coordinator, entry_id: str) -> list[SensorEntity]:
    """Return one address sensor per distinct bus identity with evidence."""
    from .sensor import HelianthusBusAddressSensor

    sensors: list[SensorEntity] = []
    seen_bus_keys: set[str] = set()
    for device in device_coordinator.data or []:
        if not has_bus_identity_evidence(device):
            continue
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
        bus_id = bus_identifier(entry_id, bus_key)
        sensors.append(HelianthusBusAddressSensor(device_coordinator, bus_id, address))
    return sensors


def _build_status_sensors(
    status_coordinator, daemon_device_id, adapter_device_id, has_reloaded
) -> list[SensorEntity]:
    """Return the daemon and adapter status sensors; skip unwanted optional fields."""
    from .sensor import HelianthusStatusSensor

    status_entries = status_coordinator.data or {}
    daemon_status = status_entries.get("daemon", {})
    adapter_status = status_entries.get("adapter", {})
    sensors: list[SensorEntity] = [
        HelianthusStatusSensor(
            status_coordinator, "Daemon", daemon_status, daemon_device_id, field
        )
        for field in DAEMON_STATUS_FIELDS
        if _daemon_field_wanted(has_reloaded, daemon_device_id, daemon_status, field)
    ]
    sensors.extend(
        HelianthusStatusSensor(
            status_coordinator, "Adapter", adapter_status, adapter_device_id, field
        )
        for field in ADAPTER_STATUS_FIELDS
    )
    return sensors


def _build_boiler_sensors(
    boiler_coordinator, entry_id: str, manufacturer: str, boiler_device_id
) -> list[SensorEntity]:
    """Return the boiler temperature, state, diagnostics and service sensors."""
    from .sensor import (
        HelianthusBoilerDiagnosticsSensor,
        HelianthusBoilerHoursTillServiceSensor,
        HelianthusBoilerStateSensor,
        HelianthusBoilerTemperatureSensor,
    )

    if not (boiler_coordinator and boiler_device_id):
        return []
    sensors: list[SensorEntity] = [
        HelianthusBoilerTemperatureSensor(
            boiler_coordinator, entry_id, boiler_device_id, field
        )
        for field in REDUCED_BOILER_TEMPERATURE_FIELDS
    ]
    for entity_class, fields in (
        (HelianthusBoilerStateSensor, BOILER_STATE_SENSOR_FIELDS),
        (HelianthusBoilerDiagnosticsSensor, BOILER_DIAGNOSTICS_SENSOR_FIELDS),
    ):
        sensors.extend(
            entity_class(
                coordinator=boiler_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                boiler_device_id=boiler_device_id,
                field=field,
            )
            for field in fields
        )
    sensors.append(
        HelianthusBoilerHoursTillServiceSensor(
            coordinator=boiler_coordinator,
            entry_id=entry_id,
            manufacturer=manufacturer,
            boiler_device_id=boiler_device_id,
        )
    )
    return sensors


def _build_circuit_sensors(
    circuit_coordinator, entry_id: str, manufacturer: str
) -> list[SensorEntity]:
    """Return the sensors of every circuit with a valid index."""
    from .sensor import HelianthusCircuitSensor

    if not (circuit_coordinator and circuit_coordinator.data):
        return []
    sensors: list[SensorEntity] = []
    for circuit in circuit_coordinator.data.get("circuits", []) or []:
        if not isinstance(circuit, dict):
            continue
        circuit_index = _parse_circuit_index(circuit.get("index"))
        if circuit_index is None:
            continue
        initial_name = _circuit_name(circuit, circuit_index)
        sensors.extend(
            HelianthusCircuitSensor(
                coordinator=circuit_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                circuit_index=circuit_index,
                initial_name=initial_name,
                field=field,
            )
            for field in CIRCUIT_SENSOR_FIELDS
        )
    return sensors


def _build_system_sensors(
    system_coordinator, entry_id: str, manufacturer: str, regulator_device_id
) -> list[SensorEntity]:
    """Return the system sensors; skip optional fields without a value."""
    from .sensor import HelianthusSystemSensor

    if not (system_coordinator and system_coordinator.data and regulator_device_id):
        return []
    sensors: list[SensorEntity] = []
    for field in SYSTEM_SENSOR_FIELDS:
        if field.optional:
            source_data = system_coordinator.data.get(field.source, {})
            if not isinstance(source_data, dict) or source_data.get(field.key) is None:
                continue
        sensors.append(
            HelianthusSystemSensor(
                coordinator=system_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                regulator_device_id=regulator_device_id,
                field=field,
            )
        )
    return sensors


def _build_radio_sensors(
    radio_coordinator,
    entry_id: str,
    manufacturer: str,
    radio_device_zone_names: dict[tuple[str, str], str],
    b524_merge_targets: dict[str, tuple[str, str]],
    has_reloaded,
) -> list[SensorEntity]:
    """Return the sensors of every exported radio device."""
    if not (radio_coordinator and radio_coordinator.data):
        return []
    sensors: list[SensorEntity] = []
    for radio in radio_coordinator.data.get("radio_devices", []) or []:
        sensors.extend(
            _build_radio_device_sensors(
                radio_coordinator,
                entry_id,
                manufacturer,
                radio,
                radio_device_zone_names,
                b524_merge_targets,
                has_reloaded,
            )
        )
    return sensors


def _exported_radio_slot(
    radio: object, b524_merge_targets: dict[str, tuple[str, str]]
) -> tuple[int, int, str] | None:
    """Return group, instance and bus key of a radio device that gets sensors."""
    if not isinstance(radio, dict) or not should_export_radio_device(radio):
        return None
    slot = _radio_slot(radio)
    bus_key = _radio_bus_key(radio)
    if slot is None or bus_key is None:
        return None
    # ADR-027: skip all sensors for merged B524 function-module slots.
    if bus_key in b524_merge_targets:
        return None
    return (slot[0], slot[1], bus_key)


def _build_radio_device_sensors(
    radio_coordinator,
    entry_id: str,
    manufacturer: str,
    radio: object,
    radio_device_zone_names: dict[tuple[str, str], str],
    b524_merge_targets: dict[str, tuple[str, str]],
    has_reloaded,
) -> list[SensorEntity]:
    """Return the sensors of one radio device, or none when it is not exported."""
    from .sensor import HelianthusRadioSensor

    exported = _exported_radio_slot(radio, b524_merge_targets)
    if exported is None:
        return []
    group, instance, bus_key = exported
    class_address = _parse_optional_int(radio.get("device_class_address"))
    is_room = class_address in _RADIO_ROOM_CLASSES
    radio_device_id = radio_device_identifier(entry_id, bus_key)
    radio_name = _radio_model_name(radio)
    radio_zone_name = radio_device_zone_names.get(radio_device_id)
    return [
        HelianthusRadioSensor(
            coordinator=radio_coordinator,
            entry_id=entry_id,
            manufacturer=manufacturer,
            radio_device_id=radio_device_id,
            radio_name=radio_name,
            group=group,
            instance=instance,
            zone_name=radio_zone_name,
            **spec,
        )
        for spec in _radio_sensor_specs(
            radio,
            group,
            is_room,
            lambda key: has_reloaded(
                f"{entry_id}-radio-{group:02x}-{instance:02d}-sensor-{key}"
            ),
        )
    ]


def _radio_sensor_specs(
    radio: dict[str, Any], group: int, is_room: bool, has_reloaded
) -> list[dict]:
    """Return the per-sensor arguments that one radio device gets, in order.

    ``has_reloaded(key)`` tells whether the sparse metadata sensor of ``key``
    has already had its one reload.
    """
    specs: list[dict] = []
    if is_room or radio.get("reception_strength") is not None:
        specs.append(
            {
                "key": "reception_strength",
                "label": "Signal Quality",
                "entity_category": EntityCategory.DIAGNOSTIC,
                "cast_int": True,
            }
        )
    if is_room:
        specs.append(
            {
                "key": "room_temperature_c",
                "label": "Room Temperature",
                "device_class": SensorDeviceClass.TEMPERATURE,
                "native_unit": UnitOfTemperature.CELSIUS,
                "state_class": SensorStateClass.MEASUREMENT,
            }
        )
        specs.append(
            {
                "key": "room_humidity_pct",
                "label": "Room Humidity",
                "device_class": _SENSOR_DEVICE_CLASS_HUMIDITY,
                "native_unit": PERCENTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
            }
        )
    elif group == 0x0C:
        specs.extend(_radio_metadata_specs(radio, has_reloaded))
    return specs


def _radio_metadata_specs(radio: dict[str, Any], has_reloaded) -> list[dict]:
    """Return the metadata sensor arguments of a B524 inventory radio slot.

    A metadata sensor is created while its value is present, and once its
    sparse entity has had its one reload whether or not the value is null then.
    """
    return [
        {
            "key": key,
            "label": label,
            "entity_category": EntityCategory.DIAGNOSTIC,
            "cast_int": True,
            "icon": icon,
        }
        for key, label, icon in (
            ("device_class_address", "Device Class Address", "mdi:identifier"),
            ("hardware_identifier", "Hardware Identifier", "mdi:identifier"),
            ("remote_control_address", "Remote Control Address", "mdi:remote"),
            ("zone_assignment", "Zone Assignment", "mdi:home-map-marker"),
        )
        if radio.get(key) is not None or has_reloaded(key)
    ]


def _build_fm5_sensors(
    fm5_coordinator, entry_id: str, manufacturer: str, parent_device_id
) -> list[SensorEntity]:
    """Return the FM5 mode sensor and, when interpreted, solar and cylinders."""
    from .sensor import HelianthusFM5ModeSensor

    if not (fm5_coordinator and fm5_coordinator.data):
        return []
    fm5_payload = fm5_coordinator.data
    mode = _fm5_mode(fm5_payload)
    sensors: list[SensorEntity] = []
    if parent_device_id:
        sensors.append(
            HelianthusFM5ModeSensor(
                coordinator=fm5_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                parent_device_id=parent_device_id,
            )
        )
    if mode == _FM5_MODE_INTERPRETED:
        if isinstance(fm5_payload.get("solar"), dict):
            sensors.extend(
                _build_solar_sensors(fm5_coordinator, entry_id, manufacturer, parent_device_id)
            )
        sensors.extend(
            _build_cylinder_sensors(
                fm5_coordinator,
                entry_id,
                manufacturer,
                fm5_payload.get("cylinders", []) or [],
                parent_device_id,
            )
        )
    return sensors


def _build_solar_sensors(
    fm5_coordinator, entry_id: str, manufacturer: str, parent_device_id
) -> list[SensorEntity]:
    """Return the solar circuit sensors of an interpreted FM5 payload."""
    from .sensor import HelianthusSolarSensor

    solar_device_id = solar_identifier(entry_id)
    return [
        HelianthusSolarSensor(
            coordinator=fm5_coordinator,
            entry_id=entry_id,
            manufacturer=manufacturer,
            solar_device_id=solar_device_id,
            parent_device_id=parent_device_id,
            key=key,
            label=label,
            device_class=device_class,
            native_unit=unit,
            state_class=state_class,
            icon=icon,
        )
        for key, label, device_class, unit, state_class, icon in (
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
            (
                "pump_hours",
                "Pump Hours",
                _SENSOR_DEVICE_CLASS_DURATION,
                "h",
                _SENSOR_STATE_CLASS_TOTAL_INCREASING,
                None,
            ),
        )
    ]


def _build_cylinder_sensors(
    fm5_coordinator, entry_id: str, manufacturer: str, cylinders, parent_device_id
) -> list[SensorEntity]:
    """Return the temperature and configuration sensors of every valid cylinder."""
    from .sensor import HelianthusCylinderConfigSensor, HelianthusCylinderSensor

    sensors: list[SensorEntity] = []
    for cylinder in cylinders:
        if not isinstance(cylinder, dict):
            continue
        index = _parse_optional_int(cylinder.get("index"))
        if index is None or index < 0:
            continue
        sensors.append(
            HelianthusCylinderSensor(
                coordinator=fm5_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                cylinder_index=index,
                parent_device_id=parent_device_id,
            )
        )
        sensors.extend(
            HelianthusCylinderConfigSensor(
                coordinator=fm5_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                cylinder_index=index,
                parent_device_id=parent_device_id,
                field=field,
            )
            for field in CYLINDER_CONFIG_SENSOR_FIELDS
        )
    return sensors


def _build_zone_and_dhw_sensors(
    semantic_coordinator,
    entry_id: str,
    manufacturer: str,
    via_device,
    regulator_device_id,
    zone_parent_device_ids,
) -> list[SensorEntity]:
    """Return the zone demand and valve sensors, then the DHW sensors."""
    from .sensor import HelianthusDemandSensor, HelianthusDHWStatusSensor

    if not (semantic_coordinator and semantic_coordinator.data):
        return []
    sensors: list[SensorEntity] = []
    for zone in semantic_coordinator.data.get("zones", []) or []:
        sensors.extend(
            _build_zone_sensors(
                semantic_coordinator,
                entry_id,
                manufacturer,
                via_device,
                zone,
                _zone_target_device_id(zone, zone_parent_device_ids, regulator_device_id),
            )
        )
    if semantic_coordinator.data.get("dhw") is not None:
        sensors.append(
            HelianthusDemandSensor(
                semantic_coordinator,
                entry_id,
                via_device,
                manufacturer,
                "DHW",
                ("dhw", None),
                target_device_id=None,
            )
        )
        sensors.append(
            HelianthusDHWStatusSensor(
                semantic_coordinator, entry_id, via_device, manufacturer
            )
        )
    return sensors


def _zone_target_device_id(zone, zone_parent_device_ids, regulator_device_id):
    """Return the device a zone's sensors attach to, or None to skip the zone."""
    zone_id = zone.get("id")
    if not zone_id:
        return None
    normalized_zone_id = _normalize_zone_id(zone_id)
    if normalized_zone_id is None:
        return None
    config = zone.get("config")
    mapping = (
        _parse_optional_int(config.get("room_temperature_zone_mapping"))
        if isinstance(config, dict)
        else None
    )
    target_device_id = zone_parent_device_ids.get(normalized_zone_id)
    if target_device_id is None:
        if mapping in (1, 2, 3, 4):
            return None
        target_device_id = regulator_device_id
    return target_device_id


def _build_zone_sensors(
    semantic_coordinator,
    entry_id: str,
    manufacturer: str,
    via_device,
    zone,
    target_device_id,
) -> list[SensorEntity]:
    """Return the demand and valve position sensors of one attached zone."""
    from .sensor import HelianthusDemandSensor, HelianthusZoneValvePositionSensor

    if target_device_id is None:
        return []
    zone_id = zone.get("id")
    return [
        HelianthusDemandSensor(
            semantic_coordinator,
            entry_id,
            via_device,
            manufacturer,
            zone.get("name") or f"Zone {zone_id}",
            ("zone", str(zone_id)),
            target_device_id=target_device_id,
        ),
        HelianthusZoneValvePositionSensor(
            coordinator=semantic_coordinator,
            entry_id=entry_id,
            manufacturer=manufacturer,
            zone_id=str(zone_id),
            zone_name=str(zone.get("name") or f"Zone {zone_id}"),
            target_device_id=target_device_id,
        ),
    ]


def _build_energy_sensors(
    energy_coordinator, entry_id: str, via_device, manufacturer: str
) -> list[SensorEntity]:
    """Return one energy sensor per source and usage."""
    from .sensor import HelianthusEnergySensor

    if not (energy_coordinator and energy_coordinator.data):
        return []
    return [
        HelianthusEnergySensor(
            energy_coordinator, entry_id, via_device, manufacturer, source, usage
        )
        for source in ("gas", "electric", "solar")
        for usage in ("dhw", "climate")
    ]


def _build_descriptor_sensors(
    coordinator, entity_class, entry_id: str, async_add_entities, unsub_listeners: list
) -> list[SensorEntity]:
    """Return one entity per known descriptor and add later descriptors on update.

    The listener adds only descriptor keys not seen before, in one call per
    update, and its unsubscribe callback is appended to ``unsub_listeners``.
    """
    descriptors = tuple(getattr(coordinator.data, "descriptors", ()) or ())
    known_descriptor_keys = {descriptor.key for descriptor in descriptors}

    def _entities(new_descriptors) -> list[SensorEntity]:
        return [
            entity_class(
                coordinator=coordinator,
                entry_id=entry_id,
                asset_ref=coordinator.asset_ref,
                descriptor=descriptor,
            )
            for descriptor in new_descriptors
        ]

    def _add_discovered_entities() -> None:
        current = coordinator.data
        new_descriptors = [
            descriptor
            for descriptor in tuple(getattr(current, "descriptors", ()) or ())
            if descriptor.key not in known_descriptor_keys
        ]
        if not new_descriptors:
            return
        known_descriptor_keys.update(descriptor.key for descriptor in new_descriptors)
        async_add_entities(_entities(new_descriptors))

    sensors = _entities(descriptors)
    unsub_listeners.append(coordinator.async_add_listener(_add_discovered_entities))
    return sensors


def _adapter_info_specs(adapter_hw: object) -> list[dict]:
    """Return the adapter hardware sensor arguments, in order."""
    specs = [
        {
            "key": "temperature_c",
            "label": "Adapter Temperature",
            "device_class": SensorDeviceClass.TEMPERATURE,
            "native_unit": UnitOfTemperature.CELSIUS,
            "state_class": SensorStateClass.MEASUREMENT,
            "icon": "mdi:thermometer",
        },
        {
            "key": "supply_voltage_mv",
            "label": "Adapter Supply Voltage",
            "device_class": SensorDeviceClass.VOLTAGE,
            "native_unit": "mV",
            "state_class": SensorStateClass.MEASUREMENT,
            "icon": "mdi:flash",
        },
        {
            "key": "bus_voltage_max_dv",
            "label": "eBUS Voltage Max",
            "device_class": SensorDeviceClass.VOLTAGE,
            "native_unit": "V",
            "state_class": SensorStateClass.MEASUREMENT,
            "icon": "mdi:sine-wave",
            "scale": 0.1,
        },
        {
            "key": "bus_voltage_min_dv",
            "label": "eBUS Voltage Min",
            "device_class": SensorDeviceClass.VOLTAGE,
            "native_unit": "V",
            "state_class": SensorStateClass.MEASUREMENT,
            "icon": "mdi:sine-wave",
            "scale": 0.1,
        },
        {
            "key": "restart_count",
            "label": "Adapter Restart Count",
            "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
            "icon": "mdi:counter",
        },
    ]
    if isinstance(adapter_hw, dict) and adapter_hw.get("reset_cause") is not None:
        specs.append(
            {
                "key": "reset_cause",
                "label": "Adapter Reset Cause",
                "icon": "mdi:alert-circle-outline",
            }
        )
    if isinstance(adapter_hw, dict) and adapter_hw.get("is_wifi") is True:
        specs.append(
            {
                "key": "wifi_rssi_dbm",
                "label": "Adapter WiFi Signal",
                "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
                "native_unit": "dBm",
                "state_class": SensorStateClass.MEASUREMENT,
                "icon": "mdi:wifi",
            }
        )
    return specs


def _build_adapter_info_sensors(
    adapter_info_coordinator, entry_id: str, adapter_device_id
) -> list[SensorEntity]:
    """Return the adapter hardware sensors; reset and Wi-Fi ones only when reported."""
    from .sensor import HelianthusAdapterInfoSensor

    if not (adapter_info_coordinator and adapter_device_id):
        return []
    return [
        HelianthusAdapterInfoSensor(
            adapter_info_coordinator, entry_id, adapter_device_id, **spec
        )
        for spec in _adapter_info_specs(adapter_info_coordinator.data)
    ]


async def _async_wire_b503_diagnostics(
    hass, entry, async_add_entities, b503_client, b503_device_id
) -> None:
    """Wire the optional B503 diagnostic sensor; a failure is logged, never raised."""
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
        _LOGGER.exception("Vaillant B503 sensor wiring failed; continuing without it")
