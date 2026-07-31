/**
 * PfClient — thin transport over the Product Factory host protocol.
 *
 * Default: shells out to `product-factory host …` and parses the emitted
 * `product-factory.host/v1` JSON envelope. When `PRODUCT_FACTORY_REMOTE_URL`
 * is set, {@link createPfClient} selects {@link RemotePfClient} (HTTP) instead
 * and never falls back to the CLI.
 */

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const HOST_PROTOCOL = "product-factory.host/v1";

/** Clear failure until remote delivery landing (R3). */
export const REMOTE_MERGE_UNSUPPORTED_CODE = "remote_merge_unsupported";
export const REMOTE_MERGE_UNSUPPORTED_MESSAGE =
  "Remote pf_merge / materialize is not supported until delivery landing (R3). " +
  "Approve/reject/cancel remain available; land results on a machine with local host access.";

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
  /** Server-registered repository id (remote mode only; resolves on the host). */
  repositoryId?: string;
  profile?: string;
  budgetUsd?: number;
  validationCommands?: string[];
  /** Deliverable naming keyed by pack role, e.g. `architecture_document`. */
  artifactOverrides?: Record<string, ArtifactOverride>;
  /** Typed pack payload validated against the pack input_schema. */
  packInput?: Record<string, unknown>;
  /** Cross-run handoff pointers (schema_id, digest, producer_run_id, …). */
  handoffRefs?: Array<Record<string, unknown>>;
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

export type PfTransportMode = "cli" | "remote";

export interface PfTransportInfo {
  mode: PfTransportMode;
  /** Selected remote base URL (no trailing slash), when mode is remote. */
  endpoint?: string;
}

export interface PfWaitOptions {
  maxPolls?: number;
  intervalMs?: number;
  sleep?: (ms: number) => Promise<void>;
}

/**
 * Transport-neutral client the plugin tools depend on. HTTP (remote) and CLI
 * transports both satisfy this shape.
 */
export interface PfClient {
  readonly transport: PfTransportInfo;
  submit(input: SubmitInput): Promise<PfResponse>;
  status(runId: string): Promise<PfResponse>;
  inspect(runId: string): Promise<PfResponse>;
  tail(runId: string, opts?: { afterSeq?: number }): Promise<PfResponse>;
  approve(runId: string, opts?: { apply?: boolean }): Promise<PfResponse>;
  reject(runId: string): Promise<PfResponse>;
  cancel(runId: string): Promise<PfResponse>;
  materialize(runId: string, input: MaterializeInput): Promise<PfResponse>;
  materializeAll(runId: string, input?: MaterializeAllInput): Promise<PfResponse>;
  /**
   * Optional bounded wait. Remote clients prefer SSE from `subscription.sse_url`
   * with poll fallback; CLI leaves this unset so tools poll `status`.
   */
  wait?(runId: string, opts?: PfWaitOptions): Promise<PfResponse>;
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
  readonly transport: PfTransportInfo = { mode: "cli" };

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
      if (input.packInput && Object.keys(input.packInput).length > 0) {
        const packFile = join(dir, "pack-input.json");
        writeFileSync(packFile, JSON.stringify(input.packInput), "utf-8");
        args.push("--pack-input", `@${packFile}`);
      }
      if (input.handoffRefs && input.handoffRefs.length > 0) {
        const handoffFile = join(dir, "handoff-refs.json");
        writeFileSync(handoffFile, JSON.stringify(input.handoffRefs), "utf-8");
        args.push("--handoff-refs", `@${handoffFile}`);
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

// ---------------------------------------------------------------------------
// Remote / HTTP transport
// ---------------------------------------------------------------------------

const AWAITING_APPROVAL = "awaiting_approval";
const TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "blocked",
  "budget_exhausted",
  "plan_rejected",
  "cancelled",
]);

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export interface RemotePfClientOptions {
  /** Base URL of the private Product Factory host (no trailing slash required). */
  baseUrl: string;
  /** Bearer token (PRODUCT_FACTORY_OBSERVE_TOKEN / PRODUCT_FACTORY_HOST_TOKEN). */
  token?: string;
  /** Force mock planner on submit. */
  mock?: boolean;
  /** Per-request timeout in milliseconds. Defaults to 120s. */
  timeoutMs?: number;
  /** Injectable fetch (tests). Defaults to global fetch. */
  fetchFn?: typeof fetch;
}

