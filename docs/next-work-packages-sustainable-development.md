# Sustainable development program

**Status:** `[~]` — program defined; implementation has not started.  
**Source:** [Sustainable-development handover](handover_sustainable_development.md).  
**Scope:** make the existing single-user, private-network, SQLite-based product supportable and honestly executable. This program does not add workflow packs, connector authority, deployment targets, multi-tenancy, distributed scheduling, a replacement CLI, or a backend-for-frontend.

## Tracker semantics

- `[ ]` planned — no complete implementation evidence.
- `[~]` in progress — implementation is incomplete.
- `[x]` complete — package exit evidence is linked and its gate has passed.

Completion requires four distinct evidence levels; do not substitute one for another.

| Evidence state | Meaning |
| --- | --- |
| Implemented | Reviewed code, schema, documentation, and migration changes exist. |
| Hermetically verified | Unit, contract, migration, and deterministic integration tests pass without a live-model or connector claim. |
| Integration verified | The packaged application exercises real boundaries such as worker, API/SSE, browser, restart, or restore. |
| Operationally proven | A controlled, environment-owned run establishes the stated outcome and limits. |

Store completion evidence in `docs/evidence/sustainable-development/<package>/`, or link an immutable CI/run artifact from there. Include the commit, commands, fixtures/corpus, and result summary.

## Dependency graph and gates

```text
Baseline verification
        |
        v
SD0 Trust-boundary closure
        | G0: security gate
        v
SD1 Executor truth
        | G1: every capability executes honestly
        v
SD2 Kernel decomposition
        | G2: coordinator is a lifecycle facade
        +----------------+----------------+----------------+
        v                v                v
SD3 Durability      SD4 Protocol      SD5 Build / CI
        +----------------+----------------+----------------+
                         | G3: supportable platform
                         v
                 SD6 Real evaluation
                         | G4: measured product proof
                         v
                 SD7 Simplification
                         v
                 SD8 Optimization
```

| Package | Status | Findings | Gate | Playbook |
| --- | --- | --- | --- | --- |
| Baseline | `[ ]` | F-01–F-26 | entry | this tracker |
| SD0 — Trust boundaries | `[ ]` | F-01–F-04 | G0 | [SD0](next-work-packages-sd0-trust-boundaries.md) |
| SD1 — Executor truth | `[ ]` | F-05–F-07 | G1 | [SD1](next-work-packages-sd1-executor-truth.md) |
| SD2 — Kernel decomposition | `[ ]` | F-06–F-08, F-22 | G2 | [SD2](next-work-packages-sd2-kernel-decomposition.md) |
| SD3 — Durability | `[ ]` | F-09–F-12 | G3 | [SD3](next-work-packages-sd3-durability.md) |
| SD4 — Protocol and clients | `[ ]` | F-13–F-18 | G3 | [SD4](next-work-packages-sd4-protocol-clients.md) |
| SD5 — Release engineering | `[ ]` | F-17, F-23, F-24 | G3 | [SD5](next-work-packages-sd5-release-engineering.md) |
| SD6 — Evaluation | `[ ]` | F-19, F-20 | G4 | [SD6](next-work-packages-sd6-evaluation.md) |
| SD7 — Simplification/governance | `[ ]` | F-03, F-05, F-13, F-21, F-22, F-25, F-26 | post-G4 | [SD7/SD8](next-work-packages-sd7-sd8-simplification-performance.md) |
| SD8 — Performance | `[ ]` | F-04, F-12, F-19, F-20 | post-G4 | [SD7/SD8](next-work-packages-sd7-sd8-simplification-performance.md) |

Finding IDs refer to the handover. Assignment is ownership, not the only possible dependency.

## Program controls

- `staging_deploy` stays disabled outside disposable tests until G0.
- Feature-pack expansion is frozen until G1.
- SD0 and SD1 land as short reviewable PR stacks, separate from coordinator cleanup.
- After G1, SD2, SD3 persistence foundations, and SD5 CI foundations may proceed in parallel only with declared file ownership and migration order.
- SD4 begins after SD2 establishes the host application-service boundary. SD6 may prepare fixtures earlier but promotion runs start after G1.
- SD7 removal waits for replacement paths, compatibility tests, and deprecation telemetry. SD8 requires SD6 measurements; speculative optimization is prohibited.
- Preserve `host/v1` through SD0–SD3. SD4 introduces `host/v2`; v0.2 supplies safety-compatible v1, v0.3 prefers v2 and writes durable v1 deprecation warnings, and v0.4 removes v1/aliases unless an explicit support decision extends them.

## Master checklist

### Baseline

- [ ] Capture starting commit, dependency graph, database schema, route list, pack registry, and active client versions.
- [ ] Archive baseline results and warnings for the commands below.
- [ ] Create evidence directories and name code/test/documentation owners before parallel streams.
- [ ] Freeze empty DB, current pre-SD0 DB, host/v1, dashboard package, and OpenCode plugin compatibility fixtures.

### Gate checklists

- [ ] **G0:** forged handoffs/approvals fail before spend or connector calls; repository context is safe; remote streams are authenticated; deployment is disabled without the new verifier.
- [ ] **G1:** each capability has a real executor path and cannot succeed from a stub or caller-supplied evidence-shaped field.
- [ ] **G2:** `RunCoordinator` is a compatibility lifecycle facade; registry/pack policy drives extensibility.
- [ ] **G3:** storage/recovery, clients, packages, and CI support unattended, diagnosable operation.
- [ ] **G4:** controlled real-task evidence supports a local-first default or records why it does not.

## Required verification

```text
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright
uv run pytest -q -m "not integration"
npm --prefix dashboard test -- --run
npm --prefix dashboard run check
npm --prefix dashboard run build
npm --prefix integrations/opencode-plugin test -- --run
npm --prefix integrations/opencode-plugin run check
uv build
```

G0–G3 also require their targeted security, migration, package, restart, and browser suites. G4 requires real AMD scorecards and an external-suite subset.

## Common PR contract

Every work package PR states its finding(s), non-goals, compatibility surface, pre-change failing/characterization tests, test ownership (unit/contract/security/integration/browser/live), migration fixtures, required events/projections, completion evidence, and rollback/recovery story. `Not applicable` needs a reason.

No work may broaden connector authority, turn simulated staging into production deployment, weaken capture/retention/classification policy, or make the monitor-only dashboard a mutation surface.

## Completion record

```text
Implementation: <PR/commit and reviewed design>
Hermetic verification: <commands, fixture IDs, result>
Integration verification: <package/restart/browser boundary result>
Operational proof: <environment-owned run/scorecard, or deferred>
Compatibility: <version, migration, rollback, deprecation evidence>
Exceptions: <none or approved exception with expiry>
```
