---
name: host-contract-change
description: Safely evolve Product Factory local CLI, host protocol, HTTP API, MCP, OpenCode plugin, remote clients, SSE, and dashboard boundaries. Use whenever a client-visible contract, mutation path, stream, or generated transport type changes.
---

# Host contract change

Trace the request from ingress to the shared application service, then from the
durable projection to each client. Do not implement client-specific mutation
semantics.

## Rules

- Local CLI, host CLI, HTTP, MCP, remote Python, and OpenCode mutations use one
  application service. Keep administrative database/backup commands separate.
- Version public contracts deliberately. Reject unknown mutation fields, bound
  request sizes/depth/counts, use canonical workflow IDs, and publish support/
  deprecation metadata.
- Generate transport DTOs from canonical contracts; handwritten code owns only
  domain helpers and client-local delivery.
- Use cursor-resumable SSE as the live protocol. Do not add unauthenticated
  streams or query-string token authentication.
- Keep dashboard loopback-only and monitor-only. Do not add bearer-token
  storage or browser mutations; remote viewing is an operator-managed tunnel.

## Required proof

Classify the change as compatible, additive, deprecating, or breaking. Add
schema snapshots and cross-language golden fixtures where applicable. Test
client parity, strict decoding, SSE reconnect/cursor behavior, package install
smoke, and unsupported remote-dashboard failure messaging.
