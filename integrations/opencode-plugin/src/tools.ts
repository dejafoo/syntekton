/**
 * Product Factory plugin tools (model-facing).
 *
 * The tool *logic* lives in plain, dependency-free handlers so it can be unit
 * tested with a mock {@link PfClient} and a mock `ask`. `createPfTools` wraps
 * those handlers with OpenCode's `tool()` helper (injected by `index.ts`), so
 * this module never has to import `@opencode-ai/plugin` at load time.
 */

import type { MaterializeInput, PfClient, PfResponse } from "./pf-client.js";

/** Workflows that end in a proposed patch → land via `approve(apply=true)`. */
export const PATCH_WORKFLOWS = new Set(["code_change", "repository_change"]);

/** Default artifact + destination for doc/report style workflows. */
export const MATERIALIZE_DEFAULTS: Record<string, { artifact: string; dest: string }> = {
  technical_plan: { artifact: "ARCHITECTURE.md", dest: "docs/ARCHITECTURE.md" },
  architecture: { artifact: "ARCHITECTURE.md", dest: "docs/ARCHITECTURE.md" },
  repository_investigation: { artifact: "EVIDENCE_REPORT.md", dest: "docs/EVIDENCE_REPORT.md" },
};

const AWAITING = "awaiting_approval";
const TERMINAL = new Set([
  "completed",
  "failed",
  "blocked",
  "budget_exhausted",
  "plan_rejected",
  "cancelled",
]);

/**
 * Minimal subset of OpenCode's `ToolContext` the handlers rely on. `ask`
 * requests operator permission and rejects when the user declines.
 */
export interface PfToolContext {
  directory?: string;
  worktree?: string;
  ask?: (input: {
    permission: string;
    patterns?: string[];
    always?: string[];
    metadata?: Record<string, unknown>;
  }) => Promise<void>;
}

export interface PfToolDeps {
  client: PfClient;
  /** Default budget for `pf_run` when the caller omits one. */
  defaultBudgetUsd?: number;
  /** Bounds for `pf_wait` polling. */
  wait?: { maxPolls?: number; intervalMs?: number; sleep?: (ms: number) => Promise<void> };
}

// ---------------------------------------------------------------------------
// Arg shapes (mirrors of the Zod schemas built in createPfTools)
// ---------------------------------------------------------------------------

export interface PfRunArgs {
  request: string;
  workflow?: string;
  repository_path?: string;
  budget_usd?: number;
  validation_commands?: string[];
}

export interface PfWaitArgs {
  run_id: string;
  max_polls?: number;
}

export interface PfReviewArgs {
  run_id: string;
}

export interface PfMergeArgs {
  run_id: string;
  /** Override the detected workflow (patch vs doc/report). */
  workflow?: string;
  /** Artifact logical name for doc/report materialize (defaults per workflow). */
  artifact?: string;
  /** Destination path under the run repository (defaults per workflow). */
  dest_path?: string;
  overwrite?: boolean;
}

