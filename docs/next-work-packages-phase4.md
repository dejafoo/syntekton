# Phase 4 — Connectors, `quality_gate`, and named deliverables

> **Status: exit criteria met.** All workstreams P4.A–F closed. Connector policy,
> injection, audit, and `quality_gate` suites are always-on and offline; the live
> Tavily and filesystem-MCP smokes are env-gated. The OpenCode UAT passed on
> `opencode` **1.18.4** for both a named `technical_plan` land and a
> `quality_gate` multi-land. See exit criteria / evidence below.

Extends [`next-work-packages-phase3g.md`](next-work-packages-phase3g.md): the
`materialize` host action and the OpenCode plugin from Phase 3.G are the landing
spine this phase builds on. Plan: Cursor Phase 4 connectors plan (do not treat
the plan file as repo truth).

**Status legend:** `[ ]` pending · `[~]` in progress · `[x]` done

## Locked defaults (do not reopen)

- **Authority unchanged.** Connectors never grant capabilities. `ToolBroker`
  remains the sole execution path ([ADR-004](architecture/ADR-004-tool-broker.md));
  the connector broker is an adapter behind it, subject to the same grant,
  `max_calls`, and budget checks. A configured connector is not a trusted one.
- **Read-only first.** Tavily and the local filesystem MCP expose read tools
  only. No write or destructive MCP tools in Phase 4; a write-capable manifest is
  denied until an operator names it in `allow_write_connectors`.
- **Providers locked.** Tavily Search API for web; `@modelcontextprotocol/server-filesystem`
  (stdio) for local files, with PF allowlisting a small read tool set client-side
  regardless of what the server advertises.
- **Deliverable identity splits role from land name.** A stable role key
  (`architecture_document`) is separate from the store/land filename
  (`integration_testing_architecture.md`). Validators key off content and role,
  never the basename.
- **Landing stays a host action.** Renamed and multi-document deliverables land
  via `materialize` / `materialize-all`; the plugin still performs no filesystem
  writes of its own.
- **No auto-merge.** Multi-artifact landing needs one explicit operator
  confirmation, same as Phase 3.G.
- Host protocol stays `product-factory.host/v1`. Worker connectors are orthogonal
  to the host MCP server (`product-factory mcp`).
- Phase 2's local model router stays deferred; OpenRouter + mock remain the
  backends.

## Workstreams

| Step | Title | Status |
| --- | --- | --- |
| P4.A | Artifact land map: pack specs, submit overrides, `materialize-all`, plugin loop | [x] |
| P4.B | Connector manifest / registry / broker, typed errors, audit events, harness | [x] |
| P4.C | Read-only Tavily `web_search` connector (mock + gated live) | [x] |
| P4.D | Read-only local filesystem MCP connector (allowlist + root confinement) | [x] |
| P4.E | `quality_gate` pack with a three-document land map | [x] |
| P4.F | Tracker, docs, `verify.sh` gates, OpenCode UAT | [x] |

## What changed (by workstream)

### P4.A — Artifact land map

The problem this phase set out to fix: `technical_plan` always composed
`ARCHITECTURE.md`, and the plugin always landed `docs/ARCHITECTURE.md`, so asking
OpenCode for an integration-testing architecture produced a correctly-written
document under the wrong name.

- New [`workflows/artifacts.py`](../src/product_factory/workflows/artifacts.py)
  defines `ArtifactLandSpec` (role, default logical name, default dest path,
  `landable`, `renamable`, `required`), the resolved `ArtifactLandMap`, and the
  role constants. `WorkflowPack` carries `artifacts: tuple[ArtifactLandSpec, ...]`
  and folds them into its content hash, so a naming change is a pack version
  change.
- Resolution order, first wins: host submit override → planner `final_artifacts`
  (non-fixed packs) → pack default. `FinalArtifactSpec` gained `role` and
  `dest_path`; `RunRequest` gained typed `artifact_overrides` (the previously dead
  `requested_artifacts` stays as a deprecated alias).
- Overrides are validated at submit-parse time, before a run exists: destinations
  must resolve under the repository root, and a spec marked `renamable=False`
  (the `proposed_patch` role) refuses renaming outright.
