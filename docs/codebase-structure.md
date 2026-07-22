# Product Factory — Codebase structure

Map of the repository for humans and agents: where code lives, what each package owns, and where to look first for common tasks.

For behavioral architecture (run lifecycle, security, evaluation), see [Architecture](architecture.md).

---

## 1. Top-level layout

```text
orchestration/
├── config/                 # YAML runtime & bench configuration
├── docs/                   # Architecture, ADRs, ops docs
├── scripts/                # bootstrap / verify helpers
├── skills/                 # Versioned skill packs (markdown + manifests)
├── src/product_factory/    # Installable Python package (authoritative code)
├── tests/                  # unit, graph, contract, integration, security, eval cases
├── pyproject.toml          # package metadata, ruff, pytest, basedpyright
├── README.md
└── .product-factory/       # local runtime data (gitignored)
```

Entry points:

| Entry | Location |
| --- | --- |
| CLI | `product-factory` → `src/product_factory/cli/app.py` |
| Library root | `src/product_factory/` |
| Observability HTTP | `src/product_factory/api/` via `product-factory observe serve` |

Python **3.13** only (`requires-python = ">=3.13,<3.14"`). Dependency management via **uv**.

---

## 2. Package map (`src/product_factory`)

```text
product_factory/
├── cli/              # Typer commands (run, plan, bench, observe, …)
├── api/              # FastAPI observability server
├── config/           # AppConfig loader (merges repo config/)
├── domain/           # Pydantic contracts (tasks, plans, findings, …)
├── gateway/          # ModelGateway + OpenRouter + Mock
├── planning/         # Planner prompts + plan compiler
├── orchestration/    # RunCoordinator, agent loop, repair, LangGraph skeleton
├── context/          # Prompt package assembly & excerpt selection
├── tools/            # Tool registry, broker, path policies
├── repositories/     # Worktrees, patches, snapshots
├── validation/       # Deterministic validators
├── scheduling/       # Ready-task selection & model profile pick
├── skills/           # SkillRegistry over skills/ tree
├── persistence/      # SQLite + artifact store
├── observability/    # Events, telemetry, redaction, query, OTEL
└── evaluation/       # Bench harness, judges, subjects, compare
```

### Dependency direction (intended)

```text
cli / api / evaluation
        │
        ▼
orchestration ──▶ planning, context, tools, repositories, validation, scheduling, skills
        │
        ▼
gateway, persistence, observability
        │
        ▼
domain, config
```

Prefer: **domain types have no I/O**; gateway/persistence are leaves; coordinator composes them. Avoid importing `evaluation` from runtime orchestration (bench calls coordinator, not the reverse).

---

## 3. Package responsibilities

### `domain/`

Typed contracts only (Pydantic):

| Module | Types |
| --- | --- |
| `tasks.py` | `TaskSpec`, `TaskResult`, `AcceptanceCriterion` |
| `plans.py` | `PlannerOutput`, `CompiledPlan`, compiler errors |
| `runs.py` | `RunRequest`, `RunManifest`, budgets |
| `capabilities.py` | Capability literals + tool-class allowlists |
| `findings.py` | `Finding`, `ValidatorResult` |
| `tools.py` | `ToolDefinition`, `CapabilityGrant` |
| `artifacts.py` | Artifact / resource refs |
| `errors.py` | Typed runtime errors |
| `usage.py` | Token/cost metrics |

**Start here** when changing public schemas or adding fields that must persist.

### `orchestration/`

| File | Role |
| --- | --- |
| `coordinator.py` | **Authoritative** plan/execute/validate/repair/compose loop |
| `agent_loop.py` | Bounded multi-turn tool agent for implementation/repair |
| `repair.py` | Repair task factory + patch fingerprint / no-progress helpers |
| `graph.py` / `state.py` | LangGraph skeleton + state typing (checkpointing shape) |
| `nodes/`, `subgraphs/` | Placeholders for graph decomposition |

Most feature work for “how a run behaves” lands in `coordinator.py` + `agent_loop.py`.

### `planning/`

| File | Role |
| --- | --- |
| `planner.py` | Build planner messages; call gateway; normalize planner JSON |
| `compiler.py` | Static validation, topo sort, AC ownership checks |

### `gateway/`

| File | Role |
| --- | --- |
| `base.py` | `ModelGateway` protocol |
| `canonical_messages.py` | Provider-neutral request/response/tool types |
| `openrouter.py` | Live adapter |
| `mock.py` | Offline adapter |
| `instrumented.py` | Telemetry wrapper |
| `pricing.py` | Cost estimation helpers |

### `tools/`

| File | Role |
| --- | --- |
| `registry.py` | Built-in tool catalogue |
| `broker.py` | Sole executor (grants, dispatch, audit) |
| `policies.py` | Path allowlisting / resolve-under-root |

### `repositories/`

| File | Role |
| --- | --- |
| `worktrees.py` | Create/manage per-task Git worktrees |
| `patches.py` | Create/apply/check diffs; conflict helpers |
| `snapshot.py` | Capture base commit / repo summary |

### `context/`

| File | Role |
| --- | --- |
| `assembler.py` | Prompt layers, manifests, excerpt vs file-list modes |

### `validation/`

| File | Role |
| --- | --- |
| `pipeline.py` | Patch apply, path scope, secrets, architecture, behavioral commands |

### `persistence/`

| File | Role |
| --- | --- |
| `database.py` | SQLite schema and queries |
| `artifacts.py` | Content-addressed blob store |

### `observability/`

Events, redaction, recorder, query helpers, stuck detection, optional OTEL bridge. Consumed by CLI runs and the FastAPI observe API.

