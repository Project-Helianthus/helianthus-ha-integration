"""Helianthus Home Assistant integration."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .admission import (
    REPAIR_EMPTY_INVENTORY_UNTRUSTED,
    status_admission_trusted,
    update_effective_admission,
)
from .const import (
    CONF_DHW_SCHEDULE_HELPER,
    CONF_HOST_ALIASES,
    CONF_INSTANCE_GUID,
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_USE_SUBSCRIPTIONS,
    CONF_VERSION,
    CONF_ZONE_SCHEDULE_HELPERS,
    DEFAULT_DHW_SCHEDULE_HELPER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_GRAPHQL_PATH,
    DEFAULT_GRAPHQL_TRANSPORT,
    DEFAULT_USE_SUBSCRIPTIONS,
    DEFAULT_ZONE_SCHEDULE_HELPERS,
    DOMAIN,
)

PLATFORMS: list[str] = [
    "sensor",
    "binary_sensor",
    "climate",
    "water_heater",
    "fan",
    "valve",
    "number",
    "select",
    "switch",
    "calendar",
    "text",
    "date",
]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _EEBusAdminBoundary:
    client: object
    lifecycle: object
    session: object


async def async_setup_eebus_admin_boundary(
    hass: object, *, entry_id: str, origin: str, instance_guid: str, session: object
) -> _EEBusAdminBoundary:
    """Create one isolated operator boundary and register fixed entry services."""
    from .eebus_admin import EEBusAdminV1Client
    from .eebus_admin_coordinator import EEBusAdminV1Lifecycle
    from .eebus_admin_services import register_eebus_admin_services

    lifecycle = EEBusAdminV1Lifecycle(entry_id=entry_id)
    lifecycle.reconcile_binding(origin=origin, instance_guid=instance_guid)
    client = EEBusAdminV1Client(session=session, base_url=origin)
    register_eebus_admin_services(hass, entry_id=entry_id, client=client)
    return _EEBusAdminBoundary(client=client, lifecycle=lifecycle, session=session)


async def async_unload_eebus_admin_boundary(hass: object, *, entry_id: str, session: object) -> None:
    """Close one isolated session; fixed services remain until final entry unload."""
    from .eebus_admin_coordinator import close_admin_session
    from .eebus_admin_services import unregister_eebus_admin_services

    unregister_eebus_admin_services(hass, entry_id=entry_id)
    await close_admin_session(session)

# --- B524 namespace: camelCase -> snake_case unique_id migration (C2) ---
# Sorted by key length descending to prevent substring collision during replace.
_SNAKE_CASE_UID_RENAME_MAP: list[tuple[str, str]] = [
    ("hwcCylinderTemperatureBottom", "hwc_cylinder_temperature_bottom"),
    ("hwcCylinderTemperatureTop", "hwc_cylinder_temperature_top"),
    ("centralHeatingPumpActive", "central_heating_pump_active"),
    ("diverterValvePositionPct", "diverter_valve_position_pct"),
    ("deactivationsTemplimiter", "deactivations_templimiter"),
    ("outdoorTemperatureAvg24h", "outdoor_temperature_avg24h"),
    ("dhwStorageTemperatureC", "dhw_storage_temperature_c"),
    ("circulationPumpActive", "circulation_pump_active"),
    ("systemFlowTemperature", "system_flow_temperature"),
    ("collectorTemperatureC", "collector_temperature_c"),
    ("adaptiveHeatingCurve", "adaptive_heating_curve"),
    ("centralHeatingStarts", "central_heating_starts"),
    ("remoteControlAddress", "remote_control_address"),
    ("ionisationVoltageUa", "ionisation_voltage_ua"),
    ("centralHeatingHours", "central_heating_hours"),
    ("systemWaterPressure", "system_water_pressure"),
    ("externalPumpActive", "external_pump_active"),
    ("returnTemperatureC", "return_temperature_c"),
    ("storageLoadPumpPct", "storage_load_pump_pct"),
    ("outdoorTemperature", "outdoor_temperature"),
    ("deviceClassAddress", "device_class_address"),
    ("hardwareIdentifier", "hardware_identifier"),
    ("dhwBivalencePointC", "dhw_bivalence_point_c"),
    ("maxRoomHumidityPct", "max_room_humidity_pct"),
    ("receptionStrength", "reception_strength"),
    ("chargeHysteresisC", "charge_hysteresis_c"),
    ("hcBivalencePointC", "hc_bivalence_point_c"),
    ("installerMenuCode", "installer_menu_code"),
    ("updatesAvailable", "updates_available"),
    ("initiatorAddress", "initiator_address"),
    ("flowTemperatureC", "flow_temperature_c"),
    ("deactivationsIFC", "deactivations_ifc"),
    ("mixerPositionPct", "mixer_position_pct"),
    ("roomTemperatureC", "room_temperature_c"),
    ("valvePositionPct", "valve_position_pct"),
    ("hoursTillService", "hours_till_service"),
    ("hcEmergencyTempC", "hc_emergency_temp_c"),
    ("firmwareVersion", "firmware_version"),
    ("dhwTemperatureC", "dhw_temperature_c"),
    ("roomHumidityPct", "room_humidity_pct"),
    ("supplyVoltageMv", "supply_voltage_mv"),
    ("busVoltageMaxDv", "bus_voltage_max_dv"),
    ("busVoltageMinDv", "bus_voltage_min_dv"),
    ("hwcMaxFlowTempC", "hwc_max_flow_temp_c"),
    ("maintenanceDate", "maintenance_date"),
    ("gasValveActive", "gas_valve_active"),
    ("maintenanceDue", "maintenance_due"),
    ("zoneAssignment", "zone_assignment"),
    ("flowsetHwcMaxC", "flowset_hwc_max_c"),
    ("installerPhone", "installer_phone"),
    ("modulationPct", "modulation_pct"),
    ("flowSetpointC", "flow_setpoint_c"),
    ("calcFlowTempC", "calc_flow_temp_c"),
    ("chargeOffsetC", "charge_offset_c"),
    ("flowsetHcMaxC", "flowset_hc_max_c"),
    ("partloadHwcKW", "partload_hwc_kw"),
    ("installerName", "installer_name"),
    ("solarEnabled", "solar_enabled"),
    ("functionMode", "function_mode"),
    ("circuitState", "circuit_state"),
    ("systemScheme", "system_scheme"),
    ("currentYield", "current_yield"),
    ("maxSetpointC", "max_setpoint_c"),
    ("temperatureC", "temperature_c"),
    ("restartCount", "restart_count"),
    ("heatingCurve", "heating_curve"),
    ("flowTempMaxC", "flow_temp_max_c"),
    ("flowTempMinC", "flow_temp_min_c"),
    ("summerLimitC", "summer_limit_c"),
    ("partloadHcKW", "partload_hc_kw"),
    ("flameActive", "flame_active"),
    ("fanSpeedRpm", "fan_speed_rpm"),
    ("wifiRssiDbm", "wifi_rssi_dbm"),
    ("phoneNumber", "phone_number"),
    ("pumpActive", "pump_active"),
    ("pumpStarts", "pump_starts"),
    ("resetCause", "reset_cause"),
    ("frostProtC", "frost_prot_c"),
    ("pumpHours", "pump_hours"),
    ("dhwStarts", "dhw_starts"),
    ("dhwHours", "dhw_hours"),
    ("fanHours", "fan_hours"),
    ("dewPoint", "dew_point"),
]


def _migrate_unique_ids_to_snake_case(hass: object, entry: object) -> int:
    """Migrate camelCase unique_ids to snake_case (B524 namespace contract).

    Returns the number of entities migrated.
    """
    import homeassistant.helpers.entity_registry as er

    registry = er.async_get(hass)
    migrated = 0
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        old_uid = entity_entry.unique_id
        new_uid = old_uid
        for old_key, new_key in _SNAKE_CASE_UID_RENAME_MAP:
            new_uid = new_uid.replace(old_key, new_key)
        if new_uid != old_uid:
            try:
                registry.async_update_entity(
                    entity_entry.entity_id, new_unique_id=new_uid
                )
                migrated += 1
            except Exception:
                _LOGGER.warning(
                    "Failed to migrate unique_id %s -> %s for %s",
                    old_uid,
                    new_uid,
                    entity_entry.entity_id,
                )
    return migrated


_HEX4_RE = re.compile(r"^[0-9a-fA-F]{4}$")
_KNOWN_BUS_DISPLAY_NAMES: dict[str, str] = {
    "BASV": "sensoCOMFORT RF",
    "VR_71": "FM5 Control Centre",
    "VR71": "FM5 Control Centre",
    "BAI00": "ecoTEC plus",
    "NETX3": "myVaillant Connect",
}
_KNOWN_BUS_MODELS: dict[str, str] = {
    "BASV": "VRC 720f/2",
    "VR_71": "VR 71",
    "VR71": "VR 71",
    "BAI00": "VUW",
    "NETX3": "VR940f",
}

_INVOKE_SET_EXT_REGISTER = """
mutation SetExtRegister($address:Int!, $params:JSON!){
  invoke(address:$address, plane:"system", method:"set_ext_register", params:$params){
    ok
    error {
      message
      code
      category
    }
  }
}
"""


def _format_hex4_version(value: str | None) -> str | None:
    if not value:
        return None
    stripped = str(value).strip()
    if "." in stripped:
        return stripped
    if _HEX4_RE.match(stripped):
        return f"{stripped[0:2]}.{stripped[2:4]}"
    return stripped


def _clean_label(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _normalized_ebus_code(device_id: object | None) -> str:
    value = _clean_label(device_id) or "UNKNOWN"
    normalized = value.upper()
    if normalized.startswith("BASV"):
        return "BASV"
    return normalized


def _canonical_bus_display_name(device: dict) -> str | None:
    device_id = _normalized_ebus_code(device.get("device_id"))
    known = _KNOWN_BUS_DISPLAY_NAMES.get(device_id)
    if known:
        return known
    return _clean_label(device.get("display_name")) or _clean_label(device.get("product_family"))


def _canonical_bus_model_name(device: dict) -> str:
    product_model = _clean_label(device.get("product_model"))
    device_id = _clean_label(device.get("device_id")) or "unknown"
    ebus_code = _normalized_ebus_code(device_id)
    base_model = product_model or _KNOWN_BUS_MODELS.get(ebus_code) or str(device_id)
    if "(eBUS:" in base_model:
        return base_model
    return f"{base_model} (eBUS: {ebus_code})"


def _stable_bus_identity_model(device: dict) -> str:
    from .device_ids import stable_bus_identity_model

    return stable_bus_identity_model(device.get("device_id"), device.get("product_model"))


def _parse_bus_address(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 0 <= value <= 0xFF:
            return value
        return None
    try:
        parsed = int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return None
    if 0 <= parsed <= 0xFF:
        return parsed
    return None


def _identifier_belongs_to_entry(token: str, entry_id: str) -> bool:
    return token in {
        f"daemon-{entry_id}",
        f"adapter-{entry_id}",
        f"{entry_id}-dhw",
        f"{entry_id}-energy",
        f"{entry_id}-boiler-burner",
        f"{entry_id}-boiler-hydraulics",
    } or (
        token.startswith(f"{entry_id}-bus-")
        or token.startswith(f"{entry_id}-zone-")
        or token.startswith(f"{entry_id}-circuit-")
        or token.startswith(f"{entry_id}-radio-")
        or token.startswith(f"{entry_id}-cylinder-")
        or token == f"{entry_id}-solar"
    )


def _identifier_matches_any_entry(token: str, active_entry_ids: set[str]) -> bool:
    for entry_id in active_entry_ids:
        if _identifier_belongs_to_entry(token, entry_id):
            return True
    return False


def _is_stale_bus_identifier(token: str, entry_id: str, known_bus_devices: set[str]) -> bool:
    prefix = f"{entry_id}-bus-"
    if not token.startswith(prefix):
        return False
    return token[len(prefix) :] not in known_bus_devices


def _stale_bus_address_unique_id(
    unique_id: str | None,
    entry_id: str,
    known_bus_devices: set[str],
) -> bool:
    if not unique_id:
        return False
    prefix = f"{entry_id}-bus-"
    suffix = "-ebus-address"
    if not unique_id.startswith(prefix) or not unique_id.endswith(suffix):
        return False
    bus_device_key = unique_id[len(prefix) : -len(suffix)]
    return bus_device_key not in known_bus_devices


def _bus_identifier_tokens_for_entry(identifiers: set[object], entry_id: str) -> tuple[str, ...]:
    prefix = f"{entry_id}-bus-"
    return tuple(
        token
        for identifier_domain, token in _iter_identifier_pairs(identifiers)
        if identifier_domain == DOMAIN and token.startswith(prefix)
    )


def _legacy_bus_identifier_address(token: str, entry_id: str) -> int | None:
    prefix = f"{entry_id}-bus-"
    if not token.startswith(prefix):
        return None
    bus_key = token[len(prefix) :]
    if "-sn-" in bus_key or "-mac-" in bus_key:
        return None
    for part in reversed([chunk.strip() for chunk in bus_key.split("-") if chunk.strip()]):
        if len(part) != 2:
            continue
        try:
            return int(part, 16)
        except ValueError:
            continue
    return None


def _select_bus_migration_target(
    existing_devices: tuple[object, ...],
    *,
    entry_id: str,
    stable_identifier: tuple[str, str],
    address: int | None,
    manufacturer: str,
    model_name: str,
    serial_number: str | None,
) -> object | None:
    _, stable_token = stable_identifier
    for device_entry in existing_devices:
        tokens = _bus_identifier_tokens_for_entry(getattr(device_entry, "identifiers", set()), entry_id)
        if stable_token in tokens:
            return device_entry

    best_score: tuple[int, int, int, int, int, int] | None = None
    best_entry: object | None = None
    serialized_model_matches: list[object] = []
    for device_entry in existing_devices:
        tokens = _bus_identifier_tokens_for_entry(getattr(device_entry, "identifiers", set()), entry_id)
        if not tokens:
            continue
        entry_manufacturer = _clean_label(getattr(device_entry, "manufacturer", None))
        if entry_manufacturer and entry_manufacturer != manufacturer:
            continue
        entry_model = _clean_label(getattr(device_entry, "model", None))
        entry_serial = _clean_label(getattr(device_entry, "serial_number", None))
        serial_match = int(bool(serial_number and entry_serial and entry_serial == serial_number))
        model_match = int(bool(entry_model and entry_model == model_name))
        if not serial_match and not model_match:
            continue
        if serial_match:
            return device_entry
        address_match = int(
            any(
                candidate == address
                for candidate in (_legacy_bus_identifier_address(token, entry_id) for token in tokens)
                if candidate is not None
            )
        )
        if entry_serial and model_match:
            serialized_model_matches.append(device_entry)
        if not address_match:
            continue
        score = (
            address_match,
            model_match,
            int(bool(entry_serial)),
            int(bool(getattr(device_entry, "area_id", None))),
            -len(tokens),
        )
        if best_score is None or score > best_score:
            best_score = score
            best_entry = device_entry
    if len(serialized_model_matches) == 1:
        return serialized_model_matches[0]
    return best_entry


def _iter_identifier_pairs(identifiers: set[object]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for identifier in identifiers:
        if not isinstance(identifier, (tuple, list)) or len(identifier) < 2:
            continue
        pairs.append((str(identifier[0]), str(identifier[1])))
    return tuple(pairs)


def _parse_zone_schedule_helper_bindings(raw: str) -> dict[str, str]:
    bindings: dict[str, str] = {}
    text = str(raw or "").strip()
    if not text:
        return bindings
    for chunk in text.split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        zone_key = key.strip().lower()
        helper_entity = value.strip()
        if not helper_entity.startswith("schedule."):
            continue
        if zone_key.isdigit():
            zone_key = f"zone-{zone_key}"
        if zone_key.startswith("zone-"):
            suffix = zone_key[5:]
            if suffix.isdigit() and int(suffix) > 0:
                bindings[zone_key] = helper_entity
    return bindings


def _zone_instance_from_id(zone_id: str) -> int | None:
    token = str(zone_id or "").strip().lower()
    if token.startswith("zone-"):
        token = token[5:]
    if not token.isdigit():
        return None
    value = int(token)
    if value <= 0:
        return None
    return value - 1


def _parse_identifier_index(token: str, prefix: str) -> int | None:
    if not token.startswith(prefix):
        return None
    suffix = token[len(prefix) :]
    if not suffix.isdigit():
        return None
    value = int(suffix)
    if value < 0:
        return None
    return value


def _config_entry_sort_key(config_entry: object) -> str:
    """Return a stable ordering key for duplicate config entry ownership."""

    return str(getattr(config_entry, "entry_id", "") or "")


def _config_entry_enabled(config_entry: object) -> bool:
    """Return whether a config entry may own live gateway setup."""

    return getattr(config_entry, "disabled_by", None) is None


_HASSIO_HELIANTHUS_FALLBACK_HOSTS = (
    "local-helianthus.local.hass.io",
    "172.30.32.1",
)


def _entry_identity_probe_addresses(
    data: Mapping[str, object],
    primary_host: str,
) -> tuple[str, ...]:
    """Return secondary hosts to try when verifying a stored gateway endpoint."""

    ordered: list[str] = []
    seen = {primary_host}

    def add(value: object) -> None:
        host = str(value or "").strip()
        if not host or host in seen:
            return
        seen.add(host)
        ordered.append(host)

    aliases = data.get(CONF_HOST_ALIASES)
    if isinstance(aliases, str):
        add(aliases)
    elif isinstance(aliases, Iterable):
        for alias in aliases:
            add(alias)

    if primary_host.startswith("172.30."):
        for host in _HASSIO_HELIANTHUS_FALLBACK_HOSTS:
            add(host)

    return tuple(ordered)


def _update_entry_endpoint_if_changed(
    hass: object,
    entry: object,
    verified_endpoint: object,
    *,
    version: str | None,
) -> bool:
    """Persist a verified reachable endpoint when a config entry drifted."""

    from .identity import same_endpoint, updated_entry_data

    data = getattr(entry, "data", None) or {}
    if same_endpoint(data, verified_endpoint):
        return False
    hass.config_entries.async_update_entry(
        entry,
        data=updated_entry_data(
            data,
            verified_endpoint,
            version=version or getattr(verified_endpoint, "version", None),
        ),
        unique_id=verified_endpoint.instance_guid,
    )
    return True


async def _find_verified_entry_by_configured_instance_guid(
    hass: object,
    session: object,
    instance_guid: str,
    *,
    exclude_entry_id: str | None = None,
) -> object | None:
    from .identity import (
        GatewayIdentityVerificationError,
        configured_instance_guid,
        verify_gateway_identity,
    )

    owner_entry: object | None = None

    for config_entry in hass.config_entries.async_entries(DOMAIN):
        if getattr(config_entry, "entry_id", None) == exclude_entry_id:
            continue
        if not _config_entry_enabled(config_entry):
            continue
        data = getattr(config_entry, "data", None) or {}
        if (
            configured_instance_guid(data, getattr(config_entry, "unique_id", None))
            != instance_guid
        ):
            continue
        try:
            host = str(data["host"])
            port = int(data["port"])
        except (KeyError, TypeError, ValueError):
            continue
        path = data.get(CONF_PATH) or DEFAULT_GRAPHQL_PATH
        transport = data.get(CONF_TRANSPORT) or DEFAULT_GRAPHQL_TRANSPORT
        version = (data.get(CONF_VERSION) or "").strip() or None
        try:
            await verify_gateway_identity(
                session=session,
                host=host,
                port=port,
                path=path,
                transport=transport,
                expected_instance_guid=instance_guid,
                version=version,
            )
        except (GatewayIdentityVerificationError, TypeError, ValueError) as exc:
            _LOGGER.debug(
                "Configured Helianthus owner entry %s did not verify live "
                "instance GUID %s: %s",
                getattr(config_entry, "entry_id", "<unknown>"),
                instance_guid,
                getattr(exc, "reason", type(exc).__name__),
            )
            continue
        if owner_entry is None or _config_entry_sort_key(config_entry) < _config_entry_sort_key(
            owner_entry
        ):
            owner_entry = config_entry
    return owner_entry


def _merge_duplicate_config_entry_options(
    hass: object,
    *,
    alias_entry: object,
    owner_entry: object,
) -> bool:
    """Preserve alias-only options before removing a duplicate config entry."""

    alias_options = dict(getattr(alias_entry, "options", None) or {})
    if not alias_options:
        return False
    owner_options = dict(getattr(owner_entry, "options", None) or {})
    merged_options = _deep_merge_options(alias_options, owner_options)
    if merged_options == owner_options:
        return False
    hass.config_entries.async_update_entry(owner_entry, options=merged_options)
    return True


def _deep_merge_options(
    alias_options: Mapping[str, object],
    owner_options: Mapping[str, object],
) -> dict[str, object]:
    """Merge alias-only nested options while preserving owner conflicts."""

    merged = dict(alias_options)
    for key, owner_value in owner_options.items():
        alias_value = merged.get(key)
        if isinstance(alias_value, Mapping) and isinstance(owner_value, Mapping):
            merged[key] = _deep_merge_options(alias_value, owner_value)
        else:
            merged[key] = owner_value
    return merged


def _schedule_duplicate_config_entry_removal(hass: object, entry_id: str) -> bool:
    """Schedule removal of an alias entry after setup returns."""

    async_remove = getattr(hass.config_entries, "async_remove", None)
    async_create_task = getattr(hass, "async_create_task", None)
    if not callable(async_remove) or not callable(async_create_task):
        return False
    async_create_task(async_remove(entry_id))
    return True


def _remove_config_entry_registry_state(
    *,
    device_registry: object,
    entity_registry: object,
    entry_id: str,
    device_entries_for_config_entry: object,
    entity_entries_for_config_entry: object,
) -> tuple[int, int]:
    """Remove device/entity registry rows that belong only to a duplicate entry."""

    removed_entities = 0
    for entity_entry in tuple(entity_entries_for_config_entry(entity_registry, entry_id)):
        entity_registry.async_remove(entity_entry.entity_id)
        removed_entities += 1

    removed_devices = 0
    for device_entry in tuple(device_entries_for_config_entry(device_registry, entry_id)):
        device_registry.async_remove_device(device_entry.id)
        removed_devices += 1

    return removed_entities, removed_devices


async def async_sanitize_legacy_eebus_admin_entry(hass: object, entry: object) -> bool:
    """Remove the retired eeBUS field without changing any other entry state."""
    data = dict(getattr(entry, "data", {}) or {})
    if "eebus_admin_credential" not in data:
        return False
    data.pop("eebus_admin_credential")
    hass.config_entries.async_update_entry(entry, data=data)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Helianthus config entry through the setup orchestration."""
    from .entry_setup import async_setup_entry as setup_entry

    return await setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one Helianthus config entry through the cleanup orchestration."""
    from .entry_cleanup import async_unload_entry as unload_entry

    return await unload_entry(hass, entry)
