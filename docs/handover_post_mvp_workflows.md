# Product Factory — Workflow Portfolio Handover

**Status:** planning handover for the post-MVP workflow portfolio  
**Audience:** humans and AI agents implementing, testing, and evaluating new workflow packs  
**Scope:** an evidence-driven agentic software-delivery lifecycle, from an ambiguous request through planning, change, verification, release, deployment, and operations.

**Companion documents:** [post-MVP direction](handover_post_mvp.md),
[host integration](host-integration.md), [remote orchestration](handover_remote_orchestration.md),
and [architecture](architecture.md).

---

## 1. Purpose and product boundary

Product Factory should help existing hosts—primarily OpenCode, but also other
CLIs, IDE integrations, scripts, and CI—run bounded and inspectable software
work. It is not a replacement for OpenCode or a conventional sequential SDLC
tool.

An agentic workflow is justified only when it has a distinct:

1. decision or outcome that is useful on its own;
2. typed input-evidence contract, rather than an unbounded chat transcript;
3. authority boundary (read-only, isolated repository write, local landing, or
   external write);
4. durable output contract that a human or later workflow can consume; and
5. validation and evaluation method that can show whether it improves results.

The model proposes plans, code, findings, and recommendations. Deterministic
Product Factory code remains responsible for grants, budgets, sandboxing,
source provenance, validation, lifecycle, approvals, and audit.

### 1.1 What is not a workflow

Avoid creating a pack for every classic SDLC noun. The following belong in a
different layer unless they acquire a distinct authority and output contract:

| Concern | Correct home |
| --- | --- |
| Web/repository/metrics query, CI command | Registered tool or connector plus a pack grant |
| Documentation | Artifact produced by a bounded workflow |
| Code review | Verification capability/mode |
| Git, issue tracker, PR provider | Host integration or connector |
| Prompt, skill, model, and routing choice | Pack policy plus evaluation configuration |
| Deployment command | Approval-gated external action within deployment execution |

This rejects shallow persona packs such as “architect agent” or “DevOps agent”
that merely attach different prompts to the same uncontrolled tool loop.

### 1.2 Existing-host first

OpenCode remains the primary interactive surface. Its plugin continues to use
`pf_run`, `pf_wait`, `pf_review`, `pf_merge`, and `pf_decline`; normal use must
not require a slash command per workflow. The plugin is a thin adapter over
`product-factory.host/v1`, not a second orchestration engine. Every pack must
also be usable by host CLI JSON, the control API, and compatible MCP/HTTP
clients.

---

## 2. Current workflow inventory

There are four canonical, versioned `WorkflowPack`s today:

| Canonical pack | Compatibility names | Outcome/artifacts | Authority and limits |
| --- | --- | --- | --- |
| `repository_change` | `code_change` | Proposed patch plus validation evidence | Writes only in isolated worktree; original repository apply needs approval; optional review/repair. |
| `repository_investigation` | — | Cited `EVIDENCE_REPORT.md` | Read-only repository investigation; no repository write grants. |
| `technical_plan` | `architecture` | `ARCHITECTURE.md` | Read-only requirements, architecture, acceptance, and handoff document. |
| `quality_gate` | — | `TEST_PLAN.md`, `QUALITY_FINDINGS.md`, optional `SECURITY_EVIDENCE.md` | Read-only; registered validation commands only; findings do not trigger repair. |

Aliases are not additional workflow behavior: `code_change` resolves to
`repository_change`, and `architecture` resolves to `technical_plan`. Each
pack has a version/content hash, allowlisted capabilities, validation policy,
routing defaults, and artifact land map. Artifact role is stable even when a
host chooses a different logical filename/destination.

### 2.1 Strengths

- The implementation/validation/repair/approval loop already exists.
- Read-only investigation establishes an evidence-first pattern.
- Technical planning already includes requirements, decisions, acceptance, and
  a human-readable handoff.
- Quality findings are correctly treated as a product, rather than an excuse
  for a quality gate to modify code and erase its own evidence.
- Tools/connectors are granted separately from workflow descriptions.
- Artifacts, validator results, costs, task/repair lineage, and events are
  durable and observable.

### 2.2 Material gaps

Current coverage is useful but only covers the middle of a change lifecycle:

```text
repository investigation → technical plan → repository change → quality gate
```

