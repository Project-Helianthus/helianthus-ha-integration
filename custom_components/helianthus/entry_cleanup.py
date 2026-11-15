"""Config-entry teardown orchestration for the Helianthus integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import DOMAIN, PLATFORMS
from .entry_services import async_unload_eebus_admin_boundary

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .entry_runtime import HelianthusConfigEntry, HelianthusRuntime


async def async_unload_entry(hass: HomeAssistant, entry: HelianthusConfigEntry) -> bool:
    """Unload a config entry; Home Assistant discards its runtime afterwards."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data: HelianthusRuntime | None = getattr(entry, "runtime_data", None)
        try:
            cleanup_counts = hass.data.get(DOMAIN, {}).get("_admission_cleanup_counts")
            if isinstance(cleanup_counts, dict):
                cleanup_counts.pop(entry.entry_id, None)
            task = None if data is None else data.listeners.subscription_task
            if task:
                task.cancel()
            listeners = None if data is None else data.listeners.unsub_listeners
            if listeners:
                for unsub in listeners:
                    try:
                        unsub()
                    except Exception:  # pragma: no cover - best-effort cleanup
                        pass
            admin_coordinator = None if data is None else data.services.eebus_admin_coordinator
            if admin_coordinator is not None:
                admin_coordinator.lifecycle.clear()
            admin_session = None if data is None else data.services.eebus_admin_session
            if admin_session is not None and data is not None and data.services.eebus_admin_services_registered:
                await async_unload_eebus_admin_boundary(hass, entry_id=entry.entry_id, session=admin_session)
            elif admin_session is not None:
                from .eebus_admin_coordinator import close_admin_session
                await close_admin_session(admin_session)
        finally:
            snapshot_coordinator = None if data is None else data.semantic_snapshot_coordinator
            if snapshot_coordinator is not None:
                await snapshot_coordinator.async_close()
        pv_m2m_boundary = None if data is None else data.services.pv_m2m_boundary
        if pv_m2m_boundary is not None:
            await pv_m2m_boundary.async_close()
        storage_m2m_boundary = None if data is None else data.services.storage_m2m_boundary
        if storage_m2m_boundary is not None:
            await storage_m2m_boundary.async_close()
        evse_m2m_boundary = None if data is None else data.services.evse_m2m_boundary
        if evse_m2m_boundary is not None:
            await evse_m2m_boundary.async_close()
    return unload_ok
