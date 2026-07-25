/**
 * Product Factory plugin tools (model-facing).
 *
 * The tool *logic* lives in plain, dependency-free handlers so it can be unit
 * tested with a mock {@link PfClient} and a mock `ask`. `createPfTools` wraps
 * those handlers with OpenCode's `tool()` helper (injected by `index.ts`), so
 * this module never has to import `@opencode-ai/plugin` at load time.
 */

import type {
  ArtifactOverride,
  LandMapEntry,
  MaterializeInput,
  PfClient,
  PfResponse,
} from "./pf-client.js";
import { landMapFrom } from "./pf-client.js";

/** Workflows that end in a proposed patch → land via `approve(apply=true)`. */
export const PATCH_WORKFLOWS = new Set(["code_change", "repository_change"]);

/**
 * Last-resort artifact + destination per workflow.
 *
 * Only used when a run's `artifact_land_map` is unavailable (e.g. a host CLI
 * older than P4.A). The land map from `pf_inspect` is always preferred, so a run
 * that asked for `integration_testing_architecture.md` lands under that name.
 */
export const MATERIALIZE_DEFAULTS: Record<string, { artifact: string; dest: string }> = {
  technical_plan: { artifact: "ARCHITECTURE.md", dest: "docs/ARCHITECTURE.md" },
  architecture: { artifact: "ARCHITECTURE.md", dest: "docs/ARCHITECTURE.md" },
  repository_investigation: { artifact: "EVIDENCE_REPORT.md", dest: "docs/EVIDENCE_REPORT.md" },
  // A quality run has three deliverables; without a land map only the primary
  // report can be placed, so `pf_merge` lands that and reports what it skipped.
  quality_gate: { artifact: "QUALITY_FINDINGS.md", dest: "docs/QUALITY_FINDINGS.md" },
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
  /**
   * Deliverable naming per pack role, e.g.
   * `{"architecture_document": "docs/integration_testing_architecture.md"}`.
   * A bare string is treated as the destination path.
   */
  artifact_overrides?: Record<string, string | ArtifactOverride>;
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
  /** Artifact logical name; overrides the land map for a single-file merge. */
  artifact?: string;
  /** Destination path under the run repository; overrides the land map. */
  dest_path?: string;
  /** Limit a multi-deliverable merge to these land-map roles. */
  roles?: string[];
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

/** Accept `{role: "docs/x.md"}` as well as `{role: {destPath: "docs/x.md"}}`. */
export function normalizeArtifactOverrides(
  raw: Record<string, string | ArtifactOverride> | undefined,
): Record<string, ArtifactOverride> | undefined {
  if (!raw) return undefined;
  const out: Record<string, ArtifactOverride> = {};
  for (const [role, value] of Object.entries(raw)) {
    if (typeof value === "string") {
      if (value.trim()) out[role] = { destPath: value.trim() };
      continue;
    }
    const override: ArtifactOverride = {};
    if (value?.destPath?.trim()) override.destPath = value.destPath.trim();
    if (value?.logicalName?.trim()) override.logicalName = value.logicalName.trim();
    if (override.destPath || override.logicalName) out[role] = override;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

export async function pfRun(deps: PfToolDeps, ctx: PfToolContext, args: PfRunArgs): Promise<string> {
  const repositoryPath = args.repository_path ?? ctx.worktree ?? ctx.directory;
  const res = await deps.client.submit({
    requestText: args.request,
    workflow: args.workflow ?? "code_change",
    repositoryPath,
    budgetUsd: args.budget_usd ?? deps.defaultBudgetUsd,
    validationCommands: args.validation_commands,
    artifactOverrides: normalizeArtifactOverrides(args.artifact_overrides),
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

  // A single explicit artifact/dest bypasses the land map; otherwise the run's
  // own land map decides which files land where.
  const singleFile = Boolean(args.artifact || args.dest_path);
  let landMap: LandMapEntry[] = [];
  if (!isPatch && !singleFile) {
    const inspected = await deps.client.inspect(args.run_id);
    if (inspected.ok) {
      landMap = landMapFrom(inspected).filter(
        (entry) => entry.landable !== false && (!args.roles || args.roles.includes(entry.role)),
      );
    }
  }

  const confirmed = await confirmMerge(ctx, {
    runId: args.run_id,
    workflow,
    action: isPatch
      ? "approve+apply patch"
      : landMap.length > 0
        ? `approve + land ${landMap.map((e) => e.suggested_dest_path).join(", ")}`
        : "approve + materialize document",
    destinations: landMap.map((entry) => entry.suggested_dest_path),
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

  // Doc/report workflow: approve if still awaiting, then land.
  if (status.status === AWAITING) {
    const approved = await deps.client.approve(args.run_id, { apply: false });
    if (!approved.ok) return summarize(approved);
  }

  // One confirmation covers the whole land map; the host still audits per file.
  if (landMap.length > 0) {
    const landed = await deps.client.materializeAll(args.run_id, {
      roles: args.roles,
      overwrite: args.overwrite ?? false,
    });
    return summarize(landed);
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
  info: { runId: string; workflow?: string; action: string; destinations?: string[] },
): Promise<boolean> {
  if (typeof ctx.ask !== "function") return false;
  try {
    await ctx.ask({
      permission: "pf_merge",
      patterns: [info.runId, ...(info.destinations ?? [])],
      always: [],
      metadata: {
        run_id: info.runId,
        workflow: info.workflow,
        action: info.action,
        destinations: info.destinations,
      },
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
    record: (key: unknown, value: unknown) => any;
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
          .describe(
            "Workflow pack id (code_change, repository_change, technical_plan, " +
              "repository_investigation, quality_gate).",
          ),
        repository_path: s.string().optional().describe("Target repo; defaults to the OpenCode workspace root."),
        budget_usd: s.number().optional().describe("Cost ceiling in USD."),
        validation_commands: s.array(s.string()).optional().describe("Validation command ids to run."),
        artifact_overrides: s
          .record(s.string(), s.string())
          .optional()
          .describe(
            "Name the deliverables by pack role, e.g. " +
              '{"architecture_document": "docs/integration_testing_architecture.md"}. ' +
              "Use this whenever the user asks for a scoped document instead of the generic default.",
          ),
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
        "Patch workflows are approved+applied; doc/report workflows are approved (if needed) and every " +
        "deliverable in the run's artifact_land_map is landed at its suggested path (see pf_review). " +
        "Pass artifact/dest_path only to override the land map for a single file.",
      args: {
        run_id: s.string().describe("Run id to merge."),
        workflow: s.string().optional().describe("Override detected workflow (patch vs doc/report)."),
        artifact: s.string().optional().describe("Artifact logical name for a single-file merge."),
        dest_path: s.string().optional().describe("Destination path for a single-file merge."),
        roles: s
          .array(s.string())
          .optional()
          .describe("Limit the merge to these land-map roles (default: every landable deliverable)."),
        overwrite: s.boolean().optional().describe("Overwrite existing destination files."),
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
