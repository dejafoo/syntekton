---
name: durability-state-change
description: Safely modify Product Factory schema migrations, repositories, events, workers, artifacts, backups, recovery, retention, and budget/state transitions. Use whenever a change persists data or must survive interruption and restart.
---

# Durability and state change

Define the aggregate owner, transaction boundary, migration order, recovery
state, and retention consequences before implementation.

## Rules

- Add schema through versioned, checksummed, transactional migrations with
  empty and prior-version upgrade fixtures. Preserve legacy rows unless a
  tested migration explicitly changes their meaning.
- Keep direct database connections inside persistence. Use the aggregate
  repository/serialized actor and verify foreign keys on every connection.
- Write state/event/budget transitions atomically or make recovery and
  idempotency explicit. Do not derive authoritative state from JSONL/events.
- Write blobs through same-filesystem temporary files, verify digest/size, and
  atomically rename. Backups require manifests, checksums, and restore checks.
- Shutdown stops admissions/recovery scanning first, waits cooperatively,
  persists forced recovery if needed, and closes DB resources last.
- Maintenance is dry-run first, backed up before material pruning, scoped to
  durable eligible IDs, and audited append-only.

## Required proof

Provide migration compatibility fixtures and interruption/restart tests for the
affected execution point. Test transaction races, artifact digest failure,
backup/restore, and dry-run versus destructive maintenance parity. State
rollback and operator recovery in the placement note.
