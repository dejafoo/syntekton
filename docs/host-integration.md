# Host integration protocol (`product-factory.host/v1`)

Vendor-neutral machine interface for submitting Product Factory runs from
OpenCode, Cursor, CI scripts, or any other host CLI. Humans keep using sync
`product-factory run`; hosts use async `product-factory host …` (JSON by
default).

See also: [observability.md](observability.md) (read models / SSE) and
[next-work-packages-phase3.md](next-work-packages-phase3.md).

## Envelope

Every machine-facing host command prints a single JSON object:

| Field | Type | Meaning |
| --- | --- | --- |
| `protocol` | string | Always `"product-factory.host/v1"` |
| `ok` | bool | Command succeeded |
| `run_id` | string \| null | Run handle when applicable |
| `status` | string \| null | Business status (`queued`, `planning`, `awaiting_approval`, …) |
| `plan_summary` | object \| null | Compact plan view when available |
| `subscription` | object \| null | How to follow events (`sse_url`, `cli_tail`) |
| `artifacts` | array | Artifact descriptors (logical name, sha256, path hints) |
| `events` | array | Event batch (tail/attach) |
| `data` | object \| null | Command-specific payload (inspect, approval record, …) |
| `error` | object \| null | `{ "code", "message", "details" }` when `ok` is false |

Clients must ignore unknown fields and treat `protocol` as a hard version check.

### Subscription

```json
{
  "sse_url": "http://127.0.0.1:8765/api/v1/runs/<run_id>/events/stream?after_seq=0",
  "cli_tail": "product-factory host tail <run_id>"
}
```

`sse_url` points at the existing observe server when it is (or will be) running.
Hosts are **not** required to start observe: `host tail` falls back to polling
SQLite, then the per-run `events.jsonl` mirror.

## CLI surface

```bash
# Async submit — returns immediately with run_id + subscription
product-factory host submit --request request.md --repo ./repo --mock

# Follow events (after_seq cursor; resumes cleanly)
product-factory host tail <run_id> --after-seq 0
product-factory host attach <run_id>   # alias for tail

product-factory host status <run_id>
product-factory host inspect <run_id>
product-factory host artifacts <run_id>

product-factory host approve <run_id>
product-factory host reject <run_id>
product-factory host cancel <run_id>
product-factory host revise <run_id> --note "…"
product-factory host export-bundle <run_id>
```

Internal (spawned by submit; not for normal host use):

```bash
product-factory host worker --run-id <run_id> [--mock]
```

Human sync path is unchanged:

```bash
product-factory run --request request.md --repo ./repo --mock
```

## Async execution model

1. `host submit` allocates `run_id`, writes curated request text under
   `.product-factory/runs/<id>/input/`, inserts a `queued` run row, and spawns
   `host worker` in the background.
2. The worker executes the ordinary coordinator path (same planning, grants,
   budgets, validation, approval gates as `run`).
3. Hosts poll `status` / `tail` until a terminal or `awaiting_approval` state.
4. `approve` / `reject` mutate the approval record and emit `approval.decided`
   audit events (same authority model as the human CLI).
5. `cancel` sets a cooperative `cancel_requested` flag on the run row; the
   coordinator observes it between tasks/waves and ends with typed
   `cancelled`. Queued / awaiting_approval cancels finalize immediately.
6. `revise` (only from `awaiting_approval`) attaches an operator note, emits
   `run.revision_requested`, and re-opens planning/execution **without**
   widening grants, budget, or workflow.
7. `export-bundle` writes a redaction-aware zip under
   `runs/<id>/export/` (manifest, plan, validations, patch/report, cost
   summary, event cursor range).

## Authority and content boundaries

- Hosts submit **named request text** and optional curated artifacts only —
  not full chat transcripts.
- Product Factory owns planning, skill/tool grants, budgets, validation, and
  approval. Hosts do not widen grants by prompt.
- Observe remains the read surface; Phase 3 control mutations go through the
  host service layer (CLI, HTTP control routes on observe/serve, MCP in P3.C).

## HTTP control API (P3.B)

Same `HostResponse` envelope as the CLI. Served by `product-factory observe serve`
(or alias `product-factory serve`):

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/v1/runs` | Submit; `202` + `subscription.sse_url` |
| `POST` | `/api/v1/runs/{id}/approve` | Optional `{ "apply": false }` |
| `POST` | `/api/v1/runs/{id}/reject` | |
| `POST` | `/api/v1/runs/{id}/cancel` | Cooperative cancel via `HostService` |
| `POST` | `/api/v1/runs/{id}/revise` | `{ "note": "…" }` bounded follow-up |
| `POST` | `/api/v1/plan` | Plan-preview (compile only; no run) |

When `PRODUCT_FACTORY_OBSERVE_TOKEN` is set, write routes require
`Authorization: Bearer <token>` even on loopback. Reads keep existing
loopback-open / non-loopback-token rules.

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"request_text":"…","repository_path":"/path/to/repo","mock":true}'
```

## OpenCode / other hosts (MCP)

Thin stdio MCP server wrapping the same `HostService` (no HTTP round-trips):

```bash
product-factory mcp [--mock] [--data-dir PATH]
```

| Tool | Maps to |
| --- | --- |
| `pf_submit` | `host.submit` |
| `pf_status` / `pf_tail` | `host.status` / one `host.tail` batch |
| `pf_inspect` | `host.inspect` |
| `pf_approve` / `pf_reject` / `pf_cancel` | control actions |
| `pf_export` | `host.export_bundle` |

`revise` is available on the CLI and HTTP control API only (kept out of the
small MCP tool set to limit context bloat); use `product-factory host revise`
or `POST …/revise` when needed.

Each tool returns `product-factory.host/v1` `HostResponse` JSON (also as MCP
`structuredContent` when the client supports it).

| Host | Consumption path |
| --- | --- |
| Scripts / CI | `product-factory host …` JSON or HTTP control API |
| HTTP clients | Control routes on observe/`serve` (this section) |
| OpenCode | MCP + slash commands — see [`examples/opencode/`](../examples/opencode/) |
| Cursor / Claude Code | Same `product-factory mcp` stdio config (no OpenCode files required) |

OpenCode packaging is config only (not a proprietary plugin). Merge
`examples/opencode/opencode.json`, enable the `product-factory` MCP server, then
try `/pf-investigate …` → `/pf-status` → `/pf-approve`.

## Workflow packs (host-facing)

| `workflow_type` | Notes |
| --- | --- |
| `code_change` / `repository_change` | Diff + approval (Phase 1 pack) |
| `repository_investigation` | Read-only evidence report; no write grants by default |
| `technical_plan` | Requirements / architecture decision / acceptance criteria |
| `architecture` | Alias → `technical_plan` (compat) |

```bash
product-factory host submit --workflow repository_investigation \
  --request request.md --repo ./repo --mock
product-factory host submit --workflow technical_plan \
  --request request.md --mock
```

## Environment

| Variable | Role |
| --- | --- |
| `PRODUCT_FACTORY_OBSERVE_URL` | Base URL for SSE subscription hints (default `http://127.0.0.1:8765`) |
| `PRODUCT_FACTORY_FORCE_MOCK` | Force mock gateway (also `--mock` on submit/worker) |
| `OPENROUTER_API_KEY` | Live model backend when mock is not forced |
| `PRODUCT_FACTORY_OBSERVE_TOKEN` | When set, HTTP write routes require `Authorization: Bearer …` |
