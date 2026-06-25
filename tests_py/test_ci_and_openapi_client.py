from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _generated_type_line(content: str, type_name: str) -> str:
    return next(line for line in content.splitlines() if line.startswith(f"export type {type_name} = "))


def test_github_workflows_are_removed_and_branch_protection_is_explicit() -> None:
    workflows_dir = ROOT / ".github" / "workflows"
    if workflows_dir.exists():
        assert not list(workflows_dir.glob("*.yml"))
        assert not list(workflows_dir.glob("*.yaml"))

    script = ROOT / "local-control-center" / "scripts" / "protect-repository-branches.ps1"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "/repos/$Owner/$Repo/rulesets" in content
    assert "refs/heads/*" in content
    assert "refs/heads/dev" in content
    assert "Assert-GhSuccess" in content
    assert "$LASTEXITCODE" in content
    assert '"type" = "creation"' in content
    assert '"type" = "update"' in content
    assert '"type" = "deletion"' in content
    assert '"type" = "pull_request"' in content
    assert "required_approving_review_count = 1" in content
    assert "require_code_owner_review = $true" in content
    assert "required_status_checks" not in content
    assert '"type" = "non_fast_forward"' in content


def test_legacy_branch_protection_script_is_warning_only_wrapper() -> None:
    legacy_script = ROOT / "local-control-center" / "scripts" / "protect-main-branch.ps1"
    replacement_script = ROOT / "local-control-center" / "scripts" / "protect-repository-branches.ps1"

    assert legacy_script.exists()
    assert replacement_script.exists()
    content = legacy_script.read_text(encoding="utf-8")
    assert "DEPRECATED" in content
    assert "Removal date: 2026-09-01" in content
    assert "protect-repository-branches.ps1" in content
    assert "Write-Warning" in content
    assert "/repos/$Owner/$Repo/rulesets" not in content
    assert '"type" = "creation"' not in content
    assert "required_approving_review_count" not in content


