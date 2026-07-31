import { describe, expect, it, vi } from "vitest";

import type { PfClient, PfResponse } from "../src/pf-client.js";
import {
  normalizeArtifactOverrides,
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
function mockClient(
  overrides: Partial<Record<keyof PfClient, PfResponse>> = {},
  transport: PfClient["transport"] = { mode: "cli" },
): {
  client: PfClient;
  calls: Record<string, ReturnType<typeof vi.fn>>;
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
    materializeAll: vi.fn(
      async () =>
        overrides.materializeAll ??
        res({ run_id: "run-1", status: "completed", data: { landed: [], skipped: [] } }),
    ),
    delivery: vi.fn(),
    deliveryBlob: vi.fn(),
    recordLanding: vi.fn(),
  };
  return {
    client: { ...calls, transport } as unknown as PfClient,
    calls: calls as never,
  };
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

/** Inspect envelope carrying a resolved land map, as P4.A hosts return. */
function landMapInspect(entries: Array<Record<string, unknown>>): PfResponse {
  return res({
    run_id: "run-1",
    status: "awaiting_approval",
    data: { artifact_land_map: entries },
  });
}

describe("pf_merge land map", () => {
  it("lands the run's named deliverable instead of the generic default", async () => {
    const { client, calls } = mockClient({
      status: res({
        run_id: "run-1",
        status: "awaiting_approval",
        data: { workflow_type: "technical_plan" },
      }),
      inspect: landMapInspect([
        {
          role: "architecture_document",
          logical_name: "integration_testing_architecture.md",
          suggested_dest_path: "docs/integration_testing_architecture.md",
          landable: true,
        },
      ]),
    });

    await pfMerge({ client }, { ask: allow }, { run_id: "run-1" });

    expect(calls.approve).toHaveBeenCalledWith("run-1", { apply: false });
    expect(calls.materializeAll).toHaveBeenCalledWith("run-1", {
      roles: undefined,
      overwrite: false,
    });
    // The generic single-file default must not be used when a land map exists.
    expect(calls.materialize).not.toHaveBeenCalled();
  });

  it("shows every destination in the confirmation prompt", async () => {
    const asked: Array<{ patterns?: string[]; metadata?: Record<string, unknown> }> = [];
    const ask: PfToolContext["ask"] = async (input) => {
      asked.push(input);
    };
    const { client } = mockClient({
      status: res({ run_id: "run-1", status: "completed", data: { workflow_type: "quality_gate" } }),
      inspect: landMapInspect([
        {
          role: "test_plan",
          logical_name: "TEST_PLAN.md",
          suggested_dest_path: "docs/TEST_PLAN.md",
          landable: true,
        },
        {
          role: "quality_findings",
          logical_name: "QUALITY_FINDINGS.md",
          suggested_dest_path: "docs/QUALITY_FINDINGS.md",
          landable: true,
        },
      ]),
    });

    await pfMerge({ client }, { ask }, { run_id: "run-1" });

    expect(asked).toHaveLength(1);
    expect(asked[0]?.patterns).toEqual([
      "run-1",
      "docs/TEST_PLAN.md",
      "docs/QUALITY_FINDINGS.md",
    ]);
    expect(asked[0]?.metadata?.destinations).toEqual([
      "docs/TEST_PLAN.md",
      "docs/QUALITY_FINDINGS.md",
    ]);
  });

  it("declining a multi-deliverable merge lands nothing", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "completed", data: { workflow_type: "technical_plan" } }),
      inspect: landMapInspect([
        {
          role: "architecture_document",
          logical_name: "scoped.md",
          suggested_dest_path: "docs/scoped.md",
          landable: true,
        },
      ]),
    });

    const out = await pfMerge({ client }, { ask: deny }, { run_id: "run-1" });

    expect(out).toContain("merge_declined");
    expect(calls.materializeAll).not.toHaveBeenCalled();
    expect(calls.materialize).not.toHaveBeenCalled();
  });

  it("limits the merge to requested roles", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "completed", data: { workflow_type: "quality_gate" } }),
      inspect: landMapInspect([
        {
          role: "test_plan",
          logical_name: "TEST_PLAN.md",
          suggested_dest_path: "docs/TEST_PLAN.md",
          landable: true,
        },
        {
          role: "quality_findings",
          logical_name: "QUALITY_FINDINGS.md",
          suggested_dest_path: "docs/QUALITY_FINDINGS.md",
          landable: true,
        },
      ]),
    });

    await pfMerge({ client }, { ask: allow }, { run_id: "run-1", roles: ["test_plan"] });

    expect(calls.materializeAll).toHaveBeenCalledWith("run-1", {
      roles: ["test_plan"],
      overwrite: false,
    });
  });

  it("ignores non-landable land-map entries", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "completed", data: { workflow_type: "technical_plan" } }),
      inspect: landMapInspect([
        {
          role: "proposed_patch",
          logical_name: "proposed.patch",
          suggested_dest_path: "proposed.patch",
          landable: false,
        },
      ]),
    });

    await pfMerge({ client }, { ask: allow }, { run_id: "run-1" });

    expect(calls.materializeAll).not.toHaveBeenCalled();
    expect(calls.materialize).toHaveBeenCalled();
  });

  it("falls back to the quality report when a host returns no land map", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "completed", data: { workflow_type: "quality_gate" } }),
      inspect: res({ run_id: "run-1", status: "completed", data: {} }),
    });

    await pfMerge({ client }, { ask: allow }, { run_id: "run-1" });

    expect(calls.materializeAll).not.toHaveBeenCalled();
    expect(calls.materialize).toHaveBeenCalledWith("run-1", {
      artifact: "QUALITY_FINDINGS.md",
      destPath: "docs/QUALITY_FINDINGS.md",
      overwrite: false,
    });
  });

  it("explicit artifact/dest_path skips the land-map lookup entirely", async () => {
    const { client, calls } = mockClient({
      status: res({ run_id: "run-1", status: "completed", data: { workflow_type: "technical_plan" } }),
    });

    await pfMerge({ client }, { ask: allow }, {
      run_id: "run-1",
      dest_path: "design/plan.md",
    });

    expect(calls.inspect).not.toHaveBeenCalled();
    expect(calls.materializeAll).not.toHaveBeenCalled();
    expect(calls.materialize).toHaveBeenCalled();
  });

  it("patch workflows never consult the land map", async () => {
    const { client, calls } = mockClient({
      status: res({
        run_id: "run-1",
        status: "awaiting_approval",
        data: { workflow_type: "repository_change" },
      }),
    });

    await pfMerge({ client }, { ask: allow }, { run_id: "run-1" });

    expect(calls.inspect).not.toHaveBeenCalled();
    expect(calls.approve).toHaveBeenCalledWith("run-1", { apply: true });
  });
});

