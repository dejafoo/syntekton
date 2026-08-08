# SD6 — Real evaluation foundation (evidence)

**Branch:** `sd/sd6-evaluation`  
**Gate:** G4 (operational proof **deferred**)  
**Base:** `sd/g3-platform` @ `07ec410fea7d16864a8ec782d8c2a617f8e4495a`

## What landed (hermetic)

| Deliverable | Status | Location |
| --- | --- | --- |
| 12 sanitized foundation cases (2× discovery / plan / repo-change / quality / release / ops) | complete | `tests/eval_cases/sd6_*.yaml` |
| Corpus catalog + snapshot hashing (includes promotion config) | complete | `product_factory.evaluation.corpus` |
| Comparison arms + promotion thresholds as data | complete | `config/evaluation/sd6_promotion.yaml` |
| Local-first + skill promotion gate logic (fail closed / deferred) | complete | `product_factory.evaluation.promotion` |
| Harness / scorecard / promotion / rollback record shapes | complete | `product_factory.evaluation.experiments` |
| SWE Atlas CaseLoader + durable mapping records | minimal | `product_factory.evaluation.adapters.swe_atlas` |
| Terminal-Bench | next (not implemented) | noted in config + playbook |
| DeepSWE | deferred (not claimed) | noted in config + playbook |
| Hermetic sample scorecards + deferred promotion decision | complete | `sample-scorecards/`, `artifacts/promotion_decision.json` |

## Honest G4 decision

**Decision: `deferred` (no promote).**

Operational G4 exit criteria (30 sanitized cases × 3 seeds, AMD-owned live scorecards, human review, external-suite subset run) are **not** satisfied in this environment. Hermetic foundation is closed; defaults must not be promoted from mock scorecards.

See [artifacts/promotion_decision.json](artifacts/promotion_decision.json).

## Durable evidence paths

In-repo:

- `docs/evidence/sustainable-development/sd6/` (this folder)
- Sample registry shapes under `artifacts/product-factory-sample/` mirroring `.product-factory/{experiments,scorecards,harness,promotions,external-adapters}/`

Runtime (operator machines):

- `.product-factory/experiments/`
- `.product-factory/scorecards/`
- `.product-factory/harness/`
- `.product-factory/promotions/`
- `.product-factory/rollbacks/`
- `.product-factory/external-adapters/`

## Hermetic proof

| Check | Result | Artifact |
| --- | --- | --- |
| `uv run pytest -q tests/unit/test_sd6_evaluation.py tests/unit/test_pmx_corpus_gates.py` | **10 passed** | [pytest-sd6-unit.txt](pytest-sd6-unit.txt) |
| `uv run pytest -q -m "not integration"` | **1005 passed**, 3 skipped, 14 deselected | [pytest-not-integration.txt](pytest-not-integration.txt) |

Corpus identity at evidence generation:

- `corpus_id`: `sd6-foundation`
- `content_sha256`: see [artifacts/sd6_corpus_snapshot.json](artifacts/sd6_corpus_snapshot.json)

## Placement note

```text
Concern: policy | evaluation
Owning boundary: product_factory.evaluation (corpus, promotion, experiments, adapters.swe_atlas)
Authoritative source: config/evaluation/sd6_promotion.yaml + durable corpus hashes + promotion records
Compatibility: existing PMX corpus/gates retained; SD6 arms/promotion are additive
Guardrail proof: tests/unit/test_sd6_evaluation.py + docs/evidence/sustainable-development/sd6/
Temporary exception: none; G4 operational AMD proof explicitly deferred
```

## Required for true G4 later

1. Connect AMD OpenAI-compatible runtime; record admission/queue/saturation snapshots.
2. Expand corpus to ≥30 sanitized cases; run 3 seeds per arm.
3. Produce live scorecards for local-only, local-first+fallback, cloud, single-agent, skills on/off.
4. Record human correction effort and accept/reject decisions.
5. Link at least one SWE Atlas (or Terminal-Bench) subset live run.
6. Emit an explicit promote / no-promote decision from operational evidence (not hermetic mocks).
