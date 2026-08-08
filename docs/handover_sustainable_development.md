# Product Factory — Post-PM5 Sustainability and Simplification Handover

**Status:** implementation handover based on a post-PM5 architecture and code
review  
**Review baseline:** repository state at commit `5d12283`  
**Audience:** humans and AI agents maintaining the orchestration kernel,
workflow packs, skills, connectors, host integrations, observability,
evaluation, persistence, and release process  
**Purpose:** close the remaining trust and execution gaps, remove obsolete
surfaces, reduce the cost of change, and establish a sustainable development
loop before broadening the product or its authority.

**Companion documents:**

- [Product vision and long-term architecture](handover_post_mvp.md)
- [Completed post-MVP implementation tracker](next-work-packages-post-mvp.md)
- [Pre-PM5 refactoring handover](handover_post_mvp_refactoring.md)
- [Workflow portfolio](handover_post_mvp_workflows.md)
- [Capability, tool, connector, and skill portfolio](handover_post_mvp_skills.md)
- [Skill granularity and composition](handover_post_mvp_skill_granularity.md)
- [Remote orchestration](handover_remote_orchestration.md)
- [Concept description](concept_description.md)

---

## 1. Executive decision

Product Factory is no longer merely an orchestration experiment. It now has a
credible, coherent kernel for bounded multi-agent work:

- an existing-host-first OpenCode integration rather than a replacement CLI;
- a versioned workflow-pack portfolio covering discovery through operations;
- typed task plans, isolated worktrees, validation, review, repair, approval,
  budgets, resumability, and durable projections;
- a brokered tool and connector boundary with explicit grants;
- local/cloud route identities, fallback policy, and cost observability;
- remote host control, server-owned workspaces, and laptop-owned delivery;
- a monitor-only dashboard and resumable SSE stream; and
- an evaluation framework with fixtures, scorecards, and regression gates.

The implementation also has unusually good hermetic verification for a project
at this stage. The reviewed baseline passed 894 non-integration Python tests,
Ruff, BasedPyright, 52 OpenCode plugin tests and type-checking, and the dashboard
unit/type checks.

The honest conclusion, however, is:

> Product Factory is **architecturally credible and contract-rich, but not yet
> operationally proven across its claimed lifecycle**. Several PM5 and
> cross-run contracts are represented by schemas, mock fixtures, and
> deterministic composition while the runtime either trusts caller assertions
> or reports successful placeholder task execution.

This is not a reason for another rewrite. It is a reason to pause horizontal
feature growth and complete a short sequence of safety, execution-truth,
simplification, and empirical-validation work.

### 1.1 Immediate decision

Do not enable a write-capable deployment connector outside a disposable test
environment until SD0 in this handover is complete. In particular:

1. cross-run handoffs must be resolved against durable producer artifacts;
2. operator approvals must be authoritative durable records, not request data;
3. the unauthenticated WebSocket route must be removed or secured; and
4. repository context assembly must share the same confinement policy as tool
   access.

After SD0, complete the missing task executors before claiming PM5 workflow
readiness. Release, operations, documentation, security, test-design, and test-
execution tasks must do the work declared by their executor mode or fail
closed; no capability may succeed through a generic stub.

### 1.2 What not to do

The review found no justification for:

- replacing FastAPI, SQLite, React, MCP, or the OpenAI-compatible model plane;
- creating a new CLI or backend-for-frontend;
- collapsing the workflow portfolio into one autonomous `full_sdlc` run;
- moving to a multi-tenant architecture;
- replacing SQLite with a network database for the current single-operator
  topology;
- adding more workflow packs before the existing executor modes are truthful;
  or
- optimizing model prompts or concurrency without real latency and quality
  measurements.

The sustainable direction is consolidation and proof, not another platform
expansion.

---

## 2. Review basis and confidence

This assessment traced the implementation from public host surfaces through
planning, policy resolution, context assembly, model/tool execution,
validation, persistence, observability, remote delivery, and evaluation. It
also compared the implementation with the goals and locked boundaries in the
companion handovers.

### 2.1 Verification performed

| Surface | Result |
| --- | --- |
| Python non-integration suite | 894 passed, 3 skipped, 14 deselected |
| Python lint | Ruff passed |
| Python static types | BasedPyright passed with no errors or warnings |
| OpenCode plugin | 52 tests passed; type-check passed |
| Dashboard | 4 unit tests passed; TypeScript check passed |
| Working tree before this document | Clean |

The Python suite emitted one Starlette/httpx `TestClient` deprecation warning.
It is not a functional failure, but a frozen dependency set should remove it
before it becomes a surprise upgrade failure.

### 2.2 What this review did not prove

The following opt-in or external paths were not executed as part of this
assessment:

- an end-to-end workflow against the AMD AI MAX local model runtime;
- live Tavily, source-fetch, Git/CI, operations, or real deployment systems;
- Docker remote integration and restart recovery under real load;
- OpenCode driving a real remote run from a laptop;
- browser automation against the dashboard;
- DeepSWE, SWE Atlas, or Terminal-Bench/Harbor; and
- restore and retention drills against a long-lived production-sized data
  directory.

Accordingly, a green hermetic suite is evidence that contracts and fixtures are
internally consistent. It is not evidence that every workflow produces useful
real-world outcomes or that every operational boundary survives failure.

### 2.3 Review scale

The Python product code is approximately 40,800 lines. The largest maintenance
hotspots are:

| Module | Approximate size | Sustainability implication |
| --- | ---: | --- |
| `orchestration/coordinator.py` | 5,010 lines | Lifecycle, workflow behavior, task execution, composition, and finalization remain too concentrated. |
| `validation/pipeline.py` | 1,472 lines | Validator registration and implementation need modular ownership before the portfolio grows. |
| `host/service.py` | 1,089 lines | Host application service, compatibility behavior, streaming fallback, and artifact projection are mixed. |
| `workflows/default_plans.py` | 1,057 lines | Eleven workflow plan templates in one file make ownership and focused testing harder. |
| `persistence/database.py` | 1,027 lines | Schema creation, migrations, and all repository methods share one connection-oriented module. |
| `integrations/opencode-plugin/src/pf-client.ts` | 971 lines | Protocol types, transport, polling, and delivery logic are manually mirrored in one client. |

File size alone is not a defect. Here it correlates with mixed ownership and
multiple sources of truth, so the refactoring recommendations below are based
on behavior boundaries rather than arbitrary line-count targets.

---

## 3. Vision-to-implementation assessment

