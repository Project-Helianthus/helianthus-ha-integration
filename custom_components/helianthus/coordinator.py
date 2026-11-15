"""Helpers shared by the domain coordinator modules."""

from __future__ import annotations

from .coordinator_availability import _is_missing_field_error


# ---------------------------------------------------------------------------
# M5b forward-compat posture (ebus_standard L7 namespace + responder signal)
# ---------------------------------------------------------------------------
#
# The HA integration is GraphQL-only. It does NOT consume MCP envelopes and
# does NOT read anything under the ``ebus_standard`` L7 namespace or the
# ``meta.capabilities.responder`` subtree defined by:
#
# - helianthus-docs-ebus/architecture/ebus_standard/11-m4b-semantic-lock.md
#   (M4B semantic lock, §7.3 forward-compat consumer golden — unknown
#   ``meta.*`` keys, unknown ``safety_class``, unknown ``data.validity``,
#   unknown ``error.code``, unknown ``DecodedField`` extra keys MUST parse
#   without error under the canonical consumer decoder).
# - helianthus-execution-plans/ebus-standard-l7-services-w16-26.implementing/
#   decisions/m4b2-responder-go-no-go.md (M4b2 responder capability shape,
#   §4.2 / §4.3 fail-closed consumer rule — ENH supported, ENS supported,
#   ebusd-tcp blocked with ``command_bridge_no_companion_listen``).
#
# Structural tolerance is achieved by construction: every dict access in
# the domain coordinator modules uses ``.get()`` with an explicit default
# and walks payloads defensively. No Pydantic / strict schema decoder is
# used. New unknown keys therefore flow through silently.
#
# The constant below is a load-bearing sentinel: it is the only durable
# guarantee that a human reviewed and locked this posture. Future
# refactors MUST NOT remove it without also updating
# ``tests/test_forward_compat_m4b.py`` and re-verifying that the synthetic
# envelope in ``tests/fixtures/mcp_forward_compat_synthetic.json``
# continues to yield zero new entities at ``async_setup_entry`` time.
M5B_FORWARD_COMPAT_POSTURE: str = (
    "M5b no-op checkpoint: the HA integration is a tombstone consumer "
    "for the ebus_standard L7 namespace and for meta.capabilities.responder. "
    "Structural tolerance via .get()-based dict walks — no strict schema, "
    "no Pydantic decoder. See M4B §7.3 semantic lock and M4b2 §4.2/§4.3 "
    "decision doc; locked by tests/test_forward_compat_m4b.py."
)


_RADIO_GROUP_ZONE_VRC = 0x09
_RADIO_GROUP_ZONE_VR92 = 0x0A
_RADIO_GROUP_INVENTORY = 0x0C


def is_missing_field_error(errors: object, fields: list[str]) -> bool:
    """Return whether a GraphQL error reports one of the fields as unknown."""
    return _is_missing_field_error(errors, fields)
