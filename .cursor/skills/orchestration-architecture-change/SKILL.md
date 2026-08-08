---
name: orchestration-architecture-change
description: Place Product Factory lifecycle, task execution, planning, scheduling, validation, repair, composition, task-result semantics, and application construction in the correct boundary. Use whenever modifying orchestration services or code that could grow RunCoordinator, RunLifecycleEngine, HostService, or another shared replacement monolith.
---

# Orchestration architecture change

Classify the requested behavior before editing. Name one primary owner and the
typed inputs it may consume.

| Concern | Owning boundary |
| --- | --- |
| Dependency construction and service wiring | application composition root |
| Submit, resume, cancel, revise, terminal sequencing | lifecycle command/engine service |
| Model/tool/deterministic task work | registered task executor |
| Allowed capabilities, outputs, validators, tool classes, repairs | pack execution policy/registry |
| Evidence/draft assembly | composition service |
| Runnable waves, slots, dependencies, worktree and repair lineage | scheduler/lineage service |
| Findings, repair eligibility, validation result | validation-repair service |
| Manifest, artifact roles, final state, next actions | finalizer/projection service |

## Rules

- Do not add implementation behavior to `RunCoordinator`, `RunLifecycleEngine`,
  `HostService`, or another general-purpose facade. A lifecycle engine may
  sequence named services; it must not construct brokers, resolve detailed
  task policy, run model/tool loops, compose outputs, or implement capabilities.
- Put dependency construction in the composition root. Inject interfaces into
  services; do not make a lifecycle service a service locator.
- Delegate unavoidable compatibility edits immediately and record an owner,
  removal condition, and target date or release.
- Do not branch on workflow/capability names in shared lifecycle code. Put
  variation in the trusted registry, effective task policy, or pack handler.
- Pass typed dependencies across boundaries; do not replace them with `Any`,
  callback bags, or calls to coordinator private methods.
- Separate executor completion from the observed domain outcome. Complete test
  execution may report failing tests; incomplete required output must not
  satisfy dependencies. Centralize terminal/dependency/repair predicates.
- Preserve durable run semantics. Events notify and diagnose; projections and
  durable records remain the source of state.

## Required proof

State the concern, owner, authority, and compatibility using the root
`AGENTS.md` placement-note format. Add a characterization test before moving
behavior and an import/AST regression test that fails if it returns to a facade
or crosses the declared dependency direction. Do not use line count as the
guard. For an extension, prove a fixture pack can use it without changing
coordinator, lifecycle, scheduler, API workflow unions, dashboard lists, or
client workflow lists.