- The coordinator resolves the land map once per run and derives every store name,
  `output/` write, and composed H1 title from it. Section validators moved to
  content/role matching, so a renamed document validates identically.
- Host surfaces: `host submit --artifact-override ROLE=PATH` and
  `--artifact-name ROLE=FILENAME`; `inspect` / `artifacts` publish `role`,
  `logical_name`, and `suggested_dest_path`; new `host materialize-all <run_id>`
  lands every resolved entry, each still path-checked and audited individually.
  Mirrored as the `pf_materialize_all` MCP tool and
  `POST /api/v1/runs/{id}/materialize-all`.
- The plugin prefers the inspected land map over its hardcoded
  `MATERIALIZE_DEFAULTS`, accepts `artifact_overrides` on `pf_run`, and loops the
  land map inside a single confirmed `pf_merge`.

### P4.B — Connector framework

New package [`src/product_factory/connectors/`](../src/product_factory/connectors/):

- `ConnectorManifest` declares id, version, provider, risk class, tool class, per-tool
  schemas, permissions (`read`/`write`/`destructive`), egress domains, the *name*
  of the credential env var (never its value), timeouts, concurrency,
  `requires_approval`, and result retention.
- `ConnectorRegistry` pairs manifests with handlers.
  [`config/connectors.yaml`](../config/connectors.yaml) is the operator surface,
  and it can only narrow: it may disable a connector, lower a timeout or result
  ceiling, shrink the egress allowlist, or add an approval requirement — never the
  reverse. Registration alone does not make a tool grantable.
- `ConnectorBroker` is the single enforcement point, reached only from
  `ToolBroker._dispatch` for connector-backed tool names. It authorizes, bounds
  and truncates results, labels them untrusted, attaches provenance, and audits
  every attempt.
- Typed errors (`ConnectorPolicyDenied`, `ConnectorEgressDenied`,
  `ConnectorUnavailable`, `ConnectorTimeout`) exist so that an outage is an
  outage: none of them degrade into "retry with a different model". Denials are
  also `UnsafeOperationError`s, so existing safety handling applies unchanged.
- Audit: `connector.invoked` / `connector.denied` / `connector.failed` carry the
  policy decision, arguments hash, result sha256, and provenance refs, with
  secrets redacted before storage.

### P4.C — Tavily web search (read-only)

- One tool, `web_search`, in tool class `web_read`, backed by the Tavily Search
  API with `TAVILY_API_KEY` read from the environment and never logged.
- Bounded by `max_results` and per-result character caps; results carry source
  URL, `retrieved_at`, and an excerpt sha256, and are recorded as untrusted tool
  calls.
- Mock mode returns deterministic fixtures under `--mock` /
  `PRODUCT_FACTORY_FORCE_MOCK=1`, so CI never reaches the network.
- Capability wiring adds `web_read` to `architecture`, `repository_analysis`,
  `security_review`, and `test_design` only — deliberately not to `implementation`
  or `repair`. `skill_grants` resolves connector tools dynamically so a skill
  prohibition cannot be sidestepped by a newly registered connector.

### P4.D — Local filesystem MCP (read-only)

- [`connectors/mcp_client.py`](../src/product_factory/connectors/mcp_client.py)
  is a minimal stdio JSON-RPC client: shell-less `subprocess.Popen`, NDJSON
  framing, version negotiation, and a reader thread so a silent server hits the
  timeout instead of blocking the caller forever.
- Three allowlisted tools — `mcp_read_file`, `mcp_list_directory`,
  `mcp_search_files` — in tool class `mcp_filesystem_read`. Anything else the
  server advertises is rejected client-side.
- Roots come from configuration (worktree or explicitly readable paths, never the
  host home by default) and every path is resolved and re-checked inside them, so
  symlink and `..` escapes are denied before the call leaves PF.
- A missing binary, a crashed server, a JSON-RPC error, or an `isError` result all
  surface as `ConnectorUnavailable`. Example config:
  [`examples/connectors/filesystem-mcp.yaml`](../examples/connectors/filesystem-mcp.yaml).

### P4.E — `quality_gate` pack

