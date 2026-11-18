"""Device-scoped entities publish the English name of their descriptor."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "helianthus"
GENERATOR = ROOT / "scripts" / "generate_entity_translations.py"

ADOPTED = (
    "sensor.py",
    "binary_sensor.py",
    "number.py",
    "select.py",
    "switch.py",
    "text.py",
    "date.py",
    "fan.py",
    "valve.py",
)
DEFERRED = (
    "HelianthusPVM2MSensor",
    "HelianthusStorageM2MSensor",
    "HelianthusEVSEM2MSensor",
)
# platform, translation key, English name.
# The platform case fails if the descriptor is filed under another platform.
# The key case fails if the descriptor key or explicit translation key changes.
# The name case fails if the descriptor label changes.
RECORDED = (
    ("sensor", "outdoor_temperature", "Outdoor Temperature"),
    ("sensor", "flow_temperature", "Flow Temperature"),
    ("sensor", "system_flow_temperature", "System Flow Temperature"),
    ("sensor", "return_temperature_c", "Return Temperature"),
    ("sensor", "circuit_pump_operating_time_total", "Pump Hours"),
    ("sensor", "pump_operating_time", "Pump Hours"),
    ("sensor", "pump_hours", "Pump Hours"),
    ("sensor", "reception_strength", "Signal Quality"),
    ("sensor", "collector_temperature_c", "Collector Temperature"),
    ("sensor", "temperature_c", "Adapter Temperature"),
    ("sensor", "gas_dhw", "Gas DHW"),
    ("sensor", "electric_climate", "Electric CLIMATE"),
    ("sensor", "ebus_address", "eBUS Address"),
    ("sensor", "eebus_admin_available", "eeBUS Admin Available"),
    ("sensor", "hours_till_service", "Hours Till Service"),
    ("binary_sensor", "schedule", "Daily Schedule Active"),
    ("binary_sensor", "flame_active", "Burner Flame Active"),
    ("binary_sensor", "central_heating_pump_active", "Hydraulics CH Pump"),
    ("binary_sensor", "boiler_central_heating_pump", "Central Heating Pump Active"),
    ("binary_sensor", "pump_active", "Solar Pump Active"),
    ("binary_sensor", "circuit_pump_active", "Pump Active"),
    ("binary_sensor", "device_connected", "Device Connected"),
    ("binary_sensor", "b524_connected", "B524 Connected"),
    ("binary_sensor", "zone_valve", "Valve"),
    ("number", "circuit_heating_curve_factor", "Heating Curve"),
    ("number", "maximum_dhw_flow_temperature_setpoint", "DHW Max Flow Setpoint"),
    ("text", "installer_phone_number", "Installer Phone"),
    ("text", "boiler_installer_phone", "Boiler Installer Phone"),
    ("select", "room_temperature_control", "Room Temperature Control"),
    ("switch", "cooling_enabled", "Cooling Enabled"),
    ("switch", "solar_switch", "{label}"),
    ("date", "maintenance_date", "Maintenance Date"),
    ("fan", "burner", "Burner"),
    ("fan", "pump", "Pump"),
    ("fan", "boiler_pump", "{pump_name}"),
    ("valve", "diverter_valve", "Diverter Valve"),
    ("valve", "mixing_valve", "Mixing Valve"),
    ("valve", "zone_valve", "Valve"),
)


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=path.name)


def _classes(path: Path) -> list[ast.ClassDef]:
    return [node for node in _module(path).body if isinstance(node, ast.ClassDef)]


def _has_entity_name(node: ast.ClassDef) -> bool:
    for child in node.body:
        if not isinstance(child, ast.Assign):
            continue
        for target in child.targets:
            if isinstance(target, ast.Name) and target.id == "_attr_has_entity_name":
                return True
    return False


def _assigns_translation_key(node: ast.ClassDef) -> bool:
    for child in ast.walk(node):
        targets = _assignment_targets(child)
        for target in targets:
            if _target_name(target) == "_attr_translation_key":
                return True
    return False


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign) and node.target is not None:
        return [node.target]
    return []


def _target_name(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Tuple):
        for element in target.elts:
            if _target_name(element) == "_attr_translation_key":
                return "_attr_translation_key"
    return None


def _generator():
    spec = importlib.util.spec_from_file_location("generate_entity_translations", GENERATOR)
    if spec is None or spec.loader is None:
        raise AssertionError("entity translation generator is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entity_section() -> dict:
    return _generator().entity_section()


def test_adopted_entity_classes_set_translation_keys() -> None:
    missing = []
    for filename in ADOPTED:
        for node in _classes(COMPONENT / filename):
            if not _has_entity_name(node) or node.name in DEFERRED:
                continue
            if not _assigns_translation_key(node):
                missing.append(node.name)
    assert missing == []


def test_rekey_entities_keep_current_names_without_translation_keys() -> None:
    sensor_classes = {node.name: node for node in _classes(COMPONENT / "sensor.py")}
    for name in DEFERRED:
        assert not _assigns_translation_key(sensor_classes[name])
    semantic = {
        node.name: node
        for node in _classes(COMPONENT / "semantic_snapshot_sensors.py")
    }
    assert _has_entity_name(semantic["SemanticSnapshotSensor"])
    assert not _assigns_translation_key(semantic["SemanticSnapshotSensor"])


def test_recorded_english_names_match_descriptor_translations() -> None:
    section = _entity_section()
    for platform, key, name in RECORDED:
        assert section[platform][key] == {"name": name}


def test_recorded_identity_fields_are_independent() -> None:
    """Each recorded field is part of the identity the generator publishes."""
    section = _entity_section()
    assert "outdoor_temperature" not in section["date"]
    assert "outdoor_temperature_renamed" not in section["sensor"]
    assert section["sensor"]["outdoor_temperature"]["name"] != "Indoor Temperature"
    pump_hours = [
        key for key, info in section["sensor"].items() if info["name"] == "Pump Hours"
    ]
    assert "circuit_pump_operating_time_total" in pump_hours
    assert "pump_operating_time" in pump_hours
    assert "pump_hours" in pump_hours


def test_strings_entity_section_matches_the_generator() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    assert strings["entity"] == _entity_section()


def test_english_file_matches_the_generator() -> None:
    module = _generator()
    text = (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    assert text == module.english_text()
    catalog = json.loads(text)
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    assert catalog["config"] == strings["config"]
    assert catalog["options"] == strings["options"]
    assert catalog["entity"] == strings["entity"]


def test_static_descriptor_keys_used_by_sensors_are_published() -> None:
    section = _entity_section()["sensor"]
    keys = _call_keys(COMPONENT / "sensor.py", "_static_pair")
    assert keys
    for key in keys:
        assert key in section


def _call_keys(path: Path, function: str) -> list[str]:
    keys = []
    for node in ast.walk(_module(path)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != function or not node.args:
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            keys.append(value.value)
    return keys


def _function(name: str) -> ast.FunctionDef:
    for node in _module(COMPONENT / "sensor_setup.py").body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def _string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_labels(node: ast.AST) -> list[tuple[str, str]]:
    pairs = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Dict):
            continue
        data = {}
        for key_node, value_node in zip(child.keys, child.values):
            key = _string(key_node)
            value = _string(value_node)
            if key is not None and value is not None:
                data[key] = value
        if "key" in data and "label" in data:
            pairs.append((data["key"], data["label"]))
    return pairs


def _tuple_labels(node: ast.AST, width: int) -> list[tuple[str, str]]:
    pairs = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Tuple) or len(child.elts) != width:
            continue
        key = _string(child.elts[0])
        label = _string(child.elts[1])
        if key is not None and label is not None:
            pairs.append((key, label))
    return pairs


def test_setup_labels_match_sensor_translations() -> None:
    section = _entity_section()["sensor"]
    setup = COMPONENT / "sensor_setup.py"
    tree = _module(setup)
    for key, label in _dict_labels(tree):
        assert section[key]["name"] == label
    solar = _function("_build_solar_sensors")
    for key, label in _tuple_labels(solar, 6):
        assert section[key]["name"] == label
    radio = _function("_radio_metadata_specs")
    for key, label in _tuple_labels(radio, 3):
        assert section[key]["name"] == label


def test_energy_names_follow_the_setup_source_and_usage() -> None:
    section = _entity_section()["sensor"]
    tuples = []
    for node in ast.walk(_function("_build_energy_sensors")):
        if not isinstance(node, ast.Tuple):
            continue
        values = [_string(element) for element in node.elts]
        if values and all(value is not None for value in values):
            tuples.append(tuple(values))
    assert ("gas", "electric", "solar") in tuples
    assert ("dhw", "climate") in tuples
    for source in ("gas", "electric", "solar"):
        for usage in ("dhw", "climate"):
            expected = f"{source.capitalize()} {usage.upper()}"
            assert section[f"{source}_{usage}"]["name"] == expected


def test_dynamic_names_substitute_to_the_supplied_text() -> None:
    section = _entity_section()
    pump = section["fan"]["boiler_pump"]["name"].replace("{pump_name}", "Feed Pump")
    switch = section["switch"]["solar_switch"]["name"].replace("{label}", "Solar Enabled")
    assert pump == "Feed Pump"
    assert switch == "Solar Enabled"
