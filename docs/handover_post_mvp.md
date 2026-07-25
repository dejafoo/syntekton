# Product Factory — Post-MVP Handover and Long-Term Direction

**Status:** Planning handover for post-MVP work  
**Audience:** Humans and AI agents planning or implementing subsequent phases  
**Scope:** Product direction, implementation boundaries, phased delivery, and evaluation strategy

---

## 1. Purpose and product boundary

Product Factory is a **local-first orchestration kernel and evidence-driven
execution service** for software work. It takes a bounded request, creates a
typed execution plan, delegates only permitted work to model-backed workers,
validates the resulting artifacts, and records enough evidence for a human to
understand and control the outcome.

The project is **not** intended to become a replacement for OpenCode, Claude
Code, Codex CLI, an IDE, or a generic chat interface. Existing developer CLIs
and IDEs should remain the primary interactive surface wherever feasible.
Product Factory should be usable from them in one or both of these ways:

1. A user invokes `product-factory` directly for a durable, inspectable run.
2. An existing CLI invokes Product Factory as a local service or controlled
   tool, receives run IDs/events/artifacts, and remains responsible for its own
   interaction model.

This boundary avoids rebuilding mature terminal editing, session, and IDE
experience while concentrating this project on what it uniquely adds:

- typed planning and constrained delegation;
- local/cloud model routing under explicit budgets;
- isolated execution, validation, repair, and approval;
- durable observability and artifacts;
- reproducible evidence for improving workflows, skills, prompts, and model
  choices.

### 1.1 North-star user journey

A solo operator is working in an existing coding CLI or IDE. They request work
such as research, planning, implementation, test design, refactoring, or a
deployment preparation. The host UI delegates a bounded subproblem to Product
Factory. Product Factory returns a run ID immediately, exposes the plan and
live state, and later returns a reviewed artifact, patch, report, or an
approval request. The operator can inspect the evidence, request a revision,
or apply the result through a controlled path.

The system must make it clear which model, tool, permissions, validation, and
cost were used. It must never make a cloud escalation, destructive operation,
or production deployment look like an invisible implementation detail.

### 1.2 Product principles

These principles remain normative for all post-MVP work.

1. **Existing CLI first.** Integrate with existing developer interfaces before
   considering a bespoke interactive terminal UI.
2. **Typed workflows, not role-play.** Workflows may create typed tasks within
   registered capabilities and schemas; they may not create arbitrary agents,
   code, permission classes, or tools.
3. **Deterministic authority.** Models propose plans, tool calls, code, and
   findings. Deterministic code enforces authorization, budgets, isolation,
   validation, and approval.
4. **Local-first, cloud-explicit.** Prefer a suitable local model. Escalate to
   cloud models only through policy with visible reason, budget impact, and
   provenance.
5. **Evidence before consensus.** Test results, validator output, artifact
   hashes, source locations, and command results are evidence; agreement among
   agents is not.
6. **Human control at external boundaries.** Merges, deployments, credential
   use, external writes, and cost escalation require an appropriate policy and
   often explicit approval.
7. **Measure every material change.** A new workflow, skill, prompt, model,
   routing rule, or tool policy is a hypothesis and needs a reproducible
   evaluation before becoming the default.

---

## 2. Starting point: what the MVP is and is not

The MVP provides a useful execution-control base:

- bounded `code_change` and `architecture` workflows;
- typed tasks, deterministic plan compilation, capability grants, Git
  worktrees, patches, validators, repair, and manual approval;
- an OpenRouter adapter and deterministic mock gateway;
- a local SQLite/artifact/event store with REST, SSE, and WebSocket read APIs;
- a benchmark harness with deterministic checks, LLM judging, multi-seed
  reports, ablations, and human-gated lesson promotion.

The MVP does **not** yet provide a broad agent platform. In particular:

- most normal coding runs are a single tool-using implementation worker plus
  deterministic composition, not a parallel team of specialists;
