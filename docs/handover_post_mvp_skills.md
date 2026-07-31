# Product Factory — Capabilities, Tools, Connectors, and Skills Handover

**Status:** planning handover for the post-MVP capability/tool portfolio

**Audience:** humans and AI agents implementing the enabling layer for the workflow portfolio
**Scope:** reusable capabilities, deterministic tools, external connectors,
skills, evidence primitives, and authority controls needed by the workflows in
[handover_post_mvp_workflows.md](handover_post_mvp_workflows.md).

The filename uses “skills” for continuity with the project vocabulary, but this
document deliberately covers more than skills. A skill is only one constrained
input to a capability. It must never be used as a substitute for a tool,
connector, policy, or human approval boundary.

**Companion documents:**

- [Workflow portfolio handover](handover_post_mvp_workflows.md) — outcomes and
  workflow authority boundaries.
- [Post-MVP direction](handover_post_mvp.md) — overall product direction.
- [Host integration](host-integration.md) — host/v1, OpenCode, CLI, HTTP, MCP.
- [Remote orchestration](handover_remote_orchestration.md) — server/laptop
  execution, workspace, delivery, and service boundary.
- [Phase 4 connectors](next-work-packages-phase4.md) — existing connector
  framework and current read-only defaults.

---

## 1. Purpose and layer boundaries

Product Factory should gain capabilities by extending stable layers, not by
adding a new “agent” for each new problem. The layers are deliberately distinct:

```text
Workflow pack       = bounded outcome, lifecycle, authority, input/output contract
Capability          = reusable kind of task a plan may assign
Skill               = versioned guidance/context for an allowed capability
Tool class          = policy category used by capability grants
Tool                = deterministic local action behind ToolBroker
Connector           = static external-system adapter with manifest, identity, egress, limits
Validator           = deterministic or bounded evidence check
Artifact/handoff    = durable, content-addressed result consumed by later work
```

For example, `feasibility_discovery` is a workflow. `domain_research` and
`decision_analysis` are capabilities. Searching, retrieving, extracting, and
normalizing a source are connector/tool operations. A domain skill may explain
how to evaluate evidence, but cannot enable web access, accept credentials, or
approve a regulated integration.

### 1.1 Normative principles

1. **Capabilities are small and durable.** Add one only when its authority,
   task semantics, or evaluation differs materially from existing capabilities.
2. **Tools execute; models propose.** The model never gains a raw shell,
   arbitrary URL fetch, filesystem root, cloud credential, or deployment target.
3. **Connectors are static and narrowing.** Registration, enablement, pack
   policy, task grant, egress policy, and budget must all allow a call.
4. **External text is untrusted.** Search results, documents, issue comments,
   logs, and connector output cannot create tools, instructions, grants, or
   authority.
5. **Evidence has provenance.** External facts must preserve source identity,
   retrieval time, digest, excerpt/location, source type, and freshness limits.
6. **Sensitive data does not enter by convenience.** Discovery and spikes use
   public/approved sources and synthetic data; health/customer/production data
   needs separate classification, policy, and expert authorization.
7. **External-write capabilities are exceptional.** Read and analysis features
   arrive before release/deployment writes and retain mandatory approvals.
8. **Every addition is measurable.** A capability, skill, connector, or
   validator becomes a default only after fixtures show better outcomes without
   safety or cost regression.

### 1.2 Non-goals

- An open-ended “browser agent” with arbitrary web access.
- A generic shell, package manager, or command execution capability.
- Giving a skill hidden network, filesystem, secret, or deployment authority.
- Domain-specific privileged connectors for every vertical.
- Letting a remote worker browse a laptop filesystem or use laptop credentials.
- Treating a source citation, polished document, or model confidence score as
  legal, clinical, security-certification, or release approval.

---

## 2. Current baseline

### 2.1 Existing capabilities

The current capability catalogue is repository-oriented:

| Capability | Current intent |
| --- | --- |
| `requirements` | Interpret requirements against repository context |
| `architecture` | System/design analysis and architecture artifacts |
| `repository_analysis` | Read-only source/Git investigation |
| `implementation` | Confined code change |
| `security_review` | Evidence-led threat/security review |
| `test_design` | Test planning and test-oriented changes where granted |
| `test_execution` | Registered test/validation execution |
| `documentation` | Generated durable documentation artifacts |
| `composition` | Assemble final artifacts from task outputs |
| `independent_review` | Independent patch/plan review |
| `repair` | Bounded repair of a validated failed change |

The existing tool classes expose confined repository read/write, Git read,
artifact write, and registered validation-command execution. The broker already
enforces task grants, call budgets, scoped paths, sandboxed commands, artifacts,
and observable tool records.

### 2.2 Existing external connectors and skills

| Layer | Current state | Limitation |
| --- | --- | --- |
| Web | Tavily read-only search, bounded results/excerpts | Search snippets alone are insufficient for defensible external-domain research. |
| Local files | Read-only filesystem MCP with explicit roots/tool allowlist | It is server/local-root access, not a laptop filesystem bridge. |
| Skills | System design, Python service, patch review, threat review | No discovery, interface, release, or operations guidance yet. |
| Validation | Registered local commands, patch/path/secret/document checks | Results are not yet a broad normalized CI/coverage/performance/release evidence plane. |

The current connector manifest/broker design is the correct foundation: a
connector declares tool schemas, permissions, egress, credential *name*,
timeouts, concurrency, retention, and approval requirements. Operator config
may only narrow that ceiling. Nothing in a plan, prompt, or connector response
can register another connector or grant a task access.

### 2.3 The capability gap

The current stack supports the middle of delivery well—repository analysis,
planning, code change, and quality review—but lacks a rigorous external evidence
plane, contract/integration analysis, release/operations evidence, and later
bounded deployment control. Those gaps cannot be closed by prompts alone.

---

## 3. Target capability catalogue

Add the following high-level capabilities gradually. Do not expose one-to-one
capabilities for each connector, standards body, or vertical domain.

