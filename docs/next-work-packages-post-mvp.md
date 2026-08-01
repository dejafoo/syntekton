# Post-MVP — Prioritized implementation plan

High-level sequencing for the work described in:

- [`handover_remote_orchestration.md`](handover_remote_orchestration.md) — laptop ↔ AMD server control plane, workspaces, delivery, local models
- [`handover_post_mvp_workflows.md`](handover_post_mvp_workflows.md) — outcome packs, typed handoffs, lifecycle portfolio
- [`handover_post_mvp_skills.md`](handover_post_mvp_skills.md) — capabilities, connectors, evidence primitives, tool plane
- [`handover_post_mvp_skill_granularity.md`](handover_post_mvp_skill_granularity.md) — small composable skills, profiles, evaluation

This tracker is the **implementation order**. The handovers remain the detailed
design truth for contracts, authority rules, and exit criteria. Do not treat a
Cursor plan file as repo truth.

**Status legend:** `[ ]` pending · `[~]` in progress · `[x]` done

**Depends on:** Phase 4 exit criteria met (connectors, `quality_gate`, named
deliverables, host/v1, OpenCode plugin). See
[`next-work-packages-phase4.md`](next-work-packages-phase4.md).

## Locked defaults (do not reopen)

- **Authority stays in Product Factory code.** Workflows, skills, and profiles
  never grant network, filesystem, credentials, cloud, or deployment access.
  `ToolBroker` remains the sole execution path; connectors only narrow.
- **Evidence before mutation.** Public/approved discovery and read-only analysis
  land before release writes; controlled deployment is last and approval-gated.
- **Typed handoffs, not chat history.** Later runs consume content-addressed
  artifact references with schema version and digest. Familiar filenames are
  never proof of schema.
- **Small skill portfolio.** Prefer method skills + profiles + reference packs
  over combinatorial domain/vendor personas. Specialize only when evaluation
  shows durable value.
- **Existing-host first.** OpenCode remains the primary surface via
  `pf_run` / `pf_wait` / `pf_review` / `pf_merge` / `pf_decline`. Every pack and
  remote action must also work over host/v1 HTTP/CLI. MCP is an adapter, not a
  second orchestrator.
- **Remote does not imply laptop authority.** The server owns workspaces; the
  laptop owns final landing after explicit confirmation. No server push/PR/
  deploy by default.
- **Local-first models are a later remote package (R4), not a prerequisite for
  discovery packs.** OpenRouter + mock remain valid backends until the local
  gateway lands.
- **No public multi-tenant SaaS in this plan.** One trusted operator, one
  private server topology first.

## Why this order

The four handovers share one dependency spine:

```text
engine contracts (packs, handoffs, provenance, skills)
  → discovery evidence plane + feasibility pack
  → intake / plan / change quality
  → remote control + delivery (can partially parallelize after foundation)
  → change intelligence + local-gateway stand-in (PM4)
  → pre-PM5 refactoring gate (RF1–RF6) — mandatory
  → release / ops read plane
  → controlled deployment + domain packs
  → evaluation-driven expansion
```

Starting with more packs or live cloud connectors before WF0/S0/G0 would grow
coordinator conditionals and untyped prompt state. Starting with deployment
before evidence primitives would expand authority without auditability.
Starting PM5 before the RF gate would magnify shared-run state, grant/prompt
drift, capture bypasses, and coordinator workflow branching.

## Phase overview

| Phase | Theme | Primary packages | Status |
| --- | --- | --- | --- |
| PM0 | Foundation contracts | WF0, S0, G0 (+ R0 parallel) | [x] |
| PM1 | Feasibility discovery slice | S1, G1–G2, WF1 | [x] |
| PM2 | Framing and remote control | WF2, R1 | [x] |
| PM3 | Understand → decide → deliver remotely | WF3–WF4, S2, R2–R3 | [x] |
| PM4 | Change intelligence + local models | WF5–WF6, S3, G3, R4 | [x] |
| RF | Pre-PM5 refactoring / hardening gate | R1–R6 (refactoring handover) | [ ] |
| PM5 | Release / ops / deploy / harden | WF7–WF9, S4–S5, G4, R5 | [ ] blocked until RF |
| Ongoing | Evaluation and promotion | WF10, S6 | [ ] throughout |