| Goal | Current reach | Honest assessment |
| --- | --- | --- |
| Use Product Factory from OpenCode and other existing hosts | OpenCode plugin, host/v1 HTTP/CLI, MCP adapter, remote client | **Strong.** The product boundary is correct and should be preserved. The top-level local CLI still bypasses the host application service in places, so the control path is not fully unified. |
| Support research, planning, architecture, implementation, testing, release, deployment, and maintenance | Eleven registered workflow packs and seventeen capabilities | **Broad contract coverage, uneven runtime depth.** Discovery, planning, repository change, review, and repair have meaningful execution paths. Several PM5 and analysis capabilities still complete through deterministic composition or a successful stub. |
| Extensive skills and contextual capabilities for small models | Twelve packaged skills plus source/policy profiles and stack detection | **Good design, unproven portfolio value.** Granularity and authority separation follow the vision. No live skill scorecard has been recorded, so specialization has not yet earned its maintenance cost empirically. |
| Wide tool support including MCP and web research | Static connector registry, Tavily, source fetch, filesystem MCP, Git/CI, ops-read, deployment adapter | **Sound control architecture.** Most connectors are disabled or mock-backed, which is correct by default but means breadth is primarily contractual. Native host tools remain outside the remote worker unless exposed through an intentional connector. |
| Local models where feasible with frontier fallback | OpenAI-compatible adapter, probes, admission, circuit breaker, named fallback, route/cost telemetry | **Strong mechanism, incomplete proof.** The default “local” routes still point to OpenRouter stand-ins, and the live local test proves protocol admission rather than task quality. The central cost/quality thesis is not yet demonstrated on the AMD host. |
| Durable plan, agent, prompt, repair, cost, and evidence observability | SQLite projections, capture-aware content ownership, SSE, dashboard | **Strong backend design.** The dashboard is thin and projection-led as intended. Browser coverage is very small, remote token use is inconsistent with the local-only UI, and an obsolete unauthenticated WebSocket route weakens the boundary. |
| Remote orchestration with server-owned execution and local landing | Remote mode, registered/git-ref workspaces, uploads, delivery bundle, OpenCode integration | **Credible first topology.** The trust boundary is sensible. Shutdown/drain, request bounds, client contract generation, and live restart drills need work before unattended use. |
| Evaluation-driven model, prompt, and skill improvement | Bench runner, private cases, experiment registry, scorecard and regression-gate types | **Framework present, feedback loop mostly empty.** Historical OpenRouter code-change tests provide useful evidence for the original orchestration thesis, but broad workflow, local-route, and skill scorecards are not recorded. External benchmark adapters remain stubs. |
| Safe approval-gated external effects | Deployment pack, connector manifest, target allowlist, idempotency and rollback receipts | **Not yet trustworthy.** The connector is disabled by default and production targets are prohibited, which limits exposure. When enabled, its approval check trusts a caller-supplied binding rather than an authoritative operator decision. |
| Sustainable extensibility | Pack registry, handlers, `PackExecutionPolicy`, effective task policy | **Partially achieved.** Pack metadata is much better than pre-refactor behavior, but executor dispatch, model/profile selection, composition callbacks, and workflow-name branches remain duplicated in the coordinator and scheduler. |

### 3.1 Product maturity statement

The most defensible current statement is:

> Product Factory is a well-tested single-operator orchestration platform for
> bounded discovery, planning, repository work, review, and evidence capture,
> with credible remote, local-routing, release, operations, and deployment
> foundations. The latter capabilities require safety closure and real-world
> evaluation before they should be described as operationally complete.

This distinction should be reflected in the README, architecture docs, and
roadmap. “Implemented” should mean the code path exists and is tested.
“Operationally proven” should require a versioned live evaluation or operator
drill.

---

## 4. Locked sustainability rules

These rules are normative for the work packages below.

| Rule | Required interpretation |
| --- | --- |
| One authority per fact | Run state, handoff state, approval state, event ordering, route policy, and artifact ownership each have one authoritative durable representation. Files may be exports or projections, not competing state stores. |
| No successful placeholders | Unknown capability, unknown executor mode, unavailable connector, missing evidence, or unimplemented behavior fails before execution or produces an explicit blocked/unsupported result. It never returns success with “stub.” |
| Client assertions are untrusted | A digest, producer ID, handoff state, approval ID, route preference, or artifact label supplied by a host is a lookup request—not proof. Resolve it against server-owned records. |
| Effective policy remains exact | The persisted effective task policy is the same policy used by the prompt, broker, model router, validator, and dashboard. |
| Repository reads are tool-grade | Context assembly, stack detection, search, manifests, and model excerpts obey the same containment, symlink, secret, generated-directory, and size rules as brokered file tools. |
| Existing-host first | OpenCode is the primary UX, host/v1 is the stable application protocol, and MCP is an adapter. Do not rebuild host-native editing/chat/navigation. |
| Local-first is measured | A profile is promoted to a local default only from real task scorecards, not protocol probes or synthetic fixture success. |
| Deletion is a feature | Compatibility surfaces receive an owner, usage evidence, and removal date. Unsupported legacy paths are removed rather than indefinitely mirrored. |
| Optimize from telemetry | Improve context scans, persistence, concurrency, and prompt size from p50/p95 measurements and quality/cost effects. Do not trade correctness for unmeasured speed. |
| Single-operator boundary remains | Do not introduce tenancy, public exposure, or distributed consistency unless a validated product need changes the architecture. |

---

## 5. Critical findings

### F-01 — Cross-run handoffs are typed claims, not verified handoffs

**Priority:** P0 integrity blocker  
**Affected areas:** `domain/artifacts.py`, `workflows/handoffs.py`, workflow
handlers, artifact instances, host submit path

`HandoffRef` contains a schema ID, digest, producer run/task, role, and claimed
state. `validate_pack_handoffs` validates shape and pack compatibility, but it
does not prove that:

- the producer run and task exist;
- that task produced an artifact with the supplied digest;
- the artifact instance has the supplied schema and role;
- the bytes still hash to the digest;
- the producer artifact has the claimed handoff state;
- the producer run was approved where approval is required; or
- the consuming run is permitted to read the producer artifact.

Several handlers then render the supplied values as provenance or use them as
acceptance/evidence references. A caller can therefore submit a syntactically
valid but fictitious “approved” handoff. This breaks the central vision of
typed, content-addressed, evidence-bearing workflow composition.

#### Required correction

Introduce a server-side `HandoffResolver` invoked at submit and again before
execution/resume. It must:

1. look up the exact producer run, task, and `ArtifactInstance`;
2. compare digest, schema ID/version, artifact role, producer task, and stored
   handoff state;
3. verify the content bytes when the artifact is promoted or consumed;
4. enforce pack compatibility and capture/classification policy;
5. return an immutable `ResolvedHandoff` containing only trusted values;
6. persist the resolved producer instance ID and resolution event on the
   consumer run; and
7. copy or mount authorized content into a run-scoped immutable input area if
   a later task needs the body.

The model and handler consume `ResolvedHandoff`, not the raw request object.
The raw reference remains useful for audit but never supplies authoritative
state.

Handoff state transitions must be server-owned. A client may request approval
or selection, but it cannot change `draft` to `approved` by changing JSON.

#### Required tests

- unknown producer run/task/artifact fails before a model or connector call;
- digest, schema, role, state, and producer mismatches each fail separately;
- a cross-run digest collision does not authorize the wrong artifact instance;
- a superseded or unavailable artifact cannot be consumed;
- a valid approved handoff survives restart and resolves identically;
- a consumer cannot read unrelated producer content by supplying its hash; and
- every workflow declaring accepted handoffs has at least one real producer →
  consumer contract test.

#### Exit

No workflow handler treats fields from raw `RunRequest.handoff_refs` as
trusted. Every accepted handoff is traceable to one durable producer artifact
instance and state transition.

### F-02 — Deployment approval can be self-asserted by the caller

**Priority:** P0 external-effect blocker  
**Affected areas:** `orchestration/coordinator.py`, `connectors/broker.py`,
`workflows/deployment_execution.py`, host approval routes, `approvals` table

The database schema defines an `approvals` table, but the runtime does not
insert, query, or consume it. The current deployment grant checks that the
request contains:

- a non-empty `approval_binding.approval_id`;
- a release plan whose request payload says `outcome == "ready"`; and
- matching release, artifact, target, and change-window values elsewhere in
  the same request.

The test happy path creates an arbitrary approval ID and supplies both sides of
the comparison. This proves internal consistency, not operator approval. If an
operator enables `allow_write_connectors` and `staging_deploy`, an authenticated
control client can manufacture a self-consistent binding and reach the
deployment connector.

The default-disabled connector and production-target prohibition reduce the
current blast radius; they do not make the approval contract correct.

#### Required correction

