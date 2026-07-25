import { EventEmitter } from "node:events";
import { describe, expect, it } from "vitest";

import {
  assertProtocol,
  CliPfClient,
  HOST_PROTOCOL,
  landMapFrom,
  parseHostJson,
  PfProtocolError,
} from "../src/pf-client.js";

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
