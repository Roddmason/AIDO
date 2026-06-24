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