Missing capabilities include a safe front door for ambiguity, typed handoffs
between runs, release and deployment decisions, operational evidence loops,
and special policy for high-risk changes such as migrations. In addition,
`WorkflowPack` is declarative but parts of planning/composition/validation
dispatch still branch on workflow type in `RunCoordinator`. Broad portfolio
growth must not simply add more coordinator conditionals.

---

## 3. Target lifecycle model

The target is a graph of bounded runs, not an autonomous `full_sdlc` pack.
Handoffs are immutable artifact references, not copied prompts or chat history.

```text
                   Domain / feasibility discovery
                            │        │ insufficient evidence / no-go
                            ▼        ▼
                  Change intake       human / expert clarification
                            │
                            ▼
                  Evidence investigation / technical spike
                            │
                            ▼
                       Technical plan
                            │
                            ▼
             Repository change / migration change
                            │
                            ▼
                      Verification gate
                            │
                            ▼
                       Release readiness
                            │ explicit external-write approval
                            ▼
                       Deployment execution
                            │
                            ▼
              Incident triage / service-health review
                            │
                            └────► new intake, plan, or change
```

The default transition is “evidence available for the operator/host to choose,”
not automatic execution. Automatic chaining is allowed only when it does not
increase authority, uses pinned schema-valid inputs, fits the budget policy,
and was requested by the host/operator.

### 3.1 Portfolio by outcome

| Family | Workflow/outcome | Default authority | Priority |
| --- | --- | --- | --- |
| Discover | `feasibility_discovery`; later `technical_spike` | Read-only; isolated write only for spike | First new pack after engine foundation |
| Frame | `change_intake` | Read-only | Immediately after discovery |
| Understand | Evidence investigation | Read-only | Evolve current investigation pack |
| Decide | Technical plan v2 | Read-only | Evolve current plan pack |
| Change | Repository change; later migration change | Isolated repository write | Current / later specialization |
| Verify | Verification gate | Read-only | Evolve quality gate |
| Release | `release_readiness` | Read-only | New pack after verification is solid |
| Deploy | `deployment_execution` | External write, mandatory approval | Late, safety-critical |
| Operate | `incident_triage`, `service_health_review` | Read-only first | After operational connectors exist |

Do not create distinct feature, bug-fix, refactor, documentation, CI, or code
review packs merely because their labels differ. They normally vary by input,
skill, validator profile, or routing policy—not outcome and authority.

---

## 4. Cross-workflow contracts and engine requirements

### 4.1 Typed handoff artifacts

Introduce versioned, content-addressed artifacts that a later run can consume
without reinterpreting prose.

| Artifact | Producer | Consumers | Required content |
| --- | --- | --- | --- |
| `FeasibilityDossier` | Feasibility discovery | Intake, plan, technical spike | Domain model, technical options, evidence, constraints, risk, unknowns, recommendation |
| `SpikeResult` | Technical spike | Intake, plan, change | Hypothesis, method, isolated environment, measurements, limitations, recommendation |
| `ChangeBrief` | Change intake | Investigation, plan, change | Outcome, scope/non-goals, acceptance, constraints, questions, risk |
| `EvidenceReport` | Investigation | Plan, incident, health | Facts, sources, inferences, unknowns, confidence |
| `TechnicalPlan` | Technical plan | Change, verification, release | Decisions, slices, acceptance mapping, compatibility/migration/rollback needs |
| `ChangeSet` | Repository/migration change | Verification, release, delivery | Base revision, patch/artifact hashes, affected surfaces, implementation evidence |
| `VerificationReport` | Verification gate | Release, deployment | Results, acceptance mapping, findings, residual risk |
| `ReleasePlan` | Release readiness | Deployment | Version/notes, rollout, monitors, rollback, approvals |
| `DeploymentRecord` | Deployment | Incident, health | Exact version/target/actions/health/rollback evidence |
| `OperationalRecord` | Incident/health review | Intake, plan | Impact, time range, evidence, hypotheses, recommended follow-up |

Every handoff carries schema version and digest, producing run/pack/task
lineage, relevant source revision, provenance/capture restrictions, and an
explicit state such as `draft`, `evidence_complete`, `approved`, or
`superseded`. The host passes a source-run/artifact reference and selected
curated facts—not full prompts, responses, or host chat history.

An incompatible or superseded input must fail before a model/tool call. A
familiar filename is never proof that an artifact has the expected schema.

### 4.2 Pack execution must become genuinely declarative

