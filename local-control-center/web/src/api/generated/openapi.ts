// Generated from FastAPI OpenAPI. Do not edit by hand.
// No network access is required; run `corepack pnpm@10.24.0 run openapi:generate`.

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export type AgentProfileResponse = { "agentProfile": JsonObject };
export type AgentProfileUpsertRequest = { "allowedSkills"?: Array<string>; "allowedTools"?: Array<string>; "id": string; "maxCostPerRun"?: number; "maxRuntimeSeconds"?: number; "memoryScope"?: string; "modelPolicyId"?: JsonValue | string; "name"?: JsonValue | string; "outputSchema"?: JsonObject; "permissionProfile"?: "plan" | "dev_safe" | "qa" | "release"; "qualityGates"?: Array<never>; "role"?: "product_owner" | "technical_lead" | "implementer" | "qa_reviewer" | "security_reviewer"; "runtimeMode"?: "api" | "cli" | "ollama" | "hybrid" | "manual" | "internal_mock"; "runtimeType"?: "api" | "cli" | "ollama" | "hybrid" | "manual" | "internal_mock" | JsonValue; "status"?: "active" | "disabled" };
export type AgentProfilesListResponse = { "agentProfiles": Array<JsonObject> };
export type AgentRunCreateRequest = { "agentProfileId": string; "input"?: JsonObject; "jobId"?: JsonValue | string; "projectId": string; "taskId"?: string; "workflowRunId"?: JsonValue | string; "workflowStepId"?: JsonValue | string };
export type AgentRunResponse = { "agentRun": JsonObject };
export type AgentRunsListResponse = { "agentRuns": Array<JsonObject> };
export type AgentsListResponse = { "agents": Array<JsonObject> };
export type ApprovalReasonRequest = { "reason": string };
export type ApprovalsListResponse = { "actionRequests": Array<JsonObject> };
export type ArchitectureDecisionCreateRequest = { "consequences"?: Array<never> | string; "context"?: string; "decision"?: string; "linkedRiskIds"?: Array<string>; "metadata"?: JsonObject; "nextStepIds"?: Array<string>; "projectId": string; "status"?: "proposed" | "accepted" | "rejected" | "superseded" | "deprecated"; "title": string };
export type ArchitectureDecisionResponse = { "architectureDecision": JsonObject };
export type ArchitectureDecisionsListResponse = { "architectureDecisions": Array<JsonObject> };
export type ArtifactCleanupRequest = { "dryRun"?: boolean };
export type ArtifactCleanupResponse = { "artifactRoot": string; "deletedFiles": Array<JsonObject>; "dryRun": boolean; "keptReferencedFiles": number; "orphanFiles": Array<JsonObject> };
export type ArtifactIngestRequest = { "content"?: JsonValue | string; "contentBase64"?: JsonValue | string; "kind": "execution_log" | "screenshot" | "test_report" | "qa_report" | "generic_artifact"; "mimeType"?: JsonValue | string; "name"?: JsonValue | string };
export type ArtifactResponse = { "artifact": JsonObject };
export type ArtifactRetentionActionRequest = { "action": string; "artifactIds": Array<string>; "now"?: JsonValue | string; "reason": string };
export type ArtifactRetentionActionResponse = { "action": string; "artifacts": Array<JsonObject> };
export type ArtifactRetentionPlanRequest = { "dryRun"?: boolean; "now"?: JsonValue | string };
export type ArtifactRetentionPlanResponse = { "dryRun": boolean; "expiredArtifacts": Array<JsonObject>; "now": string; "riskIds": Array<string> };
export type ChatCreateRequest = { "projectId": string; "prompt": string; "sessionId"?: JsonValue | string; "title"?: JsonValue | string };
export type ChatResponse = { "chat": JsonObject };
export type ChatsListResponse = { "chats": Array<JsonObject> };
export type EmptyObjectRequest = JsonObject;
export type EvidenceCreateRequest = { "acceptanceChecklist"?: Array<never>; "agentId"?: JsonValue | string; "diffRefs"?: Array<never>; "logs"?: Array<never>; "projectId": string; "qaVerdict"?: string; "riskNotes"?: Array<never>; "screenshotRefs"?: Array<never>; "taskId"?: string; "testPlan"?: string; "testResultReports"?: Array<JsonObject>; "testResults"?: Array<JsonObject>; "workflowRunId"?: JsonValue | string };
export type EvidenceDetailResponse = { "artifacts": Array<JsonObject>; "evidencePackage": JsonObject; "testResultRecords": Array<JsonObject> };
export type EvidenceListResponse = { "evidencePackages": Array<JsonObject> };
export type EvidencePackageResponse = { "evidencePackage": JsonObject };
export type GovernanceResponse = { "architectureDecisions": Array<JsonObject>; "nextSteps": Array<JsonObject>; "risks": Array<JsonObject> };
export type HTTPValidationError = { "detail"?: Array<ValidationError> };
export type HandshakeResponse = { "loopbackOnly": boolean; "token": string };
export type HealthResponse = { "ok": boolean };
export type IdeConnectionResponse = { "ideConnection": JsonObject };
export type IdeConnectionUpsertRequest = { "diagnostics"?: Array<never>; "editor"?: string; "openFiles"?: Array<never>; "projectId": string; "selection"?: JsonObject; "status"?: string; "terminalContext"?: JsonObject; "workspaceRoot"?: JsonValue | string };
export type IdeConnectionsListResponse = { "ideConnections": Array<JsonObject> };
export type IntegrationsListResponse = { "integrations": Array<JsonObject>; "mcpServers": Array<JsonObject>; "optionalAdapters": JsonObject };
export type JobCreateRequest = { "idempotencyKey"?: JsonValue | string; "kind": string; "payload"?: JsonObject; "projectId": string; "workflowRunId"?: JsonValue | string; "workflowStepId"?: JsonValue | string };
export type JobMutationResponse = { "actionRequest"?: JsonObject | JsonValue; "actionRequests"?: Array<JsonObject>; "auditEvent"?: JsonObject | JsonValue; "events"?: Array<JsonObject>; "job": JsonObject; "permissionGrant"?: JsonObject | JsonValue };
export type JobsListResponse = { "events"?: Array<JsonObject>; "jobs": Array<JsonObject> };
export type McpServerRegisterRequest = { "command": string; "id": string; "metadata"?: JsonObject; "transport"?: "stdio" };
export type McpServerResponse = { "mcpServer": JsonObject };
export type MemoryCreateRequest = { "content": string; "kind"?: string; "metadata"?: JsonObject; "projectId": string; "scope"?: string; "scopeId"?: JsonValue | string; "sourceRef"?: string };
export type MemoryListResponse = { "memoryItems": Array<JsonObject> };
export type MemoryResponse = { "memoryItem": JsonObject };
export type ModelPoliciesListResponse = { "modelPolicies": Array<JsonObject> };
export type ModelPolicyResponse = { "modelPolicy": JsonObject };
export type ModelPolicyUpsertRequest = { "allowLocal"?: boolean; "allowRemote"?: boolean; "fallback"?: Array<ModelProviderCandidate>; "id": string; "maxCostUsd"?: number; "maxTokens"?: number; "name"?: JsonValue | string; "preferred"?: Array<ModelProviderCandidate>; "status"?: "active" | "disabled"; "temperature"?: number };
export type ModelProviderCandidate = { "model": string; "provider": string };
export type ModelProvidersListResponse = { "modelProviders": Array<JsonObject> };
export type NextStepCreateRequest = { "dueAt"?: JsonValue | string; "metadata"?: JsonObject; "owner"?: string; "priority"?: "low" | "medium" | "high" | "urgent"; "projectId": string; "sourceDecisionId"?: JsonValue | string; "sourceRiskId"?: JsonValue | string; "status"?: "planned" | "in_progress" | "blocked" | "completed" | "cancelled"; "title": string };
export type NextStepResponse = { "nextStep": JsonObject };
export type NextStepUpdateRequest = { "dueAt"?: JsonValue | string; "metadata"?: JsonObject | JsonValue; "owner"?: JsonValue | string; "priority"?: "low" | "medium" | "high" | "urgent" | JsonValue; "status"?: "planned" | "in_progress" | "blocked" | "completed" | "cancelled" | JsonValue };
export type NextStepsListResponse = { "nextSteps": Array<JsonObject> };
export type OpenDesignResponse = { "backend": string; "runtime": string; "status": string };
export type OptionalReasonRequest = { "reason"?: string };
export type OverviewResponse = { "actionRequests": Array<JsonObject>; "agentProfiles": Array<JsonObject>; "agentRuns": Array<JsonObject>; "agentToolCalls": Array<JsonObject>; "agents": Array<JsonObject>; "architectureDecisions": Array<JsonObject>; "artifacts": Array<JsonObject>; "auditEvents": Array<JsonObject>; "chats": Array<JsonObject>; "costUsage": Array<JsonObject>; "events": Array<JsonObject>; "evidencePackages": Array<JsonObject>; "ideConnections": Array<JsonObject>; "jobRuns": Array<JsonObject>; "jobs": Array<JsonObject>; "mcpServers": Array<JsonObject>; "memoryItems": Array<JsonObject>; "modelCalls": Array<JsonObject>; "modelPolicies": Array<JsonObject>; "modelProviders": Array<JsonObject>; "nextSteps": Array<JsonObject>; "openDesign": JsonObject; "permissionDecisions": Array<JsonObject>; "permissionGrants": Array<JsonObject>; "pipelines": Array<JsonObject>; "policyRevisions": Array<JsonObject>; "projectTemplates": Array<JsonObject>; "projects": Array<JsonObject>; "promptTemplates": Array<JsonObject>; "providers": Array<JsonObject>; "riskRegister": Array<JsonObject>; "runtimeWorkspaces": Array<JsonObject>; "sandboxProfiles": Array<JsonObject>; "security": JsonObject; "sessions": Array<JsonObject>; "skills": Array<JsonObject>; "teams": Array<JsonObject>; "testResultRecords": Array<JsonObject>; "workflowRuns": Array<JsonObject>; "workflowSteps": Array<JsonObject>; "workflows": Array<JsonObject> };
export type PermissionGrantResponse = { "permissionGrant": JsonObject };
export type PipelineCreateRequest = { "chatId"?: JsonValue | string; "projectId": string; "sessionId"?: JsonValue | string; "stages"?: Array<JsonObject> | JsonValue; "title"?: JsonValue | string };
export type PipelineResponse = { "pipeline": JsonObject };
export type PipelinesListResponse = { "pipelines": Array<JsonObject> };
export type PoliciesListResponse = { "permissionDecisions": Array<JsonObject>; "permissionGrants": Array<JsonObject>; "policies": Array<JsonObject>; "policyRevisions": Array<JsonObject>; "sandboxProfiles": Array<JsonObject> };
export type PolicyEvaluateRequest = { "agentId"?: JsonValue | string; "command"?: JsonValue | string; "deploymentTarget"?: JsonValue | string; "environment"?: JsonValue | string; "gitOperation"?: JsonValue | string; "networkRequired"?: JsonValue | boolean; "operation"?: JsonValue | string; "path"?: JsonValue | string; "permissionProfile"?: JsonValue | string; "projectId"?: JsonValue | string; "riskLevel"?: JsonValue | string; "role"?: JsonValue | string; "secretsRequired"?: JsonValue | boolean; "tool"?: JsonValue | string; "workspaceId"?: JsonValue | string };
export type PolicyEvaluationResponse = { "decision": JsonObject };
export type ProjectCreateRequest = { "createDirectory"?: boolean; "metadata"?: JsonObject; "name"?: JsonValue | string; "path"?: JsonValue | string; "templateId"?: JsonValue | string };
export type ProjectResponse = { "auditEvent"?: JsonObject | JsonValue; "project": JsonObject };
export type ProjectTemplatesResponse = { "projectTemplates": Array<JsonObject> };
export type ProjectsListResponse = { "projects": Array<JsonObject> };
export type PromptResponse = { "promptTemplate": JsonObject };
export type PromptTemplatesListResponse = { "promptTemplates": Array<JsonObject> };
export type PromptUpsertRequest = { "appliesTo"?: JsonObject; "body": string; "id"?: JsonValue | string; "mode"?: string; "name": string; "optimizer"?: string; "projectId": string };
export type ProvidersListResponse = { "providers": Array<JsonObject> };
export type RequiredReasonRequest = { "reason": string };
export type RetrievalReindexResponse = { "index": JsonObject };
export type RetrievalSearchRequest = { "limit"?: number; "query"?: string };
export type RetrievalSearchResponse = { "results": Array<JsonObject> };
export type RetrievalStatusResponse = { "backend": string; "degraded": boolean; "dimensions": number; "faissAvailable": boolean; "indexDir": string; "indexed": number };
export type RiskCreateRequest = { "description"?: string; "evidenceRefs"?: Array<string>; "metadata"?: JsonObject; "mitigation"?: string; "owner"?: string; "projectId": string; "severity"?: "low" | "medium" | "high" | "critical"; "status"?: "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed"; "title": string };
export type RiskResponse = { "risk": JsonObject };
export type RiskUpdateRequest = { "evidenceRefs"?: Array<string> | JsonValue; "metadata"?: JsonObject | JsonValue; "mitigation"?: JsonValue | string; "owner"?: JsonValue | string; "severity"?: "low" | "medium" | "high" | "critical" | JsonValue; "status"?: "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed" | JsonValue };
export type RisksListResponse = { "risks": Array<JsonObject> };
export type RuntimeProvidersResponse = { "api": JsonObject; "cli": JsonObject; "ollama": JsonObject; "runtimeModes": Array<string> };
export type SandboxProfileMutationResponse = { "policyRevision"?: JsonObject | JsonValue; "sandboxProfile": JsonObject };
export type SandboxProfilePatchRequest = { "allowedImages"?: Array<string> | JsonValue; "allowedNetworks"?: Array<string> | JsonValue; "cpus"?: JsonValue | string; "defaultNetwork"?: JsonValue | string; "memory"?: JsonValue | string; "name"?: JsonValue | string; "reason": string; "status"?: JsonValue | string; "timeoutSeconds"?: JsonValue | number };
export type SandboxProfileResponse = { "sandboxProfile": JsonObject };
export type SandboxStatusResponse = { "docker": JsonObject; "restrictedSubprocess": JsonObject };
export type SessionCreateRequest = { "name"?: JsonValue | string; "projectId": string; "teamId"?: JsonValue | string };
export type SessionResponse = { "session": JsonObject };
export type SessionsListResponse = { "sessions": Array<JsonObject> };
export type SkillsListResponse = { "skills": Array<JsonObject> };
export type SkillsSyncRequest = { "skillsPath"?: string };
export type SkillsSyncResponse = { "skills": Array<JsonObject>; "synced": number };
export type TeamsListResponse = { "teams": Array<JsonObject> };
export type TelemetryStatusResponse = { "externalExporter": JsonObject };
export type ValidationError = { "ctx"?: JsonObject; "input"?: JsonValue; "loc": Array<number | string>; "msg": string; "type": string };
export type WorkflowCreateRequest = { "idea"?: JsonValue | string; "kind"?: "idea_to_pr" | "project_discovery" | "issue_to_pr" | "qa_validation" | "release_candidate"; "metadata"?: JsonObject; "projectId": string; "title"?: JsonValue | string };
export type WorkflowDetailResponse = { "agentRuns": Array<JsonObject>; "evidencePackages": Array<JsonObject>; "jobs": Array<JsonObject>; "workflow": JsonObject; "workflowRuns": Array<JsonObject>; "workflowSteps": Array<JsonObject>; "workspaces": Array<JsonObject> };
export type WorkflowResponse = { "workflow": JsonObject };
export type WorkflowStartResponse = { "workflow": JsonObject; "workflowRun": JsonObject; "workflowSteps": Array<JsonObject> };
export type WorkflowStatusChangeRequest = { "reason"?: string };
export type WorkflowsListResponse = { "workflowRuns": Array<JsonObject>; "workflowSteps": Array<JsonObject>; "workflows": Array<JsonObject> };
export type WorkspaceAllocateRequest = { "agentId": string; "baseBranch"?: string; "isolationType"?: "directory" | "git_worktree"; "projectId": string; "reason"?: string; "taskId": string; "workflowRunId"?: JsonValue | string; "workflowStepId"?: JsonValue | string };
export type WorkspaceArchiveRequest = { "reason"?: string };
export type WorkspaceArchiveResponse = { "evidencePackage": JsonObject; "workspace": JsonObject };
export type WorkspaceResponse = { "workspace": JsonObject };
export type WorkspacesListResponse = { "workspaces": Array<JsonObject> };

