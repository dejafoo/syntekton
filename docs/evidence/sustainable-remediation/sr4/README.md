# SR4 — Host/v2 client adoption (first slice)

**Package:** SR4  
**Findings:** RF-05  
**Evidence level:** hermetically verified (focused protocol/client/MCP unit tests)

## Placement note

```text
Concern: protocol
Owning boundary: product_factory.remote.client; product_factory.host_mcp.tools; host/protocol_v2
Authoritative source: versioned host/v2 contract + pack registry (MCP workflow enum)
Compatibility: host/v1 via explicit protocol="v1" / --protocol v1; auto negotiates v1 only when server lacks v2
Guardrail proof: tests/unit/test_sr4_remote_host_v2.py; tests/unit/test_remote_pf_client.py; tests/unit/test_host_mcp.py; tests/contract/test_sd4_host_v2.py
Temporary exception: OpenCode package split deferred; MCP still returns host/v1 HostResponse envelopes from HostService
```

## Pre-change status

| Surface | Status before this slice |
| --- | --- |
| Server `/api/v2` + `protocol_v2` | Present (SD4) |
| Python `RemotePfClient` | host/v1 only |
| MCP tools | Already call `HostService`; hardcoded workflow set |
| OpenCode plugin | host/v1 handwritten client |

## Implemented

| Slice | Change |
| --- | --- |
| SR4.B | `RemotePfClient` defaults to host/v2; typed submit/status/inspect/approve/reject/cancel via `/api/v2` |
| SR4.B | Strict `assert_protocol_v2` (unknown top-level fields rejected) |
| SR4.B | Protocol negotiation (`auto`) prefers v2; selects v1 only when meta lacks v2 |
| SR4.B | v1 is explicit (`protocol="v1"`); mock/sync/inline/`handoff_refs`/`model_profile_set` refused on v2 with no silent `/api/v1` retry |
| SR4.B | v2 handoffs use `{handoff_id, expected_digest}` |
| SR4.D (partial) | MCP `pf_submit` workflow enum from `list_accepted_workflow_ids()` (pack registry + aliases) |
| SR4.A note | MCP mutations already route through `HostService` (no duplicate authority) |

## Deferred

| Item | Checklist |
| --- | --- |
| SR4.C OpenCode package split | [ ] `generated/protocol-v2.ts` from OpenAPI [ ] `transport/{http,cli,sse}.ts` [ ] `protocol/validation.ts` [ ] `delivery/materialization.ts` [ ] consumer-ready package build vs source-TS proof [ ] migrate plugin off host/v1 |
| SR4.D MCP host/v2 envelopes | [ ] Translate tool results to `HostResponseV2` [ ] Accept v2 handoff claims on `pf_submit` [ ] Drop v1-only mock/profile request fields from MCP schema once clients migrate |
| SR4.B remainder | [ ] v2 event-tail / delivery routes (still `/api/v1`) [ ] Durable v1 deprecation telemetry on remote path [ ] Remote CLI default consumers + docs polish |
| SR4.E / G3 | [ ] Time-boxed v1 removal [ ] Cross-language OpenCode↔Python golden parity for live v2 clients [ ] All primary clients on v2 with observable v1 usage |

## Commands

```text
uv run pytest -q tests/unit/test_sr4_remote_host_v2.py tests/unit/test_remote_pf_client.py tests/unit/test_host_mcp.py tests/contract/test_sd4_host_v2.py
```

## Known limitations

- SSE stream and delivery blob/receipt paths remain under `/api/v1` (read/delivery surfaces, not mutation authority).
- OpenCode is unchanged in this pass and remains principally a host/v1 client.
- MCP still emits host/v1 `HostResponse` JSON because `HostService` returns that envelope; authority already converges on the application service.