Before introducing many packs, move workflow-specific coordinator branches
behind registered pack handlers/task templates. A pack is data plus references
to deterministic registered handlers; it never imports planner-supplied code.

Generic engine dispatch must cover:

1. input/handoff schema validation;
2. fixed or model-backed planner selection;
3. approved task templates and capability grants;
4. composition by declared artifact role;
5. validators by role and policy;
6. repair/finding/approval semantics;
7. output serialization and land-map resolution; and
8. pack-version evaluation fixtures.

A fixed plan is acceptable for a first pack version. It must still be a
pack-owned template rather than a new large `if workflow_type == ...` branch.

### 4.3 Common authority rules

| Authority class | Examples | Rule |
| --- | --- | --- |
| Read-only evidence | Intake, investigation, plan, verification, release, operations | No repository/origin/external writes; findings are legitimate output. |
| Isolated repository change | Repository change, migration change | Writes only inside a confined disposable worktree; landing needs approval. |
| Local delivery | Applying reviewed patch/artifacts | Explicit operator confirmation; path and base-revision validation. |
| External write | Deployment, future PR/issue update | Separate pack/action, registered connector, scoped credentials, mandatory approval, durable receipt. |

A workflow may lower authority per run but may never gain a tool/effect because
a planner, skill, connector result, or host prompt asks for it. Cloud escalation
also remains a visible routing-policy decision, not a pack-private fallback.

---

## 5. Workflow implementation roadmap

Work packages are ordered by prerequisite and authority expansion. Each must
deliver independently testable value; do not start a later package by weakening
the evidence, approval, or artifact-ownership rules of an earlier package.

### WF0 — Pack-engine and handoff foundation

**Goal:** make the current pack abstraction sufficiently real for portfolio
growth without accumulating coordinator-specific behavior.

**Deliver:**

- versioned handoff schemas, references, and manifest lineage;
- generic input/output role validation and handoff serialization;
- registered pack-handler/task-template dispatch for current packs;
- workflow discovery projections: accepted inputs, outputs, authority, and
  eligible next actions;
- fixture/evaluation registration keyed by pack ID, version, and content hash.

**Do not deliver:** a generic arbitrary-DAG language, a user-visible pipeline
runner, or automatic end-to-end chaining.

**Exit criteria:**

- all four current packs use generic pack dispatch without behavior regression;
- aliases retain their current artifacts and recorded canonical pack metadata;
- incompatible handoffs fail before any model/tool call;
- run detail identifies each artifact role and eligible consumer;
- existing graph/contract/security suites plus new handoff suites pass.

### WF1 — `feasibility_discovery`: domain and technical feasibility

**Problem:** before a team can form a trustworthy change request, it often
needs enough external domain knowledge to know whether an idea is technically
viable, what integration/implementation options exist, and which uncertainty
is worth resolving next. This is different from `repository_investigation`,
which explains an existing codebase, and from `technical_plan`, which assumes a
direction has been selected.

Examples include unfamiliar regulated domains, interoperability ecosystems,
third-party platform integrations, hardware constraints, novel model/runtime
choices, and technology-selection questions. A medical integration exploration
may use it to compare eReferral, EHR-access, and data-exchange options without
claiming that Product Factory has made a clinical, legal, or compliance decision.

**Inputs:**

- curated opportunity/question statement and desired decision;
- domain, geography/jurisdiction, target actors, deployment context, and known
  constraints supplied by the operator where relevant;
- optional existing artifacts, repository/source context, or previous evidence;
- explicit research scope, allowed source classes, freshness requirement, time
  and budget ceiling, and whether a follow-on technical spike is permitted.

Inputs must state unknown jurisdiction or data-access assumptions rather than
letting the model infer them. The host must not submit patient data, customer
records, credentials, full private conversations, or unrestricted browser
history as discovery context.

**Outputs:** a versioned `FeasibilityDossier`, plus a concise host summary and
one of these recommendations: `feasible`, `feasible_with_constraints`,
`insufficient_evidence`, `needs_expert_review`, or `not_recommended`.

The dossier must include:

- problem/domain model, actors, integration boundaries, and terminology;
- technical options and a declared comparison rubric (for example capability,
  interoperability, security/privacy, operational burden, maturity, cost, and
  reversibility);
- source-backed facts, sources/retrieval timestamps, source type, and evidence
  confidence; facts, inferences, and unknowns are separate;
