"""The register-services phase of config-entry setup.

This module owns the optional isolated eeBUS admin boundary, the PV, storage
and EVSE consumer boundaries, the ``ServiceBoundaries`` of the runtime that
describe them and the option-change listener that reloads the entry when one
of their options or the semantic asset selection changes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass

from .entry_runtime import HelianthusRuntime, ServiceBoundaries
from .semantic_snapshot_coordinator import semantic_asset_ids

_LOGGER = logging.getLogger(__package__)


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


@dataclass(frozen=True)
class M2MBoundaries:
    """The PV, storage and EVSE consumer boundaries of one setup; each may be ``None``."""

    pv: object | None
    storage: object | None
    evse: object | None


async def _async_setup_m2m_boundary(
    setup: Callable[..., Awaitable[object | None]],
    hass: object,
    entry: object,
    scan_interval: object,
    failure_message: str,
) -> object | None:
    """Create one consumer boundary; a configuration error leaves it ``None``."""
    try:
        return await setup(hass, entry, scan_interval=scan_interval)
    except (OSError, TypeError, ValueError):
        _LOGGER.warning(failure_message, entry.entry_id)
        return None


async def async_setup_m2m_boundaries(
    hass: object, entry: object, scan_interval: object
) -> M2MBoundaries:
    """Create the PV, storage and EVSE consumer boundaries, in that order."""
    from .evse_m2m import async_setup_evse_m2m_boundary
    from .pv_m2m import async_setup_pv_m2m_boundary
    from .storage_m2m import async_setup_storage_m2m_boundary

    pv = await _async_setup_m2m_boundary(
        async_setup_pv_m2m_boundary, hass, entry, scan_interval,
        "Canonical PV consumer configuration failed for entry %s",
    )
    storage = await _async_setup_m2m_boundary(
        async_setup_storage_m2m_boundary, hass, entry, scan_interval,
        "Canonical storage consumer configuration failed for entry %s",
    )
    evse = await _async_setup_m2m_boundary(
        async_setup_evse_m2m_boundary, hass, entry, scan_interval,
        "Canonical EVSE consumer configuration failed for entry %s",
    )
    return M2MBoundaries(pv=pv, storage=storage, evse=evse)


def semantic_asset_selection_signature(entry: object) -> tuple[str, ...] | None:
    """Retain exact validated IDs, or one bounded invalid-selection sentinel."""
    try:
        return semantic_asset_ids(entry)
    except ValueError:
        return None


def _option_reload_signatures(entry: object) -> Iterator[tuple[str, object]]:
    """Yield each reload-signature field with the signature of the entry's current options.

    The signatures are computed one at a time, so a comparison that stops at
    the first difference computes no later signature.
    """
    from .evse_m2m import evse_m2m_option_signature
    from .pv_m2m import pv_m2m_option_signature
    from .storage_m2m import storage_m2m_option_signature

    yield "pv_m2m_option_signature", pv_m2m_option_signature(entry.options)
    yield "storage_m2m_option_signature", storage_m2m_option_signature(entry.options)
    yield "evse_m2m_option_signature", evse_m2m_option_signature(entry.options)
    yield "semantic_asset_selection_signature", semantic_asset_selection_signature(entry)


def _boundary_coordinator(boundary: object | None) -> object | None:
    return None if boundary is None else boundary.coordinator


def service_boundaries(
    entry: object,
    admin: tuple[object, object | None, bool],
    boundaries: M2MBoundaries,
) -> ServiceBoundaries:
    """Return the admin and consumer boundaries with the entry's reload signatures."""
    admin_coordinator, admin_session, admin_services_registered = admin
    signatures = dict(_option_reload_signatures(entry))
    return ServiceBoundaries(
        eebus_admin_coordinator=admin_coordinator,
        eebus_admin_action_broker=admin_coordinator.lifecycle.action_broker,
        eebus_admin_session=admin_session,
        eebus_admin_services_registered=admin_services_registered,
        pv_m2m_coordinator=_boundary_coordinator(boundaries.pv),
        pv_m2m_boundary=boundaries.pv,
        storage_m2m_coordinator=_boundary_coordinator(boundaries.storage),
        storage_m2m_boundary=boundaries.storage,
        evse_m2m_coordinator=_boundary_coordinator(boundaries.evse),
        evse_m2m_boundary=boundaries.evse,
        **signatures,
    )


async def _async_reload_on_option_change(hass: object, entry: object) -> None:
    """Reload a published entry when one of its reload signatures has changed."""
    runtime = getattr(entry, "runtime_data", None)
    if not isinstance(runtime, HelianthusRuntime):
        return
    if all(
        getattr(runtime.services, name) == signature
        for name, signature in _option_reload_signatures(entry)
    ):
        return
    await hass.config_entries.async_reload(entry.entry_id)


def register_option_reload_listener(entry: object) -> None:
    """Add the option-change listener through the entry's unload hook when it has both."""
    if hasattr(entry, "add_update_listener") and hasattr(entry, "async_on_unload"):
        entry.async_on_unload(entry.add_update_listener(_async_reload_on_option_change))
