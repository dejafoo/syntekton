# SD2 — Kernel decomposition and policy consolidation

**Status:** `[x]` complete (hermetic G2). **Gate:** G2. **Findings:** F-06, F-07, F-08, F-22.  
**Depends on:** G1. **Compatibility:** preserve current behavior while `RunCoordinator` is progressively reduced to a façade.

## Outcome

Replace the coordinator's workflow-specific control flow with composable lifecycle services and a single authoritative pack execution policy. The success criterion is dependency and branch boundaries, not a line-count reduction.

## Extraction order

1. [x] **`CompositionService`** — replace callback-heavy `ComposeContext` with typed data and explicit draft/dependency services; move architecture, evidence, intake, quality, release, deployment, and operations composition out of the coordinator.
2. [x] **`ValidationRepairService`** — own validation pipelines, finding policy, repair eligibility, repair-task creation, and terminal decisions; consume pack execution policy as the sole declaration.
3. [x] **`WaveScheduler` and `WorktreeLineageService`** — own runnable selection, concurrency slots, dependency completion, patch inheritance, conflicts, and repair lineage.
4. [x] **`RunFinalizer`** — own final outputs, artifact-role enforcement, manifests, final state, and eligible-next-action projections.
5. [x] **`RunLifecycleEngine`** — own submit, run, resume, cancel, revise, approve, reject, and finalization orchestration.
6. [x] **`RunCoordinator`** — retain temporarily only as a compatibility façade delegating to those services; remove workflow implementations, model/tool loops, and composition helpers.

For every extraction: first add characterization and contract tests, then introduce the service behind an unchanged boundary, then delete the old branch in the same or a following reviewable change. Publish ownership of coordinator-adjacent files to prevent parallel edits from reintroducing coupling.

## Policy consolidation

- [x] Make `PackExecutionPolicy` authoritative for validators, output roles, findings, repairs, approvals, handoffs, and executor modes.
- [x] Retire duplicate `validation_policy`, unused `routing_defaults`, workflow-name grant sets, and duplicated capability constants only after callers migrate.
- [x] Keep aliases only in registry/host normalization; durable runs persist canonical pack IDs.
- [x] Replace hard-coded `WorkflowType` with a bounded validated string resolved by the trusted registry.
- [x] Submit an end-to-end fixture pack via the public host API without changing coordinator, scheduler, API unions, dashboard lists, or client workflow lists.
- [x] Add architecture tests prohibiting new workflow-name branches outside compatibility normalization.

## Tests, projections, and compatibility

Unit tests own individual service decisions; contract tests own public host/v1 parity; integration tests own full wave/repair/finalization behavior; migration tests own canonical ID persistence; browser tests own unchanged projections; fake-live tests confirm executor activity remains SD1-honest. Existing run data and host/v1 payloads remain readable through SD3.

Required projections/events: lifecycle transition owner, wave scheduling decision, repair origin/replacement, conflict decision, final artifact-role decision, and pack-policy resolution. Do not infer durable status from events alone.

## G2 exit checklist

- [x] `RunCoordinator` is only a lifecycle compatibility façade.
- [x] No workflow implementation, model/tool loop, or composition helper remains there.
- [x] `PackExecutionPolicy` is the sole policy declaration and durable runs use canonical pack IDs.
- [x] Fixture-pack extensibility works through the public host API without source edits to named workflow lists.
- [x] Architecture, parity, repair-lineage, finalization, dashboard, and package suites pass.
- [x] The master tracker links implementation, hermetic, integration, and operational evidence.

## Must not

Do not use a line-count target, introduce host/v2 early, widen pack authority, or make generic dynamically supplied code executable. Registry validation remains a trusted boundary.
