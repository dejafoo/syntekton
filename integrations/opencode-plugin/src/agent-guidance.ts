/**
 * Agent guidance injected via the OpenCode `config` hook.
 *
 * This replaces reliance on slash commands / AGENTS.md: the model learns how to
 * drive the Product Factory tools directly. Slash commands remain available as
 * optional / legacy thin wrappers.
 */

export const PF_AGENT_GUIDANCE = [
  "# Product Factory (host workflows)",
  "",
  "For durable investigations, technical plans, or repository changes with budgets,",
  "validation, and an approval gate, drive Product Factory via these tools instead of",
  "improvising long ad-hoc edits:",
  "",
  "- `pf_run` — submit a run. Send CURATED request text only (never the full chat).",
  "  Pick a workflow: `change_intake`, `feasibility_discovery`, `repository_investigation`,",
  "  `technical_plan`, `quality_gate`, `repository_change` / `code_change`.",
  "  Local/CLI: repository_path defaults to the workspace. Remote: pass `repository_id`",
  "  (server-registered); never send a laptop `repository_path`.",
  "  Pass `pack_input` / `handoff_refs` when needed.",
  "- `pf_wait` — wait until `awaiting_approval` or a terminal status (SSE preferred,",
  "  status poll fallback).",
  "- `pf_review` — inspect and summarize the plan / evidence / proposed patch for the user.",
  "  The response's `artifact_land_map` shows what will land and where.",
  "- `pf_merge` — land results INTO the workspace (local/CLI only). This ALWAYS asks the",
  "  operator to confirm first. Only call it when the user has explicitly decided to merge.",
  "  Unsupported in remote mode until delivery landing (R3) — use approve/reject/cancel only.",
  "- `pf_decline` — reject (awaiting approval) or cancel (in flight).",
  "",
  "## Transport / environment",
  "",
  "Tool results include a `transport` object (`mode`: `cli` | `remote`, plus `endpoint`",
  "when remote) so you know which Product Factory host you are talking to.",
  "",
  "- Default: local `product-factory host` CLI (`PRODUCT_FACTORY_BIN` optional).",
  "- Remote: set `PRODUCT_FACTORY_REMOTE_URL` to the private host base URL. Auth bearer from",
  "  `PRODUCT_FACTORY_OBSERVE_TOKEN` or `PRODUCT_FACTORY_HOST_TOKEN`. When the remote URL is",
  "  set, the plugin never falls back to the laptop CLI if the host is unreachable.",
  "",
  "## Framing with change_intake",
  "",
  "Use `change_intake` for vague or well-scoped change requests. Summarize the resulting",
  "`change_brief` or `clarification_request` for the user. NEVER auto-start",
  "`repository_change` from clarification questions or a fresh brief — wait for human",
  "answers / an explicit operator decision. When a brief is pinned, the usual next packs",
  "are `repository_investigation` or `technical_plan` (operator-initiated).",
  "",
  "## Naming the deliverable",
  "",
  "Workflow packs deliver named documents. When the user asks for something narrower",
  "than the generic default (e.g. an integration testing architecture rather than a",
  "whole-system one), pass `artifact_overrides` to `pf_run` so the deliverable is",
  "produced and landed under that name:",
  "",
  '  pf_run(request="…", workflow="technical_plan",',
  '         artifact_overrides={"architecture_document":',
  '                             "docs/integration_testing_architecture.md"})',
  "",
  "Roles: `architecture_document` (technical_plan), `evidence_report`",
  "(repository_investigation), `change_brief` / `clarification_request` (change_intake),",
  "and `test_plan` / `quality_findings` / `security_evidence` (quality_gate).",
  "`pf_merge` then lands every deliverable at the",
  "path in the run's land map, so you never copy files by hand.",
  "",
  "## Multi-document runs",
  "",
  "`quality_gate` produces several documents from one run. `pf_merge` lands them all",
  "after a single confirmation; pass `roles` to land only some of them. Override any",
  "subset of names — un-overridden roles keep their pack defaults.",
  "",
  "Never approve, apply, or materialize without an explicit user decision — `pf_merge`",
  "enforces this with a confirmation prompt.",
].join("\n");

/**
 * Shape of the object OpenCode passes to the `config` hook. Kept structural so
 * this module does not depend on `@opencode-ai/plugin` types.
 */
export interface MutableConfig {
  instructions?: string[];
  [key: string]: unknown;
}

/**
 * Append the Product Factory guidance to the OpenCode config's `instructions`
 * (idempotent — safe to run on every config load).
 */
export function applyAgentGuidance(config: MutableConfig): void {
  const marker = "# Product Factory (host workflows)";
  const existing = Array.isArray(config.instructions) ? config.instructions : [];
  if (existing.some((entry) => typeof entry === "string" && entry.includes(marker))) {
    return;
  }
  config.instructions = [...existing, PF_AGENT_GUIDANCE];
}
