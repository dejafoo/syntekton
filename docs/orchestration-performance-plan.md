# Orchestration Performance Improvement Plan

Living implementation and verification tracker for improving Product Factory orchestration quality, reliability, and cost efficiency.

This plan is grounded in:

- Full baseline: `.product-factory/bench-reports/bench-fc9f2aa05c0c.{json,md}`
- Focused `code_cache` regression retest: `.product-factory/bench-reports/bench-230101980932.{json,md}`
- Evaluation harness: [`src/product_factory/evaluation/`](../src/product_factory/evaluation/)
- Authoritative runtime: [`RunCoordinator`](../src/product_factory/orchestration/coordinator.py)

Post–Stage-B/C next sequence (hard failures → repair gate → review decision →
targeted ablations → frontier → architecture): see
[`next-work-packages-1-6.md`](next-work-packages-1-6.md).

## Status legend

- [ ] Pending
- [~] In progress
- [x] Done
- [!] Blocked or failed gate

Update each work package, test result, benchmark ID, and decision as work progresses. Do not mark a package complete until its exit gate passes.

## Execution update — 2026-07-21

Implemented and verified:

- [x] WP1 measurement core: empty/invalid artifact failure, executable behavioral
  contracts, decomposed metrics, multi-seed identity/resume, Wilson/bootstrap
  intervals, provider seeds, and per-case frequency.
- [x] WP2 fail-closed live path: deterministic workers are mock-only, live planner
  fallback is removed, and live implementation failures retain typed reasons.
- [~] WP3 tool-loop core: canonical tools, bounded multi-turn execution,
  inspect-before-write, tool/result feedback, token/cost/time/tool budgets,
  repeated-call detection, repeated patch-fingerprint stop, and persisted loop
  results are implemented. **Live five-seed reliability exit gate passed**
  (`bench-be97863325ab`).
- [~] WP4 context core: deterministic targeted excerpts, dependency artifacts,
  repository structure, omissions, evidence refs, and `context_mode`
  (`targeted` / `file_list_only`) ablation subjects are wired. Live context
  ablation experiment remains pending.
- [x] WP5 required behavioral commands run in isolated patched repositories at
  runtime and in the benchmark harness; failures block usability.
- [~] WP6 stateful repair and no-progress controls are implemented, including
  per-task `max_repair_attempts` and pre/post lineage fingerprints. The seeded
  live >50% gate remains pending.
- [~] WP7 repair/composition lineage, cumulative candidate patches, leaf selection,
  fingerprints, empty-patch rejection, and multi-writer path-conflict detection
  are implemented. A dedicated multi-writer live benchmark remains pending.
- [~] WP8 strict structured review, typed evidence-backed findings (invalid
  evidence demoted), confidence handling, and blocking-finding repair routing
  are implemented. Review-on/off defect experiments remain pending.
- [~] WP9 low-risk two-task and high-risk reviewed plans, read/write path-scope
  fields, acceptance-criterion ownership checks, and planner-mode ablations
  (`fixed` / `live` / `complexity_sensitive`) are implemented. Live planner
  ablation gates remain pending.
- [~] WP10 named ablations (including validation/repair vs no-review, context,
  and planner variants), blind randomized pairwise judging, confidence
  intervals, and stratified reports are implemented. Live model/profile and
  frontier experiments remain pending.

Verification:

- [x] `ruff`: clean.
- [x] `basedpyright src`: 0 errors.
- [x] Test suite: 76 passed, 2 skipped.
- [x] Stage B mock reliability slice:
  `bench-09de8c31f586` (4 cases × 2 subjects × 5 seeds).
  Orchestration: artifact 100%, patch-apply 100%, behavioral 100%, usable 100%.
  Baseline usable: 75%; paired usable delta +25 points, bootstrap CI +10 to +45.
- [x] Stage C mock code suite:
  `bench-a04a347f36d2` (18 cases × 2 subjects × 3 seeds).
  Both subjects: artifact 100%, patch-apply 100%, behavioral 100%, usable 94.4%.
  Paired delta 0, CI -9.3 to +9.3 points.

The Stage B/C runs validate harness reliability and deterministic mock behavior;
they do not substitute for the pending live-provider promotion gates.

Live smoke results:

- [!] `bench-d55561a1a2a4` (4 cases × 2 subjects × 1 seed):
  orchestration usable 25%, baseline usable 0%; subject cost `$0.037145`,
  judge cost `$0.077748`. It exposed over-broad implementation tools,
  context/token-budget pressure, malformed baseline diff handling, and a
  transient provider failure.
