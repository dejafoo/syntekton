---
name: durability-state-change
description: Safely modify Product Factory migrations, repositories, units of work, events, workers, artifacts, handoff or approval transitions, backups, recovery, retention, and budget/state changes. Use whenever work persists data, couples multiple records, or must survive interruption and restart.
---

# Durability and state change

Before editing, list the authoritative records, aggregate owner, transaction
boundary, migration order, blob ordering, recovery state, and retention
consequences.

## Rules

- Add schema through versioned, checksummed, transactional migrations with
  empty and prior-version upgrade fixtures. Preserve legacy rows unless a
  tested migration explicitly changes their meaning.
- Keep direct database connections inside persistence. Use the aggregate
  repository/serialized actor and verify foreign keys on every connection.
- Use one explicit unit of work for coupled run/task/event/budget, artifact/
  lineage, handoff/consumption, and approval/action-intent transitions.
  Repositories participating in it must not commit independently.
- Commit authoritative state and its event together. Publish SSE only after
  commit. Do not derive authoritative state from JSONL or event replay.
- Write blobs through same-filesystem temporary files, verify digest/size, and
  atomically rename before recording the reference; define orphan cleanup and
  recovery. Backups require manifests, checksums, a high-water mark, and
  database-to-blob restore checks.
- Record state-machine decisions in an append-only transition/audit record and
  update current state in the same transaction.
- Shutdown stops admissions/recovery scanning first, waits cooperatively,
  persists forced recovery if needed, and closes DB resources last.
- Maintenance is dry-run first, backed up before material pruning, scoped to
  durable eligible IDs, and audited append-only.

## Required proof

Provide empty/current upgrade fixtures and fault injection before and after the
affected commit or external boundary. Prove no duplicate action, double budget
settlement, state/event contradiction, or successful finalization with missing
evidence. Test races, digest failure, restart, backup/restore, and dry-run versus
destructive maintenance parity. State rollback and operator recovery in the
placement note.
