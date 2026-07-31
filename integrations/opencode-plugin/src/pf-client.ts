/**
 * PfClient — thin transport over the Product Factory host protocol.
 *
 * The default implementation shells out to `product-factory host …` and parses
 * the emitted `product-factory.host/v1` JSON envelope. Keeping this behind a
 * small interface means Phase 4 can swap in an HTTP (control API) transport
 * without touching the tool layer.
 */

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const HOST_PROTOCOL = "product-factory.host/v1";

/** Mirror of the Python `HostResponse` envelope. Unknown fields are preserved. */
export interface PfResponse {
  protocol: string;
  ok: boolean;
  run_id?: string | null;
  status?: string | null;
  plan_summary?: Record<string, unknown> | null;
  subscription?: { sse_url?: string | null; cli_tail: string } | null;
  artifacts: Array<Record<string, unknown>>;
  events: Array<Record<string, unknown>>;
  data?: Record<string, unknown> | null;
  error?: { code: string; message: string; details?: Record<string, unknown> } | null;
  [key: string]: unknown;
}

/** Host-chosen name and/or destination for one pack deliverable role. */
export interface ArtifactOverride {
  logicalName?: string;
  destPath?: string;
}

export interface SubmitInput {
  requestText: string;
  workflow?: string;
  repositoryPath?: string;
  profile?: string;
  budgetUsd?: number;
  validationCommands?: string[];
  /** Deliverable naming keyed by pack role, e.g. `architecture_document`. */
  artifactOverrides?: Record<string, ArtifactOverride>;
}

export interface MaterializeInput {
  artifact: string;
  destPath: string;
  overwrite?: boolean;
}

export interface MaterializeAllInput {
  roles?: string[];
  overwrite?: boolean;
}

/** One entry of a run's resolved `artifact_land_map` (from `pf_inspect`). */
export interface LandMapEntry {
  role: string;
  logical_name: string;
  suggested_dest_path: string;
  media_type?: string;
  landable?: boolean;
  renamable?: boolean;
  required?: boolean;
}

/**
 * Transport-neutral client the plugin tools depend on. Phase 4 connectors /
 * an HTTP transport only need to satisfy this shape.
 */
export interface PfClient {
  submit(input: SubmitInput): Promise<PfResponse>;
  status(runId: string): Promise<PfResponse>;
  inspect(runId: string): Promise<PfResponse>;
  tail(runId: string, opts?: { afterSeq?: number }): Promise<PfResponse>;
  approve(runId: string, opts?: { apply?: boolean }): Promise<PfResponse>;
  reject(runId: string): Promise<PfResponse>;
  cancel(runId: string): Promise<PfResponse>;
  materialize(runId: string, input: MaterializeInput): Promise<PfResponse>;
  materializeAll(runId: string, input?: MaterializeAllInput): Promise<PfResponse>;
}

/** Read a run's resolved deliverable land map out of an inspect envelope. */
export function landMapFrom(res: PfResponse): LandMapEntry[] {
  const raw = res.data?.["artifact_land_map"];
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (entry): entry is LandMapEntry =>
      typeof entry === "object" &&
      entry !== null &&
      typeof (entry as LandMapEntry).role === "string" &&
      typeof (entry as LandMapEntry).logical_name === "string" &&
      typeof (entry as LandMapEntry).suggested_dest_path === "string",
  );
}

export interface CliPfClientOptions {
  /** Executable to invoke. Defaults to `product-factory` (must be on PATH). */
  bin?: string;
  /** Leading args before the `host` subcommand (e.g. `["run", "product-factory"]` for `uv run`). */
  binArgs?: string[];
  /** Working directory for the child process. */
  cwd?: string;
  /** Extra environment for the child process (merged over process.env). */
  env?: NodeJS.ProcessEnv;
  /** Force `--mock` on submit (deterministic planner, no live models). */
  mock?: boolean;
  /** Per-command timeout in milliseconds. Defaults to 120s. */
  timeoutMs?: number;
  /** Injectable spawn (tests). Defaults to node:child_process.spawn. */
  spawnFn?: typeof spawn;
}

export class PfProtocolError extends Error {
  constructor(
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "PfProtocolError";
  }
}

/**
 * Parse the last complete `product-factory.host/v1` JSON object out of a
 * command's stdout. `host tail` may emit several batches; we keep the last.
 */
export function parseHostJson(stdout: string): PfResponse {
  const lines = stdout
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i]!;
    if (!line.startsWith("{")) continue;
    try {
      return JSON.parse(line) as PfResponse;
    } catch {
      // Not JSON (log noise) — keep scanning older lines.
    }
  }
  throw new PfProtocolError("No product-factory.host JSON found in CLI output", stdout);
}

/** Verify the envelope speaks the protocol version this plugin targets. */
export function assertProtocol(res: PfResponse): PfResponse {
  if (res.protocol !== HOST_PROTOCOL) {
    throw new PfProtocolError(
      `Unexpected host protocol ${String(res.protocol)}; expected ${HOST_PROTOCOL}. ` +
        "Upgrade the product-factory CLI or the plugin.",
      res.protocol,
    );
  }
  return res;
}

