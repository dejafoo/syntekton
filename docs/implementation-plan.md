# Product Factory MVP — Implementation Plan & Tasks

Living task tracker for the MVP. Authoritative architecture: [handover.md](handover.md).

## Locked decisions

- Python **3.13** via `uv` + `.venv`
- LangGraph + Pydantic v2 + Typer/Rich + SQLite + Git worktrees
- OpenRouter gateway with mock local adapter
- First vertical slice: **code_change** (health-check endpoint)
- Tracking: this markdown file (Cursor-only; no Notion sync)

## Status legend

- [ ] pending
- [x] done

---

## WP0 — Scaffold and engineering baseline

- [x] `pyproject.toml` (Python >=3.13,<3.14), uv, Ruff, basedpyright, pytest
- [x] Package layout under `src/product_factory/`
- [x] CLI skeleton with required commands
- [x] Config loader + default YAML
- [x] `scripts/bootstrap.sh`, `scripts/verify.sh`, CI workflow
- [x] This implementation plan document
- [x] ADRs 001–004

## WP1 — Domain contracts

- [x] Pydantic models (runs, budgets, tasks, plans, findings, artifacts, tools, usage)
- [x] Error hierarchy with CLI exit codes
- [x] Decimal budget arithmetic
- [x] JSON Schema export (`export_json_schemas`)

## WP2 — Model gateway

- [x] Canonical request/response models
- [x] OpenRouter HTTP adapter (`httpx`)
- [x] Mock adapter for offline/portability
- [x] Retries, cost ceiling, pricing helpers
- [x] Contract tests + opt-in live integration test

## WP3 — Artifacts and persistence

- [x] SQLite schema + `Database` helper
- [x] Content-addressed artifact store
- [x] JSONL event log
- [x] Run/task/invocation/tool_call recording

## WP4 — Repo isolation and tool broker

- [x] Repository snapshot (dirty-repo policy)
- [x] Git worktree manager
- [x] Tool registry + capability grants
- [x] Read/write/command tools via broker
- [x] Path/symlink security tests

## WP5 — Context and skills

- [x] 8-layer prompt assembly + manifests
- [x] Filesystem skill registry + matching
- [x] Agent profile prompts
- [x] Core execution contract (untrusted tool/repo text)

## WP6 — Planning and compilation

- [x] Planner gateway helper + deterministic fallback plans
- [x] Deterministic plan compiler (DAG, capabilities, validators, composer)
- [x] One plan-repair attempt in coordinator

## WP7 — Execution graph / vertical slice

- [x] LangGraph skeleton with checkpointing
- [x] `RunCoordinator` end-to-end execution
- [x] Scheduler + model selector
- [x] Concurrent wave selection (max_parallel)
- [x] Health-check vertical slice on `tests/fixtures/sample_api`

## WP8 — Validation, review, repair, approval

- [x] Deterministic validation pipeline (patch, paths, secrets, architecture sections)
- [x] Independent review task + findings artifacts
- [x] Targeted repair task creation + no-progress detection
- [x] Approval interrupt artifacts + `approve` / `reject` / `apply` CLI

## WP9 — Architecture workflow + CLI completeness

- [x] Architecture workflow path → `ARCHITECTURE.md`
- [x] CLI: init, doctor, models, plan, run, status, inspect, resume, approve, reject, apply, eval, costs

## WP10 — Evaluation harness

- [x] Eval case schema + 30 YAML cases
- [x] Baseline config names (A–E)
- [x] Runner with Markdown/JSON reports + quality_efficiency
- [x] `product-factory eval` command

## WP11 — Hardening / MVP release checklist

- [x] Security tests for broker
- [x] Graph/unit/contract tests
- [x] Portability: `mock_local` profile + MockGateway
- [x] Documentation (README, ADRs, this plan)
- [x] Known limitations documented below

## WP12 — LLM-judge evaluation harness

- [x] Enriched `EvalCase` / `SubjectArtifact` / `JudgeVerdict` / `LessonCandidate` contracts
- [x] `MockJudge` + deterministic merge + SQLite/JSON reports
- [x] Subject runners: orchestration, single-agent, isolation, frontier
- [x] `product-factory bench` CLI (run / compare / lessons)
- [x] Human-gated lesson export; ADR-005; CaseLoader stub for public suites
- [x] Docs: `docs/benchmarking.md`

---

## Known limitations (MVP)

1. Live implementation/repair uses a bounded multi-turn tool agent
   (`orchestration/agent_loop.py`). Offline/mock mode still uses deterministic
   capability handlers for reliability.
2. LangGraph graph is a checkpointed control skeleton; primary production path is `RunCoordinator`.
3. Validation command execution depends on registered commands in the worktree environment.
4. Frontier oracle (Claude Fable 5) is configured but disabled for normal runs.
5. Concurrent workers are scheduled as waves; in-process execution is sequential within a wave for determinism in tests.
6. Human scoring import UI is out of scope; reports are JSON/Markdown files.
7. LLM-judge bench CI uses `MockJudge`; live frontier judging is opt-in (`--live` / `PRODUCT_FACTORY_BENCH_LIVE=1`).
8. Lesson candidates require human accept/promote — no automatic skill/prompt injection (ADR-007).

## How to update this file

When starting or finishing a work package, toggle the checkboxes and adjust known limitations. Do not silently change normative requirements from `handover.md` without an ADR.