| Capability | Primary workflow use | Initial tool classes | Authority | Notes |
| --- | --- | --- | --- | --- |
| `domain_research` | Feasibility discovery, evidence investigation | `web_read`, `repository_read`, `artifact_write` | Read-only | Retrieves and normalizes permitted evidence; never declares approval. |
| `decision_analysis` | Feasibility discovery, intake, plan, release | `artifact_write` plus input artifacts | Read-only | Option rubric, trade-offs, uncertainty, escalation; no hidden source access. |
| `interface_analysis` | Integration feasibility, plans, migration review | `repository_read`, `web_read`, `artifact_write` | Read-only | Generic API/schema/protocol/compatibility analysis; not domain-specific. |
| `release_analysis` | Release readiness | `repository_read`, `git_read`, approved CI/release reads, `artifact_write` | Read-only | Version, rollout, monitor, rollback decision evidence. |
| `operations_analysis` | Incident triage, health review | Approved observability/incident reads, `artifact_write` | Read-only | Bounded time-window analysis; no service mutation. |
| `deployment_execution` | Deployment execution | Declared deployment/health/rollback connector tools | External write | Last capability to add; explicit approval and idempotency mandatory. |

`migration_change` does not initially require a new capability: it can combine
existing planning, interface analysis, implementation, test, review, and
composition under a stricter workflow policy. Add `data_migration_analysis`
only if repeated migration cases prove that generic interface/decision analysis
cannot express their input/output or safety contract.

Likewise, do not create `medical_integration` as a privileged capability. A
health interoperability profile may use `domain_research` and
`interface_analysis`, constrained by a source policy, jurisdiction metadata,
synthetic data, and named expert review. This keeps the authority model
portable across regulated domains.

---

## 4. Foundational evidence and safety primitives

These are enabling primitives, not optional polish. Implement them before
expanding discovery or operations connectors.

### 4.1 Source provenance record

Every externally retrieved source should have a durable normalized record:

- canonical URL and redirect chain, publisher/host, title where available;
- source type (`standard`, `regulator`, `vendor_api`, `operator_artifact`,
  `secondary_commentary`, or another controlled vocabulary);
- retrieval timestamp, content type, size, SHA-256, and extractor version;
- bounded stored excerpt/structured locations and capture-level treatment;
- declared freshness/expiry expectation and retrieval error state;
- connector/tool call ID, egress policy decision, and source trust label.

The record supports citations and auditing; it does not establish truth. A
model must label conclusions as observed fact, inference, assumption, or
unknown. Source content remains untrusted in later prompts.

### 4.2 Data classification and ingress guard

Add a deterministic policy layer before source upload, artifact persistence, or
model routing. It classifies at least public, internal, confidential, secret,
and regulated/personal data according to operator policy.

The first version should fail closed for clearly prohibited values (credentials,
private keys, known sensitive-data patterns) and require a human classification
choice for ambiguous material. It must not claim perfect PII/PHI detection.
The decision, rule version, and redaction outcome are auditable.

For discovery and technical spikes, default policy is public/approved documents
and synthetic fixtures only. Any future live-system data access needs a
separate workflow/connector approval, retention policy, and expert ownership.

### 4.3 Structured evidence and result normalization

Tool output should enter the artifact store in normalized, bounded formats:

- source/citation records for research;
- parsed contract documents and schema diffs;
- structured test/CI/static-analysis result formats;
- metric/log/trace aggregates with query provenance;
- deployment action and health receipts.

Models should receive compact summaries plus references, not unbounded raw
responses. Validators and later workflows consume the structured artifacts by
digest. This is how a quality gate can distinguish “the command ran and failed”
from “the model says tests passed.”

---

## 5. Tool and connector portfolio

This section describes the target portfolio in dependency order. Names below
are proposed public concepts, not an instruction to expose all of them directly
to models. A connector may implement several narrowly typed tools; a local
normalizer may be a brokered system operation rather than an LLM-callable tool.

### 5.1 Discovery evidence plane — first external expansion

`domain_research` needs more than a search-results page. Build a small evidence
pipeline around the existing read-only search connector:

| Tool or service | Purpose | Required boundary | Durable output |
| --- | --- | --- | --- |
| `web_search` | Find candidate, policy-allowed sources | Existing result/egress/size limits; snippets remain untrusted | Search result set and call record |
| `fetch_source` | Retrieve the selected primary source | Explicit public-host/URL policy, redirect validation, private-network denial, content-type and byte limits | Immutable source capture or approved metadata record |
| `extract_document` | Convert supported source content to bounded, location-addressable text/structure | Pure local extraction; no JavaScript execution, macro execution, or embedded-resource fetching | Extracted sections/tables plus extractor version |
| `normalize_citation` | Create a citation/provenance record from a retrieved source | Deterministic; may only reference an owned source capture | Source-provenance record |
| `compare_options` | Produce a decision matrix from supplied evidence artifacts | No network grant; explicit evaluation criteria and unknowns | Decision-evidence artifact |

The first supported input formats should be HTML, plain text, Markdown, JSON,
YAML, and PDF where the extraction library can preserve page/location metadata.
Treat password-protected archives, office macros, executable attachments, and
active content as unsupported. Adding a file type requires a fixture, resource
limits, security review, and an explicit extraction policy.

`fetch_source` must not be a generic HTTP client. At minimum it must validate
each redirect, prevent DNS rebinding/private or link-local address access,
restrict scheme and port, cap response bytes and redirects, reject unexpected
content types, and avoid forwarding ambient credentials. A first implementation
may be more conservative: allow only public HTTPS hosts selected by configured
source-policy rules and fetch only URLs returned by the approved search
connector. Broader direct-source retrieval can be introduced later with an
operator allowlist and separate tests.

Use source-policy profiles, rather than hardcoding one trust hierarchy. For a
medical interoperability discovery, a profile can prefer statutes/regulators,
standards organizations, public implementation guides, and vendor API
documentation; it still records conflicting or stale evidence and requires
human domain review before any claim of compliance.

**Do not add initially:** authenticated web browsing, account-bound crawling,
arbitrary document uploads, website interaction, or a browser automation tool.
Those features change credential, consent, retention, and prompt-injection
risk substantially and are not needed to make the discovery workflow useful.

### 5.2 Interface and integration analysis

`interface_analysis` should turn published and repository-owned contracts into
testable compatibility evidence. It is deliberately protocol-generic so that
FHIR, eReferral, REST, event-stream, database, and internal API work use the
same authority model.

