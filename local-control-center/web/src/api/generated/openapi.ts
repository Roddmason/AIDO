// Generated from FastAPI OpenAPI. Do not edit by hand.
// No network access is required; run `corepack pnpm@10.24.0 run openapi:generate`.

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

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
	"allocate_workspace_api_v1_workspaces_post": unknown,
	"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": unknown,
	"approvals_api_v1_approvals_get": never,
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": unknown,
	"approve_job_api_v1_jobs__job_id__approve_post": unknown,
	"archive_workspace_api_v1_workspaces__workspace_id__archive_post": unknown,
	"cancel_job_api_v1_jobs__job_id__cancel_post": unknown,
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": unknown,
	"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": unknown,
	"create_agent_run_api_v1_agent_runs_post": unknown,
	"create_architecture_decision_api_v1_architecture_decisions_post": unknown,
	"create_chat_api_v1_chats_post": unknown,
	"create_evidence_api_v1_evidence_post": unknown,
	"create_job_api_v1_jobs_post": unknown,
	"create_memory_api_v1_memory_post": unknown,
	"create_next_step_api_v1_next_steps_post": unknown,
	"create_pipeline_api_v1_pipelines_post": unknown,
	"create_project_api_v1_projects_post": unknown,
	"create_risk_api_v1_risks_post": unknown,
	"create_session_api_v1_sessions_post": unknown,
	"create_workflow_api_v1_workflows_post": unknown,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": unknown,
	"evaluate_policy_api_v1_policies_evaluate_post": unknown,
	"events_api_v1_events_get": never,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_evidence_api_v1_evidence__evidence_id__get": never,
	"get_workflow_api_v1_workflows__workflow_id__get": never,
	"governance_api_v1_governance_get": never,
	"handshake_api_v1_security_handshake_get": never,
	"healthz_healthz_get": never,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": unknown,
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
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": unknown,
	"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": unknown,
	"project_templates_api_v1_project_templates_get": never,
	"projects_api_v1_projects_get": never,
	"providers_api_v1_providers_get": never,
	"register_mcp_server_api_v1_integrations_mcp_register_post": unknown,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": unknown,
	"retrieval_reindex_api_v1_retrieval_reindex_post": unknown,
	"retrieval_search_api_v1_retrieval_search_post": unknown,
	"retrieval_status_api_v1_retrieval_status_get": never,
	"retry_job_api_v1_jobs__job_id__retry_post": unknown,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": unknown,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": unknown,
	"sandbox_status_api_v1_sandbox_status_get": never,
	"start_workflow_api_v1_workflows__workflow_id__start_post": unknown,
	"sync_skills_api_v1_skills_sync_post": unknown,
	"teams_api_v1_teams_get": never,
	"telemetry_status_api_v1_telemetry_status_get": never,
	"update_next_step_api_v1_next_steps__step_id__patch": unknown,
	"update_risk_api_v1_risks__risk_id__patch": unknown,
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": unknown,
	"upsert_agent_profile_api_v1_agent_profiles_post": unknown,
	"upsert_ide_connection_api_v1_ide_connections_post": unknown,
	"upsert_model_policy_api_v1_model_policies_post": unknown,
	"upsert_prompt_api_v1_prompts_post": unknown
};