- [`workflows/quality_gate.py`](../src/product_factory/workflows/quality_gate.py)
  is the first multi-artifact pack: `test_plan` → `docs/TEST_PLAN.md`,
  `quality_findings` → `docs/QUALITY_FINDINGS.md`, `security_evidence` →
  `docs/SECURITY_EVIDENCE.md`, all renamable through the P4.A land map.
- Fixed v1 planner: design tests → execute registered validation commands →
  security review → independent review → three composition tasks, one per role.
  The coordinator now tracks `composer_roles` and `documents_by_role` instead of
  assuming a single composed document.
- The pack is read-only by construction: it is granted `run_validation_command`
  but never repository write tools.
- `findings_are_deliverable` in the pack's validation policy stops blocking
  findings from spawning repair tasks — in a quality gate, a blocking finding *is*
  the deliverable, not a defect to fix.
- Generic `validate_document_sections` in the validation pipeline lets a pack
  declare its required headings, so a new deliverable shape needs no new
  validator. Secret scanning and citation checks apply to all three documents.

### P4.F — Tracker, docs, gates, UAT

- This tracker; Phase 4 status in
  [`handover_post_mvp.md`](handover_post_mvp.md); named deliverables,
  `materialize-all`, `quality_gate`, connector policy, and the new env vars in
  [`host-integration.md`](host-integration.md); plugin README updated for named
  deliverables and multi-document merge.
- [`scripts/verify.sh`](../scripts/verify.sh) runs the connector policy /
  injection / grant / audit suites explicitly (they are inside the full run
  already, but naming them attributes a regression to connectors instead of
  burying it), and gates the live smokes behind `TAVILY_INTEGRATION=1` and
  `MCP_FILESYSTEM_INTEGRATION=1`.
- [`scripts/opencode_plugin_smoke.sh`](../scripts/opencode_plugin_smoke.sh) grew
  the Phase 4 UAT: submit with `--artifact-override`, assert the inspected land
  map carries the requested name, `materialize-all`, then a `quality_gate` run
  that lands all three documents from one call.

## Exit criteria

- [x] A host can name a deliverable and it lands under that name — 29 land-map
      resolution/precedence/rejection cases in
      `tests/unit/test_artifact_land_map.py`;
      `tests/graph/test_named_deliverables.py::test_technical_plan_honors_requested_deliverable_name`,
      `::test_investigation_honors_requested_report_name`; contract coverage for
      `--artifact-override`, land-map exposure, and `materialize-all` in
      `tests/contract/test_host_protocol.py`.
- [x] Defaults are unchanged for callers that ask for nothing —
      `tests/graph/test_named_deliverables.py::test_technical_plan_default_name_is_unchanged`
      still lands `docs/ARCHITECTURE.md`.
- [x] Every external call has an audit record, a policy decision, a bounded
      result, and provenance —
      `tests/contract/test_connector_audit.py::test_successful_invocation_persists_provenance_and_hashes`,
      `::test_denied_invocation_persists_the_denial_reason`,
      `tests/connectors/test_connector_policy.py::test_oversized_results_are_truncated_and_flagged`,
      `::test_provenance_defaults_to_a_retrieval_timestamp`.
- [x] A hostile tool result cannot widen grants, register tools, impersonate
      metadata, or cause an unapproved write —
      `tests/connectors/test_connector_injection.py::test_hostile_result_cannot_register_tools_or_widen_the_grant`,
      `::test_hostile_result_cannot_impersonate_envelope_metadata`,
      `::test_injected_instructions_are_inert_text`; provider-level equivalents in
      the Tavily and filesystem-MCP suites.
- [x] Connector outages produce typed errors with no silent model fallback —
      `tests/connectors/test_connector_policy.py::test_provider_outage_becomes_connector_unavailable`,
      `::test_handler_timeouts_become_connector_timeout`,
      `::test_missing_credential_is_typed_unavailable_not_a_model_failure`.
- [x] Operator config can only narrow policy —
      `::test_config_can_narrow_egress_but_not_widen_it`,
      `::test_config_cannot_grant_egress_to_a_network_free_connector`,
      `::test_operator_config_can_lower_but_not_raise_a_timeout`,
      `::test_write_capable_connector_is_denied_until_operator_opts_in`.
- [x] Secrets never reach a result or the audit trail —
      `::test_credential_value_never_appears_in_audit_or_result`,
      `tests/contract/test_connector_audit.py::test_a_secret_in_a_connector_result_is_redacted_before_storage`.
