import type {
  CreateProjectInput,
  CreateThreadInput,
  ExecutionRecord,
  ExecutionSubmission,
  ListThreadsParams,
  OverviewSummary,
  ProjectRecord,
  ProjectTemplateRecord,
  ThreadDetailResponse,
  ThreadEventsPage,
  ThreadStatusSummary,
  ThreadSummaryRecord,
} from "./types.js";

const WRITE_ORIGIN = "http://127.0.0.1";

const DEFAULT_POLL_INTERVAL_MS = 1000;
const DEFAULT_POLL_TIMEOUT_MS = 60_000;

// Statuses terminales de /api/v1/executions/{id}; cualquier otro valor (p.ej.
// "queued"/"running") significa que hay que seguir haciendo polling.
const TERMINAL_EXECUTION_STATUSES = new Set(["completed", "failed", "blocked", "cancelled", "interrupted"]);

export interface AidoClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  /** Intervalo entre reintentos de polling de ejecuciones (ms). Default 1000. */
  pollIntervalMs?: number;
  /** Tiempo máximo total de polling antes de abortar (ms). Default 60000. */
  pollTimeoutMs?: number;
  /** Inyectable en tests para no esperar tiempo real entre reintentos de polling. */
  sleepImpl?: (ms: number) => Promise<void>;
}

export class AidoError extends Error {}