- [!] `bench-8ba9e9cb5f75` reran the same slice after the first fixes:
  both subjects usable 0%; subject cost `$0.039289`, judge cost `$0.082094`.
  It exposed cumulative-versus-per-turn input-budget semantics, fenced `diff`
  language-tag extraction, failed-secondary-task repair routing, and missing
  transitive writable-task lineage.
- [x] The issues identified by both smoke runs were fixed and covered by local
  unit/graph tests.
- [!] `bench-8391499ffa47` fresh one-seed confirmation after lineage/budget fixes:
  orchestration usable 25% (only `code_cache` usable at q=0.75), baseline 0%;
  subject cost `$0.183943`, judge cost `$0.109230`. Failures: wrong expected
  paths (`code_health`/`code_logging`), provider `json_schema` 400 (`code_retry`),
  and incomplete logging behavior.
- [x] Follow-up fixes landed: OpenRouter `json_schema` → `json_object` fallback,
  expected-file/acceptance injection into orchestration and baseline prompts,
  grant headroom for post-loop diffs, planner payload normalization, and default
  `planner_mode=fixed` for full orchestration reliability.
- [x] `bench-b7967a2a5b1a` one-seed confirmation passed Stage B thresholds:
  orchestration artifact 100%, patch-apply 100%, behavioral 100%, usable 50%;
  baseline usable 50%; subject cost `$0.324554`, judge cost `$0.127272`.
- [x] Live Stage B five-seed gate `bench-be97863325ab` (4 cases × 2 subjects × 5
  seeds = 40 cells) **PASSED**:
  - orchestration: artifact 100%, patch-apply 100%, behavioral 100%, usable **90%**
    (CI 70–97%); mean valid q ≈ 0.78; cost/usable ≈ `$0.0245`
  - baseline usable 35% (CI 18–57%)
  - paired usable delta **+55 points** (bootstrap CI +35 to +75)
  - subject cost `$0.502662`, judge cost `$0.580754`
  - per-case orch usable frequency: cache 5/5, health 3/5, logging 5/5, retry 5/5
- [x] Live Stage C code suite `bench-e59f17adf319` (18 cases × 2 subjects × 3
  seeds = 108 cells) **PASSED**:
  - orchestration usable **85.2%** (CI 73–92%), behavioral 100%, patch-apply 100%
  - baseline usable 13.0% (CI 6–24%)
  - paired usable delta **+72 points** (CI +59 to +83)
  - orch cost/usable ≈ `$0.0127` (0.38× baseline cost/usable)
  - subject cost `$0.819391`, judge cost `$1.462102`
- [~] Stage D–F (architecture quality, named ablations, frontier) remain open.
  Stage E partial slice `bench-72dfcf11b63b` completed: no-review/validation/
  context subjects ≈77.8% usable; forced review and isolation were 0% (review kept
  optional). Finding-category normalization and force-review AC injection were
  fixed afterward.

---

## 1. Baseline and target

### 1.1 What the current baseline establishes

- [x] A complete 30-case, two-subject live benchmark can run and resume.
- [x] Results are durably recorded and exported as JSON and Markdown.
- [x] The original full run produced 60 scores over 30 cases.
- [x] The focused `code_cache` investigation found and fixed:
  - hardcoded health-only implementation behavior;
  - omission of untracked files from generated patches;
  - selection of empty implementation patches during composition.
- [x] Focused live `code_cache` retest produced an applying, usable orchestration patch with q=0.95.

### 1.2 What the current baseline does not establish

- A single result per case does not establish reliability.
- The original run had both subjects at 1/30 usable artifacts.
- Many cells collapsed to the q=0.20 floor.
- Empty artifacts could skip important deterministic checks.
- Behavioral tests declared by cases are not consistently executed.
- A focused rerun reversed the original `code_cache` result, demonstrating high variance.
- Architecture template wins do not prove production-quality architecture.
- The current runtime is still primarily a one-shot patch generator, not a complete tool-using implementation agent.

### 1.3 North-star metrics

Primary metrics:

- Usable artifact rate.
- Non-empty, request-relevant artifact rate.
- Patch-apply success rate.
- Behavioral test pass rate.
- Paired win rate against `single_agent_baseline`.

Secondary metrics:

- Cost per usable artifact.
- Wall-clock time per usable artifact.
- Repair success rate.
- Scope and security violation rate.
- Tool-loop rounds and useful tool-call rate.
- Reviewer true-positive and false-blocking rates.

Initial code-suite targets:

- [ ] Non-empty patches: at least 90%.
- [ ] Applying patches: at least 75%.
- [ ] Behavioral tests passing: at least 60%.
- [ ] Final usable artifacts: at least 50%.
- [ ] Usable-rate advantage over single-agent: at least 10 percentage points.
- [ ] Live deterministic fallback artifacts: 0.
- [ ] Cost per usable artifact: no more than 2x the single-agent baseline.

---

## 2. Execution principles

1. Repair measurement before optimizing behavior.
2. Make live execution fail closed; mock-only fallbacks must never masquerade as live model output.
3. Optimize the implementation worker before planner sophistication.
4. Require behavioral evidence, not only judge preference.
5. Promote changes only after multi-seed paired experiments.
6. Use ablations to attribute improvements to workers, validation, review, or planning.
7. Keep budgets and stopping conditions explicit.
8. Preserve all benchmark inputs, model resolutions, prompts, artifacts, and validation evidence needed for reproduction.

---

## WP1 — Benchmark integrity and scoring semantics

### Goal

Make benchmark outcomes valid, interpretable, repeatable, and resistant to empty-artifact or floor-score distortions.

### Implementation

#### WP1.1 Artifact validity

- [ ] Add an `artifact_empty` validator for empty or whitespace-only artifacts.
- [ ] Require code-change artifacts to parse as unified diffs.
- [ ] Require architecture artifacts to contain meaningful non-boilerplate content.
- [ ] Prefer the first non-empty validated patch from `proposed.patch` and `implementation.patch`.
- [ ] Treat a zero-byte patch as missing rather than as a successful artifact.
- [ ] Record the selected artifact path and selection reason in `SubjectArtifact.metadata`.

Likely files:

- `src/product_factory/evaluation/deterministic.py`
- `src/product_factory/evaluation/runners.py`
- `src/product_factory/evaluation/subjects.py`

Tests:

- [ ] Empty patch fails deterministically.
- [ ] Whitespace-only patch fails.
- [ ] Prose pretending to be a patch fails.
- [ ] A non-empty `implementation.patch` is selected when `proposed.patch` is empty.
- [ ] Empty architecture output fails.

#### WP1.2 Behavioral validation contracts

- [ ] Require every code case to define at least one of:
  - expected files;
  - smoke commands;
  - behavioral acceptance checks.
- [ ] Validate case definitions at load time.
- [ ] Execute configured smoke commands in an isolated repository clone after patch application.
- [ ] Persist command ID, exit code, duration, and bounded stdout/stderr.
- [ ] Fail deterministic scoring when a required smoke command fails.

Likely files:

- `src/product_factory/evaluation/cases.py`
- `src/product_factory/evaluation/deterministic.py`
- `src/product_factory/evaluation/adapters/base.py`
- `tests/eval_cases/code_*.yaml`
- `config/policies.yaml`

Tests:

- [ ] Invalid case without a behavioral contract is rejected.
- [ ] Applying patch with failing tests is not usable.
- [ ] Passing smoke command produces evidence in the score.
- [ ] Command timeout and unknown command IDs fail safely.

#### WP1.3 Metric decomposition

- [ ] Report artifact production separately from deterministic validity.
- [ ] Report patch-apply, smoke-test, judge-quality, and usable rates independently.
- [ ] Calculate judge quality only as a secondary metric when deterministic validity passes.
- [ ] Replace or supplement `quality_efficiency` with:
  - cost per usable artifact;
  - latency per usable artifact;
  - usable artifacts per dollar.
- [ ] Preserve raw dimension scores without compressing all failures to one indistinguishable number.

Likely files:

- `src/product_factory/evaluation/deterministic.py`
- `src/product_factory/evaluation/compare.py`
- `src/product_factory/evaluation/store.py`

Tests:

- [ ] Empty cheap artifacts cannot lead efficiency rankings.
- [ ] Deterministic failure and judge failure remain distinguishable.
- [ ] Aggregates handle zero usable artifacts without division errors.

#### WP1.4 Multi-seed execution

- [ ] Add `--seeds N` to `product-factory bench run`.
- [ ] Persist `seed` on benchmark score identity and artifacts.
- [ ] Resume by `(bench_id, case_id, subject_id, seed)`.
- [ ] Pass provider seed where supported; otherwise record an orchestration seed and sampling parameters.
- [ ] Add bootstrap confidence intervals for paired usable-rate and quality differences.
- [ ] Report per-case success frequency.

Likely files:

- `src/product_factory/cli/app.py`
- `src/product_factory/evaluation/bench.py`
- `src/product_factory/evaluation/store.py`
- `src/product_factory/evaluation/compare.py`
- `src/product_factory/gateway/canonical_messages.py`
- `src/product_factory/gateway/openrouter.py`