Create a durable `ApprovalService` whose records are authoritative. At minimum,
an approval grant must contain:

- random approval ID and schema version;
- action type such as `deploy_nonproduction`;
- source release run and exact `ReleasePlan` artifact instance;
- release-plan digest, deployment artifact digest, target ID, and change
  window;
- idempotency key or action fingerprint;
- state (`pending`, `approved`, `rejected`, `expired`, `revoked`, `consumed`);
- actor/source and decision timestamp;
- optional expiry; and
- consuming deployment run/action and reconciliation status.

Only an authenticated mutation surface may transition the grant. The worker
must resolve the approval ID from the durable store and compare trusted fields
to trusted handoff and target records. A model, workflow pack, skill, request,
or connector invocation option cannot set `_approval_binding_verified`.

The existing run `approval.json` may remain as a human-readable projection,
but it must not be the state authority. The `approvals` table should either
become authoritative through versioned repository methods or be removed and
replaced by a clearly owned equivalent.

Define replay semantics deliberately. Retrying the same idempotent deployment
may reuse the same approval for the same action fingerprint; a different
artifact, target, window, or idempotency key requires another decision.

#### Required tests

- forged, unknown, rejected, expired, revoked, and cross-run approvals fail;
- changing one bound field fails before any connector call;
- a valid approval permits only the bound target and artifact;
- reuse for the same idempotent reconciliation is safe and audited;
- reuse for a different action is denied;
- approval state survives restart; and
- connector policy tests assert a durable approval lookup, not merely the
  presence of request fields.

#### Exit

The deployment connector cannot be reached without a durable operator decision
resolved independently from the deployment request.

### F-03 — The obsolete WebSocket stream bypasses API authentication

**Priority:** P0 security boundary  
**Affected areas:** `api/app.py`, observability docs, WebSocket test

REST and SSE routes use an `APIRouter` dependency that enforces loopback or
Bearer authentication. `/api/v1/events/ws` is registered directly on the app,
accepts the socket immediately, and performs no equivalent authentication.
When the service is remotely reachable with a token, this route can expose
global or selected-run events without that token.

No production client in the repository uses the WebSocket stream. The
dashboard, Python remote client, OpenCode plugin, and host tail path use SSE.

#### Required correction

Remove the WebSocket endpoint, its test, imports, and documentation claims.
SSE is the canonical resumable live protocol and already satisfies the product
need.

If an undiscovered external consumer makes removal impossible, deprecate it in
one protocol release and implement authentication before `accept()`, origin
policy, bounded subscriptions, cursor tests, and remote-mode security tests.
Do not put a long-lived bearer token in a browser query string.

#### Exit

Every remotely reachable read stream crosses the same tested authentication
boundary, and the API advertises only supported stream protocols.

### F-04 — Context assembly can read outside the repository through symlinks

**Priority:** P0 confidentiality and prompt-integrity blocker  
**Affected areas:** `context/assembler.py`, stack/profile detection, repository
inventory

`list_repository_paths` and `select_repository_excerpts` recursively walk the
repository, call `is_file()`, and read paths directly. A file symlink inside a
repository can resolve outside the repository and be inserted into a model
prompt. These functions do not use `ToolBroker` confinement or the configured
`prohibited_path_globs` policy.

The same scan also builds a candidate list for the whole tree before applying
the file-count limit and does not exclude common generated trees such as
`node_modules`, `dist`, or `build`. This is both a security gap and a concrete
large-repository latency risk.

#### Required correction

Create one immutable `SafeRepositoryInventory` per pinned repository snapshot.
All context selection, stack detection, repository summaries, and search
preselection must consume it. The inventory must:

- resolve and verify canonical containment under the snapshot root;
- reject symlinked files and directories by default;
- apply configured prohibited globs and a documented generated-directory
  denylist;
- prefer tracked files plus explicitly admitted untracked files;
- enforce per-file, total-byte, file-count, and scan-time ceilings;
- record exclusions and truncation in the prompt manifest; and
- cache by repository identity/base revision plus policy digest.

Do not fix this with another slightly different ignore list inside the context
assembler. Repository tools and implicit context must share a policy component.

#### Required tests

- file and directory symlink escape attempts;
- `.env`, secrets, prohibited glob, generated tree, binary, device/special file,
  oversized file, and invalid UTF-8 handling;
- a large `node_modules` tree does not dominate scan time;
- inventory cache identity changes with base revision or policy;
- prompt manifests explain omissions; and
- tool-visible and context-visible path sets cannot contradict each other.

#### Exit

No repository byte reaches a prompt through a path the effective task policy
would deny to a brokered read tool.

---

## 6. Runtime truth and workflow completeness

### F-05 — `PackExecutionPolicy` declares executor modes that the runtime does not dispatch

**Priority:** P1 correctness and product-claim gap

The pack layer declares executor modes such as `model_draft`, `validation`,
`research_agent_loop`, and `composition`. `_execute_task`, however, still
dispatches primarily by capability with a long conditional chain. Capabilities
that do not match a branch end with:

```text
Task <capability> completed (stub)
```

and a successful status.

At the reviewed baseline this affects at least `release_analysis`,
`operations_analysis`, `security_review`, `documentation`, and
`test_execution`. `test_design` is intercepted by a deterministic repository-
listing branch even though its declared mode is `model_draft`. Pack handlers
can subsequently compose convincing release or operational artifacts from
request fields and mock dependency outputs, so graph tests pass without proving
that the analysis task called a model, CI connector, operations connector, or
validator executor.

This is the largest gap between the completed roadmap and usable lifecycle
coverage. It is also fail-open behavior: an unimplemented capability is
reported as success.

#### Required correction

Introduce a registered `TaskExecutor` interface keyed by
`EffectiveTaskPolicy.executor_mode`, not workflow name or an open-ended
capability conditional. Initial executors should be:

| Executor | Responsibility |
| --- | --- |
| `RepositoryAgentExecutor` | Bounded inspect/edit/test loop for implementation and repair. |
| `ResearchAgentExecutor` | Read-only research/tool loop with source ledger and cited draft. |
| `InterfaceAgentExecutor` | Local contract analysis and synthetic-fixture loop. |
| `ModelDraftExecutor` | One-shot or bounded read-only tool loop producing a typed draft; use for release, operations, security, documentation, test design, and independent review only where its task adapter defines the schema. |
| `ValidationExecutor` | Run registered deterministic validations and return typed evidence; never synthesize a successful test execution. |
| `DeterministicExecutor` | Named deterministic operations such as repository inventory or controlled deployment state machine. No generic fallback. |
| `CompositionExecutor` | Invoke registered pack composition with typed dependency artifacts. |

Each capability supplies a small, registered task adapter describing its agent
profile, output schema, runtime directive, and result parser. An unknown
executor mode or a missing adapter fails during pack registration or plan
compilation.

Mock/deterministic workers must be explicitly marked test fixtures in results
and telemetry. They must never be a live fallback after a provider failure.

#### Required tests

- a table-driven test covers every registered capability and executor mode;
- each capability either invokes the expected model/tool/validator path or
  fails explicitly;
- no source string `completed (stub)` remains;
- release-readiness fake-live tests prove Git/CI/ops calls and model draft use;
- incident and service-health fake-live tests prove bounded ops reads and
  observation/inference separation;
- test execution records actual registered-command results;
- unavailable mandatory connectors block the task rather than yield a ready
  artifact; and
- deterministic/mock status is visible in manifest, events, and dashboard.

#### Exit

Executor policy is executable truth. Every registered capability has a real,
tested implementation, and adding a capability without one fails at startup or
pack registration.

### F-06 — Capability profile and routing data have multiple sources of truth

**Priority:** P1 architecture and observability correctness

