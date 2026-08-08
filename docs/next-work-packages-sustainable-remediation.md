# Sustainable remediation program

**Status:** `[~]` in progress (G0 complete; SR1+)  
**Review baseline:** repository state at commit `1b638c1`  
**Source:** post-sustainable-development architecture, implementation, test, and
documentation review  
**Purpose:** correct the remaining execution, policy, lifecycle, durability,
protocol, verification, and product-proof gaps before expanding the platform.

This document is an executable follow-up to
[the sustainable-development program](next-work-packages-sustainable-development.md)
and [its architecture handover](handover_sustainable_development.md). It does
not introduce new workflow packs, connector authority, deployment targets,
multi-tenancy, distributed scheduling, a replacement CLI, or a
backend-for-frontend.

The program is deliberately gate-first. Correctness and authoritative policy
come before structural refactoring; lifecycle boundaries come before
transaction and client migrations; operational proof comes before performance
tuning.

---

## 1. Tracker and evidence semantics

- `[ ]` planned — no complete implementation evidence exists.
- `[~]` in progress — implementation or verification is incomplete.
- `[x]` complete — every package exit condition is supported by linked
  evidence.

Completion has four distinct evidence levels. Do not substitute one for
another.

| Evidence level | Meaning |
| --- | --- |
| Implemented | Reviewed code, schema, documentation, and migration changes exist. |
| Hermetically verified | Unit, contract, migration, and deterministic integration tests pass without claiming a live boundary. |
| Integration verified | The packaged system crosses its real API, worker, browser, restart, restore, or client boundary. |
| Operationally proven | A controlled environment-owned run establishes the claimed outcome and its limits. |

Store evidence under
`docs/evidence/sustainable-remediation/<package>/`. Each package record must
identify the commit, commands, fixtures or corpus, results, known limitations,
and whether the evidence is hermetic, integration, or operational.

---

## 2. Findings and ownership

| ID | Finding | Owning package |
| --- | --- | --- |
| RF-01 | The canonical quality workflow can deadlock when completed test execution is represented as `partial`. | SR0 |
| RF-02 | Tool, capability, and external-action authority remains duplicated in workflow-specific runtime logic. | SR1 |
| RF-03 | `RunLifecycleEngine` has absorbed much of the former coordinator's implementation ownership. | SR2 |
| RF-04 | Related run, task, event, budget, artifact, handoff, and approval transitions are not consistently atomic. | SR3 |
| RF-05 | Host/v2 exists, but OpenCode, MCP, and the remote Python client remain principally host/v1 clients. | SR4 |
| RF-06 | Dashboard browser behavior and live-update guarantees are not integration-proven. | SR5 |
| RF-07 | Current architecture and integration documentation contains materially stale descriptions. | SR5 |
| RF-08 | Release and scheduled verification still contain incomplete or soft-skipped gates. | SR5 |
| RF-09 | The local-first quality, cost, latency, and affordability thesis is not proven on the target AMD environment. | SR6 |

Assignment identifies the primary owner. A finding may require supporting work
in more than one package.

---

## 3. Dependency graph and merge gates

```text
SR0 Correctness and truthful baseline
        | G0: canonical suite green
        v
SR1 Policy authority and least privilege
        | G1: no shared workflow-specific authority
        v
SR2 Lifecycle decomposition
        | G2: lifecycle engine is an orchestrator
        +-------------------+
        v                   v
SR3 Transactional      SR4 Host/v2 client
durability             adoption
        +---------+---------+
                  | G3: supportable platform
                  v
SR5 Dashboard, documentation, and release truth
                  | G4: product surfaces verified
                  v
SR6 Real local-first evaluation
                  | G5: product thesis measured
                  v
SR7 Removal and measured optimization
```

### 3.1 Program controls

