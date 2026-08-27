"""Config-entry teardown orchestration for the Helianthus integration."""

from __future__ import annotations

from . import DOMAIN, PLATFORMS, async_unload_eebus_admin_boundary

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        cleanup_counts = hass.data[DOMAIN].get("_admission_cleanup_counts")
        if isinstance(cleanup_counts, dict):
            cleanup_counts.pop(entry.entry_id, None)
        task = None if data is None else data.get("subscription_task")
        if task:
            task.cancel()
        listeners = None if data is None else data.get("unsub_listeners")
        if listeners:
            for unsub in listeners:
                try:
                    unsub()
                except Exception:  # pragma: no cover - best-effort cleanup
                    pass
        admin_coordinator = None if data is None else data.get("eebus_admin_coordinator")
        if admin_coordinator is not None:
            admin_coordinator.lifecycle.clear()
        admin_session = None if data is None else data.get("eebus_admin_session")
        if admin_session is not None and data is not None and data.get("eebus_admin_services_registered"):
            await async_unload_eebus_admin_boundary(hass, entry_id=entry.entry_id, session=admin_session)
        elif admin_session is not None:
            from .eebus_admin_coordinator import close_admin_session
            await close_admin_session(admin_session)
        pv_m2m_boundary = None if data is None else data.get("pv_m2m_boundary")
        if pv_m2m_boundary is not None:
            await pv_m2m_boundary.async_close()
    return unload_ok
