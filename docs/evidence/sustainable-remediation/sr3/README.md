# SR3 — Transactional durability (first slice)

**Package:** SR3  
**Findings:** RF-04 (partial)  
**Evidence level:** hermetically verified (focused unit tests)

## Placement note

```text
Concern: persistence
Owning boundary: product_factory.persistence.unit_of_work + WaveExecutionService.record_task_completion + HostService.submit
Authoritative source: SqliteActor immediate()/SAVEPOINT transaction; aggregate repositories
Compatibility: Database façade preserved; autonomous repository commits unchanged outside UoW
Guardrail proof: tests/unit/test_sr3_unit_of_work.py
Temporary exception: none
```

## Implemented (this pass)

| Slice | Change |
| --- | --- |
| SR3.A (partial) | `UnitOfWork` over `SqliteActor.immediate()` with nested SAVEPOINT support |
| SR3.A | Repositories defer commit via `AggregateRepository._commit()` / `commit_if_autonomous()` while a UoW owns the transaction |
| SR3.A | Atomic helpers: `admit_run_with_events`, `complete_task_with_event`; `Database.unit_of_work()` / `begin()` |
| SR3.B (wired) | Production path: `WaveExecutionService.record_task_completion` uses `unit_of_work().complete_task_with_event`; engine terminal upserts call it; post-wave `task.completed`/`task.failed` recorder emit removed (budget telemetry remains after commit) |
| SR3.B (wired) | Host queue admission uses `unit_of_work().admit_run_with_events` with a coupled `run.admitted` event |
| FK | Reconfirmed `PRAGMA foreign_keys=ON` on connect and before every transaction |
| SR3.D (partial) | Fault-injection tests prove coupled writes roll back together |

## Deferred

- Remaining SR3.A atomic helpers (task start, repair+lineage, finalization+output roles, approval consumption, handoff consumption)
- SR3.C handoff transition append-only table
- Full SR3.D fault matrix (planning, model, tool, validation, budget, handoff, deployment)
- SR3.E scheduled worker interrupt/restart recovery
- G3 durability acceptance checklist

## Commands

```text
uv run pytest -q tests/unit/test_sr3_unit_of_work.py
uv run pytest -q tests/unit/test_sd3_repositories.py tests/unit/test_sd0_migrations.py
```