- workflow behavior is mostly implemented in `RunCoordinator`, not registered
  as reusable workflow packs;
- no real OpenAI-compatible local inference adapter, cloud fallback router, or
  hardware evaluation exists;
- no MCP, web search, browser, source-control service, issue tracker, or
  deployment connector exists;
- the observability backend exists, but there is no dashboard client;
- runtime resume and true concurrent worker execution are not yet delivered;
- process-level sandboxing is absent: registered commands run repository code
  in a worktree but with local process authority.

Post-MVP work must close these gaps deliberately. It must not hide them behind
new labels for capabilities or additional prompt text.

> **Update:** the "runtime resume", "true concurrent worker execution", and
> "process-level sandboxing is absent" gaps above are the ones Phase 1 (§4)
> closes; workflow packs are partially addressed (`repository_change` is
> migrated, `architecture` is not yet). See
> [`docs/next-work-packages-phase1.md`](next-work-packages-phase1.md) for
> current status. The remaining bullets (local inference adapter, MCP/web/
> connectors, dashboard client) are still open and scoped to later phases.

---

## 3. Target architecture

```text
Existing CLI / IDE / CI
        │ local command, local HTTP, or structured subprocess protocol
        ▼
Product Factory Run Service
        ├── session + run lifecycle + approvals
        ├── workflow-pack registry
        ├── scheduler + hybrid model router
        ├── tool / connector broker
        ├── isolated execution + validation + repair
        └── artifact, event, cost, and evaluation stores
                 │                         │
                 ▼                         ▼
          Local dashboard             Benchmark / lesson loop
```

### 3.1 Integration contract with existing CLIs

The first integration must be small, local, and vendor-neutral. Provide both a
stable machine-readable CLI mode and a local API; do not require a specific
host CLI plugin in order to operate.

Minimum integration operations:

- submit a request with repository, workflow, policy, and budget;
- return `run_id`, initial plan summary, and subscription URL/command;
- retrieve run/task state, artifacts, validation results, costs, and approval
  status;
- stream events by cursor;
- approve, reject, cancel, or request a bounded revision;
- export a patch or an immutable evidence bundle.

The host CLI owns its chat/session transcript and user experience. Product
Factory receives a minimal explicit request and must not silently ingest the
whole transcript. The host may pass curated context as named artifacts with
provenance.

### 3.2 Workflow packs

Replace growth through coordinator conditionals with versioned workflow packs.
Each pack declares:

- workflow ID, semantic version, supported input and output schemas;
- allowed task capabilities and task templates;
- context-selection policy and required skills;
- allowed connector/tool classes and required approval boundaries;
- deterministic validators and repair policy;
- default model-routing policy and escalation rules;
- benchmark suites and acceptance criteria.

A workflow pack may call narrowly registered deterministic handlers. It must
not load arbitrary Python supplied by a planner or an external tool. Existing
`code_change` and `architecture` should be migrated into the pack interface
before large numbers of new workflows are added.

Initial post-MVP packs, in priority order:

1. `repository_change` — bounded implementation, test, refactor, and repair.
2. `repository_investigation` — codebase research, diagnosis, architecture
   reading, and evidence report; no writes by default.
3. `technical_plan` — requirements, architecture decision, backlog, risks,
   acceptance criteria, and implementation handoff.
4. `quality_gate` — test design, test execution, patch review, security review,
   and release-readiness evidence.
5. `delivery_preparation` — CI configuration, deployment manifests, release
   plan, and rollback plan. This pack produces artifacts only initially.

Actual deployment belongs in a later, separately approved connector policy; it
is not a default extension of a coding workflow.

### 3.3 Skills and capabilities

Capabilities remain a small, stable authorization taxonomy. A skill is
versioned procedural/context knowledge selected for a task; it is not an
executable plugin and must not grant permissions by itself.

Add a deterministic skill-policy check that verifies:

- a selected skill is compatible with the task capability and project
  language/framework;
