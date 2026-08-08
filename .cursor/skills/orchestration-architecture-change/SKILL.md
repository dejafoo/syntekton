---
name: orchestration-architecture-change
description: Place Product Factory lifecycle, task execution, planning, scheduling, validation, repair, and composition changes in the correct architecture boundary. Use whenever modifying orchestration, planning, scheduling, validation, workflow handlers, or code that could otherwise grow RunCoordinator.
---

# Orchestration architecture change

Classify the requested behavior before editing: `lifecycle`, `executor`, `pack
policy`, `composition`, `scheduler/lineage`, `validation/repair`,
`finalization`, or `read projection`.

| Concern | Owning boundary |
| --- | --- |
| Submit, resume, cancel, revise, approval/rejection, terminal transition | lifecycle engine/facade |
| Model/tool/deterministic task work | registered task executor |
| Allowed capabilities, outputs, validators, tool classes, repairs | pack execution policy/registry |
| Evidence/draft assembly | composition service |
| Runnable waves, slots, dependencies, worktree and repair lineage | scheduler/lineage service |
| Findings, repair eligibility, validation result | validation-repair service |
| Manifest, artifact roles, final state, next actions | finalizer/projection service |

## Rules

- Do not add behavior to `RunCoordinator`. If an unavoidable compatibility edit
  is needed, delegate immediately to an owning boundary and record a dated
  removal issue in the hand-off.
- Do not branch on workflow/capability names in shared lifecycle code. Put
  variation in the trusted registry, effective task policy, or pack handler.
- Pass typed dependencies across boundaries; do not replace them with `Any`,
  callback bags, or calls to coordinator private methods.
- Preserve durable run semantics. Events notify and diagnose; projections and
  durable records remain the source of state.

## Required proof

State the concern, owner, authority, and compatibility using the root
`AGENTS.md` placement-note format. Add a characterization or regression test
that fails when the behavior is moved back into the wrong boundary. For an
extension, prove a fixture pack can use it without changing coordinator,
scheduler, API workflow unions, dashboard lists, or client workflow lists.
