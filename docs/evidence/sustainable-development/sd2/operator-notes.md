# SD2 operator notes

- `RunCoordinator` is a façade; prefer `RunLifecycleEngine` and owning services for new behavior.
- `WorkflowType` is a registry-validated string: register packs before constructing `RunRequest`.
- `PackExecutionPolicy` is the sole pack decision surface; ignore deprecated `validation_policy` / `routing_defaults` for runtime decisions.
- Fixture packs: `register_workflow_pack` + `register_pack_handler`, then `HostService.submit` — do not edit named workflow lists.