- its required/prohibited tool declarations are consistent with the task grant;
- its version and content hash are captured in the prompt manifest;
- the run records the reason the skill was selected or omitted.

Do not create a capability for every job title. Prefer a small set of reusable
capabilities, such as investigation, planning, implementation, test execution,
review, repair, composition, and delivery preparation.

### 3.4 Tools, connectors, and MCP

The existing `ToolBroker` remains the sole tool execution path. Expand it into
a connector layer, but preserve the same authorization model.

Every connector/tool must define:

- stable ID, version, provider, input/output schema, and risk class;
- local or remote execution mode;
- read/write/destructive permissions and resource scopes;
- authentication source and secret-redaction rules;
- egress/network policy and timeout/concurrency limits;
- audit event shape and result-retention policy;
- approval requirement and deterministic validation where applicable.

MCP support, if added, is an adapter behind this contract. An MCP server is not
trusted merely because it is configured. Product Factory must enumerate and
allowlist each exposed MCP tool, normalize schemas/results, enforce grants
itself, and record invocation provenance. Start with read-only MCP tools and
one locally hosted server. Add remote/write-capable MCP tools only after
credential, egress, and approval controls exist.

Web search should likewise be a policy-controlled read-only connector with
source URLs, retrieval time, excerpts/content hashes, domain restrictions, and
clear distinction between retrieved facts and model inferences.

### 3.5 Hybrid model routing

Model profiles must become configuration, not hard-coded scheduler choices.
Each profile needs provider adapter, endpoint, model ID, supported features,
context limits, tool/structured-output support, pricing, availability/health,
and data-handling policy.

Routing is policy-based, not an opaque model vote. A route decision records:

- selected model and reason;
- local eligibility and local health/capacity;
- required task capabilities and context size;
- estimated cost, latency, and remaining run budget;
- fallback chain and escalation reason;
- final resolved provider/model and actual usage.

Initial policy:

1. Attempt an eligible local profile for low/medium-risk work.
2. Retry only classified transient failures within bounded limits.
3. Escalate to a configured cloud worker only for a typed reason: unsupported
   capability, context too large, local unavailable, repeated no-progress, or
   explicit user policy.
4. Require approval before a frontier escalation above a configured cost or
   data-sensitivity threshold.

The first required local adapters are one OpenAI-compatible HTTP endpoint and
one local process/runtime adapter only if needed. Support for llama.cpp, vLLM,
and SGLang must be driven by tested compatibility rather than separate graph
logic.

### 3.6 Observability and dashboard

The current event store/API is the authoritative source for a dashboard. Do not
build a second state machine in a frontend.

The dashboard should provide:

- run list with status, budget/cost, liveness, and selected models;
- a task-DAG and kanban projection: `planned → ready → running → validating →
  repairing → awaiting approval → terminal`;
- repair lineage and superseded candidate patches;
- per-task model, tool, validation, latency, token, and cost details;
- prompt manifest and, only when capture policy permits, redacted request/tool
  result content;
- artifact diff, evidence, reviewer findings, approvals, and typed failures;
- filters by workflow, repository, model, connector, outcome, and benchmark
  version.

The initial dashboard is monitor-only. Control actions (cancel, retry,
revision, approval, cloud escalation) are added only after their server-side
authorization and audit semantics exist.

### 3.7 Security and execution isolation

Path-scoped Git worktrees are necessary but not sufficient. Before connecting
untrusted repositories, public MCP servers, or deployment tools, introduce a
process sandbox for command execution with a minimal environment, explicit
network policy, controlled mounts, resource limits, and per-command logs.

Separate trust levels:

- **trusted local project:** restricted tools may run after the operator opts
  in;
- **untrusted repository/content:** no host-secret access, no unrestricted
  network, and no host-process authority;
- **external write/deployment:** explicit connector grant plus approval;
- **cloud model invocation:** data classification and provider policy check.

---

## 4. Phased delivery plan