export const OPENAPI_TITLE = "Local Control Center" as const;
export const OPENAPI_VERSION = "0.1.0" as const;

export const API_ENDPOINTS = [
	{"method": "GET", "operationId": "list_agent_profiles_api_v1_agent_profiles_get", "path": "/api/v1/agent-profiles", "summary": "List Agent Profiles"},
	{"method": "POST", "operationId": "upsert_agent_profile_api_v1_agent_profiles_post", "path": "/api/v1/agent-profiles", "summary": "Upsert Agent Profile"},
	{"method": "GET", "operationId": "list_agent_runs_api_v1_agent_runs_get", "path": "/api/v1/agent-runs", "summary": "List Agent Runs"},
	{"method": "POST", "operationId": "create_agent_run_api_v1_agent_runs_post", "path": "/api/v1/agent-runs", "summary": "Create Agent Run"},
	{"method": "GET", "operationId": "agents_api_v1_agents_get", "path": "/api/v1/agents", "summary": "Agents"},
	{"method": "GET", "operationId": "approvals_api_v1_approvals_get", "path": "/api/v1/approvals", "summary": "Approvals"},
	{"method": "GET", "operationId": "list_architecture_decisions_api_v1_architecture_decisions_get", "path": "/api/v1/architecture-decisions", "summary": "List Architecture Decisions"},
	{"method": "POST", "operationId": "create_architecture_decision_api_v1_architecture_decisions_post", "path": "/api/v1/architecture-decisions", "summary": "Create Architecture Decision"},
	{"method": "GET", "operationId": "list_chats_api_v1_chats_get", "path": "/api/v1/chats", "summary": "List Chats"},
	{"method": "POST", "operationId": "create_chat_api_v1_chats_post", "path": "/api/v1/chats", "summary": "Create Chat"},
	{"method": "GET", "operationId": "events_api_v1_events_get", "path": "/api/v1/events", "summary": "Events"},
	{"method": "GET", "operationId": "list_evidence_api_v1_evidence_get", "path": "/api/v1/evidence", "summary": "List Evidence"},
	{"method": "POST", "operationId": "create_evidence_api_v1_evidence_post", "path": "/api/v1/evidence", "summary": "Create Evidence"},
	{"method": "POST", "operationId": "cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post", "path": "/api/v1/evidence/artifacts/cleanup", "summary": "Cleanup Artifacts"},
	{"method": "POST", "operationId": "plan_artifact_retention_api_v1_evidence_artifacts_retention_post", "path": "/api/v1/evidence/artifacts/retention", "summary": "Plan Artifact Retention"},
	{"method": "POST", "operationId": "apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post", "path": "/api/v1/evidence/artifacts/retention/actions", "summary": "Apply Artifact Retention Action"},
	{"method": "GET", "operationId": "get_evidence_api_v1_evidence__evidence_id__get", "path": "/api/v1/evidence/{evidence_id}", "summary": "Get Evidence"},
	{"method": "POST", "operationId": "ingest_artifact_api_v1_evidence__evidence_id__artifacts_post", "path": "/api/v1/evidence/{evidence_id}/artifacts", "summary": "Ingest Artifact"},
	{"method": "GET", "operationId": "get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get", "path": "/api/v1/evidence/{evidence_id}/artifacts/{artifact_id}", "summary": "Get Artifact"},
	{"method": "GET", "operationId": "export_evidence_report_api_v1_evidence__evidence_id__report_get", "path": "/api/v1/evidence/{evidence_id}/report", "summary": "Export Evidence Report"},
	{"method": "GET", "operationId": "governance_api_v1_governance_get", "path": "/api/v1/governance", "summary": "Governance"},
	{"method": "GET", "operationId": "list_ide_connections_api_v1_ide_connections_get", "path": "/api/v1/ide-connections", "summary": "List Ide Connections"},
	{"method": "POST", "operationId": "upsert_ide_connection_api_v1_ide_connections_post", "path": "/api/v1/ide-connections", "summary": "Upsert Ide Connection"},
	{"method": "GET", "operationId": "list_integrations_api_v1_integrations_get", "path": "/api/v1/integrations", "summary": "List Integrations"},
	{"method": "POST", "operationId": "register_mcp_server_api_v1_integrations_mcp_register_post", "path": "/api/v1/integrations/mcp/register", "summary": "Register Mcp Server"},
	{"method": "GET", "operationId": "list_jobs_api_v1_jobs_get", "path": "/api/v1/jobs", "summary": "List Jobs"},
	{"method": "POST", "operationId": "create_job_api_v1_jobs_post", "path": "/api/v1/jobs", "summary": "Create Job"},
	{"method": "POST", "operationId": "approve_action_api_v1_jobs__job_id__actions__action_id__approve_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/approve", "summary": "Approve Action"},
	{"method": "POST", "operationId": "deny_action_api_v1_jobs__job_id__actions__action_id__deny_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/deny", "summary": "Deny Action"},
	{"method": "POST", "operationId": "approve_job_api_v1_jobs__job_id__approve_post", "path": "/api/v1/jobs/{job_id}/approve", "summary": "Approve Job"},
	{"method": "POST", "operationId": "cancel_job_api_v1_jobs__job_id__cancel_post", "path": "/api/v1/jobs/{job_id}/cancel", "summary": "Cancel Job"},
	{"method": "POST", "operationId": "retry_job_api_v1_jobs__job_id__retry_post", "path": "/api/v1/jobs/{job_id}/retry", "summary": "Retry Job"},
	{"method": "GET", "operationId": "list_memory_api_v1_memory_get", "path": "/api/v1/memory", "summary": "List Memory"},
	{"method": "POST", "operationId": "create_memory_api_v1_memory_post", "path": "/api/v1/memory", "summary": "Create Memory"},
	{"method": "GET", "operationId": "list_model_policies_api_v1_model_policies_get", "path": "/api/v1/model-policies", "summary": "List Model Policies"},
	{"method": "POST", "operationId": "upsert_model_policy_api_v1_model_policies_post", "path": "/api/v1/model-policies", "summary": "Upsert Model Policy"},
	{"method": "GET", "operationId": "list_model_providers_api_v1_model_providers_get", "path": "/api/v1/model-providers", "summary": "List Model Providers"},
	{"method": "GET", "operationId": "list_next_steps_api_v1_next_steps_get", "path": "/api/v1/next-steps", "summary": "List Next Steps"},
	{"method": "POST", "operationId": "create_next_step_api_v1_next_steps_post", "path": "/api/v1/next-steps", "summary": "Create Next Step"},
	{"method": "PATCH", "operationId": "update_next_step_api_v1_next_steps__step_id__patch", "path": "/api/v1/next-steps/{step_id}", "summary": "Update Next Step"},
	{"method": "GET", "operationId": "open_design_api_v1_open_design_get", "path": "/api/v1/open-design", "summary": "Open Design"},
	{"method": "GET", "operationId": "overview_api_v1_overview_get", "path": "/api/v1/overview", "summary": "Overview"},
	{"method": "POST", "operationId": "revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post", "path": "/api/v1/permissions/grants/{grant_id}/revoke", "summary": "Revoke Permission Grant"},
	{"method": "GET", "operationId": "list_pipelines_api_v1_pipelines_get", "path": "/api/v1/pipelines", "summary": "List Pipelines"},
	{"method": "POST", "operationId": "create_pipeline_api_v1_pipelines_post", "path": "/api/v1/pipelines", "summary": "Create Pipeline"},
	{"method": "GET", "operationId": "list_policies_api_v1_policies_get", "path": "/api/v1/policies", "summary": "List Policies"},
	{"method": "POST", "operationId": "evaluate_policy_api_v1_policies_evaluate_post", "path": "/api/v1/policies/evaluate", "summary": "Evaluate Policy"},
	{"method": "GET", "operationId": "project_templates_api_v1_project_templates_get", "path": "/api/v1/project-templates", "summary": "Project Templates"},
	{"method": "GET", "operationId": "projects_api_v1_projects_get", "path": "/api/v1/projects", "summary": "Projects"},
	{"method": "POST", "operationId": "create_project_api_v1_projects_post", "path": "/api/v1/projects", "summary": "Create Project"},
	{"method": "GET", "operationId": "list_prompts_api_v1_prompts_get", "path": "/api/v1/prompts", "summary": "List Prompts"},
	{"method": "POST", "operationId": "upsert_prompt_api_v1_prompts_post", "path": "/api/v1/prompts", "summary": "Upsert Prompt"},
	{"method": "GET", "operationId": "providers_api_v1_providers_get", "path": "/api/v1/providers", "summary": "Providers"},
	{"method": "POST", "operationId": "retrieval_reindex_api_v1_retrieval_reindex_post", "path": "/api/v1/retrieval/reindex", "summary": "Retrieval Reindex"},
	{"method": "POST", "operationId": "retrieval_search_api_v1_retrieval_search_post", "path": "/api/v1/retrieval/search", "summary": "Retrieval Search"},
	{"method": "GET", "operationId": "retrieval_status_api_v1_retrieval_status_get", "path": "/api/v1/retrieval/status", "summary": "Retrieval Status"},
	{"method": "GET", "operationId": "list_risks_api_v1_risks_get", "path": "/api/v1/risks", "summary": "List Risks"},
	{"method": "POST", "operationId": "create_risk_api_v1_risks_post", "path": "/api/v1/risks", "summary": "Create Risk"},
	{"method": "PATCH", "operationId": "update_risk_api_v1_risks__risk_id__patch", "path": "/api/v1/risks/{risk_id}", "summary": "Update Risk"},
	{"method": "GET", "operationId": "list_runtime_providers_api_v1_runtime_providers_get", "path": "/api/v1/runtime/providers", "summary": "List Runtime Providers"},
	{"method": "PATCH", "operationId": "update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch", "path": "/api/v1/sandbox/profiles/{profile_id}", "summary": "Update Sandbox Profile"},
	{"method": "POST", "operationId": "revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post", "path": "/api/v1/sandbox/profiles/{profile_id}/revoke", "summary": "Revoke Sandbox Profile"},
	{"method": "GET", "operationId": "sandbox_status_api_v1_sandbox_status_get", "path": "/api/v1/sandbox/status", "summary": "Sandbox Status"},
	{"method": "GET", "operationId": "handshake_api_v1_security_handshake_get", "path": "/api/v1/security/handshake", "summary": "Handshake"},
	{"method": "GET", "operationId": "list_sessions_api_v1_sessions_get", "path": "/api/v1/sessions", "summary": "List Sessions"},
	{"method": "POST", "operationId": "create_session_api_v1_sessions_post", "path": "/api/v1/sessions", "summary": "Create Session"},
	{"method": "GET", "operationId": "list_skills_api_v1_skills_get", "path": "/api/v1/skills", "summary": "List Skills"},
	{"method": "POST", "operationId": "sync_skills_api_v1_skills_sync_post", "path": "/api/v1/skills/sync", "summary": "Sync Skills"},
	{"method": "GET", "operationId": "teams_api_v1_teams_get", "path": "/api/v1/teams", "summary": "Teams"},
	{"method": "GET", "operationId": "telemetry_status_api_v1_telemetry_status_get", "path": "/api/v1/telemetry/status", "summary": "Telemetry Status"},
	{"method": "GET", "operationId": "list_workflows_api_v1_workflows_get", "path": "/api/v1/workflows", "summary": "List Workflows"},
	{"method": "POST", "operationId": "create_workflow_api_v1_workflows_post", "path": "/api/v1/workflows", "summary": "Create Workflow"},
	{"method": "GET", "operationId": "get_workflow_api_v1_workflows__workflow_id__get", "path": "/api/v1/workflows/{workflow_id}", "summary": "Get Workflow"},
	{"method": "POST", "operationId": "cancel_workflow_api_v1_workflows__workflow_id__cancel_post", "path": "/api/v1/workflows/{workflow_id}/cancel", "summary": "Cancel Workflow"},
	{"method": "POST", "operationId": "pause_workflow_api_v1_workflows__workflow_id__pause_post", "path": "/api/v1/workflows/{workflow_id}/pause", "summary": "Pause Workflow"},
	{"method": "POST", "operationId": "resume_workflow_api_v1_workflows__workflow_id__resume_post", "path": "/api/v1/workflows/{workflow_id}/resume", "summary": "Resume Workflow"},
	{"method": "POST", "operationId": "start_workflow_api_v1_workflows__workflow_id__start_post", "path": "/api/v1/workflows/{workflow_id}/start", "summary": "Start Workflow"},
	{"method": "GET", "operationId": "list_workspaces_api_v1_workspaces_get", "path": "/api/v1/workspaces", "summary": "List Workspaces"},
	{"method": "POST", "operationId": "allocate_workspace_api_v1_workspaces_post", "path": "/api/v1/workspaces", "summary": "Allocate Workspace"},
	{"method": "POST", "operationId": "archive_workspace_api_v1_workspaces__workspace_id__archive_post", "path": "/api/v1/workspaces/{workspace_id}/archive", "summary": "Archive Workspace"},
	{"method": "GET", "operationId": "healthz_healthz_get", "path": "/healthz", "summary": "Healthz"}
] as const;

