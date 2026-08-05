# R4 — Pack extensibility vs technical-spike completion (split plan)

**Status:** complete
**Gate authority:** [handover_post_mvp_refactoring.md](handover_post_mvp_refactoring.md) §4 R4  
**Depends on:** R1 complete; R2 `EffectiveTaskPolicy` available before **migrating** packs  
**Related ADR:** [ADR-007](architecture/ADR-007-effective-policy-and-artifact-instances.md)

The handover mixes two different kinds of work under R4. Implement them as
**separate workstreams** so coordinator cleanup is not blocked on interface
product depth, and spike product work is not mistaken for “pack extensibility
done.”

```text
R4.INV  branch inventory (read-only design artifact)
R4.EXT  generic pack dispatch + PackExecutionPolicy migration
R4.SPIKE  technical_spike / interface_analysis becomes real evidence

R4.INV ──► may start after R1 (parallel with R2)
R4.EXT ──► migrate only after EffectiveTaskPolicy (R2)
R4.SPIKE ──► may proceed on current spike path; must finish before PM5 §7
             but is not a prerequisite to start R4.EXT inventory/migration
```

---

## Stream A — `R4.INV` Branch inventory

**Goal:** classify every workflow-name / alias branch before deleting them.

### Scope (search targets)

| Area | Primary paths |
| --- | --- |
| Coordinator | [`orchestration/coordinator.py`](../src/product_factory/orchestration/coordinator.py) `_WORKFLOW_TYPES`, compose/grant/validation branches |
| Planner / defaults | [`workflows/default_plans.py`](../src/product_factory/workflows/default_plans.py), compiler |
| Handlers | [`workflows/handlers/`](../src/product_factory/workflows/handlers/) |
| Host presentation | [`host/service.py`](../src/product_factory/host/service.py), host MCP |
| Observability / CLI | query projections, land maps, eligible next actions |

### Deliverable

Add `docs/architecture/r4-workflow-branch-inventory.md` with a table:

| Location (file:symbol) | Branch key | Class | Disposition |
| --- | --- | --- | --- |
| … | `workflow_type in _SPIKE_…` | pack policy / capability executor / generic lifecycle / compat adapter | move to PackExecutionPolicy / keep / delete |

**Classes (normative)**

- **generic lifecycle** — stays in coordinator (waves, budgets, resume)
- **pack policy** — moves to `PackExecutionPolicy` / registered handler
- **capability executor** — maps to executor-mode catalogue
- **compat adapter** — host/request normalization only; not on new runtime path

**Exit:** inventory merged; no code migration required in this stream.

---

## Stream B — `R4.EXT` Generic pack execution

**Goal:** new packs register policy; coordinator gains no new workflow-name
branches.

### `PackExecutionPolicy` (minimum)

Declared on the pack (typed data + optional registered handler), not
user-supplied code:

- input/handoff roles and schema compatibility
- task templates, dependencies, eligible capabilities, **executor_mode**
- grant narrowing / connector eligibility (consumed via EffectiveTaskPolicy)
- artifact composition, validators, findings, repair, approval
- output roles, landing eligibility, evaluation fixture id

### Executor-mode catalogue (fixed)

`deterministic` | `model_draft` | `repository_agent_loop` |
`research_agent_loop` | `interface_agent_loop` | `validation` | `composition`

Unknown mode fails closed at pack compile/load time.

### Required changes

1. Generic dispatch entry point selects handler/executor from registry + mode.
2. Tool-loop availability from EffectiveTaskPolicy only (no silent research
   fallback for interface analysis).
3. Aliases only at host/request normalization; durable manifests use canonical
   pack id/version.
4. Architecture/regression test: new `workflow_type in …` in coordinator is
   limited to an allowlist of legacy normalization sites (or banned).

### Tests

- Table-driven: every canonical pack through the same dispatch entry
- Allowlist/architecture test against new coordinator workflow branches
- Pack-policy compilation rejects unknown executor mode / capability / tool
  class / validator / role / incompatible handoff

### Exit

Adding a small **read-only** pack needs registration + schemas + policy +
fixtures + host metadata — **without** a new `RunCoordinator` workflow-name
branch.

**Does not require** R4.SPIKE to be finished, but PM5 §7 still requires both.

---

## Stream C — `R4.SPIKE` Technical spike becomes real

**Goal:** product completion of interface analysis — not a refactor metric.

### Required outcomes

For an interface input, the spike path must produce and cite:

1. durable typed **contract inventory**
2. **comparison / compatibility** artifacts
3. synthetic **fixture or simulation** evidence when requested
4. a `SpikeResult` that **references** those artifacts (hashes/roles)

A polished generic prose report alone is **not** sufficient.

### Current baseline

- Pack: [`workflows/technical_spike.py`](../src/product_factory/workflows/technical_spike.py)
- Handler: [`workflows/handlers/technical_spike.py`](../src/product_factory/workflows/handlers/technical_spike.py)
- Capability: `interface_analysis` with intended contract tools — today may
  still fall through generic research tooling in the coordinator loop

### Tests

- End-to-end fixture: expected interface tools called; receipts present;
  final measurements cite typed artifacts
- Fail closed if spike completes without required artifact roles

### Exit

Handover §7 checkbox: “Interface analysis/technical spike performs and cites
its declared tools and typed evidence artifacts.”

Track this as its own checklist item in
[next-work-packages-post-mvp.md](next-work-packages-post-mvp.md) under the
pre-PM5 gate (`RF4.SPIKE`), separate from `RF4.EXT`.

---

## Sequencing rules

| May start | Must wait |
| --- | --- |
| R4.INV after R1 | — |
| R4.SPIKE anytime after PM3 spike pack exists | Prefer EffectiveTaskPolicy before tightening tool loops |
| R4.EXT migration | R2 EffectiveTaskPolicy + R4.INV complete |

Do **not** mark R4 complete in the tracker until **both** R4.EXT exit and
R4.SPIKE exit are demonstrated. Inventory alone is not completion.

## Completion evidence

- **R4.INV:** [`architecture/r4-workflow-branch-inventory.md`](architecture/r4-workflow-branch-inventory.md)
  classifies coordinator, planner, handler, host, and read-plane branches.
- **R4.EXT:** `PackExecutionPolicy` declares executor modes, grant narrowing,
  handoffs, validators, output roles, repair/approval behavior, and evaluation
  fixture identity. Registry validation fails closed; coordinator dispatch uses
  registered packs and the effective policy. Live planner capabilities are
  pack-scoped, host MCP exposes the canonical spike pack, and a read-only
  example pack registers without a coordinator workflow branch.
- **R4.SPIKE:** `interface_agent_loop` executes contract inventory,
  compatibility (when two contracts are supplied), synthetic fixture, and
  simulation tools. Typed evidence artifacts are referenced by
  `spike_result.v1`; composition fails without required typed roles.
- **Tests:** `tests/unit/test_rf4_pack_extensibility.py`,
  `tests/unit/test_interface_analysis.py`, and
  `tests/graph/test_technical_spike_pack.py`; full non-integration gate:
  785 passed, 3 skipped, 10 deselected, with only the known pre-existing
  `test_pf_submit_builds_request_and_returns_host_response` budget assertion
  failure.

## Out of scope

- PM5 packs (`release_readiness`, deployment, incident, health)
- Production connectors or deployment mutation
- Dashboard redesign beyond what EXT/SPIKE need for evidence citations (R6)
