import { describe, expect, it } from "vitest";

import { applyAgentGuidance, PF_AGENT_GUIDANCE, type MutableConfig } from "../src/agent-guidance.js";

describe("applyAgentGuidance", () => {
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
