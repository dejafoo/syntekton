# Product Factory — Architecture

This document describes how Product Factory works: goals, control flow, security boundaries, persistence, and evaluation. It is intended for humans and agents working in this repository.

For package layout and “where to edit what,” see [Codebase structure](codebase-structure.md). Decision history lives in [ADRs](architecture/). Pre-PM5 hardening contracts for
effective task policy and run-scoped artifact instances are proposed in
[ADR-007](architecture/ADR-007-effective-policy-and-artifact-instances.md).
Performance measurement work is tracked in
[orchestration-performance-plan.md](orchestration-performance-plan.md).

---

## 1. Purpose

Product Factory is a **multi-agent orchestration MVP** that turns a natural-language request (plus optional Git repository) into a **validated deliverable**:

| Workflow | Deliverable |
| --- | --- |
| `code_change` | Unified diff / proposed patch (approval before apply) |
| `architecture` | `ARCHITECTURE.md` (structured architecture document) |

Design goals:

1. **Typed task graphs** — planners emit `TaskSpec`s bound to registered *capabilities*, not free-form agents or tools.
2. **Fail-closed execution** — empty patches, invalid diffs, provider failures, and validation failures are explicit errors, not silent “success.”
3. **Local authority over tools** — models propose tool calls; `ToolBroker` is the only execution path.
4. **Isolated repository edits** — writes happen in Git worktrees; the original repo stays untouched until explicit apply.
5. **Measurable quality** — deterministic validators first, then an LLM judge harness comparing orchestration to baselines.

---

## 2. High-level system map

```text
┌─────────────┐     ┌──────────────────┐     ┌─────────────────────────┐
│ CLI / API   │────▶│  RunCoordinator  │────▶│ Persistence             │
│ (Typer)     │     │  (authoritative  │     │ SQLite + content-addr.  │
└─────────────┘     │   run path)      │     │ artifacts + events      │
                    └────────┬─────────┘     └─────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────────┐
│ Planning       │  │ ModelGateway   │  │ ToolBroker         │
│ planner +      │  │ (OpenRouter /  │  │ grants, path scope │
│ compile_plan   │  │  Mock)         │  │ registered cmds    │
└────────────────┘  └────────────────┘  └─────────┬──────────┘
                                                  │
                                         ┌────────▼──────────┐
                                         │ Git worktrees +   │
                                         │ patches / validate│
                                         └───────────────────┘

Separately:

┌──────────────────┐     ┌─────────────────┐     ┌──────────────┐
│ BenchmarkRunner  │────▶│ Subject runners │────▶│ Deterministic│
│ (bench CLI)      │     │ orch / baseline │     │ checks + LLM │
└──────────────────┘     └─────────────────┘     │ judge        │
                                                 └──────────────┘
```

**Authoritative runtime:** `RunCoordinator` in `orchestration/coordinator.py`. LangGraph (`orchestration/graph.py`) provides a checkpointed *skeleton*; the coordinator owns the real plan → execute → validate → repair → compose loop used by CLI and benchmarks.

---

## 3. Core domain concepts

### 3.1 Capabilities (not agents)

Capabilities are a fixed catalogue (`domain/capabilities.py`), for example:

- `repository_analysis`, `implementation`, `repair`
- `independent_review`, `composition`
- `architecture`, `requirements`, `test_execution`, …

Each capability has an allowed set of **tool classes**. The planner may only schedule tasks whose capability and tools are in that catalogue (ADR-003).

### 3.2 Tasks and plans

| Type | Role |
| --- | --- |
| `TaskSpec` | One unit of work: capability, objective, dependencies, path scopes, budgets, acceptance criteria |
| `PlannerOutput` | Proposed DAG + final artifact specs |
| `CompiledPlan` | Topologically ordered, compiler-validated plan |
| `TaskResult` | Status, artifacts, findings, usage, changed files |
| `RunRequest` / `RunManifest` | Input contract and durable run outcome |

**Acceptance criteria** carry verification methods (`test_suite`, `command`, `artifact_check`, `llm_review`, …). The compiler rejects plans that lack output schemas, acceptance criteria, or valid dependency structure.

### 3.3 Findings and validators

- **`ValidatorResult`** — deterministic check outcome (`pass` / `fail` / `error` / `skip`).
- **`Finding`** — structured review issue with severity, confidence, evidence refs, optional criterion id.

Blocking validation failures and blocking findings can spawn **repair** tasks.

---

