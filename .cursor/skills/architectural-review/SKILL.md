---
name: architectural-review
description: Review Product Factory changes and completion claims for architectural guardrail violations. Use for substantial features, refactors, cross-layer work, whole-codebase assessments, PRs, and hand-offs touching orchestration, persistence, authority, protocols, verification, documentation, or product proof.
---

# Architectural review

Review the diff, the resulting owning modules and dependency graph, tests, and
completion evidence—not an intended design summary. Read every other repository
skill triggered by the change before judging it.

## Procedure

1. Identify changed concerns, authoritative sources, and public/durable
   compatibility surfaces.
2. Inspect the complete affected owner and its callers, not only changed lines.
3. Compare implementation placement with the declared boundary and dependency
   direction.
4. Run or inspect negative, migration, recovery, client, browser, package, and
   live proof proportionate to the claim.
5. Classify evidence as implemented, hermetic, integration, operational, or not
   verified.

## Blocking checks

- Implementation behavior, dependency construction, or private-helper growth
  in `RunCoordinator`, `RunLifecycleEngine`, `HostService`, or another shared
  replacement monolith instead of a named owner.
- Lifecycle or application services importing concrete brokers, workflow
  handlers, capability executors, repository internals, or other forbidden
  layers contrary to the declared dependency direction.
- New named workflow/capability branch in shared runtime code, or duplicated
  policy/mapping truth or broad authority union outside a registry and
  per-capability compiled pack policy.
- Executor completion conflated with domain outcome, duplicated task-status
  predicates, or a terminal status that can deadlock dependencies.
- A capability without complete descriptor/executor/parser/result/evaluation
  chain, or a success-shaped fallback.
- Caller/event/model data used as authority without durable re-resolution.
- Direct persistence access outside repositories, a non-transactional coupled
  state/event/budget update, or no migration fixture.
- Client mutation bypassing the application service, unversioned contract,
  unauthenticated stream, server-only protocol adoption, implicit unsafe
  fallback, or dashboard mutation/token storage.
- A required check that soft-skips, a unit test used to claim a real process or
  browser boundary, stale architecture documentation, or a tracker/release/
  local-model claim stronger than its evidence.

## Review output

Verify the placement note required by root `AGENTS.md`. Confirm tests prove the
boundary and negative cases, not only the happy path or a selected-file string
check. Report each finding with severity, concrete path, violated guardrail,
impact, smallest corrective action, and verification status. Do not use line
count alone as an architecture gate. The sole temporary exception is an ADR
with owner, removal condition, and date.