- relevant standards/vendor/regulatory/operational constraints, explicitly
  labeled as technical interpretation rather than legal or clinical advice;
- assumptions, contradictions, decision blockers, and risks;
- the recommendation and its evidence basis; and
- a bounded next step: clarification, evidence investigation, technical plan,
  or an optional `technical_spike` charter.

**Authority:** read-only. The pack may use configured repository and web/
documentation connectors through normal grants. It has no repository write,
ticket write, live customer-system, live EHR, production telemetry, deployment,
or credential-use grants in its first version.

**Initial pack contract:**

- Register canonical ID `feasibility_discovery` in `WorkflowType` and the pack
  registry. Do not add an alias until there is a genuine rename/migration need.
- Add a typed input model for the decision statement, domain, jurisdiction,
  actor/deployment context, allowed source classes, source freshness, research
  budget, and optional handoff references. Do not bury these fields in an
  untyped metadata blob.
- Use only the existing read-oriented capability set initially:
  `requirements`, `architecture`, `repository_analysis`, `independent_review`,
  `documentation`, and `composition`. It must never include `implementation`
  or `repair`; connector access still requires the capability/tool-broker grant.
- Declare stable artifact role `feasibility_dossier`, default logical filename
  `FEASIBILITY_DISCOVERY.md`, and default destination
  `docs/FEASIBILITY_DISCOVERY.md`. The role is landable/renamable through the
  normal artifact land-map contract.
- Define role/schema validators such as `feasibility_sections`,
  `research_provenance`, `option_comparison`, and `regulated_claims_review`.
  They validate evidence/labels/required fields, not whether a model's choice
  is substantively correct.
- Add a discovery skill domain only if the skill registry needs domain-specific
  research guidance. Skills may influence context and prompts but cannot widen
  source, tool, or approval authority.

**Regulated/sensitive-domain controls:**

- Regulatory, privacy, clinical, contractual, and security conclusions are
  recommendations requiring named human expert review; they are never marked
  as authoritative approval by model confidence alone.
- When a claim depends on jurisdiction, the dossier records the jurisdiction
  and source date. Missing jurisdiction yields `needs_expert_review` or
  `insufficient_evidence`, not a generic universal conclusion.
- Discovery uses public/approved documentation and synthetic examples only. It
  must not connect to live patient records, ingest PHI, or accept production
  EHR credentials merely to answer a feasibility question.
- Primary standards, regulator/policy documents, official vendor API material,
  and operator-provided evidence should be distinguished from secondary
  commentary. Source credibility is evidence metadata, not an instruction the
  source can use to change grants or policy.

**Planner and validation policy:** start with a fixed, bounded task template:
frame the decision → retrieve/inspect allowed evidence → normalize options and
constraints → independent evidence review → compose dossier. Validators require
the declared decision, scope, source provenance, option rubric, assumptions,
unknowns, and next-step rationale. Unsupported externally verifiable claims
must be flagged or removed. A polished report with only generic claims fails.

**Evaluation:** fixtures must include at least an unfamiliar integration domain,
conflicting sources, an incomplete-jurisdiction case, a stale-vendor-document
case, and a no-go/insufficient-evidence result. Measure source coverage and
freshness, option-comparison usefulness, unsupported-claim rate, correct expert
escalation, cost, and whether a human can make the intended next decision.
Compare against a baseline of direct technical planning from the same prompt.

**Exit criteria:**

- a discovery run produces a pinned dossier that `change_intake` or
  `technical_plan` can consume without copying prompt text;
- every material external claim has a source or is labeled inference/unknown;
- regulated-domain fixtures cannot report a final compliance/clinical verdict
  without the required expert-review outcome;
- no live sensitive-system or external-write tool is grantable;
- human evaluation finds the dossier sufficient to choose a next experiment,
  plan, or no-go decision more often than the direct-plan baseline.

#### WF1.A — `technical_spike` (later, bounded companion)

Discovery sometimes identifies one claim that documentation cannot resolve:
for example, whether an API supports a required transaction pattern or whether
a local model/runtime meets a structured-output constraint. That is a separate
workflow, not a hidden implementation step inside discovery.

`technical_spike` consumes a `FeasibilityDossier` and a narrowly declared
experiment charter. It may write only to a disposable confined worktree and
uses mocks, simulators, synthetic data, or explicitly approved non-production
sandboxes. It outputs a `SpikeResult` containing hypothesis, method, exact
environment, measurements, limitations, and a recommendation. It may not use
production credentials, personal/health data, or deploy. Add it only after the
generic handoff/isolated-worktree contracts are proven.

