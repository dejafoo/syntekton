# SR6 — Real local-first evaluation (first slice)

**Package:** SR6  
**Findings:** RF-09 (partial — stabilization only)  
**Evidence level:** hermetically verified (stabilization corpus + promotion fail-closed)  
**AMD / G5 operational proof:** deferred — not claimed in this package

## Placement note

```text
Concern: evaluation
Owning boundary: product_factory.evaluation (corpus, promotion, experiments)
Authoritative source: config/evaluation/sd6_promotion.yaml + tests/eval_cases/sd6_*.yaml
Compatibility: reuses SD6 twelve-case foundation as sr6-stabilization; no default promotion
Guardrail proof: tests/unit/test_sr6_evaluation_stabilization.py
Temporary exception: none; AMD operational runs explicitly deferred
```

## What this slice proves

| Slice | Status | Notes |
| --- | --- | --- |
| SR6.A Stabilization corpus | hermetic scaffold complete | 12 sanitized cases (2× discovery / plan / repo / quality / release / ops), **one seed** |
| Evidence-level labeling | implemented | `mock` / `hermetic` / `integration` / `operational` |
| Promotion fail-closed | implemented | mock/hermetic/integration **cannot** promote defaults |
| SR6.B Promotion corpus (30×3) | deferred | requires AMD-owned multi-seed runs |
| SR6.C External suites live | deferred | SWE Atlas loader exists; no live suite claim |
| SR6.D Promote/retain/rollback from AMD | deferred | G5 exit criteria unmet |

## Honest decision

**Decision: deferred (no promote).**

This evidence is **hermetic / stabilization only**. It stabilizes harness identity, corpus hashing, scorecard labeling, and promotion gates. It does **not** establish the local-first quality, cost, latency, or affordability thesis on the target AMD environment.

Do not treat mock judges, deterministic fixtures, or one-seed hermetic scorecards as promotion authority.

## Corpus

- Corpus id: `sr6-stabilization`
- Case ids: `SR6_STABILIZATION_CASE_IDS` (alias of `SD6_FOUNDATION_CASE_IDS`)
- Case files: `tests/eval_cases/sd6_*.yaml`
- Catalog builder: `build_sr6_stabilization_catalog`
- Seed policy: **1** (stabilization); `may_promote: false` in `config/evaluation/sd6_promotion.yaml`

## Commands

```text
uv run pytest -q tests/unit/test_sr6_evaluation_stabilization.py tests/unit/test_sd6_evaluation.py
```

Result: [pytest-sr6-focused.txt](pytest-sr6-focused.txt) (15 passed). Tip commit: [base_commit.txt](base_commit.txt).

## Known limitations

- No live AMD runtime, model profile, saturation, queue, or spend receipts.
- No 30-case × 3-seed promotion corpus.
- No operational promote / retain / rollback decision for defaults or skills.
- External Terminal-Bench / DeepSWE subsets remain next/deferred.
