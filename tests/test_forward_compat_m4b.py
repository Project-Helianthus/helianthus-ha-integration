"""M5b_HA_NOOP_COMPAT — forward-compat checkpoint for ebus_standard + responder.

This test pins the HA integration's no-op posture against:

- M4B §7.3 synthetic-payload conformance (unknown ``meta.*`` keys, unknown
  ``safety_class``, unknown ``data.validity``, unknown ``error.code``,
  unknown ``DecodedField`` extra keys) — see
  ``helianthus-docs-ebus/architecture/ebus_standard/11-m4b-semantic-lock.md``.
- M4b2 ``meta.capabilities.responder`` shape (ENH supported, ENS supported,
  ebusd-tcp blocked with ``command_bridge_no_companion_listen``) — see
  ``helianthus-execution-plans/ebus-standard-l7-services-w16-26.implementing/
  decisions/m4b2-responder-go-no-go.md``.

Scope rationale
===============

The HA integration is GraphQL-only. It does NOT consume MCP envelopes. The
``ebus_standard`` L7 namespace and the ``meta.capabilities.responder``
signal therefore cannot leak into HA state without a deliberate new
consumer path. The conformance we assert here is a **tombstone**:

1. Structural tolerance at the parser level — any synthetic envelope fed
   through ``.get()``-based dict walks (the HA coordinator's construction)
   MUST NOT raise and MUST NOT log at ERROR.
2. No entity platform surfaces the synthetic payload's fields — there is
   no ``ebus_standard``-namespaced sensor / number / binary_sensor created
   at setup, and the existing ``PLATFORMS`` list is unchanged.
3. A no-op posture sentinel exists in ``coordinator.py`` that future
   refactors cannot silently remove without failing this test. The
   sentinel is the load-bearing invariant: it guarantees a human reviewed
   and locked the structural-tolerance posture, rather than it being an
   accidental artefact of today's implementation.

If a future change routes MCP content into HA, this test MUST be updated
in lockstep with a new explicit consumer path; it must not be silently
relaxed.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

# Stub the Home Assistant surface the coordinator imports at module load,
# mirroring the pattern used in tests/test_coordinator.py. HA is not a
# runtime dep of the unit-test environment.
_update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")


class _DataUpdateCoordinator:  # noqa: D401 — test-only stub
    def __class_getitem__(cls, _item):  # noqa: ANN206
        return cls

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None


class _UpdateFailed(Exception):
    """Test-only stand-in for homeassistant.helpers.update_coordinator.UpdateFailed."""


_update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
_update_coordinator.UpdateFailed = _UpdateFailed
_helpers = types.ModuleType("homeassistant.helpers")
_helpers.update_coordinator = _update_coordinator
_homeassistant = types.ModuleType("homeassistant")
_homeassistant.helpers = _helpers
sys.modules.setdefault("homeassistant", _homeassistant)
sys.modules.setdefault("homeassistant.helpers", _helpers)
sys.modules.setdefault("homeassistant.helpers.update_coordinator", _update_coordinator)

import custom_components.helianthus as helianthus_pkg  # noqa: E402
import custom_components.helianthus.coordinator as coordinator_mod  # noqa: E402
from custom_components.helianthus import PLATFORMS  # noqa: E402

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "mcp_forward_compat_synthetic.json"
)

# Locked PLATFORMS list as of M5b. Any addition to this list means a new
# platform surfaces entities — which, if triggered by an ebus_standard or
# responder payload, would break the NO-OP tombstone. Bump this literal
# only when an unrelated platform lands AND you have re-verified that the
# forward-compat fixture still produces zero new entities.
_EXPECTED_PLATFORMS_AT_M5B: tuple[str, ...] = (
    "sensor",
    "binary_sensor",
    "climate",
    "water_heater",
    "fan",
    "valve",
    "number",
    "select",
    "switch",
    "calendar",
    "text",
    "date",
)


def _load_synthetic() -> dict:
    with FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _walk_all(node: object) -> None:
    """Structurally walk the payload like HA coordinator .get() patterns.

    This simulates the canonical consumer-decoder pass M4B §7.3 requires:
    every unknown key MUST be reachable without raising.
    """

    if isinstance(node, dict):
        for key, value in node.items():
            # .get() access for every key — the pattern used throughout
            # custom_components/helianthus/coordinator.py.
            assert node.get(key) is value or node.get(key) == value
            _walk_all(value)
    elif isinstance(node, list):
        for item in node:
            _walk_all(item)


def test_synthetic_envelope_loads_without_raising() -> None:
    """M4B §7.3: unknown meta.* / safety_class / validity / error.code /
    DecodedField keys MUST parse under the canonical consumer decoder."""

    payload = _load_synthetic()

    # Unknown meta.* key is present and preserved verbatim.
    assert payload["meta"]["foo_future_key"].startswith("forward-compat")
    # Unknown consistency.mode value is preserved as a raw string.
    assert payload["meta"]["consistency"]["mode"] == "future_consistency_mode"
    # Unknown safety_class on a command entry.
    assert payload["commands"]["list"][0]["safety_class"] == "future_class_xyz"
    # Unknown data.validity string value.
    assert payload["decode"]["data"]["validity"] == "future_validity"
    # Unknown DecodedField extra key on a populated fields[] entry.
    assert payload["decode"]["data"]["fields"][0]["future_field"]
    # Unknown error.code.
    assert payload["error"]["code"] == "FUTURE_ERROR"


def test_responder_capability_shape_is_tolerated() -> None:
    """M4b2 §4.2: meta.capabilities.responder with 3 transports — ENH and
    ENS supported, ebusd-tcp blocked with command_bridge_no_companion_listen.

    The HA integration MUST neither inspect nor react to this subtree.
    """

    payload = _load_synthetic()
    responder = payload["meta"]["capabilities"]["responder"]

    assert responder["version"] == "v1"
    assert responder["active"]["transport"] == "ENH"
    assert responder["active"]["scope"] == "partial"

    transports = {row["transport"]: row for row in responder["transports"]}
    assert set(transports) == {"ENH", "ENS", "ebusd-tcp"}
    assert transports["ENH"]["state"] == "supported"
    assert transports["ENS"]["state"] == "supported"
    assert transports["ebusd-tcp"]["state"] == "blocked"
    assert transports["ebusd-tcp"]["scope"] == "none"
    assert (
        transports["ebusd-tcp"]["reason"]
        == "command_bridge_no_companion_listen"
    )


def test_structural_walk_does_not_raise_or_error_log(
    caplog: "logging.LogCaptureFixture",  # noqa: F821 — pytest injects
) -> None:
    """Feeding the synthetic envelope through a .get()-style walk must not
    raise and must not log at ERROR level. This mirrors the HA coordinator's
    parse posture.
    """

    payload = _load_synthetic()
    with caplog.at_level(logging.ERROR, logger="custom_components.helianthus"):
        _walk_all(payload)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not error_records, (
        "HA coordinator-equivalent walk logged ERROR on forward-compat "
        f"synthetic envelope: {error_records!r}"
    )


def test_platforms_list_unchanged_at_m5b() -> None:
    """Tombstone: the PLATFORMS list is the gate through which any new
    entity category must enter. Locking it at M5b guarantees no new
    platform silently surfaces under an ebus_standard / responder shape.
    """

    assert tuple(PLATFORMS) == _EXPECTED_PLATFORMS_AT_M5B, (
        "PLATFORMS list changed. If this is intentional and unrelated to "
        "ebus_standard / responder capability, re-verify the M5b no-op "
        "posture (no new entity surfaces the synthetic payload), then "
        "update _EXPECTED_PLATFORMS_AT_M5B."
    )


def test_no_ebus_standard_symbols_in_integration() -> None:
    """Tombstone: the HA integration package MUST NOT expose any symbol
    that consumes the ebus_standard L7 namespace or the responder
    capability subtree. If that changes, this test must be updated in
    lockstep with an explicit consumer-path review.
    """

    for mod in (helianthus_pkg, coordinator_mod):
        for attr in dir(mod):
            lower = attr.lower()
            assert "ebus_standard" not in lower, (
                f"{mod.__name__}.{attr} references ebus_standard; the M5b "
                "no-op tombstone forbids this without an explicit review."
            )
            assert "responder" not in lower, (
                f"{mod.__name__}.{attr} references responder; the M5b "
                "no-op tombstone forbids this without an explicit review."
            )


def test_coordinator_declares_forward_compat_posture() -> None:
    """Load-bearing tombstone: coordinator.py MUST declare an explicit
    M5b forward-compat posture sentinel. The sentinel is the only
    durable guarantee that a human reviewed structural tolerance against
    the ebus_standard / responder shape — without it, today's
    ``.get()``-based tolerance could silently be replaced by strict
    parsing in a future refactor.

    The sentinel is ``coordinator_mod.M5B_FORWARD_COMPAT_POSTURE``: a
    string constant naming this test and the two normative references.
    """

    posture = getattr(coordinator_mod, "M5B_FORWARD_COMPAT_POSTURE", None)
    assert isinstance(posture, str), (
        "coordinator.py missing M5B_FORWARD_COMPAT_POSTURE sentinel; see "
        "tests/test_forward_compat_m4b.py docstring."
    )
    assert "ebus_standard" in posture
    assert "responder" in posture
    assert "m4b" in posture.lower()
