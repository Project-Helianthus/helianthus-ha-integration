"""B503 boiler active-error diagnostic sensor (plan §M4_HA).

Surfaces :code:`vaillantErrors.firstActiveError` as a HA sensor entity
gated by :code:`vaillantCapabilities.b503.reason`. Follows the lifecycle
rules from plan AD11 (3-poll hysteresis on NOT_SUPPORTED flips) and
AD15 (state transitions per capability reason). No F.xxx translation
(AD05) — native_value is the raw decimal integer from GraphQL.

This module is test-surface oriented: the coordinator exposes
``async_refresh_once()`` and ``should_create_entity()`` so unit tests
can drive the lifecycle without spinning up a full HA loop. Production
``async_setup_entry`` wiring will be added by the platform integration
step; the sensor + coordinator classes here are the load-bearing
pieces.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)

# Plan AD11 — number of consecutive NOT_SUPPORTED polls required to
# destroy the entity after it has been created. The first two polls
# keep the entity alive (state=unavailable) so a transient capability
# flip does not churn the entity registry.
NOT_SUPPORTED_HYSTERESIS_POLLS = 3

# Capability reason enum values that keep the entity alive but report
# state=unavailable per plan AD15.
_UNAVAILABLE_REASONS = frozenset({"TRANSPORT_DOWN", "UNKNOWN", "SESSION_BUSY"})
_AVAILABLE_REASON = "AVAILABLE"
_NOT_SUPPORTED_REASON = "NOT_SUPPORTED"

QUERY_B503_STATE = """
query VaillantB503State {
  vaillantCapabilities {
    b503 {
      reason
    }
  }
  vaillantErrors {
    firstActiveError
    slots
  }
}
"""


def _coerce_int(value: Any) -> int | None:
    """Coerce to int; return None for bools, None, or non-numeric values."""
    if isinstance(value, bool):
        return None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_slots(raw: Any) -> list[int | None]:
    """Normalize the 5-slot error history array.

    Always returns a list of 5 entries (int or None). Non-list input
    collapses to all-None. Extra entries are truncated; shorter lists
    are padded with None.
    """
    out: list[int | None] = [None, None, None, None, None]
    if not isinstance(raw, list):
        return out
    for idx, entry in enumerate(raw[:5]):
        out[idx] = _coerce_int(entry)
    return out


class VaillantB503Coordinator:
    """Coordinator for B503 capability + error telemetry.

    Not a DataUpdateCoordinator subclass in the unit-test surface so we
    can drive it synchronously without HA's event loop scaffolding. The
    contract exposed to the sensor entity is:

    - :py:attr:`data` — the most recent normalized payload, or None.
    - :py:meth:`should_create_entity` — honors plan AD11 hysteresis.
    - :py:meth:`async_refresh_once` — fetches one poll and updates
      state. Production wiring can subclass or adapt this into a
      :class:`DataUpdateCoordinator`.
    """

    def __init__(self, hass: Any, client: Any, scan_interval: int) -> None:
        self.hass = hass
        self._client = client
        self.update_interval = timedelta(seconds=max(1, scan_interval))
        self.data: dict[str, Any] | None = None
        # Hysteresis state machine:
        # - _entity_ever_created: once we observe a non-NOT_SUPPORTED reason,
        #   the entity is created and stays until 3 consecutive NOT_SUPPORTED
        #   polls elapse.
        # - _not_supported_streak: count of consecutive NOT_SUPPORTED polls
        #   since the last non-NOT_SUPPORTED observation.
        # - _entity_destroyed: sticky flag set when the streak reaches
        #   NOT_SUPPORTED_HYSTERESIS_POLLS.
        self._entity_ever_created = False
        self._not_supported_streak = 0
        self._entity_destroyed = False

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def async_refresh_once(self) -> None:
        """Execute one poll and update lifecycle state."""
        try:
            payload = await self._client.execute(QUERY_B503_STATE)
        except Exception as exc:  # noqa: BLE001 — keep entity alive on transport errors
            _LOGGER.debug("B503 poll failed: %s", exc)
            # Treat transport failure as UNKNOWN — entity stays, state=unavailable.
            self._apply_reason("UNKNOWN")
            self.data = {"reason": "UNKNOWN", "first_active_error": None, "slots": [None] * 5}
            return

        reason = _extract_reason(payload)
        errors = payload.get("vaillantErrors") if isinstance(payload, dict) else None
        first_active: int | None = None
        slots: list[int | None] = [None, None, None, None, None]
        if isinstance(errors, dict):
            first_active = _coerce_int(errors.get("firstActiveError"))
            slots = _normalize_slots(errors.get("slots"))

        self._apply_reason(reason)
        self.data = {
            "reason": reason,
            "first_active_error": first_active,
            "slots": slots,
        }

    def _apply_reason(self, reason: str) -> None:
        if reason == _NOT_SUPPORTED_REASON:
            if self._entity_ever_created and not self._entity_destroyed:
                self._not_supported_streak += 1
                if self._not_supported_streak >= NOT_SUPPORTED_HYSTERESIS_POLLS:
                    self._entity_destroyed = True
            # If the entity was never created (cold-start NOT_SUPPORTED),
            # leave all lifecycle flags untouched — no entity is born.
            return
        # Any non-NOT_SUPPORTED reason creates the entity (if not already)
        # and resets the hysteresis counter.
        self._entity_ever_created = True
        self._not_supported_streak = 0
        # NOTE: once destroyed, we do NOT resurrect the entity mid-runtime.
        # A fresh config-entry reload is required to re-probe capability.

    # ------------------------------------------------------------------
    # Lifecycle predicate
    # ------------------------------------------------------------------

    def should_create_entity(self) -> bool:
        """Return True iff the entity should exist in the registry."""
        if not self._entity_ever_created:
            return False
        if self._entity_destroyed:
            return False
        return True

    # ------------------------------------------------------------------
    # State views consumed by the sensor entity
    # ------------------------------------------------------------------

    def current_reason(self) -> str:
        if not isinstance(self.data, dict):
            return "UNKNOWN"
        reason = self.data.get("reason")
        return str(reason) if isinstance(reason, str) else "UNKNOWN"

    def current_first_active(self) -> int | None:
        if not isinstance(self.data, dict):
            return None
        return _coerce_int(self.data.get("first_active_error"))

    def current_slots(self) -> list[int | None]:
        if not isinstance(self.data, dict):
            return [None, None, None, None, None]
        slots = self.data.get("slots")
        if isinstance(slots, list):
            return list(slots)
        return [None, None, None, None, None]


def _extract_reason(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "UNKNOWN"
    caps = payload.get("vaillantCapabilities")
    if not isinstance(caps, dict):
        return "UNKNOWN"
    b503 = caps.get("b503")
    if not isinstance(b503, dict):
        return "UNKNOWN"
    reason = b503.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    return "UNKNOWN"


class BoilerActiveErrorSensor(CoordinatorEntity, SensorEntity):
    """HA diagnostic sensor exposing the boiler's first active error.

    Plan §M4_HA: state = decimal int (or None when healthy); attribute
    ``error_history`` = 5-slot array; attribute ``capability_reason`` =
    current capability enum string.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-circle-outline"
    _attr_has_entity_name = True
    _attr_name = "Boiler Active Error"

    def __init__(
        self,
        coordinator: VaillantB503Coordinator,
        entry_id: str,
        boiler_device_id: tuple[str, str] | None = None,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}-boiler-active-error"
        if boiler_device_id is not None:
            self._attr_device_info = DeviceInfo(identifiers={boiler_device_id})

    # ------------------------------------------------------------------
    # HA entity surface
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        reason = self._coordinator.current_reason()
        if reason == _AVAILABLE_REASON:
            return True
        # NOT_SUPPORTED during hysteresis window, TRANSPORT_DOWN, UNKNOWN,
        # SESSION_BUSY all render state=unavailable per AD15.
        return False

    @property
    def native_value(self) -> int | None:
        if not self.available:
            return None
        return self._coordinator.current_first_active()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "error_history": self._coordinator.current_slots(),
            "capability_reason": self._coordinator.current_reason(),
        }


__all__ = [
    "BoilerActiveErrorSensor",
    "NOT_SUPPORTED_HYSTERESIS_POLLS",
    "QUERY_B503_STATE",
    "VaillantB503Coordinator",
]


# Suppress unused-import warning for DOMAIN in static analysis; the const
# is re-exported for integration wiring downstream.
_ = DOMAIN
