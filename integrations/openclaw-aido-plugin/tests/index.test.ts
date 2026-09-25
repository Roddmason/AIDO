import { describe, expect, it } from "vitest";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import entry, { client } from "../src/index.js";

const EXPECTED_TOOL_ORDER = [
  "aido_overview",
  "aido_list_projects",
  "aido_list_templates",
  "aido_create_project",
  "aido_list_threads",
  "aido_create_thread",
  "aido_send_message",
  "aido_thread_status",
  "aido_execution_status",
  "aido_worker_status",
  "aido_worker_resume",
];

// Ya no son `optional`: con tools.profile "coding", OpenClaw elimina las tools
// opcionales del plugin, y tools.allow actúa como allowlist exclusiva (intersección),
// pudiendo dejar al agente sin ninguna tool callable. El opt-in de escritura pasa a ser
// comportamiento del agente (confirmar antes de ejecutar) + approvals/token de AIDO.
const WRITE_TOOLS = [
  "aido_create_project",
  "aido_create_thread",
  "aido_send_message",
  "aido_worker_resume",
];

describe("aido-control plugin", () => {
  it("declares the eleven AIDO tools in order", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    expect(tools.map((tool) => tool.name)).toEqual(EXPECTED_TOOL_ORDER);
  });

  it("does not mark any tool as optional (write tools must survive the coding tools.profile)", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    const optionalNames = tools.filter((tool) => tool.optional).map((tool) => tool.name);
    expect(optionalNames).toEqual([]);
  });

  it("reminds the agent to confirm with the user before running each write tool", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    for (const name of WRITE_TOOLS) {
      const tool = tools.find((t) => t.name === name);
      expect(tool?.description).toContain("Acción de escritura");
    }
  });

  it("gives every tool a non-empty, LLM-useful description", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    for (const tool of tools) {
      expect(tool.description.length).toBeGreaterThan(10);
    }
  });

  it("requires projectId and title on aido_create_thread", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    const createThread = tools.find((tool) => tool.name === "aido_create_thread");
    expect(createThread?.parameters.required).toEqual(expect.arrayContaining(["projectId", "title"]));
  });

  it("requires threadId and content on aido_send_message", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    const sendMessage = tools.find((tool) => tool.name === "aido_send_message");
    expect(sendMessage?.parameters.required).toEqual(expect.arrayContaining(["threadId", "content"]));
  });

  it("requires threadId on aido_thread_status", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    const threadStatus = tools.find((tool) => tool.name === "aido_thread_status");
    expect(threadStatus?.parameters.required).toEqual(["threadId"]);
  });

  it("requires executionId on aido_execution_status", () => {
    const tools = getToolPluginMetadata(entry)?.tools ?? [];
    const executionStatus = tools.find((tool) => tool.name === "aido_execution_status");
    expect(executionStatus?.parameters.required).toEqual(["executionId"]);
  });
});

describe("client() caching", () => {
  it("returns the same AidoClient instance for the same baseUrl, so the token cache survives across tool calls", () => {
    const a = client({ baseUrl: "http://127.0.0.1:4310" });
    const b = client({ baseUrl: "http://127.0.0.1:4310" });
    expect(a).toBe(b);
  });

  it("returns a different instance for a different baseUrl", () => {
    const a = client({ baseUrl: "http://127.0.0.1:4310" });
    const c = client({ baseUrl: "http://127.0.0.1:9999" });
    expect(a).not.toBe(c);
  });

  it("treats a missing config the same as the default loopback baseUrl", () => {
    const a = client(undefined);
    const b = client({ baseUrl: "http://127.0.0.1:4310" });
    expect(a).toBe(b);
  });
});
