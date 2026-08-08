# Product Factory

Multi-agent product factory MVP — typed task-graph orchestration with an OpenRouter
gateway, Git worktree isolation, bounded tool-using workers, deterministic
validation/repair, and an LLM-judge evaluation harness.

Workflows:

- `code_change` / `repository_change` — proposed unified diff (approval before apply)
- `repository_investigation` — read-only evidence report with cited paths
- `technical_plan` — requirements / architecture decision / acceptance criteria (`architecture` aliases here)
- `quality_gate` — read-only test plan, quality findings, and security evidence

Deliverable filenames are not fixed: each workflow declares artifact *roles*, and
a host can name any of them per run (e.g. land an architecture document as
`docs/integration_testing_architecture.md`).

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ / npm (to build the bundled observability dashboard)
- Git

## Setup

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
```

`bootstrap.sh` runs `uv sync --frozen --extra dev` when `uv.lock` is present, then
`npm ci` + dashboard build. Supported CI and local verify paths use the same
frozen install (`uv sync --frozen`) and `npm ci` for dashboard / OpenCode plugin.

## CLI

```bash
product-factory --help
product-factory doctor
product-factory run --request request.md --repo ./tests/fixtures/sample_api \
  --workflow code_change --mock
product-factory run --request request.md --workflow technical_plan --mock
product-factory eval --limit 10 --mock
```

Machine hosts (async JSON, HTTP control, MCP, optional OpenCode plugin):

```bash
product-factory host submit --request request.md --repo ./repo --mock
product-factory host status <run_id>
product-factory host approve <run_id>
# Land a doc/report artifact into the target repo (after approval):
product-factory host materialize <run_id> \
  --artifact ARCHITECTURE.md --to docs/ARCHITECTURE.md
# Or name the deliverable up front and land everything the run produced:
product-factory host submit --request request.md --workflow technical_plan \
  --artifact-override architecture_document=docs/integration_testing_architecture.md
product-factory host materialize-all <run_id>
product-factory mcp --mock   # stdio MCP for OpenCode / Cursor / Claude Code
```

For OpenCode, prefer the in-repo plugin
([`integrations/opencode-plugin/`](integrations/opencode-plugin/)) —
`pf_run` / `pf_wait` / `pf_review` / `pf_merge` / `pf_decline`, with an
operator confirmation before any write. MCP + slash commands remain for
non-plugin hosts ([`examples/opencode/`](examples/opencode/)).

See [Host integration](docs/host-integration.md).

Set `OPENROUTER_API_KEY` for live model calls. Use `--mock` or
`PRODUCT_FACTORY_FORCE_MOCK=1` for offline runs.

Set `TAVILY_API_KEY` for live web search (`tavily_web_search` is enabled in
`config/connectors.yaml`). Put the key in the environment only — never in YAML.
Mock mode does not need it.

### Budgets, validation commands, and resume (Phase 1)

```bash
# Global budgets: cost (existing) + wall-clock; every model/tool/command call
# is checked against the run's BudgetLedger before it executes.
product-factory run --request request.md --repo . \
  --budget-usd 2.00 --max-wall-clock-seconds 900

# Behavioral validation runs registered commands from policies.yaml through
# the sandbox (tools/sandbox.py) — never a raw host shell. Unknown ids fail
# closed rather than silently skipping.
product-factory run --request request.md --repo . \
  --validation-command python_tests --validation-command python_typecheck
product-factory run --request request.md --repo . \
  --validation-commands python_tests,python_typecheck \
  --policy ./custom-policies.yaml   # optional registered_commands override

# Resume an interrupted run from SQLite + the run dir on disk (coordinator
# skips already-completed tasks and retries a crashed task once).
product-factory resume run-<id>
```

See [Sandbox and durable resume](docs/architecture/sandbox-and-resume.md) and
[Phase 1 execution kernel tracker](docs/next-work-packages-phase1.md) for the
full design and test evidence.

## Benchmarks

Compare orchestration against baselines (and optional frontier / ablations):

```bash
product-factory bench run --subjects full_orchestration,single_agent_baseline \
  --limit 5 --mock