Each phase must leave a usable, testable product increment. Do not start a
later phase solely because its design is attractive; complete the preceding
exit criteria first.

### Phase 1 — Make the execution kernel operationally truthful

> **Status: exit criteria met (mocked regression) + one live smoke run.** See
> [`docs/next-work-packages-phase1.md`](next-work-packages-phase1.md) for the
> workstream-by-workstream checklist, test evidence, and the live-smoke run
> (`OPENROUTER_API_KEY` was available: `product-factory run` end-to-end on
> `tests/fixtures/sample_api`, real OpenRouter spend $0.035, terminated
> `awaiting_approval` with a `repository_change` v1.0.0 workflow-pack hash on
> the manifest). Design note for sandbox + resume:
> [`docs/architecture/sandbox-and-resume.md`](architecture/sandbox-and-resume.md).

**Goal:** turn the MVP’s safety and durability claims into runtime guarantees.

Deliverables:

- durable coordinator checkpoints and real resume from persisted task state,
  worktree lineage, usage, and pending approval;
- global enforcement of run cost, token, tool-call, command, and wall-clock
  budgets before each action;
- normal CLI/API support for registered validation commands and policy
  selection;
- effective model-profile selection (remove unused profile-set fields);
- real concurrent execution of independent read-only or disjoint-write tasks,
  with deterministic conflict handling;
- a process-sandbox design and one implementation for validation commands;
- a migrated `repository_change` workflow pack.

Value: safe, resumable bounded code-change jobs suitable for daily use in a
trusted repository.

Exit criteria:

- interrupt/restart tests resume a run without repeating completed billable
  calls or losing repair lineage;
- a run cannot exceed any configured global limit in fault-injection tests;
- two independent tasks demonstrably overlap in wall-clock execution;
- configured validation commands run in the normal CLI path;
- sandbox tests prove no access to an injected host secret or denied network
  destination;
- current code-change regression corpus remains at or above its established
  usable-rate and safety thresholds.

### Phase 2 — Real local-first model execution and explicit escalation

**Goal:** prove the central local-model proposition on actual hardware.

Deliverables:

- OpenAI-compatible local gateway adapter and profile configuration;
- local endpoint health/capability probe;
- policy-based model router with explicit cloud fallback and approval threshold;
- recorded actual/estimated cloud cost and local latency/throughput;
- a hardware test matrix for the intended nodes and model quantizations.

Value: an operator can choose a local-first code-change/investigation run and
know exactly when and why cloud inference was used.

Exit criteria:

- the same workflow runs unchanged against a real local endpoint and OpenRouter;
- local tool calling and structured-output failures are typed and recoverable;
- fallback behavior is covered by deterministic integration tests;
- a multi-seed benchmark compares local-only, hybrid, and cloud-only routing
  under identical task, tool, and validation budgets;
- the selected route improves a predeclared objective such as usable artifacts
  per dollar, latency, or cloud-spend reduction without unacceptable quality
  loss.

### Phase 3 — Existing-CLI integration and investigation/planning workflows

> **Status: exit criteria met (mocked regression + mock host smoke).** See
> [`docs/next-work-packages-phase3.md`](next-work-packages-phase3.md) for the
> workstream checklist and test evidence (focused **49 passed**, broader
> unit/contract/graph **203 passed**; mock CLI loop `run-600efcba7fa7`). Protocol
> and OpenCode/MCP packaging: [`docs/host-integration.md`](host-integration.md),
> [`examples/opencode/`](../examples/opencode/). Live OpenRouter Stage B smoke
> skipped (mock gate sufficient).
>
> **Follow-on (Phase 3.G, exit criteria met):** vendor-neutral `materialize`
> host action (CLI + MCP + control API) plus optional OpenCode plugin packaging
> so the happy path needs no slash commands — tracker
> [`docs/next-work-packages-phase3g.md`](next-work-packages-phase3g.md), plugin
> at [`integrations/opencode-plugin/`](../integrations/opencode-plugin/). MCP
> and `product-factory.host/v1` remain the source of truth; the plugin is thin
> packaging over the host CLI.

