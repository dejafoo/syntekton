# Product Factory — Pre-PM5 Refactoring and Hardening Handover

**Status:** implementation handover; mandatory readiness gate before PM5  
**Audience:** humans and AI agents changing orchestration, persistence, tools,
gateway routing, observability, host integration, or the test suite  
**Scope:** correct composition and operational hardening of PM0–PM4 before
adding release, deployment, domain/policy, or operations workflows.

**Companion documents:**

- [Prioritized implementation plan](next-work-packages-post-mvp.md) — PM0–PM5
  ordering and PM5 scope.
- [Workflow portfolio handover](handover_post_mvp_workflows.md) — workflow
  outcomes, authority classes, and handoff contracts.
- [Capabilities, tools, connectors, and skills handover](handover_post_mvp_skills.md)
  — tool authority, evidence, and connector boundaries.
- [Remote orchestration handover](handover_remote_orchestration.md) — server,
  laptop, workspace, and delivery topology.
- [Architecture](architecture.md) — current control plane and durable runtime.

---

## 1. Purpose and decision

PM0–PM4 established a substantial foundation: pack registration and typed
handoffs, source-grounded discovery, remote host/workspace delivery,
verification evidence, a dashboard, worker leases, and a routed
OpenAI-compatible gateway. This is enough to make Product Factory useful for
bounded discovery, planning, repository change, and verification work.

It is **not** yet safe to broaden the authority surface with PM5. In
particular, release, deployment, and operations workflows would magnify four
existing weaknesses:

1. run-scoped mutable execution state can leak telemetry, budgets, or audit
   attribution across concurrent remote runs;
2. the persisted prompt/observability view is not always the same as the
   actual tool grant and repository context used at execution time;
3. evidence artifacts can bypass the declared capture policy; and
4. pack-specific lifecycle behavior is still concentrated in
   `RunCoordinator`, which will not scale safely to release and deployment
   policies.

This document defines a **pre-PM5 refactoring gate**. Complete it after PM4
and before introducing `release_readiness`, `deployment_execution`,
`incident_triage`, `service_health_review`, production-like connectors, or
additional deployment authority.

The gate is not a rewrite and does not change the product boundary:

- Product Factory remains an orchestration service used through existing hosts,
  primarily the OpenCode plugin and host/v1 API.
- The dashboard remains monitor-only.
- `ToolBroker` remains the sole route to tools/connectors.
- The server remains workspace authority; a laptop remains landing authority.
- Packs, skills, profiles, and model output never grant authority.
- PM5 still starts with one non-production deployment target and explicit
  approval. This gate does not authorize it early.

### 1.1 Completion definition

The gate is complete only when the following statement is true:

> Two independent remote runs can execute concurrently without sharing mutable
> execution state; a task’s durable context, effective grant, tool receipts,
> artifacts, costs, and dashboard view describe the same policy-controlled
> reality; and a new read-only or external-write pack can be added through
> registered pack policy rather than another workflow-type branch in the
> coordinator.

---

## 2. Locked design rules

These rules are normative. Do not solve the work packages by weakening one of
them.

| Rule | Required interpretation |
| --- | --- |
| Run isolation | No mutable field shared by concurrently executing runs may carry run-, task-, recorder-, budget-, workspace-, grant-, or audit-specific state. |
| One effective policy | The exact grant and policy decision enforced by the broker is the one shown to the model and persisted in the task context manifest. |
| Artifact ownership | A hash alone is not authorization. Every readable artifact/content reference has a run-scoped ownership record and visibility classification. |
| Capture is authoritative | `off`, `metadata`, `redacted`, and `full` apply consistently to model, tool, connector, source, validation, and artifact content. Nothing de-redacts or recreates unavailable content. |
| Pack extensibility | New workflow behavior is expressed as registered, typed policy/handlers; the generic coordinator owns lifecycle mechanics, not workflow names. |
| Fail closed | Missing ownership, missing classification, incompatible handoff, unknown model route, or incomplete policy resolution prevents execution or content display. |
| Backward compatibility | Historical runs remain readable. Missing newer metadata is reported as `unknown`/`legacy`, never silently treated as safe/full. |