function remoteUnsupported(runId?: string): PfResponse {
  return {
    protocol: HOST_PROTOCOL,
    ok: false,
    run_id: runId ?? null,
    artifacts: [],
    events: [],
    error: {
      code: REMOTE_MERGE_UNSUPPORTED_CODE,
      message: REMOTE_MERGE_UNSUPPORTED_MESSAGE,
    },
  };
}

function isTerminalOrAwaiting(status: string | null | undefined): boolean {
  return status === AWAITING_APPROVAL || (typeof status === "string" && TERMINAL_STATUSES.has(status));
}

/** Parse one SSE block (`id` / `event` / `data` lines). */
export function parseSseBlock(block: string): { id?: string; event?: string; data?: string } | null {
  const trimmed = block.trim();
  if (!trimmed || trimmed.startsWith(":")) return null;
  let id: string | undefined;
  let event: string | undefined;
  const dataLines: string[] = [];
  for (const line of trimmed.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0 && id === undefined && event === undefined) return null;
  return { id, event, data: dataLines.length > 0 ? dataLines.join("\n") : undefined };
}

/**
 * HTTP implementation of {@link PfClient} against `/api/v1/...` on a private host.
 * Fail-closed: network / protocol errors never spawn the local CLI.
 */
export class RemotePfClient implements PfClient {
  readonly transport: PfTransportInfo;

  private readonly baseUrl: string;
  private readonly token?: string;
  private readonly mock: boolean;
  private readonly timeoutMs: number;
  private readonly fetchFn: typeof fetch;
  private protocolChecked = false;

  constructor(options: RemotePfClientOptions) {
    const base = options.baseUrl.trim().replace(/\/+$/, "");
    if (!base) {
      throw new PfProtocolError("PRODUCT_FACTORY_REMOTE_URL is empty");
    }
    this.baseUrl = base;
    this.transport = { mode: "remote", endpoint: base };
    this.token = options.token?.trim() || undefined;
    this.mock = options.mock ?? false;
    this.timeoutMs = options.timeoutMs ?? 120_000;
    this.fetchFn = options.fetchFn ?? fetch;
  }

  async submit(input: SubmitInput): Promise<PfResponse> {
    if (input.repositoryPath) {
      throw new PfProtocolError(
        "repository_path is not supported in remote mode. " +
          "Pass repositoryId for a server-registered repository, or omit for no-repo workflows.",
        { repositoryPath: input.repositoryPath },
      );
    }
    const artifact_overrides: Record<string, { logical_name?: string; dest_path?: string }> = {};
    for (const [role, override] of Object.entries(input.artifactOverrides ?? {})) {
      const entry: { logical_name?: string; dest_path?: string } = {};
      if (override.logicalName) entry.logical_name = override.logicalName;
      if (override.destPath) entry.dest_path = override.destPath;
      if (entry.logical_name || entry.dest_path) artifact_overrides[role] = entry;
    }
    const body: Record<string, unknown> = {
      request_text: input.requestText,
      workflow_type: input.workflow ?? "code_change",
      model_profile_set: input.profile ?? "local-target",
      validation_commands: input.validationCommands ?? [],
      artifact_overrides,
      pack_input: input.packInput ?? {},
      handoff_refs: input.handoffRefs ?? [],
      budget_usd: input.budgetUsd ?? 3.0,
      mock: this.mock,
    };
    if (input.repositoryId) body.repository_id = input.repositoryId;
    return this.request("POST", "/api/v1/runs", { body });
  }

  status(runId: string): Promise<PfResponse> {
    return this.request("GET", `/api/v1/runs/${encodeURIComponent(runId)}/status`);
  }

  inspect(runId: string): Promise<PfResponse> {
    return this.request("GET", `/api/v1/runs/${encodeURIComponent(runId)}/inspect`);
  }

  tail(runId: string, opts: { afterSeq?: number } = {}): Promise<PfResponse> {
    // Host/v1 parity with Python RemotePfClient and `product-factory host tail`.
    // Raw observability remains at /events; SSE wait uses subscription.sse_url.
    const afterSeq = opts.afterSeq ?? 0;
    return this.request(
      "GET",
      `/api/v1/runs/${encodeURIComponent(runId)}/tail?after_seq=${encodeURIComponent(String(afterSeq))}`,
    );
  }

