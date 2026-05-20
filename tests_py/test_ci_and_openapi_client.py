from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_github_workflows_are_removed_and_main_protection_is_explicit() -> None:
    workflows_dir = ROOT / ".github" / "workflows"
    if workflows_dir.exists():
        assert not list(workflows_dir.glob("*.yml"))
        assert not list(workflows_dir.glob("*.yaml"))

    script = ROOT / "local-control-center" / "scripts" / "protect-main-branch.ps1"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "/repos/$Owner/$Repo/rulesets" in content
    assert "refs/heads/$Branch" in content
    assert "Assert-GhSuccess" in content
    assert "$LASTEXITCODE" in content
    assert '"type" = "creation"' in content
    assert '"type" = "update"' in content
    assert '"type" = "deletion"' in content
    assert '"type" = "pull_request"' in content
    assert "required_approving_review_count = 1" in content
    assert "required_status_checks" not in content
    assert '"type" = "non_fast_forward"' in content


def test_generated_openapi_client_is_checked_in_and_v1_only() -> None:
    generated = ROOT / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts"
    assert generated.exists()
    content = generated.read_text(encoding="utf-8")
    assert "Generated from FastAPI OpenAPI" in content
    assert "/api/v1/workflows" in content
    assert "/api/v1/agent-profiles" in content
    assert "/api/v1/integrations/mcp/register" in content
    assert ("/api/" + "state") not in content
    assert "export type ApiPath" in content
    assert "export type ApiEndpoint" in content
    assert "export const OPERATIONS_BY_ID" in content
    assert "export type OperationById" in content
    assert "export type OperationResponse" in content
    assert "export type OperationRequestBody" in content
    assert "export type OperationResponseBodies" in content
    assert '"healthz_healthz_get": HealthResponse' in content
    assert '"handshake_api_v1_security_handshake_get": HandshakeResponse' in content
    assert '"retrieval_status_api_v1_retrieval_status_get": RetrievalStatusResponse' in content
    assert '"create_workflow_api_v1_workflows_post": WorkflowCreateRequest' in content
    assert '"start_workflow_api_v1_workflows__workflow_id__start_post": WorkflowStatusChangeRequest' in content
    assert '"upsert_agent_profile_api_v1_agent_profiles_post": AgentProfileUpsertRequest' in content
    assert '"upsert_model_policy_api_v1_model_policies_post": ModelPolicyUpsertRequest' in content
    assert '"create_job_api_v1_jobs_post": JobCreateRequest' in content
    assert '"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": ApprovalReasonRequest' in content
    assert '"cancel_job_api_v1_jobs__job_id__cancel_post": OptionalReasonRequest' in content
    assert '"create_architecture_decision_api_v1_architecture_decisions_post": ArchitectureDecisionCreateRequest' in content
    assert '"create_risk_api_v1_risks_post": RiskCreateRequest' in content
    assert '"update_risk_api_v1_risks__risk_id__patch": RiskUpdateRequest' in content
    assert '"create_next_step_api_v1_next_steps_post": NextStepCreateRequest' in content
    assert '"update_next_step_api_v1_next_steps__step_id__patch": NextStepUpdateRequest' in content
    assert '"allocate_workspace_api_v1_workspaces_post": WorkspaceAllocateRequest' in content
    assert "export type DevcontainerMetadata" in content
    assert '"devcontainer"?: DevcontainerMetadata | null' in content
    assert '"archive_workspace_api_v1_workspaces__workspace_id__archive_post": WorkspaceArchiveRequest' in content
    assert '"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerRegisterRequest' in content
    assert '"upsert_ide_connection_api_v1_ide_connections_post": IdeConnectionUpsertRequest' in content
    assert '"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluateRequest' in content
    assert '"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": SandboxProfilePatchRequest' in content
    assert '"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": RequiredReasonRequest' in content
    assert '"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": RequiredReasonRequest' in content
    assert '"create_project_api_v1_projects_post": ProjectCreateRequest' in content
    assert '"create_session_api_v1_sessions_post": SessionCreateRequest' in content
    assert '"create_chat_api_v1_chats_post": ChatCreateRequest' in content
    assert '"create_pipeline_api_v1_pipelines_post": PipelineCreateRequest' in content
    assert '"create_memory_api_v1_memory_post": MemoryCreateRequest' in content
    assert '"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchRequest' in content
    assert '"retrieval_reindex_api_v1_retrieval_reindex_post": EmptyObjectRequest' in content
    assert '"upsert_prompt_api_v1_prompts_post": PromptUpsertRequest' in content
    assert '"create_agent_run_api_v1_agent_runs_post": AgentRunCreateRequest' in content
    assert '"sync_skills_api_v1_skills_sync_post": SkillsSyncRequest' in content
    assert '"create_evidence_api_v1_evidence_post": EvidenceCreateRequest' in content
    assert '"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactIngestRequest' in content
    assert '"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": ArtifactCleanupRequest' in content
    assert '"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": ArtifactRetentionPlanRequest' in content
    assert '"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": ArtifactRetentionActionRequest' in content
    assert '"overview_api_v1_overview_get": OverviewResponse' in content
    overview_line = next(line for line in content.splitlines() if line.startswith("export type OverviewResponse = "))
    assert '"projects": Array<ProjectRecord>' in overview_line
    assert '"jobs": Array<JobRecord>' in overview_line
    assert '"actionRequests": Array<ActionRequestRecord>' in overview_line
    assert '"runtimeWorkspaces": Array<WorkspaceRecord>' in overview_line
    assert '"evidencePackages": Array<EvidencePackageRecord>' in overview_line
    assert '"architectureDecisions": Array<ArchitectureDecisionRecord>' in overview_line
    assert '"sessions": Array<SessionRecord>' in overview_line
    assert '"chats": Array<ChatRecord>' in overview_line
    assert '"pipelines": Array<PipelineRecord>' in overview_line
    assert '"memoryItems": Array<MemoryItemRecord>' in overview_line
    assert '"workflows": Array<WorkflowRecord>' in overview_line
    assert '"workflowRuns": Array<WorkflowRunRecord>' in overview_line
    assert '"workflowSteps": Array<WorkflowStepRecord>' in overview_line
    assert '"permissionDecisions": Array<PermissionDecisionRecord>' in overview_line
    assert '"sandboxProfiles": Array<SandboxProfileRecord>' in overview_line
    assert '"agentProfiles": Array<AgentProfileRecord>' in overview_line
    assert '"agentRuns": Array<AgentRunRecord>' in overview_line
    assert '"modelPolicies": Array<ModelPolicyRecord>' in overview_line
    assert '"modelProviders": Array<ModelProviderRecord>' in overview_line
    assert '"list_jobs_api_v1_jobs_get": JobsListResponse' in content
    assert '"approvals_api_v1_approvals_get": ApprovalsListResponse' in content
    assert "export type JobRecord" in content
    assert '"modelPolicyId"?: null | string' in content
    assert '"modelPolicyId"?: JsonValue | string' not in content
    assert 'ProjectResponse = { "auditEvent"?: AuditEventRecord | null; "project": ProjectRecord }' in content
    assert '"permissionGrant"?: PermissionGrantRecord | null' in content
    assert 'export type SessionsListResponse = { "sessions": Array<SessionRecord> }' in content
    assert 'export type ChatsListResponse = { "chats": Array<ChatRecord> }' in content
    assert 'export type PipelinesListResponse = { "pipelines": Array<PipelineRecord> }' in content
    assert 'export type MemoryListResponse = { "memoryItems": Array<MemoryItemRecord> }' in content
    assert (
        'export type WorkflowsListResponse = { "workflowRuns": Array<WorkflowRunRecord>; '
        '"workflowSteps": Array<WorkflowStepRecord>; "workflows": Array<WorkflowRecord> }'
    ) in content
    assert "export type PermissionDecisionRecord" in content
    assert "export type AgentProfileRecord" in content
    assert "export type ModelPolicyRecord" in content
    assert "export type ActionRequestRecord" in content
    assert "export type EventRecord" in content
    assert "export type AuditEventRecord" in content
    assert 'JobsListResponse = { "events"?: Array<EventRecord>; "jobs": Array<JobRecord>' in content
    assert '"governance_api_v1_governance_get": GovernanceResponse' in content
    assert "export type ArchitectureDecisionRecord" in content
    assert "export type RiskRecord" in content
    assert "export type NextStepRecord" in content
    assert 'GovernanceResponse = { "architectureDecisions": Array<ArchitectureDecisionRecord>' in content
    assert "Array<never>" not in content
    assert '"projects_api_v1_projects_get": ProjectsListResponse' in content
    assert "export type ProjectRecord" in content
    assert "export type ProjectTemplateRecord" in content
    assert "export type WorkspaceRecord" in content
    assert 'ProjectsListResponse = { "projects": Array<ProjectRecord>' in content
    assert 'WorkspacesListResponse = { "workspaces": Array<WorkspaceRecord>' in content
    assert "export type EvidencePackageRecord" in content
    assert "export type TestResultRecord" in content
    assert "export type ArtifactRecord" in content
    assert 'EvidenceDetailResponse = { "artifacts": Array<ArtifactRecord>' in content
    assert "export type ArtifactFileRecord" in content
    assert "export type ExpiredArtifactRecord" in content
    assert "export type ArtifactRetentionResultRecord" in content
    assert 'ArtifactCleanupResponse = { "artifactRoot": string; "deletedFiles": Array<ArtifactFileRecord>' in content
    assert 'ArtifactRetentionPlanResponse = { "dryRun": boolean; "expiredArtifacts": Array<ExpiredArtifactRecord>' in content
    assert 'ArtifactRetentionActionResponse = { "action": string; "artifacts": Array<ArtifactRetentionResultRecord> }' in content
    assert '"list_workflows_api_v1_workflows_get": WorkflowsListResponse' in content
    assert '"get_workflow_api_v1_workflows__workflow_id__get": WorkflowDetailResponse' in content
    assert '"list_agent_profiles_api_v1_agent_profiles_get": AgentProfilesListResponse' in content
    assert '"list_model_policies_api_v1_model_policies_get": ModelPoliciesListResponse' in content
    assert '"list_workspaces_api_v1_workspaces_get": WorkspacesListResponse' in content
    assert "export type PromptTemplateRecord" in content
    assert 'PromptTemplatesListResponse = { "promptTemplates": Array<PromptTemplateRecord> }' in content
    assert "export type McpServerRecord" in content
    assert 'McpServerResponse = { "mcpServer": McpServerRecord }' in content
    assert 'IntegrationsListResponse = { "integrations": Array<IntegrationRecord>; "mcpServers": Array<McpServerRecord>' in content
    assert "export type IdeConnectionRecord" in content
    assert 'IdeConnectionsListResponse = { "ideConnections": Array<IdeConnectionRecord> }' in content
    assert "export type RetrievalSearchResultRecord" in content
    assert "export type RetrievalIndexSummary" in content
    assert 'RetrievalSearchResponse = { "results": Array<RetrievalSearchResultRecord> }' in content
    assert 'RetrievalReindexResponse = { "index": RetrievalIndexSummary }' in content
    assert "export type SkillRecord" in content
    assert 'SkillsListResponse = { "skills": Array<SkillRecord> }' in content
    assert 'SkillsSyncResponse = { "skills": Array<SkillRecord>; "synced": number }' in content
    assert "export type SecurityPosture" in content
    assert "export type OpenDesignStatus" in content
    assert "export type CliAdaptersStatus" in content
    assert "export type OllamaRuntimeProviderStatus" in content
    assert "export type CliRuntimeProviderStatus" in content
    assert "export type ApiRuntimeProviderStatus" in content
    assert (
        'RuntimeProvidersResponse = { "api": ApiRuntimeProviderStatus; '
        '"cli": CliRuntimeProviderStatus; "ollama": OllamaRuntimeProviderStatus;'
    ) in content
    assert "export type DockerSandboxStatus" in content
    assert "export type RestrictedSubprocessStatus" in content
    assert 'SandboxStatusResponse = { "docker": DockerSandboxStatus; "restrictedSubprocess": RestrictedSubprocessStatus }' in content
    assert "export type ExternalTelemetryStatus" in content
    assert 'TelemetryStatusResponse = { "externalExporter": ExternalTelemetryStatus }' in content
    assert 'WorkspaceArchiveResponse = { "evidencePackage": EvidencePackageRecord; "workspace": WorkspaceRecord }' in content
    assert '"skills": Array<SkillRecord>' in overview_line
    assert '"security": SecurityPosture' in overview_line
    assert '"openDesign": OpenDesignStatus' in overview_line
    response_section = content.split("export type OperationResponseBodies = {", 1)[1].split("};", 1)[0]
    assert ": JsonObject," not in response_section
    assert "export function buildApiPath" in content
    assert "export async function requestGeneratedOperation" in content

    api_client = (ROOT / "local-control-center" / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "requestGeneratedOperation" in api_client
    assert "overview_api_v1_overview_get" in api_client
    assert '"/api/v1/overview"' not in api_client


def test_openapi_generation_script_documents_no_network_dependency() -> None:
    script = ROOT / "local-control-center" / "scripts" / "generate_openapi_client.py"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "create_app" in content
    assert "No network access" in content
    assert "ignore_cleanup_errors=True" in content
    assert "openapi.ts" in content


def test_playwright_uses_isolated_state_for_mutating_e2e() -> None:
    config = ROOT / "playwright.config.mjs"
    assert config.exists()
    content = config.read_text(encoding="utf-8")
    assert "playwright-control-center-${process.pid}.sqlite" in content
    assert "workers: 1" in content
    assert "reuseExistingServer: false" in content