- Freeze workflow, connector, and deployment-surface expansion through G2.
- Do not combine policy-authority work with general lifecycle cleanup.
- After G2, SR3 and SR4 may proceed in parallel with declared file ownership.
- Do not remove host/v1 until host/v2 clients have shipped and deprecation
  telemetry exists.
- Do not tune model routes, prompts, concurrency, context size, or caches before
  SR6 produces measurements.
- Keep deployment simulated and non-production.
- Keep the dashboard loopback-only, monitor-only, and free of bearer-token
  storage.
- A soft-skipped, credential-absent, or optional live job cannot satisfy a
  required gate.
- Every temporary compatibility path needs an owner, removal condition, target
  release, and an ADR or tracked removal issue.

### 3.2 Master tracker

| Package | Status | Findings | Gate |
| --- | --- | --- | --- |
| SR0 — Correctness and baseline | `[x]` | RF-01, RF-08 | G0 |
| SR1 — Policy authority | `[x]` | RF-02 | G1 |
| SR2 — Lifecycle decomposition | `[~]` first cuts | RF-03 | G2 |
| SR3 — Transactional durability | `[~]` UoW slice | RF-04 | G3 |
| SR4 — Host/v2 adoption | `[~]` Python v2 | RF-05 | G3 |
| SR5 — Product and release truth | `[~]` first slice | RF-06–RF-08 | G4 |
| SR6 — Operational evaluation | `[~]` hermetic only | RF-09 | G5 |
| SR7 — Removal and optimization | `[~]` inventory | all residual findings | post-G5 |

---

## 4. SR0 — Restore correctness and a truthful baseline

### 4.1 SR0.A — Define task-result semantics

Create one authoritative definition of execution status.

| Status | Required meaning |
| --- | --- |
| `success` | The executor completed its operation and produced contract-valid required output. |
| `partial` | Required output is incomplete and cannot satisfy a dependency. |
| `blocked` | Mandatory evidence, authority, or environmental capability was unavailable before valid execution. |
| `unsupported` | No registered and permitted execution path exists. |
| `failed` | Execution was attempted but failed operationally. |
| `skipped` | Trusted pack policy intentionally omitted the task. |

Separate the result of the observed work from whether the executor itself ran
successfully. A test process returning a non-zero exit code is successful test
execution with a failing validation outcome when complete receipts were
captured. It is not a failed or partial executor operation.

Add shared domain predicates:

```text
is_terminal_task_status(status)
satisfies_dependency(status)
requires_repair_or_terminal_resolution(status)
```

Scheduler, lifecycle, repair, finalization, and projections must use these
predicates rather than local status sets.

#### Checklist

- [ ] Add characterization tests for every existing task-result status.
- [ ] Document execution status versus validation or domain outcome.
- [ ] Add the shared predicates in the domain boundary.
- [ ] Replace duplicated status collections in scheduler, lifecycle, repair,
  finalization, and projections.
- [ ] Ensure `partial` cannot silently satisfy a dependency.
- [ ] Ensure terminal unsuccessful work is diagnosed rather than reported as an
  unsatisfiable scheduler deadlock.

### 4.2 SR0.B — Repair the quality workflow

Change `TestExecutionExecutor` so that:

- complete command receipts produce task status `success`;
- command exit codes remain in typed validation receipts;
- pass, fail, timeout, and inconclusive outcomes remain evidence;
- inability to start or observe a command produces `blocked` or `failed`;
- truncated or missing mandatory receipts cannot produce success.

Test these scenarios:

1. all registered commands pass;
2. all commands execute and one or more fail;
3. the command executable is unavailable;
4. execution times out with a complete timeout receipt;
5. mandatory evidence is missing;
6. the quality report composes failing evidence without deadlocking;
7. an implementation workflow still repairs or fails when its required
   validation fails.

#### Checklist

- [ ] Add the pre-change regression demonstrating the deadlock.
- [ ] Correct executor/result semantics without synthesizing a pass.
- [ ] Update validation and quality composition fixtures.
- [ ] Verify run, task, timeline, and dashboard projections agree.
- [ ] Verify a failed test remains visibly failed evidence.

