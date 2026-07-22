# ADR-005 — LLM-judge evaluation harness

## Status

Accepted

## Decision

Extend Product Factory evaluation with an LLM-judge benchmark harness that:

1. Runs multiple **subjects** (`full_orchestration`, `single_agent_baseline`, `agent_isolation`, `frontier_reference`).
2. Applies **deterministic validators first**, then an LLM judge for semantic rubric dimensions.
3. Separates **oracle/judge cost** from subject cost.
4. Exports **human-gated lesson candidates** for prompt/skill improvement.

## Rationale

Unit/integration tests cannot measure whether orchestration beats a frontier or single-model baseline. A judge-first harness enables fair comparisons and a fine-tuning *data* loop without automatic skill promotion (ADR-007).

## Consequences

- Default judge profile is `frontier_oracle`; CI uses `MockJudge`.
- Public suites (DeepSWE, SWE Atlas) integrate via `CaseLoader` adapters mapping into `EvalCase`.
- Lesson candidates are written under `.product-factory/lessons/candidates/` with status `proposed` only.
