# Next work packages — MVP quality closure

Tracks the pre–post-MVP quality package: human-gated lesson loop, review
evidence hardening, architecture soft-matching, and the first curated promotion
cycle. Continues
[`orchestration-performance-plan.md`](orchestration-performance-plan.md) and
[`next-work-packages-1-6.md`](next-work-packages-1-6.md).

**Status legend:** `[ ]` pending · `[~]` in progress · `[x]` done

## Evidence

| Gate | Bench ID | Result |
| --- | --- | --- |
| N6 Stage D (before) | `bench-2e57f250a05f` | orch arch usable **77.8%** (7/9) |
| Q3 held-out (after promotion) | `bench-36949225fc11` | orch usable **77.8%** (7/9); saas/secure 100%; multitenant 33% (stable vs N6) |
| Q2 seeded review (mock) | unit/graph | correctness detected; style-only not blocking |
| Q2 seeded review (live smoke) | `bench-d6e9e333d7f8` | **2/2** correctness defects → blocking findings citing seed paths; usable 0% expected (repair off) |

## Workstreams

### Q1 — Lesson loop

- [x] Lesson model: `theme`, `actionable`, `source_bench_id`, status transitions
- [x] CLI: `product-factory lessons list|summarize|accept|reject|promote`
- [x] Orch-only default filter; bulk `--filter baseline`
- [x] Promote bumps skill `manifest.yaml` versions + writes promotions ledger
- [x] ADR-007 preserved (no automatic skill text injection)

### Q2 — Review evidence

- [x] `orchestration/review_findings.py` parse/demotion/validate helpers
- [x] Coordinator uses helpers; `validate_review_findings` after review
- [x] `seeded_review` subject + mock graph tests
- [x] Live review smoke (`bench-d6e9e333d7f8`, 2 cases × 1 seed; detection 2/2)
- [~] WP8 80%/20% live rates still deferred pending larger live slice
      (smoke detection 100% on n=2 correctness seeds; no style-only live cell)

### Q3 — First promotion cycle

- [x] Accepted orch `arch_multitenant` lessons from `bench-2e57f250a05f`
- [x] Rejected baseline noise on that bench
- [x] Human-authored skill + soft-match validator edits promoted
  (`architecture.system-design@1.0.1`, `quality.patch-review@1.0.1`)
- [x] Held-out live re-bench (`bench-36949225fc11`)

### Q4 — Architecture soft-matching

- [x] Soft section heading match
- [x] Soft must-cover (synonyms / significant tokens)
- [x] Unit tests

### Q5 — Docs

- [x] This tracker
- [x] `benchmarking.md` lesson CLI section
- [x] Stale tool-loop limitation removed from MVP plan
- [x] WP8 checklist progressed for evidence/seeded mock gates

## Exit

- [x] Lesson statuses leave `proposed`; promote writes ledger
- [x] CLI summarize defaults to orch-only
- [x] Mock seeded review detection/false-block tests green
- [x] Real promotion cycle with before/after bench ids
      (`bench-2e57f250a05f` → `bench-36949225fc11`)
- [x] Architecture soft-match tests green
- [x] Stale tool-loop limitation removed
