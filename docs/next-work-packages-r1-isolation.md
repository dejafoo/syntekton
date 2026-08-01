# R1 — Run and task execution isolation (implementation plan)

**Status:** ready to implement  
**Gate authority:** [handover_post_mvp_refactoring.md](handover_post_mvp_refactoring.md) §4 R1, §7  
**Depends on:** PM4 complete (gateway router + worker leases exist; they do **not** close R1)  
**Blocks:** R2 durable policy binding, safe concurrent remote workers, PM5

This plan turns the R1 package brief into a file/test playbook. Do not weaken
run isolation by serializing the whole service.

---

## 1. Problem (verified hotspots)

| Hotspot | File | Behavior today |
| --- | --- | --- |
| Shared gateway rebind | [`coordinator.py`](../src/product_factory/orchestration/coordinator.py) `run` (~520) and `resume` (~878) | `self.gateway = InstrumentedModelGateway(...)` mutates the long-lived coordinator |
| Shared connector audit | same file ~2368; [`connectors/broker.py`](../src/product_factory/connectors/broker.py) `set_audit` | Task execution replaces `self.connector_broker.audit` for the duration of the task |
| Single coordinator | [`host/service.py`](../src/product_factory/host/service.py) ~96–117, submit/workers | One `RunCoordinator` serves concurrent background workers |
| Intra-run waves | [`concurrency.py`](../src/product_factory/orchestration/concurrency.py) via `run_wave` (~1463) | Parallel tasks share the rebound gateway and broker audit field |
| Worker recovery | [`workers/supervisor.py`](../src/product_factory/workers/supervisor.py) → `coord.resume` | Resume rebuilds recorder/ledger locally but still assigns `self.gateway` |

Worker leases enforce **one writer per worktree**. They do not isolate
gateway/recorder/ledger/audit attribution across independent runs.

---

## 2. Target shape: `RunExecutionContext`

Introduce a thin, immutable (frozen dataclass or pydantic model) object in a new
module, e.g. `src/product_factory/orchestration/execution_context.py`.

```text
RunExecutionContext
  run_id: str
  workflow_type: str          # canonical pack id after host normalization
  pack_id / pack_version: str | None
  workspace_key: str | None   # same key supervisor uses for leases
  gateway: InstrumentedModelGateway   # run-scoped; never stored on coordinator
  recorder: TelemetryRecorder
  ledger: BudgetLedger
  artifacts: ArtifactStore
  run_dir: Path
  cancel_check: Callable[[], None]    # wraps _raise_if_cancelled(run_id)
  route_policy_ref: str | None        # opaque for R1; filled by R2/R5
```

**Construction**

- `build_run_execution_context(...)` used by both `run` and `resume`.
- Resume restores ledger from `budget_json` and rebuilds gateway/recorder from
  durable run metadata + `_raw_gateway` (shared **immutable** adapter only).
- Pass `ctx` explicitly into `_execute_plan`, wave scheduling, and task
  execution. Stop reading `self.gateway` inside those paths.

**Coordinator retained fields (shared, immutable config only)**

- `_raw_gateway`, `config`, `db`, `tool_registry`, `connector_broker` (without
  mutable per-run audit), `pf_root`, planner flags.

**Must remove / stop using as primary path**

- Assigning `self.gateway` in `run`/`resume` for execution (temporary compat
  shim allowed for ≤1 PR if tests still poke `coord.gateway`; delete in the
  same package exit).
- `ConnectorBroker.set_audit` as the attribution mechanism.

---

## 3. Connector / tool broker isolation

Preferred approach (minimal API churn):

1. Add optional `audit: ConnectorAudit | None` parameter to
   `ConnectorBroker.invoke` / the call path used by `ToolBroker`.
2. Prefer the per-call audit over `self.audit` when provided.
3. Stop calling `set_audit` from the coordinator task path.
4. Deprecate `set_audit` (warn in logs if used); remove once call sites are gone.

Alternative acceptable if invoke signature is awkward: a short-lived
`TaskScopedConnectorBroker` facade that holds the audit and delegates to the
shared registry/config/semaphores **without** mutating the shared broker.

`ToolBroker` already accepts `observer`, `ledger`, and `run_id` — keep creating
it per task with run-scoped artifacts/ledger from `ctx`.

---

## 4. Implementation steps (single PR stack preferred)

| Step | Change | Done when |
| --- | --- | --- |
| R1.1 | Add `RunExecutionContext` + builder; unit-test construction/restore | Importable; resume builder restores ledger snapshot |
| R1.2 | Thread `ctx` through `run` / `resume` / `_execute_plan` / task executor; stop execution-path use of `self.gateway` | Grep shows no task/model path reading `self.gateway` except shim |
| R1.3 | Per-call connector audit; remove `set_audit` from task path | Concurrent connector test green |
| R1.4 | Audit long-lived fields on `RunCoordinator` / `HostService` | Checklist in PR: no run_id/task_id/recorder on service-wide mutable fields |
| R1.5 | Race tests below; delete gateway shim if any | Exit criteria in handover §4 R1 |

Do **not** start R2 schema work in this package beyond leaving a
`route_policy_ref: None` slot.

---

## 5. Tests (must fail before the fix)

Add under `tests/unit/test_r1_run_isolation.py` and one graph/integration file.

| Test ID | Claim |
| --- | --- |
| `test_concurrent_runs_isolate_model_invocation_run_ids` | Two runs with barriered fake gateways; every `model_invocations` row and cost/ledger entry matches its run |
| `test_concurrent_connector_calls_isolate_audit_run_and_task_ids` | Interleaved connector invokes; events/receipts carry correct run_id + task_id |
| `test_resume_run_a_during_active_run_b_preserves_attribution` | Resume A while B is mid-invocation; no crossed recorder/ledger rows |
| `test_budget_exhaustion_does_not_spend_sibling_run` | Run A hits ceiling; run B still completes under its own ledger |
| `test_same_worktree_lease_still_rejects_second_writer` | Existing lease rejection still passes (regression) |
| `test_independent_worktree_runs_may_proceed_concurrently` | Two worktree keys both execute (leases allow it) |

**Quality rules**

- Use barriers / controllable fakes to force interleaving; sequential mocks do
  not prove isolation.
- Repeat the concurrent tests under `pytest-repeat` or a small loop (≥5) in CI
  for the race-focused pair.
- Prefer in-process `HostService` or direct coordinator calls with threads;
  Docker restart coverage remains PM4’s lease test, not a substitute for R1.

---

## 6. Out of scope for R1

- `EffectiveTaskPolicy`, grant-before-assemble, stack-profile artifacts (R2)
- ArtifactInstance / capture matrix (R3)
- Removing workflow-name branches (R4)
- Real AMD endpoint / circuit breaker (R5)
- Dashboard projection redesign (R6)

---

## 7. Exit checklist (maps to handover §4 R1 / §7)

- [ ] Race tests fail on pre-refactor main and pass reliably after
- [ ] Code review finds no mutable run/task gateway, recorder, ledger, or
      broker audit on the service-wide coordinator/broker singleton
- [ ] Resume rebuilds context from durable metadata
- [ ] Same-worktree lease rejection unchanged; independent worktrees concurrent
- [ ] PR states which §7 checkboxes this enables (isolation + resume) without
      claiming the full pre-PM5 gate closed

**Definition of done for the implementing agent:** handover §9 items 1–6.
