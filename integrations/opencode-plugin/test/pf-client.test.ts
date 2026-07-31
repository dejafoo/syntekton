import { EventEmitter } from "node:events";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  assertProtocol,
  CliPfClient,
  createPfClient,
  HOST_PROTOCOL,
  landRemoteDelivery,
  landMapFrom,
  parseHostJson,
  parseSseBlock,
  PfProtocolError,
  RemotePfClient,
} from "../src/pf-client.js";

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

describe("parseHostJson", () => {
  it("parses the last JSON envelope, ignoring log noise", () => {
    const stdout = [
      "some log line",
      JSON.stringify({ protocol: HOST_PROTOCOL, ok: true, status: "queued", artifacts: [], events: [] }),
      JSON.stringify({ protocol: HOST_PROTOCOL, ok: true, status: "awaiting_approval", artifacts: [], events: [] }),
    ].join("\n");
    const res = parseHostJson(stdout);
    expect(res.status).toBe("awaiting_approval");
  });

  it("throws when there is no JSON", () => {
    expect(() => parseHostJson("no json here\n")).toThrow(PfProtocolError);
  });
});

describe("assertProtocol", () => {
  it("passes matching protocol through", () => {
    const res = { protocol: HOST_PROTOCOL, ok: true, artifacts: [], events: [] };
    expect(assertProtocol(res)).toBe(res);
  });

  it("rejects a mismatched protocol", () => {
    expect(() => assertProtocol({ protocol: "other/v9", ok: true, artifacts: [], events: [] })).toThrow(
      PfProtocolError,
    );
  });
});

/** Fake child process for the injected spawn. */
function fakeSpawn(stdout: string, exitCode = 0) {
  return () => {
    const child = new EventEmitter() as EventEmitter & {
      stdout: EventEmitter;
      stderr: EventEmitter;
      kill: () => void;
    };
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => undefined;
    setTimeout(() => {
      child.stdout.emit("data", Buffer.from(stdout));
      child.emit("close", exitCode);
    }, 0);
    return child as never;
  };
}

describe("CliPfClient", () => {
  it("checks protocol on first call and parses the envelope", async () => {
    const stdout = JSON.stringify({
      protocol: HOST_PROTOCOL,
      ok: true,
      run_id: "run-abc",
      status: "queued",
      artifacts: [],
      events: [],
    });
    const client = new CliPfClient({ spawnFn: fakeSpawn(stdout) as never });
    expect(client.isProtocolChecked).toBe(false);
    const out = await client.status("run-abc");
    expect(out.run_id).toBe("run-abc");
    expect(client.isProtocolChecked).toBe(true);
  });

  it("still resolves a failure envelope emitted on a non-zero exit", async () => {
    const stdout = JSON.stringify({
      protocol: HOST_PROTOCOL,
      ok: false,
      run_id: "run-x",
      artifacts: [],
      events: [],
      error: { code: "not_found", message: "Unknown run run-x" },
    });
    const client = new CliPfClient({ spawnFn: fakeSpawn(stdout, 1) as never });
    const out = await client.status("run-x");
    expect(out.ok).toBe(false);
    expect(out.error?.code).toBe("not_found");
  });

  it("rejects when a mismatched protocol comes back", async () => {
    const stdout = JSON.stringify({ protocol: "bogus/v0", ok: true, artifacts: [], events: [] });
    const client = new CliPfClient({ spawnFn: fakeSpawn(stdout) as never });
    await expect(client.status("run-x")).rejects.toBeInstanceOf(PfProtocolError);
  });
});

/** Records the argv the client would hand to `product-factory`. */
function recordingSpawn(argvSink: string[][], stdout: string) {
  return (_bin: string, argv: string[]) => {
    argvSink.push(argv);
    return fakeSpawn(stdout)() as never;
  };
}

const OK_ENVELOPE = JSON.stringify({
  protocol: HOST_PROTOCOL,
  ok: true,
  run_id: "run-1",
  artifacts: [],
  events: [],
});

