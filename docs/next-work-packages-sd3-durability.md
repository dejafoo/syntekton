# SD3 — Persistence and unattended operation

**Status:** `[ ]` planned. **Gate:** G3 (jointly with SD4 and SD5). **Findings:** F-09–F-12.  
**Depends on:** G1; coordinate migration work with SD2 and CI foundations with SD5. **Compatibility:** retain host/v1 through SD3.

## Outcome

Make the SQLite/artifact system recoverable under restart, interruption, backup/restore, and bounded disk conditions. Persistence transitions have explicit ownership and transactions; no worker may outlive the resources it needs to finish or recover safely.

## SD3.A — Repository and transaction boundary

- [ ] Extend the SD0 migration runner for all durable changes.
- [ ] Split access into run/task, event, artifact/handoff, approval, worker, and evaluation aggregate repositories.
- [ ] Use connection-per-thread or an equivalently explicit serialized database actor; do not share implicit connection state.
- [ ] Define transaction boundaries for run/task/event/budget transitions and document lock/retry behavior.
- [ ] Enable and verify foreign keys on every connection.
- [ ] Remove direct `db.conn` access from evaluation and application services.
- [ ] Remove legacy evaluation dual writes only after a compatibility export/reader path has been verified.

**Tests:** connection isolation, FK enforcement, transition atomicity, event/budget consistency, concurrent worker operations, migration compatibility, and legacy export parity.

## SD3.B — Graceful worker shutdown and recovery

- [ ] Stop admissions and recovery scanning before signalling workers.
- [ ] Request cooperative shutdown, wait for active workers for a configurable grace period, then record forced-shutdown/recovery-required outcomes.
- [ ] Close heartbeat threads and database connections only after workers finish or their durable recovery state is written.
- [ ] Prove exactly-once recovery for planning, model waits, tools, validation, and deployment reconciliation.

**Tests:** controlled shutdown at each named execution point, repeated restart, race with admission, heartbeat termination, budget/attempt idempotence, and durable recovery projection.  
**Must not:** terminate a process in a way that allows an active worker to write through a closed connection or replay a side effect without reconciliation.

## SD3.C — Artifact and backup integrity

- [ ] Write blobs through same-filesystem temporary files, verify digest/size, fsync as appropriate, and atomically rename.
- [ ] Verify handoff bytes at promotion and consumption.
- [ ] Give backups per-file checksums and a captured high-water mark.
- [ ] Include explicit manifests for runs, artifacts, content, uploads, ops, and experiments; document configuration/skill/profile backup separately.
- [ ] Automate restore validation against database references and report missing/orphaned/corrupt records.

**Tests:** interrupted write, digest mismatch, atomic visibility, corrupted/missing blob, backup manifest mismatch, restore to clean root, and high-water consistency.

## SD3.D — Retention and maintenance

Add a dry-run-first maintenance service and CLI.

- [ ] Inventory by run, age, retention class, size, and reachability.
- [ ] Pin/unpin runs and experiments.
- [ ] Prune explicit run IDs or policy-selected candidates.
- [ ] Garbage-collect unreachable artifact/content blobs; remove stale scratch, uploads, and worktrees.
- [ ] Emit disk-warning and stop-admission thresholds.
- [ ] Perform WAL checkpoint and optional maintenance-window `VACUUM`.
- [ ] Write an append-only maintenance audit.
- [ ] Require an eligible backup before material pruning and never accept unresolved filesystem paths as deletion targets.

**Tests:** dry-run versus execute parity, pinned data, reachable content, path validation, backup prerequisite, crash/retry, audit immutability, disk thresholds, and worktree ownership.

## Integration plan and G3 contribution

Run package-installed restart/recovery and backup/restore scenarios against a real temporary data root, with controlled fake providers/connectors. The scheduled CI gate owns shutdown, restart, and restore. Operational evidence records a backup/restore drill and a supervised shutdown; it does not claim an untested production deployment.

G3 contribution is complete when repositories own transactions, foreign keys are proven on every connection, an interrupted worker recovers exactly once, artifact writes/backups verify integrity, and retention cannot delete outside its explicit eligible set.

## Must not

Do not replace SQLite, add distributed scheduling, weaken immutable artifact semantics, delete from broad/unresolved paths, or couple maintenance deletion to a browser request.