| Tool or service | Purpose | Initial scope |
| --- | --- | --- |
| `parse_contract` | Parse a declared API/schema/protocol document | OpenAPI, JSON Schema, AsyncAPI, Protobuf descriptor/IDL, and repository-owned typed interfaces where feasible |
| `contract_inventory` | Identify operations, entities, auth declarations, versions, error modes, and extensions | Deterministic normalized intermediate representation |
| `diff_contracts` | Compare two declared contract revisions | Breaking/non-breaking/unknown classification with exact locations |
| `map_capabilities` | Compare required business/technical capabilities with documented interface support | Matrix that keeps “not documented” distinct from “unsupported” |
| `generate_synthetic_fixture` | Produce safe request/response fixtures for a known contract | Synthetic only; schema validation and no realistic personal data by default |
| `run_contract_simulation` | Execute a bounded local compatibility or migration simulation | Registered sandbox command and supplied fixtures only |

The model may identify a candidate integration pattern, but parsers and schema
checks establish what a document actually declares. A capability matrix must
preserve provenance per cell and distinguish source fact, repository fact,
inference, assumption, and a question for an integration owner.

Do not begin by connecting to live EHRs, partner sandboxes, or arbitrary
authenticated APIs. A later read-only probe connector requires named system
ownership, least-privilege credentials, a data classification/retention plan,
approved request templates, rate limits, audit logs, and a workflow-level
human approval. Successfully calling an endpoint does not prove production
integration feasibility.

### 5.3 Repository intelligence and verification evidence

The current repository and validation tools are a strong base, but their
results need richer typed read models before more autonomous work is trusted.
Prefer deterministic indexes/parsers over repeated model searches.

| Tool or service | Purpose | Notes |
| --- | --- | --- |
| `symbol_search` / `find_references` | Find declarations, callers, imports, and tests | Language-aware where available; report index revision and incomplete-language coverage |
| `dependency_inventory` | Identify direct/transitive dependencies, licenses, and known package metadata | Read-only lockfile/build parsing; vulnerability intelligence remains a separately governed source |
| `git_history` / `change_impact` | Relate paths/symbols to history, ownership hints, and changed tests | Never infer actual approval ownership from Git history alone |
| `parse_validation_result` | Normalize test, lint, type-check, coverage, benchmark, and security outputs | Registered command produces raw evidence; parser produces bounded structured summary |
| `baseline_compare` | Compare a current validation metric against an approved baseline | Requires an explicit baseline artifact and tolerance, not a model judgement |
| `generate_test_fixture` | Generate or transform safe test data | Classification guard and deterministic schema validation required |

Keep `run_validation_command` registered and sandboxed. Do not turn it into
free-form shell access merely because later workflows need more commands.
New validation profiles should be versioned configuration with an argument
schema, working-directory scope, resource limits, expected evidence parser,
and test fixtures. This supports reproducible quality, performance, migration,
and security gates without making model output executable policy.

### 5.4 Release and supply-chain evidence

Release decisions need evidence from outside the working tree, initially in
read-only form. Add connectors only for systems the operator names and owns.

| Connector family | Read operations | Useful workflows |
| --- | --- | --- |
| Git/hosting provider | pull-request, checks, commit/tag, review, and release metadata | change, release, maintenance |
| CI provider | pipeline/job status, bounded logs, test reports, artifacts | quality, release, incident triage |
| Artifact/package registry | published version, digest, provenance/attestation, SBOM metadata | release analysis, maintenance |
| Issue/incident tracker | explicitly selected issue, incident, and linked decision metadata | intake, operations analysis |
| Service catalog/runbook store | service ownership, dependency, environment, and runbook references | release and operations analysis |

The first version should accept only identifiers selected from workflow inputs
or known repository metadata, and return a typed, bounded projection. It should
not expose a broad “search every issue/project” corpus to prompts. Fine-grained
selection avoids accidental data disclosure and makes provenance understandable.

Validate release claims against immutable identifiers where possible: commit
SHA, artifact digest, build/run ID, attestation digest, and environment
revision. A green CI summary is evidence, not a deployment authorization.

### 5.5 Operational evidence

`operations_analysis` must query operational systems through narrow,
observable, read-only forms. A raw log-search DSL is both expensive and too
easy to misuse. The initial tool contract should require:

- a named service/environment selected from the service catalog;
- a bounded time range, defaulting to a small recent window;
- a typed query kind such as error-rate aggregate, latency percentile, deploy
  correlation, trace lookup by supplied ID, or sampled error exemplars;
- result, byte, cardinality, and query-cost limits; and
- a receipt containing connector, query template/version, selected scope,
  aggregate/sample treatment, timestamp, and retention/capture policy.

The dashboard and workflow artifacts should show aggregates and selected
redacted exemplars by default. They must not silently ingest raw production
logs, customer payloads, access tokens, or unrestricted traces into the model
context or artifact store. Link or reference an operator console when the
investigation requires sensitive raw data.

Read-only incident management can follow this same model: resolve a supplied
incident identifier, retrieve its bounded timeline/links, synthesize an
evidence-led handoff, and propose—not perform—notifications or status changes.

### 5.6 Deployment and change-control tools — last

`deployment_execution` is intentionally a final capability. It consumes
release-analysis evidence and executes only an operator-preconfigured,
idempotent action model. Initial connectors should expose concepts such as:

| Tool | Bound parameters | Required receipt |
| --- | --- | --- |
| `resolve_deployment_target` | Named service, environment, and approved target registry | Target revision/policy snapshot |
| `start_deployment` | Immutable artifact digest, target, declared rollout strategy, approval reference | Deployment/action ID and accepted desired state |
| `get_rollout_status` | Deployment/action ID | Current state and health evidence references |
| `verify_health` | Named health profile and bounded observation window | Health-query receipts and pass/fail/unknown result |
| `rollback_deployment` | A deployment that has an approved rollback target | New action ID, previous/target revisions, and authorization reference |

