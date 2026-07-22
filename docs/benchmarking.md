# Benchmarking with the LLM-judge harness

This document describes the extensible evaluation infrastructure for comparing orchestration against baselines and scoring agents in isolation.

## Quick start

```bash
# Offline smoke (MockJudge)
product-factory bench run \
  --suite local \
  --subjects full_orchestration,single_agent_baseline,agent_isolation \
  --limit 5 \
  --mock

product-factory bench compare bench-<id>
product-factory bench lessons bench-<id>
```

Live judge (OpenRouter):

```bash
export OPENROUTER_API_KEY=...
product-factory bench run --live --judge frontier_oracle --oracle-budget-usd 5.00 --limit 3
```

## Subjects

| Subject | Purpose |
| --- | --- |
| `full_orchestration` | Multi-agent Product Factory path |
| `single_agent_baseline` | One model, one pass (no orchestration) |
| `agent_isolation` | Single capability with fixed TaskSpec |
| `frontier_reference` | Frontier model reference (oracle budget) |

## Scoring

1. Deterministic validators (patch apply, secrets, architecture sections, expected files).
2. LLM judge rubric (1–5 dimensions from handover §28.5).
3. Hard deterministic failure caps quality regardless of judge generosity.
4. `quality_efficiency = normalized_quality / max(subject_cost, floor)`.

## Fine-tuning loop (human-gated)

```text
bench scores → lesson candidates → human review → skill/prompt draft → held-out re-bench → versioned activation
```

Candidates live in `.product-factory/lessons/candidates/<bench-id>/`. Nothing is auto-promoted into `skills/`.

## Extending to public suites

Implement `CaseLoader` in `product_factory.evaluation.adapters.base` to map DeepSWE / SWE Atlas records into `EvalCase`. The judge and comparison layer stay unchanged. See `ExternalSuiteCaseLoader` as a stub.

## Related

- [ADR-005](architecture/ADR-005-llm-judge-harness.md)
- Config: `config/benchmarks.yaml`
- Cases: `tests/eval_cases/`
