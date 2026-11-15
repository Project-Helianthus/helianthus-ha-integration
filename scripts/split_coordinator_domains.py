#!/usr/bin/env python3
"""Split the coordinator module into one module per coordinator domain.

Each coordinator class moves verbatim, together with the GraphQL documents and
field lists it reads, into its domain module. The helpers the domains share stay
in ``coordinator.py``. Importers of the moved names are rewritten and the
readability baseline rows of the moved symbols follow them to their new paths.

The input is a tree before the split (``--base``, the repository by default);
the output goes to ``--out`` (the repository by default). ``--check`` generates
the output from ``--base`` without writing and compares it with the repository.
Standard library only; no network and no version control access.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "custom_components/helianthus"
SOURCE = f"{PACKAGE}/coordinator.py"
BASELINE = "scripts/readability-baseline.json"
# Names that stay in coordinator.py: the posture sentinel and the radio group
# numbers, which no coordinator reads.
SHARED_NAMES = frozenset({"M5B_FORWARD_COMPAT_POSTURE"})
SHARED_PREFIX = "_RADIO_GROUP_"

# Domain module -> (docstring, top-level names it owns, in source order).
DOMAINS = {
    "coordinator_devices": (
        "Device inventory coordinator and its GraphQL documents.",
        ("QUERY_EXTENDED_V3", "QUERY_EXTENDED_V3_NO_PART", "QUERY_EXTENDED_V3_NO_ADDRESSES",
         "QUERY_EXTENDED_V3_NO_PART_NO_ADDRESSES", "QUERY_EXTENDED_V2",
         "QUERY_EXTENDED_V2_NO_ADDRESSES", "QUERY_BASE", "QUERY_BASE_NO_ADDRESSES",
         "HelianthusCoordinator"),
    ),
    "coordinator_status": (
        "Service status coordinator and its GraphQL documents.",
        ("QUERY_STATUS", "QUERY_STATUS_NO_INITIATOR", "QUERY_STATUS_MINIMAL",
         "QUERY_STATUS_LEGACY", "_STATUS_SCHEMA_FIELDS", "HelianthusStatusCoordinator"),
    ),
    "coordinator_semantic": (
        "Semantic zone and DHW coordinator and its GraphQL documents.",
        ("QUERY_SEMANTIC", "QUERY_SEMANTIC_PROMOTED", "QUERY_SEMANTIC_NO_HOLIDAY",
         "QUERY_SEMANTIC_NO_QV", "QUERY_SEMANTIC_LEGACY", "_HOLIDAY_FIELDS", "_QV_FIELDS",
         "_SEMANTIC_RECOVERABLE_FIELDS", "_PROMOTED_SEMANTIC_FIELDS",
         "HelianthusSemanticCoordinator"),
    ),
    "coordinator_circuits": (
        "Heating circuit coordinator and its GraphQL document.",
        ("QUERY_CIRCUITS", "HelianthusCircuitCoordinator"),
    ),
    "coordinator_radio": (
        "Remote-slot radio device coordinator and its GraphQL document.",
        ("QUERY_RADIO_DEVICES", "HelianthusRadioDeviceCoordinator"),
    ),
    "coordinator_fm5": (
        "Functional-module mode, solar and cylinder coordinator and its GraphQL document.",
        ("QUERY_FM5", "HelianthusFM5Coordinator"),
    ),
    "coordinator_system": (
        "System state and configuration coordinator and its GraphQL documents.",
        ("QUERY_SYSTEM", "QUERY_SYSTEM_LEGACY", "QUERY_SYSTEM_GATEWAY_METADATA",
         "QUERY_SYSTEM_INSTALLER", "QUERY_SYSTEM_SENSITIVE",
         "SYSTEM_DHW_BOUND_REPROBE_COOLDOWN_SECONDS", "HelianthusSystemCoordinator"),
    ),
    "coordinator_energy": (
        "Energy totals coordinator and its GraphQL documents.",
        ("QUERY_ENERGY", "QUERY_ENERGY_LEGACY", "HelianthusEnergyCoordinator"),
    ),
    "coordinator_boiler": (
        "Boiler status coordinator and its GraphQL documents.",
        ("QUERY_BOILER", "QUERY_BOILER_INSTALLER", "QUERY_BOILER_SENSITIVE",
         "HelianthusBoilerCoordinator"),
    ),
    "coordinator_schedules": (
        "Weekly timer schedule coordinator and its GraphQL document.",
        ("QUERY_SCHEDULES", "HelianthusScheduleCoordinator"),
    ),
    "coordinator_adapter_info": (
        "Adapter hardware information coordinator and its GraphQL documents.",
        ("QUERY_ADAPTER_HARDWARE_INFO", "QUERY_ADAPTER_HARDWARE_INFO_MINIMAL",
         "_ADAPTER_HARDWARE_INFO_DETAILED_ONLY_FIELDS",
         "_ADAPTER_HARDWARE_INFO_REPROBE_INITIAL_DELAY_S",
         "_ADAPTER_HARDWARE_INFO_REPROBE_MAX_DELAY_S", "HelianthusAdapterInfoCoordinator"),
    ),
}
OWNER = {name: module for module, (_, names) in DOMAINS.items() for name in names}
CLASS_MODULES = tuple(sorted(
    (module, name) for name, module in OWNER.items() if name.startswith("Helianthus")
))

# The shared schema-error helper is private in its home module; the domains read
# it through the public shared helper so that no new private import appears.
SHARED_HELPER_IMPORT = "from .coordinator import is_missing_field_error as _is_missing_field_error"
SHARED_HELPER = '''

def is_missing_field_error(errors: object, fields: list[str]) -> bool:
    """Return whether a GraphQL error reports one of the fields as unknown."""
    return _is_missing_field_error(errors, fields)
'''
SHARED_DOCSTRING = "Helpers shared by the domain coordinator modules."
POSTURE_OLD = (
    "# Structural tolerance is achieved by construction: every dict access in\n"
    "# this module uses ``.get()`` with an explicit default and walks payloads\n"
    "# defensively. No Pydantic / strict schema decoder is used. New unknown\n"
    "# keys therefore flow through silently.\n"
)
POSTURE_NEW = (
    "# Structural tolerance is achieved by construction: every dict access in\n"
    "# the domain coordinator modules uses ``.get()`` with an explicit default\n"
    "# and walks payloads defensively. No Pydantic / strict schema decoder is\n"
    "# used. New unknown keys therefore flow through silently.\n"
)
# The moved docstring names the Home Assistant source by module and function
# instead of a link pinned to one upstream revision.
PINNED_REFERENCE = (
    re.compile(r"        refresh interval\. Direct listener notification preserves that deadline:\n"
               r"        https://github\.com/home-assistant/core/blob/[0-9a-f]+/"
               r"homeassistant/helpers/update_coordinator\.py#L\d+-L\d+\n"),
    "        refresh interval (``DataUpdateCoordinator`` in\n"
    "        ``homeassistant/helpers/update_coordinator.py``). Direct listener\n"
    "        notification preserves that deadline.\n",
    1,
)


def import_group(node: ast.stmt) -> int:
    """Return 0 for the standard library, 1 for third parties, 2 for the package."""
    if isinstance(node, ast.ImportFrom) and node.level:
        return 2
    name = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
    return 0 if name.split(".")[0] in sys.stdlib_module_names else 1


def render_import(node: ast.stmt, aliases: list[ast.alias]) -> str:
    rendered = [alias.name + (f" as {alias.asname}" if alias.asname else "") for alias in aliases]
    if isinstance(node, ast.Import):
        return "\n".join(f"import {item}" for item in rendered)
    module = "." * node.level + (node.module or "")
    line = f"from {module} import {', '.join(rendered)}"
    if len(line) <= 88:
        return line
    return f"from {module} import (\n" + "".join(f"    {item},\n" for item in rendered) + ")"


def render_imports(imports: list[ast.stmt], used: set[str]) -> str:
    """Render the original import statements, reduced to the names in ``used``."""
    groups: dict[int, list[str]] = {0: [], 1: [], 2: []}
    for node in imports:
        aliases = [alias for alias in node.names if (alias.asname or alias.name) in used]
        if "_is_missing_field_error" in {alias.asname or alias.name for alias in aliases}:
            groups[2].append(SHARED_HELPER_IMPORT)
            aliases = [alias for alias in aliases if alias.name != "_is_missing_field_error"]
        if aliases:
            groups[import_group(node)].append(render_import(node, aliases))
    return "\n\n".join("\n".join(lines) for lines in groups.values() if lines)


def statement_owner(node: ast.stmt) -> tuple[str, str]:
    """Return the single name a top-level statement defines and its owning module."""
    targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", node)]
    names = [getattr(target, "id", getattr(target, "name", None)) for target in targets]
    shared = names[0] in SHARED_NAMES or str(names[0]).startswith(SHARED_PREFIX)
    if len(names) != 1 or not (shared or names[0] in OWNER):
        raise SystemExit(f"no owner for the statement at line {node.lineno}: {names}")
    return names[0], OWNER.get(names[0], "")


def split_source(source: str) -> tuple[list[ast.stmt], list[tuple[str, str]]]:
    """Return the imports and each top-level statement's owner with its verbatim text."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    imports, statements, defined, start = [], [], set(), 1
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            pass
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif not isinstance(node, ast.Expr):
            name, owner = statement_owner(node)
            defined.add(name)
            statements.append((owner, "".join(lines[start - 1:node.end_lineno])))
        start = node.end_lineno + 1
    missing = (OWNER.keys() | SHARED_NAMES) - defined
    if missing:
        raise SystemExit(f"coordinator source lacks: {sorted(missing)}")
    return imports, statements


