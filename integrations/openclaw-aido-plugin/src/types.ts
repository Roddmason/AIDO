export interface ProjectRecord {
  id: string;
  name: string;
  path: string;
  templateId: string;
  source: string;
  status: string;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectTemplateRecord {
  id: string;
  name: string;
  kind: string;
}

export interface SecurityPosture {
  loopbackOnly: boolean;
  writeTokenRequired: boolean;
}

export interface CreateProjectInput {
  name?: string;
  path?: string;
  workspaceBasePath?: string;
  projectDirectoryName?: string;
  templateId?: string;
  createDirectory?: boolean;
  metadata?: Record<string, unknown>;
}

export interface OverviewSummary {
  security: SecurityPosture;
  projects: Array<Pick<ProjectRecord, "id" | "name" | "path" | "status" | "templateId">>;
  counts: Record<string, number>;
  nextSteps: unknown[];
  riskRegister: unknown[];
}

// `create_project` es asíncrono en AIDO: POST devuelve 202 con este envelope,
// y hay que hacer polling a /api/v1/executions/{executionId} hasta un status terminal.
// Verificado contra local_control_center/executions/models.py (ExecutionStatus/TERMINAL_STATUSES):
// solo cancelled/completed/failed/blocked/interrupted son terminales.
export type ExecutionStatus =
  | "queued"
  | "resource_wait"
  | "running"
  | "cancel_requested"
  | "cancelled"
  | "completed"
  | "failed"
  | "blocked"
  | "interrupted";

export interface ExecutionSubmission {
  executionId: string;
  jobId: string;
  operation: string;
  status: ExecutionStatus;
}

export interface ExecutionRecord extends ExecutionSubmission {
  result?: { project?: ProjectRecord } & Record<string, unknown>;
  resultStatusCode?: number;
}

export type ThreadOwnerType = "workspace" | "loop" | "story" | "agent_task" | "review";

export interface ThreadSummaryRecord {
  id: string;
  projectId: string;
  ownerType: ThreadOwnerType;
  ownerId: string;
  title: string;
  status: string;
  createdAt: string;
  updatedAt: string;
}

// Verificado contra ThreadAgentEventRecord (local_control_center/threads/contracts.py):
// el campo de secuencia se llama `sequence` (sin alias), no `seq`. El resto de campos
// (type, agentRole, payload, metadata, createdAt, ...) se preserva sin tipar.
export interface ThreadEvent extends Record<string, unknown> {
  sequence: number;
}

// GET /api/v1/threads/{id} y POST /api/v1/threads devuelven ThreadDetailResponse
// (thread_detail() en threads/api.py), NO un ThreadRecord plano. `events` aquí viene
// de list_events(limit=500): tail-safe (ORDER BY sequence DESC + reversed), o sea ya
// son los últimos hasta 500 eventos en orden ascendente.
export interface ThreadDetailResponse {
  thread: ThreadSummaryRecord;
  messages: unknown[];
  artifacts: unknown[];
  decisions: unknown[];
  events: ThreadEvent[];
}

// GET /api/v1/threads/{id}/events (afterSeq/limit): ventana ASC desde el cursor,
// tope 300. Con afterSeq=0 (default) trae los eventos MÁS VIEJOS, no los últimos;
// por eso no se usa como fuente de `lastEvents` en getThreadStatus (ver aidoClient.ts).
export interface ThreadEventsPage {
  events: ThreadEvent[];
  threadStatus: string;
  running: boolean;
}

export interface ListThreadsParams {
  projectId?: string;
  includeArchived?: boolean;
}

export interface CreateThreadInput {
  projectId: string;
  title: string;
  ownerType?: ThreadOwnerType;
  ownerId?: string;
}

export interface ThreadStatusSummary {
  id: string;
  title: string;
  status: string;
  running: boolean;
  lastEvents: ThreadEvent[];
}