describe("pf_run", () => {
  it("defaults repository_path to the OpenCode worktree", async () => {
    const { client, calls } = mockClient();
    const deps: PfToolDeps = { client };
    const ctx: PfToolContext = { directory: "/proj", worktree: "/proj/wt" };

    const out = await pfRun(deps, ctx, { request: "do a thing", workflow: "technical_plan" });

    expect(calls.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        requestText: "do a thing",
        workflow: "technical_plan",
        repositoryPath: "/proj/wt",
      }),
    );
    expect(out).toContain('"mode": "cli"');
  });

  it("forwards artifact overrides so the deliverable is named up front", async () => {
    const { client, calls } = mockClient();

    await pfRun({ client }, {}, {
      request: "Design integration testing",
      workflow: "technical_plan",
      artifact_overrides: {
        architecture_document: "docs/integration_testing_architecture.md",
      },
    });

    expect(calls.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        artifactOverrides: {
          architecture_document: { destPath: "docs/integration_testing_architecture.md" },
        },
      }),
    );
  });

  it("remote mode omits repository_path and forwards repository_id", async () => {
    const { client, calls } = mockClient({}, { mode: "remote", endpoint: "https://pf.example" });
    const out = await pfRun(
      { client },
      { worktree: "/laptop/repo" },
      {
        request: "frame this",
        workflow: "change_intake",
        repository_id: "main-app",
        pack_input: { decision_statement: "ship?" },
        handoff_refs: [{ schema_id: "feasibility_dossier.v1", digest: "abc" }],
      },
    );

    expect(calls.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        repositoryPath: undefined,
        repositoryId: "main-app",
        packInput: { decision_statement: "ship?" },
        handoffRefs: [{ schema_id: "feasibility_dossier.v1", digest: "abc" }],
      }),
    );
    expect(out).toContain('"mode": "remote"');
    expect(out).toContain("https://pf.example");
  });
});

describe("normalizeArtifactOverrides", () => {
  it("accepts the string shorthand and the object form", () => {
    expect(normalizeArtifactOverrides({ architecture_document: "docs/x.md" })).toEqual({
      architecture_document: { destPath: "docs/x.md" },
    });
    expect(
      normalizeArtifactOverrides({ architecture_document: { logicalName: "x.md" } }),
    ).toEqual({ architecture_document: { logicalName: "x.md" } });
  });

  it("drops empty entries and returns undefined when nothing is left", () => {
    expect(normalizeArtifactOverrides({ architecture_document: "   " })).toBeUndefined();
    expect(normalizeArtifactOverrides(undefined)).toBeUndefined();
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
    const client = { status, transport: { mode: "cli" } } as unknown as PfClient;
    const sleep = vi.fn(async () => undefined);
    const deps: PfToolDeps = { client, wait: { intervalMs: 0, sleep } };

    const out = await pfWait(deps, {}, { run_id: "run-1" });

    expect(status).toHaveBeenCalledTimes(3);
    expect(out).toContain("awaiting_approval");
  });

  it("delegates to client.wait when present (SSE-capable remote)", async () => {
    const wait = vi.fn(async () => res({ status: "awaiting_approval", run_id: "run-1" }));
    const client = {
      transport: { mode: "remote", endpoint: "https://pf.example" },
      wait,
      status: vi.fn(),
    } as unknown as PfClient;

    const out = await pfWait({ client, wait: { intervalMs: 0, sleep: async () => undefined } }, {}, {
      run_id: "run-1",
    });

    expect(wait).toHaveBeenCalledWith(
      "run-1",
      expect.objectContaining({ maxPolls: 60, intervalMs: 0 }),
    );
    expect(out).toContain("awaiting_approval");
    expect(out).toContain('"mode": "remote"');
  });
});

describe("pf_merge remote mode", () => {
  it("decline performs no approval, download, or local write", async () => {
    const { client, calls } = mockClient({}, { mode: "remote", endpoint: "https://pf.example" });
    const out = await pfMerge({ client }, { ask: deny }, { run_id: "run-1" });

    expect(out).toContain("merge_declined");
    expect(calls.approve).not.toHaveBeenCalled();
    expect(calls.delivery).not.toHaveBeenCalled();
    expect(calls.deliveryBlob).not.toHaveBeenCalled();
    expect(calls.recordLanding).not.toHaveBeenCalled();
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