/** CLI JSON implementation of {@link PfClient}. */
export class CliPfClient implements PfClient {
  private readonly bin: string;
  private readonly binArgs: string[];
  private readonly cwd?: string;
  private readonly env: NodeJS.ProcessEnv;
  private readonly mock: boolean;
  private readonly timeoutMs: number;
  private readonly spawnFn: typeof spawn;
  private protocolChecked = false;

  constructor(options: CliPfClientOptions = {}) {
    this.bin = options.bin ?? "product-factory";
    this.binArgs = options.binArgs ?? [];
    this.cwd = options.cwd;
    this.env = { ...process.env, ...(options.env ?? {}) };
    this.mock = options.mock ?? false;
    this.timeoutMs = options.timeoutMs ?? 120_000;
    this.spawnFn = options.spawnFn ?? spawn;
  }

  async submit(input: SubmitInput): Promise<PfResponse> {
    const dir = mkdtempSync(join(tmpdir(), "pf-opencode-"));
    const requestFile = join(dir, "request.md");
    writeFileSync(requestFile, input.requestText, "utf-8");
    try {
      const args = ["submit", "--request", requestFile, "--workflow", input.workflow ?? "code_change"];
      if (input.repositoryPath) args.push("--repo", input.repositoryPath);
      if (input.profile) args.push("--profile", input.profile);
      if (typeof input.budgetUsd === "number") args.push("--budget-usd", String(input.budgetUsd));
      for (const cmd of input.validationCommands ?? []) args.push("--validation-command", cmd);
      for (const [role, override] of Object.entries(input.artifactOverrides ?? {})) {
        if (override.destPath) args.push("--artifact-override", `${role}=${override.destPath}`);
        if (override.logicalName) args.push("--artifact-name", `${role}=${override.logicalName}`);
      }
      if (this.mock) args.push("--mock");
      return await this.run(args);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }

  status(runId: string): Promise<PfResponse> {
    return this.run(["status", runId]);
  }

  inspect(runId: string): Promise<PfResponse> {
    return this.run(["inspect", runId]);
  }

  tail(runId: string, opts: { afterSeq?: number } = {}): Promise<PfResponse> {
    return this.run(["tail", runId, "--after-seq", String(opts.afterSeq ?? 0), "--once"]);
  }

  approve(runId: string, opts: { apply?: boolean } = {}): Promise<PfResponse> {
    const args = ["approve", runId];
    if (opts.apply) args.push("--apply");
    return this.run(args);
  }

  reject(runId: string): Promise<PfResponse> {
    return this.run(["reject", runId]);
  }

  cancel(runId: string): Promise<PfResponse> {
    return this.run(["cancel", runId]);
  }

  materialize(runId: string, input: MaterializeInput): Promise<PfResponse> {
    const args = ["materialize", runId, "--artifact", input.artifact, "--to", input.destPath];
    if (input.overwrite) args.push("--overwrite");
    return this.run(args);
  }

  materializeAll(runId: string, input: MaterializeAllInput = {}): Promise<PfResponse> {
    const args = ["materialize-all", runId];
    for (const role of input.roles ?? []) args.push("--role", role);
    if (input.overwrite) args.push("--overwrite");
    return this.run(args);
  }

  private async run(hostArgs: string[]): Promise<PfResponse> {
    const argv = [...this.binArgs, "host", ...hostArgs];
    const stdout = await this.exec(argv);
    const res = assertProtocol(parseHostJson(stdout));
    // Protocol negotiation happens on the very first successful call.
    this.protocolChecked = true;
    return res;
  }

  private exec(argv: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
      const child = this.spawnFn(this.bin, argv, {
        cwd: this.cwd,
        env: this.env,
      });
      let stdout = "";
      let stderr = "";
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        reject(new PfProtocolError(`product-factory host timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      child.stdout?.on("data", (chunk: Buffer) => {
        stdout += chunk.toString("utf-8");
      });
      child.stderr?.on("data", (chunk: Buffer) => {
        stderr += chunk.toString("utf-8");
      });
      child.on("error", (err: Error) => {
        clearTimeout(timer);
        reject(
          new PfProtocolError(
            `Failed to spawn ${this.bin}: ${err.message}. Is product-factory installed / on PATH?`,
            err,
          ),
        );
      });
      // The CLI exits non-zero on a failure envelope, but still prints JSON on
      // stdout — so we resolve when stdout carries a valid host envelope and
      // only reject when there is nothing parseable to work with.
      child.on("close", () => {
        clearTimeout(timer);
        if (stdout.trim().length > 0) {
          resolve(stdout);
          return;
        }
        reject(
          new PfProtocolError(
            `product-factory host produced no JSON output${stderr ? `: ${stderr.trim()}` : ""}`,
            stderr,
          ),
        );
      });
    });
  }

  /** True once a successful protocol-checked call has completed. */
  get isProtocolChecked(): boolean {
    return this.protocolChecked;
  }
}