## 4. End-to-end run lifecycle

A typical `code_change` run:

```text
1. Initialize run id, SQLite row, event stream
2. Snapshot repository (base commit) if a repo path is provided
3. Plan
     - mock / planner_mode=fixed → deterministic risk-aware templates
     - live → plan_with_gateway (structured JSON), with schema fallbacks
     - metadata switches: disable_review, force_review, disable_analysis, …
4. Compile plan (fail or repair-loop on compiler errors)
5. Execute waves of ready tasks (dependency-aware)
6. After writable tasks: run validators (patch apply, path scope, secrets, smoke commands)
7. Optionally enqueue repair tasks (budget- and fingerprint-bounded)
8. Compose final proposed.patch from worktree lineage
9. Persist manifest, usage, artifacts under .product-factory/runs/<run_id>/
```

### 4.1 Planning modes

| Mode | When | Behavior |
| --- | --- | --- |
| Deterministic / `planner_mode=fixed` | Mock gateway, or explicit metadata | `default_code_change_plan` / `default_architecture_plan` |
| Live | OpenRouter + no fixed mode | Model returns `PlannerOutput` JSON |
| Policy overlays | Request metadata | Strip analysis/review, force review task, etc. |

Low-risk deterministic code plans shrink to **implementation + composition**. High-risk keywords keep analysis/review.

Benchmark `full_orchestration` currently defaults to `planner_mode=fixed` for reliability; live planner remains available via ablation subjects.

### 4.2 Task execution by capability

| Capability | Behavior (summary) |
| --- | --- |
| `repository_analysis` | List/read repo; write analysis JSON |
| `implementation` / `repair` | Bounded **agent loop**: inspect → edit → validate tools → finish; then capture `git_diff` |
| `independent_review` | Structured findings JSON (evidence required; weak evidence demoted) |
| `composition` | Deterministic patch (or architecture doc) from inherited lineage |

Implementation grants are intentionally narrow (read/search/write/diff/validate)—not arbitrary shell or artifact writes that skip the patch path.

### 4.3 Agent loop

`orchestration/agent_loop.py` runs `model → tools → model` with:

- tool-call, token, cost, and wall-clock budgets
- inspect-before-write
- repeated identical tool-call stop
- repeated write/patch fingerprint stop (`no_progress`)
- truncated tool results with content hashes

Tool authorization failures are returned to the model as tool messages when possible; post-loop `git_diff` reserves grant headroom so composition is not starved.

### 4.4 Context assembly

`context/assembler.py` builds layered prompts:

1. Core execution contract (untrusted repo/tool content)
2. Agent profile
3. Skills + tool definitions
4. Task JSON + context manifest (excerpts / dependency outputs)

`context_mode`:

- `targeted` — scored file excerpts with line numbers
- `file_list_only` — paths without bodies (ablation)

Sensitive paths (`.env`, secrets, venvs) are omitted.

### 4.5 Worktrees, lineage, composition

- Each writable task gets a Git worktree at the base commit.
- Dependency patches are applied in order; superseded predecessors are skipped.
- Overlapping path ownership or failed apply → **`composition_conflict`**.
- Per-task `{task_id}-lineage.json` records base commit, inherited artifact digests, pre/post patch fingerprints.
- Composition diffs the composed worktree vs base; empty composition fails if the run expected a patch.

### 4.6 Validation and repair

Runtime validators (also mirrored in the bench harness):

- patch format / applies
- path scope
- secret scan
- expected files (bench cases)
- registered smoke commands (e.g. `python_tests` via `config/policies.yaml`)

Repair:

- created from failed validators or blocking findings
- inherits failed candidate patches
- bounded by run-level and per-origin `max_repair_attempts`
- terminated on equivalent fingerprints / no-progress thresholds

### 4.7 Approvals

`code_change` can finish as `awaiting_approval`. CLI `approve` / `reject` / `apply` complete the human gate before touching the original repository.

---

## 5. Model gateway

All inference goes through `ModelGateway` (ADR-002):

| Implementation | Role |
| --- | --- |
| `OpenRouterGateway` | Live HTTP chat completions, tools, seeds, cost estimates |
| `MockGateway` | Deterministic offline behavior for tests and mock benches |
| `InstrumentedModelGateway` | Records requests/responses into observability |

Canonical types (`gateway/canonical_messages.py`) keep prompts and tool calls provider-neutral.

Structured outputs: prefer `json_schema`; on provider rejection, OpenRouter retries with `json_object` plus an explicit schema reminder.

