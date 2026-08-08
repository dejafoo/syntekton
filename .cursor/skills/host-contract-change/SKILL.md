---
name: host-contract-change
description: Safely evolve Product Factory local CLI, versioned host protocol, HTTP API, MCP, OpenCode plugin, remote clients, SSE, dashboard, generated DTOs, and protocol adoption. Use whenever a client-visible contract, mutation path, stream, delivery path, package, or supported protocol changes.
---

# Host contract change

Inventory every affected client first. Trace mutation requests to the shared
application service and reads from durable projections back to each client. Do
not implement client-specific mutation semantics.

## Rules

- Local CLI, host CLI, HTTP, MCP, remote Python, and OpenCode mutations use one
  application service. Keep administrative database/backup commands separate.
- Version public contracts deliberately. Reject unknown mutation fields, bound
  request sizes/depth/counts, use canonical workflow IDs, and publish support/
  deprecation metadata.
- Generate transport DTOs from canonical contracts; handwritten code owns only
  domain helpers and client-local delivery.
- Treat a protocol version as incomplete until local CLI, host CLI, HTTP, MCP,
  remote Python, and OpenCode either use it or declare a tested, time-bounded
  compatibility adapter. A server-only version is not adopted.
- Negotiate versions explicitly. Do not retry an arbitrary new-protocol failure
  through an older protocol; fall back only for a classified compatibility
  condition and emit deprecation telemetry.
- Use cursor-resumable SSE as the live protocol. Do not add unauthenticated
  streams or query-string token authentication.
- Keep dashboard loopback-only and monitor-only. Do not add bearer-token
  storage or browser mutations; remote viewing is an operator-managed tunnel.

## Required proof

Classify the change as compatible, additive, deprecating, or breaking and list
every client disposition. Add schema snapshots and cross-language golden
fixtures. Test strict decoding, negotiation/fallback, client parity, SSE
reconnect/cursor behavior, local delivery confinement, package install smoke,
and unsupported remote-dashboard failure messaging. Record adoption and
removal evidence; do not infer it from server route presence.
