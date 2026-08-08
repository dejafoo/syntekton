import { describe, expect, it } from "vitest";
import {
  isUnsupportedRemoteDashboard,
  parseSseFrame,
  projectionsForEvent,
  taskColumn,
  unsupportedRemoteDashboardMessage,
} from "./api";

describe("dashboard API compatibility helpers", () => {
  it("parses arbitrary named SSE events instead of relying on a fixed listener list", () => {
    expect(parseSseFrame("id: 42\nevent: artifact.materialized\ndata: {\"type\":\"artifact.materialized\"}\n"))
      .toEqual({ id: "42", event: "artifact.materialized", data: "{\"type\":\"artifact.materialized\"}" });
  });

  it("maps current host lifecycle task states into useful monitor-only columns", () => {
    expect(taskColumn({ capability: "implement", status: "queued" })).toBe("queued");
    expect(taskColumn({ capability: "implement", status: "awaiting_approval" })).toBe("awaiting approval");
    expect(taskColumn({ capability: "repair", status: "running" })).toBe("repairing");
    expect(taskColumn({ capability: "implement", status: "cancelled" })).toBe("failed / blocked");
  });

  it("refreshes durable evidence projections for newly added host events", () => {
    expect(projectionsForEvent("artifact.materialized")).toContain("artifacts");
    expect(projectionsForEvent("run.cancelled")).toEqual(expect.arrayContaining(["run", "tasks", "costs"]));
    expect(projectionsForEvent("host.future_event")).toEqual(expect.arrayContaining(["run", "plan", "prompts"]));
  });

  it("invalidates task projections so policy/route fields refresh from durable state", () => {
    expect(projectionsForEvent("task.started")).toEqual(
      expect.arrayContaining(["run", "tasks", "lineage", "costs"]),
    );
    expect(projectionsForEvent("model.request.completed")).toEqual(
      expect.arrayContaining(["run", "invocations", "costs"]),
    );
  });

  it("explains unsupported authenticated remote browser use without mentioning bearer storage", () => {
    const message = unsupportedRemoteDashboardMessage({
      remote_mode: true,
      dashboard: { remote_browser: "unsupported", deployment_support: "loopback_monitor_only" },
    });
    expect(message).toMatch(/loopback\/monitor-only/);
    expect(message).toMatch(/SSH\/private tunnel/);
    expect(message.toLowerCase()).not.toMatch(/bearer/);
    expect(message.toLowerCase()).not.toMatch(/localstorage/);
    expect(isUnsupportedRemoteDashboard({
      dashboard: { remote_browser: "unsupported", deployment_support: "loopback_monitor_only" },
    })).toBe(true);
  });
});
