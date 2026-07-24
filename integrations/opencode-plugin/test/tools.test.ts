import { describe, expect, it, vi } from "vitest";

import type { PfClient, PfResponse } from "../src/pf-client.js";
import {
  pfDecline,
  pfMerge,
  pfRun,
  pfWait,
  type PfToolContext,
  type PfToolDeps,
} from "../src/tools.js";

function res(partial: Partial<PfResponse>): PfResponse {
  return {
    protocol: "product-factory.host/v1",
    ok: true,
    artifacts: [],
    events: [],
    ...partial,
  };
}

/** Mock PfClient whose methods are vi.fn() and return queued/preset responses. */
function mockClient(overrides: Partial<Record<keyof PfClient, PfResponse>> = {}): {
  client: PfClient;
  calls: Record<keyof PfClient, ReturnType<typeof vi.fn>>;
} {
  const calls = {
    submit: vi.fn(async () => overrides.submit ?? res({ run_id: "run-1", status: "queued" })),
    status: vi.fn(async () => overrides.status ?? res({ run_id: "run-1", status: "awaiting_approval" })),
    inspect: vi.fn(async () => overrides.inspect ?? res({ run_id: "run-1", status: "awaiting_approval" })),
    tail: vi.fn(async () => overrides.tail ?? res({ run_id: "run-1" })),
    approve: vi.fn(async () => overrides.approve ?? res({ run_id: "run-1", status: "completed" })),
    reject: vi.fn(async () => overrides.reject ?? res({ run_id: "run-1", status: "blocked" })),
    cancel: vi.fn(async () => overrides.cancel ?? res({ run_id: "run-1", status: "cancelled" })),
    materialize: vi.fn(
      async () =>
        overrides.materialize ??
        res({ run_id: "run-1", status: "completed", data: { written_path: "docs/ARCHITECTURE.md" } }),
    ),
  };
  return { client: calls as unknown as PfClient, calls: calls as never };
}

const allow: PfToolContext["ask"] = vi.fn(async () => undefined);
const deny: PfToolContext["ask"] = vi.fn(async () => {
  throw new Error("user denied");
});

describe("pf_merge confirmation gate", () => {
  it("does NOT approve or materialize when the operator declines", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "awaiting_approval", data: { workflow_type: "technical_plan" } }),
    });
    const deps: PfToolDeps = { client };
    const ctx: PfToolContext = { ask: deny };

    const out = await pfMerge(deps, ctx, { run_id: "run-1" });

    expect(calls.approve).not.toHaveBeenCalled();
    expect(calls.materialize).not.toHaveBeenCalled();
    expect(out).toContain("merge_declined");
  });

  it("does NOT merge when no ask/permission function is available (fail-closed)", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "awaiting_approval", data: { workflow_type: "technical_plan" } }),
    });
    const deps: PfToolDeps = { client };
    const ctx: PfToolContext = {}; // no ask

    const out = await pfMerge(deps, ctx, { run_id: "run-1" });

    expect(calls.approve).not.toHaveBeenCalled();
    expect(calls.materialize).not.toHaveBeenCalled();
    expect(out).toContain("merge_declined");
  });

  it("doc/report: after allow, approves then materializes with per-workflow defaults", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "awaiting_approval", data: { workflow_type: "technical_plan" } }),
    });
    const deps: PfToolDeps = { client };
    const ctx: PfToolContext = { ask: allow };

    await pfMerge(deps, ctx, { run_id: "run-1" });

    expect(calls.approve).toHaveBeenCalledWith("run-1", { apply: false });
    expect(calls.materialize).toHaveBeenCalledWith("run-1", {
      artifact: "ARCHITECTURE.md",
      destPath: "docs/ARCHITECTURE.md",
      overwrite: false,
    });
  });

  it("repository_investigation defaults to EVIDENCE_REPORT.md", async () => {
    const { client, calls } = mockClient({
      status: res({
        run_id: "run-1",
        status: "awaiting_approval",
        data: { workflow_type: "repository_investigation" },
      }),
    });
    const deps: PfToolDeps = { client };

    await pfMerge(deps, { ask: allow }, { run_id: "run-1" });

    expect(calls.materialize).toHaveBeenCalledWith("run-1", {
      artifact: "EVIDENCE_REPORT.md",
      destPath: "docs/EVIDENCE_REPORT.md",
      overwrite: false,
    });
  });

  it("patch workflow: after allow, approves with apply=true and does NOT materialize", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "awaiting_approval", data: { workflow_type: "repository_change" } }),
    });
    const deps: PfToolDeps = { client };

    await pfMerge(deps, { ask: allow }, { run_id: "run-1" });

    expect(calls.approve).toHaveBeenCalledWith("run-1", { apply: true });
    expect(calls.materialize).not.toHaveBeenCalled();
  });

  it("honors explicit artifact/dest_path overrides for doc workflows", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "completed", data: { workflow_type: "technical_plan" } }),
    });
    const deps: PfToolDeps = { client };

    await pfMerge(deps, { ask: allow }, {
      run_id: "run-1",
      artifact: "ARCHITECTURE.md",
      dest_path: "design/plan.md",
      overwrite: true,
    });

    // Already completed → no re-approve needed.
    expect(calls.approve).not.toHaveBeenCalled();
    expect(calls.materialize).toHaveBeenCalledWith("run-1", {
      artifact: "ARCHITECTURE.md",
      destPath: "design/plan.md",
      overwrite: true,
    });
  });
});

describe("pf_run", () => {
  it("defaults repository_path to the OpenCode worktree", async () => {
    const { client, calls } = mockClient();
    const deps: PfToolDeps = { client };
    const ctx: PfToolContext = { directory: "/proj", worktree: "/proj/wt" };

    await pfRun(deps, ctx, { request: "do a thing", workflow: "technical_plan" });

    expect(calls.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        requestText: "do a thing",
        workflow: "technical_plan",
        repositoryPath: "/proj/wt",
      }),
    );
  });
});

describe("pf_wait", () => {
  it("stops once status is awaiting_approval", async () => {
    const responses = [
      res({ status: "planning" }),
      res({ status: "executing" }),
      res({ status: "awaiting_approval" }),
    ];
    const status = vi.fn(async () => responses.shift() ?? res({ status: "completed" }));
    const client = { status } as unknown as PfClient;
    const sleep = vi.fn(async () => undefined);
    const deps: PfToolDeps = { client, wait: { intervalMs: 0, sleep } };

    const out = await pfWait(deps, {}, { run_id: "run-1" });

    expect(status).toHaveBeenCalledTimes(3);
    expect(out).toContain("awaiting_approval");
  });
});

describe("pf_decline", () => {
  it("rejects when awaiting_approval and cancels otherwise", async () => {
    const rejecting = mockClient({ status: res({ status: "awaiting_approval" }) });
    await pfDecline({ client: rejecting.client }, {}, { run_id: "run-1" });
    expect(rejecting.calls.reject).toHaveBeenCalled();
    expect(rejecting.calls.cancel).not.toHaveBeenCalled();

    const cancelling = mockClient({ status: res({ status: "executing" }) });
    await pfDecline({ client: cancelling.client }, {}, { run_id: "run-1" });
    expect(cancelling.calls.cancel).toHaveBeenCalled();
    expect(cancelling.calls.reject).not.toHaveBeenCalled();
  });
});
