# Product Factory — Skill Granularity and Composition Handover

**Status:** post-MVP design handover

**Audience:** humans and AI agents extending capabilities, skills, model routing,
workflow packs, connectors, and evaluation

**Purpose:** keep the Product Factory skill system useful for constrained local
models without turning it into a large, overlapping collection of privileged
prompts or unmaintainable domain personas.

**Companion documents:**

- [Capabilities, tools, connectors, and skills](handover_post_mvp_skills.md)
  defines the wider capability/tool/evidence portfolio.
- [Workflow portfolio](handover_post_mvp_workflows.md) defines outcome-oriented
  workflow packs and their authority boundaries.
- [Remote orchestration](handover_remote_orchestration.md) defines the
  server/laptop trust boundary.
- [Phase 4 connector work](next-work-packages-phase4.md) defines the connector
  manifest and broker foundation.

---

## 1. Design decision

Product Factory should use **a small, composable, evaluated skill portfolio**.
It should not create an expert prompt for every programming language, framework,
cloud provider, vertical, regulation, or combination of them.

Small/local models benefit from focused task guidance and compact relevant
context. They do not reliably improve when given a long concatenation of
generic best practices, stale domain facts, vendor documentation, and policy
language. Excess context also raises latency, cost, and instruction-conflict
risk.

The default design is therefore:

```text
one workflow outcome
  -> one task capability
  -> one primary method skill
  -> optionally one focused specialization
  -> compact stack/domain/policy profiles and evidence excerpts
  -> brokered tool grants and deterministic validators
```

The compiler resolves this bundle from declared workflow policy and task inputs.
The executing model does not choose arbitrary skills, reference packs, tools,
or credentials.

### 1.1 Principles

1. **Few stable capabilities; more narrowly scoped skills.** A capability
   exists when task semantics, authority, or evaluation changes. A skill exists
   when the method/rubric for an allowed task changes.
2. **Facts are not instructions.** Standards, framework versions, regulations,
   vendor APIs, and repository conventions are versioned profiles or evidence,
   not embedded permanently in a skill prompt.
3. **Authority is never a skill property.** A skill cannot grant network,
   filesystem, shell, credential, ticket, cloud, or deployment access.
4. **Specialize at the point where behavior differs materially.** Prefer an
   `architecture.api-integration` skill over a separate skill for every API
   vendor; prefer a `coding.python-service` skill over one for every Python
   dependency combination.
5. **Context is assembled just in time.** Load only material relevant to the
   task’s objective, repository, target environment, data classification, and
   evidence gap.
6. **Every default is earned by evaluation.** A specialized skill is retained
   only if it improves outcome quality, safety, cost, or review effort against
   a simpler baseline.

---

## 2. Layer boundaries

Use the following decision table before adding a new “skill.” It prevents skill
text from becoming a substitute for policy or software integration.

| If the difference is primarily… | Add or change… | Example |
| --- | --- | --- |
| A user-visible outcome, lifecycle, handoff, or authority level | Workflow pack | `feasibility_discovery`, `deployment_execution` |
| A reusable task type with different grants or output semantics | Capability | `interface_analysis`, `release_analysis` |
| A reasoning method, rubric, or expected structured output | Skill | API-boundary design; contract review; incident synthesis |
| Repository/framework/environment configuration | Stack or repository profile | Python/FastAPI service conventions, package manager, test command |
| Domain facts, terminology, source preferences, or expiry | Domain reference/evidence pack | FHIR R4 reference set, public eReferral sources |
| Regulatory/control requirements and human sign-off | Policy profile, validator, and approval gate | GDPR data-flow review, EU MDR evidence checklist |
| Access to an external system or target | Connector and target configuration | Git provider, CI, AWS ECS staging target |
| Objective correctness or required evidence | Validator/evaluator | Schema validation, test result, approval reference, source freshness |

For example, a request to “add an AWS deployment skill” normally decomposes
into a generic `deployment.change-control` skill, an AWS deployment connector,
an approved target profile, explicit approval, and health/rollback validators.
The skill describes the review method; it cannot run arbitrary cloud commands.

### 2.1 Definitions

```text
Workflow pack    A bounded business outcome and task lifecycle.
Capability       A task class the workflow may assign.
Skill            Compact, versioned guidance for how one allowed task proceeds.
Profile          Structured contextual configuration, facts, or constraints.
Reference pack   Versioned external/domain material selected by policy.
Connector        A static adapter that exposes declared external operations.
Validator        Deterministic or bounded check of evidence and outputs.
```

