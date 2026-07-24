# Product Factory + OpenCode

Reference packaging for Phase 3 host integration: a local MCP server wrapping
the same `HostService` as `product-factory host …`, plus slash-command templates.

Same MCP binary works in Cursor / Claude Code; only the slash commands are
OpenCode-specific.

## Install

1. From the Product Factory repo (or any project with `uv` + this package editable):

   ```bash
   uv sync
   uv run product-factory doctor
   ```

2. Copy or merge [`opencode.json`](./opencode.json) into your project’s
   `opencode.json` (or `~/.config/opencode/opencode.json`).

   Adjust the MCP command if Product Factory is not launched via `uv run` from
   this repo — for example:

   ```json
   "command": ["product-factory", "mcp"]
   ```

   For live models, drop `--mock` / `PRODUCT_FACTORY_FORCE_MOCK` and set
   `OPENROUTER_API_KEY` in the environment OpenCode inherits.

3. Restart OpenCode (or reload MCP). Confirm tools `pf_submit`, `pf_status`,
   `pf_tail`, `pf_inspect`, `pf_approve`, `pf_reject`, `pf_cancel`, `pf_export`
   are available.

4. Optional: append [`AGENTS.md`](./AGENTS.md) guidance to your project agent
   instructions.

## Try it

```text
/pf-investigate Summarize how validation commands are selected for repository_change
```

Then `/pf-status run-…` and, when ready, `/pf-approve run-…`.

Manual smoke checklist:

1. Enable MCP → tools listed
2. `/pf-investigate …` → `pf_submit` returns `run_id`
3. Poll until `awaiting_approval` or terminal
4. `/pf-approve <run_id>` when you intend to approve

## Tools (HostResponse JSON)

| Tool | Role |
| --- | --- |
| `pf_submit` | Async submit (workflow, request text, repo, budget, validations) |
| `pf_status` | Status + plan summary |
| `pf_tail` | One event batch (`after_seq`) |
| `pf_inspect` | Plan, validations, artifacts |
| `pf_approve` / `pf_reject` / `pf_cancel` | Control |
| `pf_export` | Evidence bundle path |

Protocol docs: [`docs/host-integration.md`](../../docs/host-integration.md).
