# SD6 — Real evaluation and promotion

**Status:** `[~]` hermetic foundation complete; **G4 operational proof deferred**. **Gate:** G4. **Findings:** F-19, F-20.  
**Depends on:** G1 for promotion runs; fixtures may be prepared earlier. **Purpose:** measure whether local-first orchestration and skills provide real, safe, economical value.  
**Evidence:** [`docs/evidence/sustainable-development/sd6/`](evidence/sustainable-development/sd6/).

## Corpus and provenance

- [x] Create twelve sanitized real cases: two each for discovery, technical planning, repository change, quality, release, and operations. (`tests/eval_cases/sd6_*.yaml`)
- [x] Run one seed while the harness stabilizes. (hermetic mock seed / sample scorecards under evidence)
- [ ] Before promoting defaults, expand to at least thirty cases and three seeds.
- [x] Persist corpus hashes, harness/version/configuration, scorecards, reviewer decisions, and promotion/rollback records durably. (shapes + hermetic samples; live AMD fields still empty)
- [ ] Record AMD runtime/model/profile versions, admission capabilities, queue time, saturation, fallback reasons, and local cost estimate.

Sanitization must remove secrets, personal data, and private source material not approved for the evaluation store. A corpus case must preserve the constraints needed to assess correctness rather than becoming an unconstrained synthetic prompt.

## Comparison arms

- [x] local-only. (encoded in `config/evaluation/sd6_promotion.yaml` + gate tests)
- [x] local-first with bounded cloud fallback.
- [x] cloud orchestration.
- [x] comparable single-agent baseline.
- [x] skills enabled and skills disabled.

Keep prompt/task/evidence conditions equivalent where the arm definition allows it. Pre-register exclusions, timeouts, reviewer rubric, cost attribution, and fallback treatment before score review.

## Promotion gates

For a local-first default, require all of the following (enforced by `evaluate_local_first_promotion`; operational runs still deferred):

- [x] zero policy violations;
- [x] accepted-outcome/validator rate no more than five percentage points below the cloud arm;
- [x] human correction effort no more than 10% worse;
- [x] unsupported-claim rate no more than two percentage points worse;
- [x] at least 30% lower cloud spend; and
- [x] documented latency trade-off with no unresolved timeout/reliability regression.

For an individual skill, also require either a five-point quality improvement or a 10% correction-effort reduction, no policy regression, and no more than a 20% cost/latency increase. (`evaluate_skill_promotion`)

No aggregate score may mask a safety violation, a materially weaker workflow category, or an arm whose fallback policy differs from the pre-registered protocol. A failed promotion is evidence to tune/reject a default, not a reason to rewrite the rubric after the fact.

## External benchmarks

- [x] Implement SWE Atlas as the first external adapter, with durable adapter/version/case mapping records. (minimal CaseLoader + mapping store; **no live suite claim**)
- [ ] Add a small official Terminal-Bench/Harbor subset next, subject to its rules and infrastructure requirements.
- [x] Treat DeepSWE as a later compatibility and licensing gate, not a promised immediate dependency.

External benchmark results remain separate from the product corpus; report scope, license, environment, harness revision, and any non-comparable protocol differences.

## G4 exit checklist

- [ ] At least thirty sanitized cases, three seeds, and comparison arms complete for the proposed default.
- [ ] Human review, validator receipts, spend, latency, queue/fallback/saturation, and policy outcomes are durable and auditable.
- [ ] An AMD-owned run and at least one external-suite subset are linked from the scorecard.
- [x] Promotion, rollback, or non-promotion decision is explicit and reproducible. (**decision: deferred** — see evidence `artifacts/promotion_decision.json`)
- [~] Master tracker contains hermetic evidence; operational proof is **not** inferred from the synthetic harness.

## Must not

Do not route production customer data into the corpus, use secret-bearing PR jobs, claim a benchmark supports a different task class than it does, or optimize for a score while weakening policy/traceability.