## Workstreams

| Step | Title | Status |
| --- | --- | --- |
| PM0.A | Pack-engine + typed handoffs (WF0) | [x] |
| PM0.B | Provenance, classification, capability/skill plumbing (S0) | [x] |
| PM0.C | Skill/profile package contract + context budget (G0) | [x] |
| PM0.D | Private remote proof deployment (R0, parallel) | [x] |
| PM1.A | Base-skill evaluation baselines (G1) | [x] |
| PM1.B | Public evidence discovery plane (S1) | [x] |
| PM1.C | Discovery method skills (G2 subset) | [x] |
| PM1.D | `feasibility_discovery` pack (WF1) | [x] |
| PM2.A | `change_intake` pack (WF2) | [x] |
| PM2.B | Remote host transport + OpenCode loop (R1) | [x] |
| PM3.A | Investigation v2 + technical plan v2 (WF3–WF4) | [x] |
| PM3.B | Interface analysis + technical spike (S2, WF1.A) | [x] |
| PM3.C | Remote workspaces + delivery landing (R2–R3) | [x] |
| PM4.A | Change-set provenance + verification gate v2 (WF5–WF6) | [x] |
| PM4.B | Delivery intelligence / validation evidence (S3) | [x] |
| PM4.C | Repository-derived stack profiles (G3) | [x] |
| PM4.D | Worker supervision + local-model gateway (R4) | [x] |
| RF1 | Run/task execution isolation | [ ] |
| RF2 | EffectiveTaskPolicy + truthful grants/routing | [ ] |
| RF3 | ArtifactInstance + capture-policy unification | [ ] |
| RF4.INV | Workflow-branch inventory (design) | [ ] |
| RF4.EXT | Generic pack dispatch / PackExecutionPolicy | [ ] |
| RF4.SPIKE | Technical spike cites typed interface evidence | [ ] |
| RF5 | Real local-model proof (AMD OpenAI-compatible) | [ ] |
| RF6 | Observability, migration, operator hardening | [ ] |
| PM5.A | `release_readiness` + release/ops read plane (WF7, S4) | [ ] blocked |
| PM5.B | Controlled deployment execution (WF8, S5) | [ ] blocked |
| PM5.C | Domain/policy packs + deployment composition (G4) | [ ] blocked |
| PM5.D | Operational workflows (WF9) | [ ] blocked |
| PM5.E | Remote hardening / optional remote MCP (R5) | [ ] blocked |
| PMX | Evaluation, scorecards, controlled expansion (WF10, S6) | [ ] |

---

## PM0 — Foundation contracts

**Goal:** grow the portfolio without more `RunCoordinator` special cases or
prompt-only state.

### PM0.A — Pack-engine and typed handoffs (WF0) `[x]`

Move current packs onto registered pack-handler / task-template dispatch.
Introduce versioned handoff schemas, references, and lineage. Validate
input/output roles before model/tool calls.

**Do not:** invent a user-visible pipeline runner or automatic end-to-end chaining.

**Exit (summary):** all four current packs use generic dispatch without
regression; incompatible handoffs fail closed; artifact roles and eligible next
actions are visible on run detail.

**Done evidence:** `tests/unit/test_pack_handlers.py`, `tests/unit/test_handoffs.py`,
`tests/graph/test_workflow_packs_phase3.py`, `tests/graph/test_quality_gate_pack.py`;
handlers under `workflows/handlers/`; host `inspect` exposes `eligible_next_actions`.

### PM0.B — Provenance, classification, and capability plumbing (S0) `[x]`

Add source/connector receipt format, artifact-schema registry, data
classification / ingress guard, provenance projections, and compile-time
checks that a workflow only selects known capabilities, compatible skills,
permitted tool classes, and valid schemas.

**Exit (summary):** synthetic typed artifacts and receipts validate; disallowed
skill/capability/tool combos fail before execution; historical readers stay
compatible.

**Done evidence:** `tests/unit/test_schema_registry.py`,
`tests/unit/test_connector_receipts.py`, `tests/unit/test_classification.py`,
`tests/unit/test_compiler_pm0.py`.

### PM0.C — Skill and profile contract (G0) `[x]`

