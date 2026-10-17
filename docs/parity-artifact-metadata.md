# Parity artifact metadata validation

The Home Assistant integration validates gateway parity artifacts locally for
rollout readiness. Both `source_ref` and `generated_at` must be JSON strings
containing at least one non-whitespace character after trimming. Missing fields,
`null`, numbers, booleans, lists, objects, empty strings and whitespace-only
strings are rejected. Each diagnostic names its field (`source_ref missing` or
`generated_at missing`); metadata and gate errors accumulate in validation order.

Text is opaque: this check does not parse timestamps or require a commit digest
or reference syntax. A synthetic artifact can use `synthetic-reference` and
`2000-01-01T00:00:00Z`. Validation does not rewrite the supplied artifact.

Both required gates, `parity_contract` and `tool_classification`, must report
`pass`. Existing status normalization trims whitespace and ignores case, so
`pass`, `PASS` and ` PaSs ` are accepted. Failed, empty and null statuses are
rejected. Repository matching and existing trust checks remain unchanged.

This is consumer-side artifact validation, not a new runtime authorization gate
or evidence of physical-device qualification. It adds no gateway protocol
contract or stability promise; Helianthus remains pre-stable until 1.0.

See the [validator](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/parity_gate.py)
and [existing parity tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_parity_gate.py).
