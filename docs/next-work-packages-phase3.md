# Phase 3 — OpenCode-first host integration (stepped)

Implements [`handover_post_mvp.md`](handover_post_mvp.md) §3.1, §3.2, §4 Phase 3.
Plan: Cursor Phase 3 OpenCode host plan (do not treat the plan file as repo truth).

**Status legend:** `[ ]` pending · `[~]` in progress · `[x]` done

## Locked defaults (do not reopen)

- Phase 2 (local LLM router) stays deferred — OpenRouter/cloud + mock remain the model backends.
- **No OpenCode fork.** Host surface is: (1) stable PF protocol, (2) MCP
  adapter, (3) OpenCode config/commands as packaging.
  *Amended in Phase 3.G:* an **optional** OpenCode plugin adapter is allowed as
  packaging (see [`next-work-packages-phase3g.md`](next-work-packages-phase3g.md)),
  but the MCP adapter and `product-factory.host/v1` protocol remain the source
  of truth — no PF orchestration logic inside the plugin, and no host-specific
  protocol branch.
- Authority model unchanged: hosts submit curated requests; PF owns planning, grants, budgets, validation, approval.
- Host must not dump full chat transcripts into PF; only named request text + optional curated artifacts.
- Phase 4 MCP *connectors for workers* stay out of scope; Phase 3 MCP is **PF exposing itself to the host**, not giving workers arbitrary MCP.

## Workstreams

| Step | Title | Status |
| --- | --- | --- |
| P3.A | Host JSON protocol + submit/tail/status/approve CLI | [x] |
| P3.B | Local control API (extend observe serve) | [x] |
| P3.C | OpenCode reference integration (MCP + slash commands) | [x] |
| P3.D | `repository_investigation` + `technical_plan` packs | [x] |
| P3.E | Cancel / revise + evidence export | [x] |
| P3.F | Docs + regression gate | [x] |

## What changed (by workstream)

- **P3.A** — `product-factory.host/v1` envelope (`HostResponse`), shared
  `src/product_factory/host/` service layer, `product-factory host` CLI
  (`submit`, `attach`/`tail`, `status`, `inspect`, `artifacts`, `approve`,
  `reject`; cancel/revise/export-bundle landed in P3.E). Async `submit`
  spawns `host worker` (`python -m product_factory`); `--inline` / `--sync`
  for tests/debug. `tail` prefers observe HTTP, then SQLite, then
  `events.jsonl`. Human `run` remains sync. Protocol docs:
  [`host-integration.md`](host-integration.md).
- **P3.B** — POST control routes on `observe serve` / `serve` alias
  (`src/product_factory/api/control.py`): submit, approve, reject, cancel,
  revise, plan-preview. Same `HostResponse` envelope as CLI; write auth via
  `PRODUCT_FACTORY_OBSERVE_TOKEN` when configured.
- **P3.C** — Stdio MCP server (`src/product_factory/host_mcp/`) calling
  `HostService` directly; CLI `product-factory mcp`; tools `pf_submit`,
  `pf_status`, `pf_tail`, `pf_inspect`, `pf_approve`, `pf_reject`, `pf_cancel`,
  `pf_export` (HostResponse JSON). OpenCode packaging under
  [`examples/opencode/`](../examples/opencode/) (MCP snippet, slash commands,
  README, optional AGENTS.md).
- **P3.D** — `repository_investigation` pack (read-only evidence report +
  citation/section validators) and `technical_plan` pack; `architecture`
  aliases to `technical_plan` for one release. Registered in
  `workflows/registry.py`; frozen fixed planner templates.
- **P3.E** — Cooperative `cancel` (`cancel_requested` flag + typed `cancelled`),
  bounded `revise` after `awaiting_approval` (operator note + audit, no grant
  widening), and redaction-aware `export-bundle` zip (manifest, plan,
  validations, patch/report, cost summary, events).
- **P3.F** — Tracker exits checked with test IDs; README + handover Phase 3
  status pointers; focused + broader mock regression; mock host
  submit→approve→export smoke (live OpenRouter Stage B skipped — mock gate
  sufficient).

## Exit criteria (handover)

- [x] Reference host integration submits, streams, inspects, and approves a run end to end (P3.A + P3.C / thin script) —
      `tests/contract/test_host_protocol.py::test_host_submit_status_loop_with_mock`,
      `tests/contract/test_host_protocol.py::test_host_cli_submit_status_approve`,
      `tests/contract/test_host_control_api.py::test_control_submit_status_approve`,
      `tests/unit/test_host_mcp.py` (tool dispatch + stdio framing); CLI smoke
      `run-600efcba7fa7` (mock `--sync` submit → awaiting_approval → approve →
      export-bundle).
- [x] Investigation tasks produce evidence-backed reports without write grants (P3.D) —
      `tests/graph/test_workflow_packs_phase3.py::test_mock_investigation_produces_report_without_write_tools`,
      `tests/unit/test_workflow_packs.py::test_investigation_fixed_plan_has_no_write_tool_classes`
      (+ citation/section validators).
- [x] Planning outputs map requirements to acceptance criteria, task owners, and validation methods (P3.D) —
      `tests/graph/test_workflow_packs_phase3.py::test_architecture_and_technical_plan_alias_parity`,
      `tests/unit/test_workflow_packs.py::test_architecture_aliases_to_technical_plan`
      / `test_technical_plan_and_architecture_alias_parity` (pack metadata +
      `ARCHITECTURE.md` / `plan.json` parity).
- [x] Operator can understand status and retrieve results without reading SQLite or internal run directories (P3.A host JSON + P3.B HTTP + P3.C MCP) —
      host `status`/`inspect`/`artifacts`/`export-bundle`, control API OpenAPI write
      routes (`test_control_openapi_lists_write_routes`), MCP `pf_status` /
      `pf_inspect` / `pf_export`.

## Evidence

| Gate | ID / note |
| --- | --- |
| P3.A unit/contract | `uv run python -m pytest tests/unit/test_host_protocol.py tests/contract/test_host_protocol.py -q` |
| P3.B control API | `uv run python -m pytest tests/contract/test_host_control_api.py -q` |
| P3.C MCP unit | `uv run python -m pytest tests/unit/test_host_mcp.py -q` → **8 passed** |
| P3.D packs | `uv run python -m pytest tests/unit/test_workflow_packs.py tests/graph/test_workflow_packs_phase3.py -q` |
| P3.E cancel/revise/export | `tests/contract/test_host_protocol.py` (`test_host_cancel_mid_mock_run`, `test_host_revise_after_awaiting_approval`, `test_host_export_bundle_contents_redacted`) |
| P3.F focused regression | `uv run python -m pytest tests/unit/test_host_protocol.py tests/unit/test_host_mcp.py tests/unit/test_workflow_packs.py tests/contract/test_host_protocol.py tests/contract/test_host_control_api.py tests/graph/test_workflow_packs_phase3.py tests/graph/test_vertical_slice.py tests/graph/test_cli_contract.py -q` → **49 passed** (1 Starlette/`httpx` deprecation warning) |
| P3.F broader regression | `uv run python -m pytest tests/unit/ tests/contract/ tests/graph/ -q` → **203 passed** |
| P3.F mock host loop | CLI: `host submit --mock --sync` → `status` → `approve` → `export-bundle` on `tests/fixtures/sample_api`; **`run-600efcba7fa7`** → `completed`; export zip under `runs/…/export/evidence-bundle-*.zip` |
| Live OpenRouter smoke | **Skipped** — `OPENROUTER_API_KEY` present but mock host loop + graph/contract coverage met the gate; no Stage B / live investigation spend |