No deployment connector should accept a shell command, arbitrary manifest,
arbitrary cluster/context, mutable tag such as `latest`, or a credential from a
prompt. Start and rollback must have documented idempotency semantics,
concurrency control, audit identity, timeout/reconciliation behavior, and an
explicit human approval record. Automated rollout expansion remains out of
scope until controlled trials prove the evidence and rollback contracts.

---

## 6. Skill portfolio and lifecycle

Skills improve repeatability by providing compact, versioned task guidance.
They are not plugins with implicit authority, prompt fragments selected by
model preference, or repositories of unverified domain facts. Each skill is
bound to capability types, workflow policy, and an explicit version in the
compiled plan and task record.

### 6.1 Current skills and their durable role

| Existing skill | Correct role | Keep out of the skill |
| --- | --- | --- |
| `architecture.system-design` | Frames architecture alternatives, constraints, and trade-offs | Web access, approval decisions, or claims that a chosen architecture is feasible |
| `coding.python-service` | Guides a confined implementation task in a Python service | Shell/tool grants, filesystem scope expansion, or test-pass assertions |
| `quality.patch-review` | Guides independent review of a supplied patch/evidence | Mutation, merge approval, or access to unrelated history |
| `security.threat-review` | Structures threat finding and mitigation analysis | Security certification, broad scanning authority, or a claim of complete coverage |

Retain these skills, but make their inputs and outputs explicit. For example,
a patch-review skill receives a patch artifact, task scope, validation evidence,
and review rubric; it produces findings with severity, location, evidence,
confidence, and recommended next action. The repair capability, not the skill,
has narrowly granted write authority.

### 6.2 New reusable skills

Build the following skills only with a matching capability, evidence contract,
and evaluation fixture. The suggested names are stable identifiers, not a
requirement that each becomes a separate agent persona.

| Skill | Allowed capabilities | Purpose and required output |
| --- | --- | --- |
| `discovery.evidence-assessment` | `domain_research`, `decision_analysis` | Turns questions into evidence targets; labels source quality/freshness, fact/inference/unknown, and dissenting evidence. Produces a cited research ledger. |
| `discovery.option-framing` | `decision_analysis`, `requirements` | Defines options, criteria, constraints, reversibility, cost/risk, assumptions, and decision owner. Produces a comparison matrix and decision record. |
| `integration.contract-analysis` | `interface_analysis`, `architecture` | Maps contract declarations to use cases, versions, auth/data gaps, compatibility risks, and testable acceptance criteria. Produces a capability matrix. |
| `integration.technical-spike` | `interface_analysis`, `test_design`, `test_execution` | Designs a bounded synthetic experiment with measurable hypotheses and stop conditions. Produces a spike report and reproducible fixture references. |
| `quality.evidence-gate` | `test_design`, `test_execution`, `independent_review`, `release_analysis` | Interprets supplied validation evidence against an explicit policy. Produces pass/fail/blocked/unknown with gaps and next CLI action. |
| `release.readiness-review` | `release_analysis`, `decision_analysis` | Connects immutable build/artifact/CI evidence to rollout, monitoring, rollback, and approval needs. Produces a release decision packet. |
| `operations.incident-synthesis` | `operations_analysis`, `decision_analysis` | Separates observed signals from hypotheses, proposes bounded next reads, and creates a handoff. Produces an incident evidence summary. |
| `deployment.change-control` | `deployment_execution`, `release_analysis` | Checks required receipts, target/artifact identity, approval references, and rollback readiness. Produces an action checklist; it never bypasses approval. |

Avoid skills named after individual vendors or standards unless a repeated,
evaluated use case warrants them. A general contract-analysis skill plus a
versioned FHIR terminology/profile reference is preferable to a privileged
`fhir-agent` with hidden assumptions. Domain reference packs, if added, should
be factual, source-versioned material selected by policy, not instructions that
override a workflow or claim regulatory authority.

### 6.3 Skill contract

Every skill should be a reviewed, versioned package with at least:

- identifier, semantic version, owner, status, and compatible capabilities;
- declared required inputs/artifact schemas and expected output schema;
- concise guidance, including what evidence is sufficient and when to say
  unknown or request expert review;
- links to tool-independent rubrics, examples, and adversarial fixtures;
- data classification constraints and prohibited content handling;
- evaluation set, quality/cost thresholds, and deprecation/migration behavior.

Skill text must never name an undeclared tool, tell a model to ignore policy,
embed credentials, or prescribe free-form commands. Tool availability is
injected from the brokered task grant at runtime and must win on conflict.

### 6.4 Selection and versioning

The compiler, not the agent, selects a skill from a workflow-owned allowlist.
The compiled plan records the selected skill versions and input artifact
digests. This enables replay/evaluation when a skill changes and ensures a
repair task can inherit relevant context without inheriting stale instructions
or arbitrary tool results.

Treat a material prompt/rubric change as a versioned behavior change. Run it
against its fixtures and benchmark slice before making it the default. Retain
the old version for durable run interpretation and explicit rollback; do not
silently reinterpret a historical task with a new skill.

---

## 7. Authority, connector onboarding, and data policy

### 7.1 Connector admission contract

A connector is admitted only after it has a static manifest and an operator
configuration. The manifest must declare, for every exposed operation:

- typed input and output schemas, including bounded result/error forms;
- read/write/destructive classification, idempotency, and approval requirement;
- permitted egress hosts/schemes/ports, authentication *reference* (never
  secret value), and identity/audit behavior;
- timeout, retries, rate/concurrency, byte/page/cardinality, and cost limits;
- capture/retention/redaction handling and a provenance/receipt schema;
- mock or fixture strategy, health check, owner, and revocation mechanism.

The operator configuration can disable tools, reduce limits, restrict hosts or
targets, and choose a credential binding. It cannot expand the manifest’s
declared authority. The workflow and capability grants narrow it again per
task. A request is permitted only when every layer agrees:

```text
manifest ceiling
  ∩ operator configuration
  ∩ workflow policy
  ∩ capability/tool-class grant
  ∩ task scope and budget
  ∩ runtime data/approval guard
  = permitted call
```

This preserves the existing broker model and prevents plans, skills, MCP
descriptions, or connector responses from becoming a control plane.

### 7.2 Risk tiers and escalation

