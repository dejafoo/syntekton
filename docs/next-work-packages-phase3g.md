# Phase 3.G — OpenCode plugin + `materialize` (stepped)

Extends [`next-work-packages-phase3.md`](next-work-packages-phase3.md) with a
vendor-neutral `materialize` host action and an **optional** OpenCode plugin
adapter on top of the same protocol.
Plan: Cursor Phase 3.G OpenCode plugin plan (do not treat the plan file as repo
truth).

**Status legend:** `[ ]` pending · `[~]` in progress · `[x]` done

## Locked defaults (do not reopen)

- **MCP / `product-factory.host/v1` stay the durable host protocol.** The
  OpenCode plugin is optional packaging over it — no OpenCode fork, no PF
  orchestration logic inside the plugin. This amends the earlier Phase 3
  "no plugin SDK" default (see [Phase 3 locked defaults](next-work-packages-phase3.md#locked-defaults-do-not-reopen)).
- **Landing artifacts into a project is a PF host action** (`materialize`, or
  `approve --apply` for patches) — never ad-hoc plugin filesystem writes, so
  CLI, HTTP, MCP, and the plugin share one audited path.
- Authority model unchanged: hosts submit curated request text; PF owns
  planning, grants, budgets, validation, approval. `materialize` does **not**
  widen worker grants.
- No auto-merge: a plugin merge/land step requires explicit user confirmation.
- **Testing split:** MCP + host contract + plugin unit tests are always-on in
  CI; the OpenCode CLI smoke is **gated** (runs when `opencode` is on `PATH` or
  `OPENCODE_INTEGRATION=1`, otherwise skips with a stated reason).
- No npm publish in this workstream (in-repo / `file:` install is enough).
- Phase 4 worker MCP connectors and `quality_gate` packs remain out of scope.

## Workstreams

| Step | Title | Status |
| --- | --- | --- |
| P3.G.A | `HostService.materialize` + `host materialize` CLI + contract tests | [x] |
| P3.G.B | `pf_materialize` MCP tool + `POST …/materialize` control route + MCP stdio smoke | [x] |
| P3.G.C | `integrations/opencode-plugin/` (`pf_run`/`wait`/`review`/`merge`/`decline`, ask-gated merge, `PfClient`) | [x] |
| P3.G.D | Gated OpenCode CLI smoke script + optional `verify.sh` hook | [x] |
| P3.G.E | Tracker + host-integration / handover / Phase 3 locked-default amendments | [x] |

## What changed (by workstream)

- **P3.G.A** — `HostService.materialize(...)` copies a known run artifact (e.g.
  `ARCHITECTURE.md`, `EVIDENCE_REPORT.md`) into the run's `repository_path`.
  Allowed only from `awaiting_approval` / `completed`; destination must resolve
  under the repo root (escape rejected as a typed `HostResponse` error); emits
  an `artifact.materialized` audit event with sha256 + written path. CLI:
  `product-factory host materialize <run_id> --artifact … --to …`. Patch apply
  still goes through `approve --apply`.
- **P3.G.B** — `pf_materialize` MCP tool (`artifact`, `dest_path`,
  `overwrite?`) dispatched through the same `HostService`, plus
  `POST /api/v1/runs/{id}/materialize` on the observe/`serve` control API with
  the same `HostResponse` envelope and token rules as the other write routes.
  Always-on MCP stdio smoke under `tests/integration/mcp_sdk/` connects with
  NDJSON framing (the TypeScript SDK's `StdioClientTransport` wire format) and
  asserts the tool list.
- **P3.G.C** — `integrations/opencode-plugin/` TypeScript package
  (`@opencode-ai/plugin`) exposing `pf_run`, `pf_wait`, `pf_review`, `pf_merge`,
  `pf_decline` over a thin `PfClient` (host CLI JSON primary; MCP config stays
  for non-plugin hosts). `pf_merge` obtains user confirmation via
  `context.ask(...)` before `approve --apply` / `materialize` (fail-closed if
  `ask` is unavailable). Slash commands in
  [`examples/opencode/`](../examples/opencode/) remain optional / legacy.
- **P3.G.D** — gated OpenCode CLI smoke
  [`scripts/opencode_plugin_smoke.sh`](../scripts/opencode_plugin_smoke.sh)
  (temp git project, isolated `HOME`/`XDG_*`, `.opencode/plugins` + `file:`
  dependency, mock PF env). Asserts `opencode --version`, plugin tools on
  `GET /experimental/tool/ids` via `opencode serve`, and mock
  `technical_plan` → materialized `docs/ARCHITECTURE.md`. Wired into
  [`scripts/verify.sh`](../scripts/verify.sh) as an optional trailing step
  (skip when no binary; `OPENCODE_INTEGRATION=1` fails if missing). Host CLI
  now honors `PRODUCT_FACTORY_ROOT` / `PRODUCT_FACTORY_DATA_DIR` the same way
  MCP does, so the plugin can run with a non-PF project cwd.
- **P3.G.E** — this tracker; `materialize` documented in
  [`host-integration.md`](host-integration.md) (CLI, HTTP, MCP surfaces);
  Phase 3 locked default amended to allow the optional plugin; Phase 3 status
  pointer in [`handover_post_mvp.md`](handover_post_mvp.md). Plugin README
  documents install, UAT, smoke command, and Phase 4 extension points.

## Exit criteria

- [x] `materialize` lands a mock `technical_plan` artifact under a fixture repo
      via CLI and MCP —
      `tests/contract/test_host_protocol.py::test_host_materialize_happy_path`,
      `::test_host_cli_materialize`,
      `tests/unit/test_host_mcp.py::test_pf_materialize_dispatches_to_host_service`,
      `tests/contract/test_host_control_api.py::test_control_materialize_happy_path_and_path_escape`.
- [x] Path escape and pre-approval misuse rejected with typed `HostResponse`
      errors — `tests/contract/test_host_protocol.py::test_host_materialize_rejects_path_escape`,
      `::test_host_materialize_rejects_pre_approval_status`,
      `tests/unit/test_host_mcp.py::test_pf_materialize_requires_args`.
- [x] Plugin `pf_merge` requires user confirmation; on confirm the file appears
      in the workspace — plugin unit tests
      (`integrations/opencode-plugin` vitest: decline / no-ask refuse; allow →
      approve+materialize) + gated OpenCode smoke (tools visible + mock
      materialize of `docs/ARCHITECTURE.md`).
- [x] Slash commands no longer required for the happy path (plugin tools +
      injected agent guidance suffice) — see plugin `config` hook /
      `agent-guidance.ts` and README.
- [x] Always-on CI green; OpenCode smoke either skipped (no binary) or passed
      when gated — `scripts/opencode_plugin_smoke.sh` + `verify.sh` hook;
      real pass on OpenCode **1.18.4**.
- [x] Protocol docs describe `materialize`; plugin README documents install and
      the Phase 4 extension points (`PfClient`, new MCP tools).

## Evidence

| Gate | ID / note |
| --- | --- |
| P3.G.A host contract | `uv run python -m pytest tests/contract/test_host_protocol.py -q` (materialize happy path, path escape, pre-approval reject, CLI) |
| P3.G.B MCP + control API | `uv run python -m pytest tests/unit/test_host_mcp.py tests/contract/test_host_control_api.py tests/integration/mcp_sdk -q` |
| P3.G.A+B combined run | `uv run python -m pytest tests/unit/test_host_mcp.py tests/contract/test_host_protocol.py tests/contract/test_host_control_api.py tests/integration/mcp_sdk -q` → **33 passed** (1 Starlette/`httpx` deprecation warning) |
| P3.G.C plugin unit | `cd integrations/opencode-plugin && npm test && npm run check` — vitest green + `tsc --noEmit` |
| P3.G.D OpenCode smoke | `OPENCODE_INTEGRATION=1 bash scripts/opencode_plugin_smoke.sh` → **PASS** on `opencode 1.18.4`; tools `pf_run`/`pf_wait`/`pf_review`/`pf_merge`/`pf_decline` visible; mock `technical_plan` materialized `docs/ARCHITECTURE.md` (1275 bytes). Skip path: no binary → exit 0 with reason. |
| P3.G.D verify hook | `scripts/verify.sh` calls the smoke as an optional trailing step |
| Live model smoke | _not planned_ — mock host loop + contract coverage are the gate (same rationale as P3.F) |

## Phase 4 readiness

Both new surfaces are deliberate extension points, not one-off OpenCode glue:

- **`materialize`** takes an artifact selector (logical name / sha256), so
  Phase 4 connector- or `quality_gate`-produced artifacts land through the same
  audited, path-checked host action with no new client work.
- **MCP tool registry** stays a name→handler map, so Phase 4 can add tools
  without touching the stdio server or framing.
- **`PfClient`** in the plugin abstracts the transport (`submit`/`status`/
  `tail`/`inspect`/`approve`/`materialize`), so switching from host CLI JSON to
  the HTTP control API — or reusing the client from another host — does not
  require rewriting the model-facing tools.
- `HostResponse.data` stays open for unknown fields; clients must ignore
  extras, so new payload fields are additive.