The coordinator maintains a capability → agent-profile dictionary. The
context assembler maintains the profile prompts. The scheduler separately
maintains capability → model-profile rules. Workflow packs contain
`routing_defaults`, but those defaults are included in pack identity without
being consumed by routing.

New capabilities such as release analysis, operations analysis, and deployment
execution are missing from the coordinator's agent-profile map and silently
fall back to `implementation_worker`. This makes the effective policy and
dashboard misleading and can give a release or operations model an
implementation-oriented prompt.

#### Required correction

Create one trusted `CapabilityDescriptor` registry with at least:

- capability ID and version;
- executor mode/adapter;
- agent profile ID;
- default model role/profile policy;
- permissible tool classes;
- result schema/parser;
- default budget shape; and
- evaluation fixture/category.

Pack policy narrows this descriptor. It does not redefine the same fields in a
second unvalidated format. `routing_defaults` must either become a typed,
consumed policy or be removed from the pack contract.

Persist both requested routing policy identity and resolved route/model
identity. The dashboard should show the descriptor/profile actually used.

#### Exit

There is one reviewable mapping from capability to execution semantics. A test
iterates the registry and proves that prompt profile, model route, tools, parser,
and evaluation category are defined for every capability.

### F-07 — Pack extensibility is real at registration but incomplete end to end

**Priority:** P1 maintainability

The registry and `PackExecutionPolicy` are valuable improvements. However:

- `WorkflowType` remains a hard-coded `Literal`, so a newly registered pack is
  not automatically accepted through `RunRequest` or the API;
- coordinator constants still special-case code-change and technical-plan
  aliases;
- composition handlers receive a callback-heavy `ComposeContext` whose
  functions are owned by the coordinator;
- effective-policy resolution retains workflow-name sets and hard-coded repair
  eligibility;
- eligible-next-action logic contains special cases outside handlers;
- validation behavior can be declared in both `validation_policy` and
  `execution_policy`; and
- `findings_are_deliverable` can be declared by pack policy and handler code.

The current extensibility test proves in-process registration, not successful
submission and execution through the public host protocol.

#### Required correction

1. Accept a bounded validated string at the host boundary and resolve it through
   the trusted registry. The OpenAPI may expose an enum generated from built-in
   packs without making the domain model a static `Literal`.
2. Move workflow aliases into versioned registry metadata with removal dates.
3. Replace coordinator callback bags with explicit services passed to handlers,
   such as `ArtifactComposer`, `ValidationEvidenceReader`, and
   `DocumentDraftService`.
4. Make `PackExecutionPolicy` the single home for validators, repair policy,
   approval policy, handoff compatibility, output roles, and deliverable
   findings.
5. Add an end-to-end extension test that registers a fixture pack, submits it
   through host/v1, executes its declared mode, observes it, and validates its
   output without editing coordinator, scheduler, API union types, or
   dashboard code.

#### Exit

A new trusted read-only pack requires a pack, handler/composer where needed,
fixtures, and registry entry—no workflow-name branch in shared lifecycle code.

---

## 7. Architecture and maintainability findings

### F-08 — `RunCoordinator` remains a god object

**Priority:** P1 sustainable change cost

The earlier refactoring correctly introduced `RunExecutionContext`, effective
task policy, artifact instances, and pack handlers. It did not finish the
behavioral decomposition. `RunCoordinator` still owns:

- new-run and resume lifecycle;
- planning and plan compatibility;
- wave scheduling and worktree inheritance;
- context and prompt assembly;
- all task execution modes;
- validation and repair-loop decisions;
- workflow-specific composition helpers;
- final manifest/output behavior;
- approval, rejection, revision, cancellation, and apply; and
- event emission for all of the above.

The main `_execute` and `_execute_task` flows are too large for agents or humans
to change safely without loading unrelated lifecycle details.

#### Target decomposition

Do this incrementally behind characterization tests; do not rewrite the engine.

```text
Host application service
        │
        ▼
RunLifecycleEngine
  submit / resume / cancel / finalize
        │
        ├── PlanService + PackRegistry
        ├── WaveScheduler + WorktreeLineageService
        ├── EffectivePolicyResolver
        ├── TaskExecutorRegistry
        ├── ValidationAndRepairService
        ├── CompositionService
        ├── ApprovalService
        └── RunRepository + EventRecorder + ArtifactRepository
```

`RunCoordinator` may remain as a compatibility facade while commands and tests
migrate. Its end state should delegate application operations rather than own
workflow implementations.

#### Extraction order

1. Task executor registry and capability adapters (required by F-05).
2. Approval and handoff resolver (required by SD0).
3. Composition service and typed dependency reader.
4. Validation/repair service.
5. Wave scheduler and worktree lineage.
6. Run finalizer and lifecycle facade.

Keep each extraction behavior-preserving and delete the corresponding
coordinator branch in the same change. Do not create thin modules that still
call private coordinator methods through `Any` callbacks.

### F-09 — Persistence has outgrown bootstrap SQL and one shared connection

**Priority:** P1 durability and development safety

The database layer uses a single SQLite connection with
`check_same_thread=False`, method-level locking on many writes, startup
`CREATE TABLE`, and ad hoc `_ensure_column` migrations. Some reads and
evaluation-store writes bypass the synchronization wrapper. `EvalStore`
creates additional schema independently and dual-writes a legacy
`evaluation_runs` table.

SQLite is still appropriate. The sustainable change is a clearer SQLite
boundary:

- a versioned `schema_migrations` ledger and transactional migrations;
- repository modules by aggregate (`runs`, `tasks`, `events`, `artifacts`,
  `approvals`, `workers`, `evaluations`);
- explicit transactions for multi-row state transitions;
- a connection-per-thread policy, a small pool, or a single database actor;
- foreign keys enabled and tested;
- indexes derived from observed query plans; and
- one migration/backup compatibility policy per released schema version.

Avoid an ORM migration unless it demonstrably reduces complexity. SQL is not
the problem; unowned schema evolution is.

### F-10 — Shutdown does not drain active workers

**Priority:** P1 remote durability

`HostService.close()` stops the lease scanner and immediately closes the
coordinator database. `WorkerSupervisor.stop()` explicitly leaves active daemon
workers running. A FastAPI shutdown or service restart can therefore close the
shared database while a worker or heartbeat thread is still using it.

#### Required correction

- stop accepting new work;
- stop the scanner from spawning recovery work;
- signal cooperative shutdown/cancellation according to run policy;
- wait for active workers for a bounded grace period;
- preserve or explicitly release leases according to the observed outcome;
- close database connections only after worker threads finish; and
- report forced shutdown and recovery-required runs durably.

Add lifespan tests that stop the service during planning, a model wait, a tool
call, validation, and deployment reconciliation, then restart and verify a
single correct continuation.

### F-11 — Artifact bytes and backup coverage need crash-consistent handling

**Priority:** P2 durability

`ArtifactStore.put_bytes` writes directly to the final hash path and skips the
write whenever that path exists. A process crash can leave a partial blob that
is then trusted without verifying its digest. Use a same-filesystem temporary
file, flush/fsync where appropriate, verify size/hash, and atomically rename.
Reads used for trusted handoffs should verify the digest or rely on a verified
immutable-blob marker.

The backup helper correctly uses SQLite's online backup API. It then copies
`runs`, `ops`, and `uploads` while they may still be changing and records a hash
only for the SQLite file. Improve it with:

- a quiescent snapshot or captured high-water mark;
- a per-file checksum manifest;
- explicit inclusion/exclusion for blobs, content, uploads, and experiments;
- configuration/skill/profile backup guidance, because they live outside the
  data root;
