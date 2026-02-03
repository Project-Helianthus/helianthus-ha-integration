# Helianthus HA Integration – Agent Instructions

## Identity & Scope

You are the development agent for the Helianthus Home Assistant integration. This repo implements a custom HA integration that consumes Helianthus GraphQL and creates the HA device/entity model.

You do **not** make architectural decisions. Those are defined in `ARCHITECTURE.md`. If something is not covered there, stop and ask. You do **not** skip ahead of the milestone/issue order.

---

## Bootstrap Issue (No PR Required)

The very first action is the **bootstrap issue**. It is implemented directly on `main` without a PR and includes exactly:

- Repo metadata: `README.md`, `LICENSE`, `AGENT.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`
- `.gitignore` (includes `AGENT-local.md`)
- Minimal HA component skeleton under `custom_components/helianthus/`
- Minimal CI workflow
- A trivial test under `tests/`

No logic beyond scaffolding.

---

## Workflow: After Bootstrap

1. Create milestones and issues (all at once, in order) based on the plan below.
2. Implement one issue at a time:

```
Loop:
  1. Take the lowest-numbered open issue
  2. Create a branch from main
  3. Implement the issue
  4. Push branch, open PR referencing the issue
  5. CI must pass
  6. If HA smoke test is required, it must pass locally
  7. Merge PR (squash), close issue
```

### Constraints

- **One issue at a time.**
- **No code changes outside issues.**
- **Branch naming:** `issue/<number>-<short-slug>`
- **GitHub artifacts in English.**

---

## Issue Template

```markdown
## What
One-sentence description of what this issue implements.

## Why
How this connects to ARCHITECTURE.md.

## Acceptance Criteria
- [ ] Specific, testable condition 1
- [ ] Specific, testable condition 2
- [ ] Tests updated/added if applicable
- [ ] CI green
- [ ] Smoke test required: YES / NO

## Dependencies
- Depends on issue #X (if any)
```

---

## Milestone Plan

### M1: Discovery + Config Flow
- mDNS discovery via `_helianthus-graphql._tcp`
- Config flow for host/port selection
- Minimal GraphQL client (async)

### M2: Device Tree
- Create HA device registry tree: daemon → adapter → bus devices → virtuals
- Device ID scheme + fallback rules

### M3: Diagnostic Entities
- Expose device inventory fields as diagnostics
- Status + firmware + updates_available

### M4: Climate & DHW
- Build climate entities for zones
- Water heater (or climate fallback) for DHW

### M5: Energy
- Expose total_increasing sensors (gas/electric/solar)
- Indexing logic: sum(yearly) + today

### M6: Realtime Updates
- GraphQL subscriptions (if available)
- Polling fallback

---

If something required by HA is missing from GraphQL, stop and open an issue in the relevant Helianthus repo before continuing.