Implement skill package manifests, profile/reference-pack schemas, resolved
task-context manifests, prompt-budget enforcement, and observability digests.
Migrate existing skills (`architecture.system-design`, `coding.python-service`,
`quality.patch-review`, `security.threat-review`) into the contract without
widening authority.

**Exit (summary):** compiled tasks record exact skill/profile/reference digests;
over-budget or incompatible bundles fail before dispatch.

**Done evidence:** `tests/unit/test_skill_package.py`, `skills/manifest.py`,
`context/task_context.py`, profile stubs under `profiles/`.

### PM0.D — Private remote proof (R0, parallel) `[x]`

Document and stand up a single-server Product Factory reachable only via SSH
tunnel or private VPN: persistent data volume, service manager, canonical
external URL, token-gated API/SSE/dashboard from a laptop.

**Exit (summary):** restart preserves data; subscription URLs reconnect by
cursor; unauthenticated/non-tunnel access refused; no raw server paths in host
responses.

**Done evidence:** `docs/remote/r0-private-server.md`,
`examples/remote/docker-compose.yml`, `examples/remote/systemd/product-factory.service`.

**Parallelism note:** R0 may proceed beside PM0.A–C. Do not start R1 until PM0.A
and host/v1 remain compatible.

---

## PM1 — First product slice: feasibility discovery

**Goal:** prove the central thesis — a real open-ended research question becomes
a durable, source-grounded decision handoff without broad tools or frontier
dependency for every step.

Recommended first end-to-end slice across handovers: **S0 → S1 → WF1**, with
G1/G2 evaluation of method skills.

### PM1.A — Evaluate existing base skills (G1) `[x]`

Fixtures and scorecards for current skills vs generic/no-skill baselines on
supported model profiles.

**Exit (summary):** retain, revise, or retire each base skill from recorded
evidence, not preference.

**Done evidence:** `tests/unit/test_skill_eval_harness.py`,
`tests/eval_cases/feas_*.yaml`, `docs/skill-scorecards.md`;
`orchestration_with_skills` / `orchestration_no_skills` subjects + `disable_skills`
knob (mock-mode green; live scorecard is an operator action).

### PM1.B — Public evidence discovery plane (S1) `[x]`

Add `domain_research` and `decision_analysis`, source-policy profiles,
bounded `fetch_source` / extraction, citation normalization, research ledger
and decision-record artifacts. Keep Tavily as a candidate-source finder, not
the sole evidence store.

**Exit (summary):** adversarial fixtures for redirects, host policy, oversize,
stale/conflicting sources, and prompt injection; every substantive claim links
to evidence or is labeled inference/assumption/unknown.

**Done evidence:** `tests/unit/test_url_policy.py`,
`tests/unit/test_source_ledger.py`, `tests/unit/test_source_fetch.py`,
`tests/unit/test_evidence_tools.py`, `tests/unit/test_discovery_capabilities.py`,
`tests/unit/test_source_policy.py`, `tests/security/test_discovery_plane.py`,
`tests/connectors/test_connector_injection.py`.

### PM1.C — First method specializations (G2) `[x]`

Add only the highest-value discovery/integration methods — typically
`discovery.evidence-assessment`, `discovery.option-framing`, and later
`architecture.api-integration` / `quality.contract-verification` as PM3 needs
them. No vendor or vertical mega-skills.

**Done evidence:** `tests/unit/test_discovery_skills.py`,
`skills/discovery/evidence-assessment/`, `skills/discovery/option-framing/`,
`tests/fixtures/discovery/g2_*.yaml`.

### PM1.D — `feasibility_discovery` pack (WF1) `[x]`

Register the pack, typed inputs (decision, domain, jurisdiction, source
classes, budgets), land map role `feasibility_dossier`, fixed planner template,
and validators (`feasibility_sections`, `research_provenance`,
`option_comparison`, `regulated_claims_review`).

**Authority:** read-only; public/approved sources and synthetic examples only.

**Exit (summary):** pinned dossier consumable by intake/plan without copying
prompts; regulated fixtures cannot emit compliance/clinical verdicts without
expert-review outcomes; no live sensitive-system grants.

**Done evidence:** `tests/unit/test_feasibility_discovery_pack.py`,
`tests/graph/test_feasibility_discovery_pack.py`,
`tests/unit/test_pack_input.py`, `tests/contract/test_pack_input.py`.