### 4.3 SR0.C — Restore the baseline gate

- [ ] Fix the current Ruff formatting failures.
- [ ] Run all required hermetic gates.
- [ ] Correct tracker claims that overstate browser, integration, scheduled, or
  AMD proof.
- [ ] Capture the starting commit, failing tests, and repaired results under
  `docs/evidence/sustainable-remediation/sr0/`.

### 4.4 G0 acceptance

- [ ] The canonical non-integration suite passes.
- [ ] Ambiguous `partial` semantics cannot deadlock downstream tasks.
- [ ] Failing tests are represented as truthful evidence.
- [ ] The quality workflow completes when complete failing-test receipts exist.
- [ ] Current trackers accurately describe their evidence level.

---

## 5. SR1 — Consolidate policy authority and least privilege

### 5.1 SR1.A — Add per-capability pack authority

Extend `PackExecutionPolicy` with a per-capability policy:

```text
CapabilityExecutionPolicy
  capability_id
  executor_mode
  allowed_tool_classes
  allowed_connector_classes
  required_dependency_roles
  output_roles
  validator_policy
  repair_policy
  finding_policy
  external_action_policy
```

Resolve effective authority through intersection:

```text
capability-descriptor maximum
  INTERSECT pack capability grant
  INTERSECT trusted task specification
  INTERSECT operator/environment availability
```

No layer may widen the layer above it. `repository_change` must enumerate the
capabilities it genuinely supports and must not admit all registered
capabilities or deployment authority through a global union.

#### Checklist

- [ ] Add the per-capability policy contract and schema validation.
- [ ] Migrate every canonical pack explicitly.
- [ ] Fail pack registration when a grant exceeds the descriptor maximum.
- [ ] Fail compilation when a task requests an ungranted capability, tool, or
  connector.
- [ ] Remove workflow-wide unions that accidentally widen tasks.
- [ ] Persist the effective policy digest and resolved grants.

### 5.2 SR1.B — Remove workflow-name authority branches

Remove workflow-specific authority decisions from shared runtime code.

- Deployment authorization belongs to the deployment capability/executor and
  its action-approval policy.
- Tool grants belong to the pack's per-capability policy.
- Repair behavior belongs to pack policy and `ValidationRepairService`.
- Aliases belong only to ingress compatibility normalization.
- Shared services may branch on trusted policy values or executor modes, not on
  workflow names.

#### Checklist

- [ ] Move deployment approval enforcement out of workflow-name checks.
- [ ] Remove capability-name and workflow-name checks from effective-policy,
  lifecycle, scheduler, and host application services.
- [ ] Verify the executor independently rejects missing or mismatched action
  authority.
- [ ] Verify packs can narrow but never widen descriptor authority.

### 5.3 SR1.C — Canonicalize durable workflow identity

- [ ] Normalize host/v1 aliases before durable run creation.
- [ ] Persist the canonical pack ID as the run workflow identity.
- [ ] Preserve the requested alias only as compatibility metadata or a durable
  deprecation event.
- [ ] Persist pack version and policy digest.
- [ ] Accept canonical pack IDs only through host/v2.

### 5.4 SR1.D — Strengthen architectural guards

Replace narrow suffix-based tests with AST-based dependency and branch tests.

The guard must scan shared lifecycle, scheduling, policy, application,
persistence, protocol, and projection modules and reject:

- literal canonical workflow comparisons;
- capability conditionals outside packs, registries, executors, and explicit
  compatibility normalization;
- duplicate capability or tool-policy constants;
- widening grants in pack registration;
- new behavior in `RunCoordinator`.

An exception must identify an ADR, owner, and removal date.

Add negative tests that attempt to:

