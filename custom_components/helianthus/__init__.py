"""Helianthus Home Assistant integration."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .admission import (
    REPAIR_EMPTY_INVENTORY_UNTRUSTED,
    status_admission_trusted,
    update_effective_admission,
)
from .const import (
    CONF_DHW_SCHEDULE_HELPER,
    CONF_USE_SUBSCRIPTIONS,
    CONF_ZONE_SCHEDULE_HELPERS,
    DEFAULT_DHW_SCHEDULE_HELPER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SUBSCRIPTIONS,
    DEFAULT_ZONE_SCHEDULE_HELPERS,
    DOMAIN,
)
from .device_ids import has_known_identity_text
from .entry_ownership import iter_identifier_pairs

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
mutation SetExtRegister($address:Int!, $params:json!){
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


def _usable_presentation_label(value: object | None) -> str | None:
    if not isinstance(value, str) or not has_known_identity_text(value):
        return None
    return value.strip()


def _canonical_bus_display_name(device: dict) -> str | None:
    device_id = _normalized_ebus_code(device.get("device_id"))
    return (
        _usable_presentation_label(device.get("display_name"))
        or _usable_presentation_label(device.get("product_family"))
        or _KNOWN_BUS_DISPLAY_NAMES.get(device_id)
    )


def _canonical_bus_model_name(device: dict) -> str:
    raw_model = device.get("product_model")
    product_model = raw_model.strip() if isinstance(raw_model, str) else None
    part_number = _usable_presentation_label(device.get("part_number"))
    authoritative_device_id = _usable_presentation_label(device.get("device_id"))
    annotation = product_model and re.fullmatch(
        r"(?P<base>.+?)\s+\((?:part:\s*(?P<part>[^;()]*)\s*;\s*)?eBUS:\s*(?P<ebus_code>[^()]*)\s*\)",
        product_model,
        flags=re.IGNORECASE,
    )
    annotated_ebus_code = (
        _usable_presentation_label(annotation.group("ebus_code")) if annotation else None
    )
    ebus_code = _normalized_ebus_code(authoritative_device_id or annotated_ebus_code)
    selected_model = (
        _usable_presentation_label(annotation.group("base"))
        if annotation
        else _usable_presentation_label(product_model)
    )
    base_model = (
        selected_model or _KNOWN_BUS_MODELS.get(ebus_code) or authoritative_device_id or "unknown"
    )
    formatted_part_number = part_number or (
        _usable_presentation_label(annotation.group("part")) if annotation else None
    )
    if formatted_part_number:
        return f"{base_model} (part: {formatted_part_number}; eBUS: {ebus_code})"
    return f"{base_model} (eBUS: {ebus_code})"


def _stable_bus_identity_model(device: dict) -> str:
    from .device_ids import stable_bus_identity_model

    return stable_bus_identity_model(device.get("device_id"), device.get("product_model"))


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
        for identifier_domain, token in iter_identifier_pairs(identifiers)
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Helianthus config entry through the setup orchestration."""
    from .entry_setup import async_setup_entry as setup_entry

    return await setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one Helianthus config entry through the cleanup orchestration."""
    from .entry_cleanup import async_unload_entry as unload_entry

    return await unload_entry(hass, entry)
