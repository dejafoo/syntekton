# SD4 → G3 protocol leg evidence

**Branch:** `sd/sd4-protocol-clients`  
**Base:** `sd/sd2-kernel-decomposition` @ `f84346e`  
**Prior gates:** [`../baseline/`](../baseline/), [`../sd1/`](../sd1/), [`../sd2/`](../sd2/)

## Implemented

| Slice | Evidence |
| --- | --- |
| SD4.A one HostService | `host/registry.py`; local CLI + host CLI + MCP via `get_host_service`; `ops *` labelled administrative |
| SD4.A parity | `tests/contract/test_sd4_host_v2.py::test_ingress_parity_approve_handoff_host_vs_http` |
| SD4.B host/v2 | `host/protocol_v2.py`, `host/bounds.py`, `host/handoff_claims.py`, `api/control_v2.py` |
| SD4.B v1 compatibility | `/api/v1` retained; meta advertises both; dead fields forbidden on v2 only |
| SD4.C snapshots | `contracts/host/openapi-v2.json`, `schemas/host/v2.json` |
| SD4.C TS DTOs | `contracts/host/generated/openapi.ts` (+ dashboard copy) |
| SD4.C drift | `scripts/check_openapi_drift.sh`, `scripts/generate_host_openapi.sh` |
| SD4.C goldens | `tests/fixtures/host_protocol/v2_submit*.json` |
| SD4.D dashboard | meta `dashboard.*`; `docs/dashboard.md`; `unsupportedRemoteDashboardMessage` |

## Hermetic verification

```text
uv run pytest -q -m "not integration"
bash scripts/check_openapi_drift.sh
npm --prefix dashboard test -- --run
```

Results: see `pytest-not-integration.txt` and `operator-notes.md`.

## Integration / operational

- Integration: deferred (no live-model / remote-docker claim for SD4 alone).
- Operational: not claimed for the protocol leg.

## G3 note

SD4 completes the **protocol** contribution to G3. Full G3 still needs merge of:

1. `sd/sd2-kernel-decomposition` (already base of this branch)
2. `sd/sd3-durability`
3. `sd/sd4-protocol-clients` (this branch)
4. `sd/sd5-release-engineering`

Suggested merge order after review: SD2 → SD3 → SD4 → SD5 (or SD2 → SD5 → SD3 → SD4 if CI lockfile should land before durability conflicts). File ownership was declared to minimize collisions; expect conflicts mainly in `scripts/verify.sh` (add `check_openapi_drift.sh`) and shared docs trackers.

## Placement note

```text
Concern: protocol | host application service | CLI/HTTP/MCP ingress | dashboard boundary
Owning boundary: product_factory.host.{service,registry,protocol_v2,bounds}; product_factory.api.control_v2; product_factory.cli.app (mutations)
Authoritative source: HostService + durable handoff/approval records; pack registry for workflow IDs
Compatibility: product-factory.host/v1 + /api/v1 retained (v0.2); v2 preferred; deprecation dates in meta
Guardrail proof: tests/contract/test_sd4_host_v2.py; scripts/check_openapi_drift.sh; dashboard api.test.ts
Temporary exception: OpenCode transport module split + MCP workflow enum codegen deferred (non-blocking); action-approvals HTTP still uses ApprovalService(ApiState.db) pending SD3 db ownership unification
```
