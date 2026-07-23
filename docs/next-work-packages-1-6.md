# Next Work Packages 1–6 — Implementation Plan

Detailed implementation plan for the post–Stage-B/C sequence. This continues
[`orchestration-performance-plan.md`](orchestration-performance-plan.md); it does
**not** reopen original WP1–WP5 (already gated) or propose new orchestration
abstractions.

**North star:** raise per-case success frequency and cost/usable on the current
code suite, with ablations that attribute gains to a subsystem.

**Status legend:** `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

**Authoritative evidence so far:**

| Gate | Bench ID | Result |
| --- | --- | --- |
| Live Stage B | `bench-be97863325ab` | orch usable **90%**; `code_health` **3/5** |
| Live Stage C | `bench-e59f17adf319` | orch usable **85.2%** vs baseline 13% |
| Stage E slice | `bench-72dfcf11b63b` | no-review/validation/context ≈**77.8%**; review-on & isolation **0%** |
| N4 planner | `bench-466fb40fada6` | fixed **77.8%** vs live **66.7%** → keep fixed |
| N4 worker | `bench-fa7a67a1307f` | `coding_worker` **88.9%** vs strong **44.4%** → keep coding_worker |
| N5 Stage F | `bench-c46101767870` | orch **93.3%** vs frontier **50%**; cost/usable **8.7×** cheaper; pairwise 43% (secondary fail) |

---

## Scope and order

| WP | Title | Depends on | Rough effort |
| --- | --- | --- | --- |
| **N1** | Fix remaining hard failures | — | 2–4 days |
| **N2** | Close WP6 live seeded-repair gate | N1 health path stable enough | 1–2 days |
| **N3** | Decide review default with evidence | N1 review retest | 0.5–1 day (+ bench) |
| **N4** | Targeted Stage E leftovers | N1 isolation fair; N3 decided | 1–2 days |
| **N5** | Stage F frontier / pairwise | N1–N3 (avoid noise) | 1–2 days |
| **N6** | Stage D architecture quality | N1–N3 code path stable | 2–3 days |

Do **not** start N5/N6 until N1 exit criteria pass. N4 can overlap N2 if
isolation is already fair.

### Explicit non-goals

- Declarative role frameworks
- LangGraph rewrite (coordinator remains authoritative)
- Broader mock Stage B/C matrices
- Expanding planner complexity while `planner_mode=fixed` carries reliability
- Review UX/feature expansion before N3 gate

---

## N1 — Fix remaining hard failures

### Goal

Close the three leaks that still make the code suite look worse than it is, and
make ablations interpretable:

1. `code_health` reliability (3/5 → ≥4/5)
2. Review-on path producing empty / plan-rejected artifacts
3. Implementation isolation runner producing empty patches

### Status — 2026-07-22

- [x] N1.A contract A chosen and applied: `code_health` is a callable health
      module + tests (no HTTP). Case YAML, mock deterministic impl text, and
      judge `reference_hints` aligned.
- [x] N1.A live Stage B retest **PASSED** — `bench-9ae9322ca17d`:
      orch usable **90%** (18/20); `code_health` **5/5** (q≈0.97–1.0);
      baseline usable 50%; paired usable delta +40pp; subject `$0.505`,
      judge `$0.603`.
- [x] N1.B live review-on/off slice **PASSED** — `bench-6df9e5333310`
      (3 cases × 2 subjects × 3 seeds):
      - no-review usable 6/9 (66.7%), artifacts 9/9
      - with-review usable 6/9 (66.7%), artifacts **7/9 (77.8%)** ≥50% exit
      - empties were provider **HTTP 502**, not systematic plan rejection
      - Decision deferred to N3 (usable tied; cost similar; keep optional for now)
- [x] N1.C live isolation mini-check **PASSED** — `bench-9fb86599d67e`
      (4 cases × 3 seeds): usable **7/12 (58.3%)** ≥50%; artifact 12/12;
      patch-apply 12/12. Pre-fix Stage E isolation 0% is **invalidated**.
- [x] Fixture clone helpers materialize git from plain trees.

**N1 package exit: met.** Proceed to N2 (seeded repair) / N3 (review default).

### Background / failure autopsy (known)

**`code_health` (Stage B seeds 1, 3):**

- Deterministic smoke can pass while the LLM judge rejects usability.
- Judge summaries: patch adds a trivial `health()` / `health_check` helper + unit
  test, but **does not deliver a validated HTTP health-check endpoint**.
- Fixture `tests/fixtures/sample_api` is a bare module (`hello()`), not a web
  framework — case prompt + expected files (`src/app/health.py`,
  `tests/test_health.py`) and judge rubric are under-specified relative to
  “HTTP endpoint.”
- Acceptance criteria only say “Health endpoint added with tests.”

**Review-on (Stage E):**

- Sample failure: `Plan rejected after repair` + `artifact_empty`.
- Finding-category normalization and force-review AC injection already landed;
  need a **re-measure**, then code fixes only if the retest still fails.

**Isolation:**

- `IsolationAblationRunner` → `AgentIsolationRunner` does a **one-shot**
  `gateway.complete` with JSON task dump + file list — **no tool loop**, no
  grants, no worktree write path.
- Live models often return prose; empty after `extract_unified_diff` →
  `artifact_empty`. Unfair vs full orchestration’s multi-turn impl agent.

### Workstreams

#### N1.A — `code_health` contract alignment

**Approach (prefer in this order):**

1. **Tighten the eval contract** so “usable” matches a checkable product shape
   without requiring a full FastAPI stack unless we intentionally add one.
2. **Sticky prompts / expected paths** in the impl loop so the worker targets
   the contracted files and behaviors.
3. Only if needed: add a minimal HTTP surface to `sample_api` (e.g. stdlib
   handler or tiny framework) and a smoke that hits it.

**Tasks:**

- [ ] Pull Stage B orch non-usable cells for `code_health` (seeds 1, 3) and
      Stage C bottom-stratum misses; record patch fingerprints, changed files,
      smoke results, judge rationale.
- [ ] Decide contract A vs B and document in the case YAML + plan tracker:
  - **A (recommended):** “health module + callable check + tests” — update
    request/AC/judge hints so HTTP is **not** required; keep
    `expected_files` as today.
  - **B:** “HTTP health endpoint” — extend fixture + add a deterministic
    smoke (e.g. invoke handler / status code) so judge and smoke agree.
- [ ] Update `tests/eval_cases/code_health.yaml` (request, AC,
      `behavioral_checks` / smoke as needed).
- [ ] Ensure orchestration + baseline runners inject expected files / AC
      (already wired; verify health case still gets them).
- [ ] If workers still stop at helpers: add a targeted acceptance hint in
      impl prompts when `expected_files` include `health.py` (narrow, not
      a new role system).
- [ ] Local mock: health case remains green under deterministic workers.
- [ ] Live retest: Stage B slice, **5 seeds**, subjects
      `full_orchestration,single_agent_baseline`, cases including
      `code_health` (full four-case slice preferred).

**Likely files:**

- `tests/eval_cases/code_health.yaml`
- `tests/fixtures/sample_api/**` (only if choosing contract B)
- `src/product_factory/evaluation/runners.py` (prompt injection audit)
- `src/product_factory/orchestration/coordinator.py` / agent loop prompts
  (only if sticky expected-path hints needed)
- `docs/orchestration-performance-plan.md` (record bench + decision)

**Exit (N1.A):**

- [ ] Live `code_health` orch usable frequency **≥ 4/5** on a five-seed retest
- [ ] No regression: Stage B four-case orch usable **≥ 85%** (was 90%)

#### N1.B — Review path retest and fix

**Tasks:**

- [ ] Small live slice after current schema fixes:
  - cases: `code_cache`, `code_retry` (and optionally `code_logging`)
  - subjects: `full_orchestration` (review off), `full_orchestration_with_review`
  - seeds: **3**
- [ ] Classify failures: plan reject, empty artifact, finding schema, repair
      loop exhaustion, cost/budget.
- [ ] Fix only what the retest proves broken. Candidates:
  - force-review plan construction (`force_review` metadata in
    `coordinator.py` / `bench.py`)
  - finding parse / category map
  - repair-from-review not wiping validated candidate (lineage)
  - reviewer model / structured-output fallback
- [ ] Unit/graph tests for any regression found.
- [ ] Re-run the same small slice.

**Likely files:**

- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/orchestration/repair.py`
- `src/product_factory/domain/findings.py` (or review schema module)
- `src/product_factory/evaluation/bench.py`
- `tests/graph/test_performance_work_packages.py` (or review-specific tests)

**Exit (N1.B):**

- [ ] `full_orchestration_with_review` **non-empty artifact rate ≥ 50%** on the
      small slice (not necessarily beating no-review yet — that is N3)
- [ ] No systematic `Plan rejected after repair` on every cell
- [ ] Record bench ID + failure taxonomy in the performance plan

#### N1.C — Fair implementation isolation runner

**Goal:** isolation measures “one coding worker with tools,” not “one chat
completion.”

**Tasks:**

- [ ] Redesign `AgentIsolationRunner` / `IsolationAblationRunner` to reuse the
      **same agent loop + tool broker + worktree** path as orchestration’s
      implementation capability (fixed single `TaskSpec`, no planner/review).
- [ ] Preserve subject_id `implementation_isolation` for Stage E continuity.
- [ ] Mock path: isolation must produce applying patches for Stage B cases.
- [ ] Live mini-check: Stage B four cases × 1–3 seeds, subject
      `implementation_isolation` only; then compare to
      `full_orchestration` on same seeds if budget allows.
- [ ] Document in reports that pre-fix Stage E isolation 0% is **invalidated**.

**Likely files:**

- `src/product_factory/evaluation/runners.py` (`AgentIsolationRunner`,
  `IsolationAblationRunner`)
- Possibly extract a shared “run one capability” helper from
  `coordinator.py` if duplication is high (keep change minimal)
- Tests under `tests/evaluation/` or `tests/graph/`

**Exit (N1.C):**

- [ ] Isolation usable **> 0** on Stage B cases (target: **≥ 50%** on a
      3-seed four-case slice, or clearly documented if still below orch)
- [ ] Patches apply; smoke commands can run when present
- [ ] Ablation question “is orchestration worth it vs lone impl agent?” becomes
      answerable

### N1 package exit

- [ ] N1.A + N1.B + N1.C exits all met
- [ ] Performance plan execution update section records three bench IDs and
      keep/kill notes
- [ ] Local: `ruff`, `basedpyright src`, relevant pytest green

---

## N2 — Close WP6 live seeded-repair gate

### Status — 2026-07-22

- [x] Harness: `seeded_repair` subject + `evaluation/defects.py` +
      `force_seeded_impl` coordinator path.
- [x] Mock tests: repair triggered; candidate recovered.
- [x] Live `bench-11f4a2d7b31c`: usable **8/12 (66.7%)** after one defect
      strengthening retest; repair + fingerprint change on all cells.
- [x] **N2 / WP6 exit met.**

### Goal

Prove stateful repair recovers **>50%** of seeded repairable failures, starting
from the latest candidate, with fingerprint progress or clean terminate.

### Background

Repair machinery exists (`repair.py`, coordinator repair routing, lineage
fingerprints). The original WP6 exit gate is still open: no dedicated **seeded
failure** live experiment.

### Design

Introduce a **seeded-defect harness** (eval metadata or fixture), not ad-hoc
manual patches:

1. Start from a known-good or partial candidate **or** inject a broken patch /
   broken file into the worktree before repair.
2. Force validation failure (syntax error, failing smoke, missing expected file).
3. Allow repair attempts; measure success and fingerprint deltas.
4. Exclude infrastructure failures (provider 5xx, timeout) from the denominator.

**Seed set (minimum 6 cells, prefer 8–12):**

| Seed type | Example |
| --- | --- |
| Broken syntax in expected file | Truncated `def` in `cache.py` / `health.py` |
| Failing assertion in tests | Wrong expected return value |
| Incomplete patch | File present but missing required function |
| Valid first patch + induced smoke fail | Post-impl mutate then repair |

Cases: prefer Stage B set (`code_cache`, `code_health`, `code_logging`,
`code_retry`) so repairability matches production path.

### Tasks

- [ ] Add a bench/subject or CLI mode for seeded repair, e.g. metadata
      `seed_repair_defect: <id>` handled by orchestration runner or a thin
      `SeededRepairRunner`.
- [ ] Implement 3–4 named defect injectors under
      `tests/fixtures/` or `src/product_factory/evaluation/defects.py`.
- [ ] Assert in unit tests: repair worktree contains prior candidate; repeated
      identical fingerprint terminates; budget enforced (extend existing WP6
      tests if gaps remain).
- [ ] Live run: ≥6 seeded repairable failures, live models, fixed planner.
- [ ] Report: repair success rate, mean attempts, cost, infra vs product fail
      split, fingerprint change rate.

**Likely files:**

- `src/product_factory/orchestration/repair.py`
- `src/product_factory/orchestration/coordinator.py`
- `src/product_factory/evaluation/runners.py` / `bench.py` / new `defects.py`
- `tests/graph/test_performance_work_packages.py`
- `docs/orchestration-performance-plan.md` (WP6 exit checkboxes)

### Exit (N2 / WP6)

- [ ] **>50%** of seeded **repairable** (non-infra) failures pass after repair
- [ ] Every repair starts from latest candidate (asserted in tests + sampled
      live lineage metadata)
- [ ] Every attempt either changes fingerprint or terminates with typed reason
- [ ] Mark WP6 exit gate `[x]` in the performance plan with bench ID

If gate fails: document top failure modes; fix the highest-frequency product
bug once; re-run once. Do not expand planner/review to compensate.

---

## N3 — Decide review default with evidence

### Status — 2026-07-22

- [x] Experiment: `bench-6df9e5333310` (reuse N1.B) — usable tied **66.7%**
      with/without review; with-review artifacts 7/9 (empties = provider 502).
- [x] **Decision: keep review optional** for ordinary/low-risk runs.
- [x] High-risk fixed plans retain `independent_review` (existing planner).
- [x] Policy regression tests added; no further review schema work until a new
      measured hypothesis.
- [x] **N3 exit met.**

### Goal

Freeze the product default for review using **usable-rate-per-dollar** (and
secondarily defect catch rate), not intuition.

### Prerequisites

N1.B must produce a review subject that is not systematically empty.

### Experiment

| Subject | Metadata |
| --- | --- |
| `full_orchestration` | review off (current default path) |
| `full_orchestration_with_review` | `force_review: true` |
| Optional: `orchestration_validation_repair` | review disabled, validation on |

- Cases: 3–4 coding cases (Stage B or Stage E trio)
- Seeds: **3**
- Live models; same judge as Stage C
- Primary metrics: usable rate, cost/usable, paired delta vs no-review
- Secondary: blocking findings that caused successful repair; false-positive
  blocking rate (plan reject / empty)

### Decision rule (write into plan tracker)

| Outcome | Decision |
| --- | --- |
| Review improves usable **and** cost/usable ≤ ~1.5× no-review | Consider **default-on for high-risk plans only** (WP9 path) |
| Review ≈usable but much more expensive | Keep **optional**; no default-on |
| Review hurts usable or still empty-heavy | Keep **optional**; **stop review feature work** until a new hypothesis |

### Tasks

- [ ] Run the comparison bench; record ID.
- [ ] Fill decision table in `orchestration-performance-plan.md` (WP8 gate).
- [ ] If high-risk-only: implement/confirm planner flag that enables review only
      for high-risk plans (likely already partially present) and add one test.
- [ ] If optional: leave defaults; close WP8 “default-on” as rejected with
      evidence; do not open review UX tickets.

### Exit (N3)

- [ ] Written keep/kill decision with bench ID
- [ ] Default behavior in code matches the decision
- [ ] No further review schema work until a new measured hypothesis

---

## N4 — Targeted Stage E leftovers

### Status — 2026-07-22

- [x] Isolation subject fair (N1.C); ablation runners use fixed planner + agent
      loop where needed.
- [x] Planner ablation live — `bench-466fb40fada6`
      (`code_cache,code_health,code_retry` × 3 seeds):
      - `orchestration_fixed_planner`: **7/9 usable (77.8%)**, cost/usable ≈ `$0.021`
      - `orchestration_live_planner`: **6/9 usable (66.7%)**, cost/usable ≈ `$0.025`
      - Live −11.1pp usable and worse cost/usable → **keep `planner_mode=fixed`**
- [x] Worker model ablation live — `bench-fa7a67a1307f` (same cases/seeds;
      planner held fixed):
      - `orchestration_fixed_planner` (`coding_worker`): **8/9 usable (88.9%)**,
        cost/usable ≈ `$0.025`
      - `orchestration_strong_worker` (`local_target_reviewer`): **4/9 usable
        (44.4%)** — collapsed on `code_cache` (0/3) and weak on `code_retry` (1/3)
      - Stronger profile does **not** improve usable → **keep `coding_worker`**
- [x] Context ablation **skipped**: Stage E already tied targeted vs file-list
      at 77.8%; planner/model left no residual question.
- [x] Ablation knobs wired: `resolve_task_model_profile`, subject
      `orchestration_strong_worker`, tests in `tests/graph/test_n4_ablation_knobs.py`.
- [x] **N4 exit met.** Defaults already match (`planner_mode=fixed`,
      impl profile `coding_worker`, `context_mode=targeted` as impl detail).

### Goal

Answer only the three product-shape questions left open after Stage E.

| Ablation | Question | Subjects / knobs |
| --- | --- | --- |
| Planner fixed vs live | Is live planning worth variance? | `planner_mode=fixed` vs `live` (and optional `complexity_sensitive`) |
| Worker model profile | Stronger coding worker vs cost/usable? | `coding_worker` vs one stronger profile (config/models) |
| Context targeted vs file-list | Harder cases only; Stage E tied at 77.8% | `context_mode=targeted` vs `file_list_only` on harder / Stage C bottom stratum |

### Decision table

| Knob | Call | Bench | Default |
| --- | --- | --- | --- |
| Planner | **Keep fixed**; do not promote live as default | `bench-466fb40fada6` | `planner_mode=fixed` |
| Worker profile | **Keep `coding_worker`**; do not switch to `local_target_reviewer` | `bench-fa7a67a1307f` | coding worker via scheduler |
| Context | **Defer / drop as priority**; no ablation win claimed | Stage E `bench-72dfcf11b63b` (tie) | `context_mode=targeted` |

### Tasks

- [x] Confirm isolation subject is fair (N1.C); if re-including isolation in a
      matrix, use the fixed runner.
- [x] Planner ablation: 3 cases × 3 seeds × 2–3 planner subjects; live.
- [x] Model-profile ablation: same cases/seeds; swap only worker profile;
      hold planner fixed.
- [x] Context: **skip** unless planner/model leave a residual question; if run,
      use cases where file-list previously failed or Stage C misses.
- [x] Produce a one-page decision table: keep / kill / defer each knob.
- [x] Update Stage E section of the performance plan.

### Decision heuristics

- Prefer **fixed planner** unless live wins usable by ≥10pp with overlapping CI
  that still favors live, at acceptable cost.
- Prefer cheaper worker unless stronger model improves cost/usable.
- Drop context as a priority if still tied; keep `targeted` as implementation
  detail without claiming ablation win.

### Exit (N4)

- [x] Three keep/kill calls documented with bench IDs
- [x] Defaults in `bench.py` / coordinator metadata match decisions
- [x] No “try everything” matrix left open as a blocker

---

## N5 — Stage F frontier / pairwise

### Status — 2026-07-22

- [x] Stratified case list + CLI profile selected (below).
- [x] Live Stage F — `bench-c46101767870` (10 cases × 3 subjects × 3 seeds):
  - `full_orchestration`: **28/30 usable (93.3%)**, CI 79–98%, cost/usable ≈ `$0.018`
  - `frontier_reference` (`claude-fable-5`): **15/30 usable (50%)**, CI 33–67%,
    cost/usable ≈ `$0.157` (oracle spend `$2.36` of `$8` cap)
  - `single_agent_baseline`: **12/30 usable (40%)**
  - Paired orch−baseline usable **+53pp** (CI +33 to +73)
  - Blind pairwise orch vs baseline: **13/30 wins (43.3%)**, CI 27–61% — below
    55% writing-quality gate; usable already dominates so pairwise does **not**
    overturn product decision (same pattern as Stage C)
- [x] Unblocked frontier routing: OpenRouter `require_parameters=true` + `seed`
      404s Claude; gateway now merges profile `provider:` overrides;
      `frontier_oracle` sets `require_parameters: false`.
- [x] **Decision: cost-effective / keep mid-tier orchestration workers.** Do not
      buy frontier headroom for the coding path — orch beats one-shot frontier
      on usable **and** cost/usable (gap −43pp usable but orch is better, not
      worse; cost/usable 8.7× lower).
- [x] **N5 exit met.**

### Goal

Measure orchestration against a frontier single-agent reference under budget;
treat pairwise as a **writing-quality** signal, not the sole promotion gate.

### Prerequisites

N1–N3 done so frontier comparison is not dominated by empty review/isolation
noise.

### Configuration

- **10 stratified code cases** (Stage B reliability + Stage C diversity;
  includes historically hard `code_health` / `code_retry`):
  `code_cache,code_health,code_logging,code_retry,code_error,code_config,code_metrics,code_cli,code_refactor,code_validate`
- Subjects: `full_orchestration`, `single_agent_baseline`, `frontier_reference`
- Seeds: **3** → 90 subject-cells (30 frontier under oracle cap)
- Explicit **oracle budget** `$8.00` on `frontier_oracle`
  (`anthropic/claude-fable-5`); stop frontier cells when exceeded
- Blind pairwise: orch vs baseline via existing `BenchmarkRunner` pairwise path
- Judge: `grok_judge` (non-oracle) for absolute + pairwise scores
- Cost sketch (from Stage C/N4 unit costs): orch+baseline+judge ≈ `$2–3`;
  frontier up to cap ≈ `$8`

```bash
unset PRODUCT_FACTORY_FORCE_MOCK
product-factory bench run --live \
  --subjects full_orchestration,single_agent_baseline,frontier_reference \
  --case-ids code_cache,code_health,code_logging,code_retry,code_error,code_config,code_metrics,code_cli,code_refactor,code_validate \
  --limit 10 --seeds 3 --oracle-budget-usd 8.00 \
  --progress-log .product-factory/bench-progress/n5-stage-f.log
# Resume if interrupted:
# product-factory bench run --live --resume bench-<id> ...same flags...
```

### Primary gates (from performance plan)

- [!] Orchestration pairwise win rate vs single-agent **≥ 55%** — **43.3%**
      (CI 27–61%); secondary fail; usable +53pp dominates → do not overturn
- [x] Frontier usable/cost clause: orch **higher** usable (93% vs 50%) **and**
      **lower** cost/usable (`$0.018` vs `$0.157`) → pass product intent
      (stay mid-tier; no frontier upgrade)

### Tasks

- [x] Select stratified case list; commit as a named bench profile or CLI args
      documented in the plan.
- [x] Set oracle budget; dry-run cost estimate from prior Stage C unit costs.
- [x] Live Stage F run with resume enabled; monitor cost.
- [x] Publish report: usable rates + CIs, cost/usable, pairwise summary,
      frontier gap.
- [x] Decision: **cost-effective** — keep mid-tier workers; do not buy frontier
      coding headroom

### Likely files

- `src/product_factory/evaluation/bench.py` (already has frontier + pairwise)
- model profile config for `frontier_oracle`
- `docs/orchestration-performance-plan.md` Stage F checkboxes

### Exit (N5)

- [x] Stage F gate criteria evaluated with uncertainty
- [x] Written product implication (e.g. stay mid-tier workers vs buy frontier
      headroom)
- [x] Pairwise not used alone to overturn strong usable/cost evidence without
      explanation

---

## N6 — Stage D architecture quality

### Goal

Replace template-compliance wins with **request-specific** architecture quality,
and only then compare mean judge quality.

### Prerequisites

Code path N1–N3 stable. Architecture is a **new contract + cases** workstream,
not an impl-agent tweak.

### Problem today

- Architecture cases (e.g. `arch_saas.yaml`) have soft AC and `reference_hints`.
- Deterministic `validate_architecture_document` can pass boilerplate.
- Historical “wins” often mean baseline empty vs template-shaped orch output.

### Workstreams

#### N6.A — Scoring contract

- [ ] Define request-specific must-cover dimensions per case (or shared rubric
      with case overrides): e.g. tenancy, threat model, data model, failure
      modes, test strategy — mapped from `request` + `reference_hints`.
- [ ] Fail empty / near-empty / pure template headers as non-usable.
- [ ] Require ≥**20%** usable architecture artifacts before comparing mean
      judge quality (Stage D gate).
- [ ] Optional: lightweight deterministic keyword/section checks **plus** LLM
      judge rubric that references case-specific criteria.

#### N6.B — Cases and run

- [ ] Audit `tests/eval_cases/arch_*.yaml`; tighten AC and hints on at least
      **3** cases used for the gate.
- [ ] Run ≥3 seeds per selected architecture case.
- Subjects: `full_orchestration`, `single_agent_baseline` (frontier optional /
      budget-separated).
- [ ] If usable &lt; 20%: fix planner/architecture worker prompts and validation
      before quality comparisons; do not declare Stage D passed on mean-q alone.

### Likely files

- `src/product_factory/evaluation/deterministic.py`
- architecture validators (existing validate_architecture_document module)
- `tests/eval_cases/arch_*.yaml`
- architecture capability prompts / coordinator branch for
  `workflow_type=architecture`
- `docs/orchestration-performance-plan.md` Stage D

### Exit (N6)

- [ ] Request-specific criteria in force for gate cases
- [ ] ≥20% usable architecture artifacts on the Stage D run
- [ ] Mean judge quality compared only after usable floor met
- [ ] Decision recorded: architecture workflow ready for product use / needs
      another iteration / deprioritize vs code path

---

## Cross-cutting engineering standards

Apply on every package:

1. **Fail closed** — empty artifacts and infra errors stay typed failures.
2. **Multi-seed** — never promote on one seed.
3. **Resume** — long live runs use bench resume; do not re-spend completed cells.
4. **Tracker** — update `docs/orchestration-performance-plan.md` with bench IDs,
   metrics, and decisions before marking exits done.
5. **Local gate before live** — `uv run ruff check`, `basedpyright src`, targeted
   pytest; then live smoke (1 seed) before multi-seed gates.
6. **Cost discipline** — prefer Stage B–sized slices; escalate to Stage C width
   only when a gate requires it.
7. **No secret commits** — API keys via env only.

### Suggested PR boundaries

| PR | Contents |
| --- | --- |
| PR-N1a | Health contract + case/fixture alignment |
| PR-N1b | Isolation runner uses agent loop |
| PR-N1c | Review-path fixes (only if retest fails) |
| PR-N2 | Seeded repair harness + WP6 gate evidence |
| PR-N3 | Review default decision (often docs + tiny default flag) |
| PR-N4 | Planner/model(/context) ablation results + default knobs |
| PR-N5 | Stage F report + plan update |
| PR-N6 | Architecture scoring contract + Stage D run |

### Verification commands (templates)

```bash
# Local
uv run ruff check src tests
uv run basedpyright src
uv run pytest tests/graph/test_performance_work_packages.py tests/evaluation -q

# N1.A live Stage B retest
product-factory bench run --live \
  --subjects full_orchestration,single_agent_baseline \
  --cases code_cache,code_health,code_logging,code_retry \
  --seeds 5

# N1.B review slice
product-factory bench run --live \
  --subjects full_orchestration,full_orchestration_with_review \
  --cases code_cache,code_retry,code_logging \
  --seeds 3

# N2 seeded repair (exact flags depend on harness added in N2)
product-factory bench run --live --subjects seeded_repair --seeds 1  # TBD CLI

# N5 frontier (set oracle budget in config/flags)
product-factory bench run --live \
  --subjects full_orchestration,single_agent_baseline,frontier_reference \
  --limit 12 --seeds 3
```

---

## Dependency graph

```text
N1.A health ────────┐
N1.B review retest ─┼──► N3 review decision ──┐
N1.C isolation ─────┤                         ├──► N5 Stage F
                    │                         │
                    ├──► N2 seeded repair     ├──► N6 Stage D
                    │                         │
                    └──► N4 planner/model ────┘
                         (context optional)
```

---

## Definition of done (all of 1–6)

- [ ] N1–N6 package exits checked off with bench IDs
- [ ] WP6 and WP8 gates in `orchestration-performance-plan.md` resolved
- [ ] Stage D/E/F sections updated with decisions
- [ ] Defaults in code match measured keep/kill calls
- [ ] README / architecture docs only updated if user-facing defaults changed

---

## Tracker linkage

After approval, execute in order N1 → N2/N3 → N4 → N5/N6 and mirror status into
the **Execution update** section of
[`orchestration-performance-plan.md`](orchestration-performance-plan.md).