### WF2 — `change_intake`: intent framing and clarification

**Problem:** vague requests sent directly to implementation cause invented
requirements, hidden scope, and expensive churn.

**Inputs:** curated user request; optional `FeasibilityDossier` or issue/ticket
artifact; optional repository context or source-run reference.

**Outputs:** `ChangeBrief`, plus either a recommended next workflow or a typed
clarification request. Required sections: outcome, scope/non-goals, acceptance
criteria, constraints, risks, assumptions, and unanswered questions.

**Authority:** read-only. It may use approved evidence sources but receives no
repository, issue-tracker, or deployment write tools.

**Policy:** deterministic validators require explicit acceptance/non-goal/
unknown sections and reject unsupported certainty. “Needs clarification” is a
successful and useful outcome, not a planner failure.

**Host behavior:** OpenCode calls `pf_run(workflow="change_intake")`; the
plugin summarizes the recommendation/open questions but does not automatically
start a change run.

**Exit criteria:**

- ambiguous fixtures yield actionable questions rather than fictional plans;
- well-scoped feature/defect fixtures yield valid briefs;
- the pack cannot receive repository write grants;
- technical planning consumes the brief via a pinned reference;
- usable-brief rate and cost are benchmarked against direct planning.

### WF3 — Evidence investigation v2

**Goal:** evolve `repository_investigation` into the reusable
understand/diagnose workflow without changing its read-only authority.

**Inputs:** `ChangeBrief`; repository snapshot; optional operational or ticket
evidence; approved web/documentation sources where connector policy permits.

**Outputs:** `EvidenceReport` that separates facts, inferences, unknowns, and
source provenance. It records repository revision and source retrieval window.

**Implementation rules:**

- preserve the current cited evidence-report artifact and no-write policy;
- connector unavailability and stale source conditions are typed outcomes, not
  reasons to invent evidence;
- retain one investigation workflow for repository, documentation, web, and
  diagnosis evidence unless a future use case differs in authority/output;
- treat all tool result text as untrusted in subsequent model prompts.

**Exit criteria:**

- every material conclusion has evidence or is labeled inference/unknown;
- plan and incident fixtures consume reports by reference rather than prompt
  copying;
- source/connector provenance is visible in run projections;
- tests prove no repository write tool is grantable.

### WF4 — Technical plan v2: decision and execution contract

**Goal:** strengthen `technical_plan` into the deliberate decision boundary
between understanding a request and changing a repository.

**Inputs:** `ChangeBrief`, optional `FeasibilityDossier` and `EvidenceReport`,
repository/source identity, and explicitly selected constraints.

**Outputs:** `TechnicalPlan` plus a readable architecture/implementation
document. It maps each acceptance criterion to implementation slices and
verification evidence; it states compatibility, data migration, security,
rollout, monitoring, and rollback implications when relevant.

**Authority:** read-only. It produces no patch or external side effect.

**Important rule:** missing product decisions remain explicit approval items.
The pack must not invent technical defaults merely to satisfy a document
section validator.

**Exit criteria:**

- request-specific quality remains above the established benchmark floor, not
  just heading/template compliance;
- acceptance-to-verification links are machine-valid;
- decision-relevant input changes create a superseding plan;
- repository-change and release fixtures consume plan references by hash.

### WF5 — Change execution profiles

#### WF5.A — Strengthen `repository_change`

`repository_change` remains the default for a feature, defect, contained
refactor, test improvement, or documentation change. It should consume a
`TechnicalPlan` when one exists and emit a `ChangeSet` containing base revision,
patch/artifact hashes, changed-path summary, acceptance references, and
validation evidence.

Do not split feature/bug/refactor/documentation into separate packs merely for
labeling. Different skills, model routing, test commands, and risk settings can
be profile policy until their authority/output semantics genuinely diverge.

#### WF5.B — `migration_change` only when justified

Add this separate pack only after repository-change provenance is stable and
there are real schema/data/API migration cases. Its different safety contract
justifies a distinct workflow:

- compatibility matrix and affected API/client contract;
- forward migration, backfill, and rollback plan;
- staged validation and data-loss checks;
- mandatory approval before landing and, later, deployment;
- no production data access in the first version.

Migration code may be produced in an isolated worktree. Operational data
backfill and deployment remain later external-write work.

