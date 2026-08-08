---
name: evaluation-promotion-change
description: Design and validate Product Factory evaluation corpora, benchmark adapters, scorecards, model routes, local/cloud comparisons, skill or prompt promotion, and measured performance tuning. Use whenever changing evaluation, model defaults, fallback policy, promotion gates, benchmark claims, or optimization decisions.
---

# Evaluation and promotion change

Treat evaluation as product evidence, not a demo. Before changing promotion
gates or defaults, read `docs/next-work-packages-sd6-evaluation.md` and the SR6
section of `docs/next-work-packages-sustainable-remediation.md` completely.

## Rules

- Do not promote from mock, deterministic, hermetic, single-case, or cloud
  stand-in results. Label those runs by their actual evidence level.
- Version and hash corpora, seeds, adapters, rubrics, skill/prompt revisions,
  model profiles, runtime configuration, and scorecards.
- Record model, quantization, runtime, hardware, memory/saturation, queue time,
  provider time, fallback reason, token/cost basis, policy violations,
  unsupported claims, correction effort, quality, and reliability.
- Compare local-only, bounded local-first fallback, cloud orchestration,
  comparable single-agent, and relevant ablation arms through the same public
  application and policy path.
- Define gates before inspecting results. Do not tune scoring, exclude failures,
  or change the corpus to manufacture promotion.
- Require quality and policy non-regression for cost, latency, concurrency,
  context, prompt, model-route, cache, and performance changes.
- Treat retain, defer, and rollback as valid decisions. Preserve reviewer
  decisions and limitations durably.

## Required proof

Run the required case and seed count on the claimed environment. Store corpus
hashes, raw receipts, aggregate scorecards, confidence/variance, human-review
records, and an explicit promote/retain/defer/rollback decision. External
benchmark adapters must use the normal application service and execution
policy. State licensing and compatibility limits. Include the root `AGENTS.md`
placement note and link before/after evidence for every optimization.