product-factory bench compare bench-<id>
```

Live (OpenRouter):

```bash
unset PRODUCT_FACTORY_FORCE_MOCK
product-factory bench run --live \
  --subjects full_orchestration,single_agent_baseline \
  --limit 5 --seeds 3
```

### Human-gated lessons (ADR-007)

Bench runs export lesson candidates. Nothing is auto-promoted into `skills/`.

```bash
product-factory lessons summarize --bench bench-<id>    # orch-only by default
product-factory lessons list --bench bench-<id> --orch-only
product-factory lessons accept <lesson-id> --bench bench-<id> --note "..."
product-factory lessons reject --bench bench-<id> --filter baseline
# After human-authored skill/prompt edits:
product-factory lessons promote \
  --bench bench-<id> \
  --lesson-ids lesson-... \
  --files skills/architecture/system-design/SKILL.md \
  --bump-skill architecture.system-design \
  --note "curated promotion"
```

See [LLM-judge benchmarking](docs/benchmarking.md) and
[MVP quality closure](docs/next-work-packages-quality.md).

## Observability

```bash
uv sync --extra observability
product-factory observe serve --host 127.0.0.1 --port 8765
```

Open [http://127.0.0.1:8765/dashboard/](http://127.0.0.1:8765/dashboard/) for the bundled, monitor-only run dashboard. It uses the same REST + SSE event store as CLI runs; mutations go through `product-factory host …`, HTTP control routes on serve, or MCP — not the dashboard.
See [Observability](docs/observability.md) and [Host integration](docs/host-integration.md).

## Docs

- [Architecture](docs/architecture.md) — system design, run lifecycle, security, evaluation
- [Codebase structure](docs/codebase-structure.md) — package map and “where to edit what”
- [Observability API](docs/observability.md)
- [Local dashboard operator guide](docs/dashboard.md)
- [Implementation handover](docs/handover.md)
- [Implementation plan & tasks](docs/implementation-plan.md)
- [LLM-judge benchmarking](docs/benchmarking.md)
- [ADRs](docs/architecture/)
- [Orchestration performance plan](docs/orchestration-performance-plan.md)
- [Next work packages 1–6](docs/next-work-packages-1-6.md) — Stage B–F gates (closed)
- [MVP quality closure](docs/next-work-packages-quality.md) — lesson loop, review evidence, soft arch matching
- [Phase 1 execution kernel](docs/next-work-packages-phase1.md) — budgets, resume, sandbox, concurrency, workflow packs
- [Phase 3 host integration](docs/next-work-packages-phase3.md) — JSON host protocol, control API, OpenCode MCP, investigation/plan packs
- [Phase 3.G materialize + OpenCode plugin](docs/next-work-packages-phase3g.md) — `materialize` host action + optional OpenCode plugin (exit criteria met)
- [Phase 4 connectors, `quality_gate`, named deliverables](docs/next-work-packages-phase4.md) — artifact land map, connector policy layer, read-only Tavily + filesystem MCP (exit criteria met)
- [Host integration protocol](docs/host-integration.md) — CLI + HTTP + MCP for OpenCode / Cursor / scripts
- [OpenCode plugin](integrations/opencode-plugin/) — recommended OpenCode packaging (`pf_run` … `pf_merge`)
- [Sandbox and durable resume design](docs/architecture/sandbox-and-resume.md)

## Verify

```bash
./scripts/verify.sh
```

Always-on: dashboard check/build, ruff, basedpyright, unit/contract tests
(excluding `@integration`), and the connector policy / injection / audit suites
(offline — connectors are disabled by default and providers run in mock mode).
Optionally ends with `scripts/opencode_plugin_smoke.sh`, which skips when
`opencode` is not on `PATH` unless `OPENCODE_INTEGRATION=1` is set.

Opt-in smokes that need credentials or network egress:
`TAVILY_INTEGRATION=1` (needs `TAVILY_API_KEY`) and
`MCP_FILESYSTEM_INTEGRATION=1` (needs `npx`).