def owned_runs(statements: list[tuple[str, str]], owner: str) -> str:
    """Join the owner's consecutive statements verbatim; separate runs by two blank lines."""
    runs, previous = [], None
    for current, text in statements:
        if current == owner:
            if previous == owner:
                runs[-1] += text
            else:
                runs.append(text.lstrip("\n"))
        previous = current
    return "\n\n".join(runs)


def used_names(text: str) -> set[str]:
    return {node.id for node in ast.walk(ast.parse(text)) if isinstance(node, ast.Name)}


def module_text(docstring: str, imports: str, body: str) -> str:
    return f'"""{docstring}"""\n\nfrom __future__ import annotations\n\n{imports}\n\n\n{body}'


def split_coordinator(source: str) -> dict[str, str]:
    imports, statements = split_source(source)
    outputs = {}
    for module, (docstring, _) in DOMAINS.items():
        body = owned_runs(statements, module)
        outputs[f"{PACKAGE}/{module}.py"] = module_text(
            docstring, render_imports(imports, used_names(body)), body)
    semantic = f"{PACKAGE}/coordinator_semantic.py"
    outputs[semantic] = apply_rewrites(semantic, outputs[semantic], [PINNED_REFERENCE])
    shared = owned_runs(statements, "")
    if POSTURE_OLD not in shared:
        raise SystemExit("coordinator posture comment changed; review the split")
    shared = shared.replace(POSTURE_OLD, POSTURE_NEW) + SHARED_HELPER
    outputs[SOURCE] = module_text(
        SHARED_DOCSTRING, "from .coordinator_availability import _is_missing_field_error", shared)
    return outputs


