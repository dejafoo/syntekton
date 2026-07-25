# Host integration protocol (`product-factory.host/v1`)

Vendor-neutral machine interface for submitting Product Factory runs from
OpenCode, Cursor, CI scripts, or any other host CLI. Humans keep using sync
`product-factory run`; hosts use async `product-factory host …` (JSON by
default).

See also: [observability.md](observability.md) (read models / SSE),
[next-work-packages-phase3.md](next-work-packages-phase3.md),
[next-work-packages-phase3g.md](next-work-packages-phase3g.md) (`materialize` +
optional OpenCode plugin), and
[next-work-packages-phase4.md](next-work-packages-phase4.md) (named deliverables,
connectors, `quality_gate`).

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
product-factory host materialize <run_id> \
  --artifact ARCHITECTURE.md --to docs/ARCHITECTURE.md

# Land every deliverable the run produced (one call, still audited per file)
product-factory host materialize-all <run_id>
product-factory host materialize-all <run_id> --role test_plan

# Name a deliverable up front, by pack role
product-factory host submit --request request.md --workflow technical_plan \
  --artifact-override architecture_document=docs/integration_testing_architecture.md
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
8. `materialize` copies a known run artifact (e.g. `ARCHITECTURE.md`,
   `EVIDENCE_REPORT.md`) into the run’s `repository_path`. Allowed only when
   status is `awaiting_approval` or `completed`; destination must stay under
   the repo root (path escape rejected). Emits `artifact.materialized` with
   sha256 + dest path. Patch apply still uses `approve --apply` /
   `approve(apply=true)`.
9. `materialize-all` lands every deliverable in the run's resolved land map.
   Each file goes through `materialize`, so path checks and the audit event
   apply per file; a role the run did not produce is reported as `skipped`
   rather than landed empty. One operator confirmation upstream covers the
   batch — nothing lands implicitly.

## Named deliverables (artifact land map)

A workflow pack declares deliverables by **role** (a stable key such as
`architecture_document`), not by filename. Each role carries a default produced
name and destination that a host may override per run, so a request for an
integration-testing architecture lands as
`docs/integration_testing_architecture.md` without forking the pack or the
client. Validators key off document **content**, so renaming never changes
whether a deliverable passes.

| Role | Pack | Default destination |
| --- | --- | --- |
| `architecture_document` | `technical_plan` | `docs/ARCHITECTURE.md` |
| `evidence_report` | `repository_investigation` | `docs/EVIDENCE_REPORT.md` |
| `test_plan` | `quality_gate` | `docs/TEST_PLAN.md` |
| `quality_findings` | `quality_gate` | `docs/QUALITY_FINDINGS.md` |
| `security_evidence` | `quality_gate` | `docs/SECURITY_EVIDENCE.md` |
| `proposed_patch` | `repository_change` | applied via `approve --apply` (never copied, never renamed) |

Overrides are accepted on submit as `artifact_overrides`, either as a
destination shorthand or with an explicit produced name:

```json
{
  "artifact_overrides": {
    "architecture_document": { "dest_path": "docs/integration_testing_architecture.md" },
    "quality_findings": { "logical_name": "release_readiness.md",
                          "dest_path": "docs/qa/release_readiness.md" }
  }
}
```

An unknown role or an unsafe destination (absolute, `~`, or `..`-escaping) is
rejected as `invalid_artifact_override` at submit time, before any run spend.
Roles left un-overridden keep their pack defaults.

`inspect` and `artifacts` expose the resolved map as `artifact_land_map`, with
`role`, `logical_name`, and `suggested_dest_path` per entry, so a host lands
files where the run says rather than guessing from the workflow id.

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
| `POST` | `/api/v1/runs/{id}/materialize` | `{ "artifact", "dest_path", "overwrite?" }` |
| `POST` | `/api/v1/runs/{id}/materialize-all` | `{ "roles?", "overwrite?" }` — lands the land map |
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
| `pf_materialize` | `host.materialize` (`artifact`, `dest_path`, `overwrite?`) |
| `pf_materialize_all` | `host.materialize_all` (`roles?`, `overwrite?`) |

`revise` is available on the CLI and HTTP control API only (kept out of the
small MCP tool set to limit context bloat); use `product-factory host revise`
or `POST …/revise` when needed.

Each tool returns `product-factory.host/v1` `HostResponse` JSON (also as MCP
`structuredContent` when the client supports it).

| Host | Consumption path |
| --- | --- |
| Scripts / CI | `product-factory host …` JSON or HTTP control API |
| HTTP clients | Control routes on observe/`serve` (this section) |
| OpenCode | **Recommended:** plugin below. Alternative: MCP + slash commands — [`examples/opencode/`](../examples/opencode/) |
| Cursor / Claude Code | Same `product-factory mcp` stdio config (no OpenCode files required) |

### OpenCode plugin (recommended)

