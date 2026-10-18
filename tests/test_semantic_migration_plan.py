"""Synthetic descriptor/registry proposals, without Home Assistant or I/O."""

from copy import deepcopy
from hashlib import sha256
from types import MappingProxyType

import pytest

from custom_components.helianthus.semantic_migration_plan import build_migration_plan


# Independent expectations from the public Z08 table, not the production rows.
CANDIDATES = (
    ("pv", "pv.ac.voltage", "phase", "phase:L1", "pv.ac.voltage.line_to_neutral", "phase", "L1", "V", "voltage"),
    ("pv", "pv.ac.voltage", "phase", "phase:L2", "pv.ac.voltage.line_to_neutral", "phase", "L2", "V", "voltage"),
    ("pv", "pv.ac.voltage", "phase", "phase:L3", "pv.ac.voltage.line_to_neutral", "phase", "L3", "V", "voltage"),
    ("pv", "pv.ac.current", "phase", "phase:L1", "pv.ac.current", "phase", "L1", "A", "current"),
    ("pv", "pv.ac.current", "phase", "phase:L2", "pv.ac.current", "phase", "L2", "A", "current"),
    ("pv", "pv.ac.current", "phase", "phase:L3", "pv.ac.current", "phase", "L3", "A", "current"),
    ("pv", "pv.ac.frequency", "inverter", "inverter", "pv.ac.frequency", "scope", "total", "Hz", "frequency"),
    ("pv", "pv.ac.aggregate_active_power", "inverter", "inverter", "pv.ac.power.active", "scope", "total", "W", "power"),
    ("storage", "storage.state.soc", "pack", "asset:synthetic", "storage.state.soc", "pack", "asset:synthetic", "%", None),
    ("storage", "storage.pack.voltage", "pack", "asset:synthetic", "storage.pack.voltage", "pack", "asset:synthetic", "V", "voltage"),
    ("storage", "storage.temperature.pack", "pack", "asset:synthetic", "storage.temperature.pack", "pack", "asset:synthetic", "°C", "temperature"),
)
WITHHELD = (
    ("pv", "pv.energy.generated", "system", "system", "pv.energy.active_export_total", "scope", "total", "Wh", "energy"),
    ("pv", "pv.temperature.inverter", "inverter", "inverter", "pv.temperature", "sensor_id", "inverter", "°C", "temperature"),
    ("pv", "pv.status.operating", "inverter", "inverter", "pv.operating.state", "scope", "total", None, None),
    ("storage", "storage.pack.current", "pack", "asset:synthetic", "storage.pack.current", "pack", "asset:synthetic", "A", "current"),
    ("storage", "storage.capacity.charge", "pack", "asset:synthetic", "storage.capacity.charge", "pack", "asset:synthetic", "Ah", None),
    ("storage", "storage.capacity.discharge", "pack", "asset:synthetic", "storage.capacity.discharge", "pack", "asset:synthetic", "Ah", None),
    ("storage", "storage.status.operating", "pack", "asset:synthetic", "storage.status.operating", "pack", "asset:synthetic", "1", None),
)


def fixture(row=CANDIDATES[0]):
    family, fact, dim, value, old_fact, old_kind, old_value, unit, device_class = row
    uid = "entry-synthetic-" + family + "-saved-opaque"
    key = {"packId": "helianthus.pack." + family,
           "packVersion": "1.0.0" if family == "pv" else "1.1.0", "factId": fact,
           "dimensions": [{"id": family + ".dimension." + dim, "value": {
               "kind": "text", "text": value, "quantity": None, "boolean": None,
               "symbol": None, "symbols": None, "time": None}}]}
    return {
        "entry_id": "entry-synthetic", "configured_asset": "asset:synthetic", "family": family,
        "persisted_descriptors": [{"fact_id": old_fact,
                                   "dimension": {"kind": old_kind, "value": old_value}, "unique_id": uid}],
        "registry_inventory": [{
            "entry_id": "entry-synthetic", "asset_ref": "asset:synthetic", "unique_id": uid,
            "entity_id": "sensor.synthetic", "device_id": "device:existing",
            "device_identifier": ["helianthus", "entry-synthetic-" + family + "-asset-"
                                  + sha256(b"asset:synthetic").hexdigest()],
            "metadata": {"native_unit": unit, "device_class": device_class, "state_class": "measurement"},
            "settings": {"disabled_by": "user", "hidden_by": "user", "name": "Synthetic",
                         "area_id": "area:synthetic", "labels": ["label:synthetic"],
                         "icon": "mdi:solar-power", "entity_registry_enabled_default": False,
                         "options": {"sensor": {"display_precision": 2}},
                         "device_settings": {"name_by_user": "Synthetic family"}},
            "semantic_key": None}],
        "requests": [{"entry_id": "entry-synthetic", "asset_ref": "asset:synthetic", "key": key}],
    }