- add deployment capability to `repository_change`;
- request a tool outside a pack grant;
- widen a pack beyond its descriptor;
- invoke deployment without authoritative action approval;
- register an executor mode without its complete adapter chain.

### 5.5 G1 acceptance

- [ ] One pack policy determines validators, tools, outputs, repair, findings,
  and external-action requirements.
- [ ] No named workflow conditional remains in shared runtime code.
- [ ] Every canonical pack compiles and runs in fake-live tests.
- [ ] `repository_change` cannot acquire deployment authority.
- [ ] Durable runs use canonical pack IDs.

---

## 6. SR2 — Decompose the lifecycle kernel

The goal is explicit ownership and enforceable dependency direction, not a
line-count target.

### 6.1 SR2.A — Extract the composition root

Move dependency construction out of `RunLifecycleEngine`. Create a composition
root responsible for constructing:

- repositories and unit-of-work factory;
- trusted registries;
- model and connector gateways;
- lifecycle services;
- executor registry;
- query and projection services;
- worker supervisor;
- host application service.

The lifecycle engine receives dependencies rather than constructing them.

### 6.2 SR2.B — Extract task preparation

Create `TaskPreparationService` to own:

- capability and executor resolution;
- effective-policy calculation;
- dependency-artifact resolution;
- skill, stack profile, and model-profile resolution;
- safe repository context assembly;
- route resolution;
- immutable `TaskExecutionRequest` construction.

It returns a typed prepared request or an honest `blocked`/`unsupported`
result. It must not execute the task.

### 6.3 SR2.C — Extract wave execution

Create `WaveExecutionService` to own:

- runnable-task selection;
- concurrency admission;
- execution dispatch;
- result collection;
- task-state transitions;
- validation and repair invocation;
- liveness and heartbeat handling.

`WaveScheduler` remains responsible for scheduling decisions and must not gain
persistence, model, connector, or composition responsibilities.

### 6.4 SR2.D — Replace callback-heavy composition

Replace `ComposeContext` callbacks and `Any` fields with immutable typed data:

```text
CompositionInput
  run snapshot
  compiled plan
  task results
  dependency artifacts
  validation evidence
  findings
  lineage
  effective policy
```

`CompositionService` may receive narrow artifact/query interfaces for required
reads. Workflow handlers must not call lifecycle-engine private helpers.

### 6.5 SR2.E — Extract application commands

Create a command service for:

- submit;
- resume;
- cancel;
- revise;
- approve or reject;
- apply or deliver eligible artifacts.

Handoff and action-approval decisions remain owned by their trust services.
The command service coordinates them but cannot reinterpret their authority.

### 6.6 SR2.F — Reduce lifecycle dependencies

After extraction, `RunLifecycleEngine` should:

1. load authoritative run state;
2. ask preparation, scheduling, and execution services to advance it;
3. invoke finalization when eligible;
4. persist the lifecycle transition through a transaction boundary.

It must not directly construct or depend on:

- model, tool, or connector brokers;
- workflow handlers;
- capability-specific executors;
- approval implementations;
- repository inventory implementations;
- composition callbacks.

Keep `RunCoordinator` only as a compatibility facade until direct callers have
migrated.

### 6.7 Tests and guardrails

Add characterization tests before moving behavior:

- [ ] fresh run;
- [ ] resume after interruption;
- [ ] repair creation and lineage;
- [ ] partial, blocked, unsupported, and failed results;
- [ ] cancellation;
- [ ] approval wait and resume;
- [ ] budget exhaustion;
- [ ] finalization;
- [ ] worker recovery.

Add import-boundary tests that prohibit the extracted dependencies from moving
back into the lifecycle engine or coordinator.

### 6.8 G2 acceptance

- [ ] `RunCoordinator` is only a compatibility facade.
- [ ] `RunLifecycleEngine` contains lifecycle sequencing, not implementation
  loops.
- [ ] Adding a fixture pack requires no lifecycle, scheduler, host union,
  dashboard, or API branching.