A task may carry all of these, but they must remain separately identifiable in
the compiled plan, durable task record, prompt manifest, and observability
views. “The agent used a FHIR skill” is not sufficiently precise; the system
must be able to show the selected method skill, the FHIR reference version,
the source records, the allowed connector operations, and the validations that
actually ran.

---

## 3. When to specialize

Create a new skill only when every applicable condition is satisfied:

1. **Recurring demand:** the task appears repeatedly across workflows or
   repositories, rather than being a one-off project detail.
2. **Different method:** a generic skill cannot express the needed reasoning
   sequence, rubric, failure modes, or output contract without becoming vague.
3. **Stable scope:** the specialization has a clear boundary that will remain
   understandable as tools, libraries, and domains change.
4. **No authority expansion:** the need is guidance, not an attempt to obtain a
   broader tool grant, secret, data source, or deployment target.
5. **Evaluability:** representative fixtures can test it against a generic or
   existing-skill baseline.
6. **Ownership:** a person/team owns factual maintenance, evaluation, and
   deprecation where appropriate.

If any condition fails, use a profile, evidence artifact, workflow input, or
task-specific plan instead.

### 3.1 A practical granularity test

Ask these questions:

- Would two tasks using the proposed skill follow a predictably different
  method from the base skill?
- Can that difference be stated in one short task card and one output rubric?
- Is it independent of a specific customer, private repository, or transient
  vendor product version?
- Can it be evaluated without access to sensitive production systems?
- Could a future model use the same skill with a different connector/provider?

A “yes” to these questions supports a skill. A skill that merely says “follow
best practices for X” does not.

### 3.2 Avoid combinatorial specialization

Do **not** create skills for combinations such as:

```text
python-fastapi-postgresql-redis-kubernetes-aws-healthcare-gdpr
```

That combination mixes method, implementation ecosystem, deployment target,
domain facts, sensitive-data policy, and compliance obligations. It will be
hard to evaluate, rapidly stale, too large for a small model, and likely to
encourage implicit authority.

Compose instead:

```text
coding.python-service                 method
stack profile: Python/FastAPI/etc.    repository-derived configuration
domain pack: healthcare/FHIR          reference/evidence selection
policy: regulated data / GDPR         controls and review requirements
target profile: AWS staging           connector-bound deployment configuration
validators: API/schema/test/security  objective evidence
```

---

## 4. Recommended skill taxonomy

### 4.1 Initial portfolio

Start with a small portfolio that maps to high-frequency methods in the
workflow roadmap. The current skills—`architecture.system-design`,
`coding.python-service`, `quality.patch-review`, and
`security.threat-review`—remain useful base skills.

| Skill family | Candidate skill | Primary capabilities | Purpose |
| --- | --- | --- | --- |
| Discovery | `discovery.evidence-assessment` | `domain_research`, `decision_analysis` | Turn a question into evidence targets; distinguish fact, inference, assumption, and unknown. |
| Discovery | `discovery.option-framing` | `decision_analysis`, `requirements` | Compare options with explicit criteria, risks, reversibility, and decision owner. |
| Architecture | `architecture.system-design` | `architecture` | Establish components, constraints, trade-offs, and interfaces. |
| Architecture | `architecture.api-integration` | `architecture`, `interface_analysis` | Design bounded API/integration seams, versioning, error/data-flow concerns, and ownership. |
| Architecture | `architecture.data-migration` | `architecture`, `interface_analysis` | Plan data movement, compatibility, reversibility, and validation evidence. |
| Implementation | `coding.python-service` | `implementation`, `repair` | Make a confined Python service change with repository conventions and test evidence. |
| Verification | `quality.contract-verification` | `test_design`, `test_execution`, `independent_review` | Define and inspect schema/contract compatibility evidence. |
| Verification | `quality.patch-review` | `independent_review` | Review a scoped patch against explicit evidence and risks. |
| Security | `security.threat-review` | `security_review` | Identify assets, trust boundaries, abuse cases, mitigations, and unresolved risks. |
| Release | `release.readiness-review` | `release_analysis`, `decision_analysis` | Convert pinned verification evidence into a release decision packet. |
| Operations | `operations.incident-synthesis` | `operations_analysis`, `decision_analysis` | Separate observed signals from hypotheses and prepare bounded next steps. |
| Deployment | `deployment.change-control` | `deployment_execution`, `release_analysis` | Verify approval, artifact/target identity, rollout, health, and rollback preconditions. |

