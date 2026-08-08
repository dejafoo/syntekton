---
name: registry-first-extension
description: Add or change Product Factory capabilities, workflow packs, executor modes, validators, model/profile descriptors, tool or connector classes, output roles, and effective execution policy through trusted registries. Use whenever policy or an extension could otherwise require broad unions, duplicated maps, defaults, or named-workflow branches.
---

# Registry-first extension

Treat the registry and compiled pack policy as executable truth. An extension
must be data-driven and validated at registration/compilation, not repaired by
default behavior in a coordinator, scheduler, API client, or dashboard.

## Required descriptor chain

For every capability, provide or update:

1. canonical ID and version;
2. executor mode and registered adapter;
3. agent profile and default model role;
4. permissible tool classes and default budget;
5. result schema and parser;
6. evaluation category; and
7. pack policy entries for validators, output roles, repair/approval/handoff
   constraints where applicable.

Resolve authority by intersection:

```text
descriptor maximum
INTERSECT per-capability pack grant
INTERSECT trusted task request
INTERSECT operator/environment availability
```

Packs may narrow trusted authority; they may never widen it. Define tool,
connector, validator, output, repair, handoff, approval, and external-action
policy per capability when tasks in the same pack need different authority.
Do not replace this with a pack-wide union. Aliases may exist only at ingress
normalization. Persist canonical IDs, pack version, and policy digest.

## Rules and proof

- Reject an unknown capability, mode, adapter, parser, schema, or tool class
  before a run is admitted.
- Reject registration or compilation when a pack grant exceeds its capability
  descriptor or a task requests authority absent from its pack entry.
- Never use a generic executor or completed fallback for an unimplemented
  capability. Return `blocked` or `unsupported` when mandatory work cannot run.
- Do not duplicate mappings in a client, dashboard, or workflow-name set;
  generate or enumerate them from the registry.
- Add table-driven completeness coverage and fake-live receipts identifying the
  executor, adapter/profile, model/tool/connector activity, parser, and result.
- Add negative escalation tests for capability, tool, connector, output role,
  approval, and external-action authority.
- Demonstrate a fixture pack reaches the public host boundary without editing
  shared named-workflow branches or client-maintained workflow lists.