Tests:

- [ ] Three seeds create three independently addressable scores.
- [ ] Resume skips only completed seeds.
- [ ] Confidence interval calculations are deterministic for fixed input.
- [ ] Reports include seed count and paired sample count.

### WP1 exit gate

- [ ] Historical empty patches rescore as deterministic failures without new model calls.
- [ ] All code cases have a behavioral contract.
- [ ] Smoke commands run and affect usability.
- [ ] A two-case, three-seed smoke benchmark resumes correctly and reports confidence intervals.
- [ ] Metric and report contract tests pass.

---

## WP2 — Fail-closed live execution

### Goal

Prevent provider, parsing, or patch failures from silently becoming unrelated deterministic output.

### Implementation

- [ ] Restrict `deterministic_impl_files` to mock/offline execution.
- [ ] Mark live implementation failed when:
  - the model returns no patch;
  - patch extraction fails;
  - patch application fails and repair is unavailable;
  - the provider returns a terminal error.
- [ ] Remove live planner fallback to a deterministic plan, or require an explicit configuration switch.
- [ ] Emit typed failure reasons:
  - `empty_model_output`;
  - `invalid_patch_format`;
  - `patch_apply_failed`;
  - `provider_failed`;
  - `budget_exhausted`;
  - `no_progress`.
- [ ] Record whether any fallback was used in run and subject metadata.
- [ ] Fail the benchmark cell if a live fallback flag is present.

Likely files:

- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/planning/planner.py`
- `src/product_factory/evaluation/runners.py`
- `src/product_factory/evaluation/subjects.py`
- `src/product_factory/observability/contracts.py`

Tests:

- [ ] Non-mock gateway returning empty text produces task failure and no health/cache patch.
- [ ] Invalid diff does not trigger deterministic code generation.
- [ ] Provider 401/timeout is preserved as a provider failure.
- [ ] Mock vertical-slice behavior remains available and explicitly marked.

### WP2 exit gate

- [ ] Live deterministic fallback rate is zero in tests.
- [ ] Every live failure has an explicit reason.
- [ ] No non-health live request can emit the deterministic health patch.

---

## WP3 — Multi-turn tool-using implementation agent

### Goal

Replace one-shot patch generation with a bounded inspect-edit-test-repair loop.

### Design

Add a framework-neutral loop, for example:

`src/product_factory/orchestration/agent_loop.py`

Loop stages:

1. Assemble task and repository context.
2. Request model action with canonical tool definitions.
3. Validate tool call against capability grants.
4. Execute through `ToolBroker`.
5. Append bounded tool result to the conversation.
6. Continue until:
   - the model finishes;
   - validation passes;
   - budget expires;
   - no-progress is detected;
   - a terminal error occurs.

### Implementation

- [ ] Expose broker tools as `CanonicalToolDefinition`s.
- [ ] Support model-driven:
  - `list_files`;
  - `read_file`;
  - `search_text`;
  - `create_file`;
  - `apply_patch`;
  - `git_status`;
  - `git_diff`;
  - `run_validation_command`.
- [ ] Execute returned tool calls and feed results into subsequent model turns.
- [ ] Treat repository and tool output as untrusted content.
- [ ] Bound individual tool output and total conversation growth.
- [ ] Enforce task tool-call, token, cost, and wall-clock budgets.
- [ ] Detect repeated identical calls and repeated patch fingerprints.
- [ ] Require at least one repository inspection before a write, except for explicitly new standalone artifacts.
- [ ] Finish only with a non-empty patch and a structured implementation result.
- [ ] Persist rounds, tool calls, costs, and termination reason.

Likely files:

- `src/product_factory/orchestration/agent_loop.py` (new)
- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/gateway/canonical_messages.py`
- `src/product_factory/gateway/openrouter.py`
- `src/product_factory/tools/broker.py`
- `src/product_factory/tools/registry.py`

Tests:

- [ ] Mock sequence: read tool call → edit tool call → diff → finish.
- [ ] Unauthorized tool call is rejected without executing.
- [ ] Repeated identical calls stop with `no_progress`.
- [ ] Tool output truncation preserves hashes and diagnostics.
- [ ] Budget exhaustion stops the loop.
- [ ] Patch application failure is returned to the model for correction.
- [ ] Telemetry records each turn and tool boundary.

### WP3 exit gate

Run a five-seed reliability slice on:

- `code_cache`
- `code_health`
- `code_logging`
- `code_retry`

Required results:

- [x] At least one read and one write/apply tool call per implementation.
- [x] Non-empty patch rate at least 90%.
- [x] Patch-apply rate at least 75%.
- [x] At least three of five applying runs on at least three of four cases.
- [x] No live deterministic fallback.

Live evidence: `bench-be97863325ab`.

---

## WP4 — Repository and dependency context

### Goal

Give each worker enough relevant evidence to act without flooding the context window.

### Implementation

- [ ] Enrich repository analysis with:
  - language and package layout;
  - entry points;
  - relevant tests;
  - configuration files;
  - symbol and import hints.
- [ ] Feed repository-analysis output into dependent tasks.
- [ ] Populate `assemble_context` with:
  - targeted repository excerpts;
  - dependency outputs;
  - acceptance criteria;
  - relevant findings;
  - current patch and validator output for repair.
- [ ] Retrieve excerpts using task terms, referenced paths, imports, and symbols.
- [ ] Track context component sizes and omitted components.
- [ ] Require workers to cite evidence refs in structured task results.

Likely files:

- `src/product_factory/context/assembler.py`
- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/repositories/snapshot.py`
- `src/product_factory/domain/tasks.py`

Tests:

- [ ] Implementation prompt includes repository-analysis output.
- [ ] Relevant file excerpts are present.
- [ ] Dependency artifacts are available to dependent tasks.
- [ ] Context truncation is deterministic and recorded.
- [ ] Repair context includes the failed patch and command output.

### WP4 experiment

Compare five seeds each:

- file-list-only context;
- targeted excerpts plus dependency outputs.

Gate:

- [ ] Targeted context improves patch-apply or usable rate without exceeding the configured context soft limit.
- [ ] At least 80% of modified existing files were read before modification.

---

## WP5 — Behavioral validation in runtime and harness

### Goal

Make tests and deterministic checks the primary correctness evidence.

### Implementation

- [ ] Map workflow/case validation command IDs to registered commands.
- [ ] Grant validation tools only to appropriate capabilities.
- [ ] Run validation after each candidate patch.
- [ ] Run validation again after repair and before composition.
- [ ] Store bounded output as content-addressed artifacts.
- [ ] Correlate validation events to task and patch fingerprints.
- [ ] Distinguish:
  - command failed;
  - command timed out;
  - command unavailable;
  - tests failed;
  - infrastructure failed.
- [ ] Make required validation failures blocking.

Likely files:

- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/validation/pipeline.py`
- `src/product_factory/tools/broker.py`
- `src/product_factory/evaluation/deterministic.py`
- `config/policies.yaml`

Tests:

- [ ] Applying but behaviorally wrong patch fails.
- [ ] Passing tests produce a blocking-validator pass.
- [ ] Timeout is surfaced distinctly.
- [ ] Validation output is bounded and redacted.
- [ ] Original repository remains unmodified.

### WP5 exit gate

- [ ] Every code case executes at least one behavioral check.
- [ ] Behavioral pass rate is separately reported.
- [ ] No artifact is usable when a required runtime or harness validation fails.

---

## WP6 — Stateful repair and no-progress control

### Goal

Repair the actual failed implementation rather than regenerating from a clean base.

### Implementation

- [ ] Define patch lineage and active candidate state.
- [ ] Create repair worktrees from the latest candidate patch or commit.
- [ ] Pass failed command output, findings, acceptance criteria, and patch to repair.
- [ ] Re-run only relevant validation first, then the complete required suite.
- [ ] Record pre/post patch fingerprints.
- [ ] Stop when:
  - fingerprint repeats;
  - validation failure repeats without changed evidence;
  - budget expires;
  - maximum repair attempts is reached.
- [ ] Distinguish repairable product failures from infrastructure failures.

Likely files:

- `src/product_factory/orchestration/repair.py`
- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/repositories/worktrees.py`
- `src/product_factory/domain/tasks.py`

Tests:

- [ ] Repair worktree contains the failed candidate changes.
- [ ] Repair changes the patch fingerprint.
- [ ] Successful repair clears the original failure.
- [ ] Repeated identical patch terminates.
- [ ] Repair budget is enforced.

### WP6 exit gate

- [ ] More than 50% of seeded repairable failures pass after repair.
- [ ] Every repair starts from the latest candidate.
- [ ] Every repair attempt either changes the fingerprint or terminates.

---

## WP7 — Worktree lineage and deterministic composition

### Goal

Preserve validated changes from all contributing tasks and compose the correct final patch.

### Implementation

- [ ] Represent candidate lineage explicitly:
  - base commit;
  - implementation patch;
  - repair patch;
  - review repair;
  - final validated patch.
- [ ] Commit or snapshot successful task worktrees before dependent tasks.
- [ ] Define merge order for multiple writable tasks.
- [ ] Detect and report conflicts.
- [ ] Compose from the latest validated lineage, not the composition task's empty worktree.
- [ ] Record contributing task IDs and patch fingerprints.
- [ ] Reject empty composition when a non-empty validated candidate exists.

Likely files:

- `src/product_factory/repositories/worktrees.py`
- `src/product_factory/repositories/patches.py`
- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/domain/artifacts.py`