Defer `technical_spike` (WF1.A) until PM3 when interface contracts and isolated
spike worktrees are ready.

---

## PM2 — Framing and remote control loop

**Goal:** stop vague requests from skipping straight to implementation, and let
OpenCode drive the server without slash commands — still without claiming local
merge of remote results.

### PM2.A — `change_intake` (WF2) `[x]`

Produce `ChangeBrief` (outcome, scope/non-goals, acceptance, constraints,
risks, unknowns) or a typed clarification request. Read-only.

**Exit (summary):** ambiguous fixtures yield questions; well-scoped fixtures
yield briefs; planning consumes briefs by pin; no write grants.

**Done evidence:** `tests/unit/test_change_intake_pack.py`
(`test_authority_is_read_only`, `test_schemas_writable`,
`test_intake_validators_pass_and_fail`,
`test_eligible_next_actions_and_feasibility_prefers_intake`),
`tests/graph/test_change_intake_pack.py`
(`test_ambiguous_request_lands_clarification`,
`test_well_scoped_feature_lands_change_brief`,
`test_well_scoped_defect_lands_change_brief`,
`test_technical_plan_accepts_change_brief_handoff_pin`);
fixtures under `tests/fixtures/intake/`.

### PM2.B — Remote host transport (R1) `[x]`

Implement `RemotePfClient` (and a small transport-neutral HTTP client) using
host/v1 over the private HTTPS control plane. Support plan/investigation/
discovery-class runs against server-registered repos or no-repo modes: submit,
wait, review, cancel/reject, SSE with poll fallback.

**Do not:** claim laptop `pf_merge` for remote deliveries yet (that is R3).

**Exit (summary):** OpenCode plugin tools work across the network; CLI/HTTP
parity; remote endpoint never falls back to a laptop data root.

**Done evidence:** `tests/unit/test_remote_pf_client.py`
(`test_meta_advertises_remote_capabilities`,
`test_observe_requires_bearer_when_token_configured`,
`test_remote_mode_rejects_laptop_repository_path`,
`test_remote_mode_accepts_repository_id`,
`test_remote_client_host_v1_parity`,
`test_remote_client_protocol_mismatch_fails_closed`,
`test_host_status_inspect_routes_exist`),
`tests/contract/test_host_control_api.py`;
OpenCode plugin `integrations/opencode-plugin/test/pf-client.test.ts`,
`integrations/opencode-plugin/test/tools.test.ts`
(remote submit/`repository_id`, SSE wait + poll fallback, merge unsupported,
fail-closed when `PRODUCT_FACTORY_REMOTE_URL` set);
`docs/remote/r0-private-server.md`, `examples/remote/`.

---

## PM3 — Understand, decide, and land remotely

**Goal:** strengthen the middle of the lifecycle and make remote repository work
safe.

### PM3.0 — Docker mock sandbox harness `[x]`

Runnable `Dockerfile` + compose stack in force-mock/remote mode as the local
substitute for a private AMD server, and the backend for every PM3 HTTP
integration test.

**Done evidence:** `Dockerfile`, `examples/remote/docker-compose.yml`,
`examples/remote/docker-entrypoint.sh`,
`examples/remote/repositories.docker.yaml`, `scripts/docker_remote_up.sh`;
`tests/integration/test_remote_docker.py`
(`test_meta_remote_mock_capabilities`, `test_auth_rejects_missing_bearer`,
`test_mock_change_intake_no_repo_lifecycle`,
`test_mock_technical_plan_registered_repo`,
`test_sse_tail_or_stream_available`) under `DOCKER_INTEGRATION=1`;
soft-skip wired in `scripts/verify.sh`; `docs/remote/docker-sandbox.md`.

### PM3.A — Investigation v2 and technical plan v2 (WF3–WF4) `[x]`

Evolve `repository_investigation` into a reusable evidence workflow that
consumes `ChangeBrief` and optional external sources while remaining
read-only. Strengthen `technical_plan` into an acceptance-mapped execution
contract that refuses invented product defaults.

**Exit (summary):** facts/inferences/unknowns and provenance are first-class;
acceptance↔verification links are machine-valid; change/release fixtures
consume plans by hash.

