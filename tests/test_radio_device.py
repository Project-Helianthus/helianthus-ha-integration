"""Tests for HA-11 remote-slot radio coordinator and entities."""

from __future__ import annotations

import asyncio
import sys
import types


def _ensure_homeassistant_stubs() -> None:
    homeassistant_module = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    components_module = sys.modules.setdefault(
        "homeassistant.components",
        types.ModuleType("homeassistant.components"),
    )
    setattr(homeassistant_module, "components", components_module)
    helpers_module = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    setattr(homeassistant_module, "helpers", helpers_module)

    sensor_module = sys.modules.setdefault(
        "homeassistant.components.sensor",
        types.ModuleType("homeassistant.components.sensor"),
    )
    if not hasattr(sensor_module, "SensorEntity"):
        class _SensorEntity:
            pass

        sensor_module.SensorEntity = _SensorEntity
    if not hasattr(sensor_module, "SensorDeviceClass"):
        class _SensorDeviceClass:
            ENERGY = "energy"
            TEMPERATURE = "temperature"
            PRESSURE = "pressure"
            HUMIDITY = "humidity"
            DURATION = "duration"

        sensor_module.SensorDeviceClass = _SensorDeviceClass
    if not hasattr(sensor_module, "SensorStateClass"):
        class _SensorStateClass:
            TOTAL = "total"
            MEASUREMENT = "measurement"
            TOTAL_INCREASING = "total_increasing"

        sensor_module.SensorStateClass = _SensorStateClass

    binary_sensor_module = sys.modules.setdefault(
        "homeassistant.components.binary_sensor",
        types.ModuleType("homeassistant.components.binary_sensor"),
    )
    if not hasattr(binary_sensor_module, "BinarySensorEntity"):
        class _BinarySensorEntity:
            pass

        binary_sensor_module.BinarySensorEntity = _BinarySensorEntity
    if not hasattr(binary_sensor_module, "BinarySensorDeviceClass"):
        class _BinarySensorDeviceClass:
            RUNNING = "running"
            PROBLEM = "problem"
            OPENING = "opening"

        binary_sensor_module.BinarySensorDeviceClass = _BinarySensorDeviceClass

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        class _EntityCategory:
            DIAGNOSTIC = "diagnostic"
            CONFIG = "config"

        const_module.EntityCategory = _EntityCategory
    if not hasattr(const_module, "PERCENTAGE"):
        const_module.PERCENTAGE = "%"
    if not hasattr(const_module, "UnitOfEnergy"):
        class _UnitOfEnergy:
            KILO_WATT_HOUR = "kWh"

        const_module.UnitOfEnergy = _UnitOfEnergy
    if not hasattr(const_module, "UnitOfTemperature"):
        class _UnitOfTemperature:
            CELSIUS = "C"

        const_module.UnitOfTemperature = _UnitOfTemperature

    device_registry_module = sys.modules.setdefault(
        "homeassistant.helpers.device_registry",
        types.ModuleType("homeassistant.helpers.device_registry"),
    )
    if not hasattr(device_registry_module, "DeviceInfo"):
        class _DeviceInfo(dict):
            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                super().__init__(**kwargs)

        device_registry_module.DeviceInfo = _DeviceInfo

    update_coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        types.ModuleType("homeassistant.helpers.update_coordinator"),
    )
    if not hasattr(update_coordinator_module, "CoordinatorEntity"):
        class _CoordinatorEntity:
            def __init__(self, coordinator) -> None:  # noqa: ANN001
                self.coordinator = coordinator

        update_coordinator_module.CoordinatorEntity = _CoordinatorEntity
    if not hasattr(update_coordinator_module, "DataUpdateCoordinator"):
        class _DataUpdateCoordinator:
            def __class_getitem__(cls, _item):  # noqa: ANN206
                return cls

            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                return None

        update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
    if not hasattr(update_coordinator_module, "UpdateFailed"):
        class _UpdateFailed(Exception):
            pass

        update_coordinator_module.UpdateFailed = _UpdateFailed

    setattr(helpers_module, "update_coordinator", update_coordinator_module)

    aiohttp_module = sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
    if not hasattr(aiohttp_module, "WSMsgType"):
        class _WSMsgType:
            TEXT = "TEXT"
            CLOSE = "CLOSE"
            ERROR = "ERROR"

        aiohttp_module.WSMsgType = _WSMsgType
    if not hasattr(aiohttp_module, "ClientSession"):
        class _ClientSession:
            pass

        aiohttp_module.ClientSession = _ClientSession
    if not hasattr(aiohttp_module, "ClientWebSocketResponse"):
        class _ClientWebSocketResponse:
            pass

        aiohttp_module.ClientWebSocketResponse = _ClientWebSocketResponse