- restore validation against artifact instances and event/run rows; and
- periodic automated restore drills.

### F-12 — There is no retention or garbage-collection lifecycle

**Priority:** P2 operations

Runs, events, prompts, captures, worktrees, uploads, blobs, model records,
benchmarks, and SQLite WAL/database pages grow indefinitely. Artifact instances
carry a `retention` string, but no operator command enforces it.

Add a safe maintenance service and CLI with:

- dry-run inventory by data class, run, age, size, and retention class;
- pinned runs/experiments that cannot be removed;
- reachability-based artifact/content cleanup;
- stale upload, scratch, and worktree cleanup;
- configurable disk warning and stop-admission thresholds;
- WAL checkpoint and optional `VACUUM` maintenance windows;
- backup-before-prune policy; and
- an append-only maintenance audit.

Deletion targets must be explicit IDs, never broad unresolved paths. Default to
dry run and require a deliberate confirmation for material deletion.

---

## 8. Host, protocol, dashboard, and client findings

### F-13 — There are too many ways to invoke the coordinator

**Priority:** P2 consistency

The architectural source of truth is `product-factory.host/v1`, but several
top-level CLI commands construct `RunCoordinator` directly. The host CLI uses
`HostService`, the remote CLI uses `RemotePfClient`, the HTTP layer caches
`HostService`, MCP adapts the host, and the OpenCode plugin implements another
client. This makes behavior and deprecation harder to keep aligned.

Make a framework-neutral application service the only mutation boundary.
Local CLI commands should call it in-process or through loopback HTTP. Remote
clients call the same host protocol. `RunCoordinator` becomes an internal
engine detail.

Keep low-level operator commands for database diagnostics, backup, restore,
and offline repair, but mark them explicitly as administrative rather than
alternate run semantics.

### F-14 — Public request fields promise behavior the runtime ignores

**Priority:** P2 protocol cleanup

`model_profile_set` appears in local CLI, host CLI, remote client, MCP, API,
and `RunRequest`, but the coordinator emits a warning that it is ignored.
`project_profile` is also effectively unused. `requested_artifacts` is a
deprecated alias for typed artifact overrides.

An ignored routing field is worse than no field because an operator can believe
they selected a cost/security policy that was not applied.

For host protocol v2:

- remove `model_profile_set` and `project_profile`, or replace them with a
  validated operator-authorized `routing_policy_id` that is actually resolved;
- remove `requested_artifacts` after one documented compatibility window;
- keep resolved profile/route identity in projections and receipts; and
- reject unknown fields in mutation bodies after clients migrate.

Do not allow a model or untrusted host message to choose an arbitrary provider
profile. Routing overrides remain bounded by operator configuration and budget
policy.

### F-15 — Type and schema mirroring across clients is manual

**Priority:** P2 client maintenance

The 971-line OpenCode `pf-client.ts` and the Python remote client manually
mirror host protocol shapes. The dashboard maintains another partial TypeScript
model. API drift has already required dashboard repair.

Generate or validate shared client types from versioned OpenAPI/JSON Schema:

- check in a canonical host protocol schema snapshot;
- generate TypeScript types or run a schema-compatibility check in CI;
- keep handwritten domain helpers separate from transport DTOs;
- split OpenCode transport, polling/SSE, delivery, and tool presentation;
- add cross-language golden request/response fixtures; and
- require an explicit compatibility decision for breaking schema changes.

This does not require a generated full SDK or a backend-for-frontend.

### F-16 — Remote dashboard guidance conflicts with its authentication behavior

**Priority:** P2 product-boundary clarification

The dashboard documentation correctly describes a loopback-only, single-user
UI. Remote architecture documents also describe using it through a tunnel with
a control token. The dashboard fetch client sends same-origin cookies only; it
does not send the API Bearer token. When a token is configured, API and SSE
requests from the browser fail even though the static shell loads.

Choose and document one supported boundary:

1. **Recommended now:** dashboard is loopback-only. Use a local observability
   process or an SSH tunnel whose server listener remains loopback and whose
   security does not depend on exposing a bearer token to JavaScript.
2. **Later, if validated:** add a small server-side login/session or trusted
   reverse-proxy authentication that issues an `HttpOnly`, `Secure`,
   same-site session. Do not store the control token in JavaScript or
   `localStorage`.

Whichever option is selected needs a real browser test. Preserve the monitor-
only boundary.

### F-17 — Browser coverage is disproportionate to dashboard scope

**Priority:** P2 test gap

The dashboard has four unit tests and no browser automation. The original
acceptance scope included real FastAPI navigation, DAG/kanban agreement, live
SSE refresh, capture-policy rendering, repair lineage, cross-run content
denial, and packaged-wheel serving.

Add a small Playwright suite against the real installed application. Focus on
the five user outcomes rather than component snapshots:

- find a blocked task and its next CLI action;
- see an event without a full reload;
- trace repair origin and replacement;
- distinguish unavailable, redacted, and full evidence; and
- understand current spend and local/cloud route.

Include an authenticated/unauthenticated case for the chosen deployment
boundary and a package smoke test that installs the wheel before serving.

### F-18 — Remote submission exposes test/debug controls and lacks explicit body bounds

**Priority:** P2 remote hardening

The public submit body exposes `mock`, `inline`, and `sync`. `ApiState` can cache
separate host services by mock flag against the same data directory. These are
useful test controls but should not be part of a stable remote protocol or
allow multiple service/supervisor instances to compete over the same database.

Move execution mode to server configuration or a test-only app factory. In
remote mode, reject client attempts to change it.

Add explicit limits for request-body bytes, request text, pack-input serialized
size/depth, handoff count, validation-command count, artifact overrides, and
metadata. Upload limits and rate limits do not bound ordinary JSON submissions.

---

## 9. Evaluation, local-model, and product-proof findings

### F-19 — The local-first thesis is implemented as routing, not yet proven as a product result

**Priority:** P1 product validation

The routing implementation is thoughtful: OpenAI-compatible profiles, local
route classification, model/capability probes, circuit breaker, named cloud
fallback, fallback reasons, budget checks, and cost-basis telemetry all exist.

The default `coding_worker` and `local_target_reviewer` nevertheless point to
OpenRouter as stand-ins. The opt-in local test checks model listing, structured
output/tool-call admission, and a small completion. It does not establish that
the AMD-hosted models can complete representative Product Factory tasks at an
acceptable quality, latency, and repair cost.

This matters because “many affordable local agents outperform one frontier
model economically” is the central commercial and architectural hypothesis.

#### Required proof loop

Run a versioned corpus on the actual AMD endpoint with at least these arms:

1. local-only, fail closed;
2. local-first with bounded cloud fallback;
3. cloud worker profiles;
4. orchestration disabled/single-agent baseline where comparable; and
5. skills enabled versus disabled for promoted skills.

Measure:

- accepted outcome and deterministic validator success;
- blocking defect escape rate;
- human correction/rework time;
- correct unknown/escalation rate;
- end-to-end and per-task p50/p95 latency;
- input/output/context tokens;
- local queue time and concurrency saturation;
- cloud fallback rate and reason;
- cloud spend and estimated local compute cost; and
- retry/repair count.

Store sanitized scorecards in a versioned location or publish a reproducible
summary. At present, `docs/skill-scorecards.md` explicitly records no live
scorecard runs.

Historical synthetic/live OpenRouter code-change benchmarks remain useful
evidence that review/repair orchestration can improve outcomes. Do not discard
them; also do not generalize them to discovery, release, operations, or local
models without corresponding data.

### F-20 — PMX infrastructure is complete, but the controlled-improvement loop is not operating

**Priority:** P1 sustainability