def at(data, path):
    for name in path:
        data = data[name]
    return data


def reject(data):
    result = build_migration_plan(**data)
    assert result["proposed"] == []
    assert len(result["rejected"]) == len(data["requests"])
    return result


@pytest.mark.parametrize("row", CANDIDATES)
def test_exact_rows_preserve_saved_identity_device_metadata_and_settings(row):
    data = fixture(row)
    before = deepcopy(data)
    result = build_migration_plan(**data)
    assert result == {"proposed": [{"entry_id": "entry-synthetic", "asset_ref": "asset:synthetic",
                                    "key": before["requests"][0]["key"],
                                    "descriptor": before["persisted_descriptors"][0],
                                    "registry": before["registry_inventory"][0]}], "rejected": []}
    assert data == before


REQUEST = ("requests", 0)
KEY = REQUEST + ("key",)
DIM = KEY + ("dimensions", 0)
OLD = ("persisted_descriptors", 0)
REG = ("registry_inventory", 0)
AXES = (
    (REQUEST, "entry_id", "entry:other"), (REQUEST, "asset_ref", "asset:other"),
    (KEY, "packId", "helianthus.pack.other"), (KEY, "packVersion", "9.0.0"),
    (KEY, "factId", "fact.other"), (KEY, "dimensions", []),
    (DIM, "id", "dimension.other"), (DIM + ("value",), "kind", "boolean"),
    (DIM + ("value",), "text", "other"),
    (OLD, "fact_id", "fact.other"), (OLD + ("dimension",), "kind", "other"),
    (OLD + ("dimension",), "value", "other"), (OLD, "unique_id", "entry-synthetic-pv-other"),
    (REG, "entry_id", "entry:other"), (REG, "asset_ref", "asset:other"),
    (REG, "unique_id", "saved:other"), (REG, "device_identifier", ["helianthus", "device:other"]),
    (REG + ("metadata",), "native_unit", "other"),
    (REG + ("metadata",), "device_class", "other"),
    (REG + ("metadata",), "state_class", "other"),
)


@pytest.mark.parametrize("row", CANDIDATES)
@pytest.mark.parametrize("path,field,value", AXES)
def test_each_identity_and_metadata_axis_is_binding(row, path, field, value):
    data = fixture(row)
    at(data, path)[field] = deepcopy(value)
    reject(data)


@pytest.mark.parametrize("row", CANDIDATES)
@pytest.mark.parametrize("field", ["entry_id", "configured_asset", "family"])
def test_explicit_configuration_is_binding(row, field):
    data = fixture(row)
    data[field] = "other"
    reject(data)


OMIT = [(REQUEST, f) for f in ("entry_id", "asset_ref", "key")]
OMIT += [(KEY, f) for f in ("packId", "packVersion", "factId", "dimensions")]
OMIT += [(DIM, f) for f in ("id", "value")]
OMIT += [(DIM + ("value",), f) for f in ("kind", "text", "quantity", "boolean", "symbol", "symbols", "time")]
OMIT += [(OLD, f) for f in ("fact_id", "dimension", "unique_id")]
OMIT += [(OLD + ("dimension",), f) for f in ("kind", "value")]
OMIT += [(REG, f) for f in ("entry_id", "asset_ref", "unique_id", "entity_id", "device_id",
                           "device_identifier", "metadata", "settings", "semantic_key")]
