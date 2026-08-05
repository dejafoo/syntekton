# R4 workflow-branch inventory

**Status:** implemented inventory for RF4.EXT migration  
**Scope:** current runtime, planner, handlers, host, and read plane

This inventory classifies workflow-name and alias decisions before migration.
New packs must use registry + `PackExecutionPolicy`; they must not add a
workflow-name branch to `RunCoordinator`.

| Location (file:symbol) | Branch key | Class | Disposition |
| --- | --- | --- | --- |
| `workflows/registry.py:canonical_workflow_id` | `code_change`, `architecture` aliases | compat adapter | Keep only at request/host normalization; durable metadata uses canonical pack identity. |
| `orchestration/coordinator.py:_plan` | registered pack vs legacy fallback | generic lifecycle | Use `is_registered_workflow` + registered handler; keep legacy fallback temporarily. |
| `orchestration/coordinator.py:_plan` | repository-change review/analysis toggles | pack policy | Move task-template variants into registered handler/policy; current branch is legacy allowlisted. |
| `orchestration/coordinator.py:_execute` | findings are deliverable | pack policy | Moved to `PackExecutionPolicy.findings_are_deliverable`. |
| `orchestration/coordinator.py:_execute` | repair eligibility | pack policy | Moved to `PackExecutionPolicy.repair_eligible_capabilities`. |
| `orchestration/coordinator.py:_execute` | final output required/exclusive roles | pack policy | Moved to `required_output_roles` / `exactly_one_output_role_groups`. |
| `orchestration/coordinator.py:_execute` | deterministic compose fallback | pack policy | Moved to `fallback_composition_roles`. |
| `orchestration/coordinator.py:_execute` | repository patch/change-set finalization | pack policy | Legacy finalizer; keep allowlisted until patch finalization is a registered handler operation. |
| `orchestration/coordinator.py:_execute` | technical-plan finalization | pack policy | Legacy finalizer; keep allowlisted until architecture-specific validators are registry-driven. |
| `orchestration/coordinator.py:_execute` | role-specific validators | pack policy | Validator IDs are declared in `PackExecutionPolicy`; existing function dispatch remains a fixed trusted catalogue. |
| `orchestration/coordinator.py:_execute_task` | capability branch | capability executor | Keep fixed executor catalogue; mode now comes from `PackExecutionPolicy` through `EffectiveTaskPolicy`. |
| `orchestration/coordinator.py:_execute_task` | interface-analysis loop | capability executor | Dedicated `interface_agent_loop`; tools come from effective policy only. |
| `orchestration/coordinator.py:_execute_task` | read-only grant strips | pack policy | Existing workflow sets are legacy allowlisted; migrate to policy grant narrowing. |
| `workflows/default_plans.py` | one function per pack | pack policy | Keep behind `PackHandler.plan_template`; coordinator does not select templates by name. |
| `workflows/handlers/*` | handler per canonical pack | pack policy | Keep; trusted registry dispatch, no planner-supplied code. |
| `workflows/handoffs.py` | accepted schemas/states/roles | pack policy | Moved to `PackExecutionPolicy`. |
| `workflows/artifacts.py` / land map | output role and landing | pack policy | Keep declarative `ArtifactLandSpec`; roles cross-validated with execution policy. |
| `host/service.py` | aliases / eligible next actions | compat adapter / presentation | Resolve aliases at ingress; use handler metadata for actions. |
| `observability/query.py` | historical `workflow_type` display | compat adapter | Preserve stored value for old runs; current manifests expose canonical pack id/version. |

## Legacy coordinator branch allowlist

Until the remaining finalizer and validator catalogue extraction is complete,
workflow-name constants in `RunCoordinator` are allowed only for:

1. repository-change patch/change-set finalization and approval;
2. technical-plan architecture validation;
3. existing role-specific validation functions;
4. grant narrowing that has not yet moved to policy fields;
5. compatibility behavior for historical records.

The architecture test for RF4 rejects new workflow-name constants or direct
string comparisons outside this reviewed list. Generic lifecycle logic,
registered handler dispatch, and capability executor selection are not
workflow-name branches.