The experiment registry and regression-gate tests construct synthetic
scorecards and prove that gate logic behaves correctly. They do not produce
model/skill evidence. `ExternalSuiteCaseLoader` is still a stub, and there is no
Terminal-Bench integration.

Establish a routine rather than another large framework:

- a small private “golden work” corpus from real project tasks;
- weekly or release-candidate local/hybrid comparison;
- a human review field for correction effort and accept/reject rationale;
- a quarantine process for flaky or contaminated cases;
- a promotion record tying workflow/skill/model versions to scorecards; and
- rollback when a promoted route or skill crosses a regression threshold.

External suites are complementary:

| Benchmark | Use in Product Factory | Boundary |
| --- | --- | --- |
| SWE Atlas | Repository investigation, test generation, refactoring, and code understanding | Preserve its task-specific metrics; implement a version-pinned `CaseLoader`. |
| Terminal-Bench / Harbor | Multi-step terminal/tool execution, sandboxing, and environment setup | Run through the official isolated harness, never with host-process or production connector authority. |
| DeepSWE | Long-horizon engineering comparison | Gate on current availability, terms, harness compatibility, and contamination controls. Do not make it the only measure of product value. |

Start with one small version-pinned external subset. Real internal tasks and
human rework remain the primary product metric.

### F-21 — The deployment adapter is a simulator and should be named as such

**Priority:** P2 product clarity

`staging-local` uses an `in_process` adapter that mutates a local JSON state
machine. The opt-in live deployment test restarts that simulator and verifies
idempotency. This is valuable for testing change-control mechanics, receipts,
rollback, and reconciliation. It is not a real staging deployment integration.

Rename it and document it as `simulated_staging` or equivalent. Keep it as the
default hermetic connector. Add a real non-production adapter only after SD0
and only for a concrete target with:

- a narrow static operation set;
- an allowlisted target and credential scope;
- immutable artifact identity;
- authoritative approval;
- health and rollback receipts;
- unknown-outcome reconciliation; and
- a destructive-action drill in an expendable environment.

Do not add a generic shell-based deployment connector.

---

## 10. Configuration, documentation, build, and release findings

### F-22 — Several configuration fields are dead or duplicative

**Priority:** P2 simplification

Concrete examples include:

- `config/workflows.yaml` describes only legacy `code_change` and
  `architecture`; `WorkflowsConfig` is loaded but not used to control the
  registered pack portfolio;
- `routing_defaults` contributes to pack identity but is not used by the model
  scheduler;
- validation settings and deliverable-findings behavior have more than one
  declaration site;
- `EffectiveTaskPolicy.approval_required` is constructed as true even for
  workflows whose pack policy does not require approval, making observability
  less truthful; and
- capability IDs exist in both a `Literal` and a separate set.

For each field, choose one of three outcomes: make it typed and authoritative,
keep it as a generated projection, or remove it. Do not preserve unused
configuration merely because initialization copies it.

### F-23 — Python builds are not reproducible

**Priority:** P1 release engineering

`uv.lock` is intentionally ignored, and the Dockerfile notes that it resolves
dependencies from `pyproject.toml` during build. Python dependencies use broad
minimum ranges. Two builds from the same commit can therefore install different
dependency graphs. Dashboard and OpenCode plugin npm lockfiles are present;
Python should receive the same reproducibility.

Commit `uv.lock`, build and test with `uv sync --frozen`, and fail CI when the
lock is stale. Use `npm ci` for both TypeScript packages. Pin or digest base
images for release builds and record Python/Node/runtime versions in the build
manifest.

Python `>=3.13,<3.14` is also a narrow support promise. Either intentionally
support only 3.13 and state that clearly, or test a wider matrix before
advertising it. Do not broaden metadata without CI coverage.

### F-24 — CI does not run the repository's full hermetic verification surface

**Priority:** P1 development safety

GitHub CI runs Python format/lint, non-integration pytest, and CLI help. It does
not run BasedPyright, dashboard tests/build, OpenCode plugin tests/type-check,
wheel install/package smoke, OpenAPI/schema drift, Docker build, or browser
tests. `scripts/verify.sh` covers more locally, but even it does not make every
TypeScript test a required CI gate.

Use three tiers:

1. **Required hermetic PR gate:** frozen dependency sync, Python lint/type/unit
   tests, dashboard tests/type/build, plugin tests/type/build, schema/client
   drift, wheel build/install, packaged dashboard fetch, and basic CLI/API
   smoke.
2. **Scheduled integration gate:** Docker remote, restart/resume, backup/restore,
   browser SSE, and fake connector failure matrix.
3. **Environment-owned live gate:** AMD models and credentialed read/write
   connectors, producing scorecards and drill receipts rather than blocking
   every PR.

Keep live secrets and expensive models out of forked PR execution.

### F-25 — Documentation currently describes multiple eras at once

**Priority:** P2 operator and contributor clarity

The README and architecture overview still describe the earlier small workflow
set and LangGraph skeleton. The post-MVP tracker marks RF/PM5 pending in its
phase overview while detailed rows mark all work complete. It also marks the
local-model proof complete while the local-gateway document says hardware
cutover remains an operator action.

After the code fixes above:

- write one current architecture overview from host request to executor,
  persistence, and delivery;
- generate workflow/capability/skill/connector catalogs from registries;
- distinguish `implemented`, `verified hermetically`, and
  `operationally proven` status;
- archive completed implementation trackers under `docs/archive/plans/`;
- mark the LangGraph ADR/design as superseded rather than silently deleting
  historical reasoning;
- keep operator runbooks separate from design handovers; and
- add an explicit support matrix for local, remote, dashboard, connectors, and
  model routes.

### F-26 — Open-source and release governance are not ready

**Priority:** P2 before public release

The repository does not yet have a visible license, contribution guide,
security disclosure policy, changelog/release policy, or documented protocol
compatibility promise. Before open sourcing or distributing binaries:

- choose the license and any commercial/open-core boundary;
- add `SECURITY.md`, `CONTRIBUTING.md`, code of conduct if desired, and a
  release/changelog process;
- define semantic versions for Python package, host protocol, workflow packs,
  artifact schemas, skills, and plugin compatibility;
- add dependency update automation, secret scanning, dependency/license audit,
  and SBOM/provenance generation; and
- document supported operating systems, Python/Node versions, local model
  runtimes, and private-network deployment assumptions.

Do this after safety closure, so public documentation does not freeze flawed
approval or handoff contracts.

---

## 11. Obsolete and removable surfaces

Deletion should follow usage evidence and a bounded compatibility window. The
following list is intentionally conservative.

