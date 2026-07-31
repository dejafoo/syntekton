/**
 * @product-factory/opencode-plugin
 *
 * Thin OpenCode adapter over the Product Factory host protocol
 * (`product-factory.host/v1`). All planning, grants, budgets, validation, and
 * approval stay in PF core — this plugin only submits curated requests and,
 * after an explicit operator confirmation, lands results into the workspace.
 *
 * Transport: host CLI JSON by default (`product-factory host …`). Set
 * `PRODUCT_FACTORY_REMOTE_URL` to use the HTTP RemotePfClient against a private
 * host (`/api/v1/...`) — never falls back to CLI when remote is configured.
 */

import type { Plugin, ToolDefinition } from "@opencode-ai/plugin";
import { tool } from "@opencode-ai/plugin";

import { applyAgentGuidance, type MutableConfig } from "./agent-guidance.js";
import { createPfClient } from "./pf-client.js";
import { createPfTools, type PfToolDeps, type ToolHelper } from "./tools.js";

export * from "./pf-client.js";
export * from "./tools.js";
export { PF_AGENT_GUIDANCE, applyAgentGuidance } from "./agent-guidance.js";

export const ProductFactoryPlugin: Plugin = async ({ directory, worktree }) => {
  const client = createPfClient({ directory: worktree ?? directory });
  const deps: PfToolDeps = { client };
  const tools = createPfTools(tool as unknown as ToolHelper, deps);

  return {
    tool: tools as Record<string, ToolDefinition>,
    config: async (config: MutableConfig) => {
      applyAgentGuidance(config);
    },
  };
};

export default ProductFactoryPlugin;
