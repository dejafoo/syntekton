# SD1 — Executor truth and PM5 completion

**Status:** `[ ]` planned. **Gate:** G1. **Findings:** F-05–F-07.  
**Depends on:** G0. **Freeze:** no feature-pack expansion until G1 passes.

## Outcome

Every capability either performs its declared, traceable work through a registered executor or fails as `blocked`/`unsupported`. No pack may become ready because a caller supplied evidence-shaped data, a generic fallback returned a success-shaped result, or a deterministic mock is misrepresented as live work.

Land this as short PRs: (A) descriptors, (B) protocol/registry, (C) migration of existing paths, (D) missing behavior, then (E) delete placeholders and prove completeness. Do not combine with SD2 coordinator extraction.

## SD1.A — Capability descriptor registry

Create one trusted descriptor:

```text
CapabilityDescriptor
  id / version
  executor_mode
  executor_adapter_id
  agent_profile_id
  default_model_role
  permissible_tool_classes
  result_schema_id
  default_budget
  evaluation_category
```

- [ ] Generate `Capability`, capability IDs, scheduler defaults, prompt-profile selection, and validation coverage from this registry.
- [ ] Permit packs to narrow a descriptor but never widen tool authority.
- [ ] Remove the default fallback to `implementation_worker`.
- [ ] Add dedicated release-analyst, operations-analyst, and deployment-controller profiles.
- [ ] Fail pack registration/compilation for unknown descriptor, adapter, profile, mode, schema, or impermissible authority.

## SD1.B — Executor protocol and registry

```text
TaskExecutionRequest
  RunExecutionContext
  TaskSpec
  EffectiveTaskPolicy
  resolved handoffs
  typed dependency artifacts
  workspace
  validation evidence
  composition role

TaskExecutor.execute(request) -> TaskResult
```

- [ ] Register fixed executors: repository agent, research agent, interface agent, model draft, validation, deterministic operation, and composition.
- [ ] Dispatch strictly from persisted `EffectiveTaskPolicy.executor_mode`.
- [ ] Fail missing modes/capability adapters during registration or compilation, before a run is admitted.
- [ ] Make task results identify executor, adapter, profile, model/tool/connector activity, parser, output receipt, and live versus deterministic-mock mode.

## SD1.C — Migrate real paths without outcome drift

- [ ] implementation and repair → repository agent.
- [ ] architecture, requirements, discovery, and decision research → research agent.
- [ ] interface analysis → interface agent.
- [ ] independent review → model-draft review adapter.
- [ ] deployment state machine → deterministic deployment adapter.
- [ ] registered composition → composition executor.
- [ ] repository inventory → named deterministic adapter.
- [ ] Delete the matching coordinator branch after each executor has characterization and fake-live coverage.

## SD1.D — Implement missing executor behavior

- [ ] `release_analysis`: bounded Git/CI/operations read loop and typed release-analysis result.
- [ ] `operations_analysis`: bounded operational reads, observation/inference labels, staleness evidence, and typed output.
- [ ] `security_review`: model draft over safe repository/evidence inputs with structured findings.
- [ ] `documentation`: typed document draft grounded in verified dependency artifacts.
- [ ] `test_design`: model-generated test plan grounded in `SafeRepositoryInventory`.
- [ ] `test_execution`: registered validation commands and typed receipts; never synthesize a pass.
- [ ] Mandatory unavailable evidence produces `blocked` or `unsupported`, not success.
- [ ] Deterministic mock execution is marked in task results, events, and the dashboard.

## SD1.E — Remove successful placeholders

- [ ] Delete the generic `completed (stub)` branch.
- [ ] Add registry-completeness coverage for every capability, adapter, profile, parser, result schema, and evaluation category.
- [ ] Add fake-live tests for every canonical pack asserting expected model/tool/connector/parser/output activity.
- [ ] Reclassify PM5 documentation as hermetically implemented until real connector/model evidence exists.

## Test and observability plan

Before each migration, capture existing output behavior and explicitly decide intentional differences. Unit tests own descriptor narrowing, result parsing, policy-to-mode dispatch, and unavailable evidence. Contract tests own pack registration and host submission rejection. Integration tests own executor receipts and persisted effective policy. Fake-live tests use controlled providers/connectors and assert calls rather than prose. Dashboard tests render mock/live state plainly. Live evidence remains separate and cannot be fabricated by a fake provider.

Required events/projections: executor selected, adapter/profile resolved, activity receipts, parser failure, blocked/unsupported reason, deterministic-mock marker, and terminal result summary.

## G1 exit checklist

- [ ] Every capability has executor, adapter, profile, parser, result schema, budget, and evaluation category.
- [ ] Unknown capability/mode/adaptor fails before execution.
- [ ] PM5 outputs trace to task work and persisted receipts.
- [ ] No workflow becomes ready solely from caller-provided evidence-shaped fields.
- [ ] Required Python/dashboard/plugin/package and targeted fake-live suites pass.
- [ ] Link all four evidence levels in the master tracker; do not call a simulated adapter operational proof.

## Must not

Do not add a broad provider-routing redesign, new model authority, new packs, or hidden coordinator fallback while completing SD1. A failure that exposes unsupported capability behavior is a correct result, not a test to bypass.
