"""Read-only, explicitly mapped consumers of the detached semantic snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import json
from urllib.parse import quote

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


@dataclass(frozen=True)
class SemanticSensorDescriptor:
    pack_id: str
    pack_version: str
    fact_id: str
    dimension_id: str
    unit: str
    display_unit: str | None
    device_class: str | None
    state_class: str | None
    legacy_covered: bool
    minimum: str | None = None
    maximum: str | None = None
    symbols: tuple[str, ...] = ()


# Static rows from the public SemReg pack definitions, with K24 coverage.
# Each dimension accepts any pack-valid text value, yielding one entity per value.
# Decimal numbers are already scaled exactly by the shared decoder. Units below
# are identical-unit presentation mappings, with no float or counter conversion.
DESCRIPTORS = (
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.dc.voltage', 'pv.dimension.input', 'unit.volt', 'V', 'voltage', 'measurement', False, '0e0', '2e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.dc.current', 'pv.dimension.input', 'unit.ampere', 'A', 'current', 'measurement', False, '0e0', '2e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.dc.power', 'pv.dimension.input', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.voltage', 'pv.dimension.phase', 'unit.volt', 'V', 'voltage', 'measurement', True, '0e0', '1e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.current', 'pv.dimension.phase', 'unit.ampere', 'A', 'current', 'measurement', True, '0e0', '2e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.active_power', 'pv.dimension.phase', 'unit.watt', 'W', 'power', 'measurement', False, '-1e7', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.frequency', 'pv.dimension.inverter', 'unit.hertz', 'Hz', 'frequency', 'measurement', True, '0e0', '1e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.power_factor', 'pv.dimension.inverter', 'unit.ratio', None, None, 'measurement', False, '-1e0', '1e0', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.energy.generated', 'pv.dimension.system', 'unit.kilowatt_hour', 'kWh', 'energy', 'total', True, '0e0', '1e12', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.temperature.inverter', 'pv.dimension.inverter', 'unit.celsius', '°C', 'temperature', 'measurement', True, '-5e1', '2e2', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.limit.active_power', 'pv.dimension.inverter', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.limit.export_power', 'pv.dimension.system', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.status.operating', 'pv.dimension.inverter', '', None, None, None, True, None, None, ('generating', 'idle', 'standby')),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.status.derating', 'pv.dimension.inverter', '', None, None, None, False, None, None, ('active', 'clear')),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.status.fault', 'pv.dimension.inverter', '', None, None, None, False, None, None, ('clear', 'present')),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.dc.aggregate_voltage', 'pv.dimension.inverter', 'unit.volt', 'V', 'voltage', 'measurement', False, '0e0', '2e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.dc.aggregate_current', 'pv.dimension.inverter', 'unit.ampere', 'A', 'current', 'measurement', False, '0e0', '2e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.dc.aggregate_power', 'pv.dimension.inverter', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.aggregate_voltage', 'pv.dimension.inverter', 'unit.volt', 'V', 'voltage', 'measurement', False, '0e0', '1e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.aggregate_current', 'pv.dimension.inverter', 'unit.ampere', 'A', 'current', 'measurement', False, '0e0', '2e3', ()),
    SemanticSensorDescriptor('helianthus.pack.pv', '1.0.0', 'pv.ac.aggregate_active_power', 'pv.dimension.inverter', 'unit.watt', 'W', 'power', 'measurement', True, '-1e7', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.state.soc', 'storage.dimension.pack', 'unit.percent', '%', None, 'measurement', True, '0e0', '1e2', ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.state.soh', 'storage.dimension.pack', 'unit.percent', '%', None, 'measurement', False, '0e0', '1e2', ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.pack.voltage', 'storage.dimension.pack', 'unit.volt', 'V', 'voltage', 'measurement', True, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.pack.current', 'storage.dimension.pack', 'unit.ampere', 'A', 'current', 'measurement', True, None, None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.pack.power', 'storage.dimension.pack', 'unit.watt', 'W', 'power', 'measurement', False, None, None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.energy.charged', 'storage.dimension.pack', 'unit.kilowatt_hour', 'kWh', 'energy', 'total', False, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.energy.discharged', 'storage.dimension.pack', 'unit.kilowatt_hour', 'kWh', 'energy', 'total', False, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.capacity.charge', 'storage.dimension.pack', 'unit.ampere_hour', 'Ah', None, 'measurement', True, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.capacity.discharge', 'storage.dimension.pack', 'unit.ampere_hour', 'Ah', None, 'measurement', True, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.temperature.pack', 'storage.dimension.pack', 'unit.celsius', '°C', 'temperature', 'measurement', True, None, None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.temperature.cell', 'storage.dimension.cell', 'unit.celsius', '°C', 'temperature', 'measurement', False, None, None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.cell.voltage_minimum', 'storage.dimension.cell', 'unit.volt', 'V', 'voltage', 'measurement', False, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.cell.voltage_maximum', 'storage.dimension.cell', 'unit.volt', 'V', 'voltage', 'measurement', False, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.limit.charge_power', 'storage.dimension.interface', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.limit.discharge_power', 'storage.dimension.interface', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', None, ()),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.status.operating', 'storage.dimension.pack', '', None, None, None, True, None, None, ('active', 'idle', 'standby')),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.status.alarm', 'storage.dimension.pack', '', None, None, None, False, None, None, ('clear', 'present')),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.status.protection', 'storage.dimension.pack', '', None, None, None, False, None, None, ('active', 'clear')),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.status.warning', 'storage.dimension.pack', '', None, None, None, False, None, None, ('clear', 'present')),
    SemanticSensorDescriptor('helianthus.pack.storage', '1.1.0', 'storage.status.interlock', 'storage.dimension.interface', '', None, None, None, False, None, None, ('active', 'clear')),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.ac.active_power', 'infrastructure.dimension.phase', 'unit.watt', 'W', 'power', 'measurement', False, '-1e7', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.ac.apparent_power', 'infrastructure.dimension.phase', 'unit.volt_ampere', 'VA', 'apparent_power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.ac.current', 'infrastructure.dimension.phase', 'unit.ampere', 'A', 'current', 'measurement', False, '0e0', '1e3', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.ac.frequency', 'infrastructure.dimension.grid_connection', 'unit.hertz', 'Hz', 'frequency', 'measurement', False, '0e0', '1e3', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.ac.power_factor', 'infrastructure.dimension.grid_connection', 'unit.ratio', None, None, 'measurement', False, '-1e0', '1e0', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.ac.reactive_power', 'infrastructure.dimension.phase', 'unit.volt_ampere_reactive', 'var', 'reactive_power', 'measurement', False, '-1e7', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.ac.voltage', 'infrastructure.dimension.phase', 'unit.volt', 'V', 'voltage', 'measurement', False, '0e0', '1e3', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.energy.export', 'infrastructure.dimension.meter', 'unit.kilowatt_hour', 'kWh', 'energy', 'total', False, '0e0', '1e12', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.energy.import', 'infrastructure.dimension.meter', 'unit.kilowatt_hour', 'kWh', 'energy', 'total', False, '0e0', '1e12', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.power.apparent', 'infrastructure.dimension.grid_connection', 'unit.volt_ampere', 'VA', 'apparent_power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.power.export_active', 'infrastructure.dimension.grid_connection', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.power.export_reactive', 'infrastructure.dimension.grid_connection', 'unit.volt_ampere_reactive', 'var', 'reactive_power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.power.import_active', 'infrastructure.dimension.grid_connection', 'unit.watt', 'W', 'power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.power.import_reactive', 'infrastructure.dimension.grid_connection', 'unit.volt_ampere_reactive', 'var', 'reactive_power', 'measurement', False, '0e0', '1e7', ()),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.status.breaker', 'infrastructure.dimension.circuit', '', None, None, None, False, None, None, ('closed', 'open')),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.status.connection', 'infrastructure.dimension.grid_connection', '', None, None, None, False, None, None, ('connected', 'disconnected')),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.status.fault', 'infrastructure.dimension.site', '', None, None, None, False, None, None, ('clear', 'present')),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.status.interlock', 'infrastructure.dimension.circuit', '', None, None, None, False, None, None, ('active', 'clear')),
    SemanticSensorDescriptor('helianthus.pack.infrastructure', '1.0.0', 'infrastructure.status.readiness', 'infrastructure.dimension.grid_connection', '', None, None, None, False, None, None, ('not_ready', 'ready')),
)


def _identity_value(value):
    """Encode every typed arm without losing false, zero, or Decimal precision."""
    if isinstance(value, Decimal):
        sign, digits, exponent = value.as_tuple()
        return ["decimal", sign, list(digits), exponent]
    if isinstance(value, Mapping):
        return {key: _identity_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_identity_value(item) for item in value]
    return value


def semantic_unique_id(entry_id, asset_id, key):
    """Join escaped opaque components and canonical complete typed dimensions."""
    dimensions = sorted(key["dimensions"], key=lambda dimension: dimension["id"])
    components = [entry_id, asset_id, key["packId"], key["packVersion"], key["factId"]]
    for dimension in dimensions:
        components.extend((dimension["id"], json.dumps(
            _identity_value(dimension["value"]), sort_keys=True,
            ensure_ascii=False, separators=(",", ":"))))
    return "|".join(quote(component, safe="") for component in components)


def _descriptor(key):
    for descriptor in DESCRIPTORS:
        if ((key["packId"], key["packVersion"], key["factId"])
                == (descriptor.pack_id, descriptor.pack_version, descriptor.fact_id)
                and len(key["dimensions"]) == 1
                and key["dimensions"][0]["id"] == descriptor.dimension_id
                and key["dimensions"][0]["value"]["kind"] == "text"):
            return descriptor
    return None


def _source_admitted(snapshot, candidate):
    """Retain the existing native qualification boundary without native decoding."""
    origin = candidate["origin"]
    if (origin["kind"] != "native_observation" or not origin["evidence"]
            or not candidate["evidence"] or candidate["quality"]["assertion"] != "observed"):
        return False
    bindings = [b for b in snapshot["bindings"] if b["bindingId"] == candidate["bindingId"]]
    if len(bindings) != 1:
        return False
    binding = bindings[0]
    if (binding["state"] != "current"
            or candidate["sourceEpochId"] != binding["sourceEpochId"]
            or candidate["driverGeneration"] != binding["driverGeneration"]
            or origin["bindingId"] != binding["bindingId"]
            or origin["sourceId"] != binding["sourceId"]
            or origin["sourceEpochId"] != binding["sourceEpochId"]):
        return False
    sources = [s for s in snapshot["sources"]
               if (s["sourceId"], s["sourceEpochId"])
               == (binding["sourceId"], binding["sourceEpochId"])]
    links = [link for link in snapshot["identityLinks"]
             if link["bindingId"] == binding["bindingId"]]
    return (len(sources) == 1 and sources[0]["state"] == "current"
            and len(links) == 1 and links[0]["state"] == "qualified" and bool(links[0]["basis"]))


class SemanticSnapshotSensor(CoordinatorEntity, SensorEntity):
    """A read-only view; admission never changes the entity's semantic identity."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry_id, asset_id, key, descriptor):
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._asset_id = asset_id
        self._key = key
        self.descriptor = descriptor
        self._attr_unique_id = semantic_unique_id(entry_id, asset_id, key)
        self._attr_name = descriptor.fact_id
        self._attr_native_unit_of_measurement = descriptor.display_unit
        self._attr_device_class = descriptor.device_class
        self._attr_state_class = descriptor.state_class

    def _reading(self):
        coordinator = self.coordinator
        if not coordinator.last_update_success or coordinator.supported is not True:
            return None
        for asset in (coordinator.data or {}).get("assets", ()):
            if asset["assetId"] != self._asset_id:
                continue
            if asset["unavailableReason"] or asset["snapshot"] is None:
                return None
            # An exact projection is representation evidence, never qualification.
            dispositions = [d for d in asset["projection"]["dispositions"]
                            if d["kind"] == "fact" and d["itemId"] == self._key["factId"]]
            if (len(dispositions) != 1 or dispositions[0]["outcome"] != "exact"
                    or dispositions[0]["loss"] or self._key not in dispositions[0]["sourceKeys"]):
                return None
            facts = [f for f in asset["snapshot"]["facts"] if f["key"] == self._key]
            if len(facts) != 1 or facts[0]["conflicts"] or len(facts[0]["candidates"]) != 1:
                return None
            candidate = facts[0]["candidates"][0]
            if not _source_admitted(asset["snapshot"], candidate):
                return None
            quality = candidate["quality"]
            if (quality["qualification"] != "qualified" or quality["promotion"] != "promoted"
                    or quality["validity"] != "good" or quality["availability"] != "available"):
                return None
            evaluations = [e for e in asset["evaluation"]["facts"]
                           if (e["candidateId"], e["candidateRevision"])
                           == (candidate["candidateId"], candidate["revision"])]
            if (len(evaluations) != 1 or evaluations[0]["freshness"] != "fresh"
                    or evaluations[0]["effectiveAvailability"] != "available"):
                return None
            value = candidate["value"]
            if value is None:
                return None
            descriptor = self.descriptor
            if descriptor.unit:
                if value["kind"] != "quantity" or value["quantity"]["unit"] != descriptor.unit:
                    return None
                number = value["quantity"]["number"]
                if (not isinstance(number, Decimal) or not number.is_finite()
                        or (descriptor.minimum is not None and number < Decimal(descriptor.minimum))
                        or (descriptor.maximum is not None and number > Decimal(descriptor.maximum))):
                    return None
                return number
            if value["kind"] == "symbol":
                symbol = value["symbol"]
                if (symbol["known"] and symbol["namespace"] == descriptor.fact_id
                        and symbol["token"] in descriptor.symbols):
                    return symbol["token"]
            return None
        return None

    @property
    def available(self):
        return self._reading() is not None

    @property
    def native_value(self):
        return self._reading()


def setup_semantic_snapshot_sensors(data, entry_id, async_add_entities):
    """Discover supported keys once, retaining entities through missing/lost reads."""
    coordinator = data.get("semantic_snapshot_coordinator")
    if coordinator is None:
        return []
    known = set()

    def discover():
        entities = []
        if coordinator.supported is not True or not coordinator.last_update_success:
            return entities
        for asset in (coordinator.data or {}).get("assets", ()):
            if asset["snapshot"] is None:
                continue
            for fact in asset["snapshot"]["facts"]:
                key = fact["key"]
                descriptor = _descriptor(key)
                if descriptor is None or descriptor.legacy_covered:
                    continue
                unique_id = semantic_unique_id(entry_id, asset["assetId"], key)
                if unique_id in known:
                    continue
                known.add(unique_id)
                entities.append(SemanticSnapshotSensor(
                    coordinator, entry_id, asset["assetId"], key, descriptor))
        return entities

    initial = discover()

    def add_discovered():
        entities = discover()
        if entities:
            async_add_entities(entities)

    data.setdefault("unsub_listeners", []).append(coordinator.async_add_listener(add_discovered))
    return initial