- [x] Quality findings are evidence-backed and seeded defects are detected at the
      predeclared rate —
      `tests/graph/test_quality_gate_pack.py::test_seeded_correctness_defect_is_reported_not_repaired`,
      `::test_seeded_detection_rate_meets_predeclared_threshold` (1/1 against
      `SEEDED_DETECTION_THRESHOLD`), with the false-positive guard in
      `::test_clean_repository_yields_no_blocking_findings`.
- [x] One confirmation lands all `quality_gate` documents —
      `::test_materialize_all_lands_every_quality_deliverable`,
      `::test_quality_gate_never_receives_repository_write_tools`.
- [x] Real OpenCode lands a user-named architecture document and a `quality_gate`
      set — gated smoke PASS on `opencode 1.18.4`.

## Evidence

| Gate | ID / note |
| --- | --- |
| P4.A land map unit | `uv run pytest tests/unit/test_artifact_land_map.py -q` → **29 passed** |
| P4.A naming graph | `uv run pytest tests/graph/test_named_deliverables.py -q` → **4 passed** |
| P4.A host surfaces | `uv run pytest tests/contract/test_host_protocol.py tests/unit/test_host_mcp.py tests/contract/test_host_control_api.py -q` (overrides, land map in `inspect`, `materialize-all`, unsafe override rejected) |
| P4.B–D connector harness | `uv run pytest tests/connectors tests/contract/test_connector_audit.py -q` → **133 passed, 2 skipped** (the two skips are the live Tavily and real-MCP smokes) |
| P4.C live Tavily | `TAVILY_INTEGRATION=1 uv run pytest tests/connectors/test_tavily_connector.py -k live_search` — opt-in; needs `TAVILY_API_KEY` |
| P4.D real MCP server | `MCP_FILESYSTEM_INTEGRATION=1 uv run pytest tests/connectors/test_filesystem_mcp.py -k real_filesystem` — opt-in; needs `npx` |
| P4.E quality graph | `uv run pytest tests/graph/test_quality_gate_pack.py -q` → **7 passed** |
| P4.E pack unit | `uv run pytest tests/unit/test_workflow_packs.py -q` (registration, declared deliverables, land map, read-only plan, section validation) |
| P4.A/E plugin unit | `cd integrations/opencode-plugin && npm test && npm run check` — vitest green + `tsc --noEmit` |
| P4.F OpenCode UAT | `OPENCODE_INTEGRATION=1 bash scripts/opencode_plugin_smoke.sh` → **PASS** on `opencode 1.18.4`: tools visible; `docs/ARCHITECTURE.md` (default); `docs/integration_testing_architecture.md` (named override via `materialize-all`); `quality_gate` landing `TEST_PLAN.md` + `QUALITY_FINDINGS.md` + `SECURITY_EVIDENCE.md` in one call |
| P4.F verify gates | `scripts/verify.sh` — always-on connector suites; live smokes behind `TAVILY_INTEGRATION` / `MCP_FILESYSTEM_INTEGRATION`; OpenCode smoke behind `OPENCODE_INTEGRATION` |
| Live model smoke | _not planned_ — the mock host loop plus contract coverage is the gate (same rationale as P3.F/P3.G) |

## Phase 5+ readiness

- **Adding a deliverable** is a pack edit: declare an `ArtifactLandSpec` and its
  required sections. Naming, override validation, `materialize-all`, plugin
  landing, and evidence-bundle export all follow from the role with no client
  change.
- **Adding a connector** is a manifest plus a handler. Policy, audit, typed
  errors, grant resolution, and result bounding are inherited; nothing becomes
  grantable until an operator enables it in `config/connectors.yaml`.
- **Write-capable and remote connectors** already have their refusal path
  (`allow_write_connectors`, egress allowlists), so Phase 6 deployment connectors
  extend a policy that exists rather than inventing one.

## Non-goals (Phase 4)

- Write-capable or remote MCP servers; deployment connectors (Phase 6)
- Replacing the host MCP server with a worker MCP
- Auto-landing without confirmation
- Publishing the OpenCode plugin to npm
- Phase 5 dashboard work (landed separately)