def test_generated_openapi_client_is_checked_in_and_v1_only() -> None:
    generated = ROOT / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts"
    assert generated.exists()
    content = generated.read_text(encoding="utf-8")
    assert "Generated from FastAPI OpenAPI" in content
    assert "/api/v1/workflows" in content
    assert "/api/v1/agent-profiles" in content
    assert "/api/v1/integrations/mcp/register" in content
    assert "/api/v1/integrations/mcp/tools" not in content
    assert "/api/v1/integrations/mcp/call" not in content
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
    assert (
        '"start_workflow_api_v1_workflows__workflow_id__start_post": WorkflowStatusChangeRequest' in content
    )
    assert '"upsert_agent_profile_api_v1_agent_profiles_post": AgentProfileUpsertRequest' in content
    assert '"create_job_api_v1_jobs_post": JobCreateRequest' in content
    assert (
        '"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": ApprovalReasonRequest'
        in content
    )
    assert '"cancel_job_api_v1_jobs__job_id__cancel_post": OptionalReasonRequest' in content
    assert (
        '"create_architecture_decision_api_v1_architecture_decisions_post": ArchitectureDecisionCreateRequest'
        in content
    )
    assert '"create_risk_api_v1_risks_post": RiskCreateRequest' in content
    assert '"update_risk_api_v1_risks__risk_id__patch": RiskUpdateRequest' in content
    assert '"create_next_step_api_v1_next_steps_post": NextStepCreateRequest' in content
    assert '"update_next_step_api_v1_next_steps__step_id__patch": NextStepUpdateRequest' in content
    assert '"allocate_workspace_api_v1_workspaces_post": WorkspaceAllocateRequest' in content
    assert "export type DevcontainerMetadata" in content
    assert '"devcontainer"?: DevcontainerMetadata | null' in content
    assert (
        '"archive_workspace_api_v1_workspaces__workspace_id__archive_post": WorkspaceArchiveRequest'
        in content
    )
    assert '"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerRegisterRequest' in content
    assert '"upsert_ide_connection_api_v1_ide_connections_post": IdeConnectionUpsertRequest' in content
    assert '"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluateRequest' in content
    assert (
        '"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": SandboxProfilePatchRequest'
        in content
    )
    assert (
        '"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": RequiredReasonRequest'
        in content
    )
    assert (
        '"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": RequiredReasonRequest'
        in content
    )
    assert '"create_project_api_v1_projects_post": ProjectCreateRequest' in content
    assert '"create_session_api_v1_sessions_post": SessionCreateRequest' in content
    assert '"create_chat_api_v1_chats_post": ChatCreateRequest' in content
    assert '"create_pipeline_api_v1_pipelines_post": PipelineCreateRequest' in content
    assert '"create_memory_api_v1_memory_post": MemoryCreateRequest' in content
    assert '"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchRequest' in content
    assert '"retrieval_reindex_api_v1_retrieval_reindex_post": RetrievalReindexRequest' in content
    assert '"upsert_prompt_api_v1_prompts_post": PromptUpsertRequest' in content
    assert '"create_agent_run_api_v1_agent_runs_post": AgentRunCreateRequest' in content
    assert '"overview_api_v1_model_gateway_overview_get": ModelGatewayOverviewResponse' in content
    assert '"list_providers_api_v1_model_gateway_providers_get": ProviderAccountsListResponse' in content
    assert '"create_role_policy_api_v1_model_gateway_role_policies_post": RolePolicyUpsertRequest' in content
    assert '"route_preview_api_v1_model_gateway_route_preview_post": RoutingPreviewRequest' in content
    assert '"route_preview_api_v1_model_gateway_route_preview_post": RoutingPreviewResponse' in content
    assert '"route_execute_api_v1_model_gateway_route_execute_post": RouteExecuteResponse' in content
    assert "route_execute_mock" not in content
    assert "/api/v1/model-gateway/route/execute-mock" not in content
    assert "list_model_providers_api_v1_model_providers_get" not in content
    assert "list_model_policies_api_v1_model_policies_get" not in content
    assert "upsert_model_policy_api_v1_model_policies_post" not in content
    assert "/api/v1/model-providers" not in content
    assert "/api/v1/model-policies" not in content
    assert '"list_benchmarks_api_v1_model_gateway_benchmarks_get": ModelBenchmarksListResponse' in content
    assert (
        '"create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post": ModelBenchmarkOutcomeCreateRequest'
        in content
    )
    outcome_create_line = next(
        line
        for line in content.splitlines()
        if line.startswith("export type ModelBenchmarkOutcomeCreateRequest = ")
    )
    outcome_record_line = next(
        line for line in content.splitlines() if line.startswith("export type ModelBenchmarkOutcomeRecord = ")
    )
    benchmark_record_line = next(
        line for line in content.splitlines() if line.startswith("export type ModelBenchmarkRecord = ")
    )
    assert (
        '"provenance"?: "operator_reported" | "automated_run" | "release_validation"' in outcome_create_line
    )
    assert '"provenance": "operator_reported" | "automated_run" | "release_validation"' in outcome_record_line
    assert '"objectiveTasksAttempted": number' in benchmark_record_line
    assert '"operatorReportedTasks": number' in benchmark_record_line
    assert '"provenanceCounts": JsonObject' in benchmark_record_line
    assert '"sync_skills_api_v1_skills_sync_post": SkillsSyncRequest' in content
    assert '"create_evidence_api_v1_evidence_post": EvidenceCreateRequest' in content
    assert '"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactIngestRequest' in content
    assert '"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": ArtifactCleanupRequest' in content
    assert (
        '"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": ArtifactRetentionPlanRequest'
        in content
    )
    assert (
        '"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": ArtifactRetentionActionRequest'
        in content
    )
    assert '"overview_api_v1_overview_get": OverviewResponse' in content
    overview_line = next(
        line for line in content.splitlines() if line.startswith("export type OverviewResponse = ")
    )
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
    assert '"permissionGrant"?: ApprovalGrantRecord | null' in content
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
    assert '"routingProfileId"?: null | string' in content
    assert '"allowedProviders": Array<string>' in content
    assert "export type ModelPolicyRecord" in content
    assert "export type ActionRequestRecord" in content
    assert "export type EventRecord" in content
    assert '"severity"?: string' in content
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
    evidence_line = next(
        line for line in content.splitlines() if line.startswith("export type EvidencePackageRecord = ")
    )
    for required_field in (
        '"workflowRunId": null | string',
        '"jobId": null | string',
        '"agentRunId": null | string',
        '"workspaceId": null | string',
        '"runtimeId": null | string',
        '"runtimeHealth": JsonObject',
        '"modelCalls": Array<JsonObject>',
        '"toolCalls": Array<JsonObject>',
        '"policyDecisions": Array<JsonObject>',
        '"approvals": Array<JsonObject>',
        '"artifacts": Array<JsonObject>',
        '"diffSummary": JsonObject',
        '"hashes": JsonObject',
        '"createdAt": string',
    ):
        assert required_field in evidence_line
    assert "export type TestResultRecord" in content
    assert "export type ArtifactRecord" in content
    assert 'EvidenceDetailResponse = { "artifacts": Array<ArtifactRecord>' in content
    assert "export type ArtifactFileRecord" in content
    assert "export type ExpiredArtifactRecord" in content
    assert "export type ArtifactRetentionResultRecord" in content
    assert (
        'ArtifactCleanupResponse = { "artifactRoot": string; "deletedFiles": Array<ArtifactFileRecord>'
        in content
    )
    assert (
        'ArtifactRetentionPlanResponse = { "dryRun": boolean; "expiredArtifacts": Array<ExpiredArtifactRecord>'
        in content
    )
    assert (
        'ArtifactRetentionActionResponse = { "action": string; "artifacts": Array<ArtifactRetentionResultRecord> }'
        in content
    )
    assert '"list_workflows_api_v1_workflows_get": WorkflowsListResponse' in content
    assert '"get_workflow_api_v1_workflows__workflow_id__get": WorkflowDetailResponse' in content
    assert '"list_agent_profiles_api_v1_agent_profiles_get": AgentProfilesListResponse' in content
    assert '"list_workspaces_api_v1_workspaces_get": WorkspacesListResponse' in content
    assert "export type PromptTemplateRecord" in content
    assert 'PromptTemplatesListResponse = { "promptTemplates": Array<PromptTemplateRecord> }' in content
    assert "export type McpServerRecord" in content
    assert 'McpServerResponse = { "mcpServer": McpServerRecord }' in content
    assert (
        'IntegrationsListResponse = { "integrations": Array<IntegrationRecord>; "mcpServers": Array<McpServerRecord>'
        in content
    )
    assert "export type IdeConnectionRecord" in content
    assert 'IdeConnectionsListResponse = { "ideConnections": Array<IdeConnectionRecord> }' in content
    assert "export type RetrievalSearchResultRecord" in content
    assert "export type RetrievalIndexSummary" in content
    assert 'RetrievalReindexRequest = { "projectId": string }' in content
    assert '"projectId": string; "reason": string; "status": string' in content
    assert (
        'RetrievalSearchResponse = { "reason": string; "results": Array<RetrievalSearchResultRecord>; "status": string }'
        in content
    )
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
    assert "export type RuntimeProviderStatus" in content
    assert "export type DeveloperAgentStatus" in content
    assert "export type DevOpsAgentStatus" in content
    assert "export type DevOpsAgentRunRequest" in content
    assert "export type DevOpsAgentRunResponse" in content
    assert "export type QAAgentRunRequest" in content
    assert "export type QAAgentRunResponse" in content
    assert "export type SecurityAgentStatus" in content
    assert "export type SecurityAgentRunRequest" in content
    assert "export type SecurityAgentRunResponse" in content
    assert "export type ResearchAgentStatus" in content
    assert "export type ResearchAgentRunRequest" in content
    assert "export type ResearchAgentRunResponse" in content
    assert "export type ArchitectAgentStatus" in content
    assert "export type ArchitectAgentRunRequest" in content
    assert "export type ArchitectAgentRunResponse" in content
    assert (
        'RuntimeProvidersResponse = { "api": ApiRuntimeProviderStatus; '
        '"cli": CliRuntimeProviderStatus; "developerAgent": DeveloperAgentStatus; '
        '"ollama": OllamaRuntimeProviderStatus; '
        '"providers": Array<RuntimeProviderStatus>;'
    ) in content
    assert "export type IssueToPatchRequest" in content
    assert "export type IssueToPatchResponse" in content
    assert (
        '"developer_agent_status_api_v1_agents_developer_status_get": DeveloperAgentStatusResponse' in content
    )
    assert '"run_developer_agent_api_v1_agents_developer_runs_post": DeveloperAgentRunRequest' in content
    assert '"devops_agent_status_api_v1_agents_devops_status_get": DevOpsAgentStatusResponse' in content
    assert '"run_devops_agent_api_v1_agents_devops_runs_post": DevOpsAgentRunRequest' in content
    assert '"run_qa_agent_api_v1_agents_qa_runs_post": QAAgentRunRequest' in content
    assert '"security_agent_status_api_v1_agents_security_status_get": SecurityAgentStatusResponse' in content
    assert '"run_security_agent_api_v1_agents_security_runs_post": SecurityAgentRunRequest' in content
    assert '"research_agent_status_api_v1_agents_research_status_get": ResearchAgentStatusResponse' in content
    assert '"run_research_agent_api_v1_agents_research_runs_post": ResearchAgentRunRequest' in content
    assert (
        '"architect_agent_status_api_v1_agents_architect_status_get": ArchitectAgentStatusResponse' in content
    )
    assert '"run_architect_agent_api_v1_agents_architect_runs_post": ArchitectAgentRunRequest' in content
    assert "export type DockerSandboxStatus" in content
    assert "export type RestrictedSubprocessStatus" in content
    assert (
        'SandboxStatusResponse = { "docker": DockerSandboxStatus; "restrictedSubprocess": RestrictedSubprocessStatus }'
        in content
    )
    assert "export type ExternalTelemetryStatus" in content
    assert 'TelemetryStatusResponse = { "externalExporter": ExternalTelemetryStatus }' in content
    assert (
        'WorkspaceArchiveResponse = { "evidencePackage": EvidencePackageRecord; "workspace": WorkspaceRecord }'
        in content
    )
    assert '"skills": Array<SkillRecord>' in overview_line
    assert '"security": SecurityPosture' in overview_line
    assert '"openDesign": OpenDesignStatus' in overview_line
    response_section = content.split("export type OperationResponseBodies = {", 1)[1].split("};", 1)[0]
    assert ": JsonObject," not in response_section
    assert "export function buildApiPath" in content
    assert "export async function requestGeneratedOperation" in content

    api_client = (ROOT / "local-control-center" / "web" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8"
    )
    assert "requestGeneratedOperation" in api_client
    assert "overview_api_v1_overview_get" in api_client
    assert "getDevOpsAgentStatus" in api_client
    assert "runDevOpsAgent" in api_client
    assert "run_qa_agent_api_v1_agents_qa_runs_post" in api_client
    assert "getSecurityAgentStatus" in api_client
    assert "runSecurityAgent" in api_client
    assert "getResearchAgentStatus" in api_client
    assert "runResearchAgent" in api_client
    assert "getArchitectAgentStatus" in api_client
    assert "runArchitectAgent" in api_client
    assert '"/api/v1/overview"' not in api_client


