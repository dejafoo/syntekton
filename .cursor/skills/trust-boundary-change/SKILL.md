---
name: trust-boundary-change
description: Safely change Product Factory handoffs, approvals, artifacts, content capture, prompt context, connectors, and external actions. Use whenever untrusted input could influence authority, evidence, model context, or an external side effect.
---

# Trust-boundary change

Identify the untrusted assertion, authoritative durable source, policy that
must propagate, and the point before which model spend, tool use, or connector
calls must be impossible.

## Rules

- Request fields, event payloads, browser input, and model output are claims,
  not authority. Resolve identity, ownership, digest, schema, role, and state
  against persisted records at submission and resume.
- Materialize verified cross-run bytes into immutable consumer input and keep
  producer/parent lineage. Preserve or tighten classification, capture, and
  retention policy; never relax it downstream.
- External actions load canonical fields from an authenticated durable approval
  bound to one action fingerprint and idempotency record. Never accept a
  caller-set approval boolean or persist bearer token.
- Route repository context through safe inventory. Reject symlinks, escapes,
  prohibited/binary/oversize input, and policy ceiling breaches.
- Browser-facing APIs resolve only run-scoped identifiers and verify ownership;
  never accept a filesystem path as authority.

## Required proof

Write negative tests for forged, cross-run, stale, and changed action fields.
Assert refusal occurs before provider spend, tool calls, or connector calls.
Test capture levels and policy propagation. Emit a durable decision/refusal
event and projection that explains the result without leaking unavailable data.