CLASS_IMPORT_BLOCK = re.compile(
    r"^(?P<indent>[ ]*)from \.coordinator import \(\n(?P<names>(?:[ ]+\w+,\n)+)(?P=indent)\)\n",
    re.MULTILINE,
)


def rewrite_class_imports(text: str) -> str:
    """Replace each parenthesized coordinator class import by domain-module imports."""
    def replace(match: re.Match) -> str:
        names = [line.strip().rstrip(",") for line in match["names"].splitlines()]
        modules: dict[str, list[str]] = {}
        for name in names:
            modules.setdefault(OWNER[name], []).append(name)
        return "".join(
            f"{match['indent']}from .{module} import {', '.join(modules[module])}\n"
            for module in sorted(modules))

    rewritten, count = CLASS_IMPORT_BLOCK.subn(replace, text)
    if not count:
        raise SystemExit("no coordinator class import block found")
    return rewritten


def domain_table(indent: str, name: str) -> str:
    rows = "".join(f'{indent}    ("{module}", "{cls}"),\n' for module, cls in CLASS_MODULES)
    return f"{indent}{name} = (\n{rows}{indent})\n"


def test_coordinator_imports(text: str) -> tuple[str, str]:
    """Return the original import block of the coordinator tests and its replacement."""
    start = text.index("import custom_components.helianthus.coordinator as coordinator_module\n")
    end = text.index(")\n", text.index("from custom_components.helianthus.coordinator import (")) + 2
    names = re.findall(r"^    (\w+),$", text[start:end], re.MULTILINE)
    modules = "".join(f"    {module},\n" for module in DOMAINS_SORTED)
    graphql = "from custom_components.helianthus.graphql import (\n" \
        "    GraphQLClientError,\n    GraphQLResponseError,\n)\n"
    if text[end:end + len(graphql)] != graphql:
        raise SystemExit("coordinator test import layout changed; review the split")
    bindings = "".join(
        f"{name} = {OWNER.get(name, 'coordinator_semantic')}.{name}\n" for name in names)
    old = text[start:end + len(graphql)]
    new = f"from custom_components.helianthus import (\n{modules})\n{graphql}\n{bindings}"
    return old, new