export interface PfDeclineArgs {
  run_id: string;
  /** `reject` (default for awaiting_approval) or `cancel` (in-flight run). */
  action?: "reject" | "cancel";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function summarize(res: PfResponse): string {
  return JSON.stringify(
    {
      ok: res.ok,
      run_id: res.run_id,
      status: res.status,
      error: res.error ?? undefined,
      plan_summary: res.plan_summary ?? undefined,
      artifacts: res.artifacts?.map((a) => a.logical_name).filter(Boolean),
      data: res.data ?? undefined,
    },
    null,
    2,
  );
}

function workflowOf(res: PfResponse, override?: string): string | undefined {
  if (override) return override;
  const fromData = res.data?.["workflow_type"];
  return typeof fromData === "string" ? fromData : undefined;
}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Handlers (pure — unit tested directly)
// ---------------------------------------------------------------------------

export async function pfRun(deps: PfToolDeps, ctx: PfToolContext, args: PfRunArgs): Promise<string> {
  const repositoryPath = args.repository_path ?? ctx.worktree ?? ctx.directory;
  const res = await deps.client.submit({
    requestText: args.request,
    workflow: args.workflow ?? "code_change",
    repositoryPath,
    budgetUsd: args.budget_usd ?? deps.defaultBudgetUsd,
    validationCommands: args.validation_commands,
  });
  return summarize(res);
}

export async function pfWait(deps: PfToolDeps, _ctx: PfToolContext, args: PfWaitArgs): Promise<string> {
  const maxPolls = args.max_polls ?? deps.wait?.maxPolls ?? 60;
  const intervalMs = deps.wait?.intervalMs ?? 2_000;
  const sleep = deps.wait?.sleep ?? defaultSleep;
  let last: PfResponse | undefined;
  for (let poll = 0; poll < maxPolls; poll += 1) {
    last = await deps.client.status(args.run_id);
    const status = last.status ?? undefined;
    if (!last.ok) return summarize(last);
    if (status === AWAITING || (status && TERMINAL.has(status))) {
      return summarize(last);
    }
    if (poll < maxPolls - 1) await sleep(intervalMs);
  }
  return summarize({
    ...(last ?? { protocol: "product-factory.host/v1", ok: true, artifacts: [], events: [] }),
    data: { ...(last?.data ?? {}), timed_out: true, polls: maxPolls },
  } as PfResponse);
}

export async function pfReview(deps: PfToolDeps, _ctx: PfToolContext, args: PfReviewArgs): Promise<string> {
  const res = await deps.client.inspect(args.run_id);
  return summarize(res);
}

/**
 * Land a run's results into the workspace.
 *
 * INVARIANT: no `approve`/`materialize` call happens without an explicit
 * operator confirmation via `context.ask`. If `ask` is unavailable the merge is
 * refused (fail-closed) rather than silently proceeding.
 */
export async function pfMerge(deps: PfToolDeps, ctx: PfToolContext, args: PfMergeArgs): Promise<string> {
  const status = await deps.client.status(args.run_id);
  if (!status.ok) return summarize(status);

  const workflow = workflowOf(status, args.workflow);
  const isPatch = workflow ? PATCH_WORKFLOWS.has(workflow) : false;

  const confirmed = await confirmMerge(ctx, {
    runId: args.run_id,
    workflow,
    action: isPatch ? "approve+apply patch" : "approve + materialize document",
  });
  if (!confirmed) {
    return summarize({
      protocol: status.protocol,
      ok: false,
      run_id: args.run_id,
      status: status.status,
      artifacts: [],
      events: [],
      error: {
        code: "merge_declined",
        message: "Operator declined the merge; no approve/materialize was performed.",
      },
    });
  }

  if (isPatch) {
    const approved = await deps.client.approve(args.run_id, { apply: true });
    return summarize(approved);
  }

  // Doc/report workflow: approve if still awaiting, then materialize.
  if (status.status === AWAITING) {
    const approved = await deps.client.approve(args.run_id, { apply: false });
    if (!approved.ok) return summarize(approved);
  }

  const preset = workflow ? MATERIALIZE_DEFAULTS[workflow] : undefined;
  const artifact = args.artifact ?? preset?.artifact ?? "ARCHITECTURE.md";
  const destPath = args.dest_path ?? preset?.dest ?? "docs/ARCHITECTURE.md";
  const input: MaterializeInput = { artifact, destPath, overwrite: args.overwrite ?? false };
  const materialized = await deps.client.materialize(args.run_id, input);
  return summarize(materialized);
}

export async function pfDecline(deps: PfToolDeps, _ctx: PfToolContext, args: PfDeclineArgs): Promise<string> {
  let action = args.action;
  if (!action) {
    const status = await deps.client.status(args.run_id);
    action = status.status === AWAITING ? "reject" : "cancel";
  }
  const res = action === "reject" ? await deps.client.reject(args.run_id) : await deps.client.cancel(args.run_id);
  return summarize(res);
}

async function confirmMerge(
  ctx: PfToolContext,
  info: { runId: string; workflow?: string; action: string },
): Promise<boolean> {
  if (typeof ctx.ask !== "function") return false;
  try {
    await ctx.ask({
      permission: "pf_merge",
      patterns: [info.runId],
      always: [],
      metadata: { run_id: info.runId, workflow: info.workflow, action: info.action },
    });
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Plugin tool factory (wraps handlers with the injected `tool` helper)
// ---------------------------------------------------------------------------

/** The `tool` helper exported by `@opencode-ai/plugin` (injected to keep this module dependency-free). */
export type ToolHelper = {
  (input: {
    description: string;
    args: Record<string, unknown>;
    execute: (args: any, context: PfToolContext) => Promise<string>;
  }): unknown;
  schema: {
    string: () => any;
    number: () => any;
    boolean: () => any;
    array: (inner: unknown) => any;
    enum: (values: string[]) => any;
  };
};

export function createPfTools(tool: ToolHelper, deps: PfToolDeps): Record<string, unknown> {
  const s = tool.schema;
  return {
    pf_run: tool({
      description:
        "Submit a curated Product Factory run (workflow pack + request text). " +
        "Defaults repository_path to the current OpenCode workspace. Returns a run_id; " +
        "does NOT approve or apply anything. Send curated request text only, never the full chat.",
      args: {
        request: s.string().describe("Curated request text for the run (not the full transcript)."),
        workflow: s
          .string()
          .optional()
          .describe("Workflow pack id (code_change, repository_change, technical_plan, repository_investigation)."),
        repository_path: s.string().optional().describe("Target repo; defaults to the OpenCode workspace root."),
        budget_usd: s.number().optional().describe("Cost ceiling in USD."),
        validation_commands: s.array(s.string()).optional().describe("Validation command ids to run."),
      },
      execute: (args: PfRunArgs, context: PfToolContext) => pfRun(deps, context, args),
    }),
    pf_wait: tool({
      description:
        "Poll a run until it reaches awaiting_approval or a terminal status (bounded polling). Returns the latest status envelope.",
      args: {
        run_id: s.string().describe("Run id returned by pf_run."),
        max_polls: s.number().optional().describe("Maximum status polls before returning."),
      },
      execute: (args: PfWaitArgs, context: PfToolContext) => pfWait(deps, context, args),
    }),
    pf_review: tool({
      description: "Inspect a run and summarize its plan, validations, and artifacts for the user before any merge.",
      args: { run_id: s.string().describe("Run id to inspect.") },
      execute: (args: PfReviewArgs, context: PfToolContext) => pfReview(deps, context, args),
    }),
    pf_merge: tool({
      description:
        "Land a run's results into the workspace. ALWAYS asks the operator for confirmation first. " +
        "Patch workflows are approved+applied; doc/report workflows are approved (if needed) and materialized " +
        "to a repo path (defaults docs/ARCHITECTURE.md or docs/EVIDENCE_REPORT.md).",
      args: {
        run_id: s.string().describe("Run id to merge."),
        workflow: s.string().optional().describe("Override detected workflow (patch vs doc/report)."),
        artifact: s.string().optional().describe("Artifact logical name for doc/report materialize."),
        dest_path: s.string().optional().describe("Destination path under the run repository."),
        overwrite: s.boolean().optional().describe("Overwrite an existing destination file."),
      },
      execute: (args: PfMergeArgs, context: PfToolContext) => pfMerge(deps, context, args),
    }),
    pf_decline: tool({
      description:
        "Decline a run: reject (when awaiting_approval) or cancel (when still in flight). No workspace changes are made.",
      args: {
        run_id: s.string().describe("Run id to decline."),
        action: s.enum(["reject", "cancel"]).optional().describe("Force reject or cancel; auto-detected otherwise."),
      },
      execute: (args: PfDeclineArgs, context: PfToolContext) => pfDecline(deps, context, args),
    }),
  };
}