This table is a target catalogue, not an instruction to implement every skill
immediately. Implement a skill when the corresponding capability/workflow has
an approved first use case and fixture set.

### 4.2 Stack-specific implementation skills

Implementation is the area where specialization commonly delivers real value.
Repository layout, idioms, type systems, test tooling, error handling, and
dependency conventions vary enough that small models often benefit from a
compact ecosystem-specific method.

Create a stack skill at a maintained ecosystem boundary only when the
repository portfolio and evaluation corpus justify it. Examples could include:

| Candidate | Appropriate scope | Do not encode in the skill |
| --- | --- | --- |
| `coding.python-service` | Service boundaries, typing, test patterns, dependency handling, migration conventions | A particular customer’s modules, credentials, or every optional library |
| `coding.typescript-web` | Component/state/testing/accessibility patterns for supported web repositories | A particular design system’s full source or deployment credentials |
| `coding.jvm-service` | Build/test/error/serialization conventions for the supported JVM ecosystem | Every framework version and organizational exception |
| `coding.infrastructure-change` | Declarative change, plan/review, drift/rollback reasoning | Direct cloud mutation authority or arbitrary IaC commands |

The repository profile supplies the actual language version, build tool,
framework, local commands, conventions, architecture records, and relevant
file excerpts. The skill supplies the reusable method. If a new framework is a
minor variation, extend the profile or add a short specialization instead of
forking the base skill.

### 4.3 Architecture and design skills

Architecture skills should specialize by problem shape, not by every technology
label. High-value shapes include API/integration boundaries, evented systems,
data migration, reliability/operations design, and security-sensitive trust
boundaries.

For example, `architecture.api-integration` should require an interface
inventory, versioning/error/auth/data-flow analysis, ownership boundaries,
compatibility risks, and testable assumptions. It should use a repository or
domain profile to learn whether the implementation is FastAPI, Spring, Node,
or something else.

Create a framework-specific architecture skill only if measured evidence shows
that framework-specific constraints repeatedly dominate the design method and
cannot be represented as a profile or validator.

### 4.4 Domain-specific analysis

Domain specialization is useful, especially for discovery and integration
work, but it should be split into method and reference layers:

| Concern | Correct form |
| --- | --- |
| How to assess evidence, uncertainty, options, and expert-review needs | Discovery or interface-analysis skill |
| Terminology, official documents, standards versions, source priority, freshness | Domain reference/evidence pack |
| Customer-specific business process and constraints | Typed workflow input and operator-provided artifacts |
| Claims requiring regulated expertise | Policy gate and named human review |

For a healthcare interoperability task, use a generic evidence-assessment or
API-integration skill alongside a versioned FHIR reference pack and a source
policy that prefers official standards, regulator material, and vendor
documentation. Do not build a privileged `medical-integration-agent` that
implies clinical, legal, or compliance expertise.

Reference packs must record source version, retrieval date, jurisdiction where
relevant, owner, expiry/freshness rule, data classification, and permitted
workflow/capability use. They are evidence data and must be treated as
untrusted content at prompt construction.

### 4.5 Security and compliance

Security/compliance requirements must be implemented as a combination of
guidance, deterministic controls, evidence collection, and human decisions.
They must not be represented solely by skills with names such as `gdpr-expert`
or `hipaa-compliant`.

| Requirement | Required mechanism |
| --- | --- |
| Data handling constraints | Data classification, ingress/routing/retention policy |
| Required control/evidence checklist | Versioned policy profile and validator rules |
| Threat/risk analysis | `security.threat-review` or a narrow review skill |
| Code/configuration checks | Registered scans, static checks, dependency evidence, test profiles |
| Legal, privacy, clinical, or certification conclusion | Named human reviewer and approval record |
| Regulation/standard facts | Maintained reference pack with jurisdiction/date/source provenance |

A `privacy-impact-review` skill may be appropriate when it consistently
produces a data-flow inventory, decision questions, evidence gaps, and required
reviewers. Its allowed output is `needs_privacy_review`, not “GDPR compliant.”
The same principle applies to EU MDR, SOC 2, HIPAA, ISO standards, or any
customer control framework.

