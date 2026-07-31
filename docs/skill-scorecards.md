# Skill scorecards

Operator recipe for comparing orchestration **with skills** vs **without skills**
(G1 / PM1.A). Live numbers are recorded here by operators; CI stays mock-mode green.

## Metric columns

Record one row per **skill version × model profile** (and the matched no-skill baseline):

| Column | Meaning |
| --- | --- |
| `skill_id` / `skill_version` / `package_digest` | Skill package under test (empty for no-skill arm) |
| `model_profile` | Worker/supervisor profile used for the arm |
| `subject_id` | `orchestration_with_skills` or `orchestration_no_skills` |
| `structured_output_pass_rate` | Share of runs producing schema-valid task outputs |
| `validator_pass_rate` | Share passing deterministic validators / pack checks |
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
| `local_vs_frontier_routing_rate` | Share of calls on local vs frontier profiles |
| `reviewer_correction_effort` | Human edit/rework notes where measurable |
| `skills_disabled` | Manifest `omitted_context` contains `skills_disabled` for the no-skill arm |

Promotion requires a material improvement versus the no-skill baseline without a
safety, cost, or latency regression beyond the workflow’s stated threshold.

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

## Scorecard log (empty)

| Date | Skill | Version | Model profile | Subject | Notes / link to bench_id |
| --- | --- | --- | --- | --- | --- |
| — | — | — | — | — | Live scorecard runs are an operator action; none recorded yet. |
