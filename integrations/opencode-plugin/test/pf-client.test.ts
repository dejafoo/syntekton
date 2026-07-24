import { EventEmitter } from "node:events";
import { describe, expect, it } from "vitest";

import {
  assertProtocol,
  CliPfClient,
  HOST_PROTOCOL,
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
