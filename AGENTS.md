# Development-agent guardrails

These instructions govern agents modifying the Product Factory repository. They
are not Product Factory runtime skills. Read the applicable repository skill in
`.cursor/skills/` before changing the matching area. Cursor discovers these as
project skills; Codex loads the same canonical instructions through this file.

| Change | Required skill |
| --- | --- |
| Lifecycle, planning, task execution, scheduling, validation, repair, or composition | `orchestration-architecture-change` |
| Capability, pack, validator, model/profile, tool class, output role, or executor mode | `registry-first-extension` |
| Handoff, approval, artifact/content access, prompt capture, connector, or external action | `trust-boundary-change` |
| Schema, repository, event, worker, artifact storage, backup, recovery, or retention | `durability-state-change` |
| CLI, host protocol, HTTP API, MCP, OpenCode, remote client, SSE, or dashboard | `host-contract-change` |
| Substantial implementation, refactor, or completion review | `architectural-review` |

## Always

- Keep one authoritative source for policy, lifecycle state, and access
  decisions. Request fields are assertions, never authority, until resolved
  against trusted durable records.
- Do not add new behavior to `RunCoordinator`. Place it in an owning lifecycle
  service, executor, registry/pack, repository, adapter, or projection. A
  temporary compatibility edit requires an explicit removal issue.
- Do not add a named workflow/capability conditional in shared runtime code.
  Extend a trusted registry or pack policy instead.
- Do not introduce a success-shaped placeholder. Missing evidence or unsupported
  work must be `blocked`, `unsupported`, or another honest terminal state.
- Do not bypass the shared host application service, persistence repository
  boundary, capture policy, or versioned protocol contract.
- For a change that triggers a skill, include this placement note in the PR or
  hand-off:

```text
Concern: <lifecycle | executor | policy | persistence | protocol | UI>
Owning boundary: <service/module>
Authoritative source: <registry/durable record>
Compatibility: <none or migration/version/rollback>
Guardrail proof: <test path and result>
Temporary exception: <none or ADR/removal issue>
```

Use `docs/handover_sustainable_development.md` and the SD0–SD8 playbooks as
the target architecture. The existing coordinator and v1 protocol are legacy
compatibility surfaces while their replacements are incomplete; do not treat
their current shape as precedent for new features.