---

## 3. Target architecture

The target is a small set of immutable, durable decisions flowing through one
generic execution path.

```text
Run request + pinned inputs
          │
          ▼
Pack policy resolver ──► Compiled plan / typed task templates
          │                         │
          │                         ▼
          └──► EffectiveTaskPolicy (immutable, persisted)
                         │
                         ▼
RunExecutionContext (immutable, run-scoped)
  gateway + recorder + ledger + workspace + scoped broker factory
                         │
                         ▼
Generic task executor / declared executor mode
                         │
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
Tool/connector      artifacts/receipts   validation/evidence
execution           with run ownership   with visibility policy
       │                 │                  │
       └─────────────────┴──────────────────┘
                         ▼
             projections / SSE / dashboard
```

The coordinator may construct these objects, schedule dependency waves,
persist transitions, enforce global budgets, and invoke registered handlers.
It must not rewrite shared gateway/audit fields or select behavior from a
growing list of workflow names.

### 3.1 Core contracts

Introduce or complete the following internal contracts. Names may differ if
the resulting ownership and invariants are equivalent.

#### `RunExecutionContext`

An immutable, run-scoped object created once for `run` and once for `resume`:

- `run_id`, workflow/pack version, request classification, and workspace
  identity;
- run-scoped instrumented gateway, recorder, usage accumulator, and budget
  ledger binding;
- artifact writer/registry factory bound to the run;
- broker factory that creates a task-scoped audit binding rather than mutating
  a shared broker callback;
- clock/cancellation and durable event emitter interfaces as needed; and
- resolved model-routing policy for the run.

The context must be passed explicitly to planning, task execution, validation,
repair, composition, and resume paths. A resumed run rebuilds it from durable
metadata rather than relying on a process-global state left by another run.

#### `EffectiveTaskPolicy`

An immutable policy resolved *before* model context assembly and persisted with
the task. It contains:

- effective capability and executor mode;
- exact allowed tool names/classes and connector-policy decisions;
- path/workspace scopes, call and result limits, and data classification;
- resolved skill/profile/reference-pack identities and a bounded rendered
  repository stack profile;
- model route policy, fallback eligibility, and budget ceiling; and
- validator/repair/approval policy applicable to the task.

The broker, canonical tool schema builder, context manifest, prompt builder,
dashboard projection, and audit record all consume this same object. A model
may be informed about fewer tools for prompt-budget reasons, but never about a
tool that is not granted; that reduction must be recorded explicitly.

#### `ArtifactInstance` and `ContentReference`

Keep global content-addressed bytes deduplicated, but record each use in a
run-scoped relation. An artifact instance needs at least:

- `run_id`, `sha256`, logical role, producer task/tool/validator, and creation
  event sequence;
- media type, schema/version where applicable, size, and display name;
- classification, capture level, visibility (`available`, `redacted`,
  `metadata_only`, `unavailable`), retention, and truncation metadata; and
- provenance/input-parent references.

Content references used in model events/invocations need equivalent ownership
and availability data. Do not infer ownership from a directory path or accept
a path from a browser/client.

#### `PackExecutionPolicy`

Pack-owned registered policy declares, in typed data or a registered handler:

- input/handoff roles and schema compatibility;
- task templates, dependencies, eligible capabilities, and executor modes;
- grant narrowing and connector eligibility;
- artifact composition rules, final validators, findings, repair policy, and
  approval requirements; and
- output roles, landing eligibility, and evaluation fixture identifier.

It is intentionally not user-supplied code and cannot register tools,
connectors, validators, or arbitrary commands. A handler remains acceptable
where deterministic composition is genuinely custom, but it must be selected
from the pack registry, not `if workflow_type == ...` in the coordinator.