---

## 6. Tool broker and security

**ADR-004:** LLMs never execute tools directly.

`ToolBroker`:

1. Looks up tool in registry
2. Checks capability grant (`tool_names`, call budget)
3. Enforces **read vs write path patterns**
4. Resolves paths under the worktree (no escape to original repo)
5. Executes and records audit history / observer events

Registered commands only (no free-form shell). Results are treated as **untrusted** content in subsequent prompts.

---

## 7. Persistence and observability

Runtime data root: **`.product-factory/`** (gitignored).

| Store | Contents |
| --- | --- |
| SQLite (`data/product_factory.sqlite`) | Runs, tasks, validator rows, bench scores, events metadata |
| Artifact store | Content-addressed blobs (patches, JSON, prompts) |
| Run directories | `runs/<run_id>/output/`, prompts, worktree scratch |
| Event log + `TelemetryRecorder` | Typed run/task/tool/validation events |

Optional FastAPI observability server (`product-factory observe serve`) exposes read-only REST and WebSocket/SSE over the same store (ADR-006). Optional OTLP export via `observability/otel.py`.

---

## 8. Evaluation architecture

The bench harness compares **subjects** on shared **cases**:

| Subject | Meaning |
| --- | --- |
| `full_orchestration` | Full `RunCoordinator` path |
| `single_agent_baseline` | One-shot model, no multi-agent graph |
| `implementation_isolation` / `agent_isolation` | Capability in isolation |
| Ablations | Review on/off, context modes, planner modes, validation/repair shape |
| `frontier_reference` | Strong model under oracle budget |

Scoring pipeline:

1. **Deterministic** validity (artifact non-empty, apply, expected files, smoke).
2. **Usability** requires deterministic pass (and behavioral when required).
3. **LLM judge** scores semantic dimensions only as a secondary signal when deterministic checks pass.
4. Aggregates: usable rates with Wilson/bootstrap CIs, cost per usable artifact, paired deltas, optional blind pairwise preference.

Resume identity: `(bench_id, case_id, subject_id, seed)`.

Promotion ladder and live results: see the performance plan doc.

---

## 9. Configuration surface

| File | Role |
| --- | --- |
| `config/models.yaml` | Model profiles (ids, pricing hints, roles) |
| `config/workflows.yaml` | Workflow types and baseline validators |
| `config/policies.yaml` | Dirty-repo policy, prohibited globs, registered commands |
| `config/benchmarks.yaml` | Ablation names, default subjects, judge defaults |

Skills under `skills/**` are versioned markdown + manifests selected by capability during context assembly.

---

## 10. Trust and failure model

| Boundary | Rule |
| --- | --- |
| Repository / tool output | Data only — cannot redefine role or grants |
| Planner | Cannot invent capabilities or tools |
| Implementation | Must inspect before write; finish with non-empty patch when live |
| Live fallbacks | Deterministic code generation is mock-only |
| Secrets | Paths like `.env` excluded from context; secret_scan on artifacts |
| Budgets | Run and task budgets stop work before unbounded spend |

Typed failures include: `empty_model_output`, `invalid_patch_format`, `patch_apply_failed`, `provider_failed`, `budget_exhausted`, `no_progress`, `composition_conflict`, plan rejection.

---

## 11. Extension points

When extending the system, prefer these hooks:

1. **New capability** — `domain/capabilities.py` + compiler allowlists + coordinator branch.
2. **New tool** — registry definition + broker dispatch + grant wiring.
3. **New validator** — `validation/pipeline.py` + workflow/policies + harness `deterministic.py`.
4. **New bench subject** — `evaluation/runners.py` + `subjects.py` + `bench.py` registration.
5. **New case** — YAML under `tests/eval_cases/` with `expected_files` / `smoke_commands` as needed.
6. **Provider** — new `ModelGateway` implementation; do not leak provider types into coordinator.

---

## 12. Related documents

| Doc | Audience |
| --- | --- |
| [codebase-structure.md](codebase-structure.md) | Navigation / ownership of packages |
| [benchmarking.md](benchmarking.md) | Bench CLI and scoring details |
| [observability.md](observability.md) | Observe API |
| [architecture/ADR-*.md](architecture/) | Individual decisions |
| [orchestration-performance-plan.md](orchestration-performance-plan.md) | Reliability gates and live results |
| [handover.md](handover.md) | Broader product/requirements history |
