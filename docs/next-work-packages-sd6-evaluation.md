# SD6 — Real evaluation and promotion

**Status:** `[ ]` planned. **Gate:** G4. **Findings:** F-19, F-20.  
**Depends on:** G1 for promotion runs; fixtures may be prepared earlier. **Purpose:** measure whether local-first orchestration and skills provide real, safe, economical value.

## Corpus and provenance

- [ ] Create twelve sanitized real cases: two each for discovery, technical planning, repository change, quality, release, and operations.
- [ ] Run one seed while the harness stabilizes.
- [ ] Before promoting defaults, expand to at least thirty cases and three seeds.
- [ ] Persist corpus hashes, harness/version/configuration, scorecards, reviewer decisions, and promotion/rollback records durably.
- [ ] Record AMD runtime/model/profile versions, admission capabilities, queue time, saturation, fallback reasons, and local cost estimate.

Sanitization must remove secrets, personal data, and private source material not approved for the evaluation store. A corpus case must preserve the constraints needed to assess correctness rather than becoming an unconstrained synthetic prompt.

## Comparison arms

- [ ] local-only.
- [ ] local-first with bounded cloud fallback.
- [ ] cloud orchestration.
- [ ] comparable single-agent baseline.
- [ ] skills enabled and skills disabled.

Keep prompt/task/evidence conditions equivalent where the arm definition allows it. Pre-register exclusions, timeouts, reviewer rubric, cost attribution, and fallback treatment before score review.

## Promotion gates

For a local-first default, require all of the following:

- [ ] zero policy violations;
- [ ] accepted-outcome/validator rate no more than five percentage points below the cloud arm;
- [ ] human correction effort no more than 10% worse;
- [ ] unsupported-claim rate no more than two percentage points worse;
- [ ] at least 30% lower cloud spend; and
- [ ] documented latency trade-off with no unresolved timeout/reliability regression.

For an individual skill, also require either a five-point quality improvement or a 10% correction-effort reduction, no policy regression, and no more than a 20% cost/latency increase.

No aggregate score may mask a safety violation, a materially weaker workflow category, or an arm whose fallback policy differs from the pre-registered protocol. A failed promotion is evidence to tune/reject a default, not a reason to rewrite the rubric after the fact.

## External benchmarks

- [ ] Implement SWE Atlas as the first external adapter, with durable adapter/version/case mapping records.
- [ ] Add a small official Terminal-Bench/Harbor subset next, subject to its rules and infrastructure requirements.
- [ ] Treat DeepSWE as a later compatibility and licensing gate, not a promised immediate dependency.

External benchmark results remain separate from the product corpus; report scope, license, environment, harness revision, and any non-comparable protocol differences.

## G4 exit checklist

- [ ] At least thirty sanitized cases, three seeds, and comparison arms complete for the proposed default.
- [ ] Human review, validator receipts, spend, latency, queue/fallback/saturation, and policy outcomes are durable and auditable.
- [ ] An AMD-owned run and at least one external-suite subset are linked from the scorecard.
- [ ] Promotion, rollback, or non-promotion decision is explicit and reproducible.
- [ ] Master tracker contains all four evidence levels; operational proof is not inferred from a synthetic harness.

## Must not

Do not route production customer data into the corpus, use secret-bearing PR jobs, claim a benchmark supports a different task class than it does, or optimize for a score while weakening policy/traceability.