---

## 4. Work packages

Implement in the stated order. Packages R1–R4 are prerequisites for PM5.
R5–R6 can partially overlap once their dependencies are met.

### R1 — Run and task execution isolation

**Goal:** make concurrent worker execution correct before adding more remote
activity or external authority.

#### Required changes

1. Replace run-specific mutation of shared coordinator gateway state with
   `RunExecutionContext` injection in both new-run and resume paths.
2. Remove task/run-specific mutable audit callback state from shared
   `ConnectorBroker`. Either create a task-scoped broker facade or pass the
   audit sink as an invocation parameter that cannot escape the call.
3. Make recorder, budget ledger, usage accounting, workspace identity, and
   cancellation binding explicitly run-scoped.
4. Audit all long-lived service/coordinator fields for request/run/task data.
   Convert them to immutable shared configuration or context-local state.
5. Ensure background worker recovery creates a fresh context from persisted
   run metadata. Do not retain a stale gateway/ledger from the worker process.
6. Define the concurrency boundary: one active writer per worktree remains
   enforced, while independent worktrees/runs may execute concurrently.

#### Must not

- Serialize the whole service merely to hide shared state.
- Store a `run_id`/`task_id` in a mutable global, thread-local, or process-wide
  broker field as the primary attribution mechanism.
- Reuse a mutable gateway decorator across two concurrent runs.

#### Tests

- Two concurrently blocked/released fake-gateway runs prove that every model
  invocation, cost row, event, and budget ledger entry has the correct run ID.
- Two concurrent connector calls prove receipts and audit events have the
  correct run and task IDs even when calls interleave.
- A resume of run A during active run B preserves both runs’ recorder/ledger
  attribution.
- One run exhausting its budget cannot block or spend the budget of another.
- Existing same-worktree lease rejection still passes; independent-worktree
  concurrency is explicitly covered.

#### Exit

Race-focused tests fail against the pre-refactor behavior and pass reliably
under repeated execution. A code review can identify no mutable run/task state
on a service-wide coordinator or broker singleton.

### R2 — Resolve policy once; make context, grants, and routing truthful

**Goal:** the model, broker, durable manifest, and dashboard describe the same
execution policy.

#### Required changes

1. Extract grant resolution from task execution and run it before
   `assemble_context`. Include capability rules, pack restrictions, connector
   policy, data classification, workspace/path scope, and tool-call limits.
2. Build canonical tool schemas and prompt tool descriptions only from the
   effective grant. Persist both the effective set and any deliberate
   prompt-time reduction.
3. Persist a bounded, rendered repository stack profile—not only its digest—
   as a run artifact/resource. Include detector version, source input digests,
   confidence, limitations, and profile schema version.
4. Inject the resolved stack profile into task context within a defined token
   budget. The context manifest must retain the exact rendered/digested
   resource identity used for a task.
5. Resolve routing to an explicit route identity. On fallback, use a named
   cloud profile with its own provider, actual model, pricing, data policy, and
   reason; do not relabel a cloud request as the original local profile.
6. Persist actual provider/model/route, fallback reason, and cost-basis
   dimensions from the response path. Unknown usage/pricing remains clearly
   estimated/unknown.

#### Compatibility

Existing manifests without `effective_task_policy` or stack-profile artifact
remain readable as `legacy_unresolved`. They must not be reconstructed as if
their exact grant or prompt had been recorded.

#### Tests

- A repository profile changes the rendered task context for representative
  Python, TypeScript/Node, and unsupported repositories; all stay within the
  prompt budget.
- A task’s persisted effective grant exactly matches broker authorization and
  canonical tool schemas. A denied tool is neither advertised nor callable.
- Connector policy narrowing is visible in the task manifest and dashboard
  projection.
- Resume reuses the pinned profile/context identity rather than re-detecting a
  changed repository.
