# MCP-first Rollout Guardrails (HA Integration)

## Policy

Home Assistant capability rollout is blocked until gateway parity artifacts are green.
No new consumer-facing capability may be enabled while any blocker below is active.

## Blocker Map (Gateway Output -> HA Blocker)

| Blocker ID | Gateway artifact field | Required value | Effect in HA integration |
|---|---|---|---|
| `BLOCKER_GATEWAY_SOURCE_MISMATCH` | `source_repo` | `Project-Helianthus/helianthus-ebusgateway` | Rollout blocked; artifact rejected |
| `BLOCKER_GATEWAY_SOURCE_REF_MISSING` | `source_ref` | non-empty | Rollout blocked; artifact rejected |
| `BLOCKER_GATEWAY_PARITY_CONTRACT` | `gates.parity_contract.status` | `pass` | Rollout blocked; parity gate not satisfied |
| `BLOCKER_GATEWAY_TOOL_CLASSIFICATION` | `gates.tool_classification.status` | `pass` | Rollout blocked; cleanup/classification gate not satisfied |

## Enforcement Path

The guardrail is enforced via:

- `custom_components/helianthus/parity_gate.py`
- `scripts/check_gateway_parity_gate.py`
- `scripts/run_post_parity_adoption_checks.py`
- CI profile checks (`scripts/ci_local.sh` and `.github/workflows/ci.yml`)

## Operator Check

Run before enabling new HA capabilities:

```bash
python3 scripts/check_gateway_parity_gate.py \
  --artifact tests/fixtures/gateway_parity_artifact_pass.json
```

A non-zero exit code means rollout remains blocked.

For adopted HA capabilities, run guarded tests:

```bash
python3 scripts/run_post_parity_adoption_checks.py \
  --artifact tests/fixtures/gateway_parity_artifact_pass.json
```
