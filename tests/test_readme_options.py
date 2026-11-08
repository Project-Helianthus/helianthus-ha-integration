"""Offline README contract for public forms and repository link targets."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "helianthus"
NO_DEFAULT = "—"


def literal_constants(source):
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, AttributeError):
                continue
    return values


def form_contract(config, options, constants):
    """Read schema markers, resolving only fresh-entry fallback expressions.

    No imports or application code execute. Unsupported expressions fail closed
    so a new schema shape requires an explicit contract review.
    """
    names = literal_constants(constants)
    # Public Home Assistant keys imported by config_flow; no HA runtime needed.
    names.update(CONF_HOST="host", CONF_PORT="port")
    names.update(literal_constants((PACKAGE / "semantic_snapshot_coordinator.py").read_text()))
    rows = {}
    for flow, source in (("config", config), ("options", options)):
        tree = ast.parse(source)
        for method in ast.walk(tree):
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            assignments = {
                node.targets[0].id: node.value
                for node in method.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            }

            def value(node):
                if isinstance(node, ast.Name):
                    if node.id in names:
                        return names[node.id]
                    return value(assignments[node.id])
                if isinstance(node, ast.IfExp):
                    return value(node.orelse)
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in {"str", "bool"}:
                        return {"str": str, "bool": bool}[node.func.id](value(node.args[0]))
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                        assert len(node.args) == 2, "get requires an explicit fresh default"
                        return value(node.args[1])
                return ast.literal_eval(node)

            for node in ast.walk(method):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "vol"
                    and node.func.attr == "Schema"
                ):
                    continue
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Dict)
                for marker in node.args[0].keys:
                    assert isinstance(marker, ast.Call)
                    assert isinstance(marker.func, ast.Attribute)
                    assert isinstance(marker.func.value, ast.Name)
                    assert marker.func.value.id == "vol"
                    assert marker.func.attr in {"Required", "Optional"}
                    assert len(marker.args) == 1
                    defaults = {kw.arg: kw.value for kw in marker.keywords}
                    assert set(defaults) <= {"default"}
                    key = (flow + "/" + method.name.removeprefix("async_step_"), value(marker.args[0]))
                    assert key not in rows, f"duplicate form field: {key}"
                    rows[key] = (
                        marker.func.attr.lower(),
                        json.dumps(value(defaults["default"])) if "default" in defaults else NO_DEFAULT,
                    )
    assert rows, "no public form fields extracted"
    return rows


def documented_contract(readme):
    rows = {}
    for flow, key, presence, default in re.findall(
        r'^\| `(config/[^`]+|options/[^`]+)` \| `([^`]+)` \| (required|optional) \| `([^`]+)` \|$',
        readme, re.MULTILINE,
    ):
        identity = (flow, key)
        assert identity not in rows, f"duplicate README field: {identity}"
        rows[identity] = (presence, default)
    return rows


def check_forms(readme, config, options, constants):
    expected = form_contract(config, options, constants)
    assert documented_contract(readme) == expected, "README form fields/defaults differ from public schemas"


def local_links(readme, root):
    """Resolve inline and reference Markdown destinations without network I/O."""
    prose = re.sub(r"```.*?```", "", readme, flags=re.DOTALL)
    destinations = re.findall(r'\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)', prose)
    references = dict(re.findall(r'^\s*\[([^\]]+)\]:\s*(\S+)', prose, re.MULTILINE))
    for label, reference in re.findall(r'\[([^\]]+)\]\[([^\]]*)\]', prose):
        reference = reference or label
        assert reference in references, f"undefined Markdown reference: {reference}"
    destinations.extend(references.values())
    local = set()
    for destination in destinations:
        parsed = urlsplit(destination.strip("<>"))
        if parsed.scheme or parsed.netloc:
            assert parsed.scheme in {"https", "http", "mailto"}, "unsupported link scheme"
            if parsed.scheme != "mailto":
                assert parsed.hostname, "remote URL has no hostname"
            continue
        if not parsed.path:
            continue
        target = (root / unquote(parsed.path)).resolve()
        assert target.is_relative_to(root.resolve()), "local link escapes repository"
        assert target.exists(), f"missing local link target: {parsed.path}"
        local.add(target.relative_to(root.resolve()).as_posix())
    return local


CONFIG = (PACKAGE / "config_flow.py").read_text()
OPTIONS = (PACKAGE / "options_flow.py").read_text()
CONSTANTS = (PACKAGE / "const.py").read_text()
README = (ROOT / "README.md").read_text()
FORMS = form_contract(CONFIG, OPTIONS, CONSTANTS)


def test_readme_forms_match_public_schemas():
    check_forms(README, CONFIG, OPTIONS, CONSTANTS)


def test_readme_links_and_documentation_index():
    targets = local_links(README, ROOT)
    assert {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").rglob("*.md")} <= targets
    assert {"AGENTS.md", "ARCHITECTURE.md", "CONVENTIONS.md", "MCP_FIRST_ROLLOUT_GUARDRAILS.md"} <= targets


def test_examples_are_examples_rather_than_defaults():
    section = README.split("### 4) Config flow and options examples", 1)[1].split("## Offline", 1)[0]
    examples = re.findall(r"```yaml\n(.*?)```", section, re.DOTALL)
    assert len(examples) == 2
    for flow, example in zip(("config/user", "options/settings"), examples):
        seen = set()
        for line in example.splitlines():
            key, encoded = line.split(":", 1)
            assert key not in seen
            seen.add(key)
            assert (flow, key) in FORMS
            example_value = json.loads(encoded.split("#", 1)[0].strip())
            default = FORMS[(flow, key)][1]
            if default != NO_DEFAULT:
                assert type(example_value) is type(json.loads(default))


@pytest.mark.parametrize("flow,key", FORMS)
@pytest.mark.parametrize("field", ["flow", "key", "presence", "default"])
def test_each_documented_field_detects_drift(flow, key, field):
    presence, default = FORMS[(flow, key)]
    row = f"| `{flow}` | `{key}` | {presence} | `{default}` |"
    replacements = {
        "flow": row.replace(f"`{flow}`", f"`{flow}_changed`"),
        "key": row.replace(f"`{key}`", f"`{key}_changed`"),
        "presence": row.replace(f"| {presence} |", f"| {'optional' if presence == 'required' else 'required'} |"),
        "default": row.replace(f"`{default}`", '`"wrong-default"`'),
    }
    assert row in README
    with pytest.raises(AssertionError, match="README form"):
        check_forms(README.replace(row, replacements[field]), CONFIG, OPTIONS, CONSTANTS)


def test_newly_exposed_option_is_rejected():
    changed = OPTIONS.replace(
        'vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): int,',
        'vol.Optional("new_setting", default=False): bool,\n'
        '                vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): int,',
    )
    assert changed != OPTIONS
    with pytest.raises(AssertionError, match="README form"):
        check_forms(README, CONFIG, changed, CONSTANTS)


def test_removed_option_still_documented_is_rejected():
    changed = OPTIONS.replace('vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): int,', '')
    assert changed != OPTIONS
    with pytest.raises(AssertionError, match="README form"):
        check_forms(README, CONFIG, changed, CONSTANTS)


def test_changed_production_default_is_rejected():
    changed = CONSTANTS.replace('DEFAULT_SCAN_INTERVAL = 60', 'DEFAULT_SCAN_INTERVAL = 61')
    assert changed != CONSTANTS
    with pytest.raises(AssertionError, match="README form"):
        check_forms(README, CONFIG, OPTIONS, changed)


@pytest.mark.parametrize("link", ["[guide](missing.md)", "[guide][target]\n[target]: missing.md"])
def test_broken_local_target_is_rejected(tmp_path, link):
    with pytest.raises(AssertionError, match="missing local link"):
        local_links(link, tmp_path)