- Local-route success, allowed cloud fallback, denied fallback, missing route,
  and pre-probe budget rejection each produce correct provider/model/cost
  dimensions.

#### Exit

One task fixture can be inspected end-to-end and show one consistent policy
from compiled plan through tool receipt and dashboard detail.

### R3 — Artifact, evidence, and capture-policy unification

**Goal:** make durable evidence useful without turning the artifact store into a
capture-policy bypass.

#### Required changes

1. Add a run-scoped artifact-instance relation. Global hashes may deduplicate
   bytes, but an instance records ownership, role, producer, classification,
   and visibility for each run.
2. Register all producer paths: model prompt/response captures, tool results,
   connector source captures/records/receipts, validation raw output and
   normalized reports, handoffs, plans, profile resources, and final artifacts.
3. Define content classes. A minimum useful vocabulary is `durable_output`,
   `normalized_evidence`, `raw_tool_capture`, `raw_source_capture`,
   `raw_validation_capture`, and `model_capture`.
4. Apply capture policy at write time and read time. For each class, specify
   whether the canonical stored representation is unavailable, metadata-only,
   redacted, or full. Never retain a raw copy solely because another endpoint
   calls it an artifact.
5. Keep the evidence artifact distinct from its raw capture. For example, a
   validation report may remain a normalized, bounded durable output while
   raw stdout/stderr is unavailable under `metadata` or `off`.
6. Make observability APIs and dashboard viewers consume visibility metadata;
   unavailable material is explained rather than requested repeatedly.
7. Enforce ownership checks for every hash endpoint and download route. A
   global blob’s existence is not proof it belongs to a run.
8. Add retention/deletion behavior carefully. Deleting a raw capture must not
   invalidate a durable receipt or a handoff hash; its metadata should record
   content unavailability.

#### API behavior

Content responses must return metadata plus one of:

- `available: true` with the policy-authorized text/JSON payload;
- `available: false`, `reason: capture_off|metadata_only|expired|not_retained`;
  or
- `available: true`, `redacted: true` with exactly the stored redacted payload.

Unknown or cross-run hashes return `404`. Do not use a different status code
that reveals whether a global artifact exists.

#### Tests

- Parameterize every content class across `off`, `metadata`, `redacted`, and
  `full`; verify both stored representation and API/dashboard behavior.
- Raw source and validation output cannot be recovered through artifact content
  when policy says unavailable.
- Redacted artifacts display stored redactions exactly and cannot be
  de-redacted by JSON/text/download views.
- Same hash owned by two runs is visible only through each authorized
  `ArtifactInstance`, with correct role/producer metadata.
- Unknown and cross-run hashes return indistinguishable `404` responses.
- Historical artifacts without classification are `legacy_unknown` and not
  automatically exposed as full content.

#### Exit

An operator can inspect the Evidence dashboard tab and understand why every
item is visible, redacted, or unavailable. Capture policy cannot be bypassed
through an artifact/content route.

### R4 — Make pack execution genuinely extensible

**Goal:** remove workflow-name branching from the runtime path before PM5 adds
multiple new authority classes.

#### Required changes

1. Inventory every branch based on workflow type/alias in coordinator,
   planner, validator, composition, repair, host presentation, and
   observability. Classify each as generic lifecycle, pack policy, capability
   executor, or compatibility adapter.
2. Move workflow-specific configuration into `PackExecutionPolicy` and
   registered handlers. Start with existing packs; do not first implement PM5
   packs.
3. Define a small fixed executor-mode catalogue, for example:
   `deterministic`, `model_draft`, `repository_agent_loop`,
   `research_agent_loop`, `interface_agent_loop`, `validation`, and
   `composition`. A capability must be mapped to a supported executor mode by
   registered pack policy.
4. Make tool-loop availability derive from the effective task policy. In
   particular, `interface_analysis` must expose only its permitted contract
   inventory/diff/fixture/simulation tools, rather than silently falling back
   to generic research tools.
