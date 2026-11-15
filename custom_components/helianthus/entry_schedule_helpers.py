"""Schedule helpers of one entry: the register write that a helper turning on requests."""

from __future__ import annotations

from collections.abc import Callable
import logging

from .admission import status_admission_trusted
from .const import (
    CONF_DHW_SCHEDULE_HELPER,
    CONF_ZONE_SCHEDULE_HELPERS,
    DEFAULT_DHW_SCHEDULE_HELPER,
    DEFAULT_ZONE_SCHEDULE_HELPERS,
)
from .semantic_freshness import semantic_target_available, semantic_target_is_stale

_LOGGER = logging.getLogger(__package__)

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


def _zone_schedule_semantic_write_ready(
    semantic_coordinator: object,
    zone_id: str,
) -> bool:
    return semantic_target_available(
        semantic_coordinator, "zone", zone_id
    ) and not semantic_target_is_stale(
        semantic_coordinator, "zone", zone_id
    )


def _dhw_schedule_semantic_write_ready(semantic_coordinator: object) -> bool:
    return semantic_target_available(
        semantic_coordinator, "dhw"
    ) and not semantic_target_is_stale(semantic_coordinator, "dhw")


async def _async_apply_zone_schedule(
    *,
    client: object,
    semantic_coordinator: object,
    status_coordinator: object,
    regulator_bus_address: int | None,
    zone_id: str,
) -> bool:
    """Apply the configured schedule helper to one zone."""
    if not status_admission_trusted(status_coordinator):
        _LOGGER.warning("Zone schedule helper ignored: source admission is not trusted")
        return False
    if regulator_bus_address is None:
        _LOGGER.warning("Zone schedule helper ignored: regulator address missing")
        return False
    if not _zone_schedule_semantic_write_ready(semantic_coordinator, zone_id):
        _LOGGER.warning(
            "Zone schedule helper ignored for %s: semantic data is stale or unavailable",
            zone_id,
        )
        return False
    instance = _zone_instance_from_id(zone_id)
    if instance is None:
        _LOGGER.warning("Zone schedule helper ignored: invalid zone id %s", zone_id)
        return False
    variables = {
        "address": int(regulator_bus_address),
        "params": {
            "opcode": 0x02,
            "group": 0x03,
            "instance": int(instance),
            "addr": 0x0006,
            "data": [1, 0x00],
        },
    }
    try:
        payload = await client.mutation(_INVOKE_SET_EXT_REGISTER, variables)
        invoke = payload.get("invoke") if isinstance(payload, dict) else None
        if not isinstance(invoke, dict) or not invoke.get("ok"):
            _LOGGER.warning("Zone schedule helper write failed for %s: %s", zone_id, invoke)
            return False
        await semantic_coordinator.async_request_refresh()
        return True
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        _LOGGER.warning("Zone schedule helper write failed for %s: %s", zone_id, exc)
        return False


async def _async_apply_dhw_schedule(
    *,
    client: object,
    semantic_coordinator: object,
    status_coordinator: object,
    regulator_bus_address: int | None,
) -> bool:
    """Apply the configured schedule helper to DHW."""
    if not status_admission_trusted(status_coordinator):
        _LOGGER.warning("DHW schedule helper ignored: source admission is not trusted")
        return False
    if regulator_bus_address is None:
        _LOGGER.warning("DHW schedule helper ignored: regulator address missing")
        return False
    if not _dhw_schedule_semantic_write_ready(semantic_coordinator):
        _LOGGER.warning("DHW schedule helper ignored: semantic data is stale or unavailable")
        return False
    variables = {
        "address": int(regulator_bus_address),
        "params": {
            "opcode": 0x02,
            "group": 0x01,
            "instance": 0x00,
            "addr": 0x0003,
            "data": [1, 0x00],
        },
    }
    try:
        payload = await client.mutation(_INVOKE_SET_EXT_REGISTER, variables)
        invoke = payload.get("invoke") if isinstance(payload, dict) else None
        if not isinstance(invoke, dict) or not invoke.get("ok"):
            _LOGGER.warning("DHW schedule helper write failed: %s", invoke)
            return False
        await semantic_coordinator.async_request_refresh()
        return True
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        _LOGGER.warning("DHW schedule helper write failed: %s", exc)
        return False


def _helper_turned_on(event: object) -> bool:
    new_state = event.data.get("new_state")
    if new_state is None:
        return False
    return str(new_state.state).strip().lower() == "on"


def register_schedule_helper_listeners(
    hass: object,
    entry: object,
    *,
    client: object,
    semantic_coordinator: object,
    status_coordinator: object,
    regulator_bus_address: int | None,
) -> list[Callable[[], None]]:
    """Track the configured schedule helpers; return the unsubscribe callbacks.

    One state listener is added per configured zone helper, then one for the
    hot-water helper when its entity is a schedule. A helper that turns on
    starts the matching register write as a task.
    """
    from homeassistant.core import callback
    from homeassistant.helpers.event import async_track_state_change_event

    fences = {
        "client": client,
        "semantic_coordinator": semantic_coordinator,
        "status_coordinator": status_coordinator,
        "regulator_bus_address": regulator_bus_address,
    }
    zone_schedule_helpers = _parse_zone_schedule_helper_bindings(
        str(entry.options.get(CONF_ZONE_SCHEDULE_HELPERS, DEFAULT_ZONE_SCHEDULE_HELPERS))
    )
    dhw_schedule_helper = str(
        entry.options.get(CONF_DHW_SCHEDULE_HELPER, DEFAULT_DHW_SCHEDULE_HELPER) or ""
    ).strip()
    unsub_listeners: list[Callable[[], None]] = []

    for zone_id, helper_entity in zone_schedule_helpers.items():
        @callback
        def _handle_zone_schedule_event(event, zone_id=zone_id, helper_entity=helper_entity):
            if str(event.data.get("entity_id") or "") != helper_entity:
                return
            if _helper_turned_on(event):
                hass.async_create_task(_async_apply_zone_schedule(zone_id=zone_id, **fences))

        unsub_listeners.append(
            async_track_state_change_event(hass, [helper_entity], _handle_zone_schedule_event)
        )

    if dhw_schedule_helper.startswith("schedule."):
        @callback
        def _handle_dhw_schedule_event(event):
            if _helper_turned_on(event):
                hass.async_create_task(_async_apply_dhw_schedule(**fences))

        unsub_listeners.append(
            async_track_state_change_event(hass, [dhw_schedule_helper], _handle_dhw_schedule_event)
        )
    return unsub_listeners
