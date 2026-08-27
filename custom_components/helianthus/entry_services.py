"""Optional config-entry service boundaries."""

from __future__ import annotations

from . import _LOGGER, async_setup_eebus_admin_boundary, async_unload_eebus_admin_boundary


async def async_setup_optional_eebus_admin_service(
    hass: object,
    *,
    entry_id: str,
    origin: str,
    instance_guid: str,
    scan_interval: object,
) -> tuple[object, object | None, bool]:
    """Set up the isolated AdminV1 diagnostic boundary without failing entry setup."""
    from .eebus_admin_coordinator import (
        EEBusAdminV1Coordinator,
        close_admin_session,
        create_admin_session,
        create_unavailable_eebus_admin_coordinator,
    )
    from .eebus_admin_services import bind_eebus_admin_coordinator

    admin_session = None
    admin_boundary = None
    try:
        admin_session = create_admin_session(hass)
        admin_boundary = await async_setup_eebus_admin_boundary(
            hass,
            entry_id=entry_id,
            origin=origin,
            instance_guid=instance_guid,
            session=admin_session,
        )
        admin_coordinator = EEBusAdminV1Coordinator(
            hass,
            admin_boundary.client,
            admin_boundary.lifecycle,
            scan_interval,
        )
        bind_eebus_admin_coordinator(
            hass,
            entry_id=entry_id,
            coordinator=admin_coordinator,
        )
        await admin_coordinator.async_refresh()
        return admin_coordinator, admin_session, True
    except Exception:
        _LOGGER.warning("eeBUS AdminV1 setup failed non-fatally for entry %s", entry_id)
        if admin_boundary is not None:
            admin_boundary.lifecycle.clear()
            await async_unload_eebus_admin_boundary(
                hass,
                entry_id=entry_id,
                session=admin_boundary.session,
            )
        elif admin_session is not None:
            await close_admin_session(admin_session)
        admin_coordinator = create_unavailable_eebus_admin_coordinator(
            hass,
            entry_id=entry_id,
            interval=scan_interval,
            code="admin_boundary_unavailable",
        )
        await admin_coordinator.async_refresh()
        return admin_coordinator, None, False