**Done evidence:** `tests/unit/test_pm3a_validators.py`
(`test_investigation_v2_requires_labels_and_fact_provenance`,
`test_technical_plan_links_every_acceptance_to_verification`,
`test_technical_plan_escalates_unknowns_instead_of_inventing_defaults`),
`tests/graph/test_workflow_packs_phase3.py`
(`test_mock_investigation_produces_report_without_write_tools`,
`test_architecture_and_technical_plan_alias_parity`,
`test_brief_to_investigation_to_plan_uses_artifact_hash_pins`),
`tests/unit/test_workflow_packs.py`, `tests/unit/test_schema_registry.py`;
fixtures under `tests/fixtures/investigation/` and `tests/fixtures/plan/`.

### PM3.B — Interface analysis and technical spike (S2, WF1.A) `[x]`

Add `interface_analysis`, initial contract inventories/diffs (start with
OpenAPI/JSON Schema), synthetic fixtures, and optional `technical_spike` that
writes only to a disposable confined worktree.

**Exit (summary):** spike reports carry hypothesis, method, measurements,
limits; no live authenticated partner endpoints required.

**Done evidence:** `tests/unit/test_interface_analysis.py`
(`test_inventory_addresses_openapi_and_json_schema`,
`test_diff_classifies_breaking_and_non_breaking_changes`,
`test_synthetic_fixture_and_simulation_stay_local`,
`test_spike_rejects_path_and_symlink_escape`,
`test_invalid_contract_is_rejected`,
`test_interface_capability_tools_and_skills_are_wired`,
`test_technical_spike_pack_compiles_and_schema_is_writable`),
`tests/graph/test_technical_spike_pack.py`
(`test_mock_technical_spike_uses_data_dir_scratch_and_emits_result`);
`src/product_factory/tools/interface_analysis.py`,
`skills/integration/contract-analysis/`, `skills/integration/technical-spike/`;
fixtures under `tests/fixtures/contracts/`.

### PM3.C — Remote workspaces and local landing (R2–R3) `[x]`

Typed `git_ref` (then bounded bundle) workspace sources; server registry and
pinned revisions; delivery manifests + blob download; laptop `LandingAdapter`
wired to OpenCode confirmation. Keep server-local `materialize` distinct from
remote land.

**Exit (summary):** remote code-change records exact base commit; hash-verified
landing; decline/path escape/digest mismatch fail closed without laptop writes.

**Done evidence (C1 `git_ref`):** `tests/unit/test_git_ref_workspace.py`
(`test_prepare_git_ref_resolves_exact_commit_and_detached_checkout`,
`test_prepare_rejects_floating_ref_and_commit_mismatch`,
`test_workspace_and_task_worktree_paths_cannot_escape`),
`tests/unit/test_remote_pf_client.py`
(`test_remote_mode_git_ref_records_exact_provenance`,
`test_remote_mode_rejects_unpinned_default_git_ref`,
`test_remote_mode_rejects_laptop_repository_path`,
`test_meta_advertises_remote_capabilities`),
`tests/integration/test_remote_docker.py::test_mock_git_ref_workspace_provenance`
(`DOCKER_INTEGRATION=1`); `src/product_factory/workspace/manager.py`.

