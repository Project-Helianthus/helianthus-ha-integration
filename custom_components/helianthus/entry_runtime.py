"""The typed runtime of one config entry, published to ``entry.runtime_data``.

``HelianthusRuntime`` is composed of the outputs of the setup phases: the
gateway connection, the domain coordinators, the device identities, the
service boundaries and the started listeners, plus the admission result, the
derived boiler devices, the semantic snapshot fields that are attached after
the platforms, and the ``EntryResources`` that release what the phases
acquired. Entry setup publishes it in one assignment before the platforms are
forwarded; a failed setup releases its resources and removes it again, and
Home Assistant discards it after the entry unloads. The records that must
outlive an unload stay in the domain data instead. See
docs/entry-runtime-lifecycle.md for failure and unload behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Callable

    from aiohttp import ClientSession
    from homeassistant.config_entries import ConfigEntry

    from .device_ids import DeviceIdentifier
    from .eebus_admin_coordinator import EEBusAdminV1Coordinator
    from .eebus_pairing import EEBusActionTerminalBroker
    from .entry_cleanup import EntryResources
    from .entry_connection import GatewayConnection
    from .entry_coordinators import DomainCoordinators
    from .entry_devices import DeviceIdentities
    from .entry_listeners import EntryCoordinatorListeners
    from .evse_m2m import EVSEM2MBoundary, HelianthusEVSEM2MCoordinator
    from .pv_m2m import HelianthusPVM2MCoordinator, PVM2MBoundary
    from .semantic_snapshot_coordinator import SemanticSnapshotCoordinator
    from .storage_m2m import HelianthusStorageM2MCoordinator, StorageM2MBoundary


@dataclass(frozen=True)
class StartedListeners:
    """The listener phase: coordinator listeners, callbacks, subscription and zone parents."""

    listeners: EntryCoordinatorListeners
    unsub_listeners: list[Callable[[], None]]
    subscription_task: Task[None] | None
    zone_parent_device_ids: dict[str, DeviceIdentifier]
    unresolved_zone_ids: tuple[str, ...]
    radio_device_zone_names: dict[DeviceIdentifier, str]


@dataclass(slots=True, frozen=True)
class ServiceBoundaries:
    """The register-services phase: the admin and consumer boundaries and their reload signatures.

    A consumer coordinator and boundary are None when that consumer is not
    configured or its configuration failed. The signatures are those of the
    options the entry was set up with; the option-change listener compares
    them with the current options.
    """

    eebus_admin_coordinator: EEBusAdminV1Coordinator
    eebus_admin_action_broker: EEBusActionTerminalBroker
    eebus_admin_session: ClientSession | None
    eebus_admin_services_registered: bool
    pv_m2m_coordinator: HelianthusPVM2MCoordinator | None
    pv_m2m_boundary: PVM2MBoundary | None
    pv_m2m_option_signature: tuple[object, ...]
    storage_m2m_coordinator: HelianthusStorageM2MCoordinator | None
    storage_m2m_boundary: StorageM2MBoundary | None
    storage_m2m_option_signature: tuple[object, ...]
    evse_m2m_coordinator: HelianthusEVSEM2MCoordinator | None
    evse_m2m_boundary: EVSEM2MBoundary | None
    evse_m2m_option_signature: tuple[object, ...]
    semantic_asset_selection_signature: tuple[str, ...] | None


@dataclass(slots=True)
class HelianthusRuntime:
    """The runtime of one loaded config entry.

    Every field is set at publication; a field can hold None. ``resources``
    holds the release of every resource the setup phases acquired; unload
    releases it. The semantic snapshot fields change once more after the
    platforms are set up:
    ``semantic_snapshot_attached`` becomes True when the snapshot coordinator
    (None when no asset selection is configured) is attached, and a sensor
    setup deferred until then is held in ``semantic_snapshot_sensors_setup``.
    API-owned source-selection payloads deliberately retain their existing shape.
    """

    connection: GatewayConnection
    coordinators: DomainCoordinators
    identities: DeviceIdentities
    services: ServiceBoundaries
    listeners: StartedListeners
    resources: EntryResources
    admission_repair_code: str | None
    source_selection: dict[str, Any] | None
    boiler_physical_device_id: DeviceIdentifier | None
    boiler_via_device_id: DeviceIdentifier | None
    boiler_burner_device_id: DeviceIdentifier
    boiler_hydraulics_device_id: DeviceIdentifier
    semantic_snapshot_attached: bool = False
    semantic_snapshot_coordinator: SemanticSnapshotCoordinator | None = None
    semantic_snapshot_sensors_setup: Callable[[], None] | None = None


HelianthusConfigEntry: TypeAlias = "ConfigEntry[HelianthusRuntime]"
