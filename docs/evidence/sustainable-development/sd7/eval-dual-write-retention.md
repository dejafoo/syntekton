# Eval dual-write retention (SD7)

## Status

**Retain** the compatibility dual-write from `evaluation_scores` →
`evaluation_runs` in `EvaluationRepository.record_score`.

## Why not removed

SD3 required an export/reader path before retiring dual writes. As of SD7:

- `host/export.py` exports run evidence bundles, not evaluation table migration.
- No verified reader reconstructs scorecards solely from `evaluation_scores`
  for historical DBs that only populated `evaluation_runs`.
- Removing the dual-write on source-search alone would violate the SD7 removal
  rule (replacement + compatibility evidence required).

## Replacement path (future)

1. Implement/verify an export reader for `evaluation_runs` → modern score tables.
2. Migrate retained operator DBs.
3. Delete dual-write + optionally freeze/drop `evaluation_runs` with a migration.
4. Update this note and flip the SD3/SD7 checklist items with evidence.

## Authority today

`evaluation_scores` is the preferred durable score store; `evaluation_runs`
remains a compatibility mirror only.