_ensure_homeassistant_stubs()

from custom_components.helianthus import binary_sensor as binary_sensor_platform
from custom_components.helianthus import sensor as sensor_platform
from custom_components.helianthus import subscriptions
from custom_components.helianthus.const import DOMAIN
from custom_components.helianthus.coordinator import HelianthusRadioDeviceCoordinator


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data

    def async_set_updated_data(self, payload) -> None:  # noqa: ANN001
        self.data = payload


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def _base_payload(radio_devices: list[dict]) -> dict:
    return {
        "device_coordinator": _FakeCoordinator([]),
        "status_coordinator": _FakeCoordinator({"daemon": {}, "adapter": {}}),
        "semantic_coordinator": _FakeCoordinator({"zones": [], "dhw": None}),
        "energy_coordinator": None,
        "circuit_coordinator": None,
        "radio_coordinator": _FakeCoordinator(
            {"radioDevices": radio_devices, "radioZoneCandidates": {}}
        ),
        "system_coordinator": None,
        "boiler_coordinator": None,
        "boiler_device_id": None,
        "graphql_client": None,
        "daemon_device_id": ("helianthus", "daemon-entry-1"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "regulator_manufacturer": "Vaillant",
        "b524_merge_targets": {},
    }


def test_radio_sensor_entities_cover_room_and_inventory_devices() -> None:
    radio_devices = [
        {
            "group": 0x09,
            "instance": 1,
            "radioBusKey": "g09-i01",
            "deviceClassAddress": 0x15,
            "deviceConnected": True,
            "receptionStrength": 84,
            "roomTemperatureC": 21.5,
            "roomHumidityPct": 45.0,
            "staleCycles": 0,
        },
        {
            "group": 0x0C,
            "instance": 2,
            "radioBusKey": "g0c-i02",
            "deviceClassAddress": 0x99,
            "deviceConnected": False,
            "hardwareIdentifier": 0x2233,
            "remoteControlAddress": 17,
            "zoneAssignment": 0,
            "staleCycles": 0,
        },
    ]
    hass = _FakeHass(_base_payload(radio_devices))
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    radio_entities = [
        entity for entity in entities if isinstance(entity, sensor_platform.HelianthusRadioSensor)
    ]
    unique_ids = {entity._attr_unique_id for entity in radio_entities}
    assert "entry-1-radio-09-01-sensor-roomTemperatureC" in unique_ids
    assert "entry-1-radio-09-01-sensor-roomHumidityPct" in unique_ids
    assert "entry-1-radio-09-01-sensor-receptionStrength" in unique_ids
    assert "entry-1-radio-0c-02-sensor-deviceClassAddress" in unique_ids
    assert "entry-1-radio-0c-02-sensor-hardwareIdentifier" in unique_ids


def test_radio_connected_entity_becomes_unavailable_after_third_stale_cycle() -> None:
    radio_devices = [
        {
            "group": 0x09,
            "instance": 1,
            "radioBusKey": "g09-i01",
            "deviceClassAddress": 0x15,
            "deviceConnected": True,
            "staleCycles": 0,
        }
    ]
    payload = _base_payload(radio_devices)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    connected_entity = next(
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusRadioConnectedBinarySensor)
    )
    assert connected_entity.available is True
    assert connected_entity.is_on is True

    payload["radio_coordinator"].data = {
        "radioDevices": [{**radio_devices[0], "deviceConnected": False, "staleCycles": 2}],
        "radioZoneCandidates": {},
    }
    assert connected_entity.available is True
    assert connected_entity.is_on is False

    payload["radio_coordinator"].data = {
        "radioDevices": [{**radio_devices[0], "deviceConnected": False, "staleCycles": 3}],
        "radioZoneCandidates": {},
    }
    assert connected_entity.available is False