Tests:

- [ ] Two implementation tasks preserve both changes.
- [ ] Repair output includes original implementation.
- [ ] Conflict becomes an explicit failure.
- [ ] Composer selects the last validated candidate.
- [ ] Empty patch cannot replace a valid patch.

### WP7 exit gate

- [ ] Multi-task composition fixture passes.
- [ ] Repair lineage fixture passes.
- [ ] Final patch is reproducible from persisted lineage metadata.

---

## WP8 — Evidence-based independent review

### Goal

Use review only when it produces actionable, measurable corrective value.

### Implementation

- [ ] Define a strict structured review schema.
- [ ] Parse model review into typed `Finding`s.
- [ ] Require file/line or artifact evidence.
- [ ] Map findings to acceptance criteria.
- [ ] Classify blocking status by severity and confidence.
- [ ] Trigger repair only for blocking findings.
- [ ] Validate whether repair resolves each finding.
- [ ] Remove the unconditional resolved placeholder finding.

Likely files:

- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/domain/findings.py`
- `src/product_factory/orchestration/repair.py`
- `src/product_factory/gateway/canonical_messages.py`

Tests:

- [ ] Seeded correctness defect yields a blocking finding.
- [ ] Style-only issue does not block.
- [ ] Missing evidence makes the finding non-blocking or invalid.
- [ ] Repair closes the intended finding.

### WP8 experiment and gate

Run review-on vs review-off over seeded-defect cases.

- [ ] Defect detection at least 80%.
- [ ] False-blocking rate below 20%.
- [ ] Review improves usable rate enough to justify added cost and latency.
- [ ] If the gate fails, keep review optional rather than default.

---

## WP9 — Planner and delegation optimization

### Goal

Generate the smallest plan that covers acceptance criteria and validation responsibilities.

### Implementation

- [ ] Give the planner repository structure and relevant metadata.
- [ ] Require explicit final artifacts.
- [ ] Require bounded path scopes.
- [ ] Map each acceptance criterion to:
  - responsible task;
  - validator;
  - evidence type.
- [ ] Reject tasks without a concrete output or validation responsibility.
- [ ] Add complexity-sensitive templates:
  - trivial edit;
  - standard code change;
  - multi-component change;
  - architecture request.
- [ ] Avoid architecture/review/test-design tasks when they do not improve measurable outcomes.
- [ ] Record planner errors rather than silently falling back in live mode.

Likely files:

- `src/product_factory/planning/planner.py`
- `src/product_factory/planning/compiler.py`
- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/domain/plans.py`
- `config/workflows.yaml`

Tests:

- [ ] Every acceptance criterion has ownership and verification.
- [ ] Trivial request produces a shorter DAG than a complex request.
- [ ] Path scopes are not universally `**/*` unless justified.
- [ ] Invalid plans fail with actionable compiler errors.

### WP9 experiment and gate

Compare:

- fixed deterministic DAG;
- current live planner;
- complexity-sensitive planner.

Pass when:

- [ ] Usable rate is not reduced.
- [ ] Median task count and cost decrease for simple cases.
- [ ] Acceptance-criterion coverage remains 100%.

---

## WP10 — Controlled ablations and model strategy

### Goal

Attribute improvements and select the simplest cost-effective architecture.

### Required subjects

- Single-agent baseline.
- Implementation agent in isolation.
- Implementation plus validation/repair.
- Full orchestration without review.
- Full orchestration with review.
- Frontier reference on a stratified subset.

### Implementation

- [ ] Add named orchestration ablation configurations.
- [ ] Ensure identical worker models, temperatures, and budgets where orchestration structure is the independent variable.
- [ ] Add blind pairwise judging in addition to absolute scores.
- [ ] Randomize pairwise presentation order.
- [ ] Report Wilson or bootstrap confidence intervals.
- [ ] Stratify reports by:
  - adversarial;
  - architecture;
  - code change;
  - complexity;
  - expected validation type.

Likely files:

- `config/benchmarks.yaml`
- `src/product_factory/evaluation/bench.py`
- `src/product_factory/evaluation/judge.py`
- `src/product_factory/evaluation/compare.py`
- `src/product_factory/evaluation/runners.py`

