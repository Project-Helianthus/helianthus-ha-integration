"""Types for the existing per-entry mapping; no runtime ownership is transferred."""

from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Callable

    from aiohttp import ClientSession

    from .coordinator import (
        HelianthusAdapterInfoCoordinator,
        HelianthusBoilerCoordinator,
        HelianthusCircuitCoordinator,
        HelianthusCoordinator,
        HelianthusEnergyCoordinator,
        HelianthusFM5Coordinator,
        HelianthusRadioDeviceCoordinator,
        HelianthusScheduleCoordinator,
        HelianthusSemanticCoordinator,
        HelianthusStatusCoordinator,
        HelianthusSystemCoordinator,
    )
    from .device_ids import DeviceIdentifier
    from .eebus_admin_coordinator import EEBusAdminV1Coordinator
    from .eebus_pairing import EEBusActionTerminalBroker
    from .evse_m2m import EVSEM2MBoundary, HelianthusEVSEM2MCoordinator
    from .graphql import GraphQLClient
    from .pv_m2m import HelianthusPVM2MCoordinator, PVM2MBoundary
    from .semantic_snapshot_coordinator import SemanticSnapshotCoordinator
    from .storage_m2m import HelianthusStorageM2MCoordinator, StorageM2MBoundary


class EntryRuntime(TypedDict):
    """Required publication keys and resources added or read conditionally later.

    A required key can contain None. Presence means publication, not accepted
    setup. See docs/entry-runtime-lifecycle.md for failure and unload behavior.
    API-owned source-selection payloads deliberately retain their existing shape.
    """

    device_coordinator: "HelianthusCoordinator"
    status_coordinator: "HelianthusStatusCoordinator"
    semantic_coordinator: "HelianthusSemanticCoordinator"
    energy_coordinator: "HelianthusEnergyCoordinator"
    circuit_coordinator: "HelianthusCircuitCoordinator"
    radio_coordinator: "HelianthusRadioDeviceCoordinator"
    fm5_coordinator: "HelianthusFM5Coordinator"
    system_coordinator: "HelianthusSystemCoordinator"
    boiler_coordinator: "HelianthusBoilerCoordinator"
    schedule_coordinator: "HelianthusScheduleCoordinator"
    adapter_info_coordinator: "HelianthusAdapterInfoCoordinator"
    eebus_admin_coordinator: "EEBusAdminV1Coordinator"
    eebus_admin_action_broker: "EEBusActionTerminalBroker"
    eebus_admin_session: "ClientSession | None"
    eebus_admin_services_registered: bool
    pv_m2m_coordinator: "HelianthusPVM2MCoordinator | None"
    pv_m2m_boundary: "PVM2MBoundary | None"
    pv_m2m_option_signature: tuple[object, ...]
    storage_m2m_coordinator: "HelianthusStorageM2MCoordinator | None"
    storage_m2m_boundary: "StorageM2MBoundary | None"
    storage_m2m_option_signature: tuple[object, ...]
    evse_m2m_coordinator: "HelianthusEVSEM2MCoordinator | None"
    evse_m2m_boundary: "EVSEM2MBoundary | None"
    evse_m2m_option_signature: tuple[object, ...]
    semantic_asset_selection_signature: tuple[str, ...] | None
    graphql_client: "GraphQLClient"
    subscription_task: "Task[None] | None"
    unsub_listeners: "list[Callable[[], None]]"
    daemon_device_id: "DeviceIdentifier"
    adapter_device_id: "DeviceIdentifier"
    boiler_device_id: "DeviceIdentifier | None"
    regulator_device_id: "DeviceIdentifier | None"
    vr71_device_id: "DeviceIdentifier | None"
    regulator_manufacturer: str
    regulator_bus_address: int | None
    admission_repair_code: str | None
    source_selection: dict[str, Any] | None
    boiler_physical_device_id: "DeviceIdentifier | None"
    boiler_via_device_id: "DeviceIdentifier | None"
    boiler_burner_device_id: "DeviceIdentifier"
    boiler_hydraulics_device_id: "DeviceIdentifier"
    zone_parent_device_ids: "dict[str, DeviceIdentifier]"
    radio_device_zone_names: "dict[DeviceIdentifier, str]"
    unresolved_zone_ids: tuple[str, ...]
    b524_merge_targets: "dict[str, DeviceIdentifier]"
    semantic_snapshot_coordinator: NotRequired["SemanticSnapshotCoordinator | None"]
    semantic_snapshot_sensors_setup: NotRequired["Callable[[], None]"]
    # Fallback-only reads; setup currently publishes neither key.
    manufacturer: NotRequired[str]
    solar_device_id: NotRequired["DeviceIdentifier"]
