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
  "  Pick a workflow: `repository_investigation`, `technical_plan`,",
  "  `repository_change` / `code_change`. repository_path defaults to the workspace.",
  "- `pf_wait` — poll until `awaiting_approval` or a terminal status.",
  "- `pf_review` — inspect and summarize the plan / evidence / proposed patch for the user.",
  "- `pf_merge` — land results INTO the workspace. This ALWAYS asks the operator to",
  "  confirm first. Only call it when the user has explicitly decided to merge.",
  "- `pf_decline` — reject (awaiting approval) or cancel (in flight).",
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
