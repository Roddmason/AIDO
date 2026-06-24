import { describe, expect, it } from "vitest";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import entry from "../src/index.js";

describe("aido-control plugin", () => {
  it("declares the four AIDO tools in order", () => {
    const names = getToolPluginMetadata(entry)?.tools.map((tool) => tool.name);
    expect(names).toEqual([
      "aido_overview",
      "aido_list_projects",
      "aido_list_templates",
      "aido_create_project",
    ]);
  });
});
