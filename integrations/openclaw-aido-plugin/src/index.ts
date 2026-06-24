import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { AidoClient } from "./aidoClient.js";

const DEFAULT_BASE_URL = "http://127.0.0.1:4310";

interface AidoConfig {
  baseUrl?: string;
}

// `config` llega tipado por el SDK desde configSchema; lo tratamos como unknown
// y leemos baseUrl defensivamente para no acoplarnos al genérico del SDK.
function client(config: unknown): AidoClient {
  const baseUrl = (config as AidoConfig | undefined)?.baseUrl ?? DEFAULT_BASE_URL;
  return new AidoClient({ baseUrl });
}

export default defineToolPlugin({
  id: "aido-control",
  name: "AIDO Control",
  description: "Inspect and control your AIDO Local Control Center projects.",
  configSchema: Type.Object({
    baseUrl: Type.Optional(
      Type.String({
        description: "AIDO base URL (loopback).",
        default: DEFAULT_BASE_URL,
      }),
    ),
  }),
  tools: (tool) => [
    tool({
      name: "aido_overview",
      label: "AIDO Overview",
      description:
        "Get a curated status summary of the AIDO control center: projects, counts, security posture and next steps.",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).getOverview(context?.signal),
    }),
    tool({
      name: "aido_list_projects",
      label: "AIDO List Projects",
      description: "List all projects registered in AIDO.",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).listProjects(context?.signal),
    }),
    tool({
      name: "aido_list_templates",
      label: "AIDO List Templates",
      description: "List available AIDO project templates (use the id as templateId when creating).",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).listTemplates(context?.signal),
    }),
    tool({
      name: "aido_create_project",
      label: "AIDO Create Project",
      description:
        "Create a new project in AIDO. Provide either a path, or a name together with workspaceBasePath.",
      optional: true,
      parameters: Type.Object({
        name: Type.Optional(Type.String()),
        path: Type.Optional(Type.String()),
        workspaceBasePath: Type.Optional(Type.String()),
        projectDirectoryName: Type.Optional(Type.String()),
        templateId: Type.Optional(Type.String()),
        createDirectory: Type.Optional(Type.Boolean({ default: true })),
        metadata: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
      }),
      execute: async (params, config, context) =>
        client(config).createProject(params, context?.signal),
    }),
  ],
});