OMIT += [(REG + ("metadata",), f) for f in ("native_unit", "device_class", "state_class")]
OMIT += [(REG + ("settings",), f) for f in ("disabled_by", "hidden_by")]


@pytest.mark.parametrize("path,field", OMIT)
def test_omitted_required_member_fails_closed(path, field):
    data = fixture()
    del at(data, path)[field]
    reject(data)


@pytest.mark.parametrize("path", [REQUEST, KEY, DIM, DIM + ("value",), OLD, OLD + ("dimension",), REG, REG + ("metadata",)])
def test_unknown_identity_member_fails_closed(path):
    data = fixture()
    at(data, path)["unexpected"] = "synthetic"
    reject(data)


@pytest.mark.parametrize("row", WITHHELD)
def test_all_seven_withheld_rows_rejected_even_when_observations_equal(row):
    data = fixture(row)
    # Observations are intentionally not planner input: equal values cannot
    # change the structural disposition or erase native loss/provenance.
    old_value = new_value = 0
    assert old_value == new_value
    result = reject(data)
    assert result["rejected"] == [{"index": 0, "reason": "withheld row"}]


@pytest.mark.parametrize("row", CANDIDATES)
@pytest.mark.parametrize("saved", ["saved-first", "saved-second"])
def test_saved_id_is_retained_verbatim_and_never_recomputed(row, saved):
    data = fixture(row)
    uid = "entry-synthetic-" + row[0] + "-" + saved
    data["persisted_descriptors"][0]["unique_id"] = uid
    data["registry_inventory"][0]["unique_id"] = uid
    result = build_migration_plan(**data)
    assert not result["rejected"]
    assert result["proposed"][0]["descriptor"]["unique_id"] == uid
    assert result["proposed"][0]["registry"]["unique_id"] == uid


@pytest.mark.parametrize("case", ["descriptor-key", "descriptor-id", "registry-id", "entity-id", "shared", "request"])
def test_collisions_reject_all_participants_including_disabled_records(case):
    data = fixture()
    if case.startswith("descriptor"):
        other = deepcopy(data["persisted_descriptors"][0])
        other["unique_id" if case == "descriptor-key" else "fact_id"] += "-other"
        data["persisted_descriptors"].append(other)
    elif case == "request":
        data["requests"].append(deepcopy(data["requests"][0]))
    else:
        other = deepcopy(data["registry_inventory"][0])
        if case != "registry-id":
            other["unique_id"] += "-other"
        if case != "entity-id":
            other["entity_id"] += "_other"
        if case == "shared":
            other["semantic_key"] = deepcopy(data["requests"][0]["key"])
            other["semantic_key"]["dimensions"] = tuple(other["semantic_key"]["dimensions"])
        data["registry_inventory"].append(other)
    reject(data)


@pytest.mark.parametrize("case", ["no-descriptor", "no-registry", "extra-dimension", "wrong-arm", "foreign-id", "already-shared"])
def test_missing_identity_or_nonexact_shape_withheld(case):
    data = fixture()
    if case == "no-descriptor":
        data["persisted_descriptors"] = []
    elif case == "no-registry":
        data["registry_inventory"] = []
    elif case == "extra-dimension":
        at(data, KEY)["dimensions"].append(deepcopy(at(data, DIM)))
    elif case == "wrong-arm":
        at(data, DIM)["value"]["boolean"] = False
    elif case == "foreign-id":
        for path in (OLD, REG):
            at(data, path)["unique_id"] = "other-entry-pv-saved"
    else:
        at(data, REG)["semantic_key"] = deepcopy(at(data, KEY))
    reject(data)


def test_partial_plan_keeps_valid_sibling_and_is_deterministic():
    data = fixture()
    invalid = deepcopy(data["requests"][0])
    invalid["key"]["factId"] = "pv.energy.generated"
    data["requests"].append(invalid)
    result = build_migration_plan(**data)
    assert len(result["proposed"]) == 1
    assert result["rejected"] == [{"index": 1, "reason": "withheld row"}]
    assert result == build_migration_plan(**data)