**Exit criteria:**

- current repository-change repair/validation/approval behavior is preserved;
- every `ChangeSet` is traceable to base revision and source plan/brief;
- migration fixtures reject missing compatibility or rollback evidence;
- no migration run can become a production data operation.

### WF6 — Verification gate v2

**Goal:** evolve `quality_gate` into the independent evidence boundary before a
change is considered release-ready.

**Inputs:** `ChangeSet`, `TechnicalPlan`, relevant acceptance criteria, and a
registered validator profile. It may inspect the base/change diff and source
snapshot.

**Outputs:** `VerificationReport` plus the current test-plan, quality-findings,
and security-evidence artifacts. The report distinguishes:

- checks actually run and their evidence;
- acceptance criteria proven, unproven, or out of scope;
- findings/severity/residual risk;
- configured quality, security, performance, accessibility, and compatibility
  evidence; and
- `passes`, `passes_with_risk`, `blocked`, or `insufficient_evidence`.

**Authority:** read-only except disposable build/test outputs inside the
confined worktree. Only registered validation commands and approved read
connectors are allowed. Findings remain deliverables; repair requires a new
change run or an explicitly approved bounded repair policy.

**Do not create:** independent CI or code-review packs before the gate supports
validator profiles. CI is a connector/execution capability; review is one
source of verification evidence.

**Exit criteria:**

- seeded correctness/security/compatibility defects are reported with evidence
  and never silently repaired;
- every required acceptance criterion maps to result or explicit gap;
- skipped/unavailable validators yield `insufficient_evidence`, never a pass;
- detection quality and false-confidence rate are benchmarked against the
  current quality gate and a simpler baseline.

### WF7 — `release_readiness`: decide whether to release

**Problem:** passing repository tests does not establish that a change has a
safe version, migration, operator, monitoring, or rollback story.

**Inputs:** approved `ChangeSet`, `VerificationReport`, `TechnicalPlan`,
version/release policy, and optional prior release/operational evidence.

**Outputs:** `ReleasePlan` containing version/change notes, compatibility
impact, migration preconditions, rollout phases, monitors/alerts, rollback
criteria, required approvals, and a typed outcome: `ready`, `blocked`, or
`needs_decision`.

**Authority:** read-only. It can retrieve declared release metadata but cannot
tag, publish, create a PR/release, change an environment, or deploy.

**Exit criteria:**

- a release cannot be marked ready without required verification/migration/
  rollback evidence;
- release claims reference pinned input digests;
- fixtures produce concrete monitor/rollback criteria rather than generic text;
- OpenCode can inspect/land a release plan but cannot deploy from this pack.

### WF8 — `deployment_execution`: controlled external effect

**Prerequisites:** remote/service execution, trusted credentials,
write-capable deployment connector policies, deployment target registry, and
operator runbooks. See [remote orchestration](handover_remote_orchestration.md).

**Goal:** execute one declared rollout/rollback plan with bounded authority and
durable evidence.

**Inputs:** approved `ReleasePlan`, immutable delivery/build artifact,
target/environment ID, declared change window, and explicit operator approval.

**Outputs:** `DeploymentRecord` with exact version/digest, target, action log,
health checks, observed metrics, policy decisions, and rollback result where
used.

**Authority:** external write. It is the first workflow that may call
write-capable deployment connectors, and only through registered declarative
actions. Free-form shell, arbitrary cloud/Kubernetes/Terraform commands,
automatic credential discovery, and unbounded retry are prohibited.

**Required controls:**

- environment/target allowlist with distinct non-production/production policy;
- confirmation immediately before the effect;
- idempotency/change-window keys and concurrency locks;
- progressive rollout checkpoints with deterministic health thresholds;
- a bounded rollback action named by the release plan;
- typed cancellation/failure behavior that never falsely claims success; and
- durable receipt even when a connector fails or times out.

**Exit criteria:**

- mock connector tests prove no effect occurs without approval;
- duplicate request cannot deploy twice;
- failed health check follows declared halt/rollback policy and records evidence;
- production-target tests require a separately opted-in integration environment;
- deployment tools are ungrantable to every read-only pack.

### WF9 — Operational workflows

Operations closes the feedback loop. Initial operations packs are read-only and
produce evidence/recommendations; mitigation is a separate change or deployment
decision.

#### WF9.A — `incident_triage`