export type ApiEndpoint = (typeof API_ENDPOINTS)[number];
export type ApiMethod = ApiEndpoint["method"];
export type ApiPath = ApiEndpoint["path"];
export type ApiOperationId = ApiEndpoint["operationId"];
export type OperationById<T extends ApiOperationId> = Extract<ApiEndpoint, { operationId: T }>;
export type OperationPath<T extends ApiOperationId> = OperationById<T>["path"];
export type OperationMethod<T extends ApiOperationId> = OperationById<T>["method"];

export type OperationRequestBodies = {
	"agents_api_v1_agents_get": never,
	"allocate_workspace_api_v1_workspaces_post": WorkspaceAllocateRequest,
	"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": ArtifactRetentionActionRequest,
	"approvals_api_v1_approvals_get": never,
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": ApprovalReasonRequest,
	"approve_job_api_v1_jobs__job_id__approve_post": ApprovalReasonRequest,
	"archive_workspace_api_v1_workspaces__workspace_id__archive_post": WorkspaceArchiveRequest,
	"cancel_job_api_v1_jobs__job_id__cancel_post": OptionalReasonRequest,
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": WorkflowStatusChangeRequest,
	"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": ArtifactCleanupRequest,
	"create_agent_run_api_v1_agent_runs_post": AgentRunCreateRequest,
	"create_architecture_decision_api_v1_architecture_decisions_post": ArchitectureDecisionCreateRequest,
	"create_chat_api_v1_chats_post": ChatCreateRequest,
	"create_evidence_api_v1_evidence_post": EvidenceCreateRequest,
	"create_job_api_v1_jobs_post": JobCreateRequest,
	"create_memory_api_v1_memory_post": MemoryCreateRequest,
	"create_next_step_api_v1_next_steps_post": NextStepCreateRequest,
	"create_pipeline_api_v1_pipelines_post": PipelineCreateRequest,
	"create_project_api_v1_projects_post": ProjectCreateRequest,
	"create_risk_api_v1_risks_post": RiskCreateRequest,
	"create_session_api_v1_sessions_post": SessionCreateRequest,
	"create_workflow_api_v1_workflows_post": WorkflowCreateRequest,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": OptionalReasonRequest,
	"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluateRequest,
	"events_api_v1_events_get": never,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_evidence_api_v1_evidence__evidence_id__get": never,
	"get_workflow_api_v1_workflows__workflow_id__get": never,
	"governance_api_v1_governance_get": never,
	"handshake_api_v1_security_handshake_get": never,
	"healthz_healthz_get": never,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactIngestRequest,
	"list_agent_profiles_api_v1_agent_profiles_get": never,
	"list_agent_runs_api_v1_agent_runs_get": never,
	"list_architecture_decisions_api_v1_architecture_decisions_get": never,
	"list_chats_api_v1_chats_get": never,
	"list_evidence_api_v1_evidence_get": never,
	"list_ide_connections_api_v1_ide_connections_get": never,
	"list_integrations_api_v1_integrations_get": never,
	"list_jobs_api_v1_jobs_get": never,
	"list_memory_api_v1_memory_get": never,
	"list_model_policies_api_v1_model_policies_get": never,
	"list_model_providers_api_v1_model_providers_get": never,
	"list_next_steps_api_v1_next_steps_get": never,
	"list_pipelines_api_v1_pipelines_get": never,
	"list_policies_api_v1_policies_get": never,
	"list_prompts_api_v1_prompts_get": never,
	"list_risks_api_v1_risks_get": never,
	"list_runtime_providers_api_v1_runtime_providers_get": never,
	"list_sessions_api_v1_sessions_get": never,
	"list_skills_api_v1_skills_get": never,
	"list_workflows_api_v1_workflows_get": never,
	"list_workspaces_api_v1_workspaces_get": never,
	"open_design_api_v1_open_design_get": never,
	"overview_api_v1_overview_get": never,
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": WorkflowStatusChangeRequest,
	"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": ArtifactRetentionPlanRequest,
	"project_templates_api_v1_project_templates_get": never,
	"projects_api_v1_projects_get": never,
	"providers_api_v1_providers_get": never,
	"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerRegisterRequest,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": WorkflowStatusChangeRequest,
	"retrieval_reindex_api_v1_retrieval_reindex_post": EmptyObjectRequest,
	"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchRequest,
	"retrieval_status_api_v1_retrieval_status_get": never,
	"retry_job_api_v1_jobs__job_id__retry_post": OptionalReasonRequest,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": RequiredReasonRequest,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": RequiredReasonRequest,
	"sandbox_status_api_v1_sandbox_status_get": never,
	"start_workflow_api_v1_workflows__workflow_id__start_post": WorkflowStatusChangeRequest,
	"sync_skills_api_v1_skills_sync_post": SkillsSyncRequest,
	"teams_api_v1_teams_get": never,
	"telemetry_status_api_v1_telemetry_status_get": never,
	"update_next_step_api_v1_next_steps__step_id__patch": NextStepUpdateRequest,
	"update_risk_api_v1_risks__risk_id__patch": RiskUpdateRequest,
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": SandboxProfilePatchRequest,
	"upsert_agent_profile_api_v1_agent_profiles_post": AgentProfileUpsertRequest,
	"upsert_ide_connection_api_v1_ide_connections_post": IdeConnectionUpsertRequest,
	"upsert_model_policy_api_v1_model_policies_post": ModelPolicyUpsertRequest,
	"upsert_prompt_api_v1_prompts_post": PromptUpsertRequest
};