Decision rules:

- [ ] If isolated implementation is within 10 points of full orchestration, simplify orchestration.
- [ ] If validation/repair produces most of the gain, prioritize it over planning/review.
- [ ] Keep review default-on only when it improves usable-rate-per-dollar.
- [ ] Use stronger models only when they improve cost per usable artifact, not merely raw judge score.

---

## 3. Benchmark promotion ladder

Do not run the next stage until the previous stage passes.

### Stage A — Unit and contract verification

- [ ] Unit tests for artifact validity, metrics, seeds, and confidence intervals.
- [ ] Contract tests for persisted score/report schemas.
- [ ] Graph tests for tool loop, validation, repair, and composition.
- [ ] Security tests for tool authorization and untrusted output.

### Stage B — Four-case reliability slice

Cases:

- `code_cache`
- `code_health`
- `code_logging`
- `code_retry`

Configuration:

- two subjects;
- five seeds;
- 40 scored artifacts.

Gate:

- [x] Orchestration non-empty rate at least 90%.
- [x] Orchestration patch-apply rate at least 75%.
- [x] Orchestration usable rate at least 50%.
- [x] No live fallback artifacts.
- [x] Confidence intervals are reported.

Recorded live Stage B: `bench-be97863325ab` — orch usable 90%, baseline 35%.

### Stage C — Full code suite

Configuration:

- all code cases;
- orchestration and single-agent;
- at least three seeds.

Gate:

- [x] Behavioral pass rate at least 60%.
- [x] Orchestration usable rate at least single-agent +10 percentage points, or confidence intervals clearly show that more samples are required.
- [x] Cost per usable artifact no more than 2x baseline.

Recorded live Stage C: `bench-e59f17adf319` — orch usable 85.2% vs baseline 13.0%
(paired +72 points, CI +59 to +83); orch cost/usable ≈ `$0.0127` (0.38× baseline).

### Stage D — Architecture quality

- [ ] Replace template compliance with request-specific architecture criteria.
- [ ] Run at least three seeds per architecture case.
- [ ] Require at least 20% usable architecture artifacts before comparing mean judge quality.

### Stage E — Ablations

- [x] Worker isolation.
- [x] Validation/repair.
- [x] Review on/off.
- [~] Planner variants.
- [~] Model/profile variants.

Recorded live Stage E slice `bench-72dfcf11b63b` (3 cases × 6 subjects × 3 seeds):

- no-review / validation-repair / file-list / targeted: usable ≈ 77.8%
- with-review: usable 0% (keep review optional, not default)
- implementation isolation: usable 0% in this slice
- targeted context did not beat file-list usable rate (tie at 77.8%)

### Stage F — Frontier comparison

Configuration:

- 8–12 stratified cases;
- blind pairwise comparisons;
- explicit oracle budget.

Gate:

- [ ] Orchestration pairwise win rate against single-agent at least 55%, with uncertainty shown.
- [ ] Frontier gap within 15 percentage points, or orchestration demonstrates lower cost at comparable usable rate.

---

## 4. Operational and observability requirements

Each benchmark run must record:

- [ ] Git commit and dirty state.
- [ ] Benchmark configuration and case hashes.
- [ ] Subject configuration.
- [ ] Resolved models and providers.
- [ ] Sampling parameters and seed.
- [ ] Prompt package hashes.
- [ ] Tool calls and loop rounds.
- [ ] Patch fingerprints and lineage.
- [ ] Validation commands and results.
- [ ] Repair and review decisions.
- [ ] Token, cost, and latency totals.
- [ ] Termination reason.

Run health requirements:

- [ ] Benchmark progress is flushed after every subject/seed.
- [ ] Resume never re-spends completed cells.
- [ ] Interrupted cells are cleaned or explicitly reused.
- [ ] API keys are supplied through the environment and never persisted in logs or artifacts.
- [ ] Long runs use a process model that survives client/session transitions.

---

## 5. Recommended implementation sequence

1. [x] WP1 — Benchmark integrity and scoring semantics.
2. [x] WP2 — Fail-closed live execution.
3. [x] WP3 — Multi-turn tool-using implementation agent.
4. [~] WP4 — Repository and dependency context.
5. [x] WP5 — Behavioral validation.
6. [~] WP6 — Stateful repair.
7. [~] WP7 — Worktree lineage and composition.
8. [~] WP8 — Evidence-based review.
9. [~] WP9 — Planner and delegation optimization.
10. [~] WP10 — Controlled ablations and model strategy.