### 4.6 Cloud-provider and deployment specialization

Cloud/provider details belong primarily in connector manifests and target
profiles. A generic deployment skill can verify the method for any provider:
immutable artifact identity, declared target, approval reference, rollout
steps, health evidence, stop conditions, and rollback preconditions.

```text
deployment.change-control       generic task method
AWS/ECS staging target profile  named, operator-approved configuration
AWS connector                   typed allowed actions and credential binding
approval record                 non-LLM authorization for exact parameters
health validator                objective rollout/health result
```

Only add a provider-specific deployment specialization when a provider’s
deployment semantics require a substantially different review or recovery
method and fixtures prove the improvement. It still cannot widen connector
authority or select arbitrary accounts, clusters, or commands.

---

## 5. Context-budget and composition policy

### 5.1 Context is a quality budget

The advertised context window of a model is not its reliable reasoning budget.
For small local models, performance often falls when the task includes a long
skill library, broad repository dump, full external documents, and multiple
conflicting rule sets. Treat every token as competing for attention with the
actual task and evidence.

The initial compiler policy should use conservative defaults, calibrated per
model profile:

| Context component | Default limit | Rationale |
| --- | --- | --- |
| Primary skill | One, concise and task-specific | Provides the main reasoning method. |
| Supplemental specialization | Zero or one | Only when it addresses a known task shape absent from the primary skill. |
| Stack/domain/policy profiles | Up to two compact relevant profiles | Supplies constraints/facts without turning the prompt into a manual. |
| Evidence excerpts | Bounded excerpts with artifact/source references | Allows reasoning from evidence without loading complete documents. |
| Repository context | Task-scoped files/symbols/diffs plus a compact inventory | Avoids unbounded repository dumps. |
| Raw connector output | Never by default | Store it under capture policy; summarize/normalize first. |

Set concrete token limits per resolved model profile after evaluation. A
reasonable starting target is short skill guidance—hundreds to low thousands of
tokens—not multi-document handbooks. Reserve most of the model’s reliable
context for the task objective, relevant code/evidence, output schema, and
current validation feedback.

### 5.2 Resolved task-context manifest

Before dispatch, the compiler should create a durable context manifest:

```text
task ID and capability
workflow/pack version
primary skill ID + version + digest
optional specialization ID + version + digest
selected stack/domain/policy profiles + digests
input artifact and source-record references
repository selection criteria and resolved file/symbol references
tool-class grants, validators, model profile, token/budget limits
```

The prompt builder renders a bounded view of this manifest. The full manifest
is persisted for replay, diagnosis, cost analysis, and skill evaluation. A
repair task may inherit relevant evidence and profile references, but it must
receive a fresh task scope, validator state, budget, and tool grant.

### 5.3 Conflict and precedence rules

Use a deterministic precedence order:

```text
system and runtime safety policy
  > workflow authority and task contract
  > connector/tool grants and data policy
  > validator/output schema
  > selected skill method
  > profiles/reference content
  > untrusted external/repository text
```

If two selected profiles conflict, the compiler should reject the task or
produce an explicit question for the operator; it must not ask the model to
improvise a reconciliation. Skills must instruct the model to label conflicts,
unknowns, and evidence gaps rather than resolve them with confidence.

---

## 6. Selection and lifecycle architecture

### 6.1 Compiler-driven selection

Skill selection is an explicit compile-time policy decision. The flow should be:

```text
workflow input + workflow policy + repository/domain metadata
  -> select capability and task role
  -> select primary skill from capability/workflow allowlist
  -> select optional specialization from a compatibility rule
  -> resolve profiles and bounded evidence references
  -> validate data policy, context budget, and output/validator contract
  -> persist resolved task-context manifest
  -> dispatch task with brokered grants
```

Do not let a planner select from an unrestricted skill catalogue based on a
free-form task description. A model may recommend a different method or request
missing expertise, but the result is an observable question/escalation—not a
dynamic skill or authority change.

### 6.2 Skill package contract

Each skill package needs a small, structured manifest and concise guidance:

- stable ID, semantic version, owner, lifecycle status, and compatible
  capabilities/workflow stages;
- required input artifact schemas and expected output schema/rubric;
- declared profile slots it can consume, such as `repository_stack` or
  `domain_reference`, with maximum sizes;
- guidance for evidence sufficiency, uncertainty, blocked results, and human
  review;