5. Ensure pack validators, repair eligibility, final composition, handoff
   output roles, and `eligible_next_actions` are declared in the same
   registered policy surface.
6. Retain aliases only at host/request normalization. The runtime should see a
   canonical pack ID/version.
7. Keep a narrow compatibility adapter for historical workflow records; do not
   keep it on the new runtime path indefinitely.

#### Technical-spike completion requirement

The current technical-spike/interface-analysis path must become real before
PM5. For an interface input, it must create durable typed contract inventory,
comparison/compatibility, synthetic fixture or simulation evidence where
requested, and a `SpikeResult` that references those artifacts. A polished
generic report is not sufficient evidence.

#### Tests

- Table-driven tests exercise every existing canonical pack through the same
  generic dispatch entry point.
- A regression test makes it difficult to introduce a new workflow-name branch
  in the coordinator (for example, static architecture test or a reviewed
  allowlist limited to legacy normalization).
- End-to-end technical-spike fixture verifies expected interface tools were
  called, artifacts/receipts were created, and final measurements cite them.
- Pack-policy compilation rejects an unknown executor mode, capability, tool
  class, validator, artifact role, or incompatible handoff before model/tool
  execution.
- Existing aliases behave identically to their canonical pack while durable
  manifests use canonical identities.

#### Exit

Adding a small read-only pack requires a pack registration, schemas,
task/handler policy, validators, fixtures, and host metadata—without an
execution branch keyed on a new workflow name in `RunCoordinator`.

### R5 — Prove the local-first model plane

**Goal:** replace the current local-route stand-in with evidence that a real
AMD-hosted OpenAI-compatible endpoint is reliable enough for selected work.

#### Required changes

1. Configure at least one real local endpoint behind the existing gateway
   contract. Keep mock and cloud profiles available for deterministic tests and
   explicit fallback.
2. Implement conservative startup/periodic probes for reachability, model
   identity, structured output, tool-call protocol, context capacity, and
   basic latency. A missing capability field is not proof of support.
3. Separate route classes from model profiles. A fallback chooses an explicitly
   configured cloud model profile; its data policy, spend, and reason are
   independently observable.
4. Add circuit-breaker/backoff semantics for unhealthy local endpoints. Do not
   repeatedly spend fallback budget because of a flapping local service.
5. Define model-role admission criteria by task type: planner, research,
   implementation, review, repair, and composition may have different local
   eligibility.
6. Record model evaluation results and route decisions as durable operational
   evidence, not a YAML preference alone.

#### Evaluation and tests

- Contract tests against a controllable OpenAI-compatible fake server cover
  streaming/non-streaming completion, tool calls, structured output, timeout,
  malformed response, and usage absence.
- An opt-in integration profile runs the same contracts against the real AMD
  endpoint; it is never silently required in unit CI.
- Representative workflow fixtures compare permitted local profiles against
  explicit cloud baselines for task success, validation success, repair rate,
  latency, and total cost.
- Fallback tests cover capability miss, local transport failure, local quality
  gate failure where policy permits escalation, exhausted budget, and
  circuit-open behavior.

#### Exit

At least one task role has a documented, measured local default and named
fallback. The dashboard identifies the actual provider/model/route for every
model invocation and separates local cost from cloud spend.

### R6 — Observability, migration, and operator hardening

**Goal:** make the refactored runtime diagnosable and safe to operate while
retaining historical data.

#### Required changes

1. Update query projections and dashboard to show effective task policy,
   stack-profile resource, route/fallback information, artifact visibility,
   producer/role, and legacy status.
2. Ensure SSE invalidates affected projections from persisted events but never
   makes events the sole state source.
3. Add schema migrations for new execution-context references, task policy
   snapshots, artifact instances, and content visibility. Migrations must be
   additive and restart-safe.