Inputs: alert/incident brief, bounded time window, service/environment ID,
recent deployment records, and configured read-only logs/metrics/traces/runbook
connectors.

Output: `OperationalRecord` with impact, timeline, evidence, hypotheses,
confidence, safe immediate recommendations, and recommended follow-up
(`change_intake`, investigation, rollback decision, or human escalation).

It must not restart services, change traffic, alter data, or deploy. “No
diagnosis with current evidence” is valid output.

#### WF9.B — `service_health_review`

Inputs: selected service, time window, SLO/error/cost/security/dependency
signals, known changes, and policy thresholds.

Output: evidence-backed prioritized maintenance backlog, risk/trend summary,
and recommended investigation/change briefs. It becomes suitable for scheduled
execution only after connector freshness, retention, and cost ceilings are
explicitly configured.

#### WF9.C — Post-incident learning (later)

After a resolved incident, produce proposals for tests, monitors, runbooks, and
prevention work. It does not automatically create tickets or alter code; those
remain explicit host/connector actions in a later scoped integration.

**Exit criteria:**

- every operational connector result records source, query hash, time window,
  retention, redaction, and provenance;
- incident fixtures distinguish observation from inference and label unknowns;
- no operations pack receives external-write authority initially;
- a known incident fixture can produce a bounded evidence-backed `ChangeBrief`.

### WF10 — Evaluation and promotion loop

Each new pack, material pack revision, skill/prompt/routing change, or
connector-policy change is an experiment. Use the existing benchmark/lesson
system rather than building a separate “process improvement workflow.”

For every pack define:

- deterministic and adversarial fixtures plus permitted realistic cases;
- useful-output, safety, cost, latency, tool-call, and retry metrics;
- a simpler baseline, such as direct repository change versus
  intake→plan→change;
- promotion threshold and rollback criterion; and
- pack/model/skill/tool-policy/source hashes under test.

No pack becomes a host default because it sounds comprehensive. Promote it only
when it improves a measured outcome at acceptable cost without regressing safety
or operator comprehension.

---

## 6. Host, UI, and remote-execution integration

### 6.1 OpenCode-first without OpenCode-only behavior

Workflow expansion should require only:

- adding the registered workflow ID and typed input options to `pf_run`;
- concise plugin guidance mapping user intent to bounded workflows;
- rendering typed output/next-action summaries from host responses; and
- extending `pf_merge` only for explicitly landable and already approved
  artifacts.

Do not add a separate slash command for every pack. The plugin must not contain
workflow logic, validation, policy, or hidden authority. A model may recommend
`feasibility_discovery` for an unfamiliar/domain-risky idea and `change_intake`
for a known but ambiguous change request, but the server remains authoritative
about workflow IDs, inputs, grants, and transitions.

### 6.2 Generic hosts

Every pack must operate through:

- `product-factory host` machine JSON;
- control API `HostResponse` envelopes plus run/read projections;
- existing stdio MCP tools, extended only when submit/status/inspect/approve/
  reject/cancel/export cannot express an operation; and
- future remote HTTP/CLI/MCP clients.

Expose workflow catalogue/capability discovery so integrations do not hardcode
stale workflow IDs, artifact roles, or authority assumptions. Unsupported
workflow/input requests fail before allocating a run or consuming budget.

### 6.3 Remote implications

Workflow authority is independent of execution location, but remote operation
requires stronger provenance:

- workspaces are server-owned immutable snapshots, not laptop paths;
- handoffs/deliveries record source repository identity and revision;
- repository changes are delivered as verified patches/artifacts and land
  locally only after confirmation;
- deployment/operations connectors execute only where credential/target policy
  permits, never by granting remote access to a laptop shell/filesystem; and
- remote capture defaults to redacted and stays run-scoped.

---

## 7. Testing and acceptance strategy

### 7.1 Tests required for every pack

Before a workflow is advertised as supported, add:

1. **Pack/contract tests:** registry, aliases, schemas, version/hash, artifact
   roles, unsafe override rejection, and CLI/API/MCP visibility.
2. **Graph tests:** planner output stays inside allowed capabilities, dependencies
   are valid, and expected artifacts/terminal outcomes occur.
3. **Authority tests:** prohibited tools are ungrantable, hostile tool output
   cannot widen authority, and approvals occur at the intended boundary.
4. **Validation tests:** missing, fabricated, stale, or incompatible evidence
   becomes a typed gap/failure rather than a passing artifact.
