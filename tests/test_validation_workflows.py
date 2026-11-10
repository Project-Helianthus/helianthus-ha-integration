"""Static hosted-validation contracts; actionlint validates YAML syntax separately."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return (ROOT / ".github/workflows" / name).read_text()


def job(text, name):
    match = re.search(r"^  " + re.escape(name) + r":\n(.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S)
    if not match:
        raise AssertionError("missing job " + name)
    return match[1]


class ValidationWorkflowTests(unittest.TestCase):
    def test_all_pull_requests_and_main_pushes_are_covered(self):
        for name in ("readability.yml", "ha-validation.yml"):
            with self.subTest(workflow=name):
                text = workflow(name)
                events = text.split("on:\n", 1)[1].split("permissions:", 1)[0]
                self.assertEqual(events, "  pull_request:\n  push:\n    branches: [main]\n")
                self.assertIn("permissions:\n  contents: read\n", text)
                self.assertNotRegex(text, r"(?m)^\s+(if|environment|paths|paths-ignore):")
                self.assertNotIn("deploy", text.lower())
                self.assertEqual(text.count("runs-on:"), text.count("runs-on: ubuntu-latest"))
                self.assertEqual(text.count("runs-on:"), len(re.findall(r"timeout-minutes: [1-9][0-9]*", text)))

    def test_exact_core_dependency_supported_python_and_existing_stubs_retained(self):
        lines = (ROOT / "requirements-readability.txt").read_text().splitlines()
        self.assertEqual([line for line in lines if line.startswith("homeassistant")], ["homeassistant==2026.11.1"])
        self.assertEqual([line for line in lines if line.startswith("ruff")], ["ruff==0.15.10"])
        text = job(workflow("ha-validation.yml"), "home-assistant-tests")
        self.assertIn("    continue-on-error: true\n", text)
        self.assertRegex(text, r"uses: actions/setup-python@v5\n        with:\n          python-version: '3.14'")
        self.assertIn("run: python -m pip install -r requirements-readability.txt\n", text)
        self.assertIn("import homeassistant.core", text)
        self.assertIn("assert version('homeassistant') == '2026.11.1'", text)
        self.assertTrue(any("sys.modules" in path.read_text() and "homeassistant" in path.read_text()
                            for path in (ROOT / "tests").glob("test_*.py")))

    def test_nonzero_collection_is_required_before_component_execution(self):
        text = job(workflow("ha-validation.yml"), "home-assistant-tests")
        collect = "      - name: Collect component tests\n        run: python -m pytest --collect-only -q tests\n"
        execute = "      - name: Run component tests with core installed\n        run: python -m pytest tests\n"
        self.assertIn(collect, text)
        self.assertIn(execute, text)
        self.assertLess(text.index(collect), text.index(execute))
        # Pytest exits 5 for empty collection. Exact commands preserve that status.
        self.assertNotRegex(text, r"(?m)^ {6,}continue-on-error:")
        count = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
                    for path in (ROOT / "tests").glob("test_*.py") for node in ast.walk(ast.parse(path.read_text())))
        self.assertGreater(count, 0)

    def test_hassfest_invocation_is_reviewed_action_on_docker_capable_runner(self):
        text = job(workflow("ha-validation.yml"), "hassfest")
        self.assertRegex(text, r"(?m)^      - uses: home-assistant/actions/hassfest@[0-9a-f]{40} # 2026-10-26$")
        self.assertEqual(text.count("home-assistant/actions/hassfest@"), 1)
        self.assertIn("runs-on: ubuntu-latest", text)

    def test_readability_runs_guard_and_workflow_tests_without_refreshing_debt(self):
        text = job(workflow("readability.yml"), "readability")
        for command in ("python3 scripts/check_readability.py --self-test", "python3 scripts/check_readability.py --check",
                        "python3 -m unittest discover -s tests -p 'test_validation_workflows.py' -v"):
            self.assertIn("      - run: " + command + "\n", text)
        self.assertIn("/^ruff==/p' requirements-readability.txt", text)
        self.assertNotRegex(text, r"--(fix|generate|update)")


if __name__ == "__main__":
    unittest.main()