- explicit non-goals and prohibited claims;
- fixture/evaluation identifiers, score thresholds, and known model support;
- deprecation and migration policy.

Skill content must not include credentials, raw customer data, full standards
documents, free-form commands, tool instructions outside the broker grant, or
instructions to override policy. Use a package digest in every resolved task so
historical behavior remains interpretable after an update.

### 6.3 Profiles and reference packs

Profiles should be structured where possible. A repository stack profile, for
example, can hold language/runtime versions, build/test commands by registered
profile ID, supported framework conventions, architecture-record references,
and applicable code locations. It must not be an unbounded prose dump.

Domain and policy packs must include source provenance and expiry. The compiler
can choose the relevant compact excerpts based on the specific question, rather
than attaching all FHIR, GDPR, or cloud documentation to every task. If a
required reference is stale, missing, or incompatible with a stated
jurisdiction/version, the workflow should return `insufficient_evidence` or
`needs_expert_review`.

---

## 7. Worked compositions

### 7.1 FHIR façade feasibility and design

```text
Workflow:          feasibility_discovery, then technical plan
Capabilities:      domain_research, decision_analysis, interface_analysis,
                   architecture
Primary skills:    discovery.evidence-assessment; architecture.api-integration
Domain profile:    FHIR R4 reference pack, selected public sources, terminology
Policy profile:    regulated-data boundary and jurisdiction-specific review gate
Stack profile:     existing service/repository conventions, if present
Connectors:        approved public-document search/fetch only
Validators:        research provenance, option comparison, contract inventory,
                   regulated-claim review
```

The skills guide research/design method. The domain pack supplies factual
sources. The policy profile prevents live patient/EHR data and requires human
review of privacy/regulatory conclusions. No skill is permitted to claim FHIR
conformance, clinical safety, or legal compliance without the required evidence
and named expert review.

### 7.2 Python/FastAPI implementation slice

```text
Workflow:          repository_change
Capability:        implementation, test_design, test_execution, independent_review
Primary skill:     coding.python-service
Optional profile:  repository stack profile: Python/FastAPI/Pydantic/test runner
Inputs:            approved technical plan, scoped files/symbols, acceptance tests
Tools:             existing confined repository write + registered validation tools
Validators:        type/test/lint/API-schema profiles, patch review
```

The actual FastAPI/Pydantic versions and project conventions come from the
repository profile and lockfiles, not a hard-coded framework manual. Add a
FastAPI-specific specialization only when real fixtures show that a generic
Python-service method is repeatedly insufficient.

### 7.3 GDPR-sensitive product change

```text
Workflow:          change intake -> technical plan -> repository change
Capabilities:      requirements, architecture, security_review, implementation
Primary skills:    architecture.system-design; security.threat-review
Policy profile:    data classification, EU jurisdiction, retention/routing rules
Reference pack:    approved and versioned privacy/control sources
Validators:        data-flow inventory, secret/PII checks, required-review gate
Approval:          named privacy/legal review where policy requires it
```

The output can identify a likely data-protection impact, missing evidence, and
required review. It cannot declare “GDPR compliant.”

### 7.4 Controlled cloud deployment

```text
Workflow:          deployment_execution
Capability:        deployment_execution
Primary skill:     deployment.change-control
Target profile:    named operator-approved non-production environment
Connector:         static provider adapter with typed deployment actions
Inputs:            approved release plan and immutable artifact digest
Validators:        approval match, rollout health, timeout/reconciliation,
                   configured rollback target
```

The cloud provider integration determines what action can occur. The skill can
recommend a halt or show a missing prerequisite; it cannot choose a different
account, cluster, artifact, or approval.

---

## 8. Evaluation and promotion

### 8.1 Required fixtures

Every new skill must have compact, versioned fixtures that test both quality and
safe failure. Include:

- representative happy-path tasks with structured expected outputs;
- incomplete, contradictory, stale, or irrelevant input evidence;
- prompt-injection text inside source/repository content;
- missing profile/reference, invalid schema, and context-budget overflow cases;
- task outcomes that must become `unknown`, `blocked`, or
  `needs_expert_review`; and
- comparison tasks against the simpler base skill or no-specialization baseline.

For stack skills, use small licensed repositories or fixtures with deterministic
build/test environments. For domain/compliance use cases, use public and
synthetic material only; do not make sensitive customer data a prerequisite for
evaluation.

### 8.2 Scorecard

