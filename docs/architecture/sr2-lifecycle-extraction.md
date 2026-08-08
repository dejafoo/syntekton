# SR2 — Lifecycle extraction plan

**Status:** authoritative for SR2 implementation  
**Depends on:** G1 (SR1 complete)  
**Related:** [next-work-packages-sustainable-remediation.md](../next-work-packages-sustainable-remediation.md) §6

## Goal

`RunLifecycleEngine` sequences named owners; it does not construct brokers,
resolve detailed task policy, run model/tool loops, or compose outputs.
`RunCoordinator` remains a thin compatibility facade.

## Package layout

```text
src/product_factory/application/
  composition_root.py     # Database, registries, gateways, lifecycle services
  command_service.py      # submit/resume/cancel/revise/approve/reject/apply

src/product_factory/orchestration/
  lifecycle/engine.py     # sequencing only (shrink toward facade)
  task_preparation.py     # TaskPreparationService
  wave_execution.py       # WaveExecutionService
  composition/            # existing CompositionService
  ...
```

HostService and workers obtain the graph from `build_application(...)`.

## Public APIs

### `build_application(config, gateway, data_dir, ...) -> ApplicationServices`
Returns a typed namespace / dataclass holding:
- `db`, `tool_registry`, `connector_broker`, `skill_registry`
- `composition`, `validation_repair`, `wave_scheduler`, `worktree_lineage`, `finalizer`
- `task_preparation`, `wave_execution`, `commands`, `lifecycle`

### `TaskPreparationService.prepare(...) -> PreparedTask | BlockedPreparation`
Owns: capability/executor resolution, effective policy, dependency artifacts,
skills/stack/model profiles, safe repo context, route resolution,
immutable `TaskExecutionRequest` construction. Does not execute.

### `WaveExecutionService.run_wave_cycle(...) -> WaveCycleResult`
Owns: runnable selection (via WaveScheduler), concurrency admission, dispatch
via `execute_task`, result collection, task-state transitions, validation/repair
invocation hooks, heartbeat. Scheduler stays decision-only.

### `LifecycleCommandService`
Owns public mutations: submit, resume, cancel, revise, approve/reject, apply.
Delegates trust decisions to ApprovalService / handoff services without
reinterpreting authority.

### `CompositionInput` (typed, immutable)
Replaces callback-heavy ComposeContext fields over successive PRs:
`run_snapshot`, `compiled_plan`, `task_results`, `dependency_artifacts`,
`validation_evidence`, `findings`, `lineage`, `effective_policy`.
Handlers may keep a thin adapter during migration.

## Cut order (characterization tests first)

1. Characterization suite for fresh/resume/repair/partial/cancel/approval/budget/finalization/worker recovery (extend existing SD2 tests).
2. Extract `application/composition_root.py`; engine `__init__` receives injected deps.
3. Extract `TaskPreparationService` from `_execute_task` prep half.
4. Extract `WaveExecutionService` from `_execute` wave loop.
5. Introduce `CompositionInput` + reduce ComposeContext callbacks.
6. Extract `LifecycleCommandService`; HostService talks to commands/lifecycle.
7. Import-boundary AST tests: engine must not import concrete brokers, workflow handlers, capability executors, or ApprovalService implementations for new logic.

## Compatibility

- `RunCoordinator` continues to construct via composition root and expose
  private `_execute_task` / `_compose_*` delegates for monkeypatched tests.
- Temporary lifecycle delegates require issue id + removal target (SR7).

## Non-goals

- Transactional UoW (SR3)
- Host/v2 client migration (SR4)
- Line-count gate as success criterion