  approve(runId: string, opts: { apply?: boolean } = {}): Promise<PfResponse> {
    return this.request("POST", `/api/v1/runs/${encodeURIComponent(runId)}/approve`, {
      body: { apply: Boolean(opts.apply) },
    });
  }

  reject(runId: string): Promise<PfResponse> {
    return this.request("POST", `/api/v1/runs/${encodeURIComponent(runId)}/reject`);
  }

  cancel(runId: string): Promise<PfResponse> {
    return this.request("POST", `/api/v1/runs/${encodeURIComponent(runId)}/cancel`);
  }

  async materialize(runId: string, _input: MaterializeInput): Promise<PfResponse> {
    return remoteUnsupported(runId);
  }

  async materializeAll(runId: string, _input: MaterializeAllInput = {}): Promise<PfResponse> {
    return remoteUnsupported(runId);
  }

  /**
   * Prefer SSE from `subscription.sse_url` (status envelope), then poll status
   * when the stream is missing or disconnects.
   */
  async wait(runId: string, opts: PfWaitOptions = {}): Promise<PfResponse> {
    const maxPolls = opts.maxPolls ?? 60;
    const intervalMs = opts.intervalMs ?? 2_000;
    const sleep = opts.sleep ?? defaultSleep;

    let last = await this.status(runId);
    if (!last.ok || isTerminalOrAwaiting(last.status)) return last;

    const sseUrl = last.subscription?.sse_url;
    if (sseUrl) {
      try {
        const fromSse = await this.waitViaSse(runId, sseUrl, {
          maxPolls,
          intervalMs,
          sleep,
          initial: last,
        });
        if (fromSse) return fromSse;
      } catch {
        // Fall through to poll — never fall back to CLI.
      }
    }

    for (let poll = 0; poll < maxPolls; poll += 1) {
      last = await this.status(runId);
      if (!last.ok || isTerminalOrAwaiting(last.status)) return last;
      if (poll < maxPolls - 1) await sleep(intervalMs);
    }
    return {
      ...last,
      data: { ...(last.data ?? {}), timed_out: true, polls: maxPolls },
    };
  }

  get isProtocolChecked(): boolean {
    return this.protocolChecked;
  }