4. Document backup/restore and migration expectations for the single-user
   remote server. Test an old database/artifact layout upgraded in place.
5. Correct the PM4 status drift in the implementation tracker only after
   verifying its stated done evidence. Do not mark PM5 started merely because
   this refactoring gate exists.

#### Tests

- API contract and OpenAPI tests cover new policy/evidence fields and preserve
  old projection shapes where promised.
- Dashboard browser tests cover policy/grant display, unavailable/redacted
  evidence, model fallback identity, concurrent run updates, and legacy runs.
- Package smoke test builds the dashboard and Python distribution, upgrades a
  fixture data directory, starts the service, and retrieves dashboard/API
  projections.
- Restart/recovery test verifies a leased run continues with a rebuilt context
  and no duplicate or cross-run telemetry.

#### Exit

An operator can answer, from one run detail view: what policy and stack context
the task actually had; which tools it could use; what content may be viewed;
which model/provider was used; why fallback occurred; and what CLI action is
needed next.

---

## 5. Implementation sequence and dependency graph

```text
R1 execution isolation
        │
        ├──────────────► R6 recovery/migration/operator evidence
        │
        ▼
R2 effective policy + reproducible context
        │
        ├──────────────► R3 artifact/capture unification
        │                    │
        │                    └──► R6 dashboard/API hardening
        │
        ├──────────────► R4 generic pack execution
        │                    │
        │                    └──► PM5.A / PM5.C / PM5.D
        │
        └──────────────► R5 real local-model proof
                             │
                             └──► PM5.A / PM5.B / PM5.E

R1 + R2 + R3 + R4 + R5 + R6
        │
        ▼
       PM5
```

Recommended order:

1. R1 first; do not hide concurrent-run bugs behind service serialization.
2. R2 next; it gives R3, R4, and R5 a single durable policy source.
3. Start R3 after R2’s contract is stable. Start R4’s branch inventory in
   parallel, but migrate packs only after `EffectiveTaskPolicy` exists.
4. Run R5 in parallel with R3/R4 once routing identity is represented by R2.
5. Finish R6 last, using the stable final schemas and projections.
6. Start PM5 only after all exit criteria in section 7 are met.

---

## 6. Test strategy and coverage gaps

The existing focused PM4 tests are valuable, but many test components in
isolation. This gate must add boundary and adversarial tests that exercise how
components compose.

### 6.1 Test layers

| Layer | Purpose | Required additions |
| --- | --- | --- |
| Unit | Pure policy, schema, parser, renderer, and backoff logic | Effective-policy resolution, profile rendering/budgeting, artifact visibility, route identity, executor-mode validation. |
| Contract | API/gateway/broker behavior at stable boundaries | Run-scoped hash authorization, capture behavior, OpenAI-compatible protocol failures, exact tool grant parity. |
| Graph/integration | Real coordinator/persistence/broker interaction | Parallel-run interleaving, resume under load, generic pack dispatch, live interface-analysis loop. |
| Browser | Operator interpretation from real service projections | Concurrent updates, policy display, legacy runs, unavailable content, correct route/cost identity. |
| Package/upgrade | Installed service behavior over time | Dashboard assets, database migrations, old-run readability, backup/restore fixture. |
| Opt-in live evaluation | Claims about real local/cloud model behavior | AMD endpoint contracts and scorecards; never required for hermetic unit CI. |

### 6.2 Minimum adversarial fixtures

Add deterministic fixtures for:

- concurrent runs whose model and connector calls intentionally interleave;
- same artifact bytes referenced by two runs with different roles/visibility;
- unknown, cross-run, expired, metadata-only, redacted, and full content;
- repository profile ambiguity, unsupported stacks, and repository changes
  between first run and resume;
- connector policy narrowing after a broad capability allowlist;
- interface-contract additions/removals/breaking changes and malformed schema;
- local model timeout, invalid tool call, malformed JSON, missing usage, route
  probe false-positive, cloud fallback denied, and circuit-open state;