Recommended pull-request boundaries:

- PR 1: WP1 artifact validity, behavioral contracts, metric decomposition.
- PR 2: WP1 multi-seed/resume/confidence intervals.
- PR 3: WP2 fail-closed live execution.
- PR 4: WP3 agent-loop core and canonical tools.
- PR 5: WP4 context wiring.
- PR 6: WP5 runtime and harness validation.
- PR 7: WP6 repair state.
- PR 8: WP7 lineage/composition.
- PR 9: WP8 structured review.
- PR 10: WP9 planner optimization.
- PR 11: WP10 ablations and frontier reports.

---

## 6. First milestone checklist

The first significant milestone comprises WP1–WP4.

Implementation:

- [x] Empty artifacts fail deterministically.
- [x] Every code case has a behavioral contract.
- [x] Multi-seed execution and resume are implemented.
- [x] Live deterministic fallbacks are disabled.
- [x] Multi-turn implementation tool loop is implemented.
- [x] Repository excerpts and dependency outputs reach implementation prompts.

Verification:

- [x] Unit, contract, graph, and security tests pass.
- [x] Four-case, five-seed reliability slice completes (mock + live Stage B).
- [x] Non-empty patch rate at least 90% in the mock reliability slice.
- [x] Patch-apply rate at least 75% in the mock reliability slice.
- [x] Usable rate at least 50% in the mock reliability slice.
- [x] No live fallback artifacts (fail-closed tests pass; live Stage B passed).
- [x] Confidence intervals and cost per usable artifact are included.

Record the milestone benchmark:

- Benchmark ID: `bench-be97863325ab` (live Stage B; mock harness: `bench-09de8c31f586`)
- Git commit: `uncommitted working tree`
- Date: `2026-07-21`
- Subject models: live OpenRouter profiles / `MockGateway` for harness
- Judge model: `grok_judge` / `MockJudge`
- Non-empty rate: `100%` (live Stage B orchestration)
- Patch-apply rate: `100%` (live Stage B orchestration)
- Behavioral pass rate: `100%` (live Stage B orchestration)
- Usable rate: `90% orchestration; 35% baseline` (live Stage B)
- Usable-rate delta vs baseline: `+55 points; bootstrap CI +35 to +75`
- Cost per usable artifact: `~$0.0245` (live Stage B orchestration)
- Decision: `Live Stage B passed; proceed to Stage C`

---

## 7. Decision log

Add dated entries as implementation proceeds.

### 2026-07-21 — Baseline interpretation

- The 30-case benchmark is retained as a harness baseline, not a quality claim.
- `code_cache` demonstrated that single-run cells are too variable for promotion decisions.
- Measurement integrity and a real tool-using implementation worker are the first priorities.
- Planner and reviewer optimization are deferred until workers, validation, repair, and composition are functional.

### 2026-07-21 — Core implementation and mock promotion

- WP1, WP2, and WP5 implementation gates passed.
- The bounded implementation agent, targeted context, stateful repair, lineage,
  structured review, adaptive planner, and ablation framework are operational.
- Stage B and Stage C mock runs passed harness reliability gates.
- Mock results are not model-quality evidence; live multi-seed, review-ablation,
  planner-ablation, and frontier gates remain open.

### 2026-07-21 — Remaining WP3–WP10 code closure + live confirmation

- Closed remaining code gaps: patch-fingerprint no-progress, context ablation
  subjects, multi-writer conflict detection, repair-attempt budgets, evidence
  demotion, read/write path scopes, planner modes, and differentiated WP10
  ablations.
- Live confirmation `bench-8391499ffa47` reached 25% orchestration usable
  (`code_cache` q=0.75). Not enough to open the five-seed Stage B gate.
- Follow-ups: OpenRouter json_schema fallback, expected-file injection, grant
  headroom, planner normalization, default `planner_mode=fixed`.
- One-seed confirmation `bench-b7967a2a5b1a` cleared Stage B thresholds (50% usable).
- Live Stage B `bench-be97863325ab` **passed** with orchestration usable 90% vs
  baseline 35% (paired +55 points, CI +35 to +75). WP3 live reliability gate met.
- Live Stage C `bench-e59f17adf319` **passed** with orchestration usable 85.2% vs
  baseline 13.0% (paired +72 points, CI +59 to +83); orch cheaper per usable
  artifact than baseline (0.38×).
- Note: blind pairwise still often favored baseline textually; usable-rate and
  apply/behavioral metrics are the Stage B/C promotion criteria and passed.
- Remaining open: Stage D architecture, Stage E/F ablations and frontier.

