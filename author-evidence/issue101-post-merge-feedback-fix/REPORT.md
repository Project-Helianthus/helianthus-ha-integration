# Issue 101 post-merge feedback fix

## Immutable implementation evidence

- Repository: `Project-Helianthus/helianthus-ha-integration`
- Issue: `#101`
- Branch: `issue/101-startup-spotcheck-v2-fix-forward`
- Base commit: `fc1f675d26a65b82390bfeab51cb35db442dae88`
- Base tree: `2912299f0a6716ff1ac6ac3c592160ff3cdd68f2`
- Implementation commit: `2965cbd0fe7a604fa143c30db87207240f43b8ed`
- Implementation tree: `e6ff816f0b4f9ead99b71cd7807e007c06d1b4a7`

The implementation commit is the exact code, test, and README state validated
below. This report is recorded in a later evidence-only commit so that it can
name the immutable implementation commit and tree without a self-referential
Git hash.

## Changes

- Replaced the default topology probe's synchronous
  `socket.create_connection()` hostname lookup with the existing killable
  resolver subprocess, then connected to its resolved addresses under the same
  monotonic deadline.
- Added regressions for stalled DNS after same-port alias comparison and on the
  normal different-port production probe path. The tests reject any return to
  synchronous `create_connection()` DNS.
- Removed the Phase B test's host-speed-dependent 1.25-second total runtime
  assertion. The test now proves that the slow Phase B request started, the
  absolute-deadline transition was recorded, the worker was contained, and no
  overlapping request remained.
- Corrected the operator procedure to describe the production killable
  subprocess `urlopen` path, including proxy and redirect behavior, separately
  from bounded DNS and TCP handling for topology probes.

## RED evidence

The Python 3.14.4 review report identified the existing 1.25-second assertion as
consistently failing at about 1.80 seconds. The available local interpreter is
Python 3.14.6; the unchanged baseline test passed once in 2.04 seconds, so that
specific host-speed failure was not reproduced locally. The assertion still
measured Phase A subprocess startup and import time outside the Phase B behavior
under test and was removed.

After adding the two DNS regressions and the documentation truth assertions,
before the production and README changes:

```text
python3.14 -m pytest -q \
  tests/test_smoke_profile.py::test_run_smoke_profile_dual_topology_bounds_stalled_same_port_probe_resolution \
  tests/test_smoke_profile.py::test_run_smoke_profile_dual_topology_bounds_stalled_different_port_probe_resolution \
  tests/test_smoke_profile.py::test_startup_procedure_documents_field_specific_service_health

3 failed in 0.24s
```

Both DNS tests failed because production reached synchronous
`socket.create_connection()` instead of the bounded resolver. The documentation
test failed because the README still claimed a direct HTTP operation.

## GREEN evidence

```text
python3.14 -m pytest -q \
  tests/test_smoke_profile.py::test_run_smoke_profile_dual_topology_bounds_stalled_same_port_probe_resolution \
  tests/test_smoke_profile.py::test_run_smoke_profile_dual_topology_bounds_stalled_different_port_probe_resolution \
  tests/test_smoke_profile.py::test_startup_procedure_documents_field_specific_service_health \
  tests/test_smoke_profile.py::test_startup_v2_production_phase_b_enforces_total_deadline_for_slow_body

4 passed in 2.06s
```

```text
python3.14 -m pytest -q tests/test_smoke_profile.py

62 passed in 7.13s
```

```text
git diff --check

PASS (no output)
```

## Boundaries

- Files changed for behavior: `custom_components/helianthus/smoke_profile.py`,
  `tests/test_smoke_profile.py`, and `README.md`.
- Validation used deterministic fakes and local loopback HTTP fixtures only.
- No Home Assistant deployment, gateway or device access, credential use, or
  live-system mutation occurred.
- Per assignment, full CI was not run. Nothing was pushed; no PR, review comment,
  Project item, handoff, or manifest was created or changed.