Use a consistent risk model to decide implementation order and human
involvement. Exact names may align with current project enums, but the policy
intent should remain stable.

| Tier | Typical action | Default | Examples |
| --- | --- | --- | --- |
| R0 | Local deterministic read/transform | Allowed within task scope | Schema parsing, source normalization, local code search |
| R1 | Bounded external or repository read | Allowed only by declared grant | Web search/fetch, CI status, artifact metadata |
| R2 | Sensitive or high-impact read | Explicit workflow and operator approval | Production telemetry aggregate, authenticated partner sandbox query |
| R3 | Reversible external write | Named approval and receipt required | Create a draft ticket, start a controlled staging deployment |
| R4 | Irreversible or high-blast-radius action | Human execution/approval; automation exceptional | Production rollback policy change, publish/public communication, data mutation |

R3 and R4 must have a non-LLM approval token/reference bound to the exact
action parameters and expiry. A model-generated sentence such as “approved” is
never sufficient. Any external tool failure, uncertain target identity, or
authorization mismatch returns a typed blocked result rather than inviting a
model to retry with broader parameters.

### 7.3 Content handling and prompt-injection boundary

All connector returns are data, not instructions. Preserve their source and
classification label through extraction, artifact storage, task context,
observability, and remote transport. At prompt construction, present external
content in an explicitly delimited data channel and keep the task policy and
tool grant outside it.

Apply the existing observability capture policy to raw requests/responses and
new source/connector content. `off` and `metadata` modes must not become an
accidental research corpus; redacted/full modes expose exactly their stored
form through the run-scoped dashboard/content APIs. Never reconstruct omitted,
redacted, or transient data for a later task.

Discovery, testing, and simulations should default to public documentation and
synthetic data. For confidential or regulated material, policy must decide:

1. whether the connector may retrieve it;
2. whether it may be persisted and for how long;
3. whether it may enter a local model, a frontier fallback, or neither; and
4. who may view it in CLI logs, artifacts, and the dashboard.

If any of these is unknown, the workflow returns a question or blocked result;
it does not silently route content to a more capable model or a new connector.

### 7.4 Remote-execution implications

The remote orchestration host is a separate trust zone. Connector credentials,
model endpoints, retrieval caches, and server-side workspace access remain
there unless a specifically designed remote connector says otherwise. The host
CLI/plugin should send workflow inputs and receive durable artifacts/events;
it must not turn a user’s laptop filesystem, browser session, or credential
store into ambient tool authority.

Remote workspace synchronization and materialization follow the explicit
snapshot/patch/artifact protocol in
[handover_remote_orchestration.md](handover_remote_orchestration.md). A skill
or connector has no independent path around that protocol.

---

## 8. Implementation architecture

### 8.1 Preserve the existing execution path

New capabilities must use the same durable orchestration path as the current
repository-change capabilities:

```text
workflow input
  -> workflow compiler selects allowed capabilities, skills, model profiles,
     task scopes, budgets, and validators
  -> durable compiled plan/task records
  -> coordinator dispatches an allowed task
  -> prompt builder receives only selected skill + typed artifact summaries
     + current broker tool grant
  -> ToolBroker enforces every local tool/connector call
  -> typed result/provenance artifacts + events + budget ledger
  -> validators / quality gate / repair policy / final handoff
```

Do not create a discovery-specific mini-orchestrator, a connector-specific
agent loop, or a second state store. The workflow compiler remains responsible
for selection, the coordinator for lifecycle, the broker for authority, the
artifact store for durable evidence, and observability projections for status.
An event may notify the dashboard or trigger a projection refresh; it is not
the source of task or approval truth.

### 8.2 Required extension points

The following changes are expected as each portfolio increment is introduced.
They should be small, typed extensions of existing modules rather than a
parallel framework.

| Concern | Extension | Invariants |
| --- | --- | --- |
| Capability registry | Add capability identifier, description, allowed tool classes, input/output artifact contract, and default evaluator | Capability has no embedded connector credentials or dynamic tool selection |
| Workflow pack/compiler | Allow the new capability only in explicit workflow stages; select skill/model/validators from pack policy | A task cannot request a capability or skill outside the compiled plan |
| Tool registry/broker | Add local deterministic tools or connector operations with typed request/result records | Broker remains the only execution path and enforces scoped grant/budget/capture policy |
| Connector manifests | Define static adapter metadata and narrow operator configuration | Plans/prompts/MCP descriptions cannot register or widen a connector |
| Artifact schemas | Add versioned source, contract, decision, validation, operational, and deployment-receipt artifacts | Inputs and citations use digests/ownership checks; raw blobs remain capture-policy governed |
| Prompt construction | Insert skill version and bounded artifact summaries/reference IDs | Do not concatenate raw untrusted documents into trusted instructions |
| Validators | Validate structure/provenance/freshness/coverage and deterministic tool results | Validator outcome is evidence, not an unreviewed model claim |
| Observability | Project capability, tool/connector receipt, source classification, skill/model version, and budget usage | Capture levels and run ownership continue to control raw-content access |

Every new artifact kind should have a JSON schema (or an equivalently strict
typed contract), a producer version, a max size, a canonical content digest,
and an ownership relation to the run/task that produced it. Use forward-tolerant
readers and explicit version migrations—historical research and release
packets are audit artifacts, not temporary chat transcripts.

### 8.3 Core artifact contracts

Implement a small set of reusable contracts before feature-specific reporting.
Fields can evolve, but their semantic distinctions should not be collapsed.

| Artifact | Essential fields | Key consumers |
| --- | --- | --- |
| `source_record` | source ID, canonical URL/host, type/trust label, retrieved time, digest, locations/excerpts, freshness, connector receipt | research, decision analysis, dashboard evidence |
| `research_ledger` | question, source-record refs, claims labeled fact/inference/assumption/unknown, conflicts, open questions | discovery handoff, requirements, architecture |
| `decision_record` | decision/question, options, criteria/weights, evidence refs, uncertainties, owner, state | discovery, intake, plan, release |
| `contract_inventory` | contract digest/version, parser version, entities/operations, auth/version/error declarations, unsupported portions | interface analysis, technical spike |
| `capability_matrix` | required capability, source/repository evidence refs, support classification, gaps, verification method | feasibility, plan, implementation |
| `validation_evidence` | profile version, command/tool receipt, input revision, normalized outcomes, raw-reference availability, baseline comparison | quality gate, release |
| `operation_evidence` | service/environment/time scope, query receipt, aggregates/samples, data classification, linked deploys/incidents | operations, release |
| `deployment_receipt` | approval ref, immutable artifact, target policy snapshot, action IDs, rollout/health/rollback evidence | deploy, dashboard, audit |

