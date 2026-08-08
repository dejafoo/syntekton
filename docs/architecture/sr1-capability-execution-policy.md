# SR1 — CapabilityExecutionPolicy design note

**Status:** authoritative for SR1 implementation  
**Depends on:** G0 (SR0 complete)  
**Related:** [next-work-packages-sustainable-remediation.md](../next-work-packages-sustainable-remediation.md) §5

## Goal

One pack policy determines validators, tools, outputs, repair, findings, and
external-action requirements **per capability**, without named workflow
branches in shared runtime code.

## Contracts

```text
CapabilityExecutionPolicy
  capability_id: str
  executor_mode: ExecutorMode          # must match CapabilityDescriptor
  allowed_tool_classes: frozenset[str] # ⊆ descriptor.permissible_tool_classes
  allowed_connector_classes: frozenset[str]
  required_dependency_roles: frozenset[str]
  output_roles: frozenset[str]         # subset of pack output roles when set
  validator_ids: tuple[str, ...]       # defaults from pack validators
  repair_eligible: bool
  findings_deliverable: bool           # pack may force true for reporting caps
  approval_required: bool
  external_action_requires_approval: bool
```

`PackExecutionPolicy` keeps pack-level handoff/output XOR/required roles and
adds `capability_policies: dict[str, CapabilityExecutionPolicy]` covering every
`allowed_capabilities` entry.

Authority intersection (no layer may widen the layer above):

```text
descriptor maximum
  INTERSECT capability pack grant
  INTERSECT task.required_tool_classes
  INTERSECT operator/environment availability
```

## Pack migration map

| Pack | Capabilities (explicit) | Notes |
| --- | --- | --- |
| change_intake | requirements, repository_analysis, documentation, composition, independent_review, decision_analysis | deny writes + web/source; repair false |
| feasibility_discovery | domain_research, decision_analysis, requirements, repository_analysis, independent_review, documentation, composition, interface_analysis | deny writes |
| technical_plan | requirements, architecture, composition, independent_review, documentation, interface_analysis | handoffs stay pack-level |
| technical_spike | interface_analysis | deny writes + network |
| repository_investigation | repository_analysis, independent_review, documentation, composition | deny writes |
| **repository_change** | repository_analysis, implementation, repair, independent_review, composition, test_design, test_execution, documentation | **NOT** all CAPABILITIES; **no** deployment_execution |
| quality_gate | test_design, test_execution, security_review, independent_review, composition | findings_deliverable true; deny writes |
| release_readiness | release_analysis, operations_analysis, composition | deny deployment tools |
| deployment_execution | deployment_execution, composition | external_action_requires_approval true; explicit deployment tool classes |
| incident_triage | operations_analysis, composition | deny mutations |
| service_health_review | operations_analysis, composition | deny mutations |

## Ownership moves

| Concern | From | To |
| --- | --- | --- |
| Tool-strip by workflow_type | `effective_policy._READ_ONLY_*` sets | pack `denied_tool_names` + per-capability tool classes |
| `repair_eligible` hardcode | `task.capability in {implementation, repair}` | capability policy / pack `repair_eligible_capabilities` |
| `approval_required` always True | effective_policy | pack/capability policy |
| Deployment approval verify | `RunLifecycleEngine._deployment_approval_verified` + workflow_type check | `trust.approvals.verify_deployment_action_approval` (capability-gated only); broker flag still set at construction from that helper |

## Effective policy digest

Persist on the run (already via effective policy artifact): include
`pack_id`, `pack_version`, `capability`, sorted tool classes/names, repair and
approval flags, and `external_action_requires_approval`. Pack content hash
already covers execution_policy payload; extend `as_payload()` with
`capability_policies`.

## Negative tests required

- register/compile repository_change with deployment_execution → fail
- request tool outside capability grant → fail before broker
- widen pack beyond descriptor → fail at pack validate
- invoke deployment without durable approval → ApprovalBlockedError
- register executor mode without adapter chain → existing descriptor guards

## Non-goals

- Full lifecycle decomposition (SR2)
- Host/v2 client migration (SR4)
- Removing pack-level handoff schema fields