export type OperationResponseBodies = {
	"agents_api_v1_agents_get": AgentsListResponse,
	"allocate_workspace_api_v1_workspaces_post": WorkspaceResponse,
	"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": ArtifactRetentionActionResponse,
	"approvals_api_v1_approvals_get": ApprovalsListResponse,
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": JobMutationResponse,
	"approve_job_api_v1_jobs__job_id__approve_post": JobMutationResponse,
	"archive_workspace_api_v1_workspaces__workspace_id__archive_post": WorkspaceArchiveResponse,
	"cancel_job_api_v1_jobs__job_id__cancel_post": JobMutationResponse,
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": WorkflowResponse,
	"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": ArtifactCleanupResponse,
	"create_agent_run_api_v1_agent_runs_post": AgentRunResponse,
	"create_architecture_decision_api_v1_architecture_decisions_post": ArchitectureDecisionResponse,
	"create_chat_api_v1_chats_post": ChatResponse,
	"create_evidence_api_v1_evidence_post": EvidencePackageResponse,
	"create_job_api_v1_jobs_post": JobMutationResponse,
	"create_memory_api_v1_memory_post": MemoryResponse,
	"create_next_step_api_v1_next_steps_post": NextStepResponse,
	"create_pipeline_api_v1_pipelines_post": PipelineResponse,
	"create_project_api_v1_projects_post": ProjectResponse,
	"create_risk_api_v1_risks_post": RiskResponse,
	"create_session_api_v1_sessions_post": SessionResponse,
	"create_workflow_api_v1_workflows_post": WorkflowResponse,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": JobMutationResponse,
	"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluationResponse,
	"events_api_v1_events_get": never,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_evidence_api_v1_evidence__evidence_id__get": EvidenceDetailResponse,
	"get_workflow_api_v1_workflows__workflow_id__get": WorkflowDetailResponse,
	"governance_api_v1_governance_get": GovernanceResponse,
	"handshake_api_v1_security_handshake_get": HandshakeResponse,
	"healthz_healthz_get": HealthResponse,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactResponse,
	"list_agent_profiles_api_v1_agent_profiles_get": AgentProfilesListResponse,
	"list_agent_runs_api_v1_agent_runs_get": AgentRunsListResponse,
	"list_architecture_decisions_api_v1_architecture_decisions_get": ArchitectureDecisionsListResponse,
	"list_chats_api_v1_chats_get": ChatsListResponse,
	"list_evidence_api_v1_evidence_get": EvidenceListResponse,
	"list_ide_connections_api_v1_ide_connections_get": IdeConnectionsListResponse,
	"list_integrations_api_v1_integrations_get": IntegrationsListResponse,
	"list_jobs_api_v1_jobs_get": JobsListResponse,
	"list_memory_api_v1_memory_get": MemoryListResponse,
	"list_model_policies_api_v1_model_policies_get": ModelPoliciesListResponse,
	"list_model_providers_api_v1_model_providers_get": ModelProvidersListResponse,
	"list_next_steps_api_v1_next_steps_get": NextStepsListResponse,
	"list_pipelines_api_v1_pipelines_get": PipelinesListResponse,
	"list_policies_api_v1_policies_get": PoliciesListResponse,
	"list_prompts_api_v1_prompts_get": PromptTemplatesListResponse,
	"list_risks_api_v1_risks_get": RisksListResponse,
	"list_runtime_providers_api_v1_runtime_providers_get": RuntimeProvidersResponse,
	"list_sessions_api_v1_sessions_get": SessionsListResponse,
	"list_skills_api_v1_skills_get": SkillsListResponse,
	"list_workflows_api_v1_workflows_get": WorkflowsListResponse,
	"list_workspaces_api_v1_workspaces_get": WorkspacesListResponse,
	"open_design_api_v1_open_design_get": OpenDesignResponse,
	"overview_api_v1_overview_get": OverviewResponse,
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": WorkflowResponse,
	"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": ArtifactRetentionPlanResponse,
	"project_templates_api_v1_project_templates_get": ProjectTemplatesResponse,
	"projects_api_v1_projects_get": ProjectsListResponse,
	"providers_api_v1_providers_get": ProvidersListResponse,
	"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerResponse,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": WorkflowResponse,
	"retrieval_reindex_api_v1_retrieval_reindex_post": RetrievalReindexResponse,
	"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchResponse,
	"retrieval_status_api_v1_retrieval_status_get": RetrievalStatusResponse,
	"retry_job_api_v1_jobs__job_id__retry_post": JobMutationResponse,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": PermissionGrantResponse,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": SandboxProfileResponse,
	"sandbox_status_api_v1_sandbox_status_get": SandboxStatusResponse,
	"start_workflow_api_v1_workflows__workflow_id__start_post": WorkflowStartResponse,
	"sync_skills_api_v1_skills_sync_post": SkillsSyncResponse,
	"teams_api_v1_teams_get": TeamsListResponse,
	"telemetry_status_api_v1_telemetry_status_get": TelemetryStatusResponse,
	"update_next_step_api_v1_next_steps__step_id__patch": NextStepResponse,
	"update_risk_api_v1_risks__risk_id__patch": RiskResponse,
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": SandboxProfileMutationResponse,
	"upsert_agent_profile_api_v1_agent_profiles_post": AgentProfileResponse,
	"upsert_ide_connection_api_v1_ide_connections_post": IdeConnectionResponse,
	"upsert_model_policy_api_v1_model_policies_post": ModelPolicyResponse,
	"upsert_prompt_api_v1_prompts_post": PromptResponse
};