### `evaluation/`

Bench-only code:

| File / dir | Role |
| --- | --- |
| `bench.py` | `BenchmarkRunner` — schedule cells, resume, pairwise |
| `runners.py` | Subject implementations (orch, baseline, ablations) |
| `cases.py` / `loader.py` | Eval case schema and YAML loading |
| `deterministic.py` | Harness-side validators and metric flags |
| `judge.py` | Mock + LLM absolute/pairwise judges |
| `compare.py` | Aggregates, CIs, report Markdown/JSON |
| `store.py` | Bench score / pairwise persistence |
| `subjects.py` | Subject id/config types |
| `adapters/` | Future public-suite loaders |

### `cli/` and `api/`

- **CLI:** user-facing commands (`run`, `plan`, `bench run`, `observe serve`, …).
- **API:** read-only observability HTTP/SSE; does not replace CLI for execution.

### `config/` (package) vs repo `config/`

- Repo root `config/*.yaml` — checked-in defaults.
- `src/product_factory/config/loader.py` — loads and validates into `AppConfig`.

### `skills/` (repo root)

Skill packs keyed by domain (`coding/`, `architecture/`, `quality/`, `security/`). Each skill has `SKILL.md` + `manifest.yaml`. Selected at runtime by capability/required_skills.

---

## 4. Configuration files (`config/`)

| File | Edit when you need to… |
| --- | --- |
| `models.yaml` | Change model profiles, providers, pricing metadata |
| `workflows.yaml` | Add workflow types / baseline validator lists |
| `policies.yaml` | Register smoke commands, path prohibitions |
| `benchmarks.yaml` | Name ablations, default subjects, judge defaults |

---

## 5. Tests layout

```text
tests/
├── unit/           # Fast, isolated (agent loop, compiler, domain, …)
├── graph/          # Cross-package coordinator/broker flows (often git + tmp)
├── contract/       # Schema / API / gateway contracts
├── security/       # Tool authorization / path escape
├── integration/    # Opt-in live OpenRouter (PRODUCT_FACTORY_LIVE=1)
├── eval_cases/     # YAML cases for bench (not collected as pytest)
├── fixtures/       # sample_api Git fixture repo contents
└── conftest.py
```

Notes:

- `eval_cases/` and `fixtures/` are ignored by default pytest collection (`pyproject.toml`).
- Graph tests often clone `tests/fixtures/sample_api`.
- Prefer `PYTHONPATH=src` or `uv run` so imports resolve to the package.

---

## 6. Docs layout

| Path | Content |
| --- | --- |
| `docs/architecture.md` | This project’s system architecture (narrative) |
| `docs/codebase-structure.md` | This file |
| `docs/architecture/ADR-*.md` | Short accepted decisions |
| `docs/benchmarking.md` | Bench CLI how-to |
| `docs/observability.md` | Observe API |
| `docs/orchestration-performance-plan.md` | Living performance tracker / gates |
| `docs/implementation-plan.md` / `handover.md` | Historical planning / product notes |

---

## 7. Runtime data (`.product-factory/`)

Created locally; **not** source:

```text
.product-factory/
├── data/product_factory.sqlite
├── content/ …              # blob store
├── runs/<run_id>/          # outputs, prompts, scratch worktrees
├── bench-reports/          # bench-*.json / .md exports
└── lessons/candidates/     # human-gated lesson drafts
```

---

## 8. “Where do I change X?”

| Goal | Primary places |
| --- | --- |
| Change how live runs plan/execute | `orchestration/coordinator.py` |
| Change tool loop bounds / no-progress | `orchestration/agent_loop.py`, `orchestration/repair.py` |
| Add/adjust tools | `tools/registry.py`, `tools/broker.py` |
| Change validators | `validation/pipeline.py`, `evaluation/deterministic.py`, `config/policies.yaml` |
| Change prompts / context | `context/assembler.py`, planner system text in `planning/planner.py` |
| Change model routing | `config/models.yaml`, `scheduling/scheduler.py` |
| Add bench case | `tests/eval_cases/<id>.yaml` |
| Add bench subject / ablation | `evaluation/runners.py`, `evaluation/bench.py`, `evaluation/subjects.py`, `config/benchmarks.yaml` |
| Change scoring / reports | `evaluation/compare.py`, `evaluation/judge.py` |
| Persist new event types | `observability/contracts.py`, `observability/recorder.py`, `persistence/database.py` |
| CLI surface | `cli/app.py` |

---

## 9. Conventions for agents

1. **Match existing modules** — do not invent parallel orchestrators; extend `RunCoordinator`.
2. **Keep provider details in `gateway/`** — coordinator talks only to `ModelGateway` + canonical messages.
3. **Fail closed in live mode** — no silent deterministic implementation fallback.
4. **Prefer small typed changes** in `domain/` when contracts shift; update compiler/tests together.
5. **Bench vs runtime** — harness validators in `evaluation/deterministic.py` should stay semantically aligned with runtime `validation/pipeline.py`.
6. **Do not commit** `.product-factory/` or API keys; use env `OPENROUTER_API_KEY` for live work.
7. **Verify** with `./scripts/verify.sh` or `uv run pytest` + `ruff` + `basedpyright src`.

---

## 10. Quick mental model

```text
CLI/bench
   → RunCoordinator (plan → tasks → tools → validate → repair → compose)
        → ModelGateway (think)
        → ToolBroker (act in worktree)
        → ArtifactStore + SQLite (remember)
   → BenchmarkRunner (compare subjects; deterministic then judge)
```

If you only remember three files: **`coordinator.py`**, **`broker.py`**, **`bench.py`**.
