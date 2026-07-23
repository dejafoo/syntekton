# Product Factory

Multi-agent product factory MVP — typed task-graph orchestration with LangGraph, OpenRouter gateway, Git worktree isolation, and deterministic validation.

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Git

## Setup

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
```

## CLI

```bash
product-factory --help
product-factory doctor
product-factory run --request request.md --repo ./tests/fixtures/sample_api --workflow code_change --mock
product-factory eval --limit 10 --mock
```

Set `OPENROUTER_API_KEY` for live model calls. Use `--mock` or `PRODUCT_FACTORY_FORCE_MOCK=1` for offline runs.

## Observability

```bash
uv sync --extra observability
product-factory observe serve --host 127.0.0.1 --port 8765
```

Read-only REST + WebSocket/SSE over the same SQLite event store used by CLI runs. See [Observability](docs/observability.md).

## Docs

- [Architecture](docs/architecture.md) — system design, run lifecycle, security, evaluation
- [Codebase structure](docs/codebase-structure.md) — package map and “where to edit what”
- [Observability API](docs/observability.md)
- [Implementation handover](docs/handover.md)
- [Implementation plan & tasks](docs/implementation-plan.md)
- [LLM-judge benchmarking](docs/benchmarking.md)
- [ADRs](docs/architecture/)
- [Orchestration performance plan](docs/orchestration-performance-plan.md)
- [Next work packages 1–6](docs/next-work-packages-1-6.md) — post–Stage-B/C implementation plan

## Benchmarks

```bash
product-factory bench run --subjects full_orchestration,single_agent_baseline --limit 5 --mock
```

## Verify

```bash
./scripts/verify.sh
```
