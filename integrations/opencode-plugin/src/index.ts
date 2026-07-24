/**
 * @product-factory/opencode-plugin
 *
 * Thin OpenCode adapter over the Product Factory host protocol
 * (`product-factory.host/v1`). All planning, grants, budgets, validation, and
 * approval stay in PF core — this plugin only submits curated requests and,
 * after an explicit operator confirmation, lands results into the workspace.
 *
 * Transport: host CLI JSON (`product-factory host … `). MCP remains available
 * for raw OpenCode `mcp` config users (see README); the plugin prefers the CLI
 * to avoid running a second MCP client inside OpenCode.
 */

import type { Plugin, ToolDefinition } from "@opencode-ai/plugin";
import { tool } from "@opencode-ai/plugin";

import { applyAgentGuidance, type MutableConfig } from "./agent-guidance.js";
import { CliPfClient, type CliPfClientOptions } from "./pf-client.js";
import { createPfTools, type PfToolDeps, type ToolHelper } from "./tools.js";

export * from "./pf-client.js";
export * from "./tools.js";
export { PF_AGENT_GUIDANCE, applyAgentGuidance } from "./agent-guidance.js";

function truthy(value: string | undefined): boolean {
  return value === "1" || value === "true" || value === "yes";
}

function clientOptionsFromEnv(directory: string | undefined): CliPfClientOptions {
  const env = process.env;
  const bin = env.PRODUCT_FACTORY_BIN;
  return {
    bin: bin || "product-factory",
    cwd: directory,
    mock: truthy(env.PRODUCT_FACTORY_FORCE_MOCK),
  };
}

export const ProductFactoryPlugin: Plugin = async ({ directory, worktree }) => {
  const client = new CliPfClient(clientOptionsFromEnv(worktree ?? directory));
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