| Surface | Recommendation | Confidence and rationale |
| --- | --- | --- |
| LangGraph `orchestration/graph.py`, `state.py`, `--graph-demo`, LangGraph/checkpoint dependencies | Remove now; mark the original ADR superseded | **High.** The durable coordinator is authoritative. Search found only the legacy demo/test path using LangGraph. Removing it also removes `langgraph`, `langgraph-checkpoint-sqlite`, and likely `aiosqlite` if no remaining import exists. |
| `/api/v1/events/ws` | Remove now | **High.** It is unauthenticated and no production client uses it; SSE is the supported cursor protocol. |
| Generic successful task stub | Remove immediately | **High.** It violates fail-closed execution and hides missing product behavior. |
| `config/workflows.yaml` / `WorkflowsConfig` | Remove or generate from registry | **High that current content is dead; medium on migration shape.** Confirm no external deployment scripts edit it before removal. |
| `model_profile_set` and `project_profile` | Remove in host protocol v2 or replace with real `routing_policy_id` | **High.** One is explicitly ignored; neither should imply unenforced policy. |
| `requested_artifacts` | Remove after the documented one-release alias period | **High.** Typed `artifact_overrides` is the maintained surface. |
| Global artifact metadata keyed only by hash | Migrate readers to `ArtifactInstance`; then reduce/remove ambiguous metadata | **Medium.** Run ownership already lives in instances, while global logical name/path/creator can describe only the first identical hash. Preserve historical reads during migration. |
| Per-run JSONL event stream as a protocol fallback | Retain only as diagnostic export or remove after compatibility audit | **Medium-high.** SQLite events are declared authoritative; the mirror generates different event IDs and HostService synthesizes sequence numbers when falling back. It should not act like an equivalent state stream. |
| Legacy `evaluation_runs` dual write and raw `eval`/`bench lessons` aliases | Migrate old reports, then remove | **Medium-high.** The evaluation score/bench tables are the maintained model; dual writes complicate migrations. |
| In-process staging deployment adapter | Keep, but rename as simulator/test fixture | **High.** It provides valuable hermetic state-machine tests but is not a real deployment integration. |
| Workflow aliases `code_change` and `architecture` | Time-bound deprecation, then remove from protocol v2+ | **Medium.** They aid existing clients today; the registry already has canonical names. Telemetry should establish remaining usage. |
| MCP adapter | Keep | **High.** It is the main host-neutral extension path and supports CLIs beyond OpenCode. |
| OpenCode plugin | Keep | **High.** It realizes the primary user experience without replacing the host. |
| Bundled dashboard | Keep | **High.** It supplies useful monitor-only observability with a small operational footprint. |
| Optional OpenTelemetry bridge | Keep optional; verify ownership | **Medium-high.** It complements durable local projections and need not be on the critical path. |

Before deleting a compatibility surface, emit deprecation telemetry for one
release where practical, document the last supported version, and add a
migration note. Security-sensitive unused surfaces such as the WebSocket route
do not need a long deprecation period unless a real consumer is identified.

---

## 12. Optimization priorities

Optimization should follow correctness and instrumentation.

### 12.1 Optimize now because the problem is concrete

1. **Repository inventory:** replace repeated full-tree `rglob` scans with the
   safe cached inventory described in F-04. This improves both security and
   latency.
2. **Prompt/context reuse:** cache immutable repository summary, stack profile,
   and selected manifest metadata by base revision plus policy digest. Keep
   task-specific excerpts separate.
3. **Atomic artifacts:** avoid duplicate/corrupt writes and repeated hashing of
   unverified partial blobs.
4. **Database transitions:** group related run/task/event/budget updates in
   transactions to reduce inconsistent states and commit overhead.
5. **Client invalidation:** measure dashboard query volume under long runs;
   narrow invalidation further only if projections become a bottleneck.

### 12.2 Instrument before optimizing

Add histograms/counters for:

- plan compile time;
- repository inventory and context selection time;
- prompt assembly size/time and cache hit rate;
- model queue, provider, and tool-loop time;
- tool and connector latency/result bytes;
- validation and patch/worktree time;
- SQLite write lock/transaction duration;
- event-to-SSE and event-to-dashboard delay;
- worker queue depth, concurrency, and lease recovery; and
- local route saturation/fallback.

Set an initial regression budget from observed real runs, not arbitrary
microbenchmarks. A useful first target is to keep non-model orchestration
overhead below 10% of end-to-end latency for typical tasks while maintaining
the existing under-one-second selected-run event objective.

### 12.3 Do not optimize yet

- replacing SQLite;
- distributing the scheduler across machines;
- speculative prompt compression that removes evidence;
- more parallel agents without local-runtime saturation data;
- automatic model selection learned from a small synthetic corpus;
- a virtualized dashboard event store before browser profiling; or
- a general plugin marketplace.

---

## 13. Ordered implementation program

The packages below are the recommended follow-up sequence. SD0 is a mandatory
safety gate. SD1 is the product-truth gate. Later packages can overlap only
where they do not alter the same contracts.

### SD0 — Trust-boundary closure

**Goal:** make cross-run evidence, approvals, repository context, and remote
streams authoritative before any external-write use.

#### Tasks

1. Implement `ResolvedHandoff` and durable handoff resolution.
2. Implement authoritative approval records and deployment verification.
3. Disable deployment connector activation until the new approval verifier is
   configured.
4. Remove the unauthenticated WebSocket route and documentation references.
5. Implement `SafeRepositoryInventory` and route implicit context/stack reads
   through it.
6. Add adversarial security tests and an explicit threat-model update.

#### Exit criteria

- forged handoffs and approvals fail before spend/effect;
- every deployment effect has a durable operator decision and action
  fingerprint;
- every stream is authenticated consistently in remote mode;
- symlink and prohibited-path content cannot enter prompts; and
- the complete hermetic suite plus new security tests passes.

### SD1 — Executor truth and PM5 operational completion

**Goal:** ensure every advertised capability performs its declared work.

#### Tasks

1. Add the executor registry and capability descriptors.
2. Implement `ModelDraftExecutor` task adapters for release, operations,
   security, documentation, test design, and independent review.
3. Implement `ValidationExecutor` for test execution.
4. Preserve dedicated repository, research, interface, deployment, and
   composition executors.
5. Remove the stub fallback and wrong default agent profile.
6. Add fake-live invocation assertions for all packs.
7. Change roadmap/docs status from “operationally complete” until each live
   proof is recorded.

#### Exit criteria

- every capability is covered by a registered executor and output parser;
- unavailable mandatory evidence produces `blocked`/`unsupported`, never
  success;
- release and operations packs consume real connector receipts in fake-live
  tests; and
- PM5 outputs can be traced to task work rather than request-only composition.

### SD2 — Kernel decomposition and policy consolidation

**Goal:** reduce the cost and regression risk of adding or modifying behavior.

#### Tasks

1. Extract task executors, composition, validation/repair, scheduling/lineage,
   approval, and finalization in the order in F-08.
2. Make pack execution policy the single source for workflow behavior.
3. Remove workflow-name and capability conditional allowlists as their
   registered equivalents land.
4. Replace `ComposeContext` callback bags with typed services/data.
5. Make pack extension pass through the public host API.
6. Split the monolithic default-plan and validator modules by owned family;
   keep registries explicit and static.

#### Exit criteria

- coordinator is a lifecycle facade, not a capability implementation file;
- no new pack requires changes to coordinator/scheduler/API type unions;
- architecture tests prohibit reintroduction of workflow branches; and
- characterization tests demonstrate unchanged existing behavior.

### SD3 — Persistence, shutdown, backup, and retention

**Goal:** make unattended remote operation recoverable and bounded.

#### Tasks

1. Add versioned SQLite migrations and aggregate repositories.
2. Define concurrency/connection and transaction policy.
3. Implement graceful worker drain and restart tests.
4. Make artifact writes atomic and verifiable.
5. Strengthen backup manifests and automated restore drills.
6. Implement dry-run retention/GC, pinning, disk thresholds, and maintenance
   audit.
7. Remove or demote JSONL/evaluation legacy state after migration.

#### Exit criteria

- service shutdown during each major phase recovers exactly once;
- schema upgrades and one supported rollback/restore path are tested;
- corrupt or partial blobs are detected;
- a backup restores a consistent set of runs/artifacts/events; and
- a long-lived data root has an operator-tested capacity policy.

### SD4 — Protocol and client consolidation

**Goal:** keep OpenCode, other CLIs, dashboard, and local commands aligned with
one application protocol.

#### Tasks

1. Route all run mutations through the host application service.
2. Define host protocol v2 and remove/replace ignored request fields.
3. Generate or validate TypeScript DTOs from canonical schemas.
4. Split the OpenCode client by transport, lifecycle, and delivery concerns.
5. Bound JSON inputs and remove client-selectable debug execution modes.
6. Resolve and test the dashboard's local/remote authentication boundary.

