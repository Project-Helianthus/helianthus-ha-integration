"""Detached descriptor proposals; no ownership transfer or measurement admission."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import re


# The seven conditional rows of the public legacy migration contract.
# family, semantic fact, dimension, old fact, old dimension, unit, device class
_ROWS = (
    ("pv", "pv.ac.voltage", "phase", "pv.ac.voltage.line_to_neutral", "phase", "V", "voltage"),
    ("pv", "pv.ac.current", "phase", "pv.ac.current", "phase", "A", "current"),
    ("pv", "pv.ac.frequency", "inverter", "pv.ac.frequency", "scope", "Hz", "frequency"),
    ("pv", "pv.ac.aggregate_active_power", "inverter", "pv.ac.power.active", "scope", "W", "power"),
    ("storage", "storage.state.soc", "pack", "storage.state.soc", "pack", "%", None),
    ("storage", "storage.pack.voltage", "pack", "storage.pack.voltage", "pack", "V", "voltage"),
    ("storage", "storage.temperature.pack", "pack", "storage.temperature.pack", "pack", "°C", "temperature"),
)
_WITHHELD = frozenset((
    "pv.energy.generated", "pv.temperature.inverter", "pv.status.operating",
    "storage.pack.current", "storage.capacity.charge", "storage.capacity.discharge",
    "storage.status.operating",
))


def _fields(value, names):
    if not isinstance(value, Mapping) or set(value) != set(names.split()):
        raise ValueError("missing or unexpected fields")
    return value


def _text(value):
    if not isinstance(value, str) or not value:
        raise ValueError("expected nonempty text")
    return value


def _detach(value):
    if isinstance(value, Mapping):
        return {k: _detach(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_detach(v) for v in value)
    if isinstance(value, list):
        return [_detach(v) for v in value]
    return deepcopy(value)


def _key(value):
    key = _fields(value, "packId packVersion factId dimensions")
    for field in ("packId", "packVersion", "factId"):
        _text(key[field])
    dims = key["dimensions"]
    if not isinstance(dims, (list, tuple)) or len(dims) != 1:
        raise ValueError("expected exactly one dimension")
    dim = _fields(dims[0], "id value")
    _text(dim["id"])
    typed = _fields(dim["value"], "kind quantity boolean text symbol symbols time")
    if (typed["kind"] != "text"
            or any(typed[arm] is not None for arm in ("quantity", "boolean", "symbol", "symbols", "time"))):
        raise ValueError("expected text dimension")
    _text(typed["text"])
    return key


def _old_key(descriptor):
    _fields(descriptor, "fact_id dimension unique_id")
    dim = _fields(descriptor["dimension"], "kind value")
    return (_text(descriptor["fact_id"]), _text(dim["kind"]), _text(dim["value"]))


def _identity(value):
    if isinstance(value, Mapping):
        return tuple((k, _identity(v)) for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_identity(v) for v in value)
    return value


def _symbol(value):
    symbol = _fields(value, "namespace token known")
    _text(symbol["namespace"])
    if not isinstance(symbol["token"], str) or type(symbol["known"]) is not bool:
        raise ValueError("invalid reservation symbol")


def _integer_string(value, low, high):
    if (not isinstance(value, str) or len(value) > 20
            or not re.fullmatch(r"0|-?[1-9][0-9]*", value)
            or not low <= int(value) <= high):
        raise ValueError("invalid reservation integer string")


def _reservation_value(typed):
    typed = _fields(typed, "kind quantity boolean text symbol symbols time")
    arms = ("quantity", "boolean", "text", "symbol", "symbols", "time")
    kind = typed["kind"]
    if kind not in arms or [arm for arm in arms if typed[arm] is not None] != [kind]:
        raise ValueError("invalid reservation typed value")
    value = typed[kind]
    if kind == "text":
        _text(value)
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("invalid reservation boolean")
    elif kind == "quantity":
        quantity = _fields(value, "number unit")
        if not isinstance(quantity["number"], Decimal) or not quantity["number"].is_finite():
            raise ValueError("invalid reservation quantity")
        _text(quantity["unit"])
    elif kind == "symbol":
        _symbol(value)
    elif kind == "symbols":
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 64:
            raise ValueError("invalid reservation symbols")
        for symbol in value:
            _symbol(symbol)
    else:
        time = _fields(value, "unixNanoseconds clockId uncertaintyNs")
        _integer_string(time["unixNanoseconds"], -(2**63), 2**63 - 1)
        _text(time["clockId"])
        _integer_string(time["uncertaintyNs"], 0, 2**64 - 1)


def _reservation_key(value):
    # Other shared entities may reserve keys outside the candidate row shapes.
    # Validate the identity envelope without interpreting their measurements.
    key = _fields(value, "packId packVersion factId dimensions")
    for field in ("packId", "packVersion", "factId"):
        _text(key[field])
    if not isinstance(key["dimensions"], (list, tuple)):
        raise ValueError("invalid reservation dimensions")
    ids = []
    for dim in key["dimensions"]:
        _fields(dim, "id value")
        ids.append(_text(dim["id"]))
        _reservation_value(dim["value"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate reservation dimension")


def _inventory(records):
    for record in records:
        _fields(record, "entry_id asset_ref unique_id entity_id device_id device_identifier metadata settings semantic_key")
        for field in ("entry_id", "asset_ref", "unique_id", "entity_id", "device_id"):
            _text(record[field])
        identifier = record["device_identifier"]
        if (not isinstance(identifier, (list, tuple)) or len(identifier) != 2
                or not all(isinstance(v, str) and v for v in identifier)):
            raise ValueError("invalid device identifier")
        _fields(record["metadata"], "native_unit device_class state_class")
        settings = record["settings"]
        if not isinstance(settings, Mapping) or not {"disabled_by", "hidden_by"} <= set(settings):
            raise ValueError("missing registry settings")
        if record["semantic_key"] is not None:
            _reservation_key(record["semantic_key"])


def _expected(family, asset_ref, key):
    if (key["packId"] != "helianthus.pack." + family
            or key["packVersion"] != {"pv": "1.0.0", "storage": "1.1.0"}[family]):
        raise ValueError("pack mismatch")
    if key["factId"] in _WITHHELD:
        raise ValueError("withheld row")
    row = next((r for r in _ROWS if r[:2] == (family, key["factId"])), None)
    if row is None:
        raise ValueError("outside candidate rows")
    _, _, dimension, old_fact, old_kind, unit, device_class = row
    dim = key["dimensions"][0]
    if dim["id"] != family + ".dimension." + dimension:
        raise ValueError("dimension mismatch")
    text = dim["value"]["text"]
    if dimension == "phase":
        if text not in {"phase:L1", "phase:L2", "phase:L3"}:
            raise ValueError("unmapped phase")
        old_value = text.removeprefix("phase:")
    else:
        if text != (asset_ref if family == "storage" else "inverter"):
            raise ValueError("unmapped singleton")
        old_value = asset_ref if family == "storage" else "total"
    return (old_fact, old_kind, old_value), {
        "native_unit": unit, "device_class": device_class, "state_class": "measurement",
    }


def build_migration_plan(*, entry_id: str, configured_asset: str, family: str,
                         persisted_descriptors: Sequence[Mapping],
                         registry_inventory: Sequence[Mapping],
                         requests: Iterable[Mapping]) -> dict:
    """Return detached proposals/rejections in request order; never write state.

    Requests must be iterable (None is outside the contract). They use the
    normalized GraphQL FactKey and explicit entry/asset context.
    Inventory is a complete caller-supplied reservation view, including disabled
    records and existing shared targets. Malformed inventory rejects all requests.
    Each accepted proposal retains the entire old registry snapshot and descriptor.
    """
    proposed, rejected = [], []
    try:
        _text(entry_id)
        _text(configured_asset)
        if family not in {"pv", "storage"}:
            raise ValueError("unsupported family")
        for descriptor in persisted_descriptors:
            _old_key(descriptor)
            _text(descriptor["unique_id"])
        _inventory(registry_inventory)
    except (ValueError, TypeError):
        return {"proposed": [], "rejected": [
            {"index": i, "reason": "invalid inventory or configuration"}
            for i, _ in enumerate(requests)
        ]}

    staged = []
    for index, request in enumerate(requests):
        try:
            _fields(request, "entry_id asset_ref key")
            if request["entry_id"] != entry_id or request["asset_ref"] != configured_asset:
                raise ValueError("entry or asset mismatch")
            key = _key(request["key"])
            old_key, metadata = _expected(family, configured_asset, key)
            matches = [d for d in persisted_descriptors if _old_key(d) == old_key]
            if len(matches) != 1:
                raise ValueError("missing or ambiguous saved descriptor")
            descriptor = matches[0]
            uid = descriptor["unique_id"]
            if not uid.startswith(entry_id + "-" + family + "-"):
                raise ValueError("saved ID entry mismatch")
            if sum(d["unique_id"] == uid for d in persisted_descriptors) != 1:
                raise ValueError("saved ID collision")
            records = [r for r in registry_inventory if r["unique_id"] == uid]
            if len(records) != 1:
                raise ValueError("missing or colliding registry ID")
            record = records[0]
            identifier = ("helianthus", entry_id + "-" + family + "-asset-"
                          + sha256(configured_asset.encode()).hexdigest())
            if (record["entry_id"] != entry_id or record["asset_ref"] != configured_asset
                    or tuple(record["device_identifier"]) != identifier
                    or record["metadata"] != metadata or record["semantic_key"] is not None):
                raise ValueError("registry identity or metadata mismatch")
            if sum(r["entity_id"] == record["entity_id"] for r in registry_inventory) != 1:
                raise ValueError("entity ID collision")
            if any((r["device_id"] == record["device_id"]
                    and tuple(r["device_identifier"]) != identifier)
                   or (tuple(r["device_identifier"]) == identifier
                       and r["device_id"] != record["device_id"])
                   for r in registry_inventory):
                raise ValueError("device registry conflict")
            if any(r["entry_id"] == entry_id and r["asset_ref"] == configured_asset
                   and r["semantic_key"] is not None
                   and _identity(r["semantic_key"]) == _identity(key) for r in registry_inventory):
                raise ValueError("shared target collision")
            staged.append((index, {"entry_id": entry_id, "asset_ref": configured_asset,
                                   "key": key, "descriptor": descriptor, "registry": record}))
        except (ValueError, TypeError) as exc:
            rejected.append({"index": index, "reason": str(exc)})

    # Reject every participant in a collision, never select a winner by order.
    for index, proposal in staged:
        if any(other_index != index and (
                _identity(other["key"]) == _identity(proposal["key"])
                or other["descriptor"]["unique_id"] == proposal["descriptor"]["unique_id"])
               for other_index, other in staged):
            rejected.append({"index": index, "reason": "proposal collision"})
        else:
            proposed.append(proposal)
    return _detach({"proposed": proposed, "rejected": sorted(rejected, key=lambda r: r["index"])})