Record, per skill version and model profile:

- structured-output and validator-pass rate;
- task-specific quality and evidence coverage/citation accuracy;
- unsupported-claim, policy-violation, and denied-tool-attempt rates;
- correct `unknown`/escalation rate for intentionally insufficient evidence;
- latency, context tokens, model/tool cost, and local/frontier routing rate;
- reviewer correction effort and downstream repair/rework where measurable.

Promotion requires a material improvement versus the baseline without a safety,
cost, or latency regression that exceeds the workflow’s stated threshold. A
polished answer is not sufficient evidence of a useful specialization.

### 8.3 Lifecycle

```text
proposal with recurring-task evidence
  -> fixture and package contract
  -> local/offline evaluation
  -> opt-in shadow or low-risk use
  -> bounded default for compatible workflow/model profiles
  -> periodic regression and ownership review
  -> deprecate or merge if value disappears
```

Version material changes to guidance, rubric, permitted profiles, or expected
outputs. Preserve the old package/digest for historical runs and support an
explicit rollback. Do not silently replace a default skill after editing prompt
text.

---

## 9. Implementation sequence

### G0 — Skill and profile contract

Implement the package manifest, profile/reference-pack schemas, compatibility
validation, context manifest, prompt-budget enforcement, and observability
fields. Migrate existing skills into the contract without changing their task
authority.

**Exit criteria:** a compiled task records exact skill/profile/reference
digests; incompatible or over-budget bundles fail before model dispatch; old
runs remain readable.

### G1 — Evaluate the existing base skills

Create fixtures for current architecture, Python service, patch review, and
threat review skills. Establish generic/no-skill baselines on supported local
and fallback model profiles.

**Exit criteria:** retain, revise, or retire each current skill using a recorded
scorecard rather than subjective preference.

### G2 — Add the first method specializations

Implement only the two or three highest-value additions supported by workflow
work—normally evidence assessment, API/integration architecture, and contract
verification. Add their profiles and validators before adding domain/vendor
variants.

**Exit criteria:** a discovery-to-technical-plan scenario and an integration
change scenario show better structured evidence/acceptance coverage without
larger unsafe context bundles.

### G3 — Repository-derived profiles

Build deterministic repository-stack discovery that produces compact profiles
from declared manifests, lockfiles, architecture records, and registered
validation commands. Do not rely on a model to infer the whole stack from an
unbounded source tree.

**Exit criteria:** supported repositories resolve a stable profile; unsupported
or ambiguous repositories produce an explicit limited/unknown profile rather
than a fabricated configuration.

### G4 — Domain/policy packs and controlled deployment composition

Add versioned public-domain reference packs, policy profiles, and the
human-review/approval gates needed by regulated discovery and controlled
deployment. Introduce provider-specific target/connector profiles only behind
the existing broker/manifest controls.

**Exit criteria:** FHIR-style discovery and non-production deployment fixtures
demonstrate correct method/profile/tool separation and cannot gain extra data
or mutation authority through skill selection.

---

## 10. Non-goals and guardrails

- Do not build a marketplace of arbitrary third-party prompt files before the
  package contract, provenance, and evaluation system exist.
- Do not let a model dynamically install, select, or modify skills in a run.
- Do not encode secrets, production endpoints, private repository knowledge,
  or full proprietary documentation in skills.
- Do not represent a legal, clinical, security-certification, or compliance
  conclusion as a model-skill output.
- Do not solve lack of tool integration by making skills issue free-form shell
  or HTTP instructions.
- Do not make every named technology, cloud provider, regulation, or framework
  a first-class skill. Prefer composition until measured evidence proves a
  durable specialization is needed.

## 11. Definition of done

A skill or profile addition is complete only when:

- its layer boundary and allowed capability/workflow use are documented;
- it has a versioned package/profile schema, owner, digest, and lifecycle;
- the compiler can select it deterministically within a bounded context budget;
- it cannot widen a task’s data, connector, tool, or approval authority;
- fixtures demonstrate useful behavior and safe blocked/unknown outcomes;
- scorecard results justify its default use for a named model/task profile; and
- CLI and dashboard observability reveal what was selected, what evidence was
  used, what remained unknown, and what policy/validator controlled the result.

The success metric is not the number of skills available. It is the ability of
small, locally run models to produce more reliable and reviewable outcomes with
less unnecessary context, cost, and human rework.