export type OperationResponseBodies = {
	"agents_api_v1_agents_get": JsonObject,
	"allocate_workspace_api_v1_workspaces_post": JsonObject,
	"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": JsonObject,
	"approvals_api_v1_approvals_get": JsonObject,
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": JsonObject,
	"approve_job_api_v1_jobs__job_id__approve_post": JsonObject,
	"archive_workspace_api_v1_workspaces__workspace_id__archive_post": JsonObject,
	"cancel_job_api_v1_jobs__job_id__cancel_post": JsonObject,
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": JsonObject,
	"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": JsonObject,
	"create_agent_run_api_v1_agent_runs_post": JsonObject,
	"create_architecture_decision_api_v1_architecture_decisions_post": JsonObject,
	"create_chat_api_v1_chats_post": JsonObject,
	"create_evidence_api_v1_evidence_post": JsonObject,
	"create_job_api_v1_jobs_post": JsonObject,
	"create_memory_api_v1_memory_post": JsonObject,
	"create_next_step_api_v1_next_steps_post": JsonObject,
	"create_pipeline_api_v1_pipelines_post": JsonObject,
	"create_project_api_v1_projects_post": JsonObject,
	"create_risk_api_v1_risks_post": JsonObject,
	"create_session_api_v1_sessions_post": JsonObject,
	"create_workflow_api_v1_workflows_post": JsonObject,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": JsonObject,
	"evaluate_policy_api_v1_policies_evaluate_post": JsonObject,
	"events_api_v1_events_get": never,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_evidence_api_v1_evidence__evidence_id__get": JsonObject,
	"get_workflow_api_v1_workflows__workflow_id__get": JsonObject,
	"governance_api_v1_governance_get": JsonObject,
	"handshake_api_v1_security_handshake_get": JsonObject,
	"healthz_healthz_get": JsonObject,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": JsonObject,
	"list_agent_profiles_api_v1_agent_profiles_get": JsonObject,
	"list_agent_runs_api_v1_agent_runs_get": JsonObject,
	"list_architecture_decisions_api_v1_architecture_decisions_get": JsonObject,
	"list_chats_api_v1_chats_get": JsonObject,
	"list_evidence_api_v1_evidence_get": JsonObject,
	"list_ide_connections_api_v1_ide_connections_get": JsonObject,
	"list_integrations_api_v1_integrations_get": JsonObject,
	"list_jobs_api_v1_jobs_get": JsonObject,
	"list_memory_api_v1_memory_get": JsonObject,
	"list_model_policies_api_v1_model_policies_get": JsonObject,
	"list_model_providers_api_v1_model_providers_get": JsonObject,
	"list_next_steps_api_v1_next_steps_get": JsonObject,
	"list_pipelines_api_v1_pipelines_get": JsonObject,
	"list_policies_api_v1_policies_get": JsonObject,
	"list_prompts_api_v1_prompts_get": JsonObject,
	"list_risks_api_v1_risks_get": JsonObject,
	"list_runtime_providers_api_v1_runtime_providers_get": JsonObject,
	"list_sessions_api_v1_sessions_get": JsonObject,
	"list_skills_api_v1_skills_get": JsonObject,
	"list_workflows_api_v1_workflows_get": JsonObject,
	"list_workspaces_api_v1_workspaces_get": JsonObject,
	"open_design_api_v1_open_design_get": JsonObject,
	"overview_api_v1_overview_get": JsonObject,
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": JsonObject,
	"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": JsonObject,
	"project_templates_api_v1_project_templates_get": JsonObject,
	"projects_api_v1_projects_get": JsonObject,
	"providers_api_v1_providers_get": JsonObject,
	"register_mcp_server_api_v1_integrations_mcp_register_post": JsonObject,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": JsonObject,
	"retrieval_reindex_api_v1_retrieval_reindex_post": JsonObject,
	"retrieval_search_api_v1_retrieval_search_post": JsonObject,
	"retrieval_status_api_v1_retrieval_status_get": JsonObject,
	"retry_job_api_v1_jobs__job_id__retry_post": JsonObject,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": JsonObject,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": JsonObject,
	"sandbox_status_api_v1_sandbox_status_get": JsonObject,
	"start_workflow_api_v1_workflows__workflow_id__start_post": JsonObject,
	"sync_skills_api_v1_skills_sync_post": JsonObject,
	"teams_api_v1_teams_get": JsonObject,
	"telemetry_status_api_v1_telemetry_status_get": JsonObject,
	"update_next_step_api_v1_next_steps__step_id__patch": JsonObject,
	"update_risk_api_v1_risks__risk_id__patch": JsonObject,
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": JsonObject,
	"upsert_agent_profile_api_v1_agent_profiles_post": JsonObject,
	"upsert_ide_connection_api_v1_ide_connections_post": JsonObject,
	"upsert_model_policy_api_v1_model_policies_post": JsonObject,
	"upsert_prompt_api_v1_prompts_post": JsonObject
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
