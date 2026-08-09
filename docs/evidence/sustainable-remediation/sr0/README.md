# SR0 — Correctness and truthful baseline

**Package:** SR0  
**Findings:** RF-01, RF-08 (baseline honesty)  
**Evidence level:** hermetically verified (unit/graph focused); full hermetic suite archived in `hermetic-gate.txt`

## Placement note

```text
Concern: lifecycle | executor
Owning boundary: domain/tasks predicates; executors/validation.TestExecutionExecutor; scheduling/scheduler
Authoritative source: shared task-result predicates + validation receipts
Compatibility: none (behavior correction; TaskResult.status gains skipped)
Guardrail proof: tests/unit/test_sr0_task_result_semantics.py
Temporary exception: none
```

## Implemented

| Slice | Change |
| --- | --- |
| SR0.A | Shared predicates `is_terminal_task_status`, `satisfies_dependency`, `requires_repair_or_terminal_resolution` in `domain/tasks.py`; scheduler/lifecycle/projections use them |
| SR0.B | `TestExecutionExecutor` treats complete failing receipts as task `success` with validator/finding evidence; incomplete/unavailable → `blocked`/`failed` |
| SR0.C | Ruff format baseline cleaned; tracker/evidence honesty for this package |

## Pre-change characterization

- `partial` does not satisfy dependencies (`test_scheduler_partial_blocks_dependents_characterization`)
- Lifecycle previously reported “Unsatisfiable dependencies” when quality `T-002` was `partial`

## Commands

```text
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright
uv run pytest -q tests/unit/test_sr0_task_result_semantics.py
uv run pytest -q tests/graph/test_quality_gate_pack.py
uv run pytest -q -m "not integration"
```

## Nested uv sandbox cache

Sandboxed validation commands set `UV_CACHE_DIR` to a workspace-local
`.uv-cache` (or an explicit allowlisted value) so nested `uv run` inside
restricted/bwrap sandboxes can write cache without depending on `~/.cache`.
On bwrap, the cache directory is bound RW when it lies outside the worktree.
Guard: `tests/unit/test_sandbox.py`.

## Known limitations

- Full browser/AMD/operational proof remains deferred to SR5/SR6.
- RF-08 release soft-skips are tracked for SR5.D, not claimed complete here.
