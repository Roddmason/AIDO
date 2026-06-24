import type {
  CreateProjectInput,
  OverviewSummary,
  ProjectRecord,
  ProjectTemplateRecord,
} from "./types.js";

const WRITE_ORIGIN = "http://127.0.0.1";

export interface AidoClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
}

export class AidoError extends Error {}

export class AidoClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private cachedToken: string | null = null;

  constructor(options: AidoClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async listProjects(signal?: AbortSignal): Promise<ProjectRecord[]> {
    const data = await this.getJson<{ projects: ProjectRecord[] }>("/api/v1/projects", signal);
    return data.projects ?? [];
  }

  async listTemplates(signal?: AbortSignal): Promise<ProjectTemplateRecord[]> {
    const data = await this.getJson<{ projectTemplates: ProjectTemplateRecord[] }>(
      "/api/v1/project-templates",
      signal,
    );
    return data.projectTemplates ?? [];
  }

  async getOverview(signal?: AbortSignal): Promise<OverviewSummary> {
    const raw = await this.getJson<unknown>("/api/v1/overview", signal);
    return summarizeOverview(raw);
  }

  async createProject(input: CreateProjectInput, signal?: AbortSignal): Promise<ProjectRecord> {
    const body = JSON.stringify({ createDirectory: true, ...input });
    let response = await this.postProjects(body, await this.token(signal), signal);
    if (response.status === 403) {
      this.cachedToken = null;
      response = await this.postProjects(body, await this.token(signal), signal);
    }
    if (!response.ok) {
      const suffix =
        response.status === 403
          ? " (acceso denegado tras refrescar el token)"
          : ` ${await safeText(response)}`;
      throw new AidoError(`AIDO create_project falló: HTTP ${response.status}${suffix}`.trim());
    }
    const data = (await response.json()) as { project: ProjectRecord };
    return data.project;
  }

  private async token(signal?: AbortSignal): Promise<string> {
    if (this.cachedToken) return this.cachedToken;
    const data = await this.getJson<{ token: string }>("/api/v1/security/handshake", signal);
    this.cachedToken = data.token;
    return data.token;
  }

  private postProjects(body: string, token: string, signal?: AbortSignal): Promise<Response> {
    return this.request("/api/v1/projects", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-local-control-token": token,
        // Enviado defensivamente: AIDO hoy valida solo el token; el Origin replica al cliente canónico.
        origin: WRITE_ORIGIN,
      },
      body,
      signal,
    });
  }

  private async getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await this.request(path, { method: "GET", signal });
    if (!response.ok) {
      throw new AidoError(`AIDO request falló: HTTP ${response.status} en ${path}`);
    }
    return (await response.json()) as T;
  }

  private async request(path: string, init: RequestInit): Promise<Response> {
    try {
      return await this.fetchImpl(`${this.baseUrl}${path}`, init);
    } catch (cause) {
      throw new AidoError(`AIDO no responde en ${this.baseUrl} (${(cause as Error).message})`);
    }
  }
}

export function summarizeOverview(raw: unknown): OverviewSummary {
  const data = (raw ?? {}) as Record<string, unknown>;
  const projects = Array.isArray(data.projects) ? (data.projects as ProjectRecord[]) : [];
  const security = (data.security as OverviewSummary["security"]) ?? {
    loopbackOnly: false,
    writeTokenRequired: false,
  };
  return {
    security,
    projects: projects.map((p) => ({
      id: p.id,
      name: p.name,
      path: p.path,
      status: p.status,
      templateId: p.templateId,
    })),
    counts: {
      projects: projects.length,
      workflows: len(data.workflows),
      workflowRuns: len(data.workflowRuns),
      jobs: len(data.jobs),
      agentRuns: len(data.agentRuns),
      nextSteps: len(data.nextSteps),
      riskRegister: len(data.riskRegister),
    },
    nextSteps: Array.isArray(data.nextSteps) ? (data.nextSteps as unknown[]) : [],
    riskRegister: Array.isArray(data.riskRegister) ? (data.riskRegister as unknown[]) : [],
  };
}

function len(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}
