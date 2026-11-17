"""The register-services phase of config-entry setup.

This module owns the optional isolated eeBUS admin boundary, the PV, storage
and EVSE consumer boundaries, the ``ServiceBoundaries`` of the runtime that
describe them and the option-change listener that reloads the entry when one
of their options or the semantic asset selection changes. Each boundary that
is set up registers its release with the entry's ``EntryResources``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from .entry_runtime import HelianthusRuntime, ServiceBoundaries
from .semantic_snapshot_coordinator import semantic_asset_ids

if TYPE_CHECKING:
    from .entry_cleanup import EntryResources

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


async def _async_release_failed_admin(
    hass: object, entry_id: str, admin_session: object | None, admin_boundary: object | None
) -> None:
    """Release what a failed admin setup acquired before it failed."""
    from .eebus_admin_coordinator import close_admin_session

    if admin_boundary is not None:
        admin_boundary.lifecycle.clear()
        await async_unload_eebus_admin_boundary(
            hass,
            entry_id=entry_id,
            session=admin_boundary.session,
        )
    elif admin_session is not None:
        await close_admin_session(admin_session)


async def async_setup_optional_eebus_admin_service(
    hass: object,
    *,
    entry_id: str,
    origin: str,
    instance_guid: str,
    scan_interval: object,
    resources: EntryResources,
) -> tuple[object, object | None, bool]:
    """Set up the isolated AdminV1 diagnostic boundary without failing entry setup.

    A failure releases what the boundary acquired and leaves an unavailable
    diagnostic coordinator; a cancellation releases it and propagates. The
    entry's resources own the lifecycle and, when the boundary is up, the
    registered services and the session as separate releases, so a failing
    unregistration still closes the session.
    """
    from .eebus_admin_coordinator import (
        EEBusAdminV1Coordinator,
        close_admin_session,
        create_admin_session,
        create_unavailable_eebus_admin_coordinator,
    )
    from .eebus_admin_services import (
        bind_eebus_admin_coordinator,
        unregister_eebus_admin_services,
    )

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
    except Exception:
        _LOGGER.warning("eeBUS AdminV1 setup failed non-fatally for entry %s", entry_id)
        await _async_release_failed_admin(hass, entry_id, admin_session, admin_boundary)
        admin_coordinator = create_unavailable_eebus_admin_coordinator(
            hass,
            entry_id=entry_id,
            interval=scan_interval,
            code="admin_boundary_unavailable",
        )
        resources.own("eeBUS admin lifecycle", admin_coordinator.lifecycle.clear)
        await admin_coordinator.async_refresh()
        return admin_coordinator, None, False
    except BaseException:
        await _async_release_failed_admin(hass, entry_id, admin_session, admin_boundary)
        raise
    resources.own("eeBUS admin session", partial(close_admin_session, admin_session))
    resources.own("eeBUS admin services", partial(
        unregister_eebus_admin_services, hass, entry_id=entry_id
    ))
    resources.own("eeBUS admin lifecycle", admin_coordinator.lifecycle.clear)
    return admin_coordinator, admin_session, True


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
    family: str,
    resources: EntryResources,
) -> object | None:
    """Create one consumer boundary and own its close; a configuration error leaves it ``None``."""
    try:
        boundary = await setup(hass, entry, scan_interval=scan_interval)
    except (OSError, TypeError, ValueError):
        _LOGGER.warning(
            "Canonical %s consumer configuration failed for entry %s", family, entry.entry_id
        )
        return None
    if boundary is not None:
        resources.own(f"{family} consumer boundary", boundary.async_close)
    return boundary


async def async_setup_m2m_boundaries(
    hass: object, entry: object, scan_interval: object, *, resources: EntryResources
) -> M2MBoundaries:
    """Create the PV, storage and EVSE consumer boundaries, in that order."""
    from .evse_m2m import async_setup_evse_m2m_boundary
    from .pv_m2m import async_setup_pv_m2m_boundary
    from .storage_m2m import async_setup_storage_m2m_boundary

    pv = await _async_setup_m2m_boundary(
        async_setup_pv_m2m_boundary, hass, entry, scan_interval, "PV", resources
    )
    storage = await _async_setup_m2m_boundary(
        async_setup_storage_m2m_boundary, hass, entry, scan_interval, "storage", resources
    )
    evse = await _async_setup_m2m_boundary(
        async_setup_evse_m2m_boundary, hass, entry, scan_interval, "EVSE", resources
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


def register_option_reload_listener(entry: object, *, resources: EntryResources) -> None:
    """Add the option-change listener; ``resources`` removes it on rollback and unload.

    A setup retried after a failure would otherwise add a second listener, and
    one option change would then schedule several reloads.
    """
    if hasattr(entry, "add_update_listener"):
        resources.own("option listener", entry.add_update_listener(_async_reload_on_option_change))