export class AidoClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly pollIntervalMs: number;
  private readonly pollTimeoutMs: number;
  private readonly sleepImpl: (ms: number) => Promise<void>;
  private cachedToken: string | null = null;

  constructor(options: AidoClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    this.pollTimeoutMs = options.pollTimeoutMs ?? DEFAULT_POLL_TIMEOUT_MS;
    this.sleepImpl = options.sleepImpl ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
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

  /**
   * Crea un proyecto. AIDO responde 202 con { executionId, jobId, operation, status }
   * (operación asíncrona); se hace polling a /api/v1/executions/{executionId} hasta
   * un status terminal y se extrae `result.project`.
   */
  async createProject(input: CreateProjectInput, signal?: AbortSignal): Promise<ProjectRecord> {
    const body = JSON.stringify({ createDirectory: true, ...input });
    const response = await this.authorizedRequest(
      "/api/v1/projects",
      { method: "POST", headers: { "content-type": "application/json" }, body },
      signal,
    );
    if (response.status !== 202) {
      throw new AidoError(`AIDO create_project falló: HTTP ${response.status} ${await safeText(response)}`.trim());
    }
    const submission = await parseJson<ExecutionSubmission>(response, "/api/v1/projects");
    const execution = await this.pollExecution(submission.executionId, signal);
    if (execution.status !== "completed") {
      const suffix = execution.resultStatusCode ? ` (resultStatusCode=${execution.resultStatusCode})` : "";
      throw new AidoError(
        `AIDO create_project terminó con status "${execution.status}"${suffix} (executionId=${submission.executionId})`,
      );
    }
    const project = execution.result?.project;
    if (!project) {
      throw new AidoError(
        `AIDO create_project completó sin "project" en el resultado (executionId=${submission.executionId})`,
      );
    }
    return project;
  }

  async getExecutionStatus(executionId: string, signal?: AbortSignal): Promise<ExecutionRecord> {
    return this.getJson<ExecutionRecord>(`/api/v1/executions/${encodeURIComponent(executionId)}`, signal);
  }

  async listThreads(params: ListThreadsParams = {}, signal?: AbortSignal): Promise<ThreadSummaryRecord[]> {
    const query = buildQuery({ projectId: params.projectId, includeArchived: params.includeArchived });
    const data = await this.getJson<{ threads: ThreadSummaryRecord[] }>(`/api/v1/threads${query}`, signal);
    return data.threads ?? [];
  }

  /**
   * ownerType/ownerId por defecto replican el default de la UI de AIDO: workspace + projectId.
   * AIDO responde con el envelope ThreadDetailResponse (thread_detail()), no un ThreadRecord
   * plano, así que hay que desenvolver `.thread`.
   */
  async createThread(input: CreateThreadInput, signal?: AbortSignal): Promise<ThreadSummaryRecord> {
    const ownerType = input.ownerType ?? "workspace";
    const ownerId = input.ownerId ?? input.projectId;
    const body = JSON.stringify({ projectId: input.projectId, ownerType, ownerId, title: input.title });
    const response = await this.authorizedRequest(
      "/api/v1/threads",
      { method: "POST", headers: { "content-type": "application/json" }, body },
      signal,
    );
    if (!response.ok) {
      throw new AidoError(`AIDO create_thread falló: HTTP ${response.status} ${await safeText(response)}`.trim());
    }
    const detail = await parseJson<ThreadDetailResponse>(response, "/api/v1/threads");
    return detail.thread;
  }

  /** Devuelve el envelope completo de AIDO: { thread, messages, artifacts, decisions, events }. */
  async getThreadDetail(threadId: string, signal?: AbortSignal): Promise<ThreadDetailResponse> {
    return this.getJson<ThreadDetailResponse>(`/api/v1/threads/${encodeURIComponent(threadId)}`, signal);
  }

  async getThreadEvents(
    threadId: string,
    params: { afterSeq?: number; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<ThreadEventsPage> {
    const query = buildQuery({ afterSeq: params.afterSeq, limit: params.limit });
    return this.getJson<ThreadEventsPage>(`/api/v1/threads/${encodeURIComponent(threadId)}/events${query}`, signal);
  }

  /**
   * Resumen compacto para el LLM: no expone la timeline completa, solo los últimos N eventos.
   *
   * Fuentes (verificadas contra local_control_center/threads):
   * - `id`/`title` salen de `detail.thread` (GET /threads/{id} devuelve el envelope
   *   ThreadDetailResponse, no un ThreadRecord plano).
   * - `lastEvents` sale de `detail.events`, que ya es tail-safe (list_events limit=500,
   *   ORDER BY sequence DESC + reversed) — son los últimos eventos reales.
   * - `status`/`running` salen de GET /threads/{id}/events porque esa es la única llamada
   *   que trae `running` precalculado; su `events` NO se usa acá porque esa ventana es
   *   ascendente desde `afterSeq` (default 0) con tope 300, es decir trae los eventos
   *   MÁS VIEJOS, no los últimos.
   */
  async getThreadStatus(threadId: string, lastEventsLimit = 10, signal?: AbortSignal): Promise<ThreadStatusSummary> {
    const [detail, eventsPage] = await Promise.all([
      this.getThreadDetail(threadId, signal),
      this.getThreadEvents(threadId, {}, signal),
    ]);
    const safeLimit = Math.max(0, lastEventsLimit);
    return {
      id: detail.thread.id,
      title: detail.thread.title,
      status: eventsPage.threadStatus,
      running: eventsPage.running,
      lastEvents: safeLimit === 0 ? [] : detail.events.slice(-safeLimit),
    };
  }

  /** Inicia un run en el hilo. AIDO responde 409 si ya hay un run en cola o corriendo. */
  async sendMessage(threadId: string, content: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    const response = await this.authorizedRequest(
      `/api/v1/threads/${encodeURIComponent(threadId)}/messages`,
      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ content }) },
      signal,
    );
    if (response.status === 409) {
      throw new AidoError(`AIDO thread ${threadId} ya tiene un run en cola o corriendo (409)`);
    }
    if (!response.ok) {
      throw new AidoError(`AIDO send_message falló: HTTP ${response.status} ${await safeText(response)}`.trim());
    }
    return parseJsonOrEmpty(response);
  }

  async getWorkerStatus(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return this.getJson<Record<string, unknown>>("/api/v1/workers/status", signal);
  }

  async resumeWorker(signal?: AbortSignal): Promise<Record<string, unknown>> {
    const response = await this.authorizedRequest("/api/v1/workers/resume", { method: "POST" }, signal);
    if (!response.ok) {
      throw new AidoError(`AIDO worker_resume falló: HTTP ${response.status} ${await safeText(response)}`.trim());
    }
    return parseJsonOrEmpty(response);
  }

  private async pollExecution(executionId: string, signal?: AbortSignal): Promise<ExecutionRecord> {
    const maxAttempts = Math.max(1, Math.ceil(this.pollTimeoutMs / this.pollIntervalMs));
    let last: ExecutionRecord | undefined;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      last = await this.getExecutionStatus(executionId, signal);
      if (TERMINAL_EXECUTION_STATUSES.has(last.status)) return last;
      if (attempt < maxAttempts - 1) await this.sleepImpl(this.pollIntervalMs);
    }
    throw new AidoError(
      `AIDO execution ${executionId} no terminó dentro de ${this.pollTimeoutMs}ms (último status: ${last?.status ?? "desconocido"})`,
    );
  }

  private async token(signal?: AbortSignal): Promise<string> {
    if (this.cachedToken) return this.cachedToken;
    const data = await this.getJson<{ token: string }>("/api/v1/security/handshake", signal);
    this.cachedToken = data.token;
    return data.token;
  }

  /**
   * Envoltorio genérico para escrituras: agrega el token de control local y, si
   * AIDO responde 403 (token vencido por reinicio), lo refresca y reintenta UNA vez.
   */
  private async authorizedRequest(
    path: string,
    init: { method: string; headers?: Record<string, string>; body?: string },
    signal?: AbortSignal,
  ): Promise<Response> {
    const baseHeaders = init.headers ?? {};
    let token = await this.token(signal);
    let response = await this.request(path, {
      ...init,
      headers: { ...baseHeaders, "x-local-control-token": token, origin: WRITE_ORIGIN },
      signal,
    });
    if (response.status === 403) {
      this.cachedToken = null;
      token = await this.token(signal);
      response = await this.request(path, {
        ...init,
        headers: { ...baseHeaders, "x-local-control-token": token, origin: WRITE_ORIGIN },
        signal,
      });
    }
    return response;
  }

  private async getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await this.request(path, { method: "GET", signal });
    if (!response.ok) {
      throw new AidoError(`AIDO request falló: HTTP ${response.status} en ${path}`);
    }
    return parseJson<T>(response, path);
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

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) usp.set(key, String(value));
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

async function parseJsonOrEmpty(response: Response): Promise<Record<string, unknown>> {
  const text = await safeText(response);
  if (!text) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** Parsea el body como JSON o lanza un AidoError legible en vez de propagar un SyntaxError crudo. */
async function parseJson<T>(response: Response, context: string): Promise<T> {
  const text = await safeText(response);
  try {
    return JSON.parse(text) as T;
  } catch (cause) {
    throw new AidoError(`AIDO devolvió un body inválido en ${context}: ${(cause as Error).message}`);
  }
}
