# Product Factory + OpenCode

Two ways to drive Product Factory from OpenCode:

1. **Plugin (primary/recommended)** —
   [`@product-factory/opencode-plugin`](../../integrations/opencode-plugin/)
   adds first-class tools (`pf_run`, `pf_wait`, `pf_review`, `pf_merge`,
   `pf_decline`) and injects agent guidance, so **no slash commands are
   required**. It talks to Product Factory via the host CLI JSON protocol
   (`product-factory.host/v1`) and gates every merge behind an operator
   confirmation.
2. **MCP + slash commands (this directory, for non-plugin hosts)** — a local MCP
   server wrapping the same `HostService`, plus slash-command templates. The
   same MCP binary works in Cursor / Claude Code; only the slash commands are
   OpenCode-specific. Use this when you can't or don't want to load a plugin.

## Recommended: the OpenCode plugin

```json title="opencode.json"
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["file:../integrations/opencode-plugin"]
}
```

See [`integrations/opencode-plugin/README.md`](../../integrations/opencode-plugin/README.md)
for install options (`file:` vs `.opencode/plugins/`), the tool → CLI mapping,
the `pf_merge` confirmation invariant, and Phase 4 extension notes.

The sections below document the **MCP + slash-command** path.

## Install (MCP path)

1. From the Product Factory repo (or any project with `uv` + this package editable):

   ```bash
   uv sync
   uv run product-factory doctor
   ```

2. Copy or merge [`opencode.json`](./opencode.json) into your project’s
   `opencode.json` (or `~/.config/opencode/opencode.json`).

   When OpenCode’s cwd is **not** this repo, point `uv` at the Product Factory
   checkout with `--directory` (not `-C` — that flag is invalid on current `uv`
   and the MCP child exits immediately, which makes OpenCode look stuck):

   ```json
   "command": [
     "uv",
     "--directory",
     "/absolute/path/to/orchestration",
     "run",
     "product-factory",
     "mcp",
     "--mock"
   ]
   ```

   Faster / more reliable once the venv exists:

   ```json
   "command": [
     "/absolute/path/to/orchestration/.venv/bin/product-factory",
     "mcp",
     "--mock"
   ]
   ```

   Or, if `product-factory` is already on `PATH`:

   ```json
   "command": ["product-factory", "mcp", "--mock"]
   ```

   For live models, drop `--mock` / `PRODUCT_FACTORY_FORCE_MOCK` and set
   `OPENROUTER_API_KEY` in the environment OpenCode inherits.

3. Restart OpenCode (or reload MCP). Confirm tools `pf_submit`, `pf_status`,
   `pf_tail`, `pf_inspect`, `pf_approve`, `pf_reject`, `pf_cancel`, `pf_export`,
   `pf_materialize` are available. If OpenCode hangs on startup, run the same
   `command` in a terminal — a bad `uv` flag or missing binary fails instantly
   with an error OpenCode may not surface.

   Optional env (useful when OpenCode cwd is not this repo):

   - `PRODUCT_FACTORY_ROOT` — config checkout (defaults to this package tree)
   - `PRODUCT_FACTORY_DATA_DIR` — run/DB root (defaults to cwd `.product-factory`
     when present, else `<config-root>/.product-factory`)
   - `PRODUCT_FACTORY_FORCE_MOCK=1` — same as `--mock`

4. Optional: append [`AGENTS.md`](./AGENTS.md) guidance to your project agent
   instructions.

## Troubleshooting (OpenCode blank / stuck)

1. **Validate config JSON** (trailing commas break strict parsers):

   ```bash
   python3 -c 'import json; json.load(open("/Users/'"$USER"'/.config/opencode/opencode.json")); print("OK")'
   ```

2. **Prove the MCP binary outside OpenCode** (should print a JSON-RPC
   `initialize` result, then exit). Current PF replies in NDJSON by default;
   it mirrors `Content-Length` when the client sends that framing:

   ```bash
   # NDJSON (what OpenCode's MCP client uses):
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
     | /absolute/path/to/orchestration/.venv/bin/product-factory mcp --mock

   # Or Content-Length (LSP-style) — PF mirrors the framing:
   INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
   LEN=$(printf '%s' "$INIT" | wc -c | tr -d ' ')
   printf 'Content-Length: %s\r\n\r\n%s' "$LEN" "$INIT" \
     | /absolute/path/to/orchestration/.venv/bin/product-factory mcp --mock
   ```

3. **Isolate via A/B** in `~/.config/opencode/opencode.json`:
   - Set `"enabled": false` on `mcp.product-factory` and restart — if the UI recovers, the hang is MCP-related.
   - Temporarily set `"plugin": []` — plugins are a common blank-screen cause.
   - If tools show **Connection closed** (`-32000`), the MCP child exited before
     handshake. Usually missing config for the process cwd — upgrade PF (cwd-
     independent MCP config fallback) or set `PRODUCT_FACTORY_ROOT` in the MCP
     `environment` block. Re-run the binary handshake from step 2 from `/tmp`.
   - If tools show **Operation timed out after 30000ms**, OpenCode’s MCP client
     never parsed the initialize reply (older PF spoke LSP `Content-Length`
     framing; current PF replies in NDJSON like the TypeScript MCP SDK). Update
     to the latest `product-factory` and reload MCP — no timeout bump needed.

4. **Read OpenCode logs** while reproducing:

   ```bash
   opencode --print-logs --log-level DEBUG
   # or: ls -lt ~/.local/share/opencode/log | head
   ```

   Desktop: **OpenCode → Reload Webview**; if still blank, quit and clear `~/.cache/opencode` per [OpenCode troubleshooting](https://opencode.ai/docs/troubleshooting/).

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
| `pf_materialize` | Copy a run artifact into the target repo (`artifact`, `dest_path`) |

Protocol docs: [`docs/host-integration.md`](../../docs/host-integration.md).
