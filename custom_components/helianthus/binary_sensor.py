"""Binary schedule/state mirror entities for Helianthus semantic targets."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import dhw_identifier, zone_identifier


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


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["semantic_coordinator"]

    zones = coordinator.data.get("zones", []) if coordinator.data else []
    entities: list[HelianthusScheduleBinarySensor] = []
    for zone in zones:
        zone_id = zone.get("id")
        if zone_id is None:
            continue
        zone_name = str(zone.get("name") or f"Zone {zone_id}")
        for schedule_key, schedule_label in [
            ("schedule", "Daily Schedule Active"),
            ("quickveto", "Quick Veto Active"),
            ("away", "Away Schedule Active"),
        ]:
            entities.append(
                HelianthusScheduleBinarySensor(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    target_kind="zone",
                    target_id=str(zone_id),
                    target_name=zone_name,
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
                    target_kind="dhw",
                    target_id=None,
                    target_name="Domestic Hot Water",
                    schedule_key=schedule_key,
                    schedule_label=schedule_label,
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
        target_kind: str,
        target_id: str | None,
        target_name: str,
        schedule_key: str,
        schedule_label: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._target_kind = target_kind
        self._target_id = target_id
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
        return _normalize_preset(payload.get("preset")) == self._schedule_key

    @property
    def device_info(self) -> DeviceInfo:
        if self._target_kind == "zone":
            identifier = zone_identifier(self._entry_id, str(self._target_id))
            name = f"Zone {self._target_id}"
            model = "Virtual Zone Schedule"
        else:
            identifier = dhw_identifier(self._entry_id)
            name = "Domestic Hot Water"
            model = "Virtual DHW Schedule"
        return DeviceInfo(
            identifiers={identifier},
            manufacturer="Helianthus",
            model=model,
            name=name,
        )