describe("CliPfClient artifact naming", () => {
  it("passes artifact overrides to host submit", async () => {
    const argvs: string[][] = [];
    const client = new CliPfClient({ spawnFn: recordingSpawn(argvs, OK_ENVELOPE) as never });

    await client.submit({
      requestText: "Design integration testing",
      workflow: "technical_plan",
      artifactOverrides: {
        architecture_document: { destPath: "docs/integration_testing_architecture.md" },
        evidence_report: { logicalName: "notes.md" },
      },
    });

    const argv = argvs[0]!;
    expect(argv).toContain("--artifact-override");
    expect(argv).toContain("architecture_document=docs/integration_testing_architecture.md");
    expect(argv).toContain("--artifact-name");
    expect(argv).toContain("evidence_report=notes.md");
  });

  it("builds a materialize-all invocation with roles and overwrite", async () => {
    const argvs: string[][] = [];
    const client = new CliPfClient({ spawnFn: recordingSpawn(argvs, OK_ENVELOPE) as never });

    await client.materializeAll("run-1", { roles: ["test_plan"], overwrite: true });

    expect(argvs[0]).toEqual([
      "host",
      "materialize-all",
      "run-1",
      "--role",
      "test_plan",
      "--overwrite",
    ]);
  });
});

describe("landMapFrom", () => {
  it("reads well-formed land map entries", () => {
    const res = parseHostJson(
      JSON.stringify({
        protocol: HOST_PROTOCOL,
        ok: true,
        artifacts: [],
        events: [],
        data: {
          artifact_land_map: [
            {
              role: "architecture_document",
              logical_name: "scoped.md",
              suggested_dest_path: "docs/scoped.md",
            },
          ],
        },
      }),
    );
    expect(landMapFrom(res)).toEqual([
      {
        role: "architecture_document",
        logical_name: "scoped.md",
        suggested_dest_path: "docs/scoped.md",
      },
    ]);
  });

  it("returns nothing for hosts that predate the land map", () => {
    const res = parseHostJson(
      JSON.stringify({ protocol: HOST_PROTOCOL, ok: true, artifacts: [], events: [] }),
    );
    expect(landMapFrom(res)).toEqual([]);
  });

  it("drops malformed entries rather than trusting them", () => {
    const res = parseHostJson(
      JSON.stringify({
        protocol: HOST_PROTOCOL,
        ok: true,
        artifacts: [],
        events: [],
        data: { artifact_land_map: [{ role: "x" }, null, "nope"] },
      }),
    );
    expect(landMapFrom(res)).toEqual([]);
  });
});