DOMAINS_SORTED = tuple(sorted(DOMAINS))
FORWARD_COMPAT_SCAN = '''    domain_mods = [
        importlib.import_module(f"{helianthus_pkg.__name__}.{name}")
        for name in (
%s        )
    ]
    for mod in (helianthus_pkg, coordinator_mod, *domain_mods):
''' % "".join(f'            "{module}",\n' for module in DOMAINS_SORTED)
ENTRY_TEST_INSTALL_OLD = '''def _install_coordinators(monkeypatch, events, failing=None):
    module = ModuleType("custom_components.helianthus.coordinator")

'''
ENTRY_TEST_INSTALL_NEW = '''def _install_coordinators(monkeypatch, events, failing=None):
'''
ENTRY_TEST_LOOP_OLD = '''    for _, class_name in ORDER:
        setattr(module, class_name, double(class_name))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys.modules["custom_components.helianthus"], "coordinator", module,
                        raising=False)
'''
ENTRY_TEST_LOOP_NEW = '''    package = sys.modules["custom_components.helianthus"]
    for module_name, class_name in DOMAIN_MODULES:
        module = ModuleType(f"custom_components.helianthus.{module_name}")
        setattr(module, class_name, double(class_name))
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(package, module_name, module, raising=False)
'''
ENTRY_TEST_DOC_OLD = '''- coordinator class (eleven distinct doubles): a field built from the wrong
  class or two fields sharing one instance.
'''
ENTRY_TEST_DOC_NEW = '''- coordinator class (eleven distinct doubles): a field built from the wrong
  class or two fields sharing one instance.
- domain module (each double only in its own module): a class imported from
  another domain's module.
'''
OWNER_DOUBLES_OLD = '''    # A module double keeps this test independent of HA's DataUpdateCoordinator.
    install("custom_components.helianthus.coordinator", **{
        name: LegacyCoordinator for name in (
            "HelianthusAdapterInfoCoordinator", "HelianthusBoilerCoordinator",
            "HelianthusCircuitCoordinator", "HelianthusCoordinator",
            "HelianthusEnergyCoordinator", "HelianthusFM5Coordinator",
            "HelianthusRadioDeviceCoordinator", "HelianthusScheduleCoordinator",
            "HelianthusSemanticCoordinator", "HelianthusSystemCoordinator",
            "HelianthusStatusCoordinator",
        )
    })
'''
OWNER_DOUBLES_NEW = '''    # Module doubles keep this test independent of HA's DataUpdateCoordinator.
    _install_coordinator_doubles(install, LegacyCoordinator)
'''
DOUBLES_HELPER_ANCHOR = '''@pytest.mark.parametrize("readable_snapshot", [False, True])
def test_entry_setup_publication_unload_and_fresh_setup('''
DOUBLES_HELPER = domain_table("", "COORDINATOR_MODULES") + '''

def _install_coordinator_doubles(install, coordinator_class):
    """Replace every domain coordinator module by one serving ``coordinator_class``."""
    for module_name, class_name in COORDINATOR_MODULES:
        install(f"custom_components.helianthus.{module_name}", **{class_name: coordinator_class})


'''
HARNESS_TABLE_OLD = '''COORDINATORS = (
    "HelianthusAdapterInfoCoordinator", "HelianthusBoilerCoordinator",
    "HelianthusCircuitCoordinator", "HelianthusCoordinator",
    "HelianthusEnergyCoordinator", "HelianthusFM5Coordinator",
    "HelianthusRadioDeviceCoordinator", "HelianthusScheduleCoordinator",
    "HelianthusSemanticCoordinator", "HelianthusSystemCoordinator",
    "HelianthusStatusCoordinator",
)
'''
HARNESS_INSTALL_OLD = '''    install_module(monkeypatch, "custom_components.helianthus.coordinator",
                   **{name: _coordinator_class(name, coordinator_data) for name in COORDINATORS})
'''
HARNESS_INSTALL_NEW = '''    for module_name, name in COORDINATORS:
        install_module(monkeypatch, f"custom_components.helianthus.{module_name}",
                       **{name: _coordinator_class(name, coordinator_data)})
'''
ADVERSARIAL_STUB_OLD = '''    coordinator_module = types.ModuleType("custom_components.helianthus.coordinator")
    for name in (
        "HelianthusAdapterInfoCoordinator", "HelianthusBoilerCoordinator", "HelianthusCircuitCoordinator",
        "HelianthusCoordinator", "HelianthusEnergyCoordinator", "HelianthusFM5Coordinator",
        "HelianthusRadioDeviceCoordinator", "HelianthusScheduleCoordinator", "HelianthusSystemCoordinator",
        "HelianthusStatusCoordinator",
    ):
        setattr(coordinator_module, name, FakeCoordinator)
    coordinator_module.HelianthusCoordinator = FakeDeviceCoordinator
    coordinator_module.HelianthusSemanticCoordinator = FakeSemanticCoordinator
    replace("custom_components.helianthus.coordinator", coordinator_module)
'''
ADVERSARIAL_STUB_NEW = '''    fakes = {"HelianthusCoordinator": FakeDeviceCoordinator,
             "HelianthusSemanticCoordinator": FakeSemanticCoordinator}
    for module_name, class_name in _COORDINATOR_DOMAIN_MODULES:
        coordinator_module = types.ModuleType(f"custom_components.helianthus.{module_name}")
        setattr(coordinator_module, class_name, fakes.get(class_name, FakeCoordinator))
        replace(coordinator_module.__name__, coordinator_module)
'''
ADVERSARIAL_ANCHOR = "def _replay_actual_delayed_inventory_listener() -> None:\n"
NAMING_OLD = 'coordinator = _constants("coordinator.py")\n'
NAMING_NEW = '''coordinator = SimpleNamespace(**{
    name: value
    for module in (
%s    )
    for name, value in vars(_constants(f"{module}.py")).items()
})
''' % "".join(f'        "{module}",\n' for module in DOMAINS_SORTED)