def test_subscription_dispatches_radio_updates_to_radio_coordinator() -> None:
    class _FakeRadioCoordinator:
        def __init__(self) -> None:
            self.seen: list | None = None

        def apply_radio_update(self, payload) -> None:  # noqa: ANN001
            self.seen = payload

    fake = _FakeRadioCoordinator()
    message = {
        "type": "next",
        "payload": {
            "data": {
                "radioDevicesUpdate": [
                    {"group": 0x09, "instance": 1, "deviceConnected": True}
                ]
            }
        },
    }

    asyncio.run(
        subscriptions._handle_message(
            message,
            semantic_coordinator=None,
            energy_coordinator=None,
            boiler_coordinator=None,
            radio_coordinator=fake,
        )
    )

    assert isinstance(fake.seen, list)
    assert fake.seen[0]["group"] == 0x09


def test_radio_zone_candidates_update_on_reassignment() -> None:
    coordinator = object.__new__(HelianthusRadioDeviceCoordinator)
    coordinator._last_by_slot = {}
    coordinator._stale_cycles = {}
    coordinator.async_set_updated_data = lambda payload: setattr(coordinator, "data", payload)
    coordinator.data = {}

    coordinator.apply_radio_update(
        [
            {
                "group": 0x09,
                "instance": 1,
                "deviceConnected": True,
                "deviceClassAddress": 0x15,
                "zoneAssignment": 1,
                "remoteControlAddress": 0,
            }
        ]
    )
    assert 0 in coordinator.data["radioZoneCandidates"]

    coordinator.apply_radio_update(
        [
            {
                "group": 0x09,
                "instance": 1,
                "deviceConnected": True,
                "deviceClassAddress": 0x15,
                "zoneAssignment": 2,
                "remoteControlAddress": 0,
            }
        ]
    )
    assert 1 in coordinator.data["radioZoneCandidates"]
    assert 0 not in coordinator.data["radioZoneCandidates"]


# ---------------------------------------------------------------------------
# ADR-027: B524 function-module merge predicate tests
# ---------------------------------------------------------------------------

_VR71_BUS_DEVICE_ID = ("helianthus", "entry-1-bus-VR_71-26-5904-0100")


def _merge_payload(radio_devices: list[dict], merge_targets: dict[str, tuple[str, str]] | None = None) -> dict:
    """Payload helper with b524_merge_targets support."""
    p = _base_payload(radio_devices)
    if merge_targets is not None:
        p["b524_merge_targets"] = merge_targets
    return p


def test_adr027_merged_group_0c_sensor_entities_suppressed() -> None:
    """ADR-027 CE-positive: group 0x0C with matching bus device -> sensors suppressed."""
    radio_devices = [
        {
            "group": 0x0C,
            "instance": 1,
            "radioBusKey": "g0c-i01",
            "deviceClassAddress": 38,
            "deviceConnected": True,
            "hardwareIdentifier": 0x1704,
            "staleCycles": 0,
        },
    ]
    merge_targets = {"g0c-i01": _VR71_BUS_DEVICE_ID}
    hass = _FakeHass(_merge_payload(radio_devices, merge_targets))
    entry = _FakeEntry("entry-1")
    entities: list = []
    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    radio_sensor_uids = {
        e._attr_unique_id
        for e in entities
        if isinstance(e, sensor_platform.HelianthusRadioSensor)
    }
    # Redundant sensors must NOT be created for merged slots.
    assert "entry-1-radio-0c-01-sensor-deviceClassAddress" not in radio_sensor_uids
    assert "entry-1-radio-0c-01-sensor-hardwareIdentifier" not in radio_sensor_uids


def test_adr027_unmerged_group_0c_sensor_entities_created() -> None:
    """ADR-027 CE-3: group 0x0C with NO matching bus device -> sensors created normally."""
    radio_devices = [
        {
            "group": 0x0C,
            "instance": 2,
            "radioBusKey": "g0c-i02",
            "deviceClassAddress": 0x99,
            "deviceConnected": False,
            "hardwareIdentifier": 0x2233,
            "staleCycles": 0,
        },
    ]
    # No merge targets — bus device at 0x99 doesn't exist.
    hass = _FakeHass(_merge_payload(radio_devices, {}))
    entry = _FakeEntry("entry-1")
    entities: list = []
    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    radio_sensor_uids = {
        e._attr_unique_id
        for e in entities
        if isinstance(e, sensor_platform.HelianthusRadioSensor)
    }
    assert "entry-1-radio-0c-02-sensor-deviceClassAddress" in radio_sensor_uids
    assert "entry-1-radio-0c-02-sensor-hardwareIdentifier" in radio_sensor_uids