An artifact must identify whether a claim is direct evidence or a model
inference. A markdown report can render these artifacts, but it is not the only
representation. The typed version lets later workflows validate required
evidence, the dashboard show provenance, and test fixtures compare behavior.

### 8.4 Tool-result normalization

All new tool and connector responses should first be written as a bounded
receipt, then optionally summarized into an artifact. A receipt minimally
contains operation ID, connector/tool version, task/run IDs, parameter digest
(with safe display fields), start/end times, attempt/retry count, status/error
class, usage/cost fields, result digest, and capture availability. It should
also contain source/query/target provenance where relevant.

This makes connector observability and cost accounting uniform with model and
validation calls. It also permits a task to be resumed or diagnosed without
repeating a potentially expensive, mutable, or privileged external operation.
Do not persist raw request/response payloads unless the capture policy and
data classification permit it.

### 8.5 Model routing and local-first operation

Capabilities must state their model requirements in terms of measurable task
properties, not vendor names: context size, structured-output reliability,
coding/analysis performance, tool-call reliability, language/domain needs,
latency, and acceptable data class. The existing local-first/fallback routing
policy should choose from approved model profiles and record the resolved
provider/model and reason.

The local model is appropriate for bounded extraction summaries, repository
analysis, option drafting, and initial synthesis when evaluation supports it.
Escalate only through a compiled policy when a permitted fallback is necessary;
the escalation must preserve data policy, artifact evidence, token/cost ledger,
and task observability. A fallback must never be triggered simply because the
model asks for data or tools it does not have.

---

## 9. Delivery roadmap

Each work package must deliver an independently testable improvement. Do not
start the next package by adding broad provider integrations; first prove the
preceding package’s contracts and safety controls using fixtures.

### Package S0 — contracts, provenance, and capability plumbing

**Objective:** make it possible to add a capability/connector without creating
unstructured prompt-only state.

Implement the capability metadata extension, artifact-schema registry,
source/connector receipt format, classification/ingress guard, provenance
projection, and skill package metadata/selection. Add compile-time validation
that a workflow only selects known capabilities, compatible skills, permitted
tool classes, and valid artifact schemas.

**Value:** existing repository workflows gain clearer skill/version and tool
receipt provenance; no external connector is required yet.

**Exit criteria:**

- a synthetic task creates and validates a versioned typed artifact and receipt;
- plans record capability, skill, model profile, grants, and expected outputs;
- disallowed skill/capability/tool combinations fail before task execution;
- capture and data-classification decisions appear in run projections without
  exposing protected content;
- historical run readers remain compatible with artifacts they do not know.

### Package S1 — public evidence discovery

**Objective:** make `feasibility_discovery` produce source-grounded, auditable
research and option analysis from public/approved material.

Add `domain_research` and `decision_analysis`, source-policy profiles,
`fetch_source`, supported-format extraction, citation normalization, research
ledger/decision record artifacts, and the two discovery skills. Keep the
existing search connector as a candidate-source mechanism; do not make it the
sole evidence store.

**Value:** an operator can ask a real feasibility question, inspect each claim
and source, see uncertainty/conflicting evidence, and carry a durable decision
record into requirements or architecture planning.

**Exit criteria:**

- fixtures cover redirect, host-policy, oversize, unsupported type, extraction
  failure, stale source, conflicting-source, and prompt-injection text cases;
- every substantive report claim can link to evidence or is marked inference,
  assumption, or unknown;
- a public-source discovery run works with a local model profile and records
  any approved frontier fallback and its cost;
- no raw source can grant tools or cause a fetch outside policy.

### Package S2 — interface analysis and technical spikes

**Objective:** turn research conclusions into testable integration feasibility,
without live partner or customer data access.

Add `interface_analysis`, parsers/inventories for the initial contract formats,
capability matrix and contract-diff artifacts, synthetic fixture generation,
and registered local contract simulation. Add the contract-analysis and
technical-spike skills, then wire them into the feasibility and architecture
workflow stages.

**Value:** an agent can distinguish documented interface support from inferred
feasibility and produce a reproducible spike rather than a generic integration
recommendation.

**Exit criteria:**

- valid/invalid/partial OpenAPI and JSON Schema fixtures produce stable,
  location-addressable inventories; add other formats only after their own
  fixtures;
- contract-diff breaking classifications are tested against known examples;
- generated fixtures pass schema validation and classification rules;
- a spike report contains hypothesis, method, inputs, measurements, result,
  limits, and a next decision; and
- no live authenticated endpoint is needed to complete the workflow.

### Package S3 — delivery intelligence and verification evidence

**Objective:** make code-change, migration, and quality workflows reason from
structured repository and validation evidence rather than raw tool output.

Add language-aware repository intelligence where it pays for itself, versioned
validation profiles/parsers, baseline comparison, safe test fixtures, and the
quality-evidence skill. Start with the languages/build systems represented by
the project’s evaluation repositories; do not promise universal semantic code
search.

**Value:** task planning, review, repair, and release decisions can cite what
changed, which tests ran, what was not measured, and whether a defined baseline
regressed.

**Exit criteria:**

- parsed validation evidence agrees with representative raw test/lint/type
  outputs, including malformed and truncated output;
- a failed validation/repair loop remains task-scoped and carries evidence
  forward rather than rerunning unrelated commands;
- a model cannot execute an unregistered command by placing it in an artifact
  or skill; and
- benchmark and synthetic evaluation runs show a measurable quality or
  diagnostic improvement versus the current tool-output-only baseline.

### Package S4 — release and operations read plane

**Objective:** make release readiness and incident/maintenance workflows
evidence-led while remaining monitor-only.