**Goal:** make Product Factory useful from an existing development CLI without
becoming a competing CLI product.

Deliverables:

- stable JSON CLI protocol and local API submission/attach contract;
- run subscription/tail and artifact export commands;
- plan-preview and approval/revision APIs;
- `repository_investigation` and `technical_plan` workflow packs;
- source-grounded evidence reports with cited repository locations and explicit
  assumptions.

Value: an existing CLI can offload a research or planning task, keep its own
interactive conversation, and show a durable plan/results to the user.

Exit criteria:

- a reference host integration (a thin script or plugin, not a replacement
  CLI) submits, streams, inspects, and approves a run end to end;
- investigation tasks produce evidence-backed reports without write grants;
- planning outputs map requirements to acceptance criteria, task owners, and
  validation methods;
- user acceptance tests show a developer can understand status and retrieve
  results without reading SQLite or internal run directories.

### Phase 4 — Controlled connector expansion and quality workflows

> **Status: exit criteria met.** Tracker
> [`docs/next-work-packages-phase4.md`](next-work-packages-phase4.md). Connector
> framework behind `ToolBroker` with typed errors and per-call audit; read-only
> Tavily `web_search` and `@modelcontextprotocol/server-filesystem` connectors,
> both disabled until an operator enables them in
> [`config/connectors.yaml`](../config/connectors.yaml); `quality_gate` pack
> emitting three documents. Also closed the deliverable-naming gap: an
> artifact land map splits stable role keys from land filenames, so a host can
> request `docs/integration_testing_architecture.md` and land it via
> `materialize-all`. Connector suites (**133 passed, 2 skipped**) and the
> `quality_gate` graph (**7 passed**) are offline and always-on; live Tavily and
> MCP smokes are env-gated. OpenCode UAT passed on `opencode 1.18.4` for named
> and multi-document landing.

**Goal:** add useful external information and quality work without weakening
the authority model.

Deliverables:

- connector manifest/policy framework and connector test harness;
- read-only web search connector;
- one read-only local MCP integration;
- `quality_gate` workflow pack for test design/execution, patch review, and
  security evidence;
- explicit connector approvals, redaction, source provenance, and egress
  controls.

Value: a run can investigate external documentation or use approved local
tools, then produce a traceable quality/review artifact.

Exit criteria:

- every external call has an audit event, policy decision, bounded result, and
  provenance reference;
- prompt-injection and malicious-tool-result tests cannot widen grants or
  trigger unapproved writes;
- connector outages fail as typed errors and do not cause silent cloud/model
  fallback;
- quality findings have evidence and seeded correctness defects are detected at
  a predeclared rate with bounded false blocking.

### Phase 5 — Local observability dashboard

**Goal:** make orchestration state understandable at a glance.

Deliverables:

- a local dashboard consuming the existing REST/SSE/WebSocket APIs;
- run list, kanban/task-DAG, timeline, repair lineage, budget/cost, and
  validation/artifact views;
- redaction-aware prompt/model/tool inspection;
- liveness/stuck presentation and links back to host-CLI commands.

Value: an operator can answer “what is executing, why, with which model/tools,
at what cost, and what needs my decision?” without inspecting logs.

Exit criteria:

- dashboard state agrees with database projections in API/GUI contract tests;
- a live run updates task and budget state within a defined local latency;
- capture policy prevents unauthorized prompt/content display;
- usability test participants can locate a blocked task, its evidence, and the
  required approval action.

### Phase 6 — Delivery preparation and controlled deployment integration

**Goal:** support the delivery lifecycle without unattended production changes.

Deliverables:

- `delivery_preparation` workflow pack for CI, release, deployment, rollback,
  and operational-readiness artifacts;
- optional source-control/CI connector with least-privilege scopes;
- deployment connector only for sandbox/staging targets initially;
- explicit change set, policy check, approval, and post-action verification.