# Path -> ordered (old, new, expected occurrences) text replacements.
REWRITES = {
    "tests/test_system_field_names.py": [
        ("    coordinator_module,\n", "    coordinator_system,\n", 1),
        ("coordinator_module.UpdateFailed", "coordinator_system.UpdateFailed", 1),
    ],
    "tests/test_circuit.py": [(
        "from custom_components.helianthus import coordinator as coordinator_module\n",
        "from custom_components.helianthus import coordinator_circuits as coordinator_module\n",
        2,
    )],
    "tests/test_radio_device.py": [(
        "from custom_components.helianthus.coordinator import HelianthusRadioDeviceCoordinator\n",
        "from custom_components.helianthus.coordinator_radio import HelianthusRadioDeviceCoordinator\n",
        1,
    )],
    "tests/test_graphql_document_contract.py": [
        ("    from custom_components.helianthus import coordinator, semantic_snapshot\n",
         "    from custom_components.helianthus import coordinator_system, semantic_snapshot\n", 1),
        ('        Document("coordinator.QUERY_SYSTEM_LEGACY", coordinator.QUERY_SYSTEM_LEGACY),\n',
         '        Document("coordinator_system.QUERY_SYSTEM_LEGACY", '
         'coordinator_system.QUERY_SYSTEM_LEGACY),\n', 1),
    ],
    "tests/test_optional_query_sequences.py": [(
        '/ "custom_components/helianthus/coordinator.py"\n',
        '/ "custom_components/helianthus/coordinator_system.py"\n',
        1,
    )],
    "tests/test_naming_contract.py": [(NAMING_OLD, NAMING_NEW, 1)],
    "tests/test_number_dhw_bivalence.py": [(
        'coordinator=load("coordinator"))\n', 'coordinator=load("coordinator_system"))\n', 1,
    )],
    "tests/test_legacy_admission_dependency_gate.py": [(
        'COORDINATOR = INTEGRATION_ROOT / "coordinator.py"\n',
        'COORDINATOR = INTEGRATION_ROOT / "coordinator_status.py"\n',
        1,
    )],
    "tests/test_energy_last_good_isolation.py": [(
        'yield importlib.import_module(f"{package_name}.coordinator")\n',
        'yield importlib.import_module(f"{package_name}.coordinator_energy")\n',
        1,
    )],
    "tests/test_forward_compat_m4b.py": [
        ("import json\nimport logging\n", "import importlib\nimport json\nimport logging\n", 1),
        ("            # custom_components/helianthus/coordinator.py.\n",
         "            # the domain coordinator modules.\n", 1),
        ("    for mod in (helianthus_pkg, coordinator_mod):\n", FORWARD_COMPAT_SCAN, 1),
    ],
    "tests/test_entry_coordinators.py": [
        (ENTRY_TEST_DOC_OLD, ENTRY_TEST_DOC_NEW, 1),
        ("\n\ndef _install_coordinators(",
         "\n" + domain_table("", "DOMAIN_MODULES") + "\n\ndef _install_coordinators(", 1),
        (ENTRY_TEST_INSTALL_OLD, ENTRY_TEST_INSTALL_NEW, 1),
        (ENTRY_TEST_LOOP_OLD, ENTRY_TEST_LOOP_NEW, 1),
    ],
    "tests/test_semantic_snapshot_coordinator.py": [
        (OWNER_DOUBLES_OLD, OWNER_DOUBLES_NEW, 1),
        (DOUBLES_HELPER_ANCHOR, DOUBLES_HELPER + DOUBLES_HELPER_ANCHOR, 1),
    ],
    "tests/entry_setup_harness.py": [
        (HARNESS_TABLE_OLD, domain_table("", "COORDINATORS"), 1),
        (HARNESS_INSTALL_OLD, HARNESS_INSTALL_NEW, 1),
    ],
    "scripts/ha_adversarial_harness.py": [
        ("        from custom_components.helianthus.coordinator import HelianthusSemanticCoordinator\n",
         "        from custom_components.helianthus.coordinator_semantic import "
         "HelianthusSemanticCoordinator\n", 2),
        (ADVERSARIAL_ANCHOR,
         domain_table("", "_COORDINATOR_DOMAIN_MODULES") + "\n\n" + ADVERSARIAL_ANCHOR, 1),
        (ADVERSARIAL_STUB_OLD, ADVERSARIAL_STUB_NEW, 1),
    ],
}
CLASS_IMPORTERS = (
    f"{PACKAGE}/entry_coordinators.py",
    f"{PACKAGE}/entry_runtime.py",
)
# Readability rows whose measured count changes with the rewritten structure.
BASELINE_COUNTS = {
    ("tests/test_coordinator.py", "<module>", "E402"): 2,
    ("scripts/ha_adversarial_harness.py", "_replay_actual_delayed_inventory_listener", "PLR0915"): 96,
}