Add `release_analysis` and `operations_analysis`, beginning with one owned Git
provider/CI system and one observability or incident source. Build typed
read-only projections, immutable ID verification, bounded operational query
templates, release/operation artifacts, and the corresponding skills.

**Value:** a user can receive a release packet or incident handoff showing the
specific build/artifact/health/incident evidence and the CLI action still
required, rather than an uncheckable model summary.

**Exit criteria:**

- connector fixtures cover authorization loss, pagination/oversize, stale or
  inconsistent remote data, rate limiting, and redacted content;
- operations queries cannot expand their service/environment/time range beyond
  the compiled task scope;
- release output keeps unknown/missing evidence distinct from pass;
- the dashboard presents receipts, data classification, source links, and cost
  without exposing captures forbidden by policy.

### Package S5 — controlled deployment execution

**Objective:** add a narrowly useful deployment action only after read-side
evidence, approval, and recovery contracts are proven.

Introduce `deployment_execution` for one non-production target and one
operator-owned deployment adapter. Implement approval references, target
registry, immutable artifact binding, idempotent start/status/health/rollback
receipts, reconciliation after restart, and the change-control skill.

**Value:** the orchestration can perform a clearly authorized staging rollout
and make its result inspectable, without becoming a general CI/CD replacement.

**Exit criteria:**

- missing/expired/mismatched approval blocks action before the connector call;
- duplicate start/retry/restart behavior is deterministic and auditable;
- wrong target, mutable artifact tag, timeout, partial rollout, and failed
  health scenarios are covered by integration tests;
- rollback is offered only when a configured safe target exists; and
- production rollout is explicitly out of scope until an operator signs off on
  a separate policy and trial evidence.

### Package S6 — portfolio optimization and controlled expansion

**Objective:** make capability additions and local/frontier routing decisions
empirical rather than anecdotal.

Establish a fixture corpus, scorecards, model/skill experiment registry,
regression gates, and connector review cadence. Expand source profiles,
contract formats, providers, and vertical reference packs only where measured
workflow outcomes justify the maintenance and authority cost.

**Value:** the product learns which model/skill/tool combination is reliable
and economical for each task while preserving reproducibility and policy.

**Exit criteria:**

- each default skill/model profile has a versioned evaluation record;
- capability-level quality, latency, local/fallback rate, and cost are visible;
- rollback to a previous profile/skill is tested; and
- a connector can be disabled or credential-revoked without corrupting durable
  run evidence or blocking unrelated workflows.

---

## 10. Evaluation, test strategy, and benchmarks

Tool access makes a workflow look more capable even when it is less reliable.
Evaluation must therefore measure not only final prose or code, but evidence
accuracy, policy adherence, cost, latency, and recovery behavior. A benchmark
result is an input to a routing/default decision; it is not proof that a
capability is safe for arbitrary repositories or production systems.

### 10.1 Layered automated tests

| Layer | What to test | Examples |
| --- | --- | --- |
| Schema/unit | Artifact validation, provenance fields, classification, skill/capability compatibility, URL/contract parsing, result normalizers | malformed source record; source with missing digest; incompatible skill rejected; ambiguous data blocked |
| Broker/connector contract | Grant intersection, egress, auth reference, limits, retries, receipt creation, capture handling | redirect to private address denied; maximum bytes enforced; write denied without approval; disabled connector unavailable |
| Compiler/coordinator | Workflow stage selection, task scope, skill/model/validator pinning, repair inheritance | discovery cannot compile deployment capability; repair receives source evidence but not a broader connector grant |
| Integration | Real broker plus deterministic fake connectors and a temporary artifact store | end-to-end research ledger, contract inventory, CI receipt, restart/resume without duplicate write |
| Adversarial | Prompt injection, poisoned citations, deceptive redirects, malformed contracts, truncated logs, stale/conflicting sources | “ignore prior instructions” in a PDF cannot change task grants or result status |
| Browser/API | Observability projections, capture policy, run-scoped content ownership | dashboard displays receipt/status but not a metadata/off capture or another run’s artifact |
| Optional live smoke | One non-sensitive, operator-owned endpoint with isolated credentials | manifest health check and expected bounded response; never the only CI test |

Connector tests use a local fixture server, recorded safe responses, or a fake
transport by default. Tests must not call public websites, partner systems, or
cloud deployments in the normal unit/integration suite. Any opt-in live test
needs a separate marker, explicit operator setup, a disposable target, and a
clear cost/data policy.

### 10.2 Capability scorecards

For each capability/skill/model-profile combination, store a versioned scorecard
against a fixed fixture slice. At minimum record:

- task success and structurally valid artifact rate;
- evidence coverage: material claims with valid source/reference, and the rate
  of unsupported claims incorrectly presented as facts;
- decision/contract accuracy against labeled fixtures, including correct
  `unknown` or `needs-expert-review` outcomes;
- policy violations, denied-call attempts, classification/redaction failures,
  and unsafe retry attempts;
- tool/connector error recovery, duplicate-call behavior, and resume outcome;
- latency distribution, tool calls, local/frontier routing rate, tokens, and
  total estimated/reported cost; and
- human review effort and downstream rework where practical to capture.

Use thresholds appropriate to the risk tier. For example, a discovery
comparison may tolerate an explicit `unknown` answer but must not invent a
citation; a deployment execution profile should have zero tolerance for target
or approval mismatches in test fixtures. Do not average away rare high-impact
policy failures behind a high prose-quality score.

### 10.3 Benchmark portfolio

Maintain three complementary benchmark classes:

1. **Product Factory fixtures.** These are the primary regression suite because
   they encode the exact capability contracts, data policies, tool failures,
   and output artifacts this product requires. Include public discovery cases,
   contract-diff cases, safe synthetic integration spikes, repository changes,
   release evidence, and operational handoffs.
2. **Real-project regression corpus.** Use small, licensed, reproducible
   repositories and sanitized integration/release scenarios that resemble the
   intended user work. Pin revisions, environment images, evaluator versions,
   and budgets so results can be compared over time.
3. **External agent benchmarks.** Consider coding/terminal benchmarks such as
   DeepSWE, Terminal-Bench, and SWE Atlas where their current licenses, task
   format, evaluator, and runtime requirements fit the profile being tested.
   Use them to calibrate coding, repository-navigation, and terminal-tool
   behavior—not to validate discovery evidence, regulated integration claims,
   production operations, or deployment safety.