  private async waitViaSse(
    runId: string,
    sseUrl: string,
    opts: {
      maxPolls: number;
      intervalMs: number;
      sleep: (ms: number) => Promise<void>;
      initial: PfResponse;
    },
  ): Promise<PfResponse | null> {
    const absolute = sseUrl.startsWith("http")
      ? sseUrl
      : `${this.baseUrl}${sseUrl.startsWith("/") ? "" : "/"}${sseUrl}`;
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
      ...this.authHeaders(),
    };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await this.fetchFn(absolute, { method: "GET", headers, signal: controller.signal });
      if (!res.ok || !res.body) {
        return null;
      }
      const interesting = new Set([
        "run.status_changed",
        "run.finished",
        "run.failed",
        "approval.required",
        "approval.decided",
        "plan.rejected",
      ]);
      let last = opts.initial;
      let statusChecks = 0;
      for await (const block of this.readSseBlocks(res.body)) {
        const parsed = parseSseBlock(block);
        if (!parsed) continue;
        const eventType = parsed.event;
        let payloadStatus: string | undefined;
        if (parsed.data) {
          try {
            const data = JSON.parse(parsed.data) as Record<string, unknown>;
            const type = typeof data.type === "string" ? data.type : eventType;
            const payload = (data.payload ?? {}) as Record<string, unknown>;
            if (typeof payload.status === "string") payloadStatus = payload.status;
            else if (typeof data.status === "string") payloadStatus = data.status;
            if (type && !interesting.has(type) && type !== "heartbeat") {
              // Still allow payload-driven terminal detection below.
            } else if (type === "heartbeat") {
              continue;
            }
            if (type && interesting.has(type)) {
              statusChecks += 1;
              last = await this.status(runId);
              if (!last.ok || isTerminalOrAwaiting(last.status)) return last;
            }
          } catch {
            // Non-JSON data — ignore.
          }
        }
        if (payloadStatus && isTerminalOrAwaiting(payloadStatus)) {
          last = await this.status(runId);
          return last;
        }
        if (statusChecks >= opts.maxPolls) {
          return {
            ...last,
            data: { ...(last.data ?? {}), timed_out: true, polls: statusChecks },
          };
        }
      }
      // Stream ended without a terminal status — caller falls back to poll.
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  private async *readSseBlocks(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          if (block.trim()) yield block;
        }
      }
      if (buffer.trim()) yield buffer;
    } finally {
      reader.releaseLock();
    }
  }

  private authHeaders(): Record<string, string> {
    if (!this.token) return {};
    return { Authorization: `Bearer ${this.token}` };
  }

  private async request(
    method: string,
    path: string,
    opts: { body?: unknown } = {},
  ): Promise<PfResponse> {
    const raw = await this.requestRaw(method, path, opts);
    if (!raw || typeof raw !== "object") {
      throw new PfProtocolError(`Remote host returned non-object JSON for ${method} ${path}`, raw);
    }
    const res = assertProtocol(raw as PfResponse);
    this.protocolChecked = true;
    return res;
  }

  private async requestRaw(
    method: string,
    path: string,
    opts: { body?: unknown } = {},
  ): Promise<unknown> {
    const url = `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...this.authHeaders(),
    };
    const init: RequestInit = { method, headers };
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    init.signal = controller.signal;
    let res: Response;
    try {
      res = await this.fetchFn(url, init);
    } catch (err) {
      clearTimeout(timer);
      const message = err instanceof Error ? err.message : String(err);
      throw new PfProtocolError(
        `Remote Product Factory unreachable at ${this.baseUrl}: ${message}. ` +
          "PRODUCT_FACTORY_REMOTE_URL is set — refusing to fall back to local CLI.",
        err,
      );
    } finally {
      clearTimeout(timer);
    }
    const text = await res.text();
    let parsed: unknown;
    try {
      parsed = text.trim() ? JSON.parse(text) : null;
    } catch {
      throw new PfProtocolError(
        `Remote host returned non-JSON (${res.status}) for ${method} ${path}`,
        text,
      );
    }
    // Host envelopes may arrive with HTTP 4xx/5xx; prefer the envelope when present.
    if (
      parsed &&
      typeof parsed === "object" &&
      (parsed as PfResponse).protocol === HOST_PROTOCOL
    ) {
      return parsed;
    }
    if (!res.ok) {
      throw new PfProtocolError(
        `Remote host HTTP ${res.status} for ${method} ${path}`,
        parsed ?? text,
      );
    }
    return parsed;
  }
}

/** @deprecated Prefer {@link RemotePfClient}; kept as the plan's alternate name. */
export const HttpPfClient = RemotePfClient;

export interface CreatePfClientOptions {
  /** OpenCode worktree / directory (CLI cwd). */
  directory?: string;
  env?: NodeJS.ProcessEnv;
  /** Injectable fetch for remote mode (tests). */
  fetchFn?: typeof fetch;
  /** Injectable spawn for CLI mode (tests). */
  spawnFn?: typeof spawn;
}

/**
 * Select transport: `PRODUCT_FACTORY_REMOTE_URL` → {@link RemotePfClient};
 * otherwise {@link CliPfClient}. Never falls back to CLI when the remote URL
 * is set but unreachable.
 */
export function createPfClient(options: CreatePfClientOptions = {}): PfClient {
  const env = { ...process.env, ...(options.env ?? {}) };
  const remoteUrl = (env.PRODUCT_FACTORY_REMOTE_URL ?? "").trim();
  if (remoteUrl) {
    const token =
      (env.PRODUCT_FACTORY_OBSERVE_TOKEN ?? "").trim() ||
      (env.PRODUCT_FACTORY_HOST_TOKEN ?? "").trim() ||
      undefined;
    return new RemotePfClient({
      baseUrl: remoteUrl,
      token,
      mock: truthyEnv(env.PRODUCT_FACTORY_FORCE_MOCK),
      fetchFn: options.fetchFn,
    });
  }
  return new CliPfClient({
    bin: env.PRODUCT_FACTORY_BIN || "product-factory",
    cwd: options.directory,
    mock: truthyEnv(env.PRODUCT_FACTORY_FORCE_MOCK),
    env,
    spawnFn: options.spawnFn,
  });
}

function truthyEnv(value: string | undefined): boolean {
  return value === "1" || value === "true" || value === "yes";
}
