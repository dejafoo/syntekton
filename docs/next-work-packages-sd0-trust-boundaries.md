# SD0 — Trust-boundary closure

**Status:** `[ ]` planned. **Gate:** G0. **Findings:** F-01–F-04.  
**Depends on:** baseline in the [master tracker](next-work-packages-sustainable-development.md).  
**Non-goals:** new deployment targets, connector authority, workflow packs, tenants, or remote dashboard support.

## Outcome and sequencing

Durable records, rather than evidence-shaped caller fields, become authoritative for cross-run handoffs and external actions. Only a policy-bounded repository inventory may form repository context. Safety refusals occur before model spend, tools, or connector calls.

1. SD0.A creates the migration runner and fixtures.
2. SD0.B and SD0.C can proceed in parallel after A, with separate handoff/artifact and approval/deployment ownership.
3. SD0.D and SD0.E can proceed in parallel.
4. Integrate, run G0, and document operator behavior.

Keep migrations additive except for renaming the unused approval table. Do not combine this work with general coordinator decomposition or host/v2.

## SD0.A — Versioned migration bootstrap

- [ ] Introduce `schema_migrations(version, name, checksum, applied_at)` and one migration runner.
- [ ] Baseline existing databases without rebuilding: inspect expected current tables before recording baseline state.
- [ ] Reject unknown/partial legacy schemas with backup-and-upgrade guidance.
- [ ] Run later migrations transactionally and reject applied-version checksum drift.
- [ ] Rename the unused approval table to `legacy_approvals`; preserve all rows and never treat them as deployment authority.
- [ ] Add empty-database and current pre-SD0 upgrade fixtures, including failed/interrupted migration characterization where supported.

**Tests:** ordering, baseline idempotence, unknown schema refusal, drift refusal, empty and upgrade fixtures, foreign keys.  
**Must not:** retain an unversioned startup DDL path after cutover or rebuild existing data.

## SD0.B — Authoritative handoffs

Add the following durable records.

```text
HandoffRecord
  handoff_id
  producer_artifact_instance_id
  producer_run_id / producer_task_id
  sha256
  schema_id / schema_version
  role
  state: draft | evidence_complete | approved | superseded
  timestamps
  superseded_by
  metadata

HandoffConsumption
  consumer_run_id
  handoff_id
  producer_artifact_instance_id
  consumer_artifact_instance_id
  state_at_resolution
  resolved_at
```

- [ ] Create records only from persisted artifact instances.
- [ ] Allow system validation to promote `draft → evidence_complete`; only an authenticated operator mutation promotes `evidence_complete → approved`.
- [ ] Make `approved → superseded` terminal; prohibit backward transitions.
- [ ] Treat v1 `HandoffRef.state` as an assertion only, never authorization.
- [ ] Resolve producer run/task/artifact instance/digest/schema/role/stored state on submit and again on resume.
- [ ] Materialize verified bytes to the consumer immutable input store; create a child artifact instance whose parent is the producer; write consumption durably.
- [ ] Preserve or tighten producer classification, capture, and retention policy.
- [ ] Pass typed `ResolvedHandoff` objects to handlers and prohibit raw request handoffs as trusted evidence.

### Read and mutation surfaces

- [ ] `GET /api/v1/runs/{run_id}/handoffs`
- [ ] `POST /api/v1/handoffs/{handoff_id}/approve`
- [ ] `POST /api/v1/handoffs/{handoff_id}/supersede`
- [ ] Equivalent host CLI operations with an explicit permission prompt for approval.
- [ ] MCP/OpenCode may list/request approval, but a model-facing tool can never decide it without the host prompt.

**Tests/events:** forged producer/run/digest/schema/role/state, cross-run content, resume re-resolution, state transitions, concurrent consumption, policy propagation, and pre-spend refusal. Emit creation, promotion, consumption, supersession, and refusal events and project stored state plus artifact lineage.

## SD0.C — Authoritative external-action approval

```text
ActionApproval
  approval_id
  action_type
  subject_run_id
  subject_artifact_instance_id
  action_fingerprint
  status: pending | approved | rejected | expired | revoked | consumed
  actor
  payload
  created_at / decided_at / expires_at / consumed_at
  consumed_by_run_id
  reconciliation
```

- [ ] Canonicalize/hash deployment actions over release handoff, release-plan digest, artifact digest, target, change window, and idempotency key.
- [ ] Store actor as `local_operator` or non-secret token identity/fingerprint; never store bearer tokens.
- [ ] Add `POST /api/v1/action-approvals`, `GET /api/v1/action-approvals/{approval_id}`, and `POST /api/v1/action-approvals/{approval_id}/decision`.
- [ ] Creation produces `pending`; approval/rejection are separate authenticated mutations and support expiry/revocation.
- [ ] Let execution accept only an approval ID and retrieve every authoritative field from `ActionApproval`.
- [ ] Permit replay only for identical fingerprints and idempotency reconciliation; changed fields require a new approval.
- [ ] Remove caller-settable `_approval_binding_verified`.
- [ ] Keep deployment disabled unless the new verifier is active; simulated staging remains a disposable-test fixture.

**Tests:** forged/missing/expired/revoked/rejected/consumed approval; changed action fields; reconciliation; actor redaction; races; connector-call ordering; legacy row preservation.  
**Must not:** trust pack-input booleans or reinterpret legacy rows.

## SD0.D — Stream authentication closure

- [ ] Remove `/api/v1/events/ws`, implementation, imports, tests, and documentation.
- [ ] Keep cursor-resumable SSE as the sole live protocol.
- [ ] Add a security test enumerating HTTP/SSE routes and proving required auth in remote mode.
- [ ] Do not add query-string token authentication.

**Tests:** route-inventory regression, local loopback SSE, remote authenticated SSE, unauthenticated rejection, cursor resume/reconnect.

## SD0.E — Safe repository inventory

- [ ] Create one `SafeRepositoryInventory` per repository snapshot and policy digest.
- [ ] For Git, enumerate tracked and explicitly admitted untracked files through Git; for non-Git, use a no-follow walk.
- [ ] Reject symlink files/directories, special files, canonical-path escapes, prohibited globs, binary content, and oversized files.
- [ ] Exclude `.git`, virtual environments, caches, `node_modules`, `dist`, `build`, generated coverage, and configured prohibited paths.
- [ ] Enforce file-count, per-file-byte, total-byte, and scan-duration ceilings.
- [ ] Persist exclusions/truncation in the run input manifest.
- [ ] Route excerpts, file lists, stack detection, and summaries through this inventory.
- [ ] Defer reusable caching to SD8; any future cache keys on snapshot plus policy digest.

**Tests:** symlinks, ignored sensitive content, escapes, prohibited globs, binary/ceiling/truncation, Git tracked/admitted-untracked behavior, no-follow behavior, and manifest evidence.

## G0 exit checklist

- [ ] Forged handoffs/approvals fail before model spend or connector calls.
- [ ] Symlinked/prohibited paths cannot reach prompts or derived context.
- [ ] No unauthenticated live stream remains.
- [ ] Deployment remains disabled without authoritative approval verification.
- [ ] Python, dashboard, plugin, package, migration, API compatibility, and targeted security suites pass.
- [ ] Link implementation, hermetic, integration, and operational evidence in the master tracker.

## PR evidence and constraints

Each slice starts with characterization/failing tests, specifies API/host compatibility, emits diagnosable events/projections, and supplies migration rollback/recovery evidence. Never accept a browser filesystem path as authority, weaken policy on consumer materialization, or add an alternative approval stream.
