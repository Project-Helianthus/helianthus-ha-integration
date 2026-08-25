# AGENTS

## Scope

This repository is the Home Assistant integration for Helianthus. It is a
protocol-neutral consumer of a stable, public API and maps that API into Home
Assistant config, devices, entities, services, and diagnostics.

Do not embed protocol-specific transport or decoding logic, infer unpublished
API semantics, or make the integration a source of gateway API design. If a
stable public API cannot express a required consumer behavior, stop and request
an API decision rather than creating a private workaround.

## Workflow

1. Create one English GitHub issue for the change.
2. Create `issue/<number>-<short-slug>` from `origin/main`.
3. Make the smallest scoped change and add or update focused automated tests.
4. Run `./scripts/ci_local.sh`, commit, push, and open one PR that links the
   issue.
5. Resolve every valid P0-P2 finding and rerun validation after each fix.
6. Obtain a fresh exact-HEAD `NO_BLOCKING_FINDINGS` review with all required
   checks green.
7. Squash merge, verify remote `main`, and close the issue.

Use public GitHub URLs in tracked documentation. Instructions must remain
usable when this repository is checked out alone and must not depend on external
checkout or machine state.

## Tests and deployment

- Automated tests must use mocks, fixtures, or fakes for Home Assistant and API
  behavior; they must not require a real Home Assistant instance.
- A real-Home-Assistant smoke test is optional supplemental evidence, never a
  mandatory CI or PR gate.
- Never perform a live deployment, installation write, credential change, or
  live-system mutation without explicit operator confirmation at action time.
- Record the command and result for every validation run in the PR.
- Public API or externally visible entity-behavior changes require the
  corresponding public documentation gate.

## Review hygiene

- Keep one active issue and PR for the same repository change.
- Reply to actionable review comments with the result and commit reference.
- Preserve stable public API compatibility and existing entity unique IDs unless
  an approved issue explicitly requires a migration.
