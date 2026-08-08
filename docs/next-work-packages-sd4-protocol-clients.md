# SD4 — Host protocol and client consolidation

**Status:** `[x]` implemented on `sd/sd4-protocol-clients` (G3 protocol leg). **Gate:** G3 (jointly with SD3 and SD5 — still needs merge). **Findings:** F-13–F-18.  
**Depends on:** SD2 host application-service boundary (`sd/sd2-kernel-decomposition` @ `f84346e`). **Compatibility:** v1 stays through v0.2; v2 is preferred and advertised.

## Outcome

All mutation-capable entry points use one application service and one versioned contract. Clients validate generated transport DTOs while retaining their own local delivery/UI helpers. The dashboard remains loopback-only and monitor-only.

## SD4.A — One application service

- [x] Route local CLI, host CLI, HTTP, MCP, remote Python, and OpenCode mutations through one application service.
- [x] Keep administrative database/backup commands separate and explicitly labelled.
- [x] Prevent multiple mock/live `HostService` instances supervising the same data root.
- [x] Add parity tests proving identical authoritative handoff/approval/policy behavior regardless of ingress.

## SD4.B — `product-factory.host/v2` and `/api/v2`

- [x] Keep the envelope concept but define typed per-operation response payloads.
- [x] Accept canonical workflow IDs only.
- [x] Replace v1 handoff claims with `{handoff_id, expected_digest}`.
- [x] Remove `project_profile`, `model_profile_set`, `requested_artifacts`, `mock`, `inline`, and `sync` from v2 bodies.
- [x] Retain typed `artifact_overrides`; reject extra mutation-body fields.
- [x] Bound request bytes/text, pack-input depth/size, handoff count, validation commands, overrides, and metadata.
- [x] Keep debug execution mode in server/test configuration only.
- [x] Publish `supported_protocols`, default protocol, and deprecation dates in metadata.

**Rollout:** v0.2 implements SD0–SD3 safety while retaining v1; v0.3 prefers v2 and has the v1 adapter emit durable deprecation warnings; v0.4 removes v1/compatibility aliases unless an explicit support decision extends them. Test old clients against each stage and document the rollback decision.

## SD4.C — Generated and validated clients

- [x] Commit canonical OpenAPI/JSON Schema snapshots and generate TypeScript transport DTOs with `openapi-typescript`.
- [x] Keep domain helpers and local delivery logic handwritten.
- [ ] Split OpenCode transport into CLI, HTTP, SSE/polling, delivery, and protocol-validation modules. *(deferred — handwritten client still works against v1; split is non-blocking for G3 protocol leg)*
- [ ] Generate MCP workflow enumeration from the pack registry. *(deferred — registry already validates workflow IDs; enum codegen can follow)*
- [x] Add cross-language golden requests/responses and CI schema/client drift detection.

## SD4.D — Dashboard boundary

- [x] Preserve a loopback-only, monitor-only dashboard; do not store bearer tokens or add mutations.
- [x] Advertise dashboard deployment support explicitly in metadata.
- [x] Document that a remote control token does not make the browser UI a public remote surface; laptop use is an operator-managed SSH/private tunnel to loopback.
- [x] Add browser coverage for supported loopback use and a clear failure state for unsupported authenticated remote use.

## Tests and G3 contribution

Contract tests own strict v2 decoding, limits, canonical IDs, v1 adapter behavior, and generated-schema drift. Cross-language golden fixtures cover host CLI, HTTP, MCP, OpenCode, and remote Python. Integration tests prove one application-service instance and SSE cursor behavior. Browser tests prove dashboard read-only operation, content policy, and remote failure messaging.

G3 contribution is complete when all mutation ingress shares the application service, v2 is preferred and documented, generated DTOs detect drift, and dashboard boundaries remain intact.

**G3 still requires** merging `sd/sd3-durability`, `sd/sd4-protocol-clients`, and `sd/sd5-release-engineering` (and SD2) onto the integration branch.

## Must not

Do not add browser bearer-token storage, a BFF, hidden debug body fields, a new workflow authorization model, or a public dashboard deployment claim.
