import { describe, expect, it } from "vitest";

import { applyAgentGuidance, PF_AGENT_GUIDANCE, type MutableConfig } from "../src/agent-guidance.js";

describe("applyAgentGuidance", () => {
  it("documents remote env vars and no auto-start of repository_change", () => {
    expect(PF_AGENT_GUIDANCE).toContain("PRODUCT_FACTORY_REMOTE_URL");
    expect(PF_AGENT_GUIDANCE).toContain("PRODUCT_FACTORY_OBSERVE_TOKEN");
    expect(PF_AGENT_GUIDANCE).toContain("NEVER auto-start");
    expect(PF_AGENT_GUIDANCE).toContain("repository_change");
  });

  it("appends guidance to an empty config", () => {
    const config: MutableConfig = {};
    applyAgentGuidance(config);
    expect(config.instructions).toEqual([PF_AGENT_GUIDANCE]);
  });

  it("preserves existing instructions", () => {
    const config: MutableConfig = { instructions: ["existing"] };
    applyAgentGuidance(config);
    expect(config.instructions).toEqual(["existing", PF_AGENT_GUIDANCE]);
  });

  it("is idempotent", () => {
    const config: MutableConfig = {};
    applyAgentGuidance(config);
    applyAgentGuidance(config);
    expect(config.instructions).toHaveLength(1);
  });
});
