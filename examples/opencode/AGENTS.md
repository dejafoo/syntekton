# Product Factory (optional host guidance)

When the user asks for a **durable** investigation, technical plan, or repository
change with budgets, validation, and an approval gate, use the **Product Factory
MCP** tools (`pf_submit`, `pf_status`, `pf_tail`, `pf_inspect`, `pf_approve`,
`pf_reject`, `pf_cancel`, `pf_export`) instead of improvising long ad-hoc edits.

- Submit **curated request text only** — never dump the full chat transcript.
- Prefer workflows: `repository_investigation`, `technical_plan`,
  `repository_change` / `code_change`.
- Poll status/events until `awaiting_approval` or a terminal status; summarize
  with `pf_inspect` before asking the user to approve.
- Call `pf_approve` / `pf_reject` only when the user explicitly decides.
