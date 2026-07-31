# Product Factory (optional host guidance)

When the user asks for a **durable** investigation, technical plan, or repository
change with budgets, validation, and an approval gate, use the **Product Factory
plugin tools** (`pf_run`, `pf_wait`, `pf_review`, `pf_merge`, `pf_decline`) or the
MCP equivalents — not long ad-hoc edits.

- Submit **curated request text only** — never dump the full chat transcript.
- Prefer workflows: `change_intake`, `feasibility_discovery`,
  `repository_investigation`, `technical_plan`, `repository_change` / `code_change`.
- For `change_intake`, summarize the brief/clarification and **never** auto-start
  `repository_change`.
- Remote: set `PRODUCT_FACTORY_REMOTE_URL` (+ observe/host bearer token). Pass
  `repository_id` remotely; laptop `repository_path` is rejected. `pf_merge` is
  unsupported remotely until R3.
- Wait until `awaiting_approval` or a terminal status; summarize with `pf_review`
  before asking the user to merge/approve.
- Call `pf_merge` / approve only when the user explicitly decides.
