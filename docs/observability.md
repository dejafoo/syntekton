# Observability API

Local-first, SQLite-backed observability for Product Factory runs. The CLI writes durable events while orchestrating; a separate observer process serves REST, WebSocket, and SSE.

## Quick start

```bash
# Install optional API stack
uv sync --extra observability

# Terminal A — ordinary run (writes to .product-factory/data/*.sqlite)
uv run product-factory run --request request.md --mock

# Terminal B — observer (reads the same SQLite DB; no orchestration daemon)
uv run product-factory observe serve --host 127.0.0.1 --port 8765
```

Open the local dashboard at [http://127.0.0.1:8765/dashboard/](http://127.0.0.1:8765/dashboard/). It is a bundled, single-user monitoring UI: it has no approve, retry, cancel, or deployment controls. Use the CLI or the HTTP control routes (`POST /api/v1/runs`, approve/reject, …) for mutations — see [host-integration.md](host-integration.md).

Examples:

```bash
curl -s http://127.0.0.1:8765/api/v1/health | jq
curl -s http://127.0.0.1:8765/api/v1/runs | jq
curl -s "http://127.0.0.1:8765/api/v1/runs/<run_id>/events?after_seq=0" | jq

# SSE
curl -N "http://127.0.0.1:8765/api/v1/runs/<run_id>/events/stream?after_seq=0"
```

WebSocket: connect to `ws://127.0.0.1:8765/api/v1/events/ws`, then send:

```json
{"run_ids": ["<run_id>"], "after_seq": 0, "types": null}
```

Frames: `subscribed`, `event`, `heartbeat`, `error` (including `slow_consumer` with a resumable `after_seq`). Delivery is at-least-once; clients deduplicate by `event_id`.

## Security and content capture

| Setting | Default | Notes |
| --- | --- | --- |
| Bind address | `127.0.0.1` | Loopback only |
| `PRODUCT_FACTORY_OBSERVE_TOKEN` | unset | **Required** for non-loopback hosts (Bearer) |
| `PRODUCT_FACTORY_CAPTURE_LEVEL` | `redacted` | `off` \| `metadata` \| `redacted` \| `full` |
| CORS | off | Pass `--cors https://example.com` explicitly |

Capture level applies before persistence. Secrets/paths are redacted in `redacted` mode. `full` stores transmitted prompts/responses locally under `runs/<id>/content/` — never enable remotely by default. Chain-of-thought is never captured; only transmitted messages.

The dashboard and API expose a stored content body only when it is both referenced by the requested run and available under its original capture level. `off` and `metadata` return `available: false`; `redacted` and `full` return exactly the stored value. Artifact bodies are likewise constrained to their owning run. Unknown or cross-run hashes return 404. The browser never sends a filesystem path.

## Logs vs events

- **Structured logs** (`product_factory.observability.logging`): concise operator stdout.
- **Observability events**: versioned `ObservabilityEvent` rows in SQLite (`events` table), cursor-ordered by `seq`.
- Per-run JSONL under `runs/<id>/events.jsonl` is an optional diagnostic mirror only.

## Stuck detection

Run/task summaries include derived `liveness`: `healthy` | `slow` | `suspected_stuck` | `timed_out`, based on `last_progress_at` without mutating business status.

## Optional Phoenix / OTLP

```bash
uv sync --extra otel
# Phoenix (example): docker run -p 6006:6006 -p 4318:4318 arizephoenix/phoenix:latest
export PRODUCT_FACTORY_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
uv run product-factory run --request request.md --mock
```

Domain events map to OpenInference span kinds (`AGENT`, `LLM`, `TOOL`, …). The internal schema remains authoritative if external conventions change. See [ADR-006](architecture/ADR-006-observability-api.md).

## Dashboard consumer guidance

1. List runs via `GET /api/v1/runs`; open detail via `GET /api/v1/runs/{id}`.
2. Catch up with `GET /api/v1/runs/{id}/events?after_seq=…`, then subscribe on WS/SSE with the same cursor.
3. Use projection endpoints (`tasks`, `model-invocations`, `tool-calls`, `artifacts`, `prompts`) for list/detail UIs — do not rebuild them by replaying all events.
4. Treat heartbeats as liveness of the stream, not of the run; use run `liveness` / `last_progress_at` for stuck detection.
5. The bundled dashboard uses those projections for its state and only uses SSE to invalidate/append updates. Its plan, execution, timeline, evidence, and cost tabs are available at `/dashboard/runs/<run_id>`.

See the [dashboard operator guide](dashboard.md) for its local build, monitoring flow, and security boundary.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Empty runs | Same `--data-dir` / `.product-factory` as the CLI job |
| Import error on serve | `uv sync --extra observability` |
| Non-loopback 403 | Set `PRODUCT_FACTORY_OBSERVE_TOKEN` |
| `observability.degraded` events | SQLite busy/contention; WAL is enabled; writers retry |
| Missing prompt bodies | Capture level `metadata`/`off`, or content not yet written |