5. **Resume/cancel/budget tests:** durable restart, cancellation, limits, and
   repair/finding semantics are correct.
6. **Host tests:** affected OpenCode plugin behavior and a generic host/v1 path;
   no slash-command dependency.
7. **Evaluation fixtures:** success, ambiguous/blocked, adversarial/safety, and
   a justified simpler baseline.

### 7.2 Cross-workflow tests

Test the seams as well as individual packs:

- invalid/superseded handoffs are rejected deterministically;
- feasibility discovery without a source, source date, or declared inference
  cannot present an external claim as a fact;
- a regulated-domain discovery with missing jurisdiction/required expert review
  cannot transition directly to an implementation or deployment decision;
- unresolved intake questions cannot become an approved implementation plan;
- verification cannot mark a release-ready result when required evidence is
  missing/skipped;
- release readiness cannot invoke deployment;
- deployment cannot be reached from generic repository-change approval;
- incident triage creates a follow-up brief/reference, not unapproved remediation;
- local and remote hosts show equivalent durable status/artifact ownership.

### 7.3 Outcome measures

| Workflow family | Core outcome measure |
| --- | --- |
| Discovery | Decision usefulness, source coverage/freshness, unsupported-claim and missed-escalation rate |
| Intake | Necessary clarification rate; acceptance completeness without invention |
| Investigation | Evidence precision/coverage and source freshness |
| Plan | Decision usefulness and acceptance-to-verification completeness |
| Change | Valid scoped patches, test success, repair cost, human acceptance |
| Verification | Seeded defect detection and false-confidence rate |
| Release | Readiness decision correctness and rollout/rollback completeness |
| Deployment | Bounded correct action, health/rollback adherence, no duplicate effect |
| Operations | Diagnosis usefulness, evidence coverage, safe actionable follow-up |

Record pack hash, model route, skill versions, tool policy, connector version,
source revision, cost, latency, retries, and quality result. Compare a chain to
the smallest credible alternative; more workflow stages are not inherently better.

---

## 8. Implementation rules and anti-patterns

### Required rules

- Version every pack and handoff schema; record versions/hashes in manifests.
- Keep artifact roles stable even when host-selected names change.
- Treat tool/connector output as untrusted input to later model calls.
- Fail closed on unsupported schema, missing evidence, unsafe path, unresolved
  source revision, or unauthorized external effect.
- Emit durable events/projections for handoff creation/consumption, validator
  results, decisions, and delivery/deployment receipts.
- Keep mock/deterministic paths where practical; live-model quality does not
  replace safety tests.
- Maintain explicit alias/deprecation policy; never silently redefine a workflow.

### Anti-patterns to reject

- An autonomous `full_sdlc` pack that plans, writes, verifies, deploys, and
  reacts to production without visible authority boundaries.
- Packs that differ only by persona/prompt label.
- New packs implemented as unbounded coordinator branches and called declarative.
- Passing whole chat histories/model captures between runs instead of typed
  curated handoffs.
- Treating a feasibility dossier as legal, clinical, security-certification, or
  product-market approval rather than evidence for the named human decision.
- Verification/security packs repairing code to erase their own findings.
- Treating a well-formatted report as proof that an action occurred.
- Letting release/incident packs write to production because a connector exists.
- OpenCode-specific authority that generic host/v1 cannot represent and audit.

---

## 9. Sequencing and completion definition

Implement in this order:

1. WF0 pack engine and typed handoffs.
2. WF1 feasibility discovery, followed by WF2 change intake for a chosen idea.
3. WF3 evidence investigation and WF4 technical-plan improvements.
4. WF5 change-set provenance and migration specialization only when justified.
5. WF6 verification gate.
6. WF7 release readiness.
7. Remote/service and connector prerequisites, then WF8 deployment.
8. WF9 operational workflows.
9. WF10 evaluation and promotion throughout.

The portfolio is ready to describe as an agentic software-delivery lifecycle
when a user can start with an uncertain domain idea, determine whether it is
technically feasible from traceable evidence, then derive a bounded request and
plan, make and independently verify a change, decide release readiness from
durable evidence, and—only where explicit external policy permits—perform a
controlled deployment and later investigate its operational result. At every
transition, the host can identify source artifacts, authority, validation
evidence, cost, status, and the exact user/CLI action needed to proceed. No
workflow gains laptop, source-origin, cloud, sensitive-system, or production
authority implicitly.
