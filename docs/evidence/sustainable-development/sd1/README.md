# SD1 → G1 evidence

**Branch:** `sd/sd1-executor-truth`  
**Baseline:** [`../baseline/`](../baseline/)  
**Prior gate:** [`../sd0/`](../sd0/) (G0)

## Implemented

| Slice | Evidence |
| --- | --- |
| SD1.A Descriptors | `src/product_factory/registry/capability_descriptors.py`; profiles in `context/assembler.py`; `tests/unit/test_sd1_capability_descriptors.py` |
| SD1.B Protocol/registry | `src/product_factory/executors/protocol.py`, `registry.py`; TaskResult receipts in `domain/tasks.py` |
| SD1.C Migration | Executors under `src/product_factory/executors/`; coordinator dispatches via `execute_task` |
| SD1.D Missing behavior | `model_draft.py` (release/ops/security/docs/test_design), `validation.py` (test_execution) |
| SD1.E Placeholders | No `completed (stub)` in runtime; completeness + fake-live in `tests/unit/test_sd1_executor_completeness.py` |

## Hermetic verification

```text
uv run ruff check src/product_factory/registry src/product_factory/executors \
  src/product_factory/domain/capabilities.py src/product_factory/domain/tasks.py \
  src/product_factory/workflows/base.py src/product_factory/scheduling/scheduler.py \
  tests/unit/test_sd1_*.py
uv run pytest -q tests/unit/test_sd1_capability_descriptors.py \
  tests/unit/test_sd1_executor_completeness.py \
  tests/graph/test_quality_gate_pack.py tests/graph/test_release_readiness_pack.py \
  tests/graph/test_incident_triage_pack.py tests/graph/test_service_health_review_pack.py
uv run pytest -q -m "not integration"
```

Results: see `pytest-not-integration.txt` when archived after the gate run.

## Integration / operational

- Integration: deferred (no live-model claim).
- Operational: PM5 release/ops packs are **hermetically implemented** with connector `mock=True` under MockGateway. Live CI/ops/model evidence is not claimed.

## G1 checklist mapping

- Every capability has executor/adapter/profile/parser/schema/budget/eval category: descriptor catalog + completeness tests.
- Unknown capability/mode/adapter fails before execution: registry + pack validation.
- PM5 outputs trace to task work and persisted receipts: fake-live release/quality tests.
- No workflow ready solely from caller evidence-shaped fields: release composition requires analysis receipts; blocked without CI connector.
- Required suites: unit/graph SD1 + full hermetic pytest.

## Placement note

```text
Concern: executor | policy
Owning boundary: product_factory.registry + product_factory.executors
Authoritative source: CapabilityDescriptor catalog; persisted EffectiveTaskPolicy.executor_mode
Compatibility: coordinator still supplies composition callbacks (issue: remove-coordinator-compose-callbacks-2026-08); approval verify remains on broker grant (issue: remove-coordinator-approval-verify-2026-08)
Guardrail proof: tests/unit/test_sd1_*.py; tests/graph/test_release_readiness_pack.py; pytest -m "not integration"
Temporary exception: composition/approval callbacks on RunCoordinator until SD2 extraction
```