def apply_rewrites(path: str, text: str, rewrites: list[tuple[str | re.Pattern, str, int]]) -> str:
    for old, new, expected in rewrites:
        pattern = old if isinstance(old, re.Pattern) else re.compile(re.escape(old))
        text, found = pattern.subn(lambda _match: new, text)
        if found != expected:
            raise SystemExit(f"{path}: expected {expected} occurrence(s), found {found}: {old!r}")
    return text


def rewrite_coordinator_tests(text: str) -> str:
    old, new = test_coordinator_imports(text)
    rewrites = [
        (old, new, 1),
        ("monkeypatch.setattr(coordinator_module.time,",
         "monkeypatch.setattr(coordinator_adapter_info.time,", 1),
    ]
    return apply_rewrites("tests/test_coordinator.py", text, rewrites)


def move_baseline_rows(text: str) -> str:
    """Move the rows of moved symbols to their module and keep the path order."""
    data = json.loads(text)
    findings, moved = [], []
    for row in data["findings"]:
        key = (row["path"], row["symbol"], row["rule"])
        if key in BASELINE_COUNTS:
            row["count"] = BASELINE_COUNTS[key]
        owner = OWNER.get(row["symbol"].split(".")[0])
        if row["path"] == SOURCE and owner:
            moved.append(dict(row, path=f"{PACKAGE}/{owner}.py"))
        else:
            findings.append(row)
    for row in sorted(moved, key=lambda item: (item["path"], item["symbol"], item["rule"])):
        key = (row["path"], row["symbol"], row["rule"])
        index = next(i for i, other in enumerate(findings)
                     if (other["path"], other["symbol"], other["rule"]) > key)
        findings.insert(index, row)
    data["findings"] = findings
    return json.dumps(data, indent=2) + "\n"