**Done evidence (C2 delivery + landing):** `tests/unit/test_delivery_landing.py`
(`test_landing_verifies_then_writes_under_workspace`,
`test_landing_failures_write_nothing[missing|digest|base|escape]`,
`test_landing_rejects_changed_local_head`),
`tests/unit/test_remote_pf_client.py`
(`test_remote_delivery_manifest_blob_and_receipt`,
`test_remote_approve_never_maps_apply_to_server_workspace`);
OpenCode plugin `integrations/opencode-plugin/test/pf-client.test.ts`
(`landRemoteDelivery` → "verifies manifest and blob hashes before writing under
the workspace"; "fetches delivery manifests and binary blobs with bearer auth")
and `integrations/opencode-plugin/test/tools.test.ts` (`pf_merge remote mode` →
"decline performs no approval, download, or local write";
`pf_merge confirmation gate` → "does NOT merge when no ask/permission function
is available (fail-closed)"); `src/product_factory/delivery/`,
`src/product_factory/api/delivery.py`, CLI `product-factory land`.

**Gate evidence:** `uv run pytest -m "not integration"` (717 passed, 3 skipped;
single pre-existing failure `tests/unit/test_host_mcp.py::test_pf_submit_builds_request_and_returns_host_response`,
a MagicMock/`run_budget_from_policy` artifact that also fails at the PM2 base),
`integrations/opencode-plugin` `npm test` (52 passed),
`DOCKER_INTEGRATION=1 uv run pytest tests/integration/test_remote_docker.py`
(6 passed), and `OPENCODE_INTEGRATION=1 scripts/opencode_remote_smoke.sh`.

---

## PM4 — Change intelligence and local-first execution `[x]`

**Goal:** make change/verify workflows cite structured evidence, and let the AMD
runtime become the default model plane later through configuration-only cutover.

### PM4.A — Change-set provenance and verification gate v2 (WF5–WF6) `[x]`

Strengthen repository-change (and migration specialization only when justified)
with content-addressed `ChangeSet` provenance. Evolve `quality_gate` into a
verification gate that maps acceptance criteria to durable evidence without
gaining write/repair authority.

**Done evidence:** `tests/unit/test_pm4a_changeset.py`
(`test_repository_change_v2_emits_content_addressed_change_set`,
`test_repository_change_fails_closed_on_bad_plan_pin`);
`tests/unit/test_pm4a_verification_gate.py`
(`test_verification_report_maps_acceptance_to_evidence`,
`test_runtime_validation_evidence_is_consumed`,
`test_skipped_registered_validator_is_insufficient_evidence`,
`test_quality_gate_v2_keeps_no_repair_authority`);
`tests/graph/test_quality_gate_pack.py`, `tests/graph/test_vertical_slice.py`.

### PM4.B — Delivery intelligence (S3) `[x]`

Language-aware repository intelligence where it pays off, versioned validation
profiles/parsers, baseline comparison, and quality-evidence skill. Start from
evaluation fixture languages only.

**Done evidence:** `tests/unit/test_pm4b_validation_evidence.py`
(`test_pytest_parser_normalizes_failures_and_summary`,
`test_basedpyright_parser_normalizes_diagnostics`,
`test_parsers_fail_closed_on_malformed_output`,
`test_parsers_preserve_partial_outcomes_when_truncated`,
`test_behavioral_validation_can_persist_evidence`,
`test_baseline_comparison_uses_previous_evidence`,
`test_artifact_cannot_introduce_unregistered_command`,
`test_skill_cannot_introduce_unregistered_command`);
`skills/quality/evidence-gate/`.

### PM4.C — Repository-derived stack profiles (G3) `[x]`

Deterministic stack profiles from manifests, lockfiles, and registered
validation commands — never unbounded model inference of the whole tree.

**Done evidence:** `tests/unit/test_pm4c_stack_profiles.py`
(`test_sample_api_profile_is_stable_and_compact`,
`test_javascript_fixture_uses_declared_runtime_and_dependencies`,
`test_unknown_and_ambiguous_trees_fail_closed`,
`test_profile_registry_round_trips_yaml_and_digest`,
`test_profile_digest_slots_are_stable_in_context_and_compiler`).

### PM4.D — Worker supervision and local models (R4) `[x]`

Leased supervised workers with restart recovery; OpenAI-compatible local
gateway; health/capability probes; explicit local→cloud fallback policy and
cost observability.

**Exit (summary):** interrupted leased runs recover cleanly; cloud escalation
records allowed reason and respects budget; local/cloud routes are observable.

**Done evidence:** `tests/unit/test_pm4d_gateway_router.py`
(`test_local_route_success`, `test_capability_miss_allows_cloud_fallback`,
`test_capability_miss_denies_unapproved_fallback`,
`test_routing_budget_guard_rejects_before_probe_or_fallback`,
`test_forced_mock_construction_is_unchanged`,
`test_openai_compatible_completion_and_probe`,
`test_instrumentation_emits_route_dimensions`);
`tests/unit/test_worker_leases.py`
(`test_one_active_writer_per_worktree`,
`test_expired_lease_is_reclaimed_with_incremented_attempt`,
`test_expiry_scan_resumes_and_records_outcome`,
`test_recovery_failure_is_typed_and_retained`);
`tests/integration/test_remote_docker.py::test_mock_worker_lease_recovers_after_container_restart`;
`docs/remote/local-model-gateway.md`.

**Gate evidence:** focused PM4 contract/A/B/C/D unit + graph gate (93 passed);
`uv run pytest -m "not integration"` (760 passed, 3 skipped; only the known
pre-existing `tests/unit/test_host_mcp.py::test_pf_submit_builds_request_and_returns_host_response`
MagicMock/`run_budget_from_policy` budget failure remains); OpenCode plugin
`npm test` (52 passed); `DOCKER_INTEGRATION=1 uv run pytest
tests/integration/test_remote_docker.py` (7 passed, including restart recovery);
changed-file Ruff and Basedpyright checks plus `git diff --check` pass.
OpenRouter routing smoke was skipped because `OPENROUTER_API_KEY` was unavailable.

PM4 ships the gateway/router/probe/fallback/observability plane and supervised
workers with OpenRouter as the local-route stand-in. A local-model runtime and
AMD hardware are **not shipped**; hardware cutover remains a configuration-only
change to the OpenAI-compatible local profile endpoint.

---

## RF — Pre-PM5 refactoring and hardening gate `[ ]`

**Goal:** make concurrent remote runs, effective grants, capture policy, and
pack extensibility correct before expanding authority with release/deploy/ops
workflows.

**Normative gate:** [handover_post_mvp_refactoring.md](handover_post_mvp_refactoring.md)
(§2 locked rules, §4 packages, §7 PM5 entry checklist).

**Do not start PM5** (`release_readiness`, `deployment_execution`,
`incident_triage`, `service_health_review`, production-like connectors, or
additional deployment authority) until every §7 technical and operator outcome
is demonstrated.

| Step | Plan / contract | Status |
| --- | --- | --- |
| RF1 | [next-work-packages-r1-isolation.md](next-work-packages-r1-isolation.md) | [ ] |
| RF2–RF3 contracts | [ADR-007](architecture/ADR-007-effective-policy-and-artifact-instances.md) | proposed |
| RF2 | EffectiveTaskPolicy + stack-profile resource + route identity | [ ] |
| RF3 | ArtifactInstance + content-class × capture matrix | [ ] |
| RF4.INV / EXT / SPIKE | [next-work-packages-r4-pack-extensibility.md](next-work-packages-r4-pack-extensibility.md) | [ ] |
| RF5 | Real local OpenAI-compatible proof (opt-in; not unit CI) | [ ] |
| RF6 | Dashboard/API migration + operator guide | [ ] |

**Sequence:** RF1 → RF2 → (RF3 ∥ RF4.INV) → RF4.EXT after RF2; RF4.SPIKE
tracked separately; RF5 after route identity from RF2; RF6 last; then PM5.

### RF1 — Run and task execution isolation `[ ]`

See [next-work-packages-r1-isolation.md](next-work-packages-r1-isolation.md).
Introduce `RunExecutionContext`; stop mutating shared gateway/audit; add
interleaving race tests.

### RF2 — Resolve policy once `[ ]`

Implement `effective_task_policy.v1` per ADR-007. Grant before
`assemble_context`; persist prompt reductions; pin rendered stack profile;
named cloud fallback identity.

### RF3 — Artifact and capture unification `[ ]`

Implement `ArtifactInstance` and the capture matrix per ADR-007. Close the
artifact-content bypass for raw validation/source captures.

### RF4 — Pack extensibility (split) `[ ]`

- **RF4.INV:** branch inventory design artifact.
- **RF4.EXT:** generic dispatch + `PackExecutionPolicy` (after RF2).
- **RF4.SPIKE:** technical spike cites typed interface tools/artifacts
  (product completion; separate from EXT).

See [next-work-packages-r4-pack-extensibility.md](next-work-packages-r4-pack-extensibility.md).

### RF5 — Local-first model plane proof `[ ]`

Real AMD (or equivalent) OpenAI-compatible endpoint behind existing gateway
contracts; probes; circuit breaker; opt-in live evaluation. OpenRouter remains
the stand-in until this exits.

### RF6 — Observability and operator hardening `[ ]`

Projections for policy/visibility/route; additive migrations; package upgrade
smoke; operator guide for legacy capture and local/cloud labels.

---

## PM5 — Release, operations, deployment, hardening

**Status:** blocked until RF §7 entry gate passes.

**Goal:** close the lifecycle with evidence-led release/ops and a narrowly
useful non-production deployment path.

### PM5.A — Release readiness and read plane (WF7, S4)

`release_readiness` pack plus `release_analysis` / `operations_analysis` over
one owned Git/CI source and one observability or incident source. Monitor-only.

### PM5.B — Controlled deployment (WF8, S5)

`deployment_execution` for **one** non-production target and **one** operator
adapter: approval references, immutable artifact binding, idempotent
start/status/health/rollback receipts, restart reconciliation,
`deployment.change-control` skill.

**Production rollout is out of scope** until separate policy and trial evidence.

### PM5.C — Domain/policy packs (G4)

Versioned public domain reference packs, policy profiles, and human-review
gates for regulated discovery and deployment composition. Provider target
profiles only behind existing connector controls.

### PM5.D — Operational workflows (WF9)

`incident_triage` and `service_health_review` as read-only packs consuming
operational evidence and producing follow-up intakes/plans.

### PM5.E — Remote hardening (R5)

Ingress, uploads, rate limits, audit, backup/restore, operator docs. Streamable
HTTP MCP only if a real non-OpenCode host needs it — not a prerequisite for
OpenCode remote use.

---

## Ongoing — Evaluation and promotion (PMX / WF10 / S6)

Run continuously from PM1 onward; do not leave it as a final polish phase.

- Fixture corpus keyed by pack/skill/connector version and content hash.
- Scorecards: quality, unsupported-claim rate, correct
  unknown/escalation, latency, local/fallback rate, cost, reviewer rework.
- Experiment registry and regression gates before changing defaults.
- Expand source profiles, contract formats, providers, and vertical packs
  **only** where measured outcomes justify maintenance and authority cost.

---

## Parallelism rules

| May run in parallel | Must wait for |
| --- | --- |
| R0 beside PM0.A–C | — |
| R1 after R0 + stable host/v1 | Prefer after PM0.A so new packs are remote-visible consistently |
| R2–R3 after R1 | Prefer after WF0 handoffs so deliveries carry typed roles |
| R4 after R1 (service lifecycle) | Prefer after PM0 so routing digests exist |
| G1 anytime after G0 | S1 / WF1 for discovery skills |
| WF2 after WF1 dossier contract | S1 evidence plane if intake cites external claims |
| S5 / WF8 | S4 + WF7 + R2/R3 approval/delivery contracts |
| RF4.INV beside RF2 | RF4.EXT migration until EffectiveTaskPolicy (RF2) |
| RF4.SPIKE beside RF4.EXT | PM5 until both EXT and SPIKE exits |
| RF5 beside RF3/RF4 after RF2 route identity | — |
| PM5.* | **All** RF1–RF6 + handover §7 checkboxes |

Never parallelize by weakening an earlier package’s approval, capture, or
artifact-ownership rules.

## First 90-day recommendation

PM0–PM4 are landed. Next:

1. **RF1** — run/task isolation
   ([next-work-packages-r1-isolation.md](next-work-packages-r1-isolation.md)).
2. **RF2–RF3** — implement contracts frozen in
   [ADR-007](architecture/ADR-007-effective-policy-and-artifact-instances.md).
3. **RF4.INV → RF4.EXT** and **RF4.SPIKE** in parallel tracks
   ([r4 plan](next-work-packages-r4-pack-extensibility.md)).
4. **RF5–RF6** then close handover §7 before any PM5 pack.

Stop and reassess before investing in live partner APIs, operations connectors,
or deployment mutation.

## Definition of done (portfolio)

Post-MVP development is ready to describe as an agentic software-delivery
lifecycle when an operator can:

1. start from an uncertain domain idea and produce a source-grounded
   feasibility dossier;
2. frame a bounded change brief and technical plan from pinned evidence;
3. implement and independently verify a change with durable provenance;
4. decide release readiness from evidence rather than prose confidence;
5. optionally perform a controlled non-production deployment under explicit
   approval; and
6. (when remote mode is enabled) do the above from a laptop through OpenCode’s
   existing tools, landing verified results locally without granting the server
   laptop filesystem authority.

At every transition, the host can identify source artifacts, authority,
validation evidence, cost, status, selected skill/profile digests, and the
exact next human/CLI action.