function hostEnvelope(partial: Record<string, unknown> = {}) {
  return {
    protocol: HOST_PROTOCOL,
    ok: true,
    artifacts: [],
    events: [],
    ...partial,
  };
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("createPfClient", () => {
  it("selects RemotePfClient when PRODUCT_FACTORY_REMOTE_URL is set", () => {
    const client = createPfClient({
      env: {
        PRODUCT_FACTORY_REMOTE_URL: "https://pf.example",
        PRODUCT_FACTORY_OBSERVE_TOKEN: "secret",
      },
    });
    expect(client).toBeInstanceOf(RemotePfClient);
    expect(client.transport).toEqual({ mode: "remote", endpoint: "https://pf.example" });
  });

  it("selects CliPfClient when remote URL is unset", () => {
    const client = createPfClient({
      env: { PRODUCT_FACTORY_REMOTE_URL: "", PRODUCT_FACTORY_BIN: "pf" },
      directory: "/tmp",
    });
    expect(client).toBeInstanceOf(CliPfClient);
    expect(client.transport.mode).toBe("cli");
  });
});

describe("RemotePfClient", () => {
  it("POSTs submit with pack_input, handoff_refs, repository_id and bearer auth", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchFn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return jsonResponse(
        hostEnvelope({
          run_id: "run-r1",
          status: "queued",
          subscription: {
            sse_url: "https://pf.example/api/v1/runs/run-r1/events/stream?after_seq=0",
            cli_tail: "product-factory host tail run-r1",
          },
        }),
        202,
      );
    });

    const client = new RemotePfClient({
      baseUrl: "https://pf.example/",
      token: "tok",
      fetchFn: fetchFn as unknown as typeof fetch,
    });

    const out = await client.submit({
      requestText: "frame this change",
      workflow: "change_intake",
      repositoryId: "main-app",
      packInput: { decision_statement: "ship?" },
      handoffRefs: [{ schema_id: "feasibility_dossier.v1", digest: "abc" }],
      artifactOverrides: {
        change_brief: { destPath: "docs/BRIEF.md" },
      },
      budgetUsd: 2.5,
    });

    expect(out.run_id).toBe("run-r1");
    expect(calls[0]?.url).toBe("https://pf.example/api/v1/runs");
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.init?.headers).toMatchObject({
      Authorization: "Bearer tok",
      "Content-Type": "application/json",
    });
    const body = JSON.parse(String(calls[0]?.init?.body));
    expect(body).toMatchObject({
      request_text: "frame this change",
      workflow_type: "change_intake",
      repository_id: "main-app",
      pack_input: { decision_statement: "ship?" },
      handoff_refs: [{ schema_id: "feasibility_dossier.v1", digest: "abc" }],
      budget_usd: 2.5,
      artifact_overrides: { change_brief: { dest_path: "docs/BRIEF.md" } },
    });
    expect(body.repository_path).toBeUndefined();
  });

  it("rejects repository_path in remote mode", async () => {
    const client = new RemotePfClient({
      baseUrl: "https://pf.example",
      fetchFn: vi.fn() as unknown as typeof fetch,
    });
    await expect(
      client.submit({ requestText: "x", repositoryPath: "/Users/me/repo" }),
    ).rejects.toBeInstanceOf(PfProtocolError);
  });

  it("GETs host/v1 /tail (not raw /events) for event batches", async () => {
    const fetchFn = vi.fn(async (url: string | URL | Request, _init?: RequestInit) => {
      expect(String(url)).toBe("https://pf.example/api/v1/runs/run-1/tail?after_seq=7");
      return jsonResponse(
        hostEnvelope({
          run_id: "run-1",
          status: "executing",
          events: [{ seq: 8, type: "task.started" }],
          subscription: {
            sse_url: "https://pf.example/api/v1/runs/run-1/events/stream?after_seq=7",
            cli_tail: "product-factory host tail run-1",
          },
        }),
      );
    });
    const client = new RemotePfClient({
      baseUrl: "https://pf.example",
      token: "tok",
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    const out = await client.tail("run-1", { afterSeq: 7 });
    expect(out.ok).toBe(true);
    expect(out.events).toHaveLength(1);
    expect(fetchFn.mock.calls[0]?.[1]).toMatchObject({
      method: "GET",
      headers: expect.objectContaining({ Authorization: "Bearer tok" }),
    });
  });

  it("never falls back to CLI when remote is unreachable", async () => {
    const fetchFn = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const client = new RemotePfClient({
      baseUrl: "https://pf.example",
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    await expect(client.status("run-1")).rejects.toThrow(/refusing to fall back to local CLI/);
  });

  it("rejects a mismatched host protocol", async () => {
    const fetchFn = vi.fn(async () => jsonResponse({ protocol: "other/v0", ok: true, artifacts: [], events: [] }));
    const client = new RemotePfClient({
      baseUrl: "https://pf.example",
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    await expect(client.status("run-1")).rejects.toBeInstanceOf(PfProtocolError);
  });

  it("fetches delivery manifests and binary blobs with bearer auth", async () => {
    const digest = "a".repeat(64);
    const fetchFn = vi.fn(async (url: string | URL | Request, _init?: RequestInit) => {
      if (String(url).endsWith(`/blobs/${digest}`)) {
        return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
      }
      return jsonResponse({
        schema_version: "delivery_manifest.v1",
        delivery_id: "delivery-1",
        run_id: "run-1",
        base_revision: "abc",
        created_at: "2026-01-01T00:00:00Z",
        entries: [],
        manifest_sha256: "b".repeat(64),
      });
    });
    const client = new RemotePfClient({
      baseUrl: "https://pf.example",
      token: "tok",
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    const manifest = await client.delivery("run-1");
    const blob = await client.deliveryBlob("run-1", digest);
    expect(manifest.delivery_id).toBe("delivery-1");
    expect(Array.from(blob)).toEqual([1, 2, 3]);
    expect(fetchFn.mock.calls[1]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer tok",
    });
  });

  it("wait prefers SSE then returns awaiting_approval status", async () => {
    const sseBody =
      "event: heartbeat\ndata: {\"type\":\"heartbeat\"}\n\n" +
      "event: run.status_changed\ndata: {\"type\":\"run.status_changed\",\"payload\":{\"status\":\"awaiting_approval\"}}\n\n";
    let statusCalls = 0;

    const fetchFn = vi.fn(async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes("/events/stream")) {
        return new Response(sseBody, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      if (u.endsWith("/status")) {
        statusCalls += 1;
        return jsonResponse(
          hostEnvelope({
            run_id: "run-1",
            status: statusCalls <= 1 ? "executing" : "awaiting_approval",
            subscription: {
              sse_url: "https://pf.example/api/v1/runs/run-1/events/stream?after_seq=0",
              cli_tail: "product-factory host tail run-1",
            },
          }),
        );
      }
      throw new Error(`unexpected url ${u}`);
    });

    const client = new RemotePfClient({
      baseUrl: "https://pf.example",
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    const out = await client.wait("run-1", { maxPolls: 5, intervalMs: 0, sleep: async () => undefined });
    expect(out.status).toBe("awaiting_approval");
    expect(fetchFn.mock.calls.some((c) => String(c[0]).includes("/events/stream"))).toBe(true);
    expect(statusCalls).toBeGreaterThanOrEqual(2);
  });

  it("wait falls back to status polling when SSE is unavailable", async () => {
    let polls = 0;
    const fetchFn = vi.fn(async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes("/events/stream")) {
        return new Response("nope", { status: 500 });
      }
      polls += 1;
      return jsonResponse(
        hostEnvelope({
          run_id: "run-1",
          status: polls >= 3 ? "awaiting_approval" : "executing",
          subscription: {
            sse_url: "https://pf.example/api/v1/runs/run-1/events/stream?after_seq=0",
            cli_tail: "x",
          },
        }),
      );
    });

    const client = new RemotePfClient({
      baseUrl: "https://pf.example",
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    const out = await client.wait("run-1", { maxPolls: 5, intervalMs: 0, sleep: async () => undefined });
    expect(out.status).toBe("awaiting_approval");
    expect(polls).toBeGreaterThanOrEqual(3);
  });

  it("uses PRODUCT_FACTORY_HOST_TOKEN when OBSERVE token is absent via factory", async () => {
    const fetchFn = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
      jsonResponse(hostEnvelope({ run_id: "run-1", status: "queued" })),
    );
    const client = createPfClient({
      env: {
        PRODUCT_FACTORY_REMOTE_URL: "https://pf.example",
        PRODUCT_FACTORY_HOST_TOKEN: "host-tok",
      },
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    await (client as RemotePfClient).status("run-1");
    const init = fetchFn.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer host-tok");
  });
});

describe("landRemoteDelivery", () => {
  it("verifies manifest and blob hashes before writing under the workspace", () => {
    const root = mkdtempSync(join(tmpdir(), "pf-plugin-land-"));
    try {
      execFileSync("git", ["init", "-q"], { cwd: root });
      execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: root });
      execFileSync("git", ["config", "user.name", "Test"], { cwd: root });
      writeFileSync(join(root, "README.md"), "base\n");
      execFileSync("git", ["add", "."], { cwd: root });
      execFileSync("git", ["commit", "-qm", "base"], { cwd: root });
      const base = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
      const content = Buffer.from("# Delivered\n");
      const blobDigest = createHash("sha256").update(content).digest("hex");
      const payload = {
        schema_version: "delivery_manifest.v1" as const,
        delivery_id: "delivery-1",
        run_id: "run-1",
        base_revision: base,
        workspace_provenance: null,
        created_at: "2026-07-31T00:00:00Z",
        entries: [
          {
            role: "architecture_document",
            logical_name: "PLAN.md",
            blob_sha256: blobDigest,
            size_bytes: content.byteLength,
            media_type: "text/markdown",
            kind: "file" as const,
            suggested_dest_path: "docs/PLAN.md",
            changed_paths: [],
          },
        ],
      };
      const manifest = {
        ...payload,
        manifest_sha256: createHash("sha256").update(canonical(payload)).digest("hex"),
      };

      const result = landRemoteDelivery(
        manifest,
        new Map([[blobDigest, content]]),
        root,
      );

      expect(readFileSync(join(root, "docs/PLAN.md"), "utf8")).toBe("# Delivered\n");
      expect(result.landed_paths).toEqual(["docs/PLAN.md"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});

describe("parseSseBlock", () => {
  it("parses id/event/data lines", () => {
    expect(parseSseBlock("id: 3\nevent: run.finished\ndata: {\"ok\":true}")).toEqual({
      id: "3",
      event: "run.finished",
      data: '{"ok":true}',
    });
  });
});