A thin plugin package at [`integrations/opencode-plugin/`](../integrations/opencode-plugin/)
removes the need for slash commands: it exposes model-facing `pf_run` /
`pf_wait` / `pf_review` / `pf_merge` / `pf_decline` tools over a `PfClient` that
calls this same host CLI JSON surface, and asks the user for confirmation
before any merge/land step. It is optional packaging — MCP and
`product-factory.host/v1` remain the source of truth, and no PF orchestration
logic lives in the plugin.

Install / env / UAT: see the package README. Gated OpenCode reality smoke:
`scripts/opencode_plugin_smoke.sh` (wired into `scripts/verify.sh`; skips when
`opencode` is absent unless `OPENCODE_INTEGRATION=1`). Tracker:
[`next-work-packages-phase3g.md`](next-work-packages-phase3g.md).

### MCP + slash commands (non-plugin hosts)

Merge `examples/opencode/opencode.json`, enable the `product-factory` MCP
server, then try `/pf-investigate …` → `/pf-status` → `/pf-approve`.

When the OpenCode working directory is not the Product Factory repo, launch MCP
with an absolute command — either the venv binary, or
`uv --directory /path/to/orchestration run product-factory mcp`. Do not use
`uv -C …` (unsupported; the process exits and OpenCode appears stuck). Current
`product-factory mcp` speaks NDJSON on stdio by default (mirrors
`Content-Length` when the client sends it) and resolves config via
`PRODUCT_FACTORY_ROOT` / the install tree when cwd has no PF config.

## Workflow packs (host-facing)

| `workflow_type` | Notes |
| --- | --- |
| `code_change` / `repository_change` | Diff + approval (Phase 1 pack) |
| `repository_investigation` | Read-only evidence report; no write grants by default |
| `technical_plan` | Requirements / architecture decision / acceptance criteria |
| `architecture` | Alias → `technical_plan` (compat) |
| `quality_gate` | Read-only quality review; three deliverables, reports defects without repairing them |

```bash
product-factory host submit --workflow repository_investigation \
  --request request.md --repo ./repo --mock
product-factory host submit --workflow technical_plan \
  --request request.md --mock
product-factory host submit --workflow quality_gate \
  --request request.md --repo ./repo --mock
```

`quality_gate` produces a test plan, quality findings, and security evidence in
one run. A blocking finding is the *product* of that run, not a failure: the pack
holds no write grants, so it never spawns a repair task and the run still
completes. Land all three with a single `materialize-all`.

## Connectors (external tools)

Workers reach external providers only through the tool broker, so a connector
never grants a capability. Everything ships disabled: an operator must enable it
in [`config/connectors.yaml`](../config/connectors.yaml) **and** the workflow pack
must request the matching tool class. Every call is audited
(`connector.invoked` / `connector.denied` / `connector.failed`) with a policy
decision, bounded result, and provenance. Outages surface as typed connector
errors and never silently fall back to another model.

| Connector | Tool class | Needs |
| --- | --- | --- |
| `tavily_web_search` | `web_read` | `TAVILY_API_KEY`; egress to `api.tavily.com` |
| `filesystem_mcp` | `mcp_filesystem_read` | Explicit read roots; `npx` for the MCP server |

Read-only in both cases. `filesystem_mcp` resolves and confines every path
against its configured roots before calling the server, and allowlists three read
tools regardless of what the server advertises. Example config:
[`examples/connectors/filesystem-mcp.yaml`](../examples/connectors/filesystem-mcp.yaml).

## Environment

| Variable | Role |
| --- | --- |
| `PRODUCT_FACTORY_OBSERVE_URL` | Base URL for SSE subscription hints (default `http://127.0.0.1:8765`) |
| `PRODUCT_FACTORY_FORCE_MOCK` | Force mock gateway (also `--mock` on submit/worker) |
| `PRODUCT_FACTORY_ROOT` | Config checkout when cwd is not the PF tree (honored by `host` CLI and MCP) |
| `PRODUCT_FACTORY_DATA_DIR` | Override run/DB root (`.product-factory`) |
| `PRODUCT_FACTORY_BIN` | OpenCode plugin: absolute path to the `product-factory` executable |
| `OPENROUTER_API_KEY` | Live model backend when mock is not forced |
| `PRODUCT_FACTORY_OBSERVE_TOKEN` | When set, HTTP write routes require `Authorization: Bearer …` |
| `OPENCODE_INTEGRATION` | When `1`, `scripts/opencode_plugin_smoke.sh` fails if `opencode` is missing |
| `TAVILY_API_KEY` | Credential for the Tavily web-search connector (never logged) |
| `TAVILY_INTEGRATION` | When `1`, `verify.sh` runs the live Tavily query |
| `MCP_FILESYSTEM_INTEGRATION` | When `1`, `verify.sh` runs the real filesystem MCP server smoke |