Run external benchmarks through an adapter that gives the evaluated system only
the task inputs and authority allowed by that benchmark. Do not weaken the
broker, inject hidden credentials, or grant unregistered terminal access merely
to match a leaderboard harness. Report the model, skill versions, tool profile,
hardware/runtime, retries, budget, completion rule, and any excluded tasks.

### 10.4 Experiment discipline

Change one material variable at a time where possible: model profile, skill
version, tool profile, planner policy, or validator—not all simultaneously.
Store the run configuration and evaluator digest with every score. Promotion
requires comparison with the current default on the same fixture set, including
safety and cost measures; a qualitative sample alone is insufficient.

Use a staged release path for every material capability:

```text
fixture-only
  -> operator opt-in / read-only shadow use
  -> limited default for low-risk workflows
  -> broader default after scorecard and incident review
```

External writes stop at opt-in controlled environments until a separate
approval decision expands their scope. A regression in safety, evidence
accuracy, or cost is reason to roll back the skill/model/profile even if final
answer style appears improved.

---

## 11. CLI, MCP, and dashboard behavior

The user should invoke outcomes through the existing CLI surface—initially the
OpenCode plugin and, later, other host adapters—not manage individual tools.
The host submits a workflow request and displays streamed progress, questions,
artifacts, and required CLI actions. It may surface a capability-oriented
description for transparency, but it must not offer a generic “run connector
tool with arbitrary JSON” escape hatch.

MCP remains a transport/integration mechanism, not an authority model. Whether
a host talks through the current plugin, host control API, or MCP server, the
server-side workflow compiler and ToolBroker own grants, credentials, policies,
budgets, and durable evidence. This lets another CLI reuse the orchestration
without inheriting OpenCode-specific slash commands or tool semantics.

The local dashboard remains monitor-only. It should make the following visible
from durable projections and receipts:

- selected workflow, stage/task capabilities, skills, model profile/resolution,
  tool grants, validator state, and repair lineage;
- source/citation and connector provenance, classification, capture
  availability, and bounded result/error metadata;
- tasks waiting on a question, expert review, approval, unavailable connector,
  or validation/health evidence, together with the CLI action required; and
- local/cloud usage, connector costs where available, configured budget, and
  reported/estimated/mixed cost basis.

It must never use event replay as the sole state source, display unavailable
captured content, infer an approval from model text, or introduce mutation
controls. The API and CLI remain the authority for any current or future
control-plane operation.

---

## 12. Implementation guardrails and decisions

The following choices are intentionally locked for the first implementation.
They prevent scope expansion while still leaving room for later adapters.

| Topic | Decision |
| --- | --- |
| Capability granularity | Start with the six generic capabilities in Section 3; use workflow composition and source-policy profiles before adding vertical-specific capabilities. |
| Discovery retrieval | Search plus constrained public HTTPS fetch/extract; no interactive browser, crawler, upload, or authenticated browsing. |
| Sensitive data | Public/approved and synthetic by default; classification guard decides whether any other material is retrievable, storable, or model-routable. |
| Interface work | Parse declared documents and run local synthetic experiments first; no live partner/EHR probing in the initial capability. |
| Commands | Keep registered, sandboxed validation profiles; never add a generic shell. |
| Release/operations | Add owned-system, typed, read-only adapters before any external mutation. |
| Deployment | One non-production target, immutable artifact, approval-bound idempotent actions; no production default. |
| Skills | Compiler-selected from workflow allowlists, versioned/evaluated, and guidance-only. |
| Credentials | Server/operator managed references in connector configuration; never prompts, artifact payloads, or host-CLI ambient credentials. |
| Observability | Receipts and projections always; raw content only as current capture/data policy permits. |

### 12.1 Questions deliberately deferred

These require an operator/product decision when the relevant package begins;
they should not block S0–S3:

- which hosted Git/CI/observability/deployment providers to support first;
- which credential manager or secret injection mechanism is used on the remote
  orchestration host;
- which source-policy profiles are enabled for a particular organization or
  regulated domain;
- which contract formats merit maintained semantic parsers beyond the initial
  set;
- exact promotion thresholds for each model, skill, and risk tier; and
- whether an externally managed connector implementation is acceptable after
  manifest, isolation, provenance, and revocation review.

Resolve each with a short architecture decision record that names owner,
threat/data classification, alternatives, test strategy, rollback/revocation
mechanism, and the workflow/capability scope affected.

### 12.2 Definition of done for a new capability or connector

Do not mark an addition complete when it merely produces an impressive demo.
It is complete when all of the following are true:

- its workflow/capability/skill/tool/artifact boundaries are documented and
  enforced by compile-time and broker checks;
- schemas, fixtures, error/blocked states, provenance, capture behavior, and
  data classification are implemented and observable;
- the connector has a static manifest, narrowed operator configuration, tests,
  owner, limits, and revocation path;
- validation and scorecard evidence demonstrate the intended value relative to
  its authority, latency, and cost; and
- a user can understand from the CLI or dashboard what happened, what evidence
  supports it, what remains unknown/blocked, and which authorized next action
  is required.

---

## 13. Recommended first implementation slice

Start with **S0 followed by S1**, using a public, non-sensitive feasibility
case. This is the smallest slice that proves the central thesis: local-first
multi-agent orchestration can turn a real open-ended research question into a
durable, source-grounded decision handoff without granting broad tools or
depending on a frontier model for every step.

The slice should compile and execute a `feasibility_discovery` run with the
existing host integration, use the current web search connector plus a tightly
bounded source-fetch/extraction path, produce source records, research ledger,
option comparison, and decision record, and expose receipts/capture availability
through the existing dashboard. It should end in one of three honest outcomes:
supported recommendation, recommendation with explicitly bounded uncertainty,
or blocked/escalated-for-expert-review.

Only after that slice is repeatable should the project invest in live system
connectors, operations integrations, or deployment mutation. The next highest
value increment is normally S2: it converts discovery claims into a synthetic,
reproducible technical spike and removes a large class of integration
overconfidence.