def test_adr027_merged_binary_sensor_reparented_to_bus_device() -> None:
    """ADR-027: Device Connected entity for merged group 0x0C -> parented to bus device."""
    radio_devices = [
        {
            "group": 0x0C,
            "instance": 1,
            "radioBusKey": "g0c-i01",
            "deviceClassAddress": 38,
            "deviceConnected": True,
            "staleCycles": 0,
        },
    ]
    merge_targets = {"g0c-i01": _VR71_BUS_DEVICE_ID}
    hass = _FakeHass(_merge_payload(radio_devices, merge_targets))
    entry = _FakeEntry("entry-1")
    entities: list = []
    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    connected = [
        e for e in entities
        if isinstance(e, binary_sensor_platform.HelianthusRadioConnectedBinarySensor)
        and e._group == 0x0C and e._instance == 1
    ]
    assert len(connected) == 1
    entity = connected[0]
    # Must be parented to bus device, not to radio device.
    assert entity._radio_device_id == _VR71_BUS_DEVICE_ID
    # Must be labelled "B524 Connected" when merged.
    assert entity._attr_name == "B524 Connected"


def test_adr027_unmerged_binary_sensor_stays_on_radio_device() -> None:
    """ADR-027 CE-1: group 0x09 -> binary sensor stays on radio device (no merge)."""
    radio_devices = [
        {
            "group": 0x09,
            "instance": 1,
            "radioBusKey": "g09-i01",
            "deviceClassAddress": 0x15,
            "deviceConnected": True,
            "staleCycles": 0,
        },
    ]
    hass = _FakeHass(_merge_payload(radio_devices, {}))
    entry = _FakeEntry("entry-1")
    entities: list = []
    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    connected = [
        e for e in entities
        if isinstance(e, binary_sensor_platform.HelianthusRadioConnectedBinarySensor)
        and e._group == 0x09 and e._instance == 1
    ]
    assert len(connected) == 1
    entity = connected[0]
    # Must stay on radio device, NOT merged into BASV2.
    expected_radio_id = ("helianthus", "entry-1-radio-g09-i01")
    assert entity._radio_device_id == expected_radio_id
    assert entity._attr_name == "Device Connected"


def test_adr027_idempotency_multiple_runs_same_result() -> None:
    """ADR-027 A7: running setup twice produces identical entity sets."""
    radio_devices = [
        {
            "group": 0x0C,
            "instance": 1,
            "radioBusKey": "g0c-i01",
            "deviceClassAddress": 38,
            "deviceConnected": True,
            "hardwareIdentifier": 0x1704,
            "staleCycles": 0,
        },
        {
            "group": 0x09,
            "instance": 1,
            "radioBusKey": "g09-i01",
            "deviceClassAddress": 0x15,
            "deviceConnected": True,
            "receptionStrength": 7,
            "roomTemperatureC": 21.5,
            "roomHumidityPct": 45.0,
            "staleCycles": 0,
        },
    ]
    merge_targets = {"g0c-i01": _VR71_BUS_DEVICE_ID}

    def run_once():
        hass = _FakeHass(_merge_payload(radio_devices, merge_targets))
        entry = _FakeEntry("entry-1")
        sensor_entities: list = []
        binary_entities: list = []
        asyncio.run(sensor_platform.async_setup_entry(hass, entry, sensor_entities.extend))
        asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, binary_entities.extend))
        sensor_uids = sorted(e._attr_unique_id for e in sensor_entities if hasattr(e, "_attr_unique_id"))
        binary_uids = sorted(e._attr_unique_id for e in binary_entities if hasattr(e, "_attr_unique_id"))
        return sensor_uids, binary_uids

    first_sensors, first_binaries = run_once()
    second_sensors, second_binaries = run_once()
    assert first_sensors == second_sensors
    assert first_binaries == second_binaries
