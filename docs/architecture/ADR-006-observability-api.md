# ADR-006 — Durable observability events + read-only API

## Status

Accepted

## Decision

Product Factory uses a **domain event store in SQLite** as the authoritative operational observability log, exposed through a **read-only REST + WebSocket/SSE API**.

1. Instrumentation lives in `RunCoordinator`, the model gateway, and the tool broker — not in LangGraph stub nodes.
2. Events are versioned `ObservabilityEvent` envelopes with monotonic `seq` cursors.
3. Large/sensitive content is stored as content-addressed artifacts; events carry hashes and capture-level metadata.
4. OpenTelemetry / OpenInference export is optional and maps from the internal schema (GenAI conventions remain Development).
5. AG-UI is deferred as a future UI projection, not the storage schema.

## Rationale

Sparse per-run JSONL cannot support live dashboards or stuck detection. Vendor platforms (Langfuse) are too heavy for local MVP. OTel GenAI conventions are not stable enough to be the internal schema, but W3C trace IDs + optional OTLP keep us interoperable (e.g. Phoenix).

## Consequences

- FastAPI/uvicorn are an optional dependency extra (`observability`).
- CLI orchestration does not require the API process.
- Capture level defaults to `redacted`; full content is localhost-only and opt-in.
- JSONL remains an optional diagnostic mirror only.
