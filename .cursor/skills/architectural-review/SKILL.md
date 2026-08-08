---
name: architectural-review
description: Review Product Factory implementation changes for architectural guardrail violations before completion. Use for substantial features, refactors, cross-layer changes, or PR and hand-off review, especially changes touching orchestration, persistence, authority, or public clients.
---

# Architectural review

Review the diff and its tests, not an intended design summary. Report each
finding with a concrete path, guardrail, impact, and smallest corrective action.

## Blocking checks

- New behavior or private-helper growth in `RunCoordinator` instead of a named
  owning service, executor, registry, repository, or adapter.
- New named workflow/capability branch in shared runtime code, or duplicated
  policy/mapping truth outside a registry or compiled pack policy.
- A capability without complete descriptor/executor/parser/result/evaluation
  chain, or a success-shaped fallback.
- Caller/event/model data used as authority without durable re-resolution.
- Direct persistence access outside repositories, a non-transactional coupled
  state/event/budget update, or no migration fixture.
- Client mutation bypassing the application service, unversioned contract,
  unauthenticated stream, or dashboard mutation/token storage.

## Review output

Verify the placement note required by root `AGENTS.md`. Confirm tests prove the
boundary, not only the happy path. Blocking findings need a path and relevant
guardrail; style-only observations are non-blocking. The sole temporary
exception is an ADR with owner and removal date.
