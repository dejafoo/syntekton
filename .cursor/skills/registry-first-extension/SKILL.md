---
name: registry-first-extension
description: Add or change Product Factory capabilities, workflow packs, executor modes, validators, profiles, tool classes, output roles, and related policy through trusted registries. Use whenever an extension could otherwise require duplicated maps or named-workflow branches.
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

Packs may narrow trusted authority; they may never widen it. Aliases may exist
only at host/registry normalization. Persist canonical IDs in durable runs.

## Rules and proof

- Reject an unknown capability, mode, adapter, parser, schema, or tool class
  before a run is admitted.
- Never use a generic executor or completed fallback for an unimplemented
  capability. Return `blocked` or `unsupported` when mandatory work cannot run.
- Do not duplicate mappings in a client, dashboard, or workflow-name set;
  generate or enumerate them from the registry.
- Add table-driven completeness coverage and fake-live receipts identifying the
  executor, adapter/profile, model/tool/connector activity, parser, and result.
- Demonstrate a fixture pack reaches the public host boundary without editing
  shared named-workflow branches.
