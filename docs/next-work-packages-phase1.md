# Phase 1 — Execution kernel (stepped)

Implements [`handover_post_mvp.md`](handover_post_mvp.md) §4 Phase 1.
Plan: Cursor Phase 1 kernel plan.

**Status legend:** `[ ]` pending · `[~]` in progress · `[x]` done

## Locked defaults (do not reopen)

- `planner_mode=fixed`, review **optional**, `coding_worker` mid-tier
- No MCP, web search, local inference router, or dashboard in Phase 1
- Sandbox: restricted subprocess + optional Linux `bwrap`
- Resume: coordinator + SQLite + run dir (not LangGraph MemorySaver stub)

## Workstreams

| Step | Title | Status |
| --- | --- | --- |
| P1.A | Global budgets + profile cleanup | [x] |
| P1.B | Durable coordinator resume | [x] |
| P1.C | CLI validation commands | [x] |
| P1.D | Sandboxed validation commands | [x] |
| P1.E | Skill grant checks | [x] |
| P1.F | Controlled concurrency | [x] |
| P1.G | `repository_change` workflow pack | [x] |
| P1.H | Regression + docs gate | [x] |

## What changed (by workstream)

- **P1.A** — `orchestration/budget_ledger.py::BudgetLedger` is wired into the
  single choke points for spend: `gateway/instrumented.py` (before every
  model call), `tools/broker.py` (before every tool call and registered
  command), and `validation/pipeline.py` (before every behavioral command).
  `BudgetExhaustedError` carries which dimension tripped and a full ledger
  snapshot; the snapshot is persisted on the `runs` row after every
  task/wave so it survives a crash. CLI: `--budget-usd` (existing) and new
  `--max-wall-clock-seconds`. `model_profile_set` is deprecated via
  `warn_unused_profile_set()` (warns once, does not break callers); routing
  is unchanged (`scheduling/scheduler.py` + `models.yaml`).
- **P1.B** — `RunCoordinator.resume(run_id)` (see
  [`docs/architecture/sandbox-and-resume.md`](architecture/sandbox-and-resume.md)
  for the full design). CLI `product-factory resume` calls it directly; the
  old LangGraph checkpoint demo is preserved behind `--graph-demo` for
  comparison only.
- **P1.C** — `RunRequest.validation_commands` is the source of truth in
  `RunCoordinator._validate_outputs`; CLI exposes repeatable
  `--validation-command` and comma-separated `--validation-commands`, plus a
  `--policy` override for `policies.yaml` (registered commands). Bench
  runners (`evaluation/runners.py`) set `validation_commands` from
  `case.smoke_commands`. Unknown command ids are a typed, blocking
  `ValidatorResult` failure — never a host-shell fallback.
- **P1.D** — all registered-command execution (behavioral validation,
  deterministic smoke commands, tool-broker `run_command`) routes through
  `tools/sandbox.py::run_sandboxed_command`. Design note:
  [`docs/architecture/sandbox-and-resume.md`](architecture/sandbox-and-resume.md).
- **P1.E** — `orchestration/skill_grants.py` maps skill tool names to
  concrete broker tool names and enforces required/prohibited tools at grant
  time (`enforce_skill_grants`, called from `_execute_task` before
  `ToolBroker.set_grant`), failing closed with `SkillGrantViolation` on any
  inconsistency.
- **P1.F** — `orchestration/concurrency.py::run_wave` executes each ready
  wave: always-concurrent-eligible read-only capabilities
  (`repository_analysis`, `independent_review`, `security_review`,
  `requirements`, `test_execution`) plus writers with statically-predicted
  disjoint write patterns run in a `ThreadPoolExecutor` bounded by
  `max_parallel_tasks`; predicted-overlapping writers are serialized.
  Real (not just predicted) writer conflicts are still caught downstream by
  the existing lineage-inheritance check at composition time and surfaced as
  a typed `composition_conflict` `TaskResult` (this is a pre-existing
  mechanism; P1.F fixed a gap where that early-return path skipped
  persisting the task's terminal status to SQLite). Result merge order is
  always deterministic (plan/`ready` order), independent of thread
  completion order.
- **P1.G** — `src/product_factory/workflows/` (`WorkflowPack` protocol,
  `repository_change.py`, `registry.py`). `RunCoordinator.run()` resolves the
  pack for code-change workflows and adds `workflow_pack.manifest_metadata()`
  (id, version, content hash) to `RunManifest`. `code_change` aliases to
  `repository_change` for compatibility. Packs only reference registered
  handlers — no arbitrary planner-supplied Python.

## Exit criteria (handover)

- [x] Interrupt/restart resume without repeating completed billable calls —
      `tests/graph/test_resume.py::test_resume_skips_completed_tasks_and_retries_crashed_task`
      asserts identical `tool_calls` row counts for the completed task
      pre-/post-resume and that only the crashed task is re-dispatched.
- [x] Run cannot exceed any configured global limit (fault-injection tests) —
      `tests/unit/test_budget_ledger.py` (each dimension individually) and
      `tests/graph/test_budget_exhaustion.py` (mid-run tool-call and
      wall-clock exhaustion reach a typed terminal status).
- [x] Two independent tasks overlap in wall-clock —
      `tests/unit/test_concurrency.py` (direct `run_wave` timing) and
      `tests/graph/test_concurrency_graph.py::test_two_read_only_tasks_overlap_in_the_same_wave`
      (full coordinator run, real `ThreadPoolExecutor`, `tasks.ended_at` gap
      asserted well under the injected per-task delay).
- [x] Configured validation commands run on normal CLI path —
      `tests/graph/test_cli_contract.py` (`--validation-command`,
      `--validation-commands`, `--policy` override, unknown id fails closed
      with exit code 4).
- [x] Sandbox tests: no access to injected host secret —
      `tests/security/test_sandbox.py::test_secret_env_var_not_visible_to_sandboxed_command`
      (plus timeout-kill and bwrap network-deny, skipped when `bwrap` absent).
- [x] Code-change regression corpus at or above established floors — no
      regressions: full `tests/` suite green post-integration (see Evidence).

## Evidence

| Gate | ID / note |
| --- | --- |
| Unit/graph/security | `uv run pytest tests/unit tests/graph tests/security -q` → **158 passed, 1 skipped** (`bwrap` network-deny skipped on Darwin). |
| New test files | `tests/unit/test_budget_ledger.py`, `tests/security/test_sandbox.py`, `tests/unit/test_skill_grants.py`, `tests/unit/test_concurrency.py`, `tests/unit/test_workflow_packs.py`, `tests/graph/test_budget_exhaustion.py`, `tests/graph/test_resume.py`, `tests/graph/test_concurrency_graph.py`, `tests/graph/test_cli_contract.py` |
| Live smoke | OpenRouter `code_change` on `tests/fixtures/sample_api` + `validation_commands=[python_tests]`, budget $0.50 / 300s wall. **`run-a85570709052`** → `awaiting_approval` in 121s for **$0.016195**; pack metadata `repository_change@1.0.0` (`workflow_pack_hash` on manifest). First attempt hit a Darwin `/var` vs `/private/var` `list_files` path bug — fixed in `tools/broker.py` (resolve worktree root + relative paths) before success. |