- [ ] No handler calls lifecycle-engine private methods.
- [ ] Characterization tests demonstrate preserved outcomes.

---

## 7. SR3 — Transactional durability and recovery

### 7.1 SR3.A — Implement a real unit of work

Build a unit-of-work boundary over `SqliteActor.immediate()`. Repository methods
participating in a unit of work must not commit independently.

Provide atomic application operations for at least:

- run admission and initial events;
- task start plus run/event updates;
- task completion plus event and budget settlement;
- repair creation plus lineage;
- run finalization plus output-role enforcement and events;
- action-approval consumption plus external-action intent;
- handoff consumption plus child artifact and consumption record.

Enable and verify foreign keys on every connection.

### 7.2 SR3.B — Couple events to authoritative state

State changes and their corresponding durable events must commit in the same
SQLite transaction. SSE notifications occur only after commit and remain a
view over durable events, never an alternate state store.

### 7.3 SR3.C — Correct handoff transition persistence

Persist approval and supersession audit information authoritatively. Prefer an
append-only transition record:

```text
HandoffTransition
  handoff_id
  from_state
  to_state
  actor
  reason
  occurred_at
```

Update current state and append the transition in one transaction.

At approval and consumption:

- reload the authoritative record;
- verify the blob exists;
- recompute digest and size;
- verify schema, role, classification, retention, and state;
- atomically create the child artifact and consumption record.

### 7.4 SR3.D — Add fault-injection recovery tests

Add controlled failures around:

- planning persistence;
- before and after model invocation recording;
- tool execution;
- validation;
- repair creation;
- artifact writes;
- budget reservation and settlement;
- handoff consumption;
- action-approval consumption;
- deployment reconciliation.

For every point verify:

- no duplicate external action;
- no double budget settlement;
- no event/state contradiction;
- deterministic resume;
- no successful finalization with missing evidence.

### 7.5 SR3.E — Replace scheduled-test placeholders

- [ ] Launch a real worker in the shutdown/recovery test.
- [ ] Interrupt it during planning, model wait, tool execution, validation, and
  external-action reconciliation.
- [ ] Restart it and verify deterministic recovery.
- [ ] Verify database-to-blob references after backup restoration.
- [ ] Make required infrastructure failure visible rather than soft-skipping.

### 7.6 G3 durability acceptance

- [ ] Coupled task, run, event, and budget transitions are atomic.
- [ ] Handoff consumption cannot leave mismatched artifact and consumption
  state.
- [ ] Recovery covers model, tool, validation, and external-action boundaries.
- [ ] Required scheduled tests execute rather than silently succeeding.

---

## 8. SR4 — Adopt host/v2 across public clients

### 8.1 SR4.A — Stabilize the application-service boundary

Route all public mutations through the same host application service:

- local CLI;
- host CLI;
- HTTP;
- MCP;
- remote Python client;
- OpenCode plugin.

Administrative database, backup, and maintenance commands may remain separate
when explicitly identified as administrative surfaces.

### 8.2 SR4.B — Migrate the Python remote client

- [ ] Implement typed host/v2 operations.
- [ ] Validate all v2 response payloads strictly.
- [ ] Negotiate protocol through metadata.
- [ ] Use v2 handoff identifiers and canonical workflow IDs.
- [ ] Implement v1 only as an explicit compatibility adapter.
- [ ] Do not fall back to v1 after arbitrary v2 execution failures.

### 8.3 SR4.C — Split and migrate the OpenCode client

Split transport, protocol, delivery, and integration concerns:

```text
generated/protocol-v2.ts
transport/http.ts
transport/cli.ts
transport/sse.ts
protocol/validation.ts
delivery/materialization.ts
client.ts
```

Generate transport DTOs from canonical OpenAPI. Keep OpenCode-specific prompt
injection, session behavior, and safe local materialization handwritten.