#### Exit criteria

- local, remote, MCP, and OpenCode submissions have golden parity;
- API drift fails CI before merge;
- no public parameter is silently ignored; and
- one protocol compatibility document defines deprecation and versioning.

### SD5 — Reproducible builds and layered CI

**Goal:** make a commit produce the same verified product for contributors and
operators.

#### Tasks

1. Commit and freeze Python and npm dependency locks.
2. Expand required CI to Python types, both TypeScript packages, wheel/package
   smoke, schema drift, and packaged dashboard.
3. Add Playwright outcome tests.
4. Add scheduled Docker restart, restore, and fake-connector integration.
5. Resolve deprecation warnings under the locked dependency set.
6. Add release artifacts, hashes, SBOM, and provenance.

#### Exit criteria

- CI uses frozen installs and detects lock drift;
- an installed wheel plus packaged plugin passes smoke tests;
- the dashboard acceptance journey runs in a browser; and
- scheduled operational drills publish durable results.

### SD6 — Real-world evaluation and local-model promotion

**Goal:** convert the product hypothesis into ongoing evidence.

#### Tasks

1. Connect the AMD OpenAI-compatible runtime and record admission snapshots.
2. Build a small versioned corpus from real discovery, plan, change, quality,
   release, and operations tasks.
3. Run local-only, hybrid, cloud, baseline, and skill-ablation arms.
4. Record human correction effort and accept/reject decisions.
5. Promote model/skill routes only through regression gates.
6. Implement one external benchmark adapter and isolated harness subset.

#### Exit criteria

- at least one real scorecard exists per production-used capability family;
- local-first value is demonstrated or routing policy is adjusted honestly;
- fallback thresholds reflect observed failure modes and budget; and
- skill/model changes cannot become defaults without a corpus-linked decision.

### SD7 — Simplification, documentation, and release governance

**Goal:** leave a small, accurate, supportable product surface.

#### Tasks

1. Remove the high-confidence obsolete paths in Section 11.
2. Audit medium-confidence compatibility paths with telemetry and owners.
3. Rewrite the current architecture/README and generate catalogs.
4. Archive completed implementation plans and reconcile status labels.
5. Add license, security, contribution, compatibility, and release policies.
6. Publish the operational support matrix and known limitations.

#### Exit criteria

- no dependency exists solely for an unused legacy demo;
- every public command/config field has a maintained use case;
- current docs describe one architecture and one status vocabulary; and
- a new contributor can build, test, extend one pack, and understand the trust
  boundary without reading historical handovers first.

### SD8 — Measured performance tuning

**Goal:** improve throughput and latency without weakening evidence or safety.

#### Tasks

1. Establish p50/p95 baselines on small, medium, and monorepo-sized fixtures.
2. Ship safe repository inventory/context caching.
3. Optimize transaction and projection hot paths from profiles.
4. Tune agent concurrency to AMD memory/throughput measurements.
5. Tune context/model selection only when outcome metrics remain non-regressed.

#### Exit criteria

- before/after measurements are attached to every optimization;
- correctness, policy, and quality gates remain green; and
- operator-visible latency/cost improvements are material, not merely local
  microbenchmarks.

---

## 14. Test coverage additions by risk

| Risk | Missing or insufficient coverage | Required suite |
| --- | --- | --- |
| Cross-run artifact integrity | Existing tests validate only reference shape/compatibility | Contract + security tests against real producer artifact instances and states |
| Deployment approval | Tests accept arbitrary caller-created approval IDs | Security state-machine tests with durable approval lookup and replay/mismatch cases |
| Context path confinement | No adversarial implicit-context symlink suite | Unit/security tests sharing the broker path policy |
| Every capability executes | Graph tests can pass through request composition and stubs | Table-driven executor tests and fake-live model/tool invocation assertions |
| Worker shutdown | Scanner stop is covered more than active-worker drain | FastAPI lifespan + restart tests at blocked execution phases |
| Dashboard outcomes | Four frontend unit tests, no browser tests | Playwright against an installed real FastAPI app |
| Protocol parity | Python/TypeScript DTOs are hand-maintained | OpenAPI/schema snapshot plus cross-language golden fixtures |
| Packaging | CI does not install and serve the built wheel | Isolated wheel install, `/dashboard/`, `/api/v1/health`, plugin build smoke |
| Retention and backup | Backup unit coverage without long-lived pruning lifecycle | Property/integration tests for reachability, dry run, interrupted prune, restore |
| Local model usefulness | Protocol/admission probe only | Real workflow corpus with local/hybrid/cloud arms and human review |
| Connector failure modes | Mostly deterministic happy/deny fixtures | Timeout, truncation, stale data, partial receipt, injection, restart, reconciliation matrix |
| Schema migrations | Ad hoc column additions | Upgrade fixtures from every supported released schema version |

Use mutation or property testing selectively for path confinement, approval
bindings, handoff resolution, artifact reachability, and schema migration. Do
not pursue a blanket coverage percentage; require coverage of authority and
state transitions.

---

## 15. Definition of sustainable readiness

The post-PM5 sustainability program is complete when all of the following are
true:

### Safety and truth

- [ ] Cross-run handoffs are resolved from durable producer artifact instances.
- [ ] Deployment effects require a durable operator approval independent of
      request data.
- [ ] Every remote read/write/stream route crosses the intended authentication
      boundary.
- [ ] Implicit repository context cannot bypass broker path policy.
- [ ] No registered capability can succeed through a placeholder.

### Architecture

- [ ] Executor mode, agent profile, model route, tools, validators, and output
      parser come from one registered capability/pack policy chain.
- [ ] `RunCoordinator` no longer contains workflow implementations.
- [ ] A fixture pack works end to end through host/v1 without editing shared
      lifecycle dispatch.
- [ ] State authorities and compatibility projections are explicitly separated.

### Operations

- [ ] Active workers drain or recover safely across service shutdown.
- [ ] SQLite migrations, backups, restores, artifact integrity, and retention
      are operator-tested.
- [ ] Disk growth and local-model saturation have observable limits.
- [ ] Required builds use frozen dependency graphs.

### Product evidence

- [ ] Real AMD/local workflow scorecards exist for production-used capability
      families.
- [ ] Skill promotion compares a no-skill baseline and records human rework.
- [ ] At least one external benchmark subset runs through its prescribed
      sandbox without widening authority.
- [ ] PM5 status distinguishes simulator/mock verification from real connector
      proof.

### Maintainability

- [ ] High-confidence obsolete surfaces are removed.
- [ ] Public fields and configuration are enforced, generated, or deleted—none
      are silently ignored.
- [ ] Python, dashboard, plugin, package, browser, and schema gates run in CI.
- [ ] Current documentation and release governance are sufficient without
      reconstructing the project from historical plans.

---

## 16. Recommended first implementation slice

The first slice should be deliberately narrow and security-led:

1. add failing tests that submit a fictitious approved handoff and a fictitious
   deployment approval;
2. implement durable handoff resolution and approval lookup until those tests
   pass;
3. remove the WebSocket route;
4. add symlink-escape tests and the minimal safe repository inventory needed to
   pass them;
5. keep `staging_deploy` disabled; and
6. run the complete hermetic Python, dashboard, plugin, and package suites.

The second slice is the executor registry plus removal of successful stubs.
Only after those two slices should the project resume workflow, connector, or
model-portfolio expansion.

This sequence produces more value than another feature pack: it turns the
existing broad architecture into a trustworthy platform on which future
workflow and local-model work can be evaluated rather than merely declared.