def test_generated_core_runtime_workflow_evidence_contracts_are_strict() -> None:
    generated = ROOT / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts"
    content = generated.read_text(encoding="utf-8")

    runtime_provider = _generated_type_line(content, "RuntimeProviderStatus")
    assert '"kind": "api" | "gateway" | "local" | "cli" | "manual"' in runtime_provider
    assert '"kind": string' not in runtime_provider

    action_request = _generated_type_line(content, "ActionRequestRecord")
    assert '"status": "pending" | "approved" | "denied" | "expired"' in action_request
    assert '"riskLevel": "low" | "medium" | "high" | "critical"' in action_request
    assert '"status": string' not in action_request
    assert '"riskLevel": string' not in action_request

    approval_grant = _generated_type_line(content, "ApprovalGrantRecord")
    assert '"status": "active" | "consumed" | "expired" | "revoked"' in approval_grant
    assert '"status": string' not in approval_grant
    assert 'PermissionGrantResponse = { "permissionGrant": ApprovalGrantRecord }' in content
    assert '"permissionGrants": Array<ApprovalGrantRecord>' in content

    agent_run = _generated_type_line(content, "AgentRunRecord")
    assert (
        '"status": "queued" | "running" | "completed" | "approved" | "failed" | "blocked" | "runtime_unavailable"'
        in agent_run
    )
    assert '"status": string' not in agent_run
    tool_call = _generated_type_line(content, "AgentToolCallRecord")
    assert (
        '"status": "pending" | "allowed" | "denied" | "requires_approval" | "approval_required" | '
        '"completed" | "failed" | "blocked" | "configuration_required" | "unavailable"'
    ) in tool_call
    assert '"status": string' not in tool_call
    model_call = _generated_type_line(content, "ModelCallRecord")
    assert '"status": "planned" | "completed" | "failed" | "blocked" | "unavailable"' in model_call
    assert '"status": string' not in model_call

    evidence_package = _generated_type_line(content, "EvidencePackageRecord")
    evidence_source_enum = (
        '"evidenceSource": "operator_attested" | "evidence_collected" | '
        '"qa_passed_by_command" | "verified_completion"'
    )
    assert evidence_source_enum in evidence_package
    assert '"evidenceSource": string' not in evidence_package
    assert (
        '"qaVerdict": "not_started" | "passed" | "failed" | "blocked" | "needs_human_review"'
        in evidence_package
    )
    assert '"qaVerdict": string' not in evidence_package
    evidence_create = _generated_type_line(content, "EvidenceCreateRequest")
    assert evidence_source_enum.replace('"evidenceSource"', '"evidenceSource"?') in evidence_create
    assert '"evidenceSource"?: string' not in evidence_create
    assert (
        '"qaVerdict"?: "not_started" | "passed" | "failed" | "blocked" | "needs_human_review"'
        in evidence_create
    )
    assert '"qaVerdict"?: string' not in evidence_create

    artifact = _generated_type_line(content, "ArtifactRecord")
    assert (
        '"kind": "execution_log" | "screenshot" | "test_report" | "qa_report" | "generic_artifact"'
        in artifact
    )
    assert '"kind": string' not in artifact

    job = _generated_type_line(content, "JobRecord")
    assert (
        '"status": "queued" | "running" | "approval_required" | "completed" | "approved" | "failed" | "cancelled"'
        in job
    )

    workflow = _generated_type_line(content, "WorkflowRecord")
    assert '"kind": "idea_to_pr" | "project_discovery" | "issue_to_patch" | "issue_to_pr"' in workflow
    assert (
        '"status": "queued" | "running" | "paused" | "completed" | "failed" | "cancelled" | "blocked" | "runtime_unavailable" | "qa_failed" | "evidence_ready" | "approved_for_integration" | "promotion_failed" | "promoted_to_branch" | "pr_created"'
        in workflow
    )
    workflow_run = _generated_type_line(content, "WorkflowRunRecord")
    assert (
        '"status": "running" | "completed" | "failed" | "cancelled" | "blocked" | "runtime_unavailable" | "qa_failed" | "evidence_ready" | "approved_for_integration" | "promotion_failed" | "promoted_to_branch" | "pr_created"'
        in workflow_run
    )
    workflow_step = _generated_type_line(content, "WorkflowStepRecord")
    assert '"riskLevel"?: "low" | "medium" | "high" | "critical" | null' in workflow_step
    assert (
        '"status": "pending" | "ready" | "running" | "completed" | "failed" | "blocked" | "skipped"'
        in workflow_step
    )
    overview = _generated_type_line(content, "OverviewResponse")
    assert '"workflowEvents": Array<WorkflowEventRecord>' in overview
    workflow_event = _generated_type_line(content, "WorkflowEventRecord")
    assert '"workflowStepId"?: null | string' in workflow_event
    assert '"payload": JsonObject' in workflow_event
    assert "export type WorkflowRunDetail" in content
    workflow_detail = _generated_type_line(content, "WorkflowDetailResponse")
    assert '"workflowRunDetails"?: Array<WorkflowRunDetail>' in workflow_detail

    api_client = (ROOT / "local-control-center" / "web" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8"
    )
    assert "approveIssueToPatch" in api_client
    assert "promotePatchToBranch" in api_client
    assert "createPullRequestFromPromotedBranch" in api_client
    assert "runIssueToPr" in api_client
    assert "approveIssueToPr" in api_client
    assert "promoteIssueToPrBranch" in api_client
    assert "createPullRequestFromIssueToPr" in api_client
    assert '"pullRequest"?: JsonObject | null' in _generated_type_line(content, "IssueToPatchResponse")
    assert '"dag": JsonObject' in _generated_type_line(content, "IssueToPrResponse")
    assert "run_issue_to_pr_api_v1_workflows_issue_to_pr_post" in content
    assert "approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post" in content
    assert "promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post" in content
    assert (
        "create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post"
        in content
    )
    assert "promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post" in content
    assert (
        "create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post"
        in content
    )
    assert "{ providers: Dictionary[] }" not in api_client
    assert "{ overview: Dictionary }" not in api_client
    assert (
        "apiRequest<{ providers: Dictionary[] }>('/api/v1/runtime/provider-configuration'" not in api_client
    )


