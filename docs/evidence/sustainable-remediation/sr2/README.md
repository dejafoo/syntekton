# SR2 — Lifecycle decomposition (G2 ownership cuts; not closed)

**Package:** SR2  
**Findings:** RF-03 (partial)  
**Evidence level:** hermetically verified (focused unit + characterization)

## Placement note

```text
Concern: lifecycle
Owning boundary: TaskPreparationService, TaskRuntimeService, WaveExecutionService, LifecycleCommandService, CompositionInput
Authoritative source: characterization + AST forbidden-dependency guards
Compatibility: RunCoordinator remains facade; ComposeContext still carries callbacks with attached CompositionInput
Guardrail proof: tests/unit/test_sr2_lifecycle_extraction.py
Temporary exception: engine still owns prep-context / validation-repair / finalization sequencing (see tracker G2 remaining)
```

## Landed

- `TaskRuntimeService` owns ToolBroker construction, grants, and `execute_task` dispatch
- `WaveExecutionService.run_wave_cycle` owns runnable selection through result recording
- Host mutations via `LifecycleCommandService`
- Production compose attaches immutable `CompositionInput`
- Engine AST guards forbid `ToolBroker(` and concrete executor imports

## Deferred for G2 close

- Move remaining engine-owned prep context, worktree, validation/repair, and finalization out of the engine body
- Expand characterization: resume, repair lineage, approval wait, budget exhaustion, worker recovery

## Commands

```text
uv run pytest -q tests/unit/test_sr2_lifecycle_extraction.py
```
