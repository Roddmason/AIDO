import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { AidoClient } from "./aidoClient.js";

const DEFAULT_BASE_URL = "http://127.0.0.1:4310";

const OWNER_TYPE = Type.Union([
  Type.Literal("workspace"),
  Type.Literal("loop"),
  Type.Literal("story"),
  Type.Literal("agent_task"),
  Type.Literal("review"),
]);

interface AidoConfig {
  baseUrl?: string;
}

// `config` llega tipado por el SDK desde configSchema; lo tratamos como unknown
// y leemos baseUrl defensivamente para no acoplarnos al genérico del SDK.
//
// Se cachea un AidoClient por baseUrl a nivel de módulo: cada tool `execute` corre en
// una invocación nueva, y sin este cache `cachedToken` (el handshake de escritura) se
// perdía en cada llamada, forzando un handshake extra por cada escritura.
const clients = new Map<string, AidoClient>();

export function client(config: unknown): AidoClient {
  const baseUrl = (config as AidoConfig | undefined)?.baseUrl ?? DEFAULT_BASE_URL;
  let instance = clients.get(baseUrl);
  if (!instance) {
    instance = new AidoClient({ baseUrl });
    clients.set(baseUrl, instance);
  }
  return instance;
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
  }, { additionalProperties: false }),
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
        "Create a new project in AIDO. Provide either a path, or a name together with workspaceBasePath. " +
        "This starts an async AIDO job and waits (polling) until it finishes. " +
        "Acción de escritura: confirma con el usuario antes de ejecutarla salvo que la haya pedido explícitamente.",
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
    tool({
      name: "aido_list_threads",
      label: "AIDO List Threads",
      description:
        "List AIDO threads, optionally filtered by projectId and whether to include archived threads.",
      parameters: Type.Object({
        projectId: Type.Optional(Type.String({ description: "Only list threads for this project." })),
        includeArchived: Type.Optional(
          Type.Boolean({ description: "Include archived threads. Defaults to false.", default: false }),
        ),
      }),
      execute: async (params, config, context) =>
        client(config).listThreads(params, context?.signal),
    }),
    tool({
      name: "aido_create_thread",
      label: "AIDO Create Thread",
      description:
        "Create a new AIDO thread under a project. ownerType/ownerId default to a workspace thread " +
        "owned by the project itself, matching the AIDO UI default. " +
        "Acción de escritura: confirma con el usuario antes de ejecutarla salvo que la haya pedido explícitamente.",
      parameters: Type.Object({
        projectId: Type.String(),
        title: Type.String(),
        ownerType: Type.Optional(OWNER_TYPE),
        ownerId: Type.Optional(Type.String({ description: "Defaults to projectId when omitted." })),
      }),
      execute: async (params, config, context) =>
        client(config).createThread(params, context?.signal),
    }),
    tool({
      name: "aido_send_message",
      label: "AIDO Send Message",
      description:
        "Send a message to an AIDO thread, starting an agent run on it. Fails with a clear error if the " +
        "thread already has a run queued or in progress. " +
        "Acción de escritura: confirma con el usuario antes de ejecutarla salvo que la haya pedido explícitamente.",
      parameters: Type.Object({
        threadId: Type.String(),
        content: Type.String(),
      }),
      execute: async (params, config, context) =>
        client(config).sendMessage(params.threadId, params.content, context?.signal),
    }),
    tool({
      name: "aido_thread_status",
      label: "AIDO Thread Status",
      description:
        "Get a compact status summary for an AIDO thread: id, title, status, whether a run is currently " +
        "running, and only the last N timeline events (not the full timeline).",
      parameters: Type.Object({
        threadId: Type.String(),
        lastEventsLimit: Type.Optional(
          Type.Integer({ minimum: 1, default: 10, description: "How many recent events to include. Defaults to 10." }),
        ),
      }),
      execute: async (params, config, context) =>
        client(config).getThreadStatus(params.threadId, params.lastEventsLimit ?? 10, context?.signal),
    }),
    tool({
      name: "aido_execution_status",
      label: "AIDO Execution Status",
      description:
        "Get the raw status of an AIDO async execution (job) by its executionId, including its result once terminal.",
      parameters: Type.Object({
        executionId: Type.String(),
      }),
      execute: async (params, config, context) =>
        client(config).getExecutionStatus(params.executionId, context?.signal),
    }),
    tool({
      name: "aido_worker_status",
      label: "AIDO Worker Status",
      description: "Get the current status of the AIDO background worker.",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).getWorkerStatus(context?.signal),
    }),
    tool({
      name: "aido_worker_resume",
      label: "AIDO Worker Resume",
      description:
        "Resume the AIDO background worker. The worker starts paused, so nothing progresses (threads, " +
        "executions) until it is resumed. " +
        "Acción de escritura: confirma con el usuario antes de ejecutarla salvo que la haya pedido explícitamente.",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).resumeWorker(context?.signal),
    }),
  ],
});
