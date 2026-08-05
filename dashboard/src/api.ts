export type Dict = Record<string, unknown>;

export interface ContentRef {
  sha256: string;
  media_type?: string;
  byte_count?: number;
  capture_level?: string;
  preview?: string | null;
  logical_name?: string | null;
}

export interface RunSummary extends Dict {
  run_id: string;
  workflow_type: string;
  status: string;
  latest_seq: number;
  task_counts?: Record<string, number>;
  usage?: Dict;
  budget?: Dict;
  liveness?: string;
  active_operation?: string | null;
  updated_at?: string | null;
  error_count?: number;
  next_action?: string | null;
}

export interface TaskSummary extends Dict {
  task_id: string;
  capability: string;
  status: string;
  title?: string | null;
  dependencies?: string[];
  model_profile?: string | null;
  agent_profile?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  summary?: string | null;
  usage?: Dict;
  liveness?: string;
  active_operation?: string | null;
  effective_policy?: Dict | null;
  route_class?: string | null;
  primary_model_profile?: string | null;
  fallback_model_profile?: string | null;
  fallback_eligible?: boolean | null;
  stack_profile_digest?: string | null;
  legacy_policy?: boolean;
  next_action?: string | null;
}

export interface StreamEvent extends Dict {
  event_id: string;
  seq?: number | null;
  type: string;
  run_id: string;
  task_id?: string | null;
  severity?: string;
  summary?: string;
  payload?: Dict;
  content_refs?: ContentRef[];
  recorded_at?: string | null;
  occurred_at?: string | null;
}

export interface ContentView extends Dict {
  sha256: string;
  available: boolean;
  capture_level: string;
  media_type?: string | null;
  byte_count?: number | null;
  truncated?: boolean;
  payload?: unknown;
  redacted?: boolean;
  reason?: string | null;
  visibility?: string | null;
  content_class?: string | null;
  legacy?: boolean;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: "same-origin" });
  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return response.json() as Promise<T>;
}

export function eventItems(value: { items?: StreamEvent[] } | StreamEvent[]): StreamEvent[] {
  return Array.isArray(value) ? value : value.items ?? [];
}

export function eventIdentity(event: Pick<StreamEvent, "event_id" | "seq">): string {
  return event.event_id || `seq:${event.seq ?? "unknown"}`;
}

export function statusClass(status: string | null | undefined): string {
  return `status ${(status || "unknown").toLowerCase().replaceAll("_", "-")}`;
}

export type KanbanColumn = "queued" | "running" | "repairing" | "awaiting approval" | "succeeded" | "failed / blocked";

export function taskColumn(task: Pick<TaskSummary, "capability" | "status">): KanbanColumn {
  const status = (task.status || "").toLowerCase();
  if (["success", "succeeded", "completed"].includes(status)) return "succeeded";
  if (["failed", "blocked", "cancelled", "canceled", "timed_out", "budget_exhausted"].includes(status)) return "failed / blocked";
  if (["awaiting_approval", "awaiting-approval", "approval_required"].includes(status)) return "awaiting approval";
  if (task.capability === "repair" && !["failed", "blocked", "cancelled", "canceled"].includes(status)) return "repairing";
  if (["pending", "queued", "initializing", "planning", "not_started"].includes(status)) return "queued";
  return "running";
}

/**
 * Returns only the read models an event can change.  Events remain a signal to
 * refresh durable projections, never the source of task or run state.
 */
export function projectionsForEvent(type: string): string[] {
  if (type === "heartbeat") return [];
  if (type.startsWith("task.")) return ["run", "tasks", "lineage", "costs"];
  if (type.startsWith("plan.") || type.startsWith("workflow.")) return ["run", "plan", "tasks", "lineage"];
  if (type.startsWith("model.")) return ["run", "invocations", "costs"];
  if (type.startsWith("tool.")) return ["run", "tools"];
  if (type.startsWith("validation.")) return ["run", "validations", "lineage", "tasks"];
  if (type.startsWith("artifact.")) return ["run", "artifacts"];
  if (type.startsWith("prompt.")) return ["run", "prompts"];
  if (type.startsWith("budget.")) return ["run", "costs"];
  if (type.startsWith("approval.")) return ["run", "tasks", "lineage"];
  if (type.startsWith("run.")) return ["run", "tasks", "plan", "lineage", "costs", "artifacts", "prompts"];
  // Host integrations may introduce a new named event. Refresh the durable
  // projections rather than silently ignoring it.
  return ["run", "tasks", "plan", "lineage", "costs", "invocations", "tools", "validations", "artifacts", "prompts"];
}

export interface SseFrame {
  event: string;
  id?: string;
  data: string;
}

/** Parse one complete Server-Sent Event frame according to the SSE field rules. */
export function parseSseFrame(frame: string): SseFrame | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of frame.replaceAll("\r", "").split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value = separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "id") id = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, id, data: data.join("\n") } : null;
}

/**
 * Read all named events from the API stream.  EventSource has no wildcard
 * listener, whereas the API deliberately uses event.type as its SSE event
 * name. Reading the wire format keeps the dashboard forward-compatible with
 * host lifecycle events without requiring a server-side compatibility shim.
 */
export async function streamRunEvents(
  runId: string,
  afterSeq: number,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
  onConnected?: () => void,
): Promise<void> {
  const response = await fetch(
    `/api/v1/runs/${encodeURIComponent(runId)}/events/stream?after_seq=${Math.max(0, afterSeq)}`,
    { credentials: "same-origin", headers: { Accept: "text/event-stream" }, signal },
  );
  if (!response.ok) throw new ApiError(response.status, await response.text());
  if (!response.body) throw new Error("The browser did not expose an event-stream body.");
  onConnected?.();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = parseSseFrame(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (frame) {
          try {
            const event = JSON.parse(frame.data) as StreamEvent;
            onEvent({ ...event, type: event.type || frame.event });
          } catch {
            // A malformed frame must not take the monitoring page down; the
            // next durable projection refresh is still authoritative.
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}
