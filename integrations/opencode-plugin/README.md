# @product-factory/opencode-plugin

An [OpenCode](https://opencode.ai) plugin that exposes Product Factory host
workflows as first-class agent tools — **no slash commands required**.

It is a **thin adapter** over the stable `product-factory.host/v1` protocol. All
planning, grants, budgets, validation, and approval stay in Product Factory
core. The plugin only:

1. submits curated requests,
2. polls / reviews runs, and
3. **after an explicit operator confirmation**, lands results into the workspace
   (patch apply or artifact materialize).

## Transport: host CLI JSON (preferred)

The plugin talks to Product Factory by shelling out to
`product-factory host … ` and parsing the emitted JSON envelope
(`product-factory.host/v1`). This avoids running a second MCP client inside
OpenCode. MCP remains available for hosts that prefer raw `mcp` config (see
[`examples/opencode/`](../../examples/opencode/)).

The protocol version is checked on the first call; a mismatch fails fast with a
clear "upgrade the CLI or the plugin" error.

## Tools

| Tool | Behavior | CLI it drives |
| --- | --- | --- |
| `pf_run` | Submit a curated request (workflow + text). Defaults `repository_path` to the OpenCode worktree/directory. Returns a `run_id`. Never approves/applies. | `product-factory host submit --request … --workflow … [--repo …] [--mock]` |
| `pf_wait` | Bounded polling until `awaiting_approval` or terminal status. | `product-factory host status <run_id>` |
| `pf_review` | Inspect + summarize plan / validations / artifacts. | `product-factory host inspect <run_id>` |
| `pf_merge` | **Asks the operator to confirm first.** Then: patch workflows → `approve --apply`; doc/report workflows → `approve` (if still awaiting) + `materialize`. | `host approve <run_id> [--apply]` and/or `host materialize <run_id> --artifact … --to …` |
| `pf_decline` | `reject` (when awaiting approval) or `cancel` (in flight). | `product-factory host reject|cancel <run_id>` |

### `pf_merge` safety invariant

`pf_merge` **never** calls `approve` or `materialize` without an explicit
operator confirmation via OpenCode's tool `context.ask(...)`. If no `ask`
capability is available, the merge is **refused** (fail-closed) rather than
proceeding silently.

Merge routing by workflow:

| Workflow | Action | Default destination |
| --- | --- | --- |
| `code_change`, `repository_change` | `approve(apply=true)` (patch) | — (patch applied in repo) |
| `technical_plan`, `architecture` | `approve` if needed + `materialize` | `docs/ARCHITECTURE.md` |
| `repository_investigation` | `approve` if needed + `materialize` | `docs/EVIDENCE_REPORT.md` |

`artifact` / `dest_path` / `overwrite` can be overridden per call.

## Install

Requires `product-factory` on `PATH` (or set `PRODUCT_FACTORY_BIN` to an
absolute path, e.g. `/abs/path/orchestration/.venv/bin/product-factory`).

### Option A — `plugin` config entry (`file:` install)

```json title="opencode.json"
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["file:../integrations/opencode-plugin"]
}
```

Adjust the relative path so it resolves from your project to this directory
(OpenCode installs `file:` plugins with Bun at startup).

### Option B — local plugin directory

Copy or symlink the package into your project's plugin directory:

```bash
mkdir -p .opencode/plugins
ln -s /abs/path/orchestration/integrations/opencode-plugin/src/index.ts \
  .opencode/plugins/product-factory.ts
```

OpenCode auto-loads TypeScript files from `.opencode/plugins/` (and
`~/.config/opencode/plugins/`).

### Environment

- `PRODUCT_FACTORY_BIN` — override the CLI executable (defaults to `product-factory` on PATH).
- `PRODUCT_FACTORY_FORCE_MOCK=1` — pass `--mock` on submit (deterministic planner, no live models).
- `PRODUCT_FACTORY_ROOT` — PF config checkout when the OpenCode project cwd is not the
  Product Factory tree (honored by both `product-factory host` and `product-factory mcp`).
- `PRODUCT_FACTORY_DATA_DIR` — run/DB root (pass-through to the CLI / MCP).

For live models, drop `PRODUCT_FACTORY_FORCE_MOCK` and set `OPENROUTER_API_KEY`
in the environment OpenCode inherits.

## Agent guidance

The plugin's `config` hook injects concise instructions telling the model how
and when to use the tools (submit curated text, poll, review, merge only on
explicit user intent). This replaces reliance on slash commands / `AGENTS.md`.
Slash commands in [`examples/opencode/`](../../examples/opencode/) remain
available as optional / legacy thin wrappers.

## Development

```bash
cd integrations/opencode-plugin
npm install
npm test          # vitest run — focused unit tests
npm run check     # tsc --noEmit
```

### Unit tests

`test/tools.test.ts` exercises the tool handlers with a **mock `PfClient`** and a
**mock `ask`**:

- `pf_merge` does **not** approve/materialize when the operator declines, or when
  no `ask` is available (fail-closed).
- after allow, doc/report workflows call `approve` + `materialize` with the right
  defaults; patch workflows call `approve(apply=true)` and never materialize.
- `pf_run` defaults `repository_path` to the worktree; `pf_wait` stops at
  `awaiting_approval`; `pf_decline` picks reject vs cancel by status.

`test/pf-client.test.ts` covers JSON envelope parsing, protocol-version checking,
and that a failure envelope on a non-zero CLI exit still resolves (errors are
data, not exceptions).

## Manual UAT checklist (Tier 3)

1. Enable the plugin → `pf_run/pf_wait/pf_review/pf_merge/pf_decline` visible.
2. `pf_run` (investigate / plan / change) → returns `run_id`.
3. `pf_wait` until `awaiting_approval`.
4. `pf_review` → plan / evidence / proposed patch summarized.
5. `pf_merge` → **confirmation prompt** → on allow, a file appears
   (`docs/ARCHITECTURE.md` / `docs/EVIDENCE_REPORT.md`) or the patch is applied.

## Automated OpenCode smoke (Tier 2, P3.G.D)

Gated script (no live model / OpenRouter spend). Skips cleanly when `opencode`
is absent unless `OPENCODE_INTEGRATION=1` is set:

```bash
# From the Product Factory repo root:
export PRODUCT_FACTORY_BIN="$PWD/.venv/bin/product-factory"
export PRODUCT_FACTORY_ROOT="$PWD"
export PRODUCT_FACTORY_FORCE_MOCK=1
bash scripts/opencode_plugin_smoke.sh
# Force fail-if-missing:
OPENCODE_INTEGRATION=1 bash scripts/opencode_plugin_smoke.sh
```

What it does:

1. Records `opencode --version` (pinned locally at **1.18.4** during P3.G.D).
2. Creates a temp git project with a minimal OpenCode config that loads this
   plugin via `.opencode/plugins/` + a `file:` dependency (the reliable 1.18.x
   path; a bare `plugin: ["file:…"]` entry alone did not register tools).
3. Isolates `HOME`/`XDG_*` so the developer's global OpenCode MCP/plugins do
   not leak into the smoke.
4. Starts `opencode serve` and asserts `pf_run` / `pf_wait` / `pf_review` /
   `pf_merge` / `pf_decline` appear on `GET /experimental/tool/ids`.
5. Drives a mock `technical_plan` through the same host CLI the plugin uses and
   asserts `docs/ARCHITECTURE.md` is materialized under the temp project.

`scripts/verify.sh` runs this as an optional trailing step (skip when no
`opencode` binary).

## Phase 4 extension points

- **`PfClient` interface** (`src/pf-client.ts`) — swap the CLI transport for an
  HTTP (control API) transport, or a connector-backed client, without touching
  the tool layer. Just implement `submit/status/inspect/tail/approve/reject/cancel/materialize`.
- **`createPfTools` + handlers** (`src/tools.ts`) — add new tools (e.g. a
  `pf_quality_gate` for Phase 4 quality artifacts) alongside the existing ones;
  `materialize` already accepts any artifact selector (logical name or sha256),
  so connector-produced artifacts land the same audited way.
- **Protocol version** — the plugin checks `product-factory.host/v1`; bump both
  sides together when the protocol evolves.