export type OperationRequestBody<T extends ApiOperationId> = OperationRequestBodies[T];
export type OperationResponse<T extends ApiOperationId> = OperationResponseBodies[T];

export const OPERATIONS_BY_ID = {
	"list_agent_profiles_api_v1_agent_profiles_get": {"method": "GET", "operationId": "list_agent_profiles_api_v1_agent_profiles_get", "path": "/api/v1/agent-profiles", "summary": "List Agent Profiles"},
	"upsert_agent_profile_api_v1_agent_profiles_post": {"method": "POST", "operationId": "upsert_agent_profile_api_v1_agent_profiles_post", "path": "/api/v1/agent-profiles", "summary": "Upsert Agent Profile"},
	"list_agent_runs_api_v1_agent_runs_get": {"method": "GET", "operationId": "list_agent_runs_api_v1_agent_runs_get", "path": "/api/v1/agent-runs", "summary": "List Agent Runs"},
	"create_agent_run_api_v1_agent_runs_post": {"method": "POST", "operationId": "create_agent_run_api_v1_agent_runs_post", "path": "/api/v1/agent-runs", "summary": "Create Agent Run"},
	"agents_api_v1_agents_get": {"method": "GET", "operationId": "agents_api_v1_agents_get", "path": "/api/v1/agents", "summary": "Agents"},
	"approvals_api_v1_approvals_get": {"method": "GET", "operationId": "approvals_api_v1_approvals_get", "path": "/api/v1/approvals", "summary": "Approvals"},
	"list_architecture_decisions_api_v1_architecture_decisions_get": {"method": "GET", "operationId": "list_architecture_decisions_api_v1_architecture_decisions_get", "path": "/api/v1/architecture-decisions", "summary": "List Architecture Decisions"},
	"create_architecture_decision_api_v1_architecture_decisions_post": {"method": "POST", "operationId": "create_architecture_decision_api_v1_architecture_decisions_post", "path": "/api/v1/architecture-decisions", "summary": "Create Architecture Decision"},
	"list_chats_api_v1_chats_get": {"method": "GET", "operationId": "list_chats_api_v1_chats_get", "path": "/api/v1/chats", "summary": "List Chats"},
	"create_chat_api_v1_chats_post": {"method": "POST", "operationId": "create_chat_api_v1_chats_post", "path": "/api/v1/chats", "summary": "Create Chat"},
	"events_api_v1_events_get": {"method": "GET", "operationId": "events_api_v1_events_get", "path": "/api/v1/events", "summary": "Events"},
	"list_evidence_api_v1_evidence_get": {"method": "GET", "operationId": "list_evidence_api_v1_evidence_get", "path": "/api/v1/evidence", "summary": "List Evidence"},
	"create_evidence_api_v1_evidence_post": {"method": "POST", "operationId": "create_evidence_api_v1_evidence_post", "path": "/api/v1/evidence", "summary": "Create Evidence"},
	"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": {"method": "POST", "operationId": "cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post", "path": "/api/v1/evidence/artifacts/cleanup", "summary": "Cleanup Artifacts"},
	"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": {"method": "POST", "operationId": "plan_artifact_retention_api_v1_evidence_artifacts_retention_post", "path": "/api/v1/evidence/artifacts/retention", "summary": "Plan Artifact Retention"},
	"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": {"method": "POST", "operationId": "apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post", "path": "/api/v1/evidence/artifacts/retention/actions", "summary": "Apply Artifact Retention Action"},
	"get_evidence_api_v1_evidence__evidence_id__get": {"method": "GET", "operationId": "get_evidence_api_v1_evidence__evidence_id__get", "path": "/api/v1/evidence/{evidence_id}", "summary": "Get Evidence"},
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": {"method": "POST", "operationId": "ingest_artifact_api_v1_evidence__evidence_id__artifacts_post", "path": "/api/v1/evidence/{evidence_id}/artifacts", "summary": "Ingest Artifact"},
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": {"method": "GET", "operationId": "get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get", "path": "/api/v1/evidence/{evidence_id}/artifacts/{artifact_id}", "summary": "Get Artifact"},
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": {"method": "GET", "operationId": "export_evidence_report_api_v1_evidence__evidence_id__report_get", "path": "/api/v1/evidence/{evidence_id}/report", "summary": "Export Evidence Report"},
	"governance_api_v1_governance_get": {"method": "GET", "operationId": "governance_api_v1_governance_get", "path": "/api/v1/governance", "summary": "Governance"},
	"list_ide_connections_api_v1_ide_connections_get": {"method": "GET", "operationId": "list_ide_connections_api_v1_ide_connections_get", "path": "/api/v1/ide-connections", "summary": "List Ide Connections"},
	"upsert_ide_connection_api_v1_ide_connections_post": {"method": "POST", "operationId": "upsert_ide_connection_api_v1_ide_connections_post", "path": "/api/v1/ide-connections", "summary": "Upsert Ide Connection"},
	"list_integrations_api_v1_integrations_get": {"method": "GET", "operationId": "list_integrations_api_v1_integrations_get", "path": "/api/v1/integrations", "summary": "List Integrations"},
	"register_mcp_server_api_v1_integrations_mcp_register_post": {"method": "POST", "operationId": "register_mcp_server_api_v1_integrations_mcp_register_post", "path": "/api/v1/integrations/mcp/register", "summary": "Register Mcp Server"},
	"list_jobs_api_v1_jobs_get": {"method": "GET", "operationId": "list_jobs_api_v1_jobs_get", "path": "/api/v1/jobs", "summary": "List Jobs"},
	"create_job_api_v1_jobs_post": {"method": "POST", "operationId": "create_job_api_v1_jobs_post", "path": "/api/v1/jobs", "summary": "Create Job"},
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": {"method": "POST", "operationId": "approve_action_api_v1_jobs__job_id__actions__action_id__approve_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/approve", "summary": "Approve Action"},
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": {"method": "POST", "operationId": "deny_action_api_v1_jobs__job_id__actions__action_id__deny_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/deny", "summary": "Deny Action"},
	"approve_job_api_v1_jobs__job_id__approve_post": {"method": "POST", "operationId": "approve_job_api_v1_jobs__job_id__approve_post", "path": "/api/v1/jobs/{job_id}/approve", "summary": "Approve Job"},
	"cancel_job_api_v1_jobs__job_id__cancel_post": {"method": "POST", "operationId": "cancel_job_api_v1_jobs__job_id__cancel_post", "path": "/api/v1/jobs/{job_id}/cancel", "summary": "Cancel Job"},
	"retry_job_api_v1_jobs__job_id__retry_post": {"method": "POST", "operationId": "retry_job_api_v1_jobs__job_id__retry_post", "path": "/api/v1/jobs/{job_id}/retry", "summary": "Retry Job"},
	"list_memory_api_v1_memory_get": {"method": "GET", "operationId": "list_memory_api_v1_memory_get", "path": "/api/v1/memory", "summary": "List Memory"},
	"create_memory_api_v1_memory_post": {"method": "POST", "operationId": "create_memory_api_v1_memory_post", "path": "/api/v1/memory", "summary": "Create Memory"},
	"list_model_policies_api_v1_model_policies_get": {"method": "GET", "operationId": "list_model_policies_api_v1_model_policies_get", "path": "/api/v1/model-policies", "summary": "List Model Policies"},
	"upsert_model_policy_api_v1_model_policies_post": {"method": "POST", "operationId": "upsert_model_policy_api_v1_model_policies_post", "path": "/api/v1/model-policies", "summary": "Upsert Model Policy"},
	"list_model_providers_api_v1_model_providers_get": {"method": "GET", "operationId": "list_model_providers_api_v1_model_providers_get", "path": "/api/v1/model-providers", "summary": "List Model Providers"},
	"list_next_steps_api_v1_next_steps_get": {"method": "GET", "operationId": "list_next_steps_api_v1_next_steps_get", "path": "/api/v1/next-steps", "summary": "List Next Steps"},
	"create_next_step_api_v1_next_steps_post": {"method": "POST", "operationId": "create_next_step_api_v1_next_steps_post", "path": "/api/v1/next-steps", "summary": "Create Next Step"},
	"update_next_step_api_v1_next_steps__step_id__patch": {"method": "PATCH", "operationId": "update_next_step_api_v1_next_steps__step_id__patch", "path": "/api/v1/next-steps/{step_id}", "summary": "Update Next Step"},
	"open_design_api_v1_open_design_get": {"method": "GET", "operationId": "open_design_api_v1_open_design_get", "path": "/api/v1/open-design", "summary": "Open Design"},
	"overview_api_v1_overview_get": {"method": "GET", "operationId": "overview_api_v1_overview_get", "path": "/api/v1/overview", "summary": "Overview"},
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": {"method": "POST", "operationId": "revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post", "path": "/api/v1/permissions/grants/{grant_id}/revoke", "summary": "Revoke Permission Grant"},
	"list_pipelines_api_v1_pipelines_get": {"method": "GET", "operationId": "list_pipelines_api_v1_pipelines_get", "path": "/api/v1/pipelines", "summary": "List Pipelines"},
	"create_pipeline_api_v1_pipelines_post": {"method": "POST", "operationId": "create_pipeline_api_v1_pipelines_post", "path": "/api/v1/pipelines", "summary": "Create Pipeline"},
	"list_policies_api_v1_policies_get": {"method": "GET", "operationId": "list_policies_api_v1_policies_get", "path": "/api/v1/policies", "summary": "List Policies"},
	"evaluate_policy_api_v1_policies_evaluate_post": {"method": "POST", "operationId": "evaluate_policy_api_v1_policies_evaluate_post", "path": "/api/v1/policies/evaluate", "summary": "Evaluate Policy"},
	"project_templates_api_v1_project_templates_get": {"method": "GET", "operationId": "project_templates_api_v1_project_templates_get", "path": "/api/v1/project-templates", "summary": "Project Templates"},
	"projects_api_v1_projects_get": {"method": "GET", "operationId": "projects_api_v1_projects_get", "path": "/api/v1/projects", "summary": "Projects"},
	"create_project_api_v1_projects_post": {"method": "POST", "operationId": "create_project_api_v1_projects_post", "path": "/api/v1/projects", "summary": "Create Project"},
	"list_prompts_api_v1_prompts_get": {"method": "GET", "operationId": "list_prompts_api_v1_prompts_get", "path": "/api/v1/prompts", "summary": "List Prompts"},
	"upsert_prompt_api_v1_prompts_post": {"method": "POST", "operationId": "upsert_prompt_api_v1_prompts_post", "path": "/api/v1/prompts", "summary": "Upsert Prompt"},
	"providers_api_v1_providers_get": {"method": "GET", "operationId": "providers_api_v1_providers_get", "path": "/api/v1/providers", "summary": "Providers"},
	"retrieval_reindex_api_v1_retrieval_reindex_post": {"method": "POST", "operationId": "retrieval_reindex_api_v1_retrieval_reindex_post", "path": "/api/v1/retrieval/reindex", "summary": "Retrieval Reindex"},
	"retrieval_search_api_v1_retrieval_search_post": {"method": "POST", "operationId": "retrieval_search_api_v1_retrieval_search_post", "path": "/api/v1/retrieval/search", "summary": "Retrieval Search"},
	"retrieval_status_api_v1_retrieval_status_get": {"method": "GET", "operationId": "retrieval_status_api_v1_retrieval_status_get", "path": "/api/v1/retrieval/status", "summary": "Retrieval Status"},
	"list_risks_api_v1_risks_get": {"method": "GET", "operationId": "list_risks_api_v1_risks_get", "path": "/api/v1/risks", "summary": "List Risks"},
	"create_risk_api_v1_risks_post": {"method": "POST", "operationId": "create_risk_api_v1_risks_post", "path": "/api/v1/risks", "summary": "Create Risk"},
	"update_risk_api_v1_risks__risk_id__patch": {"method": "PATCH", "operationId": "update_risk_api_v1_risks__risk_id__patch", "path": "/api/v1/risks/{risk_id}", "summary": "Update Risk"},
	"list_runtime_providers_api_v1_runtime_providers_get": {"method": "GET", "operationId": "list_runtime_providers_api_v1_runtime_providers_get", "path": "/api/v1/runtime/providers", "summary": "List Runtime Providers"},
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": {"method": "PATCH", "operationId": "update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch", "path": "/api/v1/sandbox/profiles/{profile_id}", "summary": "Update Sandbox Profile"},
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": {"method": "POST", "operationId": "revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post", "path": "/api/v1/sandbox/profiles/{profile_id}/revoke", "summary": "Revoke Sandbox Profile"},
	"sandbox_status_api_v1_sandbox_status_get": {"method": "GET", "operationId": "sandbox_status_api_v1_sandbox_status_get", "path": "/api/v1/sandbox/status", "summary": "Sandbox Status"},
	"handshake_api_v1_security_handshake_get": {"method": "GET", "operationId": "handshake_api_v1_security_handshake_get", "path": "/api/v1/security/handshake", "summary": "Handshake"},
	"list_sessions_api_v1_sessions_get": {"method": "GET", "operationId": "list_sessions_api_v1_sessions_get", "path": "/api/v1/sessions", "summary": "List Sessions"},
	"create_session_api_v1_sessions_post": {"method": "POST", "operationId": "create_session_api_v1_sessions_post", "path": "/api/v1/sessions", "summary": "Create Session"},
	"list_skills_api_v1_skills_get": {"method": "GET", "operationId": "list_skills_api_v1_skills_get", "path": "/api/v1/skills", "summary": "List Skills"},
	"sync_skills_api_v1_skills_sync_post": {"method": "POST", "operationId": "sync_skills_api_v1_skills_sync_post", "path": "/api/v1/skills/sync", "summary": "Sync Skills"},
	"teams_api_v1_teams_get": {"method": "GET", "operationId": "teams_api_v1_teams_get", "path": "/api/v1/teams", "summary": "Teams"},
	"telemetry_status_api_v1_telemetry_status_get": {"method": "GET", "operationId": "telemetry_status_api_v1_telemetry_status_get", "path": "/api/v1/telemetry/status", "summary": "Telemetry Status"},
	"list_workflows_api_v1_workflows_get": {"method": "GET", "operationId": "list_workflows_api_v1_workflows_get", "path": "/api/v1/workflows", "summary": "List Workflows"},
	"create_workflow_api_v1_workflows_post": {"method": "POST", "operationId": "create_workflow_api_v1_workflows_post", "path": "/api/v1/workflows", "summary": "Create Workflow"},
	"get_workflow_api_v1_workflows__workflow_id__get": {"method": "GET", "operationId": "get_workflow_api_v1_workflows__workflow_id__get", "path": "/api/v1/workflows/{workflow_id}", "summary": "Get Workflow"},
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": {"method": "POST", "operationId": "cancel_workflow_api_v1_workflows__workflow_id__cancel_post", "path": "/api/v1/workflows/{workflow_id}/cancel", "summary": "Cancel Workflow"},
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": {"method": "POST", "operationId": "pause_workflow_api_v1_workflows__workflow_id__pause_post", "path": "/api/v1/workflows/{workflow_id}/pause", "summary": "Pause Workflow"},
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": {"method": "POST", "operationId": "resume_workflow_api_v1_workflows__workflow_id__resume_post", "path": "/api/v1/workflows/{workflow_id}/resume", "summary": "Resume Workflow"},
	"start_workflow_api_v1_workflows__workflow_id__start_post": {"method": "POST", "operationId": "start_workflow_api_v1_workflows__workflow_id__start_post", "path": "/api/v1/workflows/{workflow_id}/start", "summary": "Start Workflow"},
	"list_workspaces_api_v1_workspaces_get": {"method": "GET", "operationId": "list_workspaces_api_v1_workspaces_get", "path": "/api/v1/workspaces", "summary": "List Workspaces"},
	"allocate_workspace_api_v1_workspaces_post": {"method": "POST", "operationId": "allocate_workspace_api_v1_workspaces_post", "path": "/api/v1/workspaces", "summary": "Allocate Workspace"},
	"archive_workspace_api_v1_workspaces__workspace_id__archive_post": {"method": "POST", "operationId": "archive_workspace_api_v1_workspaces__workspace_id__archive_post", "path": "/api/v1/workspaces/{workspace_id}/archive", "summary": "Archive Workspace"},
	"healthz_healthz_get": {"method": "GET", "operationId": "healthz_healthz_get", "path": "/healthz", "summary": "Healthz"}
} as const satisfies Record<ApiOperationId, ApiEndpoint>;

