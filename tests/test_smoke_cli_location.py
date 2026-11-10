"""Keep the operator CLI outside the installed Home Assistant component."""

from __future__ import annotations

import ast
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISOLATED_RUNNER = '''
import importlib.abc
import importlib.util
import multiprocessing
from pathlib import Path
import runpy
import sys

sys.path.insert(0, sys.argv[1])

class NoHomeAssistant(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "homeassistant" or fullname.startswith("homeassistant."):
            raise AssertionError("Home Assistant import attempted")

sys.meta_path.insert(0, NoHomeAssistant())

def block_network(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
        raise OSError("network blocked")

sys.addaudithook(block_network)

if __name__ == "__main__":
    mode = sys.argv[2]
    if mode == "help":
        sys.argv = ["scripts.smoke_profile", "--help"]
        runpy.run_module("scripts.smoke_profile", run_name="__main__")
    elif mode == "old":
        runpy.run_module("custom_components.helianthus.smoke_profile", run_name="__main__")
    else:
        from scripts import smoke_profile
        from custom_components.helianthus.admission import normalize_source_selection
        assert smoke_profile.normalize_source_selection is normalize_source_selection
        assert importlib.util.find_spec("custom_components.helianthus.smoke_profile") is None
        assert not any(name.startswith("homeassistant") for name in sys.modules)
        if mode == "spawn":
            context = multiprocessing.get_context("spawn")
            for target, arguments, expected in (
                (smoke_profile._resolver_worker, ("gateway.example", 80), "network blocked"),
                (smoke_profile._urlopen_request_worker,
                 ("unsupported://gateway.example/graphql", b"{}", 1.0),
                 "connection error: unknown url type: unsupported"),
            ):
                receive, send = context.Pipe(duplex=False)
                process = context.Process(target=target, args=(send, *arguments))
                try:
                    process.start()
                    send.close()
                    assert receive.poll(5), "spawn worker did not reply"
                    assert receive.recv() == ("error", expected)
                    process.join(timeout=5)
                    assert process.exitcode == 0
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                    receive.close()
                    send.close()
        print("isolated " + mode + " passed")
'''


def _run_isolated(tmp_path: Path, mode: str) -> subprocess.CompletedProcess:
    runner = tmp_path / "isolated_smoke.py"
    runner.write_text(ISOLATED_RUNNER, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(runner), str(ROOT), mode],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False,
    )


@pytest.mark.parametrize("mode", ["import", "help", "spawn"])
def test_operator_cli_without_home_assistant_or_network(tmp_path: Path, mode: str) -> None:
    result = _run_isolated(tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "help":
        for option in ("--url", "--timeout", "--startup-spotcheck-v2", "--json", "--dual-topology"):
            assert option in result.stdout
    else:
        assert result.stdout.strip() == f"isolated {mode} passed"


def test_old_cli_invocation_has_no_alias(tmp_path: Path) -> None:
    result = _run_isolated(tmp_path, "old")
    assert result.returncode == 1
    assert "No module named custom_components.helianthus.smoke_profile" in result.stderr
    assert "network blocked" not in result.stderr


def test_installed_component_inventory_excludes_operator_cli(tmp_path: Path) -> None:
    source = ROOT / "custom_components/helianthus"
    installed = tmp_path / "custom_components/helianthus"
    shutil.copytree(source, installed, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    inventory = {path.relative_to(installed).as_posix() for path in installed.rglob("*") if path.is_file()}
    assert {"__init__.py", "manifest.json", "strings.json", "admission.py", "address.py", "const.py"} <= inventory
    assert "smoke_profile.py" not in inventory
    assert not (source / "smoke_profile.py").exists()
    assert (ROOT / "scripts/smoke_profile.py").is_file()
    assert not any("smoke" in path or path.startswith("scripts/") for path in inventory)
    for path in installed.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.Constant) and node.value == "__main__" for node in ast.walk(tree)), path.name