Add a consumer-ready package build unless an OpenCode compatibility test proves
that source TypeScript packaging is the required supported format.

### 8.4 SR4.D — Migrate MCP

- [ ] Generate workflow enumeration from the pack registry or application
  metadata.
- [ ] Translate model-facing tools into canonical host/v2 commands.
- [ ] Preserve explicit host permission for approval decisions.
- [ ] Do not duplicate mutation or authority logic in MCP handlers.

### 8.5 SR4.E — Time-box compatibility

Recommended release progression:

1. next minor release: v2 preferred; v1 supported with durable deprecation
   telemetry;
2. following minor release: remove v1 when client and telemetry evidence permit;
3. any extension requires a written compatibility decision.

Commit shared golden request/response fixtures for Python and TypeScript. Cover
strict extra-field rejection, alias behavior, version negotiation, handoffs,
stream cursors, errors, and delivery metadata.

### 8.6 G3 protocol acceptance

- [ ] OpenCode, remote Python, MCP, HTTP, and CLI mutations converge on one
  application service.
- [ ] Primary clients negotiate and use host/v2.
- [ ] Host/v1 usage is observable.
- [ ] Aliases are accepted only through v1 compatibility.
- [ ] Cross-language golden fixtures and generated-contract drift checks pass.

---

## 9. SR5 — Dashboard, documentation, and release truth

### 9.1 SR5.A — Complete dashboard support guarantees

Wire service metadata detection into the running UI:

- [x] fetch metadata at startup; *(first slice — Vitest + UI notice; not Playwright)*
- [x] detect unsupported authenticated or public remote configurations; *(via `remote_mode` meta)*
- [x] show loopback/tunnel guidance;
- [x] never store bearer tokens;
- [x] never add mutation controls.

Add Playwright tests against a real FastAPI process and seeded SQLite data:

1. empty database;
2. run-list to detail navigation;
3. DAG, kanban, and task projection agreement;
4. live SSE update without reload;
5. local persisted-event latency under one second at p95;
6. repair lineage;
7. redacted capture rendering;
8. metadata/off capture unavailability;
9. cross-run content denial;
10. unsupported remote-dashboard state;
11. blocked-task diagnosis and CLI guidance;
12. cost and budget inspection.

Componentize the dashboard so API, stream, routing, projection transformation,
and views can be tested independently.

### 9.2 SR5.B — Rewrite current-state documentation

Update:

- [~] architecture; *(SR5 first slice: evidence-level banner + coordinator compatibility note)*
- [ ] codebase structure;
- [ ] host protocol and compatibility policy;
- [ ] OpenCode integration guidance;
- [ ] workflow, capability, connector, model, and skill catalogs;
- [ ] persistence and recovery model;
- [~] dashboard support boundary; *(meta fetch + docs honesty markers)*
- [~] operator and release guidance; *(required vs optional live jobs)*
- [ ] known limitations.

Mark historical handovers and completed plans clearly. They must not be
presented as current architecture. Generate mechanical catalogs from trusted
registries while retaining hand-authored explanations.

**First slice:** `handover_sustainable_development.md` marked historical; SD
master/SD4/SD5 trackers corrected for overstated browser/AMD/G3 claims.
Evidence: [`docs/evidence/sustainable-remediation/sr5/`](evidence/sustainable-remediation/sr5/).

### 9.3 SR5.C — Make tracker evidence explicit

Every completed tracker item must link to applicable evidence:

- implementation commit or PR;
- test output;
- migration fixture;
- package artifact;
- browser report;
- restart or restore record;
- operational scorecard.

Use `implemented`, `hermetically verified`, `integration verified`, and
`operationally proven` explicitly.

### 9.4 SR5.D — Finish release engineering

Required pull-request verification:

```text
uv sync --frozen --extra dev
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright
uv run pytest -q -m "not integration"
npm --prefix dashboard ci
npm --prefix dashboard test -- --run
npm --prefix dashboard run check
npm --prefix dashboard run build
npm --prefix integrations/opencode-plugin ci
npm --prefix integrations/opencode-plugin test -- --run
npm --prefix integrations/opencode-plugin run check
bash scripts/check_openapi_drift.sh
bash scripts/package_smoke.sh
uv build
```

Add:

- [ ] wheel install and packaged-dashboard smoke;
- [ ] plugin package and install smoke;
- [ ] container image digest recording;
- [ ] release hashes;
- [ ] SBOM;
- [ ] build provenance;
- [ ] dependency and license reports.

Scheduled jobs must distinguish a required infrastructure failure from missing
optional live credentials.

**First slice:** `scripts/verify.sh` no longer invokes soft-skipping Docker /
deploy / backup integration modules on the default path; they run only when
their `*_INTEGRATION=1` gates are set. `scheduled-recovery.yml` labels required
vs optional live jobs explicitly.

### 9.5 G4 acceptance

- [ ] Documentation matches the runtime dependency graph and behavior.
- [ ] Playwright verifies every claimed dashboard boundary.
- [ ] Package artifacts install and run independently of the source tree.
- [ ] Required CI contains no placeholder or silent-success job.
- [ ] Trackers do not overstate verification.

---

## 10. SR6 — Prove the local-first product thesis

### 10.1 SR6.A — Stabilization corpus

Begin with twelve sanitized real cases:

- two discovery;
- two technical planning;
- two repository change;
- two quality;
- two release;
- two operations.

Use one seed while fixing harness and scoring defects. Stabilization runs cannot
promote defaults.

### 10.2 SR6.B — Promotion corpus

Expand to at least thirty cases and three seeds per comparison arm:

- local-only;
- local-first with bounded cloud fallback;
- cloud orchestration;
- comparable single-agent baseline;
- skills enabled;
- skills disabled.

Record:

- local model, quantization, runtime, and profile;
- AMD driver/runtime versions;
- memory use and saturation;
- admission and queue time;
- fallback reasons;
- token and latency accounting;
- cloud spend;
- local compute-cost estimate;
- policy violations;
- unsupported claims;
- human correction effort;
- accepted outcome and validator scores.

### 10.3 SR6.C — External suites

- [ ] Implement and execute SWE Atlas as the first adapter.
- [ ] Run a small official Terminal-Bench/Harbor subset.
- [ ] Gate DeepSWE behind compatibility and licensing review.

Adapters must use the same public application service, policies, and execution
paths as normal product runs.

### 10.4 SR6.D — Promotion decisions

Retain these promotion requirements:

- zero policy violations;
- accepted-outcome or validator quality no more than five percentage points
  below the cloud arm;
- correction effort no more than 10% worse;
- unsupported-claim rate no more than two percentage points worse;
- at least 30% lower cloud spend for local-first promotion;
- no unresolved timeout, reliability, or recovery regression.

A failed experiment is a valid result. Do not modify scoring to manufacture a
promotion.

### 10.5 G5 acceptance

- [ ] At least thirty cases and three seeds are complete.
- [ ] AMD execution uses the real target runtime and models rather than a cloud
  stand-in.
- [ ] Scorecards include quality, cost, latency, reliability, queueing, and
  saturation.
- [ ] An explicit promote, retain, or rollback decision exists for every default
  route and promoted skill.

---

## 11. SR7 — Removal and measured optimization

Begin only after G5.

### 11.1 SR7.A — Remove compatibility debt

Inventory (first slice; no host/v1 removal):
[`docs/architecture/sr7-compatibility-inventory.md`](architecture/sr7-compatibility-inventory.md);
evidence: [`docs/evidence/sustainable-remediation/sr7/`](evidence/sustainable-remediation/sr7/).

Remove when replacement and usage evidence permit:

- [ ] host/v1;
- [ ] workflow aliases;
- [ ] deprecated request fields;
- [ ] direct `RunCoordinator` persistence access;
- [ ] legacy evaluation dual writes;
- [ ] stale JSONL authority paths;
- [ ] temporary lifecycle delegates;
- [x] unused LangGraph or demo dependencies *(already removed in SD7; keep absence tests)*;
- [ ] dead remote-client helpers;
- [ ] obsolete trackers and architectural claims.

Every deletion requires a compatibility test plus telemetry or repository-usage
evidence.

### 11.2 SR7.B — Optimize measured hotspots

Measure before and after:

- safe repository inventory;
- prompt assembly;
- task preparation;
- SQLite transactions;
- projection queries;
- model admission and queueing;
- worker concurrency;
- SSE propagation;
- validation execution.

Only then consider:

- repository inventory caching by revision and policy digest;
- stack-profile caching;
- batched projection queries;
- transaction consolidation;
- AMD-specific concurrency tuning;
- context and route changes.

Every model, prompt, or context optimization must re-run the SR6 quality gates.

### 11.3 SR7 acceptance

- [ ] Every remaining compatibility surface has a current support decision.
- [ ] Removed surfaces have compatibility and usage evidence.
- [ ] Every optimization has baseline, after-measurement, and non-regression
  evidence.
- [ ] Obsolete components are not retained merely because historical plans
  mention them.

---

## 12. Reviewable PR stack

Use small PRs with one primary concern:

1. task-result semantics and quality regression;
2. formatting baseline and truthful tracker state;
3. per-capability pack policy;
4. workflow-specific authority removal and stronger guards;
5. composition root and task-preparation extraction;
6. wave execution and typed composition extraction;
7. application-command and lifecycle-facade completion;
8. SQLite unit of work and atomic run/task/event transitions;
9. atomic trust transitions and recovery tests;
10. Python and MCP host/v2 migration;
11. OpenCode host/v2 migration and package split;
12. dashboard browser coverage;
13. documentation and release engineering;
14. evaluation-harness stabilization;
15. AMD corpus and external benchmark execution;
16. compatibility removal and measured optimization.

Do not combine trust-boundary, lifecycle, protocol, and general-cleanup changes
into one PR.

---

## 13. Common PR contract

Every PR must include the repository placement note:

```text
Concern: <lifecycle | executor | policy | persistence | protocol | UI>
Owning boundary: <service/module>
Authoritative source: <registry/durable record>
Compatibility: <none or migration/version/rollback>
Guardrail proof: <test path and result>
Temporary exception: <none or ADR/removal issue>
```

It must also state:

- finding IDs;
- non-goals;
- pre-change failing or characterization tests;
- unit, contract, security, integration, browser, and live-test ownership;
- schema and compatibility fixtures;
- required events and projections;
- rollback or recovery behavior;
- completion-evidence path.

Tests must prove the boundary and its negative cases, not only a successful
example.

---

## 14. Overall completion criteria

This remediation program is complete only when:

- [ ] the canonical verification suite is green;
- [ ] task outcomes cannot deadlock workflows through ambiguous status
  semantics;
- [ ] one pack policy controls execution authority;
- [ ] shared runtime code contains no named workflow branches;
- [ ] the lifecycle engine delegates implementation work to named owners;
- [ ] state, events, budget, trust transitions, and recovery are transactionally
  sound;
- [ ] primary clients use host/v2;
- [ ] dashboard guarantees are browser-tested;
- [ ] documentation describes the current architecture;
- [ ] release gates execute real checks;
- [ ] local-first value is proven or honestly rejected using real AMD runs;
- [ ] compatibility debt and optimization are handled from evidence rather than
  intuition.

The desired end state is not merely a smaller coordinator. It is a repository
where lifecycle, executor, policy, persistence, protocol, UI, and evaluation
concerns each have an authoritative owner and an automated guardrail preventing
them from collapsing back into a shared orchestration monolith.
