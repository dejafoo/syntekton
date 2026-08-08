# Skill scorecards

Operator recipe for comparing orchestration **with skills** vs **without skills**
(G1 / PM1.A / SD6). Live numbers are recorded here by operators; CI stays mock-mode green.

## Corpus identity (PMX / SD6)

Hermetic fixture identity is computed by
`product_factory.evaluation.corpus.build_corpus_snapshot` over `tests/eval_cases`
(including the SD6 twelve-case foundation `sd6_*.yaml`), PM5 fixture slices,
pack/skill manifests, `config/connectors.yaml`, and
`config/evaluation/sd6_promotion.yaml`.

SD6 comparison arms and promotion thresholds are authoritative in
`config/evaluation/sd6_promotion.yaml` and enforced by
`evaluate_local_first_promotion` / `evaluate_skill_promotion`. Experiment
registration, harness manifests, scorecards, and promotion/rollback records live
under `.product-factory/` via `ExperimentRegistry`.

```bash
uv run pytest -q tests/unit/test_pmx_corpus_gates.py tests/unit/test_sd6_evaluation.py
```

Hermetic SD6 evidence (including an explicit **deferred** G4 promotion decision):
[`docs/evidence/sustainable-development/sd6/`](evidence/sustainable-development/sd6/).

Live scorecard numbers below remain an operator action; CI stays mock-mode green.
**Do not promote local-first defaults from hermetic mocks.**

## Metric columns

Record one row per **skill version × model profile** (and the matched no-skill baseline):

| Column | Meaning |
| --- | --- |
| `skill_id` / `skill_version` / `package_digest` | Skill package under test (empty for no-skill arm) |
| `model_profile` | Worker/supervisor profile used for the arm |
| `subject_id` | `orchestration_with_skills` or `orchestration_no_skills` |
| `comparison_arm` | `local_only` / `local_first_fallback` / `cloud` / `single_agent_baseline` / `skills_enabled` / `skills_disabled` |
| `structured_output_pass_rate` | Share of runs producing schema-valid task outputs |
| `validator_pass_rate` | Share passing deterministic validators / pack checks |
| `accepted_outcome_rate` | Share of accepted / usable outcomes |
| `quality_score` | Task-specific judge quality (normalized 0–1) |
| `evidence_coverage` | Must-cover / source-class coverage rate |
| `citation_accuracy` | Cited sources resolvable and correctly classed |
| `unsupported_claim_rate` | Fact-like claims without citation or label |
| `policy_violation_rate` | Source/tool/approval policy breaches |
| `denied_tool_attempt_rate` | Tool calls rejected by grant enforcement |
| `correct_unknown_escalation_rate` | Correct `unknown` / `needs_expert_review` / `insufficient_evidence` on sparse fixtures |
| `latency_ms` | End-to-end subject latency |
| `context_tokens` | Prompt package estimated tokens |
| `model_tool_cost_usd` | Model + tool spend for the subject |
| `cloud_spend_usd` | Cloud spend attributed to the arm |
| `human_correction_effort` | Human edit/rework notes where measurable |
| `local_vs_frontier_routing_rate` | Share of calls on local vs frontier profiles |
| `skills_disabled` | Manifest `omitted_context` contains `skills_disabled` for the no-skill arm |

Promotion requires playbook SD6 thresholds (local-first vs cloud; skill vs no-skill)
without a safety, cost, or latency regression beyond the configured limits.

## Bench command

```bash
product-factory bench run --cases tests/eval_cases \
  --subjects orchestration_with_skills,orchestration_no_skills \
  --seeds 3 --live
```

Mock / CI smoke (no live models):

```bash
product-factory bench run --cases tests/eval_cases \
  --subjects orchestration_with_skills,orchestration_no_skills \
  --seeds 1
```

Arm identity: `RunRequest.metadata["disable_skills"] == "true"` empties matched
skills and records `skills_disabled` on the prompt package
`omitted_context` so scorecards can prove which arm ran.

## Report locations

Under the product-factory data root (default `.product-factory/`):

| Artifact | Path |
| --- | --- |
| JSON report | `.product-factory/bench-reports/<bench_id>.json` |
| Markdown report | `.product-factory/bench-reports/<bench_id>.md` |
| Per-run worktrees | `.product-factory/benches/<bench_id>/` |
| Lesson candidates | `.product-factory/lessons/candidates/<bench_id>/` |
| Experiments | `.product-factory/experiments/` |
| Scorecards | `.product-factory/scorecards/` |
| Harness manifests | `.product-factory/harness/` |
| Promotion / rollback | `.product-factory/promotions/` / `.product-factory/rollbacks/` |
| External adapters | `.product-factory/external-adapters/` |

## Scorecard log

| Date | Skill | Version | Model profile | Subject | Notes / link to bench_id |
| --- | --- | --- | --- | --- | --- |
| 2026-08-08 | — | — | coding_worker | hermetic arms | SD6 hermetic sample scorecards only; G4 operational deferred. See `docs/evidence/sustainable-development/sd6/`. |
| — | — | — | — | — | Live AMD scorecard runs are an operator action; none recorded yet. |