def generate(base: Path) -> dict[str, str]:
    """Return every output path with its text, generated from the tree at ``base``."""
    def read(path: str) -> str:
        return (base / path).read_text(encoding="utf-8")

    outputs = split_coordinator(read(SOURCE))
    for path in CLASS_IMPORTERS:
        outputs[path] = rewrite_class_imports(read(path))
    outputs["tests/test_coordinator.py"] = rewrite_coordinator_tests(read("tests/test_coordinator.py"))
    for path, rewrites in REWRITES.items():
        outputs[path] = apply_rewrites(path, read(path), rewrites)
    outputs[BASELINE] = move_baseline_rows(read(BASELINE))
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, default=ROOT, help="tree before the split")
    parser.add_argument("--out", type=Path, default=ROOT, help="tree to write the output to")
    parser.add_argument("--check", action="store_true",
                        help="compare the output generated from --base with the repository")
    args = parser.parse_args()
    if args.check and args.base.resolve() == ROOT:
        parser.error("--check needs --base pointing at a tree before the split")
    outputs = generate(args.base)
    if args.check:
        differing = [path for path, text in sorted(outputs.items())
                     if not (ROOT / path).is_file()
                     or (ROOT / path).read_text(encoding="utf-8") != text]
        for path in differing:
            print(f"differs: {path}")
        print(f"{len(outputs) - len(differing)} of {len(outputs)} generated files match")
        return int(bool(differing))
    for path, text in sorted(outputs.items()):
        target = args.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(f"wrote {len(outputs)} files under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