def test_generated_credential_contract_and_settings_ui_are_secret_safe() -> None:
    generated = ROOT / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts"
    content = generated.read_text(encoding="utf-8")

    for operation in (
        '"list_credentials_api_v1_credentials_get": CredentialsListResponse',
        '"create_credential_api_v1_credentials_post": CredentialCreateRequest',
        '"create_credential_api_v1_credentials_post": CredentialResponse',
        '"list_credential_audit_api_v1_credentials_audit_get": CredentialAuditResponse',
        '"migrate_credentials_api_v1_credentials_migrate_post": CredentialMigrationResponse',
        '"validate_credential_api_v1_credentials__credential_id__validate_post": CredentialValidationResponse',
        '"rotate_credential_api_v1_credentials__credential_id__rotate_post": CredentialRotateRequest',
        '"delete_credential_api_v1_credentials__credential_id__delete": CredentialDeleteResponse',
    ):
        assert operation in content

    credential_record = _generated_type_line(content, "CredentialRecord")
    assert '"backendKind": string' in credential_record
    assert '"hasFingerprint": boolean' in credential_record
    assert '"value"' not in credential_record
    assert '"salt"' not in credential_record
    assert '"fingerprint":' not in credential_record

    api_client = (ROOT / "local-control-center" / "web" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8"
    )
    for symbol in (
        "getCredentials",
        "createCredential",
        "validateCredential",
        "rotateCredential",
        "deleteCredential",
        "migrateCredentials",
        "getCredentialAudit",
    ):
        assert symbol in api_client
    assert "apiRequest<" not in api_client.split("export function getCredentials", 1)[1].split(
        "export function", 1
    )[0]

    settings = (
        ROOT / "local-control-center" / "web" / "src" / "features" / "settings" / "SettingsPage.tsx"
    ).read_text(encoding="utf-8")
    assert "CredentialManagerPanel" in settings
    assert "value:" not in settings
    assert "fingerprint" not in settings.lower()
    assert "salt" not in settings.lower()


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
    runner = ROOT / "scripts" / "run-web-tests.mjs"
    cleanup = ROOT / "scripts" / "cleanup-playwright-webserver.mjs"
    assert config.exists()
    assert runner.exists()
    assert cleanup.exists()
    content = config.read_text(encoding="utf-8")
    runner_content = runner.read_text(encoding="utf-8")
    cleanup_content = cleanup.read_text(encoding="utf-8")
    assert "playwright-control-center-${process.pid}.sqlite" in content
    assert "workers: 1" in content
    assert "reuseExistingServer: false" in content
    assert "'--reporter=line'" in runner_content
    assert "'--list'" in runner_content
    assert "chunkTests(tests)" in runner_content
    assert "PLAYWRIGHT_TESTS_PER_CHUNK" in runner_content
    assert "PLAYWRIGHT_ARTIFACT_ROOT" in runner_content
    assert "'--output', chunkOutput" in runner_content
    assert "safePathSegment(project)" in runner_content
    assert "defaultDashboardPort" in runner_content
    assert "8600 + (process.pid % 900)" in runner_content
    assert "playwrightProjects = ['desktop', 'mobile']" in runner_content
    assert "`--project=${project}`" in runner_content
    assert "dashboardServerCommand" in runner_content
    assert "waitForDashboardHealth" in runner_content
    assert "PLAYWRIGHT_EXTERNAL_SERVER: '1'" in runner_content
    assert "dashboard server exited before healthcheck" in runner_content
    assert "projectCleanupStatus" in runner_content
    assert "Wait-Process" in cleanup_content
