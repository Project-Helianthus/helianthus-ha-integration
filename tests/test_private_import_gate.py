"""Synthetic AST guard cases; component modules are never executed."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "private_import_guard", Path(__file__).resolve().parents[1] / "scripts/check_private_imports.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
P = guard.PACKAGE


class PrivateImportGateTests(unittest.TestCase):
    def edges(self, source, *, path="consumer.py", extra=None):
        sources = {"custom_components/helianthus/" + path: source,
                   "custom_components/helianthus/__init__.py": "",
                   "custom_components/helianthus/helpers.py": ""}
        sources.update(extra or {})
        return guard.collect_edges(sources)

    def test_absolute_relative_and_alias_private_imports(self):
        for source in (f"from {P}.helpers import _hidden",
                       "from .helpers import _hidden as visible"):
            with self.subTest(source=source):
                self.assertEqual(self.edges(source), {(P+'.consumer', P+'.helpers', '_hidden', 'private'): 1})

    def test_relative_depth_and_init_canonicalization(self):
        for source in ("from ..helpers import _hidden", "from ..helpers.__init__ import _hidden"):
            with self.subTest(source=source):
                self.assertEqual(self.edges(source, path='nested/consumer.py'),
                                 {(P+'.nested.consumer', P+'.helpers', '_hidden', 'private'): 1})
        self.assertIn((P+'.consumer', P, '_hidden', 'private'),
                      self.edges('from .__init__ import _hidden'))

    def test_package_init_relative_base(self):
        edges = self.edges('from ..helpers import _hidden', path='nested/__init__.py')
        self.assertIn((P+'.nested', P+'.helpers', '_hidden', 'private'), edges)

    def test_public_sibling_and_external_imports_allowed(self):
        self.assertFalse(self.edges('from .helpers import public\nfrom external import _private'))

    def test_package_imports_only_platform_entrypoints_exempt(self):
        self.assertFalse(self.edges('from . import public', path='sensor.py'))
        self.assertEqual(self.edges('from . import public'),
                         {(P+'.consumer', P, 'public', 'package'): 1})
        self.assertTrue(self.edges('from . import _hidden', path='sensor.py'))
        self.assertTrue(self.edges('from .. import public', path='nested/sensor.py'))

    def test_import_alias_attributes(self):
        cases = [(f'import {P} as pkg\npkg._hidden', P),
                 (f'import {P}\n{P}._hidden', P),
                 ('from . import helpers as pkg\npkg._hidden', P+'.helpers'),
                 (f'import {P}.helpers as pkg\npkg._hidden', P+'.helpers'),
                 ('from custom_components import helianthus as pkg\npkg._hidden', P)]
        for source, target in cases:
            with self.subTest(source=source):
                self.assertIn((P+'.consumer', target, '_hidden', 'private'), self.edges(source))

    def test_public_package_attribute_boundary(self):
        self.assertIn((P+'.consumer', P, 'public', 'package'),
                      self.edges(f'import {P} as pkg\npkg.public'))
        self.assertFalse(self.edges(f'import {P} as pkg\npkg.public', path='sensor.py'))

    def test_star_import_rejected_even_in_platform(self):
        for path in ('consumer.py', 'sensor.py'):
            self.assertIn((P+'.'+path[:-3], P+'.helpers', '*', 'star'),
                          self.edges('from .helpers import *', path=path))

    def test_reexport_preserves_private_origin(self):
        extra = {'custom_components/helianthus/facade.py': 'from .helpers import _hidden as public'}
        self.assertIn((P+'.consumer', P+'.helpers', '_hidden', 'private'),
                      self.edges('from .facade import public', extra=extra))
        self.assertIn((P+'.consumer', P+'.helpers', '_hidden', 'private'),
                      self.edges('from . import facade as pkg\npkg.public', extra=extra))

    def test_module_alias_reexport_and_private_module_import(self):
        extra = {'custom_components/helianthus/facade.py': 'from . import helpers as api'}
        self.assertIn((P+'.consumer', P+'.helpers', '_hidden', 'private'),
                      self.edges('from .facade import api as pkg\npkg._hidden', extra=extra))
        extra = {'custom_components/helianthus/facade.py': f'import {P}.helpers as api'}
        self.assertIn((P+'.consumer', P+'.helpers', '_hidden', 'private'),
                      self.edges('from . import facade as pkg\npkg.api._hidden', extra=extra))
        self.assertIn((P+'.consumer', P, '_internal', 'private'),
                      self.edges(f'import {P}._internal as module'))

    def test_cli_rejects_growth_and_preserves_reductions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scripts').mkdir()
            component = root/'custom_components/helianthus'
            component.mkdir(parents=True)
            (component/'__init__.py').write_text('raise RuntimeError("must never execute")')
            consumer = component/'consumer.py'
            consumer.write_text('from .helpers import _hidden')
            shutil.copyfile(Path(guard.__file__), root/'scripts/check_private_imports.py')
            edges = self.edges(consumer.read_text())
            baseline = {'version': 1, 'edges': [dict(zip(guard.FIELDS, k), count=v) for k,v in edges.items()]}
            (root/'scripts/private-import-baseline.json').write_text(json.dumps(baseline))
            def run():
                return subprocess.run([sys.executable, str(root/'scripts/check_private_imports.py'), '--check'],
                                      capture_output=True, text=True, timeout=10,
                                      env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            self.assertEqual(run().returncode, 0)
            consumer.write_text('from .helpers import _hidden, _new')
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('_new', result.stdout)
            consumer.write_text('')
            self.assertEqual(run().returncode, 0)
            consumer.write_text('from .helpers import *')
            self.assertEqual(run().returncode, 1)
            consumer.write_text('invalid syntax here!')
            self.assertEqual(run().returncode, 2)

    def test_reused_alias_cannot_hide_private_dependency(self):
        source = f'import {P}.helpers as pkg\npkg._hidden\nimport external as pkg'
        self.assertIn((P+'.consumer', P+'.helpers', '_hidden', 'private'), self.edges(source))
        extra = {'custom_components/helianthus/facade.py':
                 'from .helpers import _hidden as public\nfrom external import public'}
        self.assertIn((P+'.consumer', P+'.helpers', '_hidden', 'private'),
                      self.edges('from .facade import public', extra=extra))

    def test_reexport_cycle_terminates(self):
        extra = {'custom_components/helianthus/facade.py': 'from .consumer import public'}
        self.assertFalse(self.edges('from .facade import public', extra=extra))

    def test_function_attributes_are_not_module_dependencies(self):
        self.assertFalse(self.edges('from .helpers import public\npublic._attribute'))

    def test_ratchet_all_identity_fields_and_growth(self):
        baseline = self.edges('from .helpers import _hidden')
        cases = [dict(source='from .helpers import _hidden', path='other.py'),
                 dict(source='from .other import _hidden'),
                 dict(source='from .helpers import _different')]
        for case in cases:
            with self.subTest(case=case):
                self.assertTrue(self.edges(**case) - baseline)
        key = next(iter(baseline))
        changed_kind = guard.Counter({(*key[:-1], 'star'): 1})
        self.assertTrue(changed_kind - baseline)
        self.assertTrue(self.edges('from .helpers import _hidden\nfrom .helpers import _hidden as second') - baseline)
        self.assertFalse(guard.Counter() - baseline)
        self.assertFalse(self.edges('from .helpers import _hidden as other') - baseline)

    def test_baseline_schema_rejects_duplicate_and_invalid_counts(self):
        row = dict(zip(guard.FIELDS, (P+'.consumer', P+'.helpers', '_hidden', 'private')), count=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'baseline.json'
            for rows in ([row, row], [dict(row, count=0)], [dict(row, count=True)], [dict(row, kind='unknown')]):
                with self.subTest(rows=rows):
                    path.write_text(json.dumps({'version': 1, 'edges': rows}))
                    with self.assertRaises(ValueError):
                        guard.load_baseline(path)
            path.write_text(json.dumps({'version': 1, 'edges': [row]}))
            self.assertEqual(sum(guard.load_baseline(path).values()), 1)

    def test_shipped_scope_and_cli_ratchet(self):
        root = Path(__file__).resolve().parents[1]
        sources = {p.relative_to(root).as_posix(): p.read_text() for p in (root/'custom_components/helianthus').rglob('*.py')}
        baseline = guard.load_baseline(root/'scripts/private-import-baseline.json')
        self.assertFalse(guard.collect_edges(sources) - baseline)
        sources['custom_components/helianthus/synthetic.py'] = 'from .helpers import _new'
        self.assertTrue(guard.collect_edges(sources) - baseline)


if __name__ == '__main__':
    unittest.main()