- old database rows/manifests/artifacts with missing new fields; and
- hostile external/validation output that looks like an instruction but must
  remain data and be displayed only within capture policy.

### 6.3 Test-quality rules

- Do not prove isolation with a single sequential mock call; use barriers or
  controllable fakes to force interleaving.
- Do not test capture policy only through model-event endpoints; test every
  artifact/content/download route.
- Do not count a generic final report as proof a specialist tool loop ran;
  assert typed tool receipts and referenced artifacts.
- Do not use the real AMD endpoint as a unit-test dependency; make it an
  explicit operator/evaluation profile with recorded environment metadata.
- Maintain property/table-driven coverage for every canonical pack and every
  capture level so adding a pack/content class requires an explicit decision.

---

## 7. PM5 entry gate

PM5 may begin only when all items below are demonstrated in CI, package smoke
tests, or a documented opt-in local-model evaluation as appropriate.

### Required technical outcomes

- [ ] Concurrent runs have isolated model telemetry, tool receipts, event
      attribution, workspace leases, and budget ledgers.
- [ ] Resume reconstructs run-scoped execution context deterministically.
- [ ] Every task has a persisted effective policy; advertised tools, broker
      grants, and dashboard data agree.
- [ ] The model receives a pinned, bounded stack-profile resource where one is
      applicable, and resume preserves it.
- [ ] Every artifact/content reference is run-authorized, classified, and
      capture-policy controlled.
- [ ] Interface analysis/technical spike performs and cites its declared tools
      and typed evidence artifacts.
- [ ] Existing packs execute through registered generic dispatch with no new
      workflow-name branches on the current path.
- [ ] At least one actual local OpenAI-compatible model route has measured
      admission evidence and an explicit named cloud fallback.
- [ ] Dashboard/API can explain effective policy, evidence availability, and
      actual model route for current and legacy runs.
- [ ] Data migration, package installation, restart, and dashboard smoke tests
      pass from a representative existing `.product-factory` directory.

### Required operator outcomes

- [ ] An operator guide explains legacy-data behavior, capture availability,
      local/cloud route labels, restart/recovery, and known local-model limits.
- [ ] A usability walkthrough can identify a blocked task, its actual grant,
      validation/source evidence availability, repair lineage, cost route, and
      the host/CLI action needed to continue.
- [ ] The implementation tracker’s PM4 status accurately reflects verified
      completion; PM5 remains unstarted until this gate passes.

---

## 8. Explicit deferrals

This gate deliberately does **not** add:

- release, deployment, incident, or health workflow packs;
- production deployment access, automatic rollback, PR creation, or server
  push to a laptop/origin repository;
- public multi-tenant authentication/authorization;
- arbitrary shell/web/MCP access;
- domain-specific medical/EHR connectors or compliance conclusions;
- autonomous full-lifecycle chaining; or
- a new CLI, dashboard mutation controls, or backend-for-frontend service.

Those are PM5 or later concerns. The purpose here is to make the already
implemented workflow foundation dependable enough that PM5 expands authority
through explicit policy rather than accumulated special cases.

---

## 9. Definition of done for an implementation agent

An implementation agent working on any R-package should deliver:

1. a narrowly scoped design note or ADR when changing a durable contract,
   authority boundary, or migration behavior;
2. additive schema/API changes with historical-read behavior documented;
3. focused unit/contract tests plus at least one composition-level test for the
   package’s central claim;
4. documentation updates to the relevant operator and architecture documents;
5. no unrelated formatting or refactoring of the dirty worktree; and
6. an explicit statement of which PM5 entry-gate checkboxes the change enables
   but does not yet close.

No package may claim completion solely because a component-level test passes.
Its specified exit condition must be demonstrated at the authoritative runtime
and, where relevant, through the read-model/dashboard surface.
