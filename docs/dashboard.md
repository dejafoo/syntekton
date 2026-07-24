# Local observability dashboard

The dashboard is a bundled React single-page application served by `product-factory observe serve` at `/dashboard/`. It is deliberately local, single-user, and monitor-only. Use the CLI for approval, retry, cancellation, revision, model routing, and deployment actions.

## Start it

```bash
uv sync --extra observability
npm --prefix dashboard ci
npm --prefix dashboard run build
uv run product-factory observe serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/dashboard/`. The run list refreshes every two seconds while visible. A selected run starts from durable projections and follows its named SSE events; a stale label means the stream is reconnecting, not that the run has changed state.

## Operator flow

```text
Run list ── select run ──► Plan: DAG + kanban + repair lineage
                           Execution: tasks, models, tools, validation
                           Timeline: cursor-ordered durable events
                           Evidence: authorized artifacts, plan, lineage
                           Costs: budget ledger and model/task totals
```

When a task is blocked, select it in **Plan** or **Execution**, inspect validator/tool evidence, then use the CLI action indicated by the run's normal workflow. The dashboard does not make that change for you.

## Capture and local boundary

Content is fetched only through run-scoped API endpoints. `off` and `metadata` captures show an explanation instead of a body. `redacted` and `full` show exactly their stored representation; no UI or API de-redacts content, and chain-of-thought is never captured. A hash belonging to another run returns 404.

The dashboard is intended for loopback use. The existing API token and CORS options apply to the API service, but this dashboard is not a remote multi-user deployment target.
