# SD2 → G2 evidence

**Branch:** `sd/sd2-kernel-decomposition`  
**Baseline:** [`../baseline/`](../baseline/)  
**Prior gate:** [`../sd1/`](../sd1/) (G1)

## Implemented

| Slice | Evidence |
| --- | --- |
| CompositionService | `src/product_factory/orchestration/composition/`; CompositionExecutor typed `composition=` |
| ValidationRepairService | `src/product_factory/orchestration/validation_repair/`; pack validators/roles drive pipelines |
| WaveScheduler + WorktreeLineageService | `scheduling/scheduler.py` (`WaveScheduler`); `orchestration/worktree_lineage.py` |
| RunFinalizer | `orchestration/finalization/run_finalizer.py`; pack output-role decisions |
| RunLifecycleEngine | `orchestration/lifecycle/engine.py` |
| RunCoordinator façade | `orchestration/coordinator.py` — delegates only |
| PackExecutionPolicy authority | Named `_CODE_CHANGE`/`_TECHNICAL_PLAN` frozensets removed; finalizer/validation use pack roles/validators |
| WorkflowType | Registry-validated `str` on `RunRequest` (`domain/runs.py`) |
| Fixture pack via host API | `tests/unit/test_sd2_kernel_decomposition.py::test_fixture_pack_submits_via_public_host_api` |
| Architecture guards | `tests/unit/test_rf4_pack_extensibility.py`, `test_sd2_kernel_decomposition.py` |

## Hermetic verification

```text
uv run pytest -q -m "not integration"
```

Results: see `pytest-not-integration.txt`.

## Integration / operational

- Integration: deferred (no live-model claim).
- Operational: not claimed for G2.

## G2 checklist mapping

- `RunCoordinator` is a lifecycle compatibility façade: source + architecture tests.
- Composition helpers / model loops live in CompositionService + executors + lifecycle engine.
- `PackExecutionPolicy` sole runtime declaration for validators/roles/repairs; deprecated `validation_policy` / `routing_defaults` are optional identity fields only.
- Fixture pack submits through `HostService.submit` without editing coordinator/scheduler/API unions/dashboard lists.
- Architecture tests forbid new `*_WORKFLOW_TYPES` constants in shared lifecycle modules.

## Placement note

```text
Concern: lifecycle | composition | validation/repair | scheduler/lineage | finalization | policy
Owning boundary: product_factory.orchestration.{lifecycle,composition,validation_repair,finalization,worktree_lineage}; product_factory.scheduling
Authoritative source: PackExecutionPolicy; workflow registry for WorkflowType
Compatibility: RunCoordinator façade + thin monkeypatch delegates; host/v1 unchanged; WorkflowType Literal → registry-validated str
Guardrail proof: tests/unit/test_sd2_kernel_decomposition.py; tests/unit/test_rf4_pack_extensibility.py; pytest -m "not integration"
Temporary exception: engine still holds wave-loop body pending further SD7 shrink; approval verify remains on broker grant until deployment executor owns ApprovalService (issue: remove-coordinator-approval-verify-2026-08); handoff resolve temporary on lifecycle engine (issue: remove-coordinator-handoff-resolve-2026-08)
```

## Deferrals

- Wave-loop body lives in `RunLifecycleEngine._execute` by design (not the façade); further shrink is SD7.
- Removing deprecated `validation_policy` / `routing_defaults` fields from pack identity hashes (kept optional for hash/shape stability; unused for decisions).
- Host/v2 and OpenAPI codegen (SD4).
- Persistence repository split / worker drain (SD3).