export type GeneratedRequestOptions<TBody = unknown> = {
	pathParams?: Record<string, string | number>;
	query?: Record<string, string | number | boolean | null | undefined>;
	token?: string;
	body?: TBody;
	signal?: AbortSignal;
};

export function findEndpoint(method: ApiMethod, path: ApiPath): ApiEndpoint | undefined {
	return API_ENDPOINTS.find((endpoint) => endpoint.method === method && endpoint.path === path);
}

export function buildApiPath(
	path: string,
	pathParams: Record<string, string | number> = {},
	query: Record<string, string | number | boolean | null | undefined> = {},
): string {
	const resolvedPath = path.replace(/\{([^}]+)\}/g, (_match, key: string) => {
		const value = pathParams[key];
		if (value === undefined || value === null) {
			throw new Error(`Missing path parameter: ${key}`);
		}
		return encodeURIComponent(String(value));
	});
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(query)) {
		if (value !== undefined && value !== null) params.set(key, String(value));
	}
	const queryString = params.toString();
	return queryString ? `${resolvedPath}?${queryString}` : resolvedPath;
}

export async function requestGeneratedOperation<
	TOperationId extends ApiOperationId,
	TResponse = OperationResponse<TOperationId>,
>(
	operationId: TOperationId,
	options: GeneratedRequestOptions<OperationRequestBody<TOperationId>> = {},
): Promise<TResponse> {
	const endpoint = OPERATIONS_BY_ID[operationId];
	const headers: Record<string, string> = { Accept: "application/json" };
	if (options.body !== undefined) headers["Content-Type"] = "application/json";
	if (options.token) headers["X-Local-Control-Token"] = options.token;
	const response = await fetch(buildApiPath(endpoint.path, options.pathParams, options.query), {
		method: endpoint.method,
		headers,
		body: options.body === undefined ? undefined : JSON.stringify(options.body),
		signal: options.signal,
	});
	const text = await response.text();
	const payload = text ? JSON.parse(text) : {};
	if (!response.ok) {
		const detail = payload.detail ?? payload.error ?? response.statusText;
		throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
	}
	return payload as TResponse;
}