def test_result_does_not_alias_inputs_in_either_direction():
    data = fixture()
    before = deepcopy(data)
    result = build_migration_plan(**data)
    proposal = result["proposed"][0]
    proposal["key"]["dimensions"][0]["value"]["text"] = "changed"
    proposal["descriptor"]["dimension"]["value"] = "changed"
    proposal["registry"]["settings"]["options"]["sensor"]["display_precision"] = 8
    proposal["registry"]["device_identifier"][1] = "changed"
    assert data == before
    result = build_migration_plan(**data)
    data["registry_inventory"][0]["settings"]["labels"].append("later")
    data["requests"][0]["key"]["dimensions"].clear()
    assert result["proposed"][0]["registry"] == before["registry_inventory"][0]
    assert result["proposed"][0]["key"] == before["requests"][0]["key"]


def test_normalized_readonly_graphql_key_supported():
    data = fixture()
    expected = deepcopy(data["requests"][0]["key"])

    def freeze(value):
        if isinstance(value, dict):
            return MappingProxyType({k: freeze(v) for k, v in value.items()})
        if isinstance(value, list):
            return tuple(freeze(v) for v in value)
        return value

    data["requests"][0]["key"] = freeze(expected)
    result = build_migration_plan(**data)
    assert not result["rejected"]
    assert result["proposed"][0]["key"]["factId"] == "pv.ac.voltage"
    assert result["proposed"][0]["key"]["dimensions"][0]["value"]["text"] == "phase:L1"


@pytest.mark.parametrize("axis", ["entry_id", "asset_ref"])
def test_detached_nonmatching_registry_target_does_not_capture_pair(axis):
    data = fixture()
    other = deepcopy(data["registry_inventory"][0])
    other[axis] = "other"
    other["unique_id"] = "other-saved"
    other["entity_id"] = "sensor.other"
    other["semantic_key"] = deepcopy(data["requests"][0]["key"])
    data["registry_inventory"].append(other)
    assert len(build_migration_plan(**data)["proposed"]) == 1


@pytest.mark.parametrize("field", ["device_id", "device_identifier"])
def test_device_registry_conflicts_withhold_pair(field):
    data = fixture()
    other = deepcopy(data["registry_inventory"][0])
    other["unique_id"] += "-other"
    other["entity_id"] += "_other"
    other[field] = "device:other" if field == "device_id" else ["helianthus", "device:other"]
    data["registry_inventory"].append(other)
    reject(data)


@pytest.mark.parametrize("axis", [0, 1])
def test_device_identifier_namespace_and_value_are_independent(axis):
    data = fixture()
    data["registry_inventory"][0]["device_identifier"][axis] = "other"
    reject(data)


@pytest.mark.parametrize("disabled,hidden", [(None, None), ("integration", None), (None, "user"), ("user", "integration")])
def test_registry_settings_are_preserved_without_enabling_or_unhiding(disabled, hidden):
    data = fixture()
    settings = data["registry_inventory"][0]["settings"]
    settings.update(disabled_by=disabled, hidden_by=hidden)
    result = build_migration_plan(**data)
    assert not result["rejected"]
    assert result["proposed"][0]["registry"]["settings"] == settings


@pytest.mark.parametrize("row", CANDIDATES)
def test_same_family_alternative_fact_does_not_join_by_position(row):
    data = fixture(row)
    other = next(r for r in CANDIDATES if r[0] == row[0] and r[1] != row[1])
    at(data, KEY)["factId"] = other[1]
    reject(data)


def test_inventory_retains_unrelated_multidimension_shared_reservations():
    data = fixture()
    other = deepcopy(data["registry_inventory"][0])
    other["unique_id"] = "other-shared"
    other["entity_id"] = "sensor.other"
    other["semantic_key"] = deepcopy(data["requests"][0]["key"])
    dim = {"id": "dimension.synthetic", "value": {
        "kind": "boolean", "boolean": False, "text": None, "quantity": None,
        "symbol": None, "symbols": None, "time": None}}
    other["semantic_key"]["dimensions"].append(dim)
    data["registry_inventory"].append(other)
    result = build_migration_plan(**data)
    assert not result["rejected"]
    assert len(result["proposed"]) == 1
