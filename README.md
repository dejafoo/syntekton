# Product Factory

Multi-agent product factory — typed task-graph orchestration with durable SQLite
state, registry-backed capabilities/packs, a shared host application service
(`host/v1` compatibility + `host/v2`), Git worktree isolation, bounded
tool/connector execution, and hermetic evaluation scaffolding.

## Current surfaces (post-SD4)

| Surface | Role |
| --- | --- |
| `product-factory` CLI / `host` subcommands | Local mutations via `HostService` |
| HTTP `/api/v2` | Preferred typed control plane |
| HTTP `/api/v1` | Compatibility (deprecation window) |
| SSE event streams | Authenticated live updates (WebSocket removed) |
| MCP + OpenCode plugin | Machine hosts; monitor dashboard is loopback/read-oriented |
| Registry catalogs | Generated from trusted registries → [`docs/catalogs/`](docs/catalogs/) |

Authoritative policy and lifecycle state live in durable records and registries —
not in request-shaped fields, JSONL mirrors, or `config/workflows.yaml`
(retained as a non-authoritative bootstrap mirror only).

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ / npm (dashboard + OpenCode plugin)
- Git

## Setup

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
```

`bootstrap.sh` runs `uv sync --frozen --extra dev` when `uv.lock` is present, then
`npm ci` + dashboard build. Supported CI and local verify paths use the same
frozen install.

## CLI

```bash
product-factory --help
product-factory doctor
product-factory run --request request.md --repo ./tests/fixtures/sample_api \
  --workflow code_change --mock
product-factory host submit --request request.md --repo ./repo --mock
product-factory host status <run_id>
product-factory host approve <run_id>
product-factory mcp --mock
```

See [Host integration](docs/host-integration.md), [Architecture](docs/architecture.md),
[Contributing](CONTRIBUTING.md), and [Security](SECURITY.md).

Set `OPENROUTER_API_KEY` for live model calls. Use `--mock` or
`PRODUCT_FACTORY_FORCE_MOCK=1` for offline runs.

## Sustainable development

Program trackers and evidence live under
`docs/next-work-packages-sustainable-development.md` and
`docs/evidence/sustainable-development/`. SD6 G4 operational AMD proof and SD8
production tuning remain explicitly deferred where noted.