Value: the system can prepare and, in approved non-production environments,
execute a release workflow with a complete audit trail.

Exit criteria:

- no deployment-capable connector can execute without an explicit approval
  record;
- staging deployment/rollback drills succeed in an isolated environment;
- secrets never appear in events, prompt captures, artifacts, or benchmark
  exports;
- failure leaves a typed state and rollback evidence rather than an ambiguous
  “completed” result.

### Phase 7 — Continuous optimization and optional fine-tuning

**Goal:** make workflow/model improvement routine, safe, and evidence-led.

Deliverables:

- experiment registry for workflow, skill, prompt, model-route, tool-policy,
  and benchmark versions;
- hold-out evaluation gates and rollbackable default selection;
- model capability/cost cards refreshed from measured runs;
- curated datasets for any fine-tuning, with license/provenance/data-sensitivity
  review;
- human-authored skill/prompt changes promoted only after held-out re-bench.

Value: the operator can improve capability and cost systematically rather than
depending on anecdotal successful runs.

Exit criteria:

- every promoted change names the hypothesis, evidence, hold-out result, and
  rollback target;
- no automatic lesson or judge output modifies a trusted skill/prompt/policy;
- default model routing is based on a published objective and confidence
  interval, not a single successful run;
- fine-tuning, if attempted, is compared against prompt/skill/routing baselines
  on a separate held-out set.

---

## 5. Evaluation and benchmark strategy

### 5.1 Evaluation hierarchy

Use three complementary layers. No single benchmark should decide product
direction.

1. **Fast local regression cases** — small deterministic cases for unit/graph
   regressions, grants, budgets, repair, and safety.
2. **Private product corpus** — sanitized, versioned tasks derived from the
   operator’s real repositories and workflows. This is the primary decision
   corpus because it measures the actual intended use.
3. **External suites** — periodically run public/research benchmarks to test
   generalization and compare model/agent configurations beyond the private
   corpus.

Every result must identify task version, environment image/commit, subject
configuration, workflow/skill/prompt versions, model resolution, seed, budget,
tool policy, validator version, and whether any cloud fallback occurred.

Primary metrics:

- usable artifact/task success rate;
- deterministic validation, behavioral validation, and security-policy pass
  rates;
- cloud cost per usable outcome and local latency/throughput;
- total wall-clock time and human-intervention rate;
- repair recovery, false-blocking, scope-violation, and infrastructure-failure
  rates;
- paired deltas and confidence intervals against fair controls.

The fair control for a multi-agent workflow is a single worker with the **same
model, tool access, context budget, validation environment, and cost ceiling**.
The experiment may then isolate planning, review, repair, parallelism, skill,
or routing effects. A one-shot patch-only baseline is retained as a product
comparison, but is not evidence for the value of multi-agent orchestration.

### 5.2 External benchmark option

External suites are optional adapters under `product_factory.evaluation.adapters`.
They must not change the runtime orchestration graph or become required for
ordinary development. Before adoption, verify license, task access, expected
agent interface, environment/sandbox requirements, evaluation cost, and
submission restrictions from the benchmark’s current official documentation.

Candidate suites:

| Suite | Best use in this project | Adoption notes |
| --- | --- | --- |
| [Terminal-Bench / Harbor](https://github.com/harbor-framework/terminal-bench) | Terminal tool use, multi-step execution, environment setup, and sandbox behavior | Use the official harness/sandbox. Begin with a small version-pinned smoke subset before a full run. Do not run benchmark tasks with host-process authority. |
| [SWE Atlas](https://github.com/scaleapi/SWE-Atlas) | Repository investigation/Q&A, test writing, and refactoring workflows | Particularly aligned with planned investigation and quality packs. Preserve its task-specific evaluation protocol rather than reducing every task to a patch-apply score. |
| [DeepSWE](https://deepswe.datacurve.ai/) | Long-horizon software engineering and frontier-agent comparison | Treat availability, access terms, and official harness compatibility as a feasibility gate. Use it only when the project can run an equivalent agent interface and preserve evaluation integrity. |
| SWE-bench family / other approved suites | Issue-resolution regression and broader comparison | Evaluate separately; do not mix score denominators or call different suites one composite “agent score.” |

Integration modes:

- **Case adapter:** map a suite’s task schema to `EvalCase` only when its
  required environment and grading semantics can be preserved.
- **Harness adapter:** run the suite’s official harness and convert its
  per-task result/artifact/usage records into Product Factory reports. Prefer
  this mode for Terminal-Bench and any suite with a prescribed sandbox.
- **Read-only import:** ingest published/previous result records for comparison
  dashboards without claiming they are equivalent to local runs.

External-suite adoption gate:

1. Implement a version-pinned adapter and a one-to-three-task smoke test.
2. Prove task isolation and no leakage of host credentials.
3. Compare Product Factory with a fair single-worker control.
4. Validate that the official scorer and Product Factory report agree on the
   terminal result for sampled tasks.
5. Record direct benchmark costs separately from Product Factory’s own judge
   costs; do not use an LLM judge to override an official deterministic score.
6. Run a wider, multi-seed slice only after the smoke test is stable.

External scores are generalization evidence, not a substitute for the private
product corpus. Do not tune prompts against public test tasks and then present
the resulting score as a general capability improvement.

### 5.3 Lessons, prompts, and fine-tuning

Maintain the current human-gated promotion principle:

```text
failure / cost outlier
  → evidence-backed lesson candidate
  → human diagnosis
  → explicit skill, prompt, validator, workflow, or router change
  → held-out re-benchmark
  → versioned promotion or rollback
```

Fine-tuning is a last-mile optimization, not the default remedy for an
orchestration failure. Investigate in this order:

1. faulty task contract, validator, or benchmark;
2. missing context, tool capability, workflow decomposition, or policy;
3. skill/prompt selection or model routing;
4. model quality/capacity;
5. fine-tuning on a licensed, sanitized, versioned dataset.

---

## 6. Planning rules for AI agents

When planning or implementing post-MVP work, AI agents must:

1. Name the phase and exit criterion the change supports.
2. Preserve the authority boundary: no model-originated permission or tool
   expansion.
3. Extend a registered workflow pack, provider adapter, connector, validator,
   or dashboard projection; do not add an unrelated parallel orchestrator.
4. Update domain contracts, persistence, observability, and tests together when
   a durable concept changes.
5. Add deterministic tests before live-provider tests; use small live smoke
   slices before expensive multi-seed experiments.
6. Keep test, benchmark, and host-user environments isolated. Never use a
   product repository or operator credentials as a generic benchmark sandbox.
7. Record model/tool/prompt/skill versions and evaluation evidence before
   changing defaults.
8. Do not implement unattended production deployment, unrestricted internet,
   arbitrary MCP execution, or automatic skill/policy promotion without a new
   explicit approval and security design.

---

## 7. Definition of post-MVP readiness

The project is ready to be described as a local-first orchestration platform
when all of the following are true:

- an existing CLI can submit, monitor, inspect, and approve a durable run;
- at least one real local inference endpoint and one explicit cloud fallback
  path have passed the same workflow contract;
- workflows are versioned packs with deterministic validation and evaluation
  coverage, not accumulated coordinator branches;
- external tools/MCP are brokered, scoped, audited, and tested under an
  isolation policy;
- dashboard projections explain plan, task/repair state, prompt provenance,
  validation, model/tool use, and cost;
- the private corpus plus at least one external suite demonstrate measured
  generalization against fair controls;
- any default workflow/model/skill decision is backed by multi-seed evidence
  and can be rolled back.

Until then, describe Product Factory precisely as an evolving orchestration MVP
and evaluation harness, not as an autonomous product factory or a replacement
for existing coding CLIs.
