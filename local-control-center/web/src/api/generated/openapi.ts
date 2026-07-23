// Generated from FastAPI OpenAPI. Do not edit by hand.
// No network access is required; run `corepack pnpm@10.24.0 run openapi:generate`.

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export type AIExecutionBranchPlan = { "maxTokens": number; "model": string; "providerId": string };
export type AIExecutionBranchResult = { "actualCostUsd"?: null | number; "branchId": string; "content"?: null | string; "costStatus": "actual" | "free" | "unknown"; "errorCode"?: null | string; "estimatedCostUsd"?: null | number; "inputTokens"?: null | number; "latencyMs"?: null | number; "leaseId"?: null | string; "model": string; "outputTokens"?: null | number; "pricingSource": string; "providerId": string; "reservedTokens": number; "status": "blocked" | "completed" | "failed"; "totalTokens"?: null | number; "usageStatus": "actual" | "unknown" };
export type AIExecutionMessage = { "content": string; "role": "system" | "user" | "assistant" | "tool" };
export type AIExecutionPlan = { "branches": Array<AIExecutionBranchPlan>; "maxParallelism"?: number; "messages": Array<AIExecutionMessage>; "minSuccessful"?: number; "projectId": string; "strategy"?: "single" | "parallel_compare" | "quorum"; "temperature"?: null | number };
export type AIExecutionResponse = { "execution": AIExecutionResult };
export type AIExecutionResult = { "branches": Array<AIExecutionBranchResult>; "completedAt": string; "createdAt": string; "executionId": string; "failedBranches": number; "maxParallelism": number; "minSuccessful": number; "projectId": string; "status": "blocked" | "completed" | "partial" | "failed"; "strategy": "single" | "parallel_compare" | "quorum"; "succeeded": boolean; "successfulBranches": number };
export type AcceptanceCriterionRecord = { "createdAt": string; "criterion": string; "id": string; "metadata": JsonObject; "projectId": string; "sequence": number; "status": string; "storyId": string; "updatedAt": string };
export type ActionRequestRecord = { "actionType": string; "command": string; "commandArgv"?: Array<string>; "decidedAt"?: null | string; "decidedBy"?: null | string; "diffRefs"?: Array<JsonValue>; "evidenceRefs"?: Array<string>; "expiresAt"?: null | string; "id": string; "jobId": string; "payload": JsonObject; "projectId": string; "reason": string; "requestedAt": string; "riskLevel": "low" | "medium" | "high" | "critical"; "runtime"?: JsonObject; "runtimeId"?: null | string; "status": "pending" | "approved" | "denied" | "expired"; "workspace"?: JsonObject; "workspaceId"?: null | string; "workspacePath"?: null | string };
export type AgentAssignmentRecord = { "agentId": string; "assignedAt": string; "assignedBy": string; "canonicalArtifactId": string; "createdAt": string; "handoffId": string; "id": string; "inputSchema": JsonObject; "metadata": JsonObject; "outputSchema": JsonObject; "projectId": string; "releasedAt"?: null | string; "reviewRequired": boolean; "role": string; "status": string; "taskId": string; "updatedAt": string };
export type AgentProfileProjectOverrideRecord = { "agentProfileId": string; "createdAt": string; "projectId": string; "reason": string; "status": "active" | "disabled"; "updatedAt": string };
export type AgentProfileProjectOverrideRequest = { "allowedProviders"?: Array<string> | null; "allowedRuntimes"?: Array<string> | null; "allowedSkills"?: Array<string> | null; "allowedTools"?: Array<string> | null; "defaultRuntimePolicy"?: JsonObject | null; "maxCostPerRun"?: null | number; "maxRuntimeSeconds"?: null | number; "maxTokensPerRun"?: null | number; "qualityGates"?: Array<JsonValue> | null; "reason": string; "requiresApprovalOverUsd"?: null | number; "reviewerPolicy"?: JsonObject | null; "runtimeMode"?: "api" | "cli" | "ollama" | "hybrid" | "manual" | null; "status"?: "active" | "disabled" };
export type AgentProfileRecord = { "allowApi": boolean; "allowCli": boolean; "allowRemote": boolean; "allowedProviders": Array<string>; "allowedRuntimes": Array<string>; "allowedSkills": Array<string>; "allowedTools": Array<string>; "costLimits": JsonObject; "createdAt": string; "defaultRuntimePolicy": JsonObject; "id": string; "maxCostPerRun": number; "maxRuntimeSeconds": number; "maxTokensPerRun": number; "memoryScope": string; "modelPolicyId"?: null | string; "name": string; "outputSchema": JsonObject; "permissionProfile": "plan" | "dev_safe" | "qa" | "release"; "projectOverride"?: AgentProfileProjectOverrideRecord | null; "qualityGates": Array<JsonValue>; "requiresApprovalOverUsd"?: null | number; "reviewerPolicy": JsonObject; "role": "analyst" | "assessor" | "aido_lead" | "product_owner" | "project_manager" | "scrum_master" | "architect" | "technical_lead" | "technical_lead_shadow" | "developer" | "backend_engineer" | "frontend_engineer" | "mobile_engineer" | "database_engineer" | "data_engineer" | "implementer" | "devops" | "devops_engineer" | "qa" | "qa_engineer" | "qa_reviewer" | "security_engineer" | "security_reviewer" | "pentester" | "researcher" | "release_manager"; "roleModelPolicyId"?: null | string; "routingProfileId"?: null | string; "runtimeAvailability"?: AgentRuntimeAvailability; "runtimeMode": "api" | "cli" | "ollama" | "hybrid" | "manual"; "runtimeType": "api" | "cli" | "ollama" | "hybrid" | "manual"; "status": "active" | "disabled"; "updatedAt": string };
export type AgentProfileResponse = { "agentProfile": AgentProfileRecord };
export type AgentProfileUpsertRequest = { "allowApi"?: boolean; "allowCli"?: boolean; "allowRemote"?: boolean; "allowedProviders"?: Array<string>; "allowedRuntimes"?: Array<string>; "allowedSkills"?: Array<string>; "allowedTools"?: Array<string>; "defaultRuntimePolicy"?: JsonObject; "id": string; "maxCostPerRun"?: number; "maxRuntimeSeconds"?: number; "maxTokensPerRun"?: number; "memoryScope"?: string; "modelPolicyId"?: null | string; "name"?: null | string; "outputSchema"?: JsonObject; "permissionProfile"?: "plan" | "dev_safe" | "qa" | "release"; "qualityGates"?: Array<JsonValue>; "requiresApprovalOverUsd"?: null | number; "reviewerPolicy"?: JsonObject; "role"?: "analyst" | "assessor" | "aido_lead" | "product_owner" | "project_manager" | "scrum_master" | "architect" | "technical_lead" | "technical_lead_shadow" | "developer" | "backend_engineer" | "frontend_engineer" | "mobile_engineer" | "database_engineer" | "data_engineer" | "implementer" | "devops" | "devops_engineer" | "qa" | "qa_engineer" | "qa_reviewer" | "security_engineer" | "security_reviewer" | "pentester" | "researcher" | "release_manager"; "roleModelPolicyId"?: null | string; "routingProfileId"?: null | string; "runtimeMode"?: "api" | "cli" | "ollama" | "hybrid" | "manual"; "runtimeType"?: "api" | "cli" | "ollama" | "hybrid" | "manual" | null; "status"?: "active" | "disabled" };
export type AgentProfilesListResponse = { "agentProfiles": Array<AgentProfileRecord> };
export type AgentRunCreateRequest = { "agentProfileId": string; "input"?: JsonObject; "jobId"?: null | string; "projectId": string; "taskId"?: string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type AgentRunRecord = { "createdAt": string; "id": string; "input": JsonObject; "jobId"?: null | string; "metadata": JsonObject; "output": JsonObject; "projectId": string; "status": "queued" | "running" | "completed" | "approved" | "failed" | "blocked" | "runtime_unavailable" | "runtime_failed" | "timed_out" | "qa_failed" | "evidence_ready" | "approval_required" | "awaiting_permission" | "cancelled"; "updatedAt": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type AgentRunResponse = { "agentRun": AgentRunRecord };
export type AgentRunsListResponse = { "agentRuns": Array<AgentRunRecord> };
export type AgentRuntimeAvailability = { "available"?: boolean; "blockedReason"?: string; "candidateProviderIds"?: Array<string>; "requiredCapabilities"?: Array<string>; "selectedProviderId"?: null | string; "status"?: "available" | "blocked" | "disabled" | "configuration_required" | "unknown" };
export type AgentTaskRecord = { "category": string; "createdAt": string; "description"?: null | string; "estimateHours"?: null | number; "id": string; "metadata": JsonObject; "priority": string; "projectId": string; "role": string; "status": string; "storyId"?: null | string; "title": string; "updatedAt": string; "version": number };
export type AgentToolCallRecord = { "agentRunId": string; "createdAt": string; "id": string; "payload": JsonObject; "status": "pending" | "allowed" | "denied" | "requires_approval" | "approval_required" | "completed" | "failed" | "blocked" | "configuration_required" | "unavailable"; "toolName": string; "updatedAt": string };
export type AgentsListResponse = { "agents": Array<CatalogAgentRecord> };
export type ApiRuntimeProviderStatus = { "adapters": Array<string>; "available": boolean; "provider": string };
export type ApprovalGrantRecord = { "actionRequestId"?: null | string; "agentId"?: null | string; "command"?: null | string; "commandArgv"?: Array<string>; "consumedAt"?: null | string; "consumedByAgentRunId"?: null | string; "expiresAt"?: null | string; "grantedAt"?: null | string; "grantedBy"?: null | string; "id": string; "jobId"?: null | string; "path"?: null | string; "payload": JsonObject; "permissionDecisionId"?: null | string; "projectId"?: null | string; "reason": string; "revokeReason"?: null | string; "revokedAt"?: null | string; "revokedBy"?: null | string; "runtimeId"?: null | string; "status": "active" | "consumed" | "expired" | "revoked"; "tool"?: null | string; "workspaceId"?: null | string };
export type ApprovalReasonRequest = { "reason": string };
export type ApprovalsListResponse = { "actionRequests": Array<ActionRequestRecord> };
export type ArchitectAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean; "verdictSource": string };
export type ArchitectAgentRunRequest = { "approvalGrantId"?: null | string; "diffArtifactId": string; "evidenceRefs"?: Array<string>; "metadata"?: JsonObject; "model"?: null | string; "preferredRuntime"?: null | string; "projectId": string; "relevantDocs"?: Array<JsonObject>; "riskRegister"?: Array<JsonObject>; "taskId"?: string; "testResults"?: Array<JsonObject>; "workflowContext"?: JsonObject; "workspaceId": string };
export type ArchitectAgentRunResponse = { "agentRun": AgentRunRecord; "architectAgent": ArchitectAgentStatus; "architectureDecision"?: JsonObject | null; "evidencePackage": JsonObject; "job": JsonObject; "output"?: JsonObject | null; "reason": string; "riskEntries"?: Array<JsonObject>; "runtime": JsonObject; "runtimeResult": JsonObject; "status": string; "workspace": JsonObject };
export type ArchitectAgentStatus = { "candidateRuntimeIds"?: Array<string>; "contract": ArchitectAgentContract; "executable": boolean; "id": string; "reason": string; "selectedRuntimeId"?: null | string; "status": string };
export type ArchitectAgentStatusResponse = { "architectAgent": ArchitectAgentStatus };
export type ArchitectureDecisionCreateRequest = { "consequences"?: Array<JsonValue> | string; "context"?: string; "decision"?: string; "linkedRiskIds"?: Array<string>; "metadata"?: JsonObject; "nextStepIds"?: Array<string>; "projectId": string; "status"?: "proposed" | "accepted" | "rejected" | "superseded" | "deprecated"; "title": string };
export type ArchitectureDecisionRecord = { "consequences"?: Array<JsonValue> | string; "context": string; "createdAt": string; "decision": string; "id": string; "linkedRiskIds": Array<string>; "metadata": JsonObject; "nextStepIds": Array<string>; "projectId": string; "status": "proposed" | "accepted" | "rejected" | "superseded" | "deprecated"; "title": string; "updatedAt": string };
export type ArchitectureDecisionResponse = { "architectureDecision": ArchitectureDecisionRecord };
export type ArchitectureDecisionsListResponse = { "architectureDecisions": Array<ArchitectureDecisionRecord> };
export type ArtifactCleanupRequest = { "dryRun"?: boolean };
export type ArtifactCleanupResponse = { "artifactRoot": string; "deletedFiles": Array<ArtifactFileRecord>; "dryRun": boolean; "keptReferencedFiles": number; "orphanFiles": Array<ArtifactFileRecord> };
export type ArtifactFileRecord = { "path": string; "sizeBytes": number };
export type ArtifactIngestRequest = { "content"?: null | string; "contentBase64"?: null | string; "kind": "generated_image" | "execution_log" | "screenshot" | "test_report" | "qa_report" | "generic_artifact" | "git_patch" | "git_status" | "git_diff" | "security_findings" | "model_call" | "evidence_manifest" | "devops_command_report" | "devops_report" | "security_report" | "research_source" | "research_report" | "cli_stdout" | "cli_stderr" | "cli_runtime_log" | "workspace_patch_manifest" | "project_assessment" | "product_owner_manifest" | "product_owner_output" | "product_brief" | "product_backlog"; "mimeType"?: null | string; "name"?: null | string };
export type ArtifactRecord = { "createdAt": string; "evidencePackageId"?: null | string; "hash"?: null | string; "id": string; "kind": "generated_image" | "execution_log" | "screenshot" | "test_report" | "qa_report" | "generic_artifact" | "git_patch" | "git_status" | "git_diff" | "security_findings" | "model_call" | "evidence_manifest" | "devops_command_report" | "devops_report" | "security_report" | "research_source" | "research_report" | "cli_stdout" | "cli_stderr" | "cli_runtime_log" | "workspace_patch_manifest" | "project_assessment" | "product_owner_manifest" | "product_owner_output" | "product_brief" | "product_backlog"; "metadata": JsonObject; "path": string; "projectId": string };
export type ArtifactResponse = { "artifact": ArtifactRecord };
export type ArtifactRetentionActionRequest = { "action": string; "artifactIds": Array<string>; "now"?: null | string; "reason": string };
export type ArtifactRetentionActionResponse = { "action": string; "artifacts": Array<ArtifactRetentionResultRecord> };
export type ArtifactRetentionPlanRequest = { "dryRun"?: boolean; "now"?: null | string };
export type ArtifactRetentionPlanResponse = { "dryRun": boolean; "expiredArtifacts": Array<ExpiredArtifactRecord>; "now": string; "riskIds": Array<string> };
export type ArtifactRetentionResultRecord = { "createdAt": string; "evidencePackageId"?: null | string; "hash"?: null | string; "id": string; "kind": "generated_image" | "execution_log" | "screenshot" | "test_report" | "qa_report" | "generic_artifact" | "git_patch" | "git_status" | "git_diff" | "security_findings" | "model_call" | "evidence_manifest" | "devops_command_report" | "devops_report" | "security_report" | "research_source" | "research_report" | "cli_stdout" | "cli_stderr" | "cli_runtime_log" | "workspace_patch_manifest" | "project_assessment" | "product_owner_manifest" | "product_owner_output" | "product_brief" | "product_backlog"; "metadata": JsonObject; "path": string; "projectId": string; "retentionAction": JsonObject };
export type ArtifactSummary = { "artifactId": string; "kind": string; "name"?: null | string; "path"?: null | string };
export type AssignmentConflictRecord = { "assignmentId": string; "createdAt": string; "disagreement": string; "finalResolution": string; "handoffId": string; "id": string; "metadata": JsonObject; "projectId": string; "raisedBy": string; "resolvedAt"?: null | string; "resolvedBy"?: null | string; "status": string; "updatedAt": string };
export type AssignmentHandoffRecord = { "artifactId": string; "assignmentId": string; "blockedReason": string; "createdAt": string; "fromAgentId": string; "id": string; "metadata": JsonObject; "projectId": string; "reviewRequired": boolean; "status": string; "toAgentId": string; "updatedAt": string };
export type AssignmentReviewRecord = { "assignmentId": string; "createdAt": string; "decision": string; "findings": Array<JsonValue>; "handoffId": string; "id": string; "policyRequired": boolean; "projectId": string; "resolvedAt"?: null | string; "reviewerAgentId": string; "status": string; "updatedAt": string };
export type AssignmentSummary = { "assignmentId": string; "assignmentStatus": string; "taskId": string; "taskTitle"?: null | string };
export type AssumptionRecord = { "briefId"?: null | string; "confidence"?: null | string; "createdAt": string; "id": string; "initiativeId"?: null | string; "metadata": JsonObject; "owner"?: null | string; "projectId": string; "sourceQuestionId"?: null | string; "statement": string; "status": string; "updatedAt": string; "validation"?: null | string };
export type AuditEventRecord = { "action": string; "actor": string; "createdAt": string; "id": string; "payload": JsonObject; "projectId"?: null | string; "target": string };
export type BudgetRulePatchRequest = { "actionOnExceed"?: string; "enabled"?: boolean; "id"?: null | string; "maxCostUsd"?: null | number; "maxTokens"?: null | number; "period"?: string; "scopeId"?: null | string; "scopeType"?: null | string };
export type BudgetRuleRecord = { "actionOnExceed": string; "createdAt": string; "enabled": boolean; "id": string; "maxCostUsd"?: null | number; "maxTokens"?: null | number; "period": string; "scopeId"?: null | string; "scopeType": string; "updatedAt": string };
export type BudgetRuleResponse = { "budgetRule": BudgetRuleRecord };
export type BudgetRuleUpsertRequest = { "actionOnExceed"?: string; "enabled"?: boolean; "id"?: null | string; "maxCostUsd"?: null | number; "maxTokens"?: null | number; "period"?: string; "scopeId"?: null | string; "scopeType"?: string };
export type BudgetRulesListResponse = { "budgetRules": Array<BudgetRuleRecord> };
export type CapabilityUsage = { "promptTokens"?: null | number; "totalTokens"?: null | number };
export type CatalogAgentRecord = { "capabilities": Array<JsonValue>; "createdAt": string; "id": string; "kind": string; "model": string; "name": string; "permissions": JsonObject; "providerId": string; "role": string; "teamId": string; "updatedAt": string };
export type ChatRecord = { "createdAt": string; "id": string; "metadata": JsonObject; "projectId": string; "prompt": string; "sessionId"?: null | string; "status": string; "title": string; "updatedAt": string };
export type ChatsListResponse = { "chats": Array<ChatRecord> };
export type ClarificationQuestionRecord = { "askedBy"?: null | string; "createdAt": string; "id": string; "initiativeId"?: null | string; "metadata": JsonObject; "priority": string; "projectId": string; "question": string; "sequence": number; "sessionId"?: null | string; "status": string; "updatedAt": string };
export type CliAdaptersStatus = { "cli_claude": boolean; "cli_codex": boolean };
export type CliRuntimeProviderStatus = { "adapters": CliAdaptersStatus; "available": boolean; "provider": string };
export type CliRuntimeRecord = { "executable"?: null | string; "id"?: null | string; "message"?: string; "runtime": string; "status": string; "version"?: null | string };
export type CliRuntimesListResponse = { "cliRuntimes": Array<CliRuntimeRecord> };
export type CliSessionCancelResponse = { "cancelled": boolean };
export type CliSessionEventRecord = { "artifactId"?: null | string; "cliSessionId": string; "createdAt": string; "id": string; "payload": JsonObject; "projectId"?: null | string; "seq": number; "type": string };
export type CliSessionEventsResponse = { "events": Array<CliSessionEventRecord>; "latestSeq": number; "running": boolean };
export type CliSessionRecord = { "agentId"?: null | string; "command": Array<JsonValue>; "createdAt": string; "envPolicy": JsonObject; "error"?: null | string; "executable": string; "finishedAt"?: null | string; "id": string; "logsArtifactId"?: null | string; "runtime": string; "startedAt"?: null | string; "status": string; "stderrArtifactId"?: null | string; "stdoutArtifactId"?: null | string; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string; "workspaceId": string };
export type CliSessionResponse = { "cliSession": CliSessionRecord };
export type CliSessionStartRequest = { "agentId"?: null | string; "argv": Array<string>; "branchName"?: null | string; "envPolicy"?: JsonObject | null; "runtime"?: null | string; "timeoutSeconds"?: null | number; "workspaceId": string; "worktreeId"?: null | string };
export type CliSessionStartResponse = { "id": string; "startedAt": string; "status": string };
export type CliSessionsListResponse = { "cliSessions": Array<CliSessionRecord> };
export type CostUsageRecord = { "amountUsd": number; "createdAt": string; "id": string; "metadata": JsonObject; "projectId": string; "scope": string };
export type CredentialAuditRecord = { "action": string; "actor": string; "backendKind": string; "createdAt": string; "credentialId": string; "detail": string; "id": string; "name": string; "outcome": string };
export type CredentialAuditResponse = { "audit": Array<CredentialAuditRecord> };
export type CredentialBackendStatus = { "configured": boolean; "default": boolean; "detail": string; "kind": string; "readOnly": boolean };
export type CredentialCreateRequest = { "actor"?: string; "authMode"?: string; "backendKind"?: null | string; "credentialRef"?: null | string; "label"?: null | string; "locator"?: null | string; "metadata"?: JsonObject; "name"?: null | string; "source"?: null | string; "value": string };
export type CredentialDeleteResponse = { "deleted": CredentialDeletedRecord };
export type CredentialDeletedRecord = { "deleted": boolean; "name": string };
export type CredentialMigrateRequest = { "actor"?: string };
export type CredentialMigrationResponse = { "report": JsonObject };
export type CredentialProviderUsageRecord = { "credentialRef": string; "displayName": string; "enabled": boolean; "providerId": string };
export type CredentialRecord = { "authMode": string; "backendKind": string; "createdAt": string; "credentialRef": string; "enabled": boolean; "fingerprintAlgo": string; "hasFingerprint": boolean; "id": string; "label": string; "lastRotatedAt": null | string; "lastValidatedAt": null | string; "locator": string; "metadata": JsonObject; "name": string; "providerUsages": Array<CredentialProviderUsageRecord>; "rotatedAt": null | string; "source": string; "status": string; "updatedAt": string };
export type CredentialResponse = { "credential": CredentialRecord };
export type CredentialRotateRequest = { "actor"?: string; "value": string };
export type CredentialValidationRecord = { "fingerprintMatches": boolean; "name": string; "present": boolean; "valid": boolean };
export type CredentialValidationResponse = { "validation": CredentialValidationRecord };
export type CredentialsListResponse = { "backends": Array<CredentialBackendStatus>; "credentials": Array<CredentialRecord> };
export type DevOpsAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean; "verdictSource": string };
export type DevOpsAgentRunRequest = { "buildScripts"?: Array<string>; "dockerHealthcheck"?: boolean; "metadata"?: JsonObject; "projectId": string; "qualityScripts"?: Array<string> | null; "taskId"?: string; "workspaceId": string };
export type DevOpsAgentRunResponse = { "agentRun": AgentRunRecord; "commands": Array<JsonObject>; "configArtifact": JsonObject; "configFindings": Array<JsonObject>; "contract": DevOpsAgentContract; "docker": JsonObject; "evidencePackage": JsonObject; "filesScanned": Array<JsonObject>; "job": JsonObject; "reason": string; "status": string; "verdict": string; "versions": JsonObject; "workspace": JsonObject };
export type DevOpsAgentStatus = { "contract": DevOpsAgentContract; "executable": boolean; "id": string; "reason": string; "status": string };
export type DevOpsAgentStatusResponse = { "devopsAgent": DevOpsAgentStatus };
export type DevcontainerMetadata = { "enabled"?: boolean; "features"?: Array<string>; "image"?: string; "templateId"?: string };
export type DeveloperAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean };
export type DeveloperAgentRunRequest = { "approvalGrantId"?: null | string; "instruction": string; "maxCostUsd"?: null | number; "metadata"?: JsonObject; "model"?: null | string; "preferredRuntime"?: null | string; "projectId": string; "qaCommands"?: Array<Array<string>>; "requireApproval"?: boolean; "taskId"?: string; "workspaceId": string };
export type DeveloperAgentRunResponse = { "agentRun": AgentRunRecord; "developerAgent": DeveloperAgentStatus; "diffSummary": JsonObject; "evidencePackage": JsonObject; "job": JsonObject; "qaResults": Array<JsonObject>; "reason": string; "runtime": JsonObject; "runtimeResult": JsonObject; "status": string; "workspace": JsonObject };
export type DeveloperAgentStatus = { "candidateRuntimeIds"?: Array<string>; "contract": DeveloperAgentContract; "executable": boolean; "id": string; "reason": string; "selectedRuntimeId"?: null | string; "status": string };
export type DeveloperAgentStatusResponse = { "developerAgent": DeveloperAgentStatus };
export type DeveloperDetails = { "input"?: JsonObject; "metadata"?: JsonObject; "output"?: JsonObject };
export type DirectoryPickerRequest = { "initialPath"?: null | string; "title"?: string };
export type DirectoryPickerResponse = { "reason"?: null | string; "selectedPath"?: null | string; "status": string };
export type DiscoverModelsResponse = { "models": Array<ModelCatalogRecord> };
export type DockerSandboxStatus = { "available": boolean; "defaultNetwork": string; "executable"?: null | string; "fallback": string; "hostMount": string; "mode": string; "policy": SandboxProfileRecord; "required": boolean; "writes": string };
export type EmbeddingResponse = { "data": Array<EmbeddingVector>; "model": string; "providerId": string; "usage"?: CapabilityUsage | null };
export type EmbeddingVector = { "embedding": Array<number>; "index": number; "object"?: string };
export type EpicRecord = { "createdAt": string; "description"?: null | string; "id": string; "metadata": JsonObject; "owner"?: null | string; "priority": string; "projectId": string; "status": string; "title": string; "updatedAt": string; "version": number };
export type EventRecord = { "createdAt": string; "id": string; "jobId"?: null | string; "payload": JsonObject; "projectId"?: null | string; "severity"?: string; "type": string };
export type EvidenceCreateRequest = { "acceptanceChecklist"?: Array<JsonValue>; "actualCostUsd"?: null | number; "agentId"?: null | string; "agentRunId"?: null | string; "approvals"?: Array<JsonObject>; "artifactIds"?: Array<string>; "artifacts"?: Array<JsonObject>; "diffRefs"?: Array<JsonValue>; "diffSummary"?: JsonObject; "estimatedCostUsd"?: null | number; "evidenceSource"?: "operator_attested" | "evidence_collected" | "qa_passed_by_command" | "verified_completion"; "hashes"?: JsonObject; "jobId"?: null | string; "latencyMs"?: null | number; "logs"?: Array<JsonValue>; "model"?: null | string; "modelCalls"?: Array<JsonObject>; "policyDecisions"?: Array<JsonObject>; "projectId": string; "providerId"?: null | string; "qaVerdict"?: "not_started" | "passed" | "failed" | "blocked" | "needs_human_review" | "evidence_collected" | "architecture_reviewed" | "devops_risk" | "devops_blocked" | "security_passed" | "security_blocked" | "skipped_with_reason" | "backlog_generated" | "brief_ready" | "blocked_pending_decisions"; "rework"?: boolean | null; "riskNotes"?: Array<JsonValue>; "role"?: null | string; "runtimeHealth"?: JsonObject; "runtimeId"?: null | string; "runtimeType"?: null | string; "screenshotRefs"?: Array<JsonValue>; "taskId"?: string; "testPlan"?: string; "testResultReports"?: Array<JsonObject>; "testResults"?: Array<JsonObject>; "toolCalls"?: Array<JsonObject>; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string; "workspaceId"?: null | string };
export type EvidenceDetailResponse = { "artifacts": Array<ArtifactRecord>; "evidencePackage": EvidencePackageRecord; "testResultRecords": Array<TestResultRecord> };
export type EvidenceListResponse = { "evidencePackages": Array<EvidencePackageRecord> };
export type EvidencePackageRecord = { "acceptanceChecklist": Array<JsonValue>; "agentId"?: null | string; "agentRunId": null | string; "approvals": Array<JsonObject>; "artifactIds"?: Array<string>; "artifacts": Array<JsonObject>; "createdAt": string; "diffRefs": Array<JsonValue>; "diffSummary": JsonObject; "evidenceSource": "operator_attested" | "evidence_collected" | "qa_passed_by_command" | "verified_completion"; "hashes": JsonObject; "id": string; "jobId": null | string; "logs": Array<JsonValue>; "modelCalls": Array<JsonObject>; "policyDecisions": Array<JsonObject>; "projectId": string; "qaVerdict": "not_started" | "passed" | "failed" | "blocked" | "needs_human_review" | "evidence_collected" | "architecture_reviewed" | "devops_risk" | "devops_blocked" | "security_passed" | "security_blocked" | "skipped_with_reason" | "backlog_generated" | "brief_ready" | "blocked_pending_decisions"; "riskNotes": Array<JsonValue>; "runtimeHealth": JsonObject; "runtimeId": null | string; "screenshotRefs": Array<JsonValue>; "taskId": string; "testPlan": string; "testResults": Array<JsonValue>; "toolCalls": Array<JsonObject>; "workflowRunId": null | string; "workflowStepId"?: null | string; "workspaceId": null | string };
export type EvidencePackageResponse = { "evidencePackage": EvidencePackageRecord };
export type ExpiredArtifactRecord = { "createdAt": string; "evidencePackageId"?: null | string; "expiresAt": string; "hash"?: null | string; "id": string; "kind": "generated_image" | "execution_log" | "screenshot" | "test_report" | "qa_report" | "generic_artifact" | "git_patch" | "git_status" | "git_diff" | "security_findings" | "model_call" | "evidence_manifest" | "devops_command_report" | "devops_report" | "security_report" | "research_source" | "research_report" | "cli_stdout" | "cli_stderr" | "cli_runtime_log" | "workspace_patch_manifest" | "project_assessment" | "product_owner_manifest" | "product_owner_output" | "product_brief" | "product_backlog"; "metadata": JsonObject; "path": string; "projectId": string; "retentionStatus": string };
export type ExternalTelemetryStatus = { "available": boolean; "enabled": boolean; "metricsEnabled": boolean; "metricsEndpoint"?: null | string; "mode": string; "reason": string; "serviceName"?: null | string; "tracesEnabled": boolean; "tracesEndpoint"?: null | string };
export type GitBranchCreateRequest = { "base"?: null | string; "name": string };
export type GitBranchMutationResponse = { "base"?: null | string; "branch": string; "currentBranch"?: string; "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitBranchPolicyApplyRequest = { "allowProtected"?: boolean; "branchName"?: null | string; "createBranch"?: boolean; "intent": string; "selectedBase"?: string };
export type GitBranchPolicyApplyResponse = { "created": boolean; "currentBranch"?: string; "intent": string; "policyDecisionIds"?: Array<string>; "projectId": string; "protectedBranches": Array<string>; "reason": string; "selectedBase": string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "suggestedBranchName": string; "targetBranch": string; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitBranchesResponse = { "currentBranch"?: string; "dirty"?: boolean; "localBranches"?: Array<string>; "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "remoteBranches"?: Array<string>; "remotes"?: Array<GitRemoteRecord>; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitCheckoutRequest = { "allowDirty"?: boolean; "branch": string };
export type GitCheckoutResponse = { "currentBranch"?: string; "dirty"?: boolean; "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "requestedBranch": string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitCommandTraceRecord = { "execution"?: string; "permissionDecisionId"?: null | string; "returnCode"?: null | number; "toolCallId": string; "toolCallStatus": string };
export type GitCommitRecord = { "author"?: string; "authoredAt"?: string; "hash"?: string; "shortHash"?: string; "subject"?: string };
export type GitDiffResponse = { "changedFiles"?: Array<string>; "diff": string; "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "root": string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitGitleaksRecord = { "configured": boolean; "executable": boolean; "exitCode"?: null | number; "findingCount"?: number; "permissionDecisionId"?: null | string; "reason": string; "report"?: JsonValue | null; "reportPath"?: null | string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCallId"?: null | string };
export type GitGitleaksScanResponse = { "deliveryBlocked": boolean; "gitleaks": GitGitleaksRecord; "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitInitRequest = { "defaultBranch"?: "main" | "dev" };
export type GitInitResponse = { "commitCreated": boolean; "currentBranch"?: string; "defaultBranch": "main" | "dev"; "gitignoreCreated": boolean; "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitRemoteAddRequest = { "name": string; "url": string };
export type GitRemoteMetadataRecord = { "createdAt": string; "host": string; "lastTestReason"?: null | string; "lastTestStatus"?: null | string; "lastTestedAt"?: null | string; "name": string; "path": string; "scheme": string; "updatedAt": string; "url": string };
export type GitRemoteMutationResponse = { "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "remote"?: GitRemoteMetadataRecord | null; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitRemoteRecord = { "direction": "fetch" | "push"; "name": string; "url": string };
export type GitRemoteTestRequest = { "allowNetwork"?: boolean };
export type GitRemoteTestResponse = { "outputPreview"?: string; "policyDecisionIds"?: Array<string>; "projectId": string; "reason": string; "remoteName": string; "status": "completed" | "blocked" | "configuration_required" | "failed"; "tested": boolean; "toolCalls"?: Array<GitCommandTraceRecord>; "workspaceId": string };
export type GitStatusResponse = { "changedFiles"?: Array<string>; "currentBranch"?: string; "dirty"?: boolean; "lastCommit"?: GitCommitRecord | null; "policyDecisionIds"?: Array<string>; "porcelain"?: Array<string>; "projectId": string; "reason": string; "remotes"?: Array<GitRemoteRecord>; "root": string; "stagedFiles"?: Array<string>; "status": "completed" | "blocked" | "configuration_required" | "failed"; "toolCalls"?: Array<GitCommandTraceRecord>; "untrackedFiles"?: Array<string>; "workspaceId": string; "worktrees"?: Array<GitWorktreeRecord> };
export type GitWorktreeRecord = { "bare"?: boolean; "branch"?: string; "detached"?: boolean; "head"?: string; "path": string };
export type GovernanceResponse = { "architectureDecisions": Array<ArchitectureDecisionRecord>; "nextSteps": Array<NextStepRecord>; "risks": Array<RiskRecord> };
export type HTTPValidationError = { "detail"?: Array<ValidationError> };
export type HandshakeResponse = { "loopbackOnly": boolean; "token": string };
export type HealthResponse = { "ok": boolean };
export type I18nCatalog = { "defaultLanguage": string; "languages": Array<I18nLanguage>; "translations": JsonObject };
export type I18nCatalogResponse = { "defaultLanguage": string; "languages": Array<I18nLanguage>; "translations": JsonObject };
export type I18nLanguage = { "code": string; "enabled"?: boolean; "name": string; "nativeName": string };
export type IdeConnectionRecord = { "createdAt": string; "diagnostics": Array<JsonValue>; "editor": string; "id": string; "openFiles": Array<JsonValue>; "projectId": string; "selection": JsonObject; "status": string; "terminalContext": JsonObject; "updatedAt": string; "workspaceRoot": string };
export type IdeConnectionResponse = { "ideConnection": IdeConnectionRecord };
export type IdeConnectionUpsertRequest = { "diagnostics"?: Array<JsonValue>; "editor"?: string; "openFiles"?: Array<JsonValue>; "projectId": string; "selection"?: JsonObject; "status"?: string; "terminalContext"?: JsonObject; "workspaceRoot"?: null | string };
export type IdeConnectionsListResponse = { "ideConnections": Array<IdeConnectionRecord> };
export type ImageArtifactReference = { "artifactId": string; "downloadPath": string; "mimeType": "image/png" | "image/jpeg"; "sha256": string; "sizeBytes": number };
export type ImageEditingResponse = { "artifacts": Array<ImageArtifactReference>; "model": string; "providerId": string };
export type ImageGenerationResponse = { "artifacts": Array<ImageArtifactReference>; "model": string; "providerId": string };
export type IntegrationRecord = { "config": JsonObject; "createdAt": string; "id": string; "kind": string; "status": string; "updatedAt": string };
export type IntegrationsListResponse = { "integrations": Array<IntegrationRecord>; "mcpServers": Array<McpServerRecord>; "optionalAdapters": JsonObject };
export type IssueToPatchRequest = { "issueText": string; "maxCostUsd"?: null | number; "preferredRuntime"?: null | string; "projectId": string; "qaCommands"?: Array<Array<string>>; "requireApproval"?: boolean; "targetPath"?: null | string; "title": string };
export type IssueToPatchResponse = { "agentRun": AgentRunRecord; "diffSummary": JsonObject; "evidencePackage": EvidencePackageRecord; "job": JobRecord; "pullRequest"?: JsonObject | null; "qaResults": Array<JsonObject>; "reason": string; "runtime": JsonObject; "runtimeResult": JsonObject; "status": string; "workflow": WorkflowRecord; "workflowRun": WorkflowRunRecord; "workflowSteps": Array<WorkflowStepRecord>; "workspace": WorkspaceRecord };
export type IssueToPrRequest = { "buildScripts"?: Array<string>; "createPullRequest"?: boolean; "dockerHealthcheck"?: boolean; "issueText": string; "maxCostUsd"?: null | number; "maxReworkAttempts"?: number; "preferredRuntime"?: null | string; "projectId": string; "qaCommands"?: Array<Array<string>>; "qualityScripts"?: Array<string>; "requireApproval"?: boolean; "targetPath"?: null | string; "title": string };
export type IssueToPrResponse = { "agentRun": AgentRunRecord; "completion": JsonObject; "dag": JsonObject; "diffSummary": JsonObject; "evidencePackage": EvidencePackageRecord; "gateResults": Array<JsonObject>; "job": JobRecord; "pullRequest"?: JsonObject | null; "qaResults": Array<JsonObject>; "reason": string; "rework": JsonObject; "runtime": JsonObject; "runtimeResult": JsonObject; "status": string; "timeline"?: Array<JsonObject>; "workflow": WorkflowRecord; "workflowRun": WorkflowRunRecord; "workflowSteps": Array<WorkflowStepRecord>; "workspace": WorkspaceRecord };
export type IterationRecord = { "assignmentCount": number; "briefId"?: null | string; "createdAt": string; "estimatedCost": JsonObject; "goal"?: null | string; "id": string; "projectId": string; "qualityGates": Array<JsonValue>; "runtimes": Array<JsonValue>; "securityGates": Array<JsonValue>; "status": string; "storyIds": Array<JsonValue>; "taskCount": number; "title": string; "updatedAt": string; "workspaceStrategy"?: null | string };
export type JobCreateRequest = { "idempotencyKey"?: null | string; "kind": string; "payload"?: JsonObject; "projectId": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type JobMutationResponse = { "actionRequest"?: ActionRequestRecord | null; "actionRequests"?: Array<ActionRequestRecord>; "auditEvent"?: AuditEventRecord | null; "events"?: Array<EventRecord>; "job": JobRecord; "permissionGrant"?: ApprovalGrantRecord | null };
export type JobRecord = { "createdAt": string; "id": string; "idempotencyKey"?: null | string; "kind": string; "leaseExpiresAt"?: null | string; "leaseOwner"?: null | string; "payload": JsonObject; "projectId": string; "status": "queued" | "running" | "approval_required" | "completed" | "approved" | "failed" | "cancelled"; "updatedAt": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type JobRunRecord = { "completedAt"?: null | string; "id": string; "jobId": string; "metadata": JsonObject; "providerId"?: null | string; "startedAt": string; "status": "queued" | "running" | "completed" | "failed" | "cancelled"; "summary": string };
export type JobsListResponse = { "events"?: Array<EventRecord>; "jobs": Array<JobRecord> };
export type LowLevelEvents = { "modelCalls"?: Array<ModelCallSummary>; "toolCalls"?: Array<ToolCallSummary> };
export type McpServerRecord = { "command": string; "createdAt": string; "id": string; "metadata": JsonObject; "status": string; "transport": "stdio"; "updatedAt": string };
export type McpServerRegisterRequest = { "command": string; "id": string; "metadata"?: JsonObject; "transport"?: "stdio" };
export type McpServerResponse = { "mcpServer": McpServerRecord };
export type MemoryCreateRequest = { "content": string; "expiresAt"?: null | string; "kind"?: string; "metadata"?: JsonObject; "projectId": string; "scope"?: string; "scopeId"?: null | string; "sourceRef"?: string; "ttlSeconds"?: null | number };
export type MemoryDeleteRequest = { "reason"?: string };
export type MemoryItemRecord = { "content": string; "createdAt": string; "createdByRunId"?: null | string; "deletedAt"?: null | string; "expiresAt"?: null | string; "hash": string; "id": string; "kind": string; "metadata": JsonObject; "projectId": string; "scope": string; "scopeId"?: null | string; "sourceRef": string; "supersedesId"?: null | string; "updatedAt": string; "validFrom": string; "version": number };
export type MemoryListResponse = { "memoryItems": Array<MemoryItemRecord> };
export type MemoryResponse = { "memoryItem": MemoryItemRecord };
export type ModelBenchmarkOutcomeCreateRequest = { "actualCostUsd"?: null | number; "agentId"?: null | string; "estimatedCostUsd"?: null | number; "jobId"?: null | string; "latencyMs"?: null | number; "metadata"?: JsonObject; "model": string; "provenance"?: "operator_reported" | "automated_run" | "release_validation"; "providerId": string; "qaPass"?: boolean | null; "rework"?: boolean | null; "role"?: null | string; "runtimeType"?: string; "success"?: boolean | null; "taskId"?: null | string; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type ModelBenchmarkOutcomeRecord = { "actualCostUsd"?: null | number; "agentId"?: null | string; "createdAt": string; "estimatedCostUsd"?: null | number; "id": string; "jobId"?: null | string; "latencyMs"?: null | number; "metadata": JsonObject; "model": string; "provenance": "operator_reported" | "automated_run" | "release_validation"; "providerId": string; "qaPass"?: boolean | null; "rework"?: boolean | null; "role"?: null | string; "runtimeType": string; "success"?: boolean | null; "taskId"?: null | string; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type ModelBenchmarkOutcomeResponse = { "outcome": ModelBenchmarkOutcomeRecord };
export type ModelBenchmarkOutcomesListResponse = { "outcomes": Array<ModelBenchmarkOutcomeRecord> };
export type ModelBenchmarkRecord = { "automatedRunTasks": number; "avgCost"?: null | number; "avgLatencyMs"?: null | number; "createdAt": string; "id": string; "insufficientData": boolean; "lastUsedAt"?: null | string; "metadata": JsonObject; "model": string; "objectiveTasksAttempted": number; "operatorReportedTasks": number; "provenanceCounts": JsonObject; "providerId": string; "qaPassRate"?: null | number; "releaseValidationTasks": number; "reworkRate"?: null | number; "role"?: null | string; "successRate"?: null | number; "tasksAttempted": number; "updatedAt": string };
export type ModelBenchmarksListResponse = { "benchmarks": Array<ModelBenchmarkRecord> };
export type ModelCallRecord = { "agentRunId"?: null | string; "completionTokens": null | number; "costUsd"?: null | number; "createdAt": string; "id": string; "metadata": JsonObject; "model": string; "modelPolicyId"?: null | string; "projectId": string; "promptTokens": null | number; "provider": string; "status": "planned" | "completed" | "failed" | "blocked" | "unavailable" };
export type ModelCallSummary = { "completionTokens": null | number; "costUsd"?: null | number; "createdAt": string; "id": string; "model": string; "promptTokens": null | number; "provider": string; "status": string };
export type ModelCatalogListResponse = { "models": Array<ModelCatalogRecord> };
export type ModelCatalogPatchRequest = { "apiFamily"?: "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing" | null; "cachedInputPricePerMtok"?: null | number; "contextWindow"?: null | number; "displayName"?: null | string; "effortLevels"?: Array<string> | null; "enabled"?: boolean | null; "freeTier"?: boolean | null; "freeTierNotes"?: null | string; "inputPricePerMtok"?: null | number; "maxOutputTokens"?: null | number; "modelFamily"?: null | string; "outputPricePerMtok"?: null | number; "reasoningPricePerMtok"?: null | number; "source"?: null | string; "supportsEmbeddings"?: boolean | null; "supportsImageEditing"?: boolean | null; "supportsImageGeneration"?: boolean | null; "supportsJson"?: boolean | null; "supportsReasoning"?: boolean | null; "supportsRerank"?: boolean | null; "supportsStreaming"?: boolean | null; "supportsThinking"?: boolean | null; "supportsTools"?: boolean | null; "supportsVision"?: boolean | null };
export type ModelCatalogRecord = { "apiFamily": "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing"; "cachedInputPricePerMtok"?: null | number; "contextWindow": number; "createdAt": string; "displayName": string; "effortLevels": Array<string>; "enabled": boolean; "freeTier": boolean; "freeTierNotes": string; "id": string; "inputPricePerMtok"?: null | number; "maxOutputTokens": number; "model": string; "modelFamily": string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "source": string; "supportsEmbeddings": boolean; "supportsImageEditing": boolean; "supportsImageGeneration": boolean; "supportsJson": boolean; "supportsReasoning": boolean; "supportsRerank": boolean; "supportsStreaming": boolean; "supportsThinking": boolean; "supportsTools": boolean; "supportsVision": boolean; "updatedAt": string };
export type ModelCatalogResponse = { "model": ModelCatalogRecord };
export type ModelCatalogUpsertRequest = { "apiFamily"?: "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing"; "cachedInputPricePerMtok"?: null | number; "contextWindow"?: number; "displayName"?: null | string; "effortLevels"?: Array<string>; "enabled"?: boolean; "freeTier"?: boolean; "freeTierNotes"?: string; "inputPricePerMtok"?: null | number; "maxOutputTokens"?: number; "model": string; "modelFamily"?: string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "source"?: string; "supportsEmbeddings"?: boolean; "supportsImageEditing"?: boolean; "supportsImageGeneration"?: boolean; "supportsJson"?: boolean; "supportsReasoning"?: boolean; "supportsRerank"?: boolean; "supportsStreaming"?: boolean; "supportsThinking"?: boolean; "supportsTools"?: boolean; "supportsVision"?: boolean };
export type ModelGatewayOverviewRecord = { "activeCliSessions": number; "actualCostToday"?: null | number; "apiProviders": number; "cliRuntimes": number; "degraded": number; "estimatedCostToday": number; "healthy": number; "localProviders": number; "offline": number; "pendingModelApprovals": number; "providersEnabled": number; "providersInCooldown": number; "totalTokensToday": null | number };
export type ModelGatewayOverviewResponse = { "overview": ModelGatewayOverviewRecord };
export type ModelPolicyRecord = { "allowLocal": boolean; "allowRemote": boolean; "createdAt": string; "fallback": Array<ModelProviderCandidate>; "id": string; "maxCostUsd": number; "maxTokens": number; "name": string; "preferred": Array<ModelProviderCandidate>; "status": "active" | "disabled"; "temperature": number; "updatedAt": string };
export type ModelProviderCandidate = { "model": string; "provider": string };
export type ModelProviderRecord = { "allowRemote": boolean; "createdAt": string; "id": string; "label": string; "metadata": JsonObject; "provider": string; "status": string; "updatedAt": string };
export type N8nEventDeliveryRecord = { "createdAt": string; "error": string; "eventType": "thread.created" | "loop.blocked" | "approval.required" | "qa.failed" | "gitleaks.failed" | "delivery.ready"; "id": string; "projectId": string; "requestPayload": JsonObject; "responseBody": JsonObject; "status": "delivered" | "failed"; "statusCode"?: null | number; "subjectId"?: null | string; "targetId": string };
export type N8nEventDeliveryResponse = { "delivery": N8nEventDeliveryRecord };
export type N8nEventEmitRequest = { "eventType": "thread.created" | "loop.blocked" | "approval.required" | "qa.failed" | "gitleaks.failed" | "delivery.ready"; "payload"?: JsonObject; "projectId": string; "subjectId"?: null | string; "targetId"?: null | string };
export type N8nEventTestRequest = { "eventType"?: "thread.created" | "loop.blocked" | "approval.required" | "qa.failed" | "gitleaks.failed" | "delivery.ready"; "payload"?: JsonObject; "projectId": string; "targetId"?: null | string };
export type N8nInboundWebhookRequest = { "action": string; "payload"?: JsonObject; "projectId": string };
export type N8nInboundWebhookResponse = { "accepted": boolean; "action": "create_thread" | "add_message" | "get_status"; "message"?: JsonObject | null; "status"?: JsonObject | null; "thread"?: JsonObject | null };
export type N8nIntegrationStatusRecord = { "allowedEventTypes": Array<"thread.created" | "loop.blocked" | "approval.required" | "qa.failed" | "gitleaks.failed" | "delivery.ready">; "configured": boolean; "enabledTargetCount": number; "eventAllowlist": Array<"thread.created" | "loop.blocked" | "approval.required" | "qa.failed" | "gitleaks.failed" | "delivery.ready">; "targetCount": number; "targets": Array<N8nWebhookTargetRecord> };
export type N8nIntegrationStatusResponse = { "status": N8nIntegrationStatusRecord };
export type N8nWebhookTargetCreateRequest = { "allowedEventTypes": Array<"thread.created" | "loop.blocked" | "approval.required" | "qa.failed" | "gitleaks.failed" | "delivery.ready">; "credentialRef": string; "enabled"?: boolean; "metadata"?: JsonObject; "projectId": string; "url": string };
export type N8nWebhookTargetRecord = { "allowedEventTypes": Array<"thread.created" | "loop.blocked" | "approval.required" | "qa.failed" | "gitleaks.failed" | "delivery.ready">; "createdAt": string; "credentialRef": string; "enabled": boolean; "id": string; "metadata": JsonObject; "projectId": string; "updatedAt": string; "url": string };
export type N8nWebhookTargetResponse = { "target": N8nWebhookTargetRecord };
export type NextStep = { "handoffTo"?: null | string; "source": "agent" | "handoff"; "text"?: null | string };
export type NextStepCreateRequest = { "dueAt"?: null | string; "metadata"?: JsonObject; "owner"?: string; "priority"?: "low" | "medium" | "high" | "urgent"; "projectId": string; "sourceDecisionId"?: null | string; "sourceRiskId"?: null | string; "status"?: "planned" | "in_progress" | "blocked" | "completed" | "cancelled"; "title": string };
export type NextStepRecord = { "createdAt": string; "dueAt"?: null | string; "id": string; "metadata": JsonObject; "owner": string; "priority": "low" | "medium" | "high" | "urgent"; "projectId": string; "sourceDecisionId"?: null | string; "sourceRiskId"?: null | string; "status": "planned" | "in_progress" | "blocked" | "completed" | "cancelled"; "title": string; "updatedAt": string };
export type NextStepResponse = { "nextStep": NextStepRecord };
export type NextStepUpdateRequest = { "dueAt"?: null | string; "metadata"?: JsonObject | null; "owner"?: null | string; "priority"?: "low" | "medium" | "high" | "urgent" | null; "status"?: "planned" | "in_progress" | "blocked" | "completed" | "cancelled" | null };
export type NextStepsListResponse = { "nextSteps": Array<NextStepRecord> };
export type NvidiaNimContainerRuntimeFacts = { "id": "docker" | "podman"; "installed": boolean; "serverReachable": boolean; "version"?: null | string };
export type NvidiaNimContainerToolkitFacts = { "cdiAvailable": boolean; "detectionScope": "aido_host_process"; "installed": boolean; "version"?: null | string };
export type NvidiaNimGpuFacts = { "computeCapability"?: null | string; "driverVersion"?: null | string; "index": number; "memoryTotalGiB": number; "name": string };
export type NvidiaNimPlatformFacts = { "architecture": string; "distributions"?: Array<string>; "os": string; "target": "linux" | "wsl2" | "windows" | "unknown"; "wslAvailable": boolean; "wslVersion"?: null | number };
export type NvidiaNimPreflightBlocker = { "actions"?: Array<NvidiaNimRemediationAction>; "category": "hardware" | "platform" | "runtime" | "observation"; "code": string; "message": string; "observed"?: null | number | string; "required"?: null | number | string; "sourceUrl"?: null | string };
export type NvidiaNimPreflightResponse = { "facts": NvidiaNimSystemFacts; "mutationAttempted": false; "observedAt": string; "probeScope": "aido_host_process"; "profiles": Array<NvidiaNimProfilePreflightResult>; "status": "ready" | "blocked" };
export type NvidiaNimProfilePreflightResult = { "blockers": Array<NvidiaNimPreflightBlocker>; "displayName": string; "hardwareCompatible": boolean; "modelId": string; "mutationAttempted": false; "observedGpuMemoryGiB"?: null | number; "profileId": string; "requiredGpuMemoryGiB": number; "requirement": NvidiaNimRequirement; "runtimeReady": boolean; "status": "ready" | "blocked_incompatible_hardware" | "blocked_runtime_not_ready" };
export type NvidiaNimRemediationAction = { "description": string; "href"?: null | string; "id": string; "kind": "open_documentation" | "open_configuration" | "select_supported_target"; "label": string };
export type NvidiaNimRequirement = { "displayName": string; "minimumComputeCapability": number; "minimumDriverMajor": number; "minimumGpuArchitecture": string; "minimumGpuMemoryGiB": number; "minimumSystemMemoryGiB": number; "modelId": string; "platformSourceUrl": string; "profileId": string; "sourceUrl": string; "supportedTargets": Array<"linux" | "wsl2">; "verifiedAt": string; "wslSupportedGeForceSeries": Array<number> };
export type NvidiaNimSystemFacts = { "containerRuntimes": Array<NvidiaNimContainerRuntimeFacts>; "containerToolkit": NvidiaNimContainerToolkitFacts; "diskFreeGiB"?: null | number; "gpus": Array<NvidiaNimGpuFacts>; "platform": NvidiaNimPlatformFacts; "resourceScope": "host" | "runtime_target"; "swapGiB"?: null | number; "systemMemoryGiB"?: null | number };
export type OllamaEndpointCreateRequest = { "baseUrl": string; "credentialRef"?: null | string; "displayName"?: null | string; "enabled"?: boolean; "id": string; "kind"?: "local" | "remote" | null; "label"?: null | string; "metadata"?: JsonObject };
export type OllamaEndpointHealthRecord = { "healthStatus": string; "id": string; "lastError"?: null | string; "latency": number; "latencyMs": number; "message"?: string; "models"?: Array<string>; "providerId": string; "status": string };
export type OllamaEndpointHealthResponse = { "health": OllamaEndpointHealthRecord };
export type OllamaEndpointRecord = { "baseUrl": string; "createdAt": string; "credentialRef"?: null | string; "credentialStatus": string; "displayName": string; "enabled": boolean; "healthStatus": string; "id": string; "kind": "local" | "remote"; "label": string; "lastError": string; "lastHealthCheckAt"?: null | string; "latency"?: null | number; "latencyMs"?: null | number; "models"?: Array<string>; "providerId": string; "runtimeId": string; "updatedAt": string };
export type OllamaEndpointResponse = { "endpoint": OllamaEndpointRecord };
export type OllamaEndpointsListResponse = { "endpoints": Array<OllamaEndpointRecord> };
export type OllamaRuntimeProviderStatus = { "available": boolean; "models": Array<string>; "provider": string; "reason"?: string };
export type OllamaSyncModelsResponse = { "models": Array<JsonObject> };
export type OpenDesignResponse = { "backend": string; "runtime": string; "status": string };
export type OpenDesignStatus = { "status": string };
export type OptionalReasonRequest = { "reason"?: string };
export type OverviewResponse = { "actionRequests": Array<ActionRequestRecord>; "agentProfiles": Array<AgentProfileRecord>; "agentRuns": Array<AgentRunRecord>; "agentToolCalls": Array<AgentToolCallRecord>; "agents": Array<CatalogAgentRecord>; "architectureDecisions": Array<ArchitectureDecisionRecord>; "artifacts": Array<ArtifactRecord>; "auditEvents": Array<AuditEventRecord>; "costUsage": Array<CostUsageRecord>; "events": Array<EventRecord>; "evidencePackages": Array<EvidencePackageRecord>; "ideConnections": Array<IdeConnectionRecord>; "jobRuns": Array<JobRunRecord>; "jobs": Array<JobRecord>; "mcpServers": Array<McpServerRecord>; "memoryItems": Array<MemoryItemRecord>; "modelCalls": Array<ModelCallRecord>; "modelPolicies": Array<ModelPolicyRecord>; "modelProviders": Array<ModelProviderRecord>; "nextSteps": Array<NextStepRecord>; "openDesign": OpenDesignStatus; "permissionDecisions": Array<PermissionDecisionRecord>; "permissionGrants": Array<ApprovalGrantRecord>; "policyRevisions": Array<PolicyRevisionRecord>; "projectTemplates": Array<ProjectTemplateRecord>; "projects": Array<ProjectRecord>; "promptTemplates": Array<PromptTemplateRecord>; "providers": Array<ProviderRecord>; "riskRegister": Array<RiskRecord>; "runtimeWorkspaces": Array<WorkspaceRecord>; "sandboxProfiles": Array<SandboxProfileRecord>; "security": SecurityPosture; "skills": Array<SkillRecord>; "teams": Array<TeamRecord>; "testResultRecords": Array<TestResultRecord>; "threads": Array<ThreadRecord>; "workflowEvents": Array<WorkflowEventRecord>; "workflowRuns": Array<WorkflowRunRecord>; "workflowSteps": Array<WorkflowStepRecord>; "workflows": Array<WorkflowRecord> };
export type PermissionDecisionRecord = { "agentId"?: null | string; "command"?: null | string; "createdAt": string; "decision": "allow" | "deny" | "requires_approval" | "requires_human"; "id": string; "path"?: null | string; "payload": JsonObject; "projectId"?: null | string; "reason": string; "riskLevel": "low" | "medium" | "high" | "critical"; "role"?: null | string; "tool"?: null | string; "workspaceId"?: null | string };
export type PermissionGrantResponse = { "permissionGrant": ApprovalGrantRecord };
export type PipelineRecord = { "chatId"?: null | string; "createdAt": string; "id": string; "metadata": JsonObject; "projectId": string; "sessionId"?: null | string; "stages": Array<JsonObject>; "status": string; "title": string; "updatedAt": string };
export type PipelinesListResponse = { "pipelines": Array<PipelineRecord> };
export type PluginAgentRecord = { "capabilities": Array<string>; "createdAt": string; "id": string; "role": string; "schema": JsonObject; "status": string };
export type PluginInstallEventRecord = { "action": string; "createdAt": string; "id": string; "manifestHash"?: null | string; "payload": JsonObject; "pluginId"?: null | string; "pluginVersionId"?: null | string; "reason": string; "status": string };
export type PluginInstallEventsResponse = { "events": Array<PluginInstallEventRecord> };
export type PluginInstallLocalRequest = { "path": string };
export type PluginPermissionRecord = { "createdAt": string; "id": string; "permission": string; "reason": string; "riskLevel": string; "status": string };
export type PluginRecord = { "activeVersion": PluginVersionRecord | null; "activeVersionId": null | string; "capabilities": Array<string>; "createdAt": string; "id": string; "name": string; "permissions": Array<string>; "publisher": string; "status": string; "trustLevel": string; "updatedAt": string };
export type PluginResponse = { "plugin": PluginRecord };
export type PluginScanCandidateRecord = { "agentsCount"?: number; "id"?: null | string; "installed"?: boolean; "installedStatus"?: null | string; "issues": Array<string>; "name"?: null | string; "path": string; "permissions"?: Array<string>; "publisher"?: null | string; "skillsCount"?: number; "toolsCount"?: number; "trustLevel"?: null | string; "valid": boolean; "version"?: null | string };
export type PluginScanLocalRequest = { "path": string };
export type PluginScanLocalResponse = { "candidates": Array<PluginScanCandidateRecord>; "root": string };
export type PluginSkillRecord = { "contractHash": string; "createdAt": string; "id": string; "path": string; "status": string };
export type PluginToolRecord = { "brokerTool": string; "createdAt": string; "id": string; "name": string; "policy": JsonValue; "policyRequired": boolean; "schema": JsonObject; "status": string };
export type PluginValidationResponse = { "issues": Array<string>; "plugin"?: PluginRecord | null; "valid": boolean };
export type PluginVersionRecord = { "agents": Array<PluginAgentRecord>; "checksums": JsonObject; "entrypoints": JsonObject; "id": string; "installedAt": string; "manifest": JsonObject; "manifestHash": string; "manifestPath": string; "minAidoVersion": string; "packageHash": string; "permissions": Array<PluginPermissionRecord>; "pluginId": string; "skills": Array<PluginSkillRecord>; "status": string; "tools": Array<PluginToolRecord>; "validatedAt": string; "version": string };
export type PluginsListResponse = { "plugins": Array<PluginRecord> };
export type PoliciesListResponse = { "permissionDecisions": Array<PermissionDecisionRecord>; "permissionGrants": Array<ApprovalGrantRecord>; "policies": Array<PolicyRecord>; "policyRevisions": Array<PolicyRevisionRecord>; "sandboxProfiles": Array<SandboxProfileRecord> };
export type PolicyEvaluateRequest = { "agentId"?: null | string; "command"?: null | string; "deploymentTarget"?: null | string; "environment"?: null | string; "gitOperation"?: null | string; "networkRequired"?: boolean | null; "operation"?: null | string; "path"?: null | string; "permissionProfile"?: null | string; "projectId"?: null | string; "riskLevel"?: "low" | "medium" | "high" | "critical" | null; "role"?: null | string; "secretsRequired"?: boolean | null; "tool"?: null | string; "workspaceId"?: null | string };
export type PolicyEvaluationResponse = { "decision": PermissionDecisionRecord };
export type PolicyRecord = { "createdAt": string; "id": string; "name": string; "profile": string; "rules": Array<JsonObject>; "updatedAt": string };
export type PolicyRevisionRecord = { "actor": string; "changedFields": Array<string>; "createdAt": string; "id": string; "previous": JsonObject; "reason": string; "subjectId": string; "subjectType": string; "updated": JsonObject; "version": number };
export type PricingSnapshotCreateRequest = { "applyToCatalog"?: boolean; "cachedInputPricePerMtok"?: null | number; "effectiveAt"?: null | string; "freeTier"?: boolean; "inputPricePerMtok"?: null | number; "metadata"?: JsonObject; "model": string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "sourceRef"?: string };
export type PricingSnapshotRecord = { "applyToCatalog": boolean; "cachedInputPricePerMtok"?: null | number; "createdAt": string; "effectiveAt"?: null | string; "freeTier": boolean; "id": string; "inputPricePerMtok"?: null | number; "metadata": JsonObject; "model": string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "sourceRef": string };
export type PricingSnapshotResponse = { "pricingSnapshot": PricingSnapshotRecord };
export type PricingSnapshotsListResponse = { "pricingSnapshots": Array<PricingSnapshotRecord> };
export type ProductBriefRecord = { "createdAt": string; "goals": Array<JsonValue>; "id": string; "initiativeId"?: null | string; "outOfScope"?: null | string; "problemStatement"?: null | string; "projectId": string; "scope"?: null | string; "status": string; "successMetrics": Array<JsonValue>; "summary"?: null | string; "targetUsers": Array<JsonValue>; "title": string; "updatedAt": string; "version": number };
export type ProductDecisionRecord = { "briefId"?: null | string; "consequences": Array<JsonValue>; "context"?: null | string; "createdAt": string; "decidedAt"?: null | string; "decidedBy"?: null | string; "decision"?: null | string; "id": string; "initiativeId"?: null | string; "linkedAssumptionIds": Array<JsonValue>; "linkedQuestionIds": Array<JsonValue>; "metadata": JsonObject; "projectId": string; "rationale"?: null | string; "status": string; "supersedesId"?: null | string; "title": string; "updatedAt": string; "version": number };
export type ProductLoopAidoDecideRequest = { "actor"?: null | string; "reason"?: null | string };
export type ProductLoopApprovalRequest = { "actor"?: null | string; "reason"?: null | string };
export type ProductLoopFeedbackApplyResponse = { "allowedNextStates": Array<string>; "feedback": ProductLoopFeedbackRecord; "loop": ProductLoopRecord; "resumable": boolean; "transitions": Array<ProductLoopTransitionRecord> };
export type ProductLoopFeedbackRecord = { "action": "accept" | "request_changes" | "change_scope" | "reprioritize" | "reject_decision" | "reopen_story" | "pause_loop" | "cancel_loop" | "continue"; "actor": string; "classification": "rework_task" | "new_story" | "new_epic" | "brief_revision" | "architecture_revision"; "createdAt": string; "effects": Array<JsonValue>; "feedback": string; "id": string; "loopId": string; "metadata": JsonObject; "projectId": string; "status": string; "targetId": string; "targetType": string; "updatedAt": string };
export type ProductLoopFeedbackRequest = { "action": "accept" | "request_changes" | "change_scope" | "reprioritize" | "reject_decision" | "reopen_story" | "pause_loop" | "cancel_loop" | "continue"; "actor"?: null | string; "correlationId"?: null | string; "expectedVersion"?: null | number; "feedback": string; "payload"?: JsonObject | null; "targetId"?: null | string; "targetType"?: null | string };
export type ProductLoopRecord = { "context": JsonObject; "createdAt": string; "id": string; "initiativeId"?: null | string; "previousState"?: null | string; "projectId": string; "state": string; "status": string; "title": string; "updatedAt": string; "version": number };
export type ProductLoopResumeResponse = { "allowedNextStates": Array<string>; "loop": ProductLoopRecord; "resumable": boolean; "transitions": Array<ProductLoopTransitionRecord> };
export type ProductLoopStartRequest = { "budget"?: JsonObject | null; "context"?: JsonObject | null; "correlationId"?: null | string; "deadline"?: null | string; "initiativeId"?: null | string; "maxReworkRounds"?: null | number; "timeouts"?: JsonObject | null; "title": string };
export type ProductLoopStateResponse = { "acceptanceCriteria": Array<AcceptanceCriterionRecord>; "assignmentConflicts": Array<AssignmentConflictRecord>; "assignmentHandoffs": Array<AssignmentHandoffRecord>; "assignmentReviews": Array<AssignmentReviewRecord>; "assignments": Array<AgentAssignmentRecord>; "assumptions": Array<AssumptionRecord>; "brief"?: ProductBriefRecord | null; "decisions": Array<ProductDecisionRecord>; "epics": Array<EpicRecord>; "feedback": Array<ProductLoopFeedbackRecord>; "iterations": Array<IterationRecord>; "loops": Array<ProductLoopRecord>; "questions": Array<ClarificationQuestionRecord>; "stories": Array<UserStoryRecord>; "storyDependencies": Array<StoryDependencyRecord>; "taskDependencies": Array<TaskDependencyRecord>; "tasks": Array<AgentTaskRecord>; "transitions": Array<ProductLoopTransitionRecord> };
export type ProductLoopTransitionRecord = { "actor": string; "createdAt": string; "fromState": string; "id": string; "loopId": string; "metadata": JsonObject; "projectId": string; "reason": string; "toState": string; "trigger": string; "version": number };
export type ProductLoopTransitionRequest = { "correlationId"?: null | string; "expectedVersion"?: null | number; "reason"?: null | string; "toState": string; "trigger"?: null | string };
export type ProductOwnerAgentRunRequest = { "approvalGrantId"?: null | string; "autonomy"?: JsonObject | null; "completenessThreshold"?: null | number; "epicId"?: null | string; "idea"?: null | string; "initiativeId"?: null | string; "metadata"?: JsonObject; "model"?: null | string; "preferredRuntime"?: null | string; "projectId": string; "taskId"?: string; "workflowContext"?: JsonObject; "workspaceId": string };
export type ProductOwnerAgentRunResponse = { "agentRun": AgentRunRecord; "assumptions"?: Array<JsonObject>; "blockingDecisions"?: Array<JsonObject>; "brief"?: JsonObject | null; "completeness"?: JsonObject | null; "confidence"?: string; "decisions"?: Array<JsonObject>; "epics"?: Array<JsonObject>; "evidencePackage": JsonObject; "initiative"?: JsonObject | null; "job": JsonObject; "output"?: JsonObject | null; "productBriefPatch"?: JsonObject | null; "productOwnerAgent": ProductOwnerAgentStatus; "productOwnerOutput"?: JsonObject | null; "questions"?: Array<JsonObject>; "reason": string; "recommendedNextAction"?: string; "risks"?: Array<JsonObject>; "runtime": JsonObject; "runtimeResult": JsonObject; "status": string; "summary"?: string; "userStories"?: Array<JsonObject>; "workspace": JsonObject };
export type ProductOwnerAgentStatus = { "candidateRuntimeIds"?: Array<string>; "contract": JsonObject; "executable": boolean; "id": string; "reason": string; "selectedRuntimeId"?: null | string; "status": string };
export type ProductOwnerAgentStatusResponse = { "productOwnerAgent": ProductOwnerAgentStatus };
export type ProjectAssessmentRecord = { "createdAt": string; "findingsCount": number; "gapCount": number; "id": string; "projectId": string; "riskCount": number; "rootPath": string; "source": string; "status": string; "summary": JsonObject; "updatedAt": string };
export type ProjectAssessmentRunResponse = { "assessment"?: ProjectAssessmentRecord | null; "findings"?: Array<ProjectFindingRecord>; "reason": string; "status": string };
export type ProjectAssessmentsListResponse = { "assessments": Array<ProjectAssessmentRecord> };
export type ProjectCreateRequest = { "createDirectory"?: boolean; "metadata"?: JsonObject; "name"?: null | string; "path"?: null | string; "projectDirectoryName"?: null | string; "templateId"?: null | string; "workspaceBasePath"?: null | string };
export type ProjectDiscoveryRequest = { "path": string };
export type ProjectDiscoveryResponse = { "discovery": JsonObject };
export type ProjectFindingRecord = { "assessmentId": string; "category": string; "confidence": string; "createdAt": string; "detail": string; "evidence": string; "id": string; "metadata": JsonObject; "projectId": string; "severity": string; "title": string };
export type ProjectFindingsListResponse = { "findings": Array<ProjectFindingRecord> };
export type ProjectFunctionalityRecord = { "createdAt": string; "filePaths": Array<string>; "fingerprint": string; "id": string; "metadata": JsonObject; "name": string; "normalizedName": string; "performanceNotes": Array<JsonObject>; "projectId": string; "reason"?: null | string; "score"?: null | number; "sourceThreadId": string; "status": "open" | "queued" | "running" | "waiting_decision" | "awaiting_approval" | "blocked" | "resolved" | "archived" | "deleted"; "summary": string; "updatedAt": string };
export type ProjectFunctionalityResponse = { "functionality": Array<ProjectFunctionalityRecord> };
export type ProjectRecord = { "createdAt": string; "id": string; "metadata": JsonObject; "name": string; "path": string; "source": string; "status": string; "templateId": string; "updatedAt": string };
export type ProjectResponse = { "auditEvent"?: AuditEventRecord | null; "project": ProjectRecord };
export type ProjectTemplateRecord = { "id": string; "kind": string; "name": string };
export type ProjectTemplatesResponse = { "projectTemplates": Array<ProjectTemplateRecord> };
export type ProjectsListResponse = { "projects": Array<ProjectRecord> };
export type PromotePatchToBranchRequest = { "branchName"?: null | string; "evidencePackageId"?: null | string; "qaCommands"?: Array<Array<string>> | null; "reason": string };
export type PromptResponse = { "promptTemplate": PromptTemplateRecord };
export type PromptTemplateRecord = { "appliesTo": JsonObject; "body": string; "createdAt": string; "id": string; "mode": string; "name": string; "optimizer": string; "projectId": string; "updatedAt": string; "version": number };
export type PromptTemplatesListResponse = { "promptTemplates": Array<PromptTemplateRecord> };
export type PromptUpsertRequest = { "appliesTo"?: JsonObject; "body": string; "id"?: null | string; "mode"?: string; "name": string; "optimizer"?: string; "projectId": string };
export type ProviderAccountFromCatalogRequest = { "adapterProfile"?: null | string; "apiFamily"?: "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing" | null; "baseUrl"?: null | string; "credentialRef"?: null | string; "deploymentMode"?: "custom" | "hosted_trial" | "self_hosted_development" | "self_hosted_enterprise" | "partner_paid" | null; "displayName"?: null | string; "enabled"?: boolean; "instanceId"?: null | string; "metadata"?: JsonObject; "pricingMode"?: "unknown" | "free" | "configured" | null; "providerId": string; "termsMode"?: "unspecified" | "evaluation" | "accepted" | null };
export type ProviderAccountPatchRequest = { "adapterProfile"?: null | string; "apiFamily"?: "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing" | null; "apiFormat"?: null | string; "baseUrl"?: null | string; "credentialRef"?: null | string; "deploymentMode"?: "custom" | "hosted_trial" | "self_hosted_development" | "self_hosted_enterprise" | "partner_paid" | null; "displayName"?: null | string; "enabled"?: boolean | null; "metadata"?: JsonObject | null; "pricingMode"?: "unknown" | "free" | "configured" | null; "providerFamily"?: null | string; "providerType"?: null | string; "quotaMode"?: null | string; "termsMode"?: "unspecified" | "evaluation" | "accepted" | null };
export type ProviderAccountRecord = { "adapterProfile": string; "apiFamily": "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing"; "apiFormat": string; "baseUrl"?: null | string; "createdAt": string; "credentialRef"?: null | string; "credentialStatus": string; "deploymentMode": "custom" | "hosted_trial" | "self_hosted_development" | "self_hosted_enterprise" | "partner_paid"; "displayName": string; "enabled": boolean; "healthStatus": string; "id": string; "lastError": string; "lastHealthCheckAt"?: null | string; "metadata"?: JsonObject; "pricingMode": "unknown" | "free" | "configured"; "providerFamily": string; "providerId": string; "providerType": string; "quotaMode": string; "termsMode": "unspecified" | "evaluation" | "accepted"; "updatedAt": string };
export type ProviderAccountResponse = { "provider": ProviderAccountRecord };
export type ProviderAccountUpsertRequest = { "adapterProfile"?: string; "apiFamily"?: "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing"; "apiFormat"?: string; "baseUrl"?: string; "credentialRef"?: string; "deploymentMode"?: "custom" | "hosted_trial" | "self_hosted_development" | "self_hosted_enterprise" | "partner_paid"; "displayName"?: null | string; "enabled"?: boolean; "metadata"?: JsonObject; "pricingMode"?: "unknown" | "free" | "configured"; "providerFamily"?: null | string; "providerId": string; "providerType"?: string; "quotaMode"?: string; "termsMode"?: "unspecified" | "evaluation" | "accepted" };
export type ProviderAccountsListResponse = { "providers": Array<ProviderAccountRecord> };
export type ProviderCatalogEntryRecord = { "adapterProfile": string; "aliases"?: Array<string>; "apiFamily": "chat_completions" | "embeddings" | "rerank" | "image_generation" | "image_editing"; "apiFormat": string; "capabilities": Array<string>; "credentialKind": string; "defaultBaseUrl"?: null | string; "deploymentMode": "custom" | "hosted_trial" | "self_hosted_development" | "self_hosted_enterprise" | "partner_paid"; "displayName": string; "docsUrl": string; "id": string; "knownModels": Array<string>; "modelSync": JsonObject; "pricingMode": "unknown" | "free" | "configured"; "pricingSource": string; "providerFamily": string; "providerType": string; "requiredFields": Array<string>; "termsMode": "unspecified" | "evaluation" | "accepted" };
export type ProviderCatalogListResponse = { "providers": Array<ProviderCatalogEntryRecord>; "version": string };
export type ProviderEmbeddingRequest = { "encodingFormat"?: "float" | null; "input": Array<string> | string; "inputType"?: "query" | "passage" | null; "model": string; "truncate"?: "START" | "END" | "NONE" | null };
export type ProviderEmbeddingResponse = { "embedding": EmbeddingResponse };
export type ProviderHealth = { "healthStatus": string; "lastError"?: null | string; "message"?: string; "providerId": string; "status": string };
export type ProviderHealthResponse = { "health": ProviderHealth };
export type ProviderImageEditingRequest = { "aspectRatio"?: "1:1" | "16:9" | "9:16" | "4:3" | "3:4" | "3:2" | "2:3" | null; "cfgScale"?: null | number; "height"?: null | number; "inputArtifactId": string; "model": string; "negativePrompt"?: null | string; "projectId": string; "prompt": string; "seed"?: null | number; "steps"?: null | number; "width"?: null | number };
export type ProviderImageEditingResponse = { "imageEditing": ImageEditingResponse };
export type ProviderImageGenerationRequest = { "aspectRatio"?: "1:1" | "16:9" | "9:16" | "4:3" | "3:4" | "3:2" | "2:3" | null; "cfgScale"?: null | number; "height"?: null | number; "model": string; "negativePrompt"?: null | string; "projectId": string; "prompt": string; "seed"?: null | number; "steps"?: null | number; "width"?: null | number };
export type ProviderImageGenerationResponse = { "imageGeneration": ImageGenerationResponse };
export type ProviderLimitPatchRequest = { "dailyRequests"?: null | number; "dailyTokens"?: null | number; "enabled"?: boolean | null; "fallbackRetryAfterSeconds"?: null | number; "maxConcurrency"?: null | number; "maxCostPerRequestUsd"?: null | number; "monthlyBudgetUsd"?: null | number; "monthlyRequests"?: null | number; "monthlyTokens"?: null | number; "rpm"?: null | number; "tpm"?: null | number; "unknownLimitStrategy"?: "conservative" | "block" | "allow" | null; "windowTimezone"?: null | string };
export type ProviderLimitRecord = { "cooldownUntil"?: null | string; "createdAt": string; "currentWindow": JsonObject; "dailyRequests"?: null | number; "dailyTokens"?: null | number; "enabled": boolean; "fallbackRetryAfterSeconds": number; "id": string; "last429At"?: null | string; "lastLimitErrorAt"?: null | string; "maxConcurrency"?: null | number; "maxCostPerRequestUsd"?: null | number; "model": string; "monthlyBudgetUsd"?: null | number; "monthlyRequests"?: null | number; "monthlyTokens"?: null | number; "providerId": string; "rpm"?: null | number; "tpm"?: null | number; "unknownLimitStrategy": string; "updatedAt": string; "windowTimezone": string };
export type ProviderLimitResponse = { "providerLimit": ProviderLimitRecord };
export type ProviderLimitStatusRecord = { "activeLeases": number; "allowed": boolean; "cooldownUntil"?: null | string; "effectiveLimitId"?: null | string; "enabled": boolean; "guarded": boolean; "model": string; "providerId": string; "quotaPressure": number; "reason": string; "windows": Array<ProviderLimitWindowStatus> };
export type ProviderLimitStatusResponse = { "status": ProviderLimitStatusRecord };
export type ProviderLimitUpsertRequest = { "dailyRequests"?: null | number; "dailyTokens"?: null | number; "enabled"?: boolean; "fallbackRetryAfterSeconds"?: number; "maxConcurrency"?: null | number; "maxCostPerRequestUsd"?: null | number; "model": string; "monthlyBudgetUsd"?: null | number; "monthlyRequests"?: null | number; "monthlyTokens"?: null | number; "providerId": string; "rpm"?: null | number; "tpm"?: null | number; "unknownLimitStrategy"?: "conservative" | "block" | "allow"; "windowTimezone"?: string };
export type ProviderLimitWindowStatus = { "committedRequests": number; "committedTokens": number; "kind": "minute" | "day" | "month"; "knownCostUsd": number; "reservedCostUsd": number; "reservedRequests": number; "reservedTokens": number; "resetsAt": string; "startsAt": string; "unverifiedCostUsd": number; "unverifiedTokens": number };
export type ProviderLimitsListResponse = { "providerLimits": Array<ProviderLimitRecord> };
export type ProviderRecord = { "capabilities": Array<JsonValue>; "id": string; "kind": string; "label": string; "metadata": JsonObject; "models": Array<JsonValue>; "status": string; "updatedAt": string };
export type ProviderRerankRequest = { "model": string; "passages": Array<RerankPassage>; "query": string; "truncate"?: "START" | "END" | "NONE" | null };
export type ProviderRerankResponse = { "rerank": RerankResponse };
export type ProvidersListResponse = { "providers": Array<ProviderRecord> };
export type PullRequestCreateRequest = { "baseBranch"?: null | string; "reason": string; "title"?: null | string };
export type QAAgentCommandRequest = { "argv": Array<string>; "critical"?: boolean; "label"?: null | string; "timeoutSeconds"?: null | number };
export type QAAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean; "verdictSource": string };
export type QAAgentRunRequest = { "commands"?: Array<QAAgentCommandRequest>; "metadata"?: JsonObject; "projectId": string; "taskId"?: string; "workspaceId": string };
export type QAAgentRunResponse = { "agentRun": AgentRunRecord; "contract": QAAgentContract; "evidencePackage": JsonObject; "job": JsonObject; "reason": string; "results": Array<JsonObject>; "status": string; "verdict": string; "workspace": JsonObject };
export type RemediationActionRecord = { "actionType": "open_settings_section" | "validate_runtime" | "switch_runtime" | "continue_plan_only" | "git_init" | "add_remote" | "create_branch" | "checkout_branch" | "run_gitleaks" | "run_worker_once" | "check_network_access" | "answer_question" | "approve_resource_decision" | "retry_loop" | "view_diff" | "save_patch"; "blockerType": "runtime_not_executable" | "runtime_auth_missing" | "runtime_output_invalid" | "git_not_initialized" | "git_dirty_tree" | "git_status_failed" | "git_branch_missing" | "git_remote_missing" | "gitleaks_missing" | "gitleaks_failed" | "qa_failed" | "po_needs_input" | "worker_not_running" | "provider_missing_credentials" | "provider_health_failed" | "resource_manager_unconfigured" | "resource_manager_privacy_blocked" | "resource_manager_approval_required" | "team_scheduler_failed" | "technical_lead_planning_failed" | "product_owner_output_invalid" | "research_required" | "workspace_root_missing" | "workspace_allocation_failed" | "review_diff_unavailable" | "approval_unavailable" | "resource_learning_failed" | "project_assessment_failed" | "functionality_memory_decision_required" | "thread_similarity_decision_required" | "thread_intake_decision_required"; "confirmationRequired"?: boolean; "createdAt": string; "description": string; "destructive"?: boolean; "id": string; "loopId": string; "payload": JsonObject; "primary"?: boolean; "projectId": string; "resolvedAt"?: null | string; "stage": string; "status": "pending" | "resolved" | "dismissed" | "failed"; "technicalReason"?: string; "threadId": string; "title": string };
export type RemediationDismissResponse = { "remediation": RemediationActionRecord };
export type RemediationExecuteRequest = { "payload"?: JsonObject };
export type RemediationExecuteResponse = { "execution": JsonObject; "remediation": RemediationActionRecord };
export type RemediationListResponse = { "remediations": Array<RemediationActionRecord> };
export type RequiredReasonRequest = { "reason": string };
export type RerankPassage = { "text": string };
export type RerankResponse = { "model": string; "providerId": string; "rankings": Array<RerankResult>; "usage"?: CapabilityUsage | null };
export type RerankResult = { "index": number; "passage"?: RerankPassage | null; "relevanceScore": number };
export type ResearchAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean; "sourcePolicy": JsonObject; "verdictSource": string };
export type ResearchAgentRunRequest = { "claims"?: Array<ResearchClaimRequest>; "conclusions"?: Array<ResearchConclusionRequest>; "maxSources"?: null | number; "metadata"?: JsonObject; "projectId": string; "query"?: null | string; "sources"?: Array<ResearchSourceRequest>; "taskId"?: string; "technicalDecisions"?: Array<ResearchTechnicalDecisionRequest>; "workspaceId": string };
export type ResearchAgentRunResponse = { "agentRun": AgentRunRecord; "citationCheck": JsonObject; "conclusions": Array<JsonObject>; "conflictFindings": Array<JsonObject>; "contract": ResearchAgentContract; "discrepancies"?: Array<JsonObject>; "evidencePackage": JsonObject; "job": JsonObject; "reason": string; "recommendation"?: JsonObject; "reportArtifact": JsonObject; "sources": Array<JsonObject>; "status": "research_required" | "research_running" | "research_ready" | "research_blocked"; "technicalDecisions"?: Array<JsonObject>; "verdict": "research_required" | "research_running" | "research_ready" | "research_blocked"; "workspace": JsonObject };
export type ResearchAgentStatus = { "contract": ResearchAgentContract; "executable": boolean; "id": string; "reason": string; "status": string };
export type ResearchAgentStatusResponse = { "researchAgent": ResearchAgentStatus };
export type ResearchClaimRequest = { "sourceUrl": string; "topic": string; "value": string };
export type ResearchConclusionRequest = { "citations"?: Array<string>; "statement": string; "webBased"?: boolean };
export type ResearchSourceRequest = { "content"?: null | string; "fetchedAt"?: null | string; "publisher": string; "relatedArtifact"?: null | string; "sourceType"?: null | string; "title"?: null | string; "trustLevel"?: "official_documentation" | "official_repository" | "standard_rfc" | "primary_research" | "reputable_secondary" | "untrusted" | null; "url": string };
export type ResearchTechnicalDecisionRequest = { "decision": string; "sourceUrls"?: Array<string>; "title": string };
export type ResolvedSetting = { "editableScopes": Array<string>; "enum"?: Array<string> | null; "inherited": boolean; "key": string; "labelKey"?: string; "origin": "default" | "general" | "project"; "projectSection"?: null | string; "section": string; "source": "default" | "general" | "project"; "type": string; "value": JsonValue };
export type RestrictedSubprocessStatus = { "available": boolean; "fallbackOnlyForLowRisk": boolean; "requiresArgv": boolean; "shell": boolean; "workspaceBound": boolean };
export type RetrievalIndexSummary = { "backend": string; "degraded": boolean; "dimensions": number; "ids": Array<string>; "indexed": number; "projectId": string; "reason": string; "status": string };
export type RetrievalReindexRequest = { "projectId": string };
export type RetrievalReindexResponse = { "index": RetrievalIndexSummary };
export type RetrievalSearchRequest = { "limit"?: number; "projectId": string; "query"?: string };
export type RetrievalSearchResponse = { "reason": string; "results": Array<RetrievalSearchResultRecord>; "status": string };
export type RetrievalSearchResultRecord = { "memoryItem": MemoryItemRecord; "score": number };
export type RetrievalStatusResponse = { "available": boolean; "backend": string; "degraded": boolean; "dimensions": number; "faissAvailable": boolean; "indexDir": string; "indexed": number; "reason": string; "status": string };
export type ReviewerSummary = { "decision"?: null | string; "reviewerAgentId": string; "status": string };
export type RiskCreateRequest = { "description"?: string; "evidenceRefs"?: Array<string>; "metadata"?: JsonObject; "mitigation"?: string; "owner"?: string; "projectId": string; "severity"?: "low" | "medium" | "high" | "critical"; "status"?: "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed"; "title": string };
export type RiskRecord = { "createdAt": string; "description": string; "evidenceRefs": Array<string>; "id": string; "metadata": JsonObject; "mitigation": string; "owner": string; "projectId": string; "severity": "low" | "medium" | "high" | "critical"; "status": "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed"; "title": string; "updatedAt": string };
export type RiskResponse = { "risk": RiskRecord };
export type RiskUpdateRequest = { "evidenceRefs"?: Array<string> | null; "metadata"?: JsonObject | null; "mitigation"?: null | string; "owner"?: null | string; "severity"?: "low" | "medium" | "high" | "critical" | null; "status"?: "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed" | null };
export type RisksListResponse = { "risks": Array<RiskRecord> };
export type RolePoliciesListResponse = { "rolePolicies": Array<RolePolicyRecord> };
export type RolePolicyPatchRequest = { "allowApi"?: boolean; "allowCli"?: boolean; "allowLocal"?: boolean; "allowRemote"?: boolean; "allowUnknownCost"?: boolean; "blocked"?: Array<JsonObject>; "escalation"?: Array<JsonObject>; "fallback"?: Array<JsonObject>; "id"?: null | string; "maxCostPerTaskUsd"?: number; "maxTokensPerRun"?: number; "preferred"?: Array<JsonObject>; "requireApprovalForUnknownCost"?: boolean; "requiresApprovalForReasoningMax"?: boolean; "requiresApprovalOverUsd"?: null | number; "role"?: null | string; "routingProfileId"?: null | string };
export type RolePolicyRecord = { "allowApi": boolean; "allowCli": boolean; "allowLocal": boolean; "allowRemote": boolean; "allowUnknownCost": boolean; "blocked": Array<JsonObject>; "createdAt": string; "escalation": Array<JsonObject>; "fallback": Array<JsonObject>; "id": string; "maxCostPerTaskUsd": number; "maxTokensPerRun": number; "preferred": Array<JsonObject>; "requireApprovalForUnknownCost": boolean; "requiresApprovalForReasoningMax": boolean; "requiresApprovalOverUsd"?: null | number; "role": string; "routingProfileId": string; "updatedAt": string };
export type RolePolicyResponse = { "rolePolicy": RolePolicyRecord };
export type RolePolicyUpsertRequest = { "allowApi"?: boolean; "allowCli"?: boolean; "allowLocal"?: boolean; "allowRemote"?: boolean; "allowUnknownCost"?: boolean; "blocked"?: Array<JsonObject>; "escalation"?: Array<JsonObject>; "fallback"?: Array<JsonObject>; "id"?: null | string; "maxCostPerTaskUsd"?: number; "maxTokensPerRun"?: number; "preferred"?: Array<JsonObject>; "requireApprovalForUnknownCost"?: boolean; "requiresApprovalForReasoningMax"?: boolean; "requiresApprovalOverUsd"?: null | number; "role": string; "routingProfileId"?: null | string };
export type RouteExecuteResponse = { "content"?: null | string; "routing": RoutingPreviewResponse; "usage"?: UsageLedgerRecord | null };
export type RoutingCandidateRecord = { "effort"?: null | string; "estimatedCostUsd"?: null | number; "freeTier"?: boolean; "model": string; "priceKnown"?: boolean; "pricingSource"?: string; "pricingStaleness"?: string; "provider": string; "runtime": string; "score": number; "scoreBreakdown": JsonObject };
export type RoutingDecisionRecord = { "agentId"?: null | string; "candidates": Array<JsonObject>; "createdAt": string; "decisionReason": string; "estimatedCostUsd"?: null | number; "estimatedTokens"?: null | number; "id": string; "jobId"?: null | string; "mode": string; "policyResult": JsonObject; "rejected": Array<JsonObject>; "role": string; "scoreBreakdown": JsonObject; "selectedEffort"?: null | string; "selectedModel"?: null | string; "selectedProvider"?: null | string; "selectedRuntime"?: null | string; "taskId"?: null | string; "taskType": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type RoutingDecisionsListResponse = { "routingDecisions": Array<RoutingDecisionRecord> };
export type RoutingPolicyResult = { "allowUnknownCost"?: boolean; "freeTierOnly"?: boolean; "maxCostPerTaskUsd"?: null | number; "requireApprovalForUnknownCost"?: boolean; "requiresApproval": boolean; "rolePolicyId": string; "unknownCostPolicy"?: JsonObject };
export type RoutingPreviewRequest = { "agentId"?: null | string; "allowFallback"?: boolean; "budgetRemainingUsd"?: null | number; "contextTokensEstimate"?: number; "jobId"?: null | string; "manualModel"?: null | string; "manualProvider"?: null | string; "manualRuntime"?: null | string; "mode"?: string; "privacyLevel"?: string; "projectId"?: null | string; "requiresCodeEdit"?: boolean; "requiresJson"?: boolean; "requiresReasoning"?: boolean; "requiresSearch"?: boolean; "requiresTools"?: boolean; "requiresVision"?: boolean; "riskLevel"?: string; "role"?: string; "taskId"?: null | string; "taskType"?: string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type RoutingPreviewResponse = { "budgetResult"?: JsonObject; "candidates": Array<RoutingCandidateRecord>; "decisionReason": string; "estimatedCostUsd"?: null | number; "estimatedTokens": number; "policyResult": RoutingPolicyResult; "quotaResult"?: JsonObject; "rejected": Array<RoutingRejectedRecord>; "scoreBreakdown": JsonObject; "selected": RoutingSelection | null };
export type RoutingProfilePatchRequest = { "enabled"?: boolean | null; "mode"?: null | string; "name"?: null | string; "objective"?: null | string; "rules"?: JsonObject | null };
export type RoutingProfileRecord = { "createdAt": string; "enabled": boolean; "id": string; "mode": string; "name": string; "objective": string; "rules": JsonObject; "updatedAt": string };
export type RoutingProfileResponse = { "routingProfile": RoutingProfileRecord };
export type RoutingProfileUpsertRequest = { "enabled"?: boolean; "id"?: null | string; "mode"?: null | string; "name": string; "objective"?: string; "rules"?: JsonObject };
export type RoutingProfilesListResponse = { "routingProfiles": Array<RoutingProfileRecord> };
export type RoutingRejectedRecord = { "model"?: null | string; "provider": string; "reason": string; "runtime"?: null | string };
export type RoutingSelection = { "effort"?: null | string; "model": string; "provider": string; "runtime": string };
export type RuntimeDetectionResponse = { "detection": CliRuntimeRecord };
export type RuntimeHealthRecord = { "message"?: string; "runtime": string; "status": string };
export type RuntimeHealthResponse = { "health": RuntimeHealthRecord };
export type RuntimeProviderConfigurationRecord = { "configured": boolean; "displayName": string; "id": string; "kind": "api" | "gateway" | "local" | "cli" | "manual"; "missing": Array<string>; "reason": string; "status": "configured" | "configuration_required" | "override_unset"; "variables": Array<RuntimeProviderConfigurationVariable> };
export type RuntimeProviderConfigurationResponse = { "providers": Array<RuntimeProviderConfigurationRecord> };
export type RuntimeProviderConfigurationVariable = { "configured": boolean; "fingerprint"?: null | string; "key": string; "name": string; "required": boolean; "secret": boolean };
export type RuntimeProviderSafety = { "network"?: "blocked_by_default" | "runtime_policy_gated" | "remote_calls_disabled_by_default" | "local_only"; "shell"?: boolean; "structuredArgv"?: boolean; "workspaceBound"?: boolean };
export type RuntimeProviderStatus = { "authenticated"?: boolean; "available": boolean; "blockerType"?: null | string; "canEditWorkspace"?: boolean; "canRunPrompt"?: boolean; "canRunVersionCheck"?: boolean; "capabilities"?: Array<string>; "configurationWarnings"?: Array<string>; "configured": boolean; "detected"?: boolean; "detectedCommand"?: null | string; "displayName": string; "executable": boolean; "healthCheckedAt"?: null | string; "healthStatus"?: string; "id": string; "installed"?: boolean; "kind": "api" | "gateway" | "local" | "cli" | "manual"; "lastError"?: string; "productOwnerExecutable"?: boolean; "reason": string; "requiredConfiguration"?: Array<string>; "requiresApproval"?: boolean; "safety"?: RuntimeProviderSafety; "version"?: null | string; "versionVerified"?: boolean };
export type RuntimeProvidersResponse = { "api": ApiRuntimeProviderStatus; "cli": CliRuntimeProviderStatus; "configurationWarnings"?: Array<string>; "developerAgent": DeveloperAgentStatus; "ollama": OllamaRuntimeProviderStatus; "providers": Array<RuntimeProviderStatus>; "runtimeModes": Array<"api" | "cli" | "ollama" | "hybrid" | "manual"> };
export type SandboxProfileMutationResponse = { "policyRevision"?: PolicyRevisionRecord | null; "sandboxProfile": SandboxProfileRecord };
export type SandboxProfilePatchRequest = { "allowedImages"?: Array<string> | null; "allowedNetworks"?: Array<string> | null; "cpus"?: null | string; "defaultNetwork"?: null | string; "memory"?: null | string; "name"?: null | string; "reason": string; "status"?: "active" | "disabled" | "revoked" | null; "timeoutSeconds"?: null | number };
export type SandboxProfileRecord = { "allowedImages": Array<string>; "allowedNetworks": Array<string>; "cpus": string; "createdAt": string; "defaultNetwork": string; "id": string; "memory": string; "name": string; "revokeReason"?: null | string; "revokedAt"?: null | string; "revokedBy"?: null | string; "status": "active" | "disabled" | "revoked"; "timeoutSeconds": number; "updatedAt": string };
export type SandboxProfileResponse = { "sandboxProfile": SandboxProfileRecord };
export type SandboxStatusResponse = { "docker": DockerSandboxStatus; "restrictedSubprocess": RestrictedSubprocessStatus };
export type SecurityAgentCommandCandidateRequest = { "argv": Array<string>; "label"?: null | string };
export type SecurityAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean; "verdictSource": string };
export type SecurityAgentRunRequest = { "approvalGrantId"?: null | string; "commandCandidates"?: Array<SecurityAgentCommandCandidateRequest>; "diffArtifactId"?: null | string; "metadata"?: JsonObject; "model"?: null | string; "pathsToCheck"?: Array<string>; "preferredRuntime"?: null | string; "projectId": string; "runModelAnalysis"?: boolean; "taskId"?: string; "workspaceId": string };
export type SecurityAgentRunResponse = { "agentRun": AgentRunRecord; "contract": SecurityAgentContract; "dependencyFiles": Array<JsonObject>; "evidencePackage": JsonObject; "externalScanners"?: Array<JsonObject>; "filesScanned": Array<JsonObject>; "findings": Array<JsonObject>; "findingsArtifact": JsonObject; "job": JsonObject; "modelAnalysis"?: JsonObject | null; "reason": string; "status": string; "verdict": string; "workspace": JsonObject };
export type SecurityAgentStatus = { "candidateRuntimeIds"?: Array<string>; "contract": SecurityAgentContract; "executable": boolean; "id": string; "reason": string; "selectedRuntimeId"?: null | string; "status": string };
export type SecurityAgentStatusResponse = { "securityAgent": SecurityAgentStatus };
export type SecurityPosture = { "loopbackOnly": boolean; "writeTokenRequired": boolean };
export type SelfImprovementLessonRecord = { "approvedActionRequestId": string; "createdAt": string; "evidencePackageIds": Array<string>; "id": string; "lesson": string; "metadata": JsonObject; "promotedAt"?: null | string; "promotedBy": string; "promotionActionRequestId": string; "promotionJobId": string; "promotionStatus": string; "proposalId"?: null | string; "scope": "project" | "global"; "selfProjectId": string; "sourceProjectId": string; "status": string; "title": string; "updatedAt": string };
export type SelfImprovementLessonRequest = { "evidencePackageIds"?: Array<string>; "lesson": string; "metadata"?: JsonObject; "proposalId"?: null | string; "proposedBy"?: string; "scope": "project" | "global"; "sourceProjectId": string; "title": string };
export type SelfImprovementLessonResponse = { "auditEvent": AuditEventRecord; "lesson": SelfImprovementLessonRecord; "promotionActionRequest"?: ActionRequestRecord | null; "promotionJob"?: JobRecord | null };
export type SelfImprovementPerformanceRecord = { "baselineValue"?: null | number; "createdAt": string; "evidencePackageId": string; "id": string; "metadata": JsonObject; "metricName": string; "proposalId"?: null | string; "recordedBy": string; "selfProjectId": string; "sourceProjectId": string; "targetValue"?: null | number; "unit": string; "value": number };
export type SelfImprovementPerformanceRequest = { "baselineValue"?: null | number; "evidencePackageId": string; "metadata"?: JsonObject; "metricName": string; "proposalId"?: null | string; "recordedBy"?: string; "sourceProjectId": string; "targetValue"?: null | number; "unit": string; "value": number };
export type SelfImprovementPerformanceResponse = { "auditEvent": AuditEventRecord; "performanceRecord": SelfImprovementPerformanceRecord };
export type SelfImprovementPromoteLessonRequest = { "actor"?: string; "reason": string };
export type SelfImprovementProposalRecord = { "createdAt": string; "epicId": string; "goalLoopId": string; "id": string; "metadata": JsonObject; "qaCommands": Array<Array<string>>; "selfProjectId": string; "sourceProjectId": string; "status": string; "storyId": string; "summary": string; "targetPaths": Array<string>; "taskId": string; "title": string; "updatedAt": string; "workflowId": string; "workspaceId": string };
export type SelfImprovementProposalRequest = { "metadata"?: JsonObject; "proposedBy"?: string; "qaCommands"?: Array<Array<string>>; "sourceProjectId": string; "summary": string; "targetPaths"?: Array<string>; "title": string };
export type SelfImprovementProposalResponse = { "auditEvent": AuditEventRecord; "epic": EpicRecord; "goal": ProductLoopRecord; "proposal": SelfImprovementProposalRecord; "selfImprovementProject": ProjectRecord; "sourceProject": ProjectRecord; "story": UserStoryRecord; "task": AgentTaskRecord; "workflow": WorkflowRecord; "workspace": WorkspaceRecord };
export type SelfImprovementStateResponse = { "lessons": Array<SelfImprovementLessonRecord>; "performanceRecords": Array<SelfImprovementPerformanceRecord>; "proposals": Array<SelfImprovementProposalRecord>; "selfImprovementProject": ProjectRecord | null };
export type SessionRecord = { "createdAt": string; "id": string; "metadata": JsonObject; "name": string; "projectId": string; "status": string; "teamId"?: null | string; "updatedAt": string };
export type SessionsListResponse = { "sessions": Array<SessionRecord> };
export type SetSettingRequest = { "scope": "general" | "project"; "scopeId"?: null | string; "value": JsonValue };
export type SettingsResponse = { "general": Array<ResolvedSetting>; "project": Array<ResolvedSetting> };
export type SkillRecord = { "compatibility": string; "createdAt": string; "description": string; "id": string; "license": string; "metadata": JsonObject; "name": string; "path": string; "riskLevel": string; "updatedAt": string };
export type SkillsListResponse = { "skills": Array<SkillRecord> };
export type SkillsSyncRequest = { "skillsPath"?: string };
export type SkillsSyncResponse = { "skills": Array<SkillRecord>; "synced": number };
export type StoryDependencyRecord = { "createdAt": string; "dependsOnStoryId": string; "id": string; "metadata": JsonObject; "projectId": string; "reason": string; "storyId": string; "type": string };
export type StorySpecAcceptanceCriterion = { "criterion": string; "id": string; "sequence": number; "status": string };
export type StorySpecDependency = { "role": string; "taskId": string };
export type StorySpecEpic = { "description": string; "id": string; "title": string };
export type StorySpecResponse = { "acceptanceCriteria": Array<StorySpecAcceptanceCriterion>; "epic": StorySpecEpic; "promptText": string; "roleResponsibilities": Array<StorySpecRoleResponsibility>; "story": StorySpecStory; "storyId": string };
export type StorySpecRoleResponsibility = { "dependsOn": Array<StorySpecDependency>; "goal": string; "qualityGates": Array<JsonObject>; "reviewerRole": string; "role": string; "taskId": string; "title": string };
export type StorySpecStory = { "asA": string; "businessValue": string; "iWant": string; "priority": string; "soThat": string; "status": string; "title": string };
export type TaskDependencyRecord = { "createdAt": string; "dependsOnTaskId": string; "id": string; "metadata": JsonObject; "projectId": string; "reason": string; "taskId": string; "type": string };
export type TeamActivityEntry = { "agentId": string; "agentName": string; "blockedReason"?: null | string; "completedArtifact"?: ArtifactSummary | null; "costSource"?: "actual" | "estimated" | null; "costUsd"?: null | number; "currentAssignment"?: AssignmentSummary | null; "developerDetails": DeveloperDetails; "durationMs"?: null | number; "endedAt"?: null | string; "id": string; "lowLevelEvents": LowLevelEvents; "modelCallCount": number; "nextStep"?: NextStep | null; "provider"?: null | string; "reviewer"?: ReviewerSummary | null; "role": string; "runtime": string; "startedAt"?: null | string; "state": "active" | "blocked" | "done"; "status": string; "toolCallCount": number };
export type TeamActivityResponse = { "activeCount": number; "blockedCount": number; "entries"?: Array<TeamActivityEntry>; "generatedAt": string; "projectId": string; "totalCount": number; "truncated": boolean };
export type TeamRecord = { "capabilities": Array<JsonValue>; "createdAt": string; "id": string; "metadata": JsonObject; "name": string; "projectId": string; "updatedAt": string; "version": string };
export type TeamsListResponse = { "teams": Array<TeamRecord> };
export type TelemetryStatusResponse = { "externalExporter": ExternalTelemetryStatus };
export type TestPromptRecord = { "error"?: null | string; "latencyMs": number; "model": string; "ok": boolean; "providerId": string; "sample"?: string; "totalTokens"?: null | number; "usageSource"?: string };
export type TestPromptRequest = { "model"?: null | string };
export type TestPromptResponse = { "test": TestPromptRecord };
export type TestResultRecord = { "command": string; "createdAt": string; "durationMs"?: null | number; "evidencePackageId": string; "id": string; "metadata": JsonObject; "outputRef"?: null | string; "projectId": string; "status": "passed" | "failed" | "completed" | "denied" | "allowed" | "requires_approval" | "approval_required" | "blocked" | "skipped" | "skipped_with_reason" | "error" | "timed_out" };
export type ThreadAgentEventRecord = { "agentRole"?: null | string; "createdAt": string; "id": string; "metadata": JsonObject; "payload": JsonObject; "projectId": string; "sequence": number; "threadId": string; "type": string };
export type ThreadArchiveRequest = { "actor"?: string; "reason": string };
export type ThreadArchiveResponse = { "auditEvent": AuditEventRecord; "thread": ThreadRecord };
export type ThreadArtifactRecord = { "artifactId": string; "createdAt": string; "id": string; "kind": string; "messageId"?: null | string; "metadata": JsonObject; "projectId": string; "threadId": string; "title": string };
export type ThreadBudgetUsedRecord = { "callCount": number; "capScope"?: null | string; "costStatus": "actual" | "estimated" | "mixed" | "partial" | "unknown"; "perRunCapUsd"?: null | number; "usedUsd"?: null | number };
export type ThreadCancelRequest = { "actor"?: null | string; "reason"?: null | string };
export type ThreadCancelResponse = { "cancelledJobIds": Array<string>; "thread": ThreadRecord };
export type ThreadCheaperAlternativeRecord = { "deltaUsd"?: null | number; "estimatedCostUsd"?: null | number; "model"?: null | string; "priceKnown"?: boolean; "provider"?: null | string; "runtime"?: null | string };
export type ThreadCostPerformanceRecord = { "budgetUsed": ThreadBudgetUsedRecord; "cheaperAlternative"?: ThreadCheaperAlternativeRecord | null; "cost": ThreadCostSummaryRecord; "hasData": boolean; "latency": ThreadLatencySummaryRecord; "loopIds": Array<string>; "modelChosen"?: ThreadModelChosenRecord | null; "policy": ThreadCostPolicyRecord; "qualityRework": ThreadQualityReworkRecord; "reasonSelected"?: null | string; "threadId": string; "tokens": ThreadTokenSummaryRecord };
export type ThreadCostPerformanceResponse = { "costPerformance": ThreadCostPerformanceRecord };
export type ThreadCostPolicyRecord = { "approvalRequired": boolean; "forceLocal": boolean; "mode": "economy" | "balanced" | "critical" | "maximum"; "premiumApproval"?: ThreadPremiumApprovalRecord | null; "premiumApprovalOverUsd"?: null | number };
export type ThreadCostSummaryRecord = { "actualCostUsd"?: null | number; "estimatedCostUsd"?: null | number };
export type ThreadCreateRequest = { "ownerId": string; "ownerType": "workspace" | "loop" | "story" | "agent_task" | "review"; "projectId": string; "title": string };
export type ThreadDecisionRecord = { "createdAt": string; "decidedAt"?: null | string; "decidedBy"?: null | string; "id": string; "messageId"?: null | string; "metadata": JsonObject; "options": Array<string>; "projectId": string; "prompt": string; "resolution"?: null | string; "status": "pending" | "resolved" | "dismissed"; "threadId": string; "title": string; "updatedAt": string };
export type ThreadDecisionResolveRequest = { "decidedBy"?: null | string; "resolution": string };
export type ThreadDecisionResolveResponse = { "decision": ThreadDecisionRecord; "thread": ThreadRecord };
export type ThreadDeleteRequest = { "actor"?: string; "reason": string };
export type ThreadDeleteResponse = { "auditEvent": AuditEventRecord; "thread": ThreadRecord };
export type ThreadDetailResponse = { "artifacts": Array<ThreadArtifactRecord>; "decisions": Array<ThreadDecisionRecord>; "events": Array<ThreadAgentEventRecord>; "messages": Array<ThreadMessageRecord>; "thread": ThreadRecord };
export type ThreadEventsResponse = { "events": Array<ThreadAgentEventRecord>; "lastSeq": number; "running": boolean; "threadStatus": "open" | "queued" | "running" | "waiting_decision" | "awaiting_approval" | "blocked" | "resolved" | "archived" | "deleted" };
export type ThreadLatencySummaryRecord = { "avgMs"?: null | number; "callCount": number; "knownCalls": number; "latencyStatus": "actual" | "partial" | "unknown" | "none"; "maxMs"?: null | number; "p50Ms"?: null | number; "unknownCalls": number };
export type ThreadListResponse = { "threads": Array<ThreadRecord> };
export type ThreadMemoryDecisionRecord = { "createdAt": string; "decidedAt"?: null | string; "id": string; "prompt": string; "resolution": string; "threadId": string; "threadTitle": string; "title": string };
export type ThreadMemoryEvidenceRecord = { "artifactId": string; "createdAt": string; "id": string; "kind": string; "threadId": string; "threadTitle": string; "title": string };
export type ThreadMemoryImplementedRecord = { "score": number; "summary": string; "threadId": string; "title": string; "updatedAt": string };
export type ThreadMemoryLessonRecord = { "content": string; "createdAt": string; "id": string; "matchedKeywords": Array<string>; "sourceRef": string };
export type ThreadMemoryPerformanceRecord = { "createdAt": string; "id": string; "note": string; "source": "similarity_event" | "thread_message"; "threadId": string; "threadTitle": string };
export type ThreadMemoryRecallResponse = { "generatedAt": string; "implementedFunctionality": Array<ThreadMemoryImplementedRecord>; "lessonsLearned": Array<ThreadMemoryLessonRecord>; "performanceIssues": Array<ThreadMemoryPerformanceRecord>; "previousDecisions": Array<ThreadMemoryDecisionRecord>; "relatedEvidence": Array<ThreadMemoryEvidenceRecord>; "similarThreads": Array<ThreadSimilarityCandidateRecord>; "sourceThreadId": string };
export type ThreadMemoryReindexResponse = { "functionality"?: ProjectFunctionalityRecord | null; "index": JsonObject };
export type ThreadMessageRecord = { "author": string; "content": string; "createdAt": string; "id": string; "kind": "user" | "aido_lead" | "agent_summary" | "decision_request" | "artifact" | "error" | "system_event" | "operator_note"; "metadata": JsonObject; "projectId": string; "sequence": number; "threadId": string };
export type ThreadMessageRequest = { "author"?: null | string; "content": string; "metadata"?: JsonObject };
export type ThreadMessageResultResponse = { "artifacts": Array<ThreadArtifactRecord>; "blocked": boolean; "decision"?: ThreadDecisionRecord | null; "events": Array<ThreadAgentEventRecord>; "messages": Array<ThreadMessageRecord>; "run": ThreadRunSummary; "thread": ThreadRecord };
export type ThreadModelChosenRecord = { "effort"?: null | string; "mode"?: null | string; "model"?: null | string; "provider"?: null | string; "runtime"?: null | string };
export type ThreadNoteRequest = { "author"?: null | string; "content": string; "metadata"?: JsonObject };
export type ThreadNoteResponse = { "message": ThreadMessageRecord; "thread": ThreadRecord };
export type ThreadPremiumApprovalRecord = { "costTier"?: null | string; "estimatedCostUsd"?: null | number; "reason": string; "required": boolean; "thresholdUsd"?: null | number };
export type ThreadQualityReworkRecord = { "benchmarkInsufficientData"?: boolean; "maxReworkRounds"?: null | number; "modelLabel"?: null | string; "modelQaPassRate"?: null | number; "modelReworkRate"?: null | number; "modelSuccessRate"?: null | number; "reworkRounds"?: null | number };
export type ThreadRecord = { "archivedAt"?: null | string; "archivedBy"?: null | string; "createdAt": string; "deletedAt"?: null | string; "deletedBy"?: null | string; "id": string; "lifecycleReason"?: null | string; "metadata": JsonObject; "ownerId": string; "ownerType": "workspace" | "loop" | "story" | "agent_task" | "review"; "projectId": string; "status": "open" | "queued" | "running" | "waiting_decision" | "awaiting_approval" | "blocked" | "resolved" | "archived" | "deleted"; "summary": string; "title": string; "updatedAt": string };
export type ThreadRunSummary = { "jobId"?: null | string; "loopId"?: null | string; "reason"?: null | string; "status": "queued" | "running" | "blocked" | "awaiting_approval" | "completed" | "failed" };
export type ThreadSimilarityArtifactRef = { "artifactId": string; "kind": string; "title": string };
export type ThreadSimilarityCandidateRecord = { "artifactRefs": Array<ThreadSimilarityArtifactRef>; "keywords": Array<string>; "projectId": string; "reason": string; "score": number; "status": "open" | "queued" | "running" | "waiting_decision" | "awaiting_approval" | "blocked" | "resolved" | "archived" | "deleted"; "summary": string; "threadId": string; "title": string; "updatedAt": string };
export type ThreadSimilarityEventRecord = { "action": "continue_existing" | "improve_existing" | "performance_pass" | "create_new_anyway"; "candidateThreadId": string; "createdAt": string; "functionalityId"?: string; "id": string; "projectId": string; "reason": string; "score": number; "sourceThreadId": string };
export type ThreadSimilarityMarkRequest = { "action": "continue_existing" | "improve_existing" | "performance_pass" | "create_new_anyway"; "reason"?: string; "score"?: number };
export type ThreadSimilarityMarkResponse = { "event": ThreadSimilarityEventRecord };
export type ThreadSimilarityResponse = { "candidates": Array<ThreadSimilarityCandidateRecord> };
export type ThreadTokenSummaryRecord = { "callCount": number; "knownCalls": number; "tokenStatus": "actual" | "partial" | "unknown" | "none"; "totalTokens": null | number; "unknownCalls": number };
export type ThreadUpdateRequest = { "title"?: null | string };
export type ToolCallSummary = { "createdAt": string; "id": string; "status": string; "toolName": string };
export type UsageLedgerListResponse = { "usageLedger": Array<UsageLedgerRecord> };
export type UsageLedgerRecord = { "actualCostUsd"?: null | number; "agentId"?: null | string; "cachedInputTokens": null | number; "costStatus"?: string; "createdAt": string; "currency": string; "estimatedCostUsd"?: null | number; "id": string; "inputTokens": null | number; "jobId"?: null | string; "latencyMs"?: null | number; "model": string; "outputTokens": null | number; "providerId": string; "rawUsage": JsonObject; "reasoningTokens": null | number; "requestId"?: null | string; "role"?: null | string; "runtimeType": string; "sessionId"?: null | string; "taskId"?: null | string; "tokenStatus"?: string; "toolTokens": null | number; "totalTokens": null | number; "usageSource": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type UsageSummaryProvider = { "estimatedCostUsd": null | number; "providerId": string; "totalTokens": null | number };
export type UsageSummaryRecord = { "actualCostUsd"?: null | number; "byProvider": Array<UsageSummaryProvider>; "estimatedCostUsd": null | number; "totalTokens": null | number };
export type UsageSummaryResponse = { "summary": UsageSummaryRecord };
export type UserStoryRecord = { "asA"?: null | string; "businessValue"?: null | string; "createdAt": string; "description"?: null | string; "epicId"?: null | string; "iWant"?: null | string; "id": string; "metadata": JsonObject; "owner"?: null | string; "priority": string; "projectId": string; "soThat"?: null | string; "status": string; "storyPoints"?: null | number; "title": string; "updatedAt": string; "version": number };
export type ValidationError = { "ctx"?: JsonObject; "input"?: JsonValue; "loc": Array<number | string>; "msg": string; "type": string };
export type WorkerRunOnceResponse = { "autostart": boolean; "claimedJobs": number; "completedRuns": number; "failedRuns": number; "inFlightJobs": number; "lastError"?: null | string; "lastIdleAt"?: null | string; "lastRunAt"?: null | string; "maxConcurrentJobs": number; "paused": boolean; "pollIntervalSeconds": number; "reason": string; "running": boolean; "runs": Array<JsonObject>; "status": "running" | "stopped" | "paused" | "idle" | "blocked" | "failed" | "completed" };
export type WorkerStatusResponse = { "autostart": boolean; "claimedJobs": number; "completedRuns": number; "failedRuns": number; "inFlightJobs": number; "lastError"?: null | string; "lastIdleAt"?: null | string; "lastRunAt"?: null | string; "maxConcurrentJobs": number; "paused": boolean; "pollIntervalSeconds": number; "reason": string; "running": boolean; "status": "running" | "stopped" | "paused" | "idle" | "blocked" | "failed" | "completed" };
export type WorkflowCreateRequest = { "idea"?: null | string; "kind"?: "idea_to_pr" | "project_discovery" | "issue_to_patch" | "issue_to_pr" | "qa_validation" | "release_candidate" | "pr_release_retro"; "metadata"?: JsonObject; "projectId": string; "title"?: null | string };
export type WorkflowDetailResponse = { "actionRequests"?: Array<ActionRequestRecord>; "agentRuns": Array<AgentRunRecord>; "agentToolCalls"?: Array<AgentToolCallRecord>; "artifacts"?: Array<ArtifactRecord>; "evidencePackages": Array<EvidencePackageRecord>; "jobRuns"?: Array<JobRunRecord>; "jobs": Array<JobRecord>; "modelCalls"?: Array<ModelCallRecord>; "permissionDecisions"?: Array<PermissionDecisionRecord>; "testResultRecords"?: Array<TestResultRecord>; "workflow": WorkflowRecord; "workflowEvents"?: Array<WorkflowEventRecord>; "workflowRunDetails"?: Array<WorkflowRunDetail>; "workflowRuns": Array<WorkflowRunRecord>; "workflowSteps": Array<WorkflowStepRecord>; "workspaces": Array<WorkspaceRecord> };
export type WorkflowEventRecord = { "causationId"?: null | string; "correlationId"?: null | string; "createdAt": string; "id": string; "payload": JsonObject; "projectId"?: null | string; "severity": string; "type": string; "workflowId": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type WorkflowGateAdvanceRequest = { "evidencePackageId"?: null | string; "reason"?: string };
export type WorkflowGateAdvanceResponse = { "advanced": boolean; "gateState": string; "reason": string; "workflowStep": WorkflowStepRecord };
export type WorkflowRecord = { "createdAt": string; "id": string; "kind": "idea_to_pr" | "project_discovery" | "issue_to_patch" | "issue_to_pr" | "qa_validation" | "release_candidate" | "pr_release_retro"; "metadata": JsonObject; "projectId": string; "status": "queued" | "running" | "paused" | "completed" | "failed" | "cancelled" | "blocked" | "runtime_unavailable" | "qa_failed" | "evidence_ready" | "approved_for_integration" | "promotion_failed" | "promoted_to_branch" | "pr_created"; "title": string; "updatedAt": string };
export type WorkflowResponse = { "workflow": WorkflowRecord };
export type WorkflowRunDetail = { "actionRequests"?: Array<ActionRequestRecord>; "agentRuns": Array<AgentRunRecord>; "agentToolCalls"?: Array<AgentToolCallRecord>; "artifacts"?: Array<ArtifactRecord>; "evidencePackages": Array<EvidencePackageRecord>; "jobRuns"?: Array<JobRunRecord>; "jobs": Array<JobRecord>; "modelCalls"?: Array<ModelCallRecord>; "permissionDecisions"?: Array<PermissionDecisionRecord>; "testResultRecords"?: Array<TestResultRecord>; "workflowEvents"?: Array<WorkflowEventRecord>; "workflowRun": WorkflowRunRecord; "workflowSteps": Array<WorkflowStepRecord>; "workspaces": Array<WorkspaceRecord> };
export type WorkflowRunRecord = { "completedAt"?: null | string; "id": string; "metadata": JsonObject; "projectId": string; "startedAt": string; "status": "running" | "completed" | "failed" | "cancelled" | "blocked" | "runtime_unavailable" | "qa_failed" | "evidence_ready" | "approved_for_integration" | "promotion_failed" | "promoted_to_branch" | "pr_created"; "workflowId": string };
export type WorkflowStartResponse = { "workflow": WorkflowRecord; "workflowRun": WorkflowRunRecord; "workflowSteps": Array<WorkflowStepRecord> };
export type WorkflowStatusChangeRequest = { "reason"?: string };
export type WorkflowStepRecord = { "agentProfileId"?: null | string; "createdAt": string; "id": string; "input": JsonObject; "manualModelOverride"?: null | string; "metadata": JsonObject; "modelMode"?: null | string; "name": string; "output": JsonObject; "projectId": string; "riskLevel"?: "low" | "medium" | "high" | "critical" | null; "role"?: null | string; "status": "pending" | "ready" | "running" | "completed" | "failed" | "blocked" | "skipped"; "taskType"?: null | string; "updatedAt": string; "workflowId": string; "workflowRunId": string };
export type WorkflowsListResponse = { "workflowRuns": Array<WorkflowRunRecord>; "workflowSteps": Array<WorkflowStepRecord>; "workflows": Array<WorkflowRecord> };
export type WorkspaceAllocateRequest = { "agentId": string; "baseBranch"?: string; "devcontainer"?: DevcontainerMetadata | null; "isolationType"?: "directory" | "git_worktree"; "projectId": string; "reason"?: string; "taskId": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type WorkspaceArchiveRequest = { "reason"?: string };
export type WorkspaceArchiveResponse = { "evidencePackage": EvidencePackageRecord; "workspace": WorkspaceRecord };
export type WorkspaceRecord = { "archivedAt"?: null | string; "createdAt": string; "id": string; "isolationType": "directory" | "git_worktree"; "metadata": JsonObject; "ownerAgentId": string; "path": string; "projectId": string; "status": string; "taskId": string; "updatedAt": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type WorkspaceResponse = { "workspace": WorkspaceRecord };
export type WorkspacesListResponse = { "workspaces": Array<WorkspaceRecord> };

export const OPENAPI_TITLE = "Local Control Center" as const;
export const OPENAPI_VERSION = "0.1.0" as const;

export const API_ENDPOINTS = [
	{"method": "GET", "operationId": "list_agent_profiles_api_v1_agent_profiles_get", "path": "/api/v1/agent-profiles", "summary": "List Agent Profiles"},
	{"method": "POST", "operationId": "upsert_agent_profile_api_v1_agent_profiles_post", "path": "/api/v1/agent-profiles", "summary": "Upsert Agent Profile"},
	{"method": "GET", "operationId": "list_agent_runs_api_v1_agent_runs_get", "path": "/api/v1/agent-runs", "summary": "List Agent Runs"},
	{"method": "POST", "operationId": "create_agent_run_api_v1_agent_runs_post", "path": "/api/v1/agent-runs", "summary": "Create Agent Run"},
	{"method": "GET", "operationId": "agents_api_v1_agents_get", "path": "/api/v1/agents", "summary": "Agents"},
	{"method": "POST", "operationId": "run_architect_agent_api_v1_agents_architect_runs_post", "path": "/api/v1/agents/architect/runs", "summary": "Run Architect Agent"},
	{"method": "GET", "operationId": "architect_agent_status_api_v1_agents_architect_status_get", "path": "/api/v1/agents/architect/status", "summary": "Architect Agent Status"},
	{"method": "POST", "operationId": "run_developer_agent_api_v1_agents_developer_runs_post", "path": "/api/v1/agents/developer/runs", "summary": "Run Developer Agent"},
	{"method": "GET", "operationId": "developer_agent_status_api_v1_agents_developer_status_get", "path": "/api/v1/agents/developer/status", "summary": "Developer Agent Status"},
	{"method": "POST", "operationId": "run_devops_agent_api_v1_agents_devops_runs_post", "path": "/api/v1/agents/devops/runs", "summary": "Run Devops Agent"},
	{"method": "GET", "operationId": "devops_agent_status_api_v1_agents_devops_status_get", "path": "/api/v1/agents/devops/status", "summary": "Devops Agent Status"},
	{"method": "POST", "operationId": "run_product_owner_agent_api_v1_agents_product_owner_runs_post", "path": "/api/v1/agents/product-owner/runs", "summary": "Run Product Owner Agent"},
	{"method": "GET", "operationId": "product_owner_agent_status_api_v1_agents_product_owner_status_get", "path": "/api/v1/agents/product-owner/status", "summary": "Product Owner Agent Status"},
	{"method": "POST", "operationId": "run_qa_agent_api_v1_agents_qa_runs_post", "path": "/api/v1/agents/qa/runs", "summary": "Run Qa Agent"},
	{"method": "POST", "operationId": "run_research_agent_api_v1_agents_research_runs_post", "path": "/api/v1/agents/research/runs", "summary": "Run Research Agent"},
	{"method": "GET", "operationId": "research_agent_status_api_v1_agents_research_status_get", "path": "/api/v1/agents/research/status", "summary": "Research Agent Status"},
	{"method": "POST", "operationId": "run_security_agent_api_v1_agents_security_runs_post", "path": "/api/v1/agents/security/runs", "summary": "Run Security Agent"},
	{"method": "GET", "operationId": "security_agent_status_api_v1_agents_security_status_get", "path": "/api/v1/agents/security/status", "summary": "Security Agent Status"},
	{"method": "GET", "operationId": "approvals_api_v1_approvals_get", "path": "/api/v1/approvals", "summary": "Approvals"},
	{"method": "GET", "operationId": "list_architecture_decisions_api_v1_architecture_decisions_get", "path": "/api/v1/architecture-decisions", "summary": "List Architecture Decisions"},
	{"method": "POST", "operationId": "create_architecture_decision_api_v1_architecture_decisions_post", "path": "/api/v1/architecture-decisions", "summary": "Create Architecture Decision"},
	{"method": "POST", "operationId": "start_session_api_v1_cli_sessions_post", "path": "/api/v1/cli-sessions", "summary": "Start Session"},
	{"method": "POST", "operationId": "cancel_session_api_v1_cli_sessions__session_id__cancel_post", "path": "/api/v1/cli-sessions/{session_id}/cancel", "summary": "Cancel Session"},
	{"method": "GET", "operationId": "session_events_api_v1_cli_sessions__session_id__events_get", "path": "/api/v1/cli-sessions/{session_id}/events", "summary": "Session Events"},
	{"method": "GET", "operationId": "list_credentials_api_v1_credentials_get", "path": "/api/v1/credentials", "summary": "List Credentials"},
	{"method": "POST", "operationId": "create_credential_api_v1_credentials_post", "path": "/api/v1/credentials", "summary": "Create Credential"},
	{"method": "GET", "operationId": "list_credential_audit_api_v1_credentials_audit_get", "path": "/api/v1/credentials/audit", "summary": "List Credential Audit"},
	{"method": "POST", "operationId": "migrate_credentials_api_v1_credentials_migrate_post", "path": "/api/v1/credentials/migrate", "summary": "Migrate Credentials"},
	{"method": "DELETE", "operationId": "delete_credential_api_v1_credentials__credential_id__delete", "path": "/api/v1/credentials/{credential_id}", "summary": "Delete Credential"},
	{"method": "POST", "operationId": "rotate_credential_api_v1_credentials__credential_id__rotate_post", "path": "/api/v1/credentials/{credential_id}/rotate", "summary": "Rotate Credential"},
	{"method": "POST", "operationId": "validate_credential_api_v1_credentials__credential_id__validate_post", "path": "/api/v1/credentials/{credential_id}/validate", "summary": "Validate Credential"},
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
	{"method": "GET", "operationId": "get_i18n_catalog_api_v1_i18n_catalog_get", "path": "/api/v1/i18n/catalog", "summary": "Get I18N Catalog"},
	{"method": "PUT", "operationId": "put_i18n_catalog_api_v1_i18n_catalog_put", "path": "/api/v1/i18n/catalog", "summary": "Put I18N Catalog"},
	{"method": "GET", "operationId": "list_ide_connections_api_v1_ide_connections_get", "path": "/api/v1/ide-connections", "summary": "List Ide Connections"},
	{"method": "POST", "operationId": "upsert_ide_connection_api_v1_ide_connections_post", "path": "/api/v1/ide-connections", "summary": "Upsert Ide Connection"},
	{"method": "GET", "operationId": "list_integrations_api_v1_integrations_get", "path": "/api/v1/integrations", "summary": "List Integrations"},
	{"method": "POST", "operationId": "register_mcp_server_api_v1_integrations_mcp_register_post", "path": "/api/v1/integrations/mcp/register", "summary": "Register Mcp Server"},
	{"method": "POST", "operationId": "configure_n8n_api_v1_integrations_n8n_configure_post", "path": "/api/v1/integrations/n8n/configure", "summary": "Configure N8N"},
	{"method": "POST", "operationId": "emit_n8n_api_v1_integrations_n8n_emit_post", "path": "/api/v1/integrations/n8n/emit", "summary": "Emit N8N"},
	{"method": "POST", "operationId": "emit_n8n_event_api_v1_integrations_n8n_emit_event_post", "path": "/api/v1/integrations/n8n/emit-event", "summary": "Emit N8N Event"},
	{"method": "POST", "operationId": "receive_n8n_inbound_api_v1_integrations_n8n_inbound__token__post", "path": "/api/v1/integrations/n8n/inbound/{token}", "summary": "Receive N8N Inbound"},
	{"method": "GET", "operationId": "get_n8n_status_api_v1_integrations_n8n_status_get", "path": "/api/v1/integrations/n8n/status", "summary": "Get N8N Status"},
	{"method": "POST", "operationId": "test_n8n_target_api_v1_integrations_n8n_test_post", "path": "/api/v1/integrations/n8n/test", "summary": "Test N8N Target"},
	{"method": "POST", "operationId": "receive_n8n_webhook_api_v1_integrations_n8n_webhook_post", "path": "/api/v1/integrations/n8n/webhook", "summary": "Receive N8N Webhook"},
	{"method": "POST", "operationId": "upsert_n8n_webhook_target_api_v1_integrations_n8n_webhook_targets_post", "path": "/api/v1/integrations/n8n/webhook-targets", "summary": "Upsert N8N Webhook Target"},
	{"method": "GET", "operationId": "list_jobs_api_v1_jobs_get", "path": "/api/v1/jobs", "summary": "List Jobs"},
	{"method": "POST", "operationId": "create_job_api_v1_jobs_post", "path": "/api/v1/jobs", "summary": "Create Job"},
	{"method": "POST", "operationId": "approve_action_api_v1_jobs__job_id__actions__action_id__approve_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/approve", "summary": "Approve Action"},
	{"method": "POST", "operationId": "deny_action_api_v1_jobs__job_id__actions__action_id__deny_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/deny", "summary": "Deny Action"},
	{"method": "POST", "operationId": "approve_job_api_v1_jobs__job_id__approve_post", "path": "/api/v1/jobs/{job_id}/approve", "summary": "Approve Job"},
	{"method": "POST", "operationId": "cancel_job_api_v1_jobs__job_id__cancel_post", "path": "/api/v1/jobs/{job_id}/cancel", "summary": "Cancel Job"},
	{"method": "POST", "operationId": "retry_job_api_v1_jobs__job_id__retry_post", "path": "/api/v1/jobs/{job_id}/retry", "summary": "Retry Job"},
	{"method": "GET", "operationId": "list_chats_api_v1_legacy_chats_get", "path": "/api/v1/legacy/chats", "summary": "List Chats"},
	{"method": "GET", "operationId": "list_pipelines_api_v1_legacy_pipelines_get", "path": "/api/v1/legacy/pipelines", "summary": "List Pipelines"},
	{"method": "GET", "operationId": "list_sessions_api_v1_legacy_sessions_get", "path": "/api/v1/legacy/sessions", "summary": "List Sessions"},
	{"method": "POST", "operationId": "select_directory_api_v1_local_paths_select_directory_post", "path": "/api/v1/local-paths/select-directory", "summary": "Select Directory"},
	{"method": "GET", "operationId": "list_memory_api_v1_memory_get", "path": "/api/v1/memory", "summary": "List Memory"},
	{"method": "POST", "operationId": "create_memory_api_v1_memory_post", "path": "/api/v1/memory", "summary": "Create Memory"},
	{"method": "DELETE", "operationId": "delete_memory_api_v1_memory__memory_id__delete", "path": "/api/v1/memory/{memory_id}", "summary": "Delete Memory"},
	{"method": "POST", "operationId": "execute_ai_execution_api_v1_model_gateway_ai_executions_post", "path": "/api/v1/model-gateway/ai-executions", "summary": "Execute Ai Execution"},
	{"method": "GET", "operationId": "list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get", "path": "/api/v1/model-gateway/benchmark-outcomes", "summary": "List Benchmark Outcomes"},
	{"method": "POST", "operationId": "create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post", "path": "/api/v1/model-gateway/benchmark-outcomes", "summary": "Create Benchmark Outcome"},
	{"method": "GET", "operationId": "list_benchmarks_api_v1_model_gateway_benchmarks_get", "path": "/api/v1/model-gateway/benchmarks", "summary": "List Benchmarks"},
	{"method": "GET", "operationId": "list_budget_rules_api_v1_model_gateway_budget_rules_get", "path": "/api/v1/model-gateway/budget-rules", "summary": "List Budget Rules"},
	{"method": "POST", "operationId": "create_budget_rule_api_v1_model_gateway_budget_rules_post", "path": "/api/v1/model-gateway/budget-rules", "summary": "Create Budget Rule"},
	{"method": "PATCH", "operationId": "patch_budget_rule_api_v1_model_gateway_budget_rules__rule_id__patch", "path": "/api/v1/model-gateway/budget-rules/{rule_id}", "summary": "Patch Budget Rule"},
	{"method": "GET", "operationId": "list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get", "path": "/api/v1/model-gateway/cli-runtimes", "summary": "List Cli Runtimes"},
	{"method": "POST", "operationId": "detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post", "path": "/api/v1/model-gateway/cli-runtimes/{runtime_id}/detect", "summary": "Detect Cli Runtime"},
	{"method": "POST", "operationId": "health_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__health_check_post", "path": "/api/v1/model-gateway/cli-runtimes/{runtime_id}/health-check", "summary": "Health Cli Runtime"},
	{"method": "GET", "operationId": "list_cli_sessions_api_v1_model_gateway_cli_sessions_get", "path": "/api/v1/model-gateway/cli-sessions", "summary": "List Cli Sessions"},
	{"method": "GET", "operationId": "get_cli_session_api_v1_model_gateway_cli_sessions__session_id__get", "path": "/api/v1/model-gateway/cli-sessions/{session_id}", "summary": "Get Cli Session"},
	{"method": "GET", "operationId": "list_models_api_v1_model_gateway_models_get", "path": "/api/v1/model-gateway/models", "summary": "List Models"},
	{"method": "POST", "operationId": "create_model_api_v1_model_gateway_models_post", "path": "/api/v1/model-gateway/models", "summary": "Create Model"},
	{"method": "PATCH", "operationId": "patch_model_api_v1_model_gateway_models__model_id__patch", "path": "/api/v1/model-gateway/models/{model_id}", "summary": "Patch Model"},
	{"method": "GET", "operationId": "overview_api_v1_model_gateway_overview_get", "path": "/api/v1/model-gateway/overview", "summary": "Overview"},
	{"method": "GET", "operationId": "list_pricing_snapshots_api_v1_model_gateway_pricing_snapshots_get", "path": "/api/v1/model-gateway/pricing-snapshots", "summary": "List Pricing Snapshots"},
	{"method": "POST", "operationId": "create_pricing_snapshot_api_v1_model_gateway_pricing_snapshots_post", "path": "/api/v1/model-gateway/pricing-snapshots", "summary": "Create Pricing Snapshot"},
	{"method": "GET", "operationId": "list_provider_limits_api_v1_model_gateway_provider_limits_get", "path": "/api/v1/model-gateway/provider-limits", "summary": "List Provider Limits"},
	{"method": "POST", "operationId": "create_provider_limit_api_v1_model_gateway_provider_limits_post", "path": "/api/v1/model-gateway/provider-limits", "summary": "Create Provider Limit"},
	{"method": "GET", "operationId": "provider_limit_status_api_v1_model_gateway_provider_limits_status_get", "path": "/api/v1/model-gateway/provider-limits/status", "summary": "Provider Limit Status"},
	{"method": "PATCH", "operationId": "patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch", "path": "/api/v1/model-gateway/provider-limits/{limit_id}", "summary": "Patch Provider Limit"},
	{"method": "GET", "operationId": "list_providers_api_v1_model_gateway_providers_get", "path": "/api/v1/model-gateway/providers", "summary": "List Providers"},
	{"method": "POST", "operationId": "create_provider_api_v1_model_gateway_providers_post", "path": "/api/v1/model-gateway/providers", "summary": "Create Provider"},
	{"method": "GET", "operationId": "get_provider_api_v1_model_gateway_providers__provider_id__get", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Get Provider"},
	{"method": "PATCH", "operationId": "patch_provider_api_v1_model_gateway_providers__provider_id__patch", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Patch Provider"},
	{"method": "POST", "operationId": "discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post", "path": "/api/v1/model-gateway/providers/{provider_id}/discover-models", "summary": "Discover Models"},
	{"method": "POST", "operationId": "execute_provider_embedding_api_v1_model_gateway_providers__provider_id__embeddings_post", "path": "/api/v1/model-gateway/providers/{provider_id}/embeddings", "summary": "Execute Provider Embedding"},
	{"method": "POST", "operationId": "provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post", "path": "/api/v1/model-gateway/providers/{provider_id}/health-check", "summary": "Provider Health Check"},
	{"method": "POST", "operationId": "execute_provider_image_editing_api_v1_model_gateway_providers__provider_id__images_edits_post", "path": "/api/v1/model-gateway/providers/{provider_id}/images/edits", "summary": "Execute Provider Image Editing"},
	{"method": "POST", "operationId": "execute_provider_image_generation_api_v1_model_gateway_providers__provider_id__images_generations_post", "path": "/api/v1/model-gateway/providers/{provider_id}/images/generations", "summary": "Execute Provider Image Generation"},
	{"method": "POST", "operationId": "execute_provider_rerank_api_v1_model_gateway_providers__provider_id__rerank_post", "path": "/api/v1/model-gateway/providers/{provider_id}/rerank", "summary": "Execute Provider Rerank"},
	{"method": "POST", "operationId": "test_prompt_api_v1_model_gateway_providers__provider_id__test_prompt_post", "path": "/api/v1/model-gateway/providers/{provider_id}/test-prompt", "summary": "Test Prompt"},
	{"method": "GET", "operationId": "list_role_policies_api_v1_model_gateway_role_policies_get", "path": "/api/v1/model-gateway/role-policies", "summary": "List Role Policies"},
	{"method": "POST", "operationId": "create_role_policy_api_v1_model_gateway_role_policies_post", "path": "/api/v1/model-gateway/role-policies", "summary": "Create Role Policy"},
	{"method": "PATCH", "operationId": "patch_role_policy_api_v1_model_gateway_role_policies__policy_id__patch", "path": "/api/v1/model-gateway/role-policies/{policy_id}", "summary": "Patch Role Policy"},
	{"method": "POST", "operationId": "route_execute_api_v1_model_gateway_route_execute_post", "path": "/api/v1/model-gateway/route/execute", "summary": "Route Execute"},
	{"method": "POST", "operationId": "route_preview_api_v1_model_gateway_route_preview_post", "path": "/api/v1/model-gateway/route/preview", "summary": "Route Preview"},
	{"method": "GET", "operationId": "list_routing_decisions_api_v1_model_gateway_routing_decisions_get", "path": "/api/v1/model-gateway/routing-decisions", "summary": "List Routing Decisions"},
	{"method": "GET", "operationId": "list_routing_profiles_api_v1_model_gateway_routing_profiles_get", "path": "/api/v1/model-gateway/routing-profiles", "summary": "List Routing Profiles"},
	{"method": "POST", "operationId": "create_routing_profile_api_v1_model_gateway_routing_profiles_post", "path": "/api/v1/model-gateway/routing-profiles", "summary": "Create Routing Profile"},
	{"method": "PATCH", "operationId": "patch_routing_profile_api_v1_model_gateway_routing_profiles__profile_id__patch", "path": "/api/v1/model-gateway/routing-profiles/{profile_id}", "summary": "Patch Routing Profile"},
	{"method": "GET", "operationId": "list_usage_ledger_api_v1_model_gateway_usage_ledger_get", "path": "/api/v1/model-gateway/usage-ledger", "summary": "List Usage Ledger"},
	{"method": "GET", "operationId": "usage_summary_api_v1_model_gateway_usage_ledger_summary_get", "path": "/api/v1/model-gateway/usage-ledger/summary", "summary": "Usage Summary"},
	{"method": "GET", "operationId": "list_next_steps_api_v1_next_steps_get", "path": "/api/v1/next-steps", "summary": "List Next Steps"},
	{"method": "POST", "operationId": "create_next_step_api_v1_next_steps_post", "path": "/api/v1/next-steps", "summary": "Create Next Step"},
	{"method": "PATCH", "operationId": "update_next_step_api_v1_next_steps__step_id__patch", "path": "/api/v1/next-steps/{step_id}", "summary": "Update Next Step"},
	{"method": "GET", "operationId": "local_preflight_api_v1_nvidia_nim_preflight_get", "path": "/api/v1/nvidia-nim/preflight", "summary": "Local Preflight"},
	{"method": "GET", "operationId": "list_endpoints_api_v1_ollama_endpoints_get", "path": "/api/v1/ollama/endpoints", "summary": "List Endpoints"},
	{"method": "POST", "operationId": "create_endpoint_api_v1_ollama_endpoints_post", "path": "/api/v1/ollama/endpoints", "summary": "Create Endpoint"},
	{"method": "POST", "operationId": "health_endpoint_api_v1_ollama_endpoints__endpoint_id__health_post", "path": "/api/v1/ollama/endpoints/{endpoint_id}/health", "summary": "Health Endpoint"},
	{"method": "POST", "operationId": "sync_models_api_v1_ollama_endpoints__endpoint_id__sync_models_post", "path": "/api/v1/ollama/endpoints/{endpoint_id}/sync-models", "summary": "Sync Models"},
	{"method": "GET", "operationId": "open_design_api_v1_open_design_get", "path": "/api/v1/open-design", "summary": "Open Design"},
	{"method": "GET", "operationId": "overview_api_v1_overview_get", "path": "/api/v1/overview", "summary": "Overview"},
	{"method": "POST", "operationId": "revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post", "path": "/api/v1/permissions/grants/{grant_id}/revoke", "summary": "Revoke Permission Grant"},
	{"method": "GET", "operationId": "list_plugins_api_v1_plugins_get", "path": "/api/v1/plugins", "summary": "List Plugins"},
	{"method": "GET", "operationId": "list_plugin_install_events_api_v1_plugins_install_events_get", "path": "/api/v1/plugins/install-events", "summary": "List Plugin Install Events"},
	{"method": "POST", "operationId": "install_local_plugin_api_v1_plugins_install_local_post", "path": "/api/v1/plugins/install-local", "summary": "Install Local Plugin"},
	{"method": "POST", "operationId": "scan_local_plugins_api_v1_plugins_scan_local_post", "path": "/api/v1/plugins/scan-local", "summary": "Scan Local Plugins"},
	{"method": "POST", "operationId": "disable_plugin_api_v1_plugins__plugin_id__disable_post", "path": "/api/v1/plugins/{plugin_id}/disable", "summary": "Disable Plugin"},
	{"method": "POST", "operationId": "enable_plugin_api_v1_plugins__plugin_id__enable_post", "path": "/api/v1/plugins/{plugin_id}/enable", "summary": "Enable Plugin"},
	{"method": "POST", "operationId": "validate_plugin_api_v1_plugins__plugin_id__validate_post", "path": "/api/v1/plugins/{plugin_id}/validate", "summary": "Validate Plugin"},
	{"method": "GET", "operationId": "list_policies_api_v1_policies_get", "path": "/api/v1/policies", "summary": "List Policies"},
	{"method": "POST", "operationId": "evaluate_policy_api_v1_policies_evaluate_post", "path": "/api/v1/policies/evaluate", "summary": "Evaluate Policy"},
	{"method": "GET", "operationId": "project_templates_api_v1_project_templates_get", "path": "/api/v1/project-templates", "summary": "Project Templates"},
	{"method": "GET", "operationId": "projects_api_v1_projects_get", "path": "/api/v1/projects", "summary": "Projects"},
	{"method": "POST", "operationId": "create_project_api_v1_projects_post", "path": "/api/v1/projects", "summary": "Create Project"},
	{"method": "POST", "operationId": "discover_project_api_v1_projects_discover_post", "path": "/api/v1/projects/discover", "summary": "Discover Project"},
	{"method": "PUT", "operationId": "agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put", "path": "/api/v1/projects/{project_id}/agent-profile-overrides/{profile_id}", "summary": "Agent Profile Override"},
	{"method": "GET", "operationId": "get_project_generated_image_api_v1_projects__project_id__artifacts__artifact_id__get", "path": "/api/v1/projects/{project_id}/artifacts/{artifact_id}", "summary": "Get Project Generated Image"},
	{"method": "POST", "operationId": "run_assessment_api_v1_projects__project_id__assessment_post", "path": "/api/v1/projects/{project_id}/assessment", "summary": "Run Assessment"},
	{"method": "GET", "operationId": "list_assessments_api_v1_projects__project_id__assessments_get", "path": "/api/v1/projects/{project_id}/assessments", "summary": "List Assessments"},
	{"method": "GET", "operationId": "list_findings_api_v1_projects__project_id__findings_get", "path": "/api/v1/projects/{project_id}/findings", "summary": "List Findings"},
	{"method": "GET", "operationId": "project_functionality_api_v1_projects__project_id__functionality_get", "path": "/api/v1/projects/{project_id}/functionality", "summary": "Project Functionality"},
	{"method": "POST", "operationId": "apply_git_branch_policy_api_v1_projects__project_id__git_branch_policy_apply_post", "path": "/api/v1/projects/{project_id}/git/branch-policy/apply", "summary": "Apply Git Branch Policy"},
	{"method": "GET", "operationId": "git_branches_api_v1_projects__project_id__git_branches_get", "path": "/api/v1/projects/{project_id}/git/branches", "summary": "Git Branches"},
	{"method": "POST", "operationId": "create_git_branch_api_v1_projects__project_id__git_branches_post", "path": "/api/v1/projects/{project_id}/git/branches", "summary": "Create Git Branch"},
	{"method": "POST", "operationId": "checkout_git_branch_api_v1_projects__project_id__git_checkout_post", "path": "/api/v1/projects/{project_id}/git/checkout", "summary": "Checkout Git Branch"},
	{"method": "GET", "operationId": "git_diff_api_v1_projects__project_id__git_diff_get", "path": "/api/v1/projects/{project_id}/git/diff", "summary": "Git Diff"},
	{"method": "POST", "operationId": "git_gitleaks_scan_api_v1_projects__project_id__git_gitleaks_scan_post", "path": "/api/v1/projects/{project_id}/git/gitleaks/scan", "summary": "Git Gitleaks Scan"},
	{"method": "POST", "operationId": "init_git_repository_api_v1_projects__project_id__git_init_post", "path": "/api/v1/projects/{project_id}/git/init", "summary": "Init Git Repository"},
	{"method": "POST", "operationId": "add_git_remote_api_v1_projects__project_id__git_remotes_post", "path": "/api/v1/projects/{project_id}/git/remotes", "summary": "Add Git Remote"},
	{"method": "POST", "operationId": "test_git_remote_api_v1_projects__project_id__git_remotes__name__test_post", "path": "/api/v1/projects/{project_id}/git/remotes/{name}/test", "summary": "Test Git Remote"},
	{"method": "GET", "operationId": "git_status_api_v1_projects__project_id__git_status_get", "path": "/api/v1/projects/{project_id}/git/status", "summary": "Git Status"},
	{"method": "GET", "operationId": "get_product_loop_state_api_v1_projects__project_id__product_loop_get", "path": "/api/v1/projects/{project_id}/product-loop", "summary": "Get Product Loop State"},
	{"method": "POST", "operationId": "start_product_loop_api_v1_projects__project_id__product_loop_post", "path": "/api/v1/projects/{project_id}/product-loop", "summary": "Start Product Loop"},
	{"method": "POST", "operationId": "approve_product_brief_api_v1_projects__project_id__product_loop_brief__brief_id__approve_post", "path": "/api/v1/projects/{project_id}/product-loop/brief/{brief_id}/approve", "summary": "Approve Product Brief"},
	{"method": "GET", "operationId": "get_story_spec_api_v1_projects__project_id__product_loop_stories__story_id__spec_get", "path": "/api/v1/projects/{project_id}/product-loop/stories/{story_id}/spec", "summary": "Get Story Spec"},
	{"method": "POST", "operationId": "aido_decide_product_loop_api_v1_projects__project_id__product_loop__loop_id__aido_decide_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/aido-decide", "summary": "Aido Decide Product Loop"},
	{"method": "POST", "operationId": "approve_product_loop_backlog_api_v1_projects__project_id__product_loop__loop_id__backlog_approve_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/backlog/approve", "summary": "Approve Product Loop Backlog"},
	{"method": "POST", "operationId": "apply_product_loop_feedback_api_v1_projects__project_id__product_loop__loop_id__feedback_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/feedback", "summary": "Apply Product Loop Feedback"},
	{"method": "POST", "operationId": "transition_product_loop_api_v1_projects__project_id__product_loop__loop_id__transition_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/transition", "summary": "Transition Product Loop"},
	{"method": "GET", "operationId": "team_activity_api_v1_projects__project_id__team_activity_get", "path": "/api/v1/projects/{project_id}/team-activity", "summary": "Team Activity"},
	{"method": "GET", "operationId": "list_prompts_api_v1_prompts_get", "path": "/api/v1/prompts", "summary": "List Prompts"},
	{"method": "POST", "operationId": "upsert_prompt_api_v1_prompts_post", "path": "/api/v1/prompts", "summary": "Upsert Prompt"},
	{"method": "POST", "operationId": "create_account_from_catalog_api_v1_provider_accounts_from_catalog_post", "path": "/api/v1/provider-accounts/from-catalog", "summary": "Create Account From Catalog"},
	{"method": "POST", "operationId": "sync_provider_account_models_api_v1_provider_accounts__account_id__sync_models_post", "path": "/api/v1/provider-accounts/{account_id}/sync-models", "summary": "Sync Provider Account Models"},
	{"method": "GET", "operationId": "providers_api_v1_providers_get", "path": "/api/v1/providers", "summary": "Providers"},
	{"method": "GET", "operationId": "list_catalog_api_v1_providers_catalog_get", "path": "/api/v1/providers/catalog", "summary": "List Catalog"},
	{"method": "POST", "operationId": "dismiss_remediation_api_v1_remediations__remediation_id__dismiss_post", "path": "/api/v1/remediations/{remediation_id}/dismiss", "summary": "Dismiss Remediation"},
	{"method": "POST", "operationId": "execute_remediation_api_v1_remediations__remediation_id__execute_post", "path": "/api/v1/remediations/{remediation_id}/execute", "summary": "Execute Remediation"},
	{"method": "POST", "operationId": "retrieval_reindex_api_v1_retrieval_reindex_post", "path": "/api/v1/retrieval/reindex", "summary": "Retrieval Reindex"},
	{"method": "POST", "operationId": "retrieval_search_api_v1_retrieval_search_post", "path": "/api/v1/retrieval/search", "summary": "Retrieval Search"},
	{"method": "GET", "operationId": "retrieval_status_api_v1_retrieval_status_get", "path": "/api/v1/retrieval/status", "summary": "Retrieval Status"},
	{"method": "GET", "operationId": "list_risks_api_v1_risks_get", "path": "/api/v1/risks", "summary": "List Risks"},
	{"method": "POST", "operationId": "create_risk_api_v1_risks_post", "path": "/api/v1/risks", "summary": "Create Risk"},
	{"method": "PATCH", "operationId": "update_risk_api_v1_risks__risk_id__patch", "path": "/api/v1/risks/{risk_id}", "summary": "Update Risk"},
	{"method": "GET", "operationId": "list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get", "path": "/api/v1/runtime/provider-configuration", "summary": "List Runtime Provider Configuration"},
	{"method": "GET", "operationId": "list_runtime_providers_api_v1_runtime_providers_get", "path": "/api/v1/runtime/providers", "summary": "List Runtime Providers"},
	{"method": "PATCH", "operationId": "update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch", "path": "/api/v1/sandbox/profiles/{profile_id}", "summary": "Update Sandbox Profile"},
	{"method": "POST", "operationId": "revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post", "path": "/api/v1/sandbox/profiles/{profile_id}/revoke", "summary": "Revoke Sandbox Profile"},
	{"method": "GET", "operationId": "sandbox_status_api_v1_sandbox_status_get", "path": "/api/v1/sandbox/status", "summary": "Sandbox Status"},
	{"method": "GET", "operationId": "handshake_api_v1_security_handshake_get", "path": "/api/v1/security/handshake", "summary": "Handshake"},
	{"method": "GET", "operationId": "get_self_improvement_state_api_v1_self_improvement_get", "path": "/api/v1/self-improvement", "summary": "Get Self Improvement State"},
	{"method": "POST", "operationId": "record_self_improvement_lesson_api_v1_self_improvement_lessons_post", "path": "/api/v1/self-improvement/lessons", "summary": "Record Self Improvement Lesson"},
	{"method": "POST", "operationId": "promote_self_improvement_lesson_api_v1_self_improvement_lessons__lesson_id__promote_post", "path": "/api/v1/self-improvement/lessons/{lesson_id}/promote", "summary": "Promote Self Improvement Lesson"},
	{"method": "POST", "operationId": "record_self_improvement_performance_api_v1_self_improvement_performance_records_post", "path": "/api/v1/self-improvement/performance-records", "summary": "Record Self Improvement Performance"},
	{"method": "POST", "operationId": "create_self_improvement_proposal_api_v1_self_improvement_proposals_post", "path": "/api/v1/self-improvement/proposals", "summary": "Create Self Improvement Proposal"},
	{"method": "GET", "operationId": "get_settings_api_v1_settings_get", "path": "/api/v1/settings", "summary": "Get Settings"},
	{"method": "DELETE", "operationId": "delete_setting_api_v1_settings__key__delete", "path": "/api/v1/settings/{key}", "summary": "Delete Setting"},
	{"method": "PUT", "operationId": "put_setting_api_v1_settings__key__put", "path": "/api/v1/settings/{key}", "summary": "Put Setting"},
	{"method": "GET", "operationId": "list_skills_api_v1_skills_get", "path": "/api/v1/skills", "summary": "List Skills"},
	{"method": "POST", "operationId": "sync_skills_api_v1_skills_sync_post", "path": "/api/v1/skills/sync", "summary": "Sync Skills"},
	{"method": "GET", "operationId": "teams_api_v1_teams_get", "path": "/api/v1/teams", "summary": "Teams"},
	{"method": "GET", "operationId": "telemetry_status_api_v1_telemetry_status_get", "path": "/api/v1/telemetry/status", "summary": "Telemetry Status"},
	{"method": "GET", "operationId": "list_threads_api_v1_threads_get", "path": "/api/v1/threads", "summary": "List Threads"},
	{"method": "POST", "operationId": "create_thread_api_v1_threads_post", "path": "/api/v1/threads", "summary": "Create Thread"},
	{"method": "GET", "operationId": "find_similar_threads_api_v1_threads_similar_get", "path": "/api/v1/threads/similar", "summary": "Find Similar Threads"},
	{"method": "DELETE", "operationId": "delete_thread_api_v1_threads__thread_id__delete", "path": "/api/v1/threads/{thread_id}", "summary": "Delete Thread"},
	{"method": "GET", "operationId": "get_thread_api_v1_threads__thread_id__get", "path": "/api/v1/threads/{thread_id}", "summary": "Get Thread"},
	{"method": "PATCH", "operationId": "update_thread_api_v1_threads__thread_id__patch", "path": "/api/v1/threads/{thread_id}", "summary": "Update Thread"},
	{"method": "POST", "operationId": "archive_thread_api_v1_threads__thread_id__archive_post", "path": "/api/v1/threads/{thread_id}/archive", "summary": "Archive Thread"},
	{"method": "POST", "operationId": "cancel_execution_api_v1_threads__thread_id__cancel_post", "path": "/api/v1/threads/{thread_id}/cancel", "summary": "Cancel Execution"},
	{"method": "GET", "operationId": "thread_cost_performance_api_v1_threads__thread_id__cost_performance_get", "path": "/api/v1/threads/{thread_id}/cost-performance", "summary": "Thread Cost Performance"},
	{"method": "POST", "operationId": "resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post", "path": "/api/v1/threads/{thread_id}/decisions/{decision_id}/resolve", "summary": "Resolve Decision"},
	{"method": "GET", "operationId": "thread_events_api_v1_threads__thread_id__events_get", "path": "/api/v1/threads/{thread_id}/events", "summary": "Thread Events"},
	{"method": "GET", "operationId": "thread_memory_api_v1_threads__thread_id__memory_get", "path": "/api/v1/threads/{thread_id}/memory", "summary": "Thread Memory"},
	{"method": "POST", "operationId": "reindex_thread_memory_api_v1_threads__thread_id__memory_reindex_post", "path": "/api/v1/threads/{thread_id}/memory/reindex", "summary": "Reindex Thread Memory"},
	{"method": "POST", "operationId": "post_message_api_v1_threads__thread_id__messages_post", "path": "/api/v1/threads/{thread_id}/messages", "summary": "Post Message"},
	{"method": "POST", "operationId": "post_note_api_v1_threads__thread_id__notes_post", "path": "/api/v1/threads/{thread_id}/notes", "summary": "Post Note"},
	{"method": "GET", "operationId": "list_thread_remediations_api_v1_threads__thread_id__remediations_get", "path": "/api/v1/threads/{thread_id}/remediations", "summary": "List Thread Remediations"},
	{"method": "GET", "operationId": "find_similar_to_thread_api_v1_threads__thread_id__similar_get", "path": "/api/v1/threads/{thread_id}/similar", "summary": "Find Similar To Thread"},
	{"method": "POST", "operationId": "mark_similar_thread_api_v1_threads__thread_id__similar__candidate_id__mark_post", "path": "/api/v1/threads/{thread_id}/similar/{candidate_id}/mark", "summary": "Mark Similar Thread"},
	{"method": "POST", "operationId": "unarchive_thread_api_v1_threads__thread_id__unarchive_post", "path": "/api/v1/threads/{thread_id}/unarchive", "summary": "Unarchive Thread"},
	{"method": "POST", "operationId": "worker_pause_api_v1_workers_pause_post", "path": "/api/v1/workers/pause", "summary": "Worker Pause"},
	{"method": "POST", "operationId": "worker_resume_api_v1_workers_resume_post", "path": "/api/v1/workers/resume", "summary": "Worker Resume"},
	{"method": "POST", "operationId": "worker_run_once_api_v1_workers_run_once_post", "path": "/api/v1/workers/run-once", "summary": "Worker Run Once"},
	{"method": "GET", "operationId": "worker_status_api_v1_workers_status_get", "path": "/api/v1/workers/status", "summary": "Worker Status"},
	{"method": "GET", "operationId": "list_workflows_api_v1_workflows_get", "path": "/api/v1/workflows", "summary": "List Workflows"},
	{"method": "POST", "operationId": "create_workflow_api_v1_workflows_post", "path": "/api/v1/workflows", "summary": "Create Workflow"},
	{"method": "POST", "operationId": "run_issue_to_patch_api_v1_workflows_issue_to_patch_post", "path": "/api/v1/workflows/issue-to-patch", "summary": "Run Issue To Patch"},
	{"method": "POST", "operationId": "approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post", "path": "/api/v1/workflows/issue-to-patch/{run_id}/approve", "summary": "Approve Issue To Patch"},
	{"method": "POST", "operationId": "promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post", "path": "/api/v1/workflows/issue-to-patch/{run_id}/promote", "summary": "Promote Patch To Branch"},
	{"method": "POST", "operationId": "create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post", "path": "/api/v1/workflows/issue-to-patch/{run_id}/pull-request", "summary": "Create Pull Request From Promoted Branch"},
	{"method": "POST", "operationId": "run_issue_to_pr_api_v1_workflows_issue_to_pr_post", "path": "/api/v1/workflows/issue-to-pr", "summary": "Run Issue To Pr"},
	{"method": "POST", "operationId": "approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post", "path": "/api/v1/workflows/issue-to-pr/{run_id}/approve", "summary": "Approve Issue To Pr"},
	{"method": "POST", "operationId": "promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post", "path": "/api/v1/workflows/issue-to-pr/{run_id}/promote", "summary": "Promote Issue To Pr Branch"},
	{"method": "POST", "operationId": "create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post", "path": "/api/v1/workflows/issue-to-pr/{run_id}/pull-request", "summary": "Create Pull Request From Issue To Pr"},
	{"method": "GET", "operationId": "get_workflow_api_v1_workflows__workflow_id__get", "path": "/api/v1/workflows/{workflow_id}", "summary": "Get Workflow"},
	{"method": "POST", "operationId": "cancel_workflow_api_v1_workflows__workflow_id__cancel_post", "path": "/api/v1/workflows/{workflow_id}/cancel", "summary": "Cancel Workflow"},
	{"method": "POST", "operationId": "pause_workflow_api_v1_workflows__workflow_id__pause_post", "path": "/api/v1/workflows/{workflow_id}/pause", "summary": "Pause Workflow"},
	{"method": "POST", "operationId": "resume_workflow_api_v1_workflows__workflow_id__resume_post", "path": "/api/v1/workflows/{workflow_id}/resume", "summary": "Resume Workflow"},
	{"method": "POST", "operationId": "start_workflow_api_v1_workflows__workflow_id__start_post", "path": "/api/v1/workflows/{workflow_id}/start", "summary": "Start Workflow"},
	{"method": "POST", "operationId": "advance_workflow_gate_api_v1_workflows__workflow_id__steps__step_id__advance_post", "path": "/api/v1/workflows/{workflow_id}/steps/{step_id}/advance", "summary": "Advance Workflow Gate"},
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
	"add_git_remote_api_v1_projects__project_id__git_remotes_post": GitRemoteAddRequest,
	"advance_workflow_gate_api_v1_workflows__workflow_id__steps__step_id__advance_post": WorkflowGateAdvanceRequest,
	"agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put": AgentProfileProjectOverrideRequest,
	"agents_api_v1_agents_get": never,
	"aido_decide_product_loop_api_v1_projects__project_id__product_loop__loop_id__aido_decide_post": ProductLoopAidoDecideRequest,
	"allocate_workspace_api_v1_workspaces_post": WorkspaceAllocateRequest,
	"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": ArtifactRetentionActionRequest,
	"apply_git_branch_policy_api_v1_projects__project_id__git_branch_policy_apply_post": GitBranchPolicyApplyRequest,
	"apply_product_loop_feedback_api_v1_projects__project_id__product_loop__loop_id__feedback_post": ProductLoopFeedbackRequest,
	"approvals_api_v1_approvals_get": never,
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": ApprovalReasonRequest,
	"approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post": WorkflowStatusChangeRequest,
	"approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post": WorkflowStatusChangeRequest,
	"approve_job_api_v1_jobs__job_id__approve_post": ApprovalReasonRequest,
	"approve_product_brief_api_v1_projects__project_id__product_loop_brief__brief_id__approve_post": ProductLoopApprovalRequest,
	"approve_product_loop_backlog_api_v1_projects__project_id__product_loop__loop_id__backlog_approve_post": ProductLoopApprovalRequest,
	"architect_agent_status_api_v1_agents_architect_status_get": never,
	"archive_thread_api_v1_threads__thread_id__archive_post": ThreadArchiveRequest,
	"archive_workspace_api_v1_workspaces__workspace_id__archive_post": WorkspaceArchiveRequest,
	"cancel_execution_api_v1_threads__thread_id__cancel_post": ThreadCancelRequest | null,
	"cancel_job_api_v1_jobs__job_id__cancel_post": OptionalReasonRequest,
	"cancel_session_api_v1_cli_sessions__session_id__cancel_post": unknown,
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": WorkflowStatusChangeRequest,
	"checkout_git_branch_api_v1_projects__project_id__git_checkout_post": GitCheckoutRequest,
	"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": ArtifactCleanupRequest,
	"configure_n8n_api_v1_integrations_n8n_configure_post": N8nWebhookTargetCreateRequest,
	"create_account_from_catalog_api_v1_provider_accounts_from_catalog_post": ProviderAccountFromCatalogRequest,
	"create_agent_run_api_v1_agent_runs_post": AgentRunCreateRequest,
	"create_architecture_decision_api_v1_architecture_decisions_post": ArchitectureDecisionCreateRequest,
	"create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post": ModelBenchmarkOutcomeCreateRequest,
	"create_budget_rule_api_v1_model_gateway_budget_rules_post": BudgetRuleUpsertRequest,
	"create_credential_api_v1_credentials_post": CredentialCreateRequest,
	"create_endpoint_api_v1_ollama_endpoints_post": OllamaEndpointCreateRequest,
	"create_evidence_api_v1_evidence_post": EvidenceCreateRequest,
	"create_git_branch_api_v1_projects__project_id__git_branches_post": GitBranchCreateRequest,
	"create_job_api_v1_jobs_post": JobCreateRequest,
	"create_memory_api_v1_memory_post": MemoryCreateRequest,
	"create_model_api_v1_model_gateway_models_post": ModelCatalogUpsertRequest,
	"create_next_step_api_v1_next_steps_post": NextStepCreateRequest,
	"create_pricing_snapshot_api_v1_model_gateway_pricing_snapshots_post": PricingSnapshotCreateRequest,
	"create_project_api_v1_projects_post": ProjectCreateRequest,
	"create_provider_api_v1_model_gateway_providers_post": ProviderAccountUpsertRequest,
	"create_provider_limit_api_v1_model_gateway_provider_limits_post": ProviderLimitUpsertRequest,
	"create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post": PullRequestCreateRequest,
	"create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post": PullRequestCreateRequest,
	"create_risk_api_v1_risks_post": RiskCreateRequest,
	"create_role_policy_api_v1_model_gateway_role_policies_post": RolePolicyUpsertRequest,
	"create_routing_profile_api_v1_model_gateway_routing_profiles_post": RoutingProfileUpsertRequest,
	"create_self_improvement_proposal_api_v1_self_improvement_proposals_post": SelfImprovementProposalRequest,
	"create_thread_api_v1_threads_post": ThreadCreateRequest,
	"create_workflow_api_v1_workflows_post": WorkflowCreateRequest,
	"delete_credential_api_v1_credentials__credential_id__delete": never,
	"delete_memory_api_v1_memory__memory_id__delete": MemoryDeleteRequest,
	"delete_setting_api_v1_settings__key__delete": never,
	"delete_thread_api_v1_threads__thread_id__delete": ThreadDeleteRequest | null,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": ApprovalReasonRequest,
	"detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post": unknown,
	"developer_agent_status_api_v1_agents_developer_status_get": never,
	"devops_agent_status_api_v1_agents_devops_status_get": never,
	"disable_plugin_api_v1_plugins__plugin_id__disable_post": unknown,
	"discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post": unknown,
	"discover_project_api_v1_projects_discover_post": ProjectDiscoveryRequest,
	"dismiss_remediation_api_v1_remediations__remediation_id__dismiss_post": unknown,
	"emit_n8n_api_v1_integrations_n8n_emit_post": N8nEventEmitRequest,
	"emit_n8n_event_api_v1_integrations_n8n_emit_event_post": N8nEventEmitRequest,
	"enable_plugin_api_v1_plugins__plugin_id__enable_post": unknown,
	"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluateRequest,
	"events_api_v1_events_get": never,
	"execute_ai_execution_api_v1_model_gateway_ai_executions_post": AIExecutionPlan,
	"execute_provider_embedding_api_v1_model_gateway_providers__provider_id__embeddings_post": ProviderEmbeddingRequest,
	"execute_provider_image_editing_api_v1_model_gateway_providers__provider_id__images_edits_post": ProviderImageEditingRequest,
	"execute_provider_image_generation_api_v1_model_gateway_providers__provider_id__images_generations_post": ProviderImageGenerationRequest,
	"execute_provider_rerank_api_v1_model_gateway_providers__provider_id__rerank_post": ProviderRerankRequest,
	"execute_remediation_api_v1_remediations__remediation_id__execute_post": RemediationExecuteRequest | null,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"find_similar_threads_api_v1_threads_similar_get": never,
	"find_similar_to_thread_api_v1_threads__thread_id__similar_get": never,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_cli_session_api_v1_model_gateway_cli_sessions__session_id__get": never,
	"get_evidence_api_v1_evidence__evidence_id__get": never,
	"get_i18n_catalog_api_v1_i18n_catalog_get": never,
	"get_n8n_status_api_v1_integrations_n8n_status_get": never,
	"get_product_loop_state_api_v1_projects__project_id__product_loop_get": never,
	"get_project_generated_image_api_v1_projects__project_id__artifacts__artifact_id__get": never,
	"get_provider_api_v1_model_gateway_providers__provider_id__get": never,
	"get_self_improvement_state_api_v1_self_improvement_get": never,
	"get_settings_api_v1_settings_get": never,
	"get_story_spec_api_v1_projects__project_id__product_loop_stories__story_id__spec_get": never,
	"get_thread_api_v1_threads__thread_id__get": never,
	"get_workflow_api_v1_workflows__workflow_id__get": never,
	"git_branches_api_v1_projects__project_id__git_branches_get": never,
	"git_diff_api_v1_projects__project_id__git_diff_get": never,
	"git_gitleaks_scan_api_v1_projects__project_id__git_gitleaks_scan_post": unknown,
	"git_status_api_v1_projects__project_id__git_status_get": never,
	"governance_api_v1_governance_get": never,
	"handshake_api_v1_security_handshake_get": never,
	"health_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__health_check_post": unknown,
	"health_endpoint_api_v1_ollama_endpoints__endpoint_id__health_post": unknown,
	"healthz_healthz_get": never,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactIngestRequest,
	"init_git_repository_api_v1_projects__project_id__git_init_post": GitInitRequest,
	"install_local_plugin_api_v1_plugins_install_local_post": PluginInstallLocalRequest,
	"list_agent_profiles_api_v1_agent_profiles_get": never,
	"list_agent_runs_api_v1_agent_runs_get": never,
	"list_architecture_decisions_api_v1_architecture_decisions_get": never,
	"list_assessments_api_v1_projects__project_id__assessments_get": never,
	"list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get": never,
	"list_benchmarks_api_v1_model_gateway_benchmarks_get": never,
	"list_budget_rules_api_v1_model_gateway_budget_rules_get": never,
	"list_catalog_api_v1_providers_catalog_get": never,
	"list_chats_api_v1_legacy_chats_get": never,
	"list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get": never,
	"list_cli_sessions_api_v1_model_gateway_cli_sessions_get": never,
	"list_credential_audit_api_v1_credentials_audit_get": never,
	"list_credentials_api_v1_credentials_get": never,
	"list_endpoints_api_v1_ollama_endpoints_get": never,
	"list_evidence_api_v1_evidence_get": never,
	"list_findings_api_v1_projects__project_id__findings_get": never,
	"list_ide_connections_api_v1_ide_connections_get": never,
	"list_integrations_api_v1_integrations_get": never,
	"list_jobs_api_v1_jobs_get": never,
	"list_memory_api_v1_memory_get": never,
	"list_models_api_v1_model_gateway_models_get": never,
	"list_next_steps_api_v1_next_steps_get": never,
	"list_pipelines_api_v1_legacy_pipelines_get": never,
	"list_plugin_install_events_api_v1_plugins_install_events_get": never,
	"list_plugins_api_v1_plugins_get": never,
	"list_policies_api_v1_policies_get": never,
	"list_pricing_snapshots_api_v1_model_gateway_pricing_snapshots_get": never,
	"list_prompts_api_v1_prompts_get": never,
	"list_provider_limits_api_v1_model_gateway_provider_limits_get": never,
	"list_providers_api_v1_model_gateway_providers_get": never,
	"list_risks_api_v1_risks_get": never,
	"list_role_policies_api_v1_model_gateway_role_policies_get": never,
	"list_routing_decisions_api_v1_model_gateway_routing_decisions_get": never,
	"list_routing_profiles_api_v1_model_gateway_routing_profiles_get": never,
	"list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get": never,
	"list_runtime_providers_api_v1_runtime_providers_get": never,
	"list_sessions_api_v1_legacy_sessions_get": never,
	"list_skills_api_v1_skills_get": never,
	"list_thread_remediations_api_v1_threads__thread_id__remediations_get": never,
	"list_threads_api_v1_threads_get": never,
	"list_usage_ledger_api_v1_model_gateway_usage_ledger_get": never,
	"list_workflows_api_v1_workflows_get": never,
	"list_workspaces_api_v1_workspaces_get": never,
	"local_preflight_api_v1_nvidia_nim_preflight_get": never,
	"mark_similar_thread_api_v1_threads__thread_id__similar__candidate_id__mark_post": ThreadSimilarityMarkRequest,
	"migrate_credentials_api_v1_credentials_migrate_post": CredentialMigrateRequest | null,
	"open_design_api_v1_open_design_get": never,
	"overview_api_v1_model_gateway_overview_get": never,
	"overview_api_v1_overview_get": never,
	"patch_budget_rule_api_v1_model_gateway_budget_rules__rule_id__patch": BudgetRulePatchRequest,
	"patch_model_api_v1_model_gateway_models__model_id__patch": ModelCatalogPatchRequest,
	"patch_provider_api_v1_model_gateway_providers__provider_id__patch": ProviderAccountPatchRequest,
	"patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch": ProviderLimitPatchRequest,
	"patch_role_policy_api_v1_model_gateway_role_policies__policy_id__patch": RolePolicyPatchRequest,
	"patch_routing_profile_api_v1_model_gateway_routing_profiles__profile_id__patch": RoutingProfilePatchRequest,
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": WorkflowStatusChangeRequest,
	"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": ArtifactRetentionPlanRequest,
	"post_message_api_v1_threads__thread_id__messages_post": ThreadMessageRequest,
	"post_note_api_v1_threads__thread_id__notes_post": ThreadNoteRequest,
	"product_owner_agent_status_api_v1_agents_product_owner_status_get": never,
	"project_functionality_api_v1_projects__project_id__functionality_get": never,
	"project_templates_api_v1_project_templates_get": never,
	"projects_api_v1_projects_get": never,
	"promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post": PromotePatchToBranchRequest,
	"promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post": PromotePatchToBranchRequest,
	"promote_self_improvement_lesson_api_v1_self_improvement_lessons__lesson_id__promote_post": SelfImprovementPromoteLessonRequest,
	"provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post": unknown,
	"provider_limit_status_api_v1_model_gateway_provider_limits_status_get": never,
	"providers_api_v1_providers_get": never,
	"put_i18n_catalog_api_v1_i18n_catalog_put": I18nCatalog,
	"put_setting_api_v1_settings__key__put": SetSettingRequest,
	"receive_n8n_inbound_api_v1_integrations_n8n_inbound__token__post": N8nInboundWebhookRequest,
	"receive_n8n_webhook_api_v1_integrations_n8n_webhook_post": N8nInboundWebhookRequest,
	"record_self_improvement_lesson_api_v1_self_improvement_lessons_post": SelfImprovementLessonRequest,
	"record_self_improvement_performance_api_v1_self_improvement_performance_records_post": SelfImprovementPerformanceRequest,
	"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerRegisterRequest,
	"reindex_thread_memory_api_v1_threads__thread_id__memory_reindex_post": unknown,
	"research_agent_status_api_v1_agents_research_status_get": never,
	"resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post": ThreadDecisionResolveRequest,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": WorkflowStatusChangeRequest,
	"retrieval_reindex_api_v1_retrieval_reindex_post": RetrievalReindexRequest,
	"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchRequest,
	"retrieval_status_api_v1_retrieval_status_get": never,
	"retry_job_api_v1_jobs__job_id__retry_post": OptionalReasonRequest,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": RequiredReasonRequest,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": RequiredReasonRequest,
	"rotate_credential_api_v1_credentials__credential_id__rotate_post": CredentialRotateRequest,
	"route_execute_api_v1_model_gateway_route_execute_post": RoutingPreviewRequest,
	"route_preview_api_v1_model_gateway_route_preview_post": RoutingPreviewRequest,
	"run_architect_agent_api_v1_agents_architect_runs_post": ArchitectAgentRunRequest,
	"run_assessment_api_v1_projects__project_id__assessment_post": unknown,
	"run_developer_agent_api_v1_agents_developer_runs_post": DeveloperAgentRunRequest,
	"run_devops_agent_api_v1_agents_devops_runs_post": DevOpsAgentRunRequest,
	"run_issue_to_patch_api_v1_workflows_issue_to_patch_post": IssueToPatchRequest,
	"run_issue_to_pr_api_v1_workflows_issue_to_pr_post": IssueToPrRequest,
	"run_product_owner_agent_api_v1_agents_product_owner_runs_post": ProductOwnerAgentRunRequest,
	"run_qa_agent_api_v1_agents_qa_runs_post": QAAgentRunRequest,
	"run_research_agent_api_v1_agents_research_runs_post": ResearchAgentRunRequest,
	"run_security_agent_api_v1_agents_security_runs_post": SecurityAgentRunRequest,
	"sandbox_status_api_v1_sandbox_status_get": never,
	"scan_local_plugins_api_v1_plugins_scan_local_post": PluginScanLocalRequest,
	"security_agent_status_api_v1_agents_security_status_get": never,
	"select_directory_api_v1_local_paths_select_directory_post": DirectoryPickerRequest,
	"session_events_api_v1_cli_sessions__session_id__events_get": never,
	"start_product_loop_api_v1_projects__project_id__product_loop_post": ProductLoopStartRequest,
	"start_session_api_v1_cli_sessions_post": CliSessionStartRequest,
	"start_workflow_api_v1_workflows__workflow_id__start_post": WorkflowStatusChangeRequest,
	"sync_models_api_v1_ollama_endpoints__endpoint_id__sync_models_post": unknown,
	"sync_provider_account_models_api_v1_provider_accounts__account_id__sync_models_post": unknown,
	"sync_skills_api_v1_skills_sync_post": SkillsSyncRequest,
	"team_activity_api_v1_projects__project_id__team_activity_get": never,
	"teams_api_v1_teams_get": never,
	"telemetry_status_api_v1_telemetry_status_get": never,
	"test_git_remote_api_v1_projects__project_id__git_remotes__name__test_post": GitRemoteTestRequest,
	"test_n8n_target_api_v1_integrations_n8n_test_post": N8nEventTestRequest,
	"test_prompt_api_v1_model_gateway_providers__provider_id__test_prompt_post": TestPromptRequest,
	"thread_cost_performance_api_v1_threads__thread_id__cost_performance_get": never,
	"thread_events_api_v1_threads__thread_id__events_get": never,
	"thread_memory_api_v1_threads__thread_id__memory_get": never,
	"transition_product_loop_api_v1_projects__project_id__product_loop__loop_id__transition_post": ProductLoopTransitionRequest,
	"unarchive_thread_api_v1_threads__thread_id__unarchive_post": ThreadArchiveRequest,
	"update_next_step_api_v1_next_steps__step_id__patch": NextStepUpdateRequest,
	"update_risk_api_v1_risks__risk_id__patch": RiskUpdateRequest,
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": SandboxProfilePatchRequest,
	"update_thread_api_v1_threads__thread_id__patch": ThreadUpdateRequest,
	"upsert_agent_profile_api_v1_agent_profiles_post": AgentProfileUpsertRequest,
	"upsert_ide_connection_api_v1_ide_connections_post": IdeConnectionUpsertRequest,
	"upsert_n8n_webhook_target_api_v1_integrations_n8n_webhook_targets_post": N8nWebhookTargetCreateRequest,
	"upsert_prompt_api_v1_prompts_post": PromptUpsertRequest,
	"usage_summary_api_v1_model_gateway_usage_ledger_summary_get": never,
	"validate_credential_api_v1_credentials__credential_id__validate_post": unknown,
	"validate_plugin_api_v1_plugins__plugin_id__validate_post": unknown,
	"worker_pause_api_v1_workers_pause_post": unknown,
	"worker_resume_api_v1_workers_resume_post": unknown,
	"worker_run_once_api_v1_workers_run_once_post": unknown,
	"worker_status_api_v1_workers_status_get": never
};

export type OperationResponseBodies = {
	"add_git_remote_api_v1_projects__project_id__git_remotes_post": GitRemoteMutationResponse,
	"advance_workflow_gate_api_v1_workflows__workflow_id__steps__step_id__advance_post": WorkflowGateAdvanceResponse,
	"agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put": AgentProfileResponse,
	"agents_api_v1_agents_get": AgentsListResponse,
	"aido_decide_product_loop_api_v1_projects__project_id__product_loop__loop_id__aido_decide_post": ProductLoopStateResponse,
	"allocate_workspace_api_v1_workspaces_post": WorkspaceResponse,
	"apply_artifact_retention_action_api_v1_evidence_artifacts_retention_actions_post": ArtifactRetentionActionResponse,
	"apply_git_branch_policy_api_v1_projects__project_id__git_branch_policy_apply_post": GitBranchPolicyApplyResponse,
	"apply_product_loop_feedback_api_v1_projects__project_id__product_loop__loop_id__feedback_post": ProductLoopFeedbackApplyResponse,
	"approvals_api_v1_approvals_get": ApprovalsListResponse,
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": JobMutationResponse,
	"approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post": IssueToPatchResponse,
	"approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post": IssueToPrResponse,
	"approve_job_api_v1_jobs__job_id__approve_post": JobMutationResponse,
	"approve_product_brief_api_v1_projects__project_id__product_loop_brief__brief_id__approve_post": ProductLoopStateResponse,
	"approve_product_loop_backlog_api_v1_projects__project_id__product_loop__loop_id__backlog_approve_post": ProductLoopStateResponse,
	"architect_agent_status_api_v1_agents_architect_status_get": ArchitectAgentStatusResponse,
	"archive_thread_api_v1_threads__thread_id__archive_post": ThreadArchiveResponse,
	"archive_workspace_api_v1_workspaces__workspace_id__archive_post": WorkspaceArchiveResponse,
	"cancel_execution_api_v1_threads__thread_id__cancel_post": ThreadCancelResponse,
	"cancel_job_api_v1_jobs__job_id__cancel_post": JobMutationResponse,
	"cancel_session_api_v1_cli_sessions__session_id__cancel_post": CliSessionCancelResponse,
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": WorkflowResponse,
	"checkout_git_branch_api_v1_projects__project_id__git_checkout_post": GitCheckoutResponse,
	"cleanup_artifacts_api_v1_evidence_artifacts_cleanup_post": ArtifactCleanupResponse,
	"configure_n8n_api_v1_integrations_n8n_configure_post": N8nWebhookTargetResponse,
	"create_account_from_catalog_api_v1_provider_accounts_from_catalog_post": ProviderAccountResponse,
	"create_agent_run_api_v1_agent_runs_post": AgentRunResponse,
	"create_architecture_decision_api_v1_architecture_decisions_post": ArchitectureDecisionResponse,
	"create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post": ModelBenchmarkOutcomeResponse,
	"create_budget_rule_api_v1_model_gateway_budget_rules_post": BudgetRuleResponse,
	"create_credential_api_v1_credentials_post": CredentialResponse,
	"create_endpoint_api_v1_ollama_endpoints_post": OllamaEndpointResponse,
	"create_evidence_api_v1_evidence_post": EvidencePackageResponse,
	"create_git_branch_api_v1_projects__project_id__git_branches_post": GitBranchMutationResponse,
	"create_job_api_v1_jobs_post": JobMutationResponse,
	"create_memory_api_v1_memory_post": MemoryResponse,
	"create_model_api_v1_model_gateway_models_post": ModelCatalogResponse,
	"create_next_step_api_v1_next_steps_post": NextStepResponse,
	"create_pricing_snapshot_api_v1_model_gateway_pricing_snapshots_post": PricingSnapshotResponse,
	"create_project_api_v1_projects_post": ProjectResponse,
	"create_provider_api_v1_model_gateway_providers_post": ProviderAccountResponse,
	"create_provider_limit_api_v1_model_gateway_provider_limits_post": ProviderLimitResponse,
	"create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post": IssueToPrResponse,
	"create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post": IssueToPatchResponse,
	"create_risk_api_v1_risks_post": RiskResponse,
	"create_role_policy_api_v1_model_gateway_role_policies_post": RolePolicyResponse,
	"create_routing_profile_api_v1_model_gateway_routing_profiles_post": RoutingProfileResponse,
	"create_self_improvement_proposal_api_v1_self_improvement_proposals_post": SelfImprovementProposalResponse,
	"create_thread_api_v1_threads_post": ThreadDetailResponse,
	"create_workflow_api_v1_workflows_post": WorkflowResponse,
	"delete_credential_api_v1_credentials__credential_id__delete": CredentialDeleteResponse,
	"delete_memory_api_v1_memory__memory_id__delete": MemoryResponse,
	"delete_setting_api_v1_settings__key__delete": never,
	"delete_thread_api_v1_threads__thread_id__delete": ThreadDeleteResponse,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": JobMutationResponse,
	"detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post": RuntimeDetectionResponse,
	"developer_agent_status_api_v1_agents_developer_status_get": DeveloperAgentStatusResponse,
	"devops_agent_status_api_v1_agents_devops_status_get": DevOpsAgentStatusResponse,
	"disable_plugin_api_v1_plugins__plugin_id__disable_post": PluginResponse,
	"discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post": DiscoverModelsResponse,
	"discover_project_api_v1_projects_discover_post": ProjectDiscoveryResponse,
	"dismiss_remediation_api_v1_remediations__remediation_id__dismiss_post": RemediationDismissResponse,
	"emit_n8n_api_v1_integrations_n8n_emit_post": N8nEventDeliveryResponse,
	"emit_n8n_event_api_v1_integrations_n8n_emit_event_post": N8nEventDeliveryResponse,
	"enable_plugin_api_v1_plugins__plugin_id__enable_post": PluginResponse,
	"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluationResponse,
	"events_api_v1_events_get": never,
	"execute_ai_execution_api_v1_model_gateway_ai_executions_post": AIExecutionResponse,
	"execute_provider_embedding_api_v1_model_gateway_providers__provider_id__embeddings_post": ProviderEmbeddingResponse,
	"execute_provider_image_editing_api_v1_model_gateway_providers__provider_id__images_edits_post": ProviderImageEditingResponse,
	"execute_provider_image_generation_api_v1_model_gateway_providers__provider_id__images_generations_post": ProviderImageGenerationResponse,
	"execute_provider_rerank_api_v1_model_gateway_providers__provider_id__rerank_post": ProviderRerankResponse,
	"execute_remediation_api_v1_remediations__remediation_id__execute_post": RemediationExecuteResponse,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"find_similar_threads_api_v1_threads_similar_get": ThreadSimilarityResponse,
	"find_similar_to_thread_api_v1_threads__thread_id__similar_get": ThreadSimilarityResponse,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_cli_session_api_v1_model_gateway_cli_sessions__session_id__get": CliSessionResponse,
	"get_evidence_api_v1_evidence__evidence_id__get": EvidenceDetailResponse,
	"get_i18n_catalog_api_v1_i18n_catalog_get": I18nCatalogResponse,
	"get_n8n_status_api_v1_integrations_n8n_status_get": N8nIntegrationStatusResponse,
	"get_product_loop_state_api_v1_projects__project_id__product_loop_get": ProductLoopStateResponse,
	"get_project_generated_image_api_v1_projects__project_id__artifacts__artifact_id__get": never,
	"get_provider_api_v1_model_gateway_providers__provider_id__get": ProviderAccountResponse,
	"get_self_improvement_state_api_v1_self_improvement_get": SelfImprovementStateResponse,
	"get_settings_api_v1_settings_get": SettingsResponse,
	"get_story_spec_api_v1_projects__project_id__product_loop_stories__story_id__spec_get": StorySpecResponse,
	"get_thread_api_v1_threads__thread_id__get": ThreadDetailResponse,
	"get_workflow_api_v1_workflows__workflow_id__get": WorkflowDetailResponse,
	"git_branches_api_v1_projects__project_id__git_branches_get": GitBranchesResponse,
	"git_diff_api_v1_projects__project_id__git_diff_get": GitDiffResponse,
	"git_gitleaks_scan_api_v1_projects__project_id__git_gitleaks_scan_post": GitGitleaksScanResponse,
	"git_status_api_v1_projects__project_id__git_status_get": GitStatusResponse,
	"governance_api_v1_governance_get": GovernanceResponse,
	"handshake_api_v1_security_handshake_get": HandshakeResponse,
	"health_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__health_check_post": RuntimeHealthResponse,
	"health_endpoint_api_v1_ollama_endpoints__endpoint_id__health_post": OllamaEndpointHealthResponse,
	"healthz_healthz_get": HealthResponse,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactResponse,
	"init_git_repository_api_v1_projects__project_id__git_init_post": GitInitResponse,
	"install_local_plugin_api_v1_plugins_install_local_post": PluginResponse,
	"list_agent_profiles_api_v1_agent_profiles_get": AgentProfilesListResponse,
	"list_agent_runs_api_v1_agent_runs_get": AgentRunsListResponse,
	"list_architecture_decisions_api_v1_architecture_decisions_get": ArchitectureDecisionsListResponse,
	"list_assessments_api_v1_projects__project_id__assessments_get": ProjectAssessmentsListResponse,
	"list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get": ModelBenchmarkOutcomesListResponse,
	"list_benchmarks_api_v1_model_gateway_benchmarks_get": ModelBenchmarksListResponse,
	"list_budget_rules_api_v1_model_gateway_budget_rules_get": BudgetRulesListResponse,
	"list_catalog_api_v1_providers_catalog_get": ProviderCatalogListResponse,
	"list_chats_api_v1_legacy_chats_get": ChatsListResponse,
	"list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get": CliRuntimesListResponse,
	"list_cli_sessions_api_v1_model_gateway_cli_sessions_get": CliSessionsListResponse,
	"list_credential_audit_api_v1_credentials_audit_get": CredentialAuditResponse,
	"list_credentials_api_v1_credentials_get": CredentialsListResponse,
	"list_endpoints_api_v1_ollama_endpoints_get": OllamaEndpointsListResponse,
	"list_evidence_api_v1_evidence_get": EvidenceListResponse,
	"list_findings_api_v1_projects__project_id__findings_get": ProjectFindingsListResponse,
	"list_ide_connections_api_v1_ide_connections_get": IdeConnectionsListResponse,
	"list_integrations_api_v1_integrations_get": IntegrationsListResponse,
	"list_jobs_api_v1_jobs_get": JobsListResponse,
	"list_memory_api_v1_memory_get": MemoryListResponse,
	"list_models_api_v1_model_gateway_models_get": ModelCatalogListResponse,
	"list_next_steps_api_v1_next_steps_get": NextStepsListResponse,
	"list_pipelines_api_v1_legacy_pipelines_get": PipelinesListResponse,
	"list_plugin_install_events_api_v1_plugins_install_events_get": PluginInstallEventsResponse,
	"list_plugins_api_v1_plugins_get": PluginsListResponse,
	"list_policies_api_v1_policies_get": PoliciesListResponse,
	"list_pricing_snapshots_api_v1_model_gateway_pricing_snapshots_get": PricingSnapshotsListResponse,
	"list_prompts_api_v1_prompts_get": PromptTemplatesListResponse,
	"list_provider_limits_api_v1_model_gateway_provider_limits_get": ProviderLimitsListResponse,
	"list_providers_api_v1_model_gateway_providers_get": ProviderAccountsListResponse,
	"list_risks_api_v1_risks_get": RisksListResponse,
	"list_role_policies_api_v1_model_gateway_role_policies_get": RolePoliciesListResponse,
	"list_routing_decisions_api_v1_model_gateway_routing_decisions_get": RoutingDecisionsListResponse,
	"list_routing_profiles_api_v1_model_gateway_routing_profiles_get": RoutingProfilesListResponse,
	"list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get": RuntimeProviderConfigurationResponse,
	"list_runtime_providers_api_v1_runtime_providers_get": RuntimeProvidersResponse,
	"list_sessions_api_v1_legacy_sessions_get": SessionsListResponse,
	"list_skills_api_v1_skills_get": SkillsListResponse,
	"list_thread_remediations_api_v1_threads__thread_id__remediations_get": RemediationListResponse,
	"list_threads_api_v1_threads_get": ThreadListResponse,
	"list_usage_ledger_api_v1_model_gateway_usage_ledger_get": UsageLedgerListResponse,
	"list_workflows_api_v1_workflows_get": WorkflowsListResponse,
	"list_workspaces_api_v1_workspaces_get": WorkspacesListResponse,
	"local_preflight_api_v1_nvidia_nim_preflight_get": NvidiaNimPreflightResponse,
	"mark_similar_thread_api_v1_threads__thread_id__similar__candidate_id__mark_post": ThreadSimilarityMarkResponse,
	"migrate_credentials_api_v1_credentials_migrate_post": CredentialMigrationResponse,
	"open_design_api_v1_open_design_get": OpenDesignResponse,
	"overview_api_v1_model_gateway_overview_get": ModelGatewayOverviewResponse,
	"overview_api_v1_overview_get": OverviewResponse,
	"patch_budget_rule_api_v1_model_gateway_budget_rules__rule_id__patch": BudgetRuleResponse,
	"patch_model_api_v1_model_gateway_models__model_id__patch": ModelCatalogResponse,
	"patch_provider_api_v1_model_gateway_providers__provider_id__patch": ProviderAccountResponse,
	"patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch": ProviderLimitResponse,
	"patch_role_policy_api_v1_model_gateway_role_policies__policy_id__patch": RolePolicyResponse,
	"patch_routing_profile_api_v1_model_gateway_routing_profiles__profile_id__patch": RoutingProfileResponse,
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": WorkflowResponse,
	"plan_artifact_retention_api_v1_evidence_artifacts_retention_post": ArtifactRetentionPlanResponse,
	"post_message_api_v1_threads__thread_id__messages_post": ThreadMessageResultResponse,
	"post_note_api_v1_threads__thread_id__notes_post": ThreadNoteResponse,
	"product_owner_agent_status_api_v1_agents_product_owner_status_get": ProductOwnerAgentStatusResponse,
	"project_functionality_api_v1_projects__project_id__functionality_get": ProjectFunctionalityResponse,
	"project_templates_api_v1_project_templates_get": ProjectTemplatesResponse,
	"projects_api_v1_projects_get": ProjectsListResponse,
	"promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post": IssueToPrResponse,
	"promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post": IssueToPatchResponse,
	"promote_self_improvement_lesson_api_v1_self_improvement_lessons__lesson_id__promote_post": SelfImprovementLessonResponse,
	"provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post": ProviderHealthResponse,
	"provider_limit_status_api_v1_model_gateway_provider_limits_status_get": ProviderLimitStatusResponse,
	"providers_api_v1_providers_get": ProvidersListResponse,
	"put_i18n_catalog_api_v1_i18n_catalog_put": I18nCatalogResponse,
	"put_setting_api_v1_settings__key__put": never,
	"receive_n8n_inbound_api_v1_integrations_n8n_inbound__token__post": N8nInboundWebhookResponse,
	"receive_n8n_webhook_api_v1_integrations_n8n_webhook_post": N8nInboundWebhookResponse,
	"record_self_improvement_lesson_api_v1_self_improvement_lessons_post": SelfImprovementLessonResponse,
	"record_self_improvement_performance_api_v1_self_improvement_performance_records_post": SelfImprovementPerformanceResponse,
	"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerResponse,
	"reindex_thread_memory_api_v1_threads__thread_id__memory_reindex_post": ThreadMemoryReindexResponse,
	"research_agent_status_api_v1_agents_research_status_get": ResearchAgentStatusResponse,
	"resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post": ThreadDecisionResolveResponse,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": WorkflowResponse,
	"retrieval_reindex_api_v1_retrieval_reindex_post": RetrievalReindexResponse,
	"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchResponse,
	"retrieval_status_api_v1_retrieval_status_get": RetrievalStatusResponse,
	"retry_job_api_v1_jobs__job_id__retry_post": JobMutationResponse,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": PermissionGrantResponse,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": SandboxProfileResponse,
	"rotate_credential_api_v1_credentials__credential_id__rotate_post": CredentialResponse,
	"route_execute_api_v1_model_gateway_route_execute_post": RouteExecuteResponse,
	"route_preview_api_v1_model_gateway_route_preview_post": RoutingPreviewResponse,
	"run_architect_agent_api_v1_agents_architect_runs_post": ArchitectAgentRunResponse,
	"run_assessment_api_v1_projects__project_id__assessment_post": ProjectAssessmentRunResponse,
	"run_developer_agent_api_v1_agents_developer_runs_post": DeveloperAgentRunResponse,
	"run_devops_agent_api_v1_agents_devops_runs_post": DevOpsAgentRunResponse,
	"run_issue_to_patch_api_v1_workflows_issue_to_patch_post": IssueToPatchResponse,
	"run_issue_to_pr_api_v1_workflows_issue_to_pr_post": IssueToPrResponse,
	"run_product_owner_agent_api_v1_agents_product_owner_runs_post": ProductOwnerAgentRunResponse,
	"run_qa_agent_api_v1_agents_qa_runs_post": QAAgentRunResponse,
	"run_research_agent_api_v1_agents_research_runs_post": ResearchAgentRunResponse,
	"run_security_agent_api_v1_agents_security_runs_post": SecurityAgentRunResponse,
	"sandbox_status_api_v1_sandbox_status_get": SandboxStatusResponse,
	"scan_local_plugins_api_v1_plugins_scan_local_post": PluginScanLocalResponse,
	"security_agent_status_api_v1_agents_security_status_get": SecurityAgentStatusResponse,
	"select_directory_api_v1_local_paths_select_directory_post": DirectoryPickerResponse,
	"session_events_api_v1_cli_sessions__session_id__events_get": CliSessionEventsResponse,
	"start_product_loop_api_v1_projects__project_id__product_loop_post": ProductLoopResumeResponse,
	"start_session_api_v1_cli_sessions_post": CliSessionStartResponse,
	"start_workflow_api_v1_workflows__workflow_id__start_post": WorkflowStartResponse,
	"sync_models_api_v1_ollama_endpoints__endpoint_id__sync_models_post": OllamaSyncModelsResponse,
	"sync_provider_account_models_api_v1_provider_accounts__account_id__sync_models_post": DiscoverModelsResponse,
	"sync_skills_api_v1_skills_sync_post": SkillsSyncResponse,
	"team_activity_api_v1_projects__project_id__team_activity_get": TeamActivityResponse,
	"teams_api_v1_teams_get": TeamsListResponse,
	"telemetry_status_api_v1_telemetry_status_get": TelemetryStatusResponse,
	"test_git_remote_api_v1_projects__project_id__git_remotes__name__test_post": GitRemoteTestResponse,
	"test_n8n_target_api_v1_integrations_n8n_test_post": N8nEventDeliveryResponse,
	"test_prompt_api_v1_model_gateway_providers__provider_id__test_prompt_post": TestPromptResponse,
	"thread_cost_performance_api_v1_threads__thread_id__cost_performance_get": ThreadCostPerformanceResponse,
	"thread_events_api_v1_threads__thread_id__events_get": ThreadEventsResponse,
	"thread_memory_api_v1_threads__thread_id__memory_get": ThreadMemoryRecallResponse,
	"transition_product_loop_api_v1_projects__project_id__product_loop__loop_id__transition_post": ProductLoopResumeResponse,
	"unarchive_thread_api_v1_threads__thread_id__unarchive_post": ThreadArchiveResponse,
	"update_next_step_api_v1_next_steps__step_id__patch": NextStepResponse,
	"update_risk_api_v1_risks__risk_id__patch": RiskResponse,
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": SandboxProfileMutationResponse,
	"update_thread_api_v1_threads__thread_id__patch": ThreadDetailResponse,
	"upsert_agent_profile_api_v1_agent_profiles_post": AgentProfileResponse,
	"upsert_ide_connection_api_v1_ide_connections_post": IdeConnectionResponse,
	"upsert_n8n_webhook_target_api_v1_integrations_n8n_webhook_targets_post": N8nWebhookTargetResponse,
	"upsert_prompt_api_v1_prompts_post": PromptResponse,
	"usage_summary_api_v1_model_gateway_usage_ledger_summary_get": UsageSummaryResponse,
	"validate_credential_api_v1_credentials__credential_id__validate_post": CredentialValidationResponse,
	"validate_plugin_api_v1_plugins__plugin_id__validate_post": PluginValidationResponse,
	"worker_pause_api_v1_workers_pause_post": WorkerStatusResponse,
	"worker_resume_api_v1_workers_resume_post": WorkerStatusResponse,
	"worker_run_once_api_v1_workers_run_once_post": WorkerRunOnceResponse,
	"worker_status_api_v1_workers_status_get": WorkerStatusResponse
};

export type OperationRequestBody<T extends ApiOperationId> = OperationRequestBodies[T];
export type OperationResponse<T extends ApiOperationId> = OperationResponseBodies[T];

export const OPERATIONS_BY_ID = {
	"list_agent_profiles_api_v1_agent_profiles_get": {"method": "GET", "operationId": "list_agent_profiles_api_v1_agent_profiles_get", "path": "/api/v1/agent-profiles", "summary": "List Agent Profiles"},
	"upsert_agent_profile_api_v1_agent_profiles_post": {"method": "POST", "operationId": "upsert_agent_profile_api_v1_agent_profiles_post", "path": "/api/v1/agent-profiles", "summary": "Upsert Agent Profile"},
	"list_agent_runs_api_v1_agent_runs_get": {"method": "GET", "operationId": "list_agent_runs_api_v1_agent_runs_get", "path": "/api/v1/agent-runs", "summary": "List Agent Runs"},
	"create_agent_run_api_v1_agent_runs_post": {"method": "POST", "operationId": "create_agent_run_api_v1_agent_runs_post", "path": "/api/v1/agent-runs", "summary": "Create Agent Run"},
	"agents_api_v1_agents_get": {"method": "GET", "operationId": "agents_api_v1_agents_get", "path": "/api/v1/agents", "summary": "Agents"},
	"run_architect_agent_api_v1_agents_architect_runs_post": {"method": "POST", "operationId": "run_architect_agent_api_v1_agents_architect_runs_post", "path": "/api/v1/agents/architect/runs", "summary": "Run Architect Agent"},
	"architect_agent_status_api_v1_agents_architect_status_get": {"method": "GET", "operationId": "architect_agent_status_api_v1_agents_architect_status_get", "path": "/api/v1/agents/architect/status", "summary": "Architect Agent Status"},
	"run_developer_agent_api_v1_agents_developer_runs_post": {"method": "POST", "operationId": "run_developer_agent_api_v1_agents_developer_runs_post", "path": "/api/v1/agents/developer/runs", "summary": "Run Developer Agent"},
	"developer_agent_status_api_v1_agents_developer_status_get": {"method": "GET", "operationId": "developer_agent_status_api_v1_agents_developer_status_get", "path": "/api/v1/agents/developer/status", "summary": "Developer Agent Status"},
	"run_devops_agent_api_v1_agents_devops_runs_post": {"method": "POST", "operationId": "run_devops_agent_api_v1_agents_devops_runs_post", "path": "/api/v1/agents/devops/runs", "summary": "Run Devops Agent"},
	"devops_agent_status_api_v1_agents_devops_status_get": {"method": "GET", "operationId": "devops_agent_status_api_v1_agents_devops_status_get", "path": "/api/v1/agents/devops/status", "summary": "Devops Agent Status"},
	"run_product_owner_agent_api_v1_agents_product_owner_runs_post": {"method": "POST", "operationId": "run_product_owner_agent_api_v1_agents_product_owner_runs_post", "path": "/api/v1/agents/product-owner/runs", "summary": "Run Product Owner Agent"},
	"product_owner_agent_status_api_v1_agents_product_owner_status_get": {"method": "GET", "operationId": "product_owner_agent_status_api_v1_agents_product_owner_status_get", "path": "/api/v1/agents/product-owner/status", "summary": "Product Owner Agent Status"},
	"run_qa_agent_api_v1_agents_qa_runs_post": {"method": "POST", "operationId": "run_qa_agent_api_v1_agents_qa_runs_post", "path": "/api/v1/agents/qa/runs", "summary": "Run Qa Agent"},
	"run_research_agent_api_v1_agents_research_runs_post": {"method": "POST", "operationId": "run_research_agent_api_v1_agents_research_runs_post", "path": "/api/v1/agents/research/runs", "summary": "Run Research Agent"},
	"research_agent_status_api_v1_agents_research_status_get": {"method": "GET", "operationId": "research_agent_status_api_v1_agents_research_status_get", "path": "/api/v1/agents/research/status", "summary": "Research Agent Status"},
	"run_security_agent_api_v1_agents_security_runs_post": {"method": "POST", "operationId": "run_security_agent_api_v1_agents_security_runs_post", "path": "/api/v1/agents/security/runs", "summary": "Run Security Agent"},
	"security_agent_status_api_v1_agents_security_status_get": {"method": "GET", "operationId": "security_agent_status_api_v1_agents_security_status_get", "path": "/api/v1/agents/security/status", "summary": "Security Agent Status"},
	"approvals_api_v1_approvals_get": {"method": "GET", "operationId": "approvals_api_v1_approvals_get", "path": "/api/v1/approvals", "summary": "Approvals"},
	"list_architecture_decisions_api_v1_architecture_decisions_get": {"method": "GET", "operationId": "list_architecture_decisions_api_v1_architecture_decisions_get", "path": "/api/v1/architecture-decisions", "summary": "List Architecture Decisions"},
	"create_architecture_decision_api_v1_architecture_decisions_post": {"method": "POST", "operationId": "create_architecture_decision_api_v1_architecture_decisions_post", "path": "/api/v1/architecture-decisions", "summary": "Create Architecture Decision"},
	"start_session_api_v1_cli_sessions_post": {"method": "POST", "operationId": "start_session_api_v1_cli_sessions_post", "path": "/api/v1/cli-sessions", "summary": "Start Session"},
	"cancel_session_api_v1_cli_sessions__session_id__cancel_post": {"method": "POST", "operationId": "cancel_session_api_v1_cli_sessions__session_id__cancel_post", "path": "/api/v1/cli-sessions/{session_id}/cancel", "summary": "Cancel Session"},
	"session_events_api_v1_cli_sessions__session_id__events_get": {"method": "GET", "operationId": "session_events_api_v1_cli_sessions__session_id__events_get", "path": "/api/v1/cli-sessions/{session_id}/events", "summary": "Session Events"},
	"list_credentials_api_v1_credentials_get": {"method": "GET", "operationId": "list_credentials_api_v1_credentials_get", "path": "/api/v1/credentials", "summary": "List Credentials"},
	"create_credential_api_v1_credentials_post": {"method": "POST", "operationId": "create_credential_api_v1_credentials_post", "path": "/api/v1/credentials", "summary": "Create Credential"},
	"list_credential_audit_api_v1_credentials_audit_get": {"method": "GET", "operationId": "list_credential_audit_api_v1_credentials_audit_get", "path": "/api/v1/credentials/audit", "summary": "List Credential Audit"},
	"migrate_credentials_api_v1_credentials_migrate_post": {"method": "POST", "operationId": "migrate_credentials_api_v1_credentials_migrate_post", "path": "/api/v1/credentials/migrate", "summary": "Migrate Credentials"},
	"delete_credential_api_v1_credentials__credential_id__delete": {"method": "DELETE", "operationId": "delete_credential_api_v1_credentials__credential_id__delete", "path": "/api/v1/credentials/{credential_id}", "summary": "Delete Credential"},
	"rotate_credential_api_v1_credentials__credential_id__rotate_post": {"method": "POST", "operationId": "rotate_credential_api_v1_credentials__credential_id__rotate_post", "path": "/api/v1/credentials/{credential_id}/rotate", "summary": "Rotate Credential"},
	"validate_credential_api_v1_credentials__credential_id__validate_post": {"method": "POST", "operationId": "validate_credential_api_v1_credentials__credential_id__validate_post", "path": "/api/v1/credentials/{credential_id}/validate", "summary": "Validate Credential"},
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
	"get_i18n_catalog_api_v1_i18n_catalog_get": {"method": "GET", "operationId": "get_i18n_catalog_api_v1_i18n_catalog_get", "path": "/api/v1/i18n/catalog", "summary": "Get I18N Catalog"},
	"put_i18n_catalog_api_v1_i18n_catalog_put": {"method": "PUT", "operationId": "put_i18n_catalog_api_v1_i18n_catalog_put", "path": "/api/v1/i18n/catalog", "summary": "Put I18N Catalog"},
	"list_ide_connections_api_v1_ide_connections_get": {"method": "GET", "operationId": "list_ide_connections_api_v1_ide_connections_get", "path": "/api/v1/ide-connections", "summary": "List Ide Connections"},
	"upsert_ide_connection_api_v1_ide_connections_post": {"method": "POST", "operationId": "upsert_ide_connection_api_v1_ide_connections_post", "path": "/api/v1/ide-connections", "summary": "Upsert Ide Connection"},
	"list_integrations_api_v1_integrations_get": {"method": "GET", "operationId": "list_integrations_api_v1_integrations_get", "path": "/api/v1/integrations", "summary": "List Integrations"},
	"register_mcp_server_api_v1_integrations_mcp_register_post": {"method": "POST", "operationId": "register_mcp_server_api_v1_integrations_mcp_register_post", "path": "/api/v1/integrations/mcp/register", "summary": "Register Mcp Server"},
	"configure_n8n_api_v1_integrations_n8n_configure_post": {"method": "POST", "operationId": "configure_n8n_api_v1_integrations_n8n_configure_post", "path": "/api/v1/integrations/n8n/configure", "summary": "Configure N8N"},
	"emit_n8n_api_v1_integrations_n8n_emit_post": {"method": "POST", "operationId": "emit_n8n_api_v1_integrations_n8n_emit_post", "path": "/api/v1/integrations/n8n/emit", "summary": "Emit N8N"},
	"emit_n8n_event_api_v1_integrations_n8n_emit_event_post": {"method": "POST", "operationId": "emit_n8n_event_api_v1_integrations_n8n_emit_event_post", "path": "/api/v1/integrations/n8n/emit-event", "summary": "Emit N8N Event"},
	"receive_n8n_inbound_api_v1_integrations_n8n_inbound__token__post": {"method": "POST", "operationId": "receive_n8n_inbound_api_v1_integrations_n8n_inbound__token__post", "path": "/api/v1/integrations/n8n/inbound/{token}", "summary": "Receive N8N Inbound"},
	"get_n8n_status_api_v1_integrations_n8n_status_get": {"method": "GET", "operationId": "get_n8n_status_api_v1_integrations_n8n_status_get", "path": "/api/v1/integrations/n8n/status", "summary": "Get N8N Status"},
	"test_n8n_target_api_v1_integrations_n8n_test_post": {"method": "POST", "operationId": "test_n8n_target_api_v1_integrations_n8n_test_post", "path": "/api/v1/integrations/n8n/test", "summary": "Test N8N Target"},
	"receive_n8n_webhook_api_v1_integrations_n8n_webhook_post": {"method": "POST", "operationId": "receive_n8n_webhook_api_v1_integrations_n8n_webhook_post", "path": "/api/v1/integrations/n8n/webhook", "summary": "Receive N8N Webhook"},
	"upsert_n8n_webhook_target_api_v1_integrations_n8n_webhook_targets_post": {"method": "POST", "operationId": "upsert_n8n_webhook_target_api_v1_integrations_n8n_webhook_targets_post", "path": "/api/v1/integrations/n8n/webhook-targets", "summary": "Upsert N8N Webhook Target"},
	"list_jobs_api_v1_jobs_get": {"method": "GET", "operationId": "list_jobs_api_v1_jobs_get", "path": "/api/v1/jobs", "summary": "List Jobs"},
	"create_job_api_v1_jobs_post": {"method": "POST", "operationId": "create_job_api_v1_jobs_post", "path": "/api/v1/jobs", "summary": "Create Job"},
	"approve_action_api_v1_jobs__job_id__actions__action_id__approve_post": {"method": "POST", "operationId": "approve_action_api_v1_jobs__job_id__actions__action_id__approve_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/approve", "summary": "Approve Action"},
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": {"method": "POST", "operationId": "deny_action_api_v1_jobs__job_id__actions__action_id__deny_post", "path": "/api/v1/jobs/{job_id}/actions/{action_id}/deny", "summary": "Deny Action"},
	"approve_job_api_v1_jobs__job_id__approve_post": {"method": "POST", "operationId": "approve_job_api_v1_jobs__job_id__approve_post", "path": "/api/v1/jobs/{job_id}/approve", "summary": "Approve Job"},
	"cancel_job_api_v1_jobs__job_id__cancel_post": {"method": "POST", "operationId": "cancel_job_api_v1_jobs__job_id__cancel_post", "path": "/api/v1/jobs/{job_id}/cancel", "summary": "Cancel Job"},
	"retry_job_api_v1_jobs__job_id__retry_post": {"method": "POST", "operationId": "retry_job_api_v1_jobs__job_id__retry_post", "path": "/api/v1/jobs/{job_id}/retry", "summary": "Retry Job"},
	"list_chats_api_v1_legacy_chats_get": {"method": "GET", "operationId": "list_chats_api_v1_legacy_chats_get", "path": "/api/v1/legacy/chats", "summary": "List Chats"},
	"list_pipelines_api_v1_legacy_pipelines_get": {"method": "GET", "operationId": "list_pipelines_api_v1_legacy_pipelines_get", "path": "/api/v1/legacy/pipelines", "summary": "List Pipelines"},
	"list_sessions_api_v1_legacy_sessions_get": {"method": "GET", "operationId": "list_sessions_api_v1_legacy_sessions_get", "path": "/api/v1/legacy/sessions", "summary": "List Sessions"},
	"select_directory_api_v1_local_paths_select_directory_post": {"method": "POST", "operationId": "select_directory_api_v1_local_paths_select_directory_post", "path": "/api/v1/local-paths/select-directory", "summary": "Select Directory"},
	"list_memory_api_v1_memory_get": {"method": "GET", "operationId": "list_memory_api_v1_memory_get", "path": "/api/v1/memory", "summary": "List Memory"},
	"create_memory_api_v1_memory_post": {"method": "POST", "operationId": "create_memory_api_v1_memory_post", "path": "/api/v1/memory", "summary": "Create Memory"},
	"delete_memory_api_v1_memory__memory_id__delete": {"method": "DELETE", "operationId": "delete_memory_api_v1_memory__memory_id__delete", "path": "/api/v1/memory/{memory_id}", "summary": "Delete Memory"},
	"execute_ai_execution_api_v1_model_gateway_ai_executions_post": {"method": "POST", "operationId": "execute_ai_execution_api_v1_model_gateway_ai_executions_post", "path": "/api/v1/model-gateway/ai-executions", "summary": "Execute Ai Execution"},
	"list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get": {"method": "GET", "operationId": "list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get", "path": "/api/v1/model-gateway/benchmark-outcomes", "summary": "List Benchmark Outcomes"},
	"create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post": {"method": "POST", "operationId": "create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post", "path": "/api/v1/model-gateway/benchmark-outcomes", "summary": "Create Benchmark Outcome"},
	"list_benchmarks_api_v1_model_gateway_benchmarks_get": {"method": "GET", "operationId": "list_benchmarks_api_v1_model_gateway_benchmarks_get", "path": "/api/v1/model-gateway/benchmarks", "summary": "List Benchmarks"},
	"list_budget_rules_api_v1_model_gateway_budget_rules_get": {"method": "GET", "operationId": "list_budget_rules_api_v1_model_gateway_budget_rules_get", "path": "/api/v1/model-gateway/budget-rules", "summary": "List Budget Rules"},
	"create_budget_rule_api_v1_model_gateway_budget_rules_post": {"method": "POST", "operationId": "create_budget_rule_api_v1_model_gateway_budget_rules_post", "path": "/api/v1/model-gateway/budget-rules", "summary": "Create Budget Rule"},
	"patch_budget_rule_api_v1_model_gateway_budget_rules__rule_id__patch": {"method": "PATCH", "operationId": "patch_budget_rule_api_v1_model_gateway_budget_rules__rule_id__patch", "path": "/api/v1/model-gateway/budget-rules/{rule_id}", "summary": "Patch Budget Rule"},
	"list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get": {"method": "GET", "operationId": "list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get", "path": "/api/v1/model-gateway/cli-runtimes", "summary": "List Cli Runtimes"},
	"detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post": {"method": "POST", "operationId": "detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post", "path": "/api/v1/model-gateway/cli-runtimes/{runtime_id}/detect", "summary": "Detect Cli Runtime"},
	"health_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__health_check_post": {"method": "POST", "operationId": "health_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__health_check_post", "path": "/api/v1/model-gateway/cli-runtimes/{runtime_id}/health-check", "summary": "Health Cli Runtime"},
	"list_cli_sessions_api_v1_model_gateway_cli_sessions_get": {"method": "GET", "operationId": "list_cli_sessions_api_v1_model_gateway_cli_sessions_get", "path": "/api/v1/model-gateway/cli-sessions", "summary": "List Cli Sessions"},
	"get_cli_session_api_v1_model_gateway_cli_sessions__session_id__get": {"method": "GET", "operationId": "get_cli_session_api_v1_model_gateway_cli_sessions__session_id__get", "path": "/api/v1/model-gateway/cli-sessions/{session_id}", "summary": "Get Cli Session"},
	"list_models_api_v1_model_gateway_models_get": {"method": "GET", "operationId": "list_models_api_v1_model_gateway_models_get", "path": "/api/v1/model-gateway/models", "summary": "List Models"},
	"create_model_api_v1_model_gateway_models_post": {"method": "POST", "operationId": "create_model_api_v1_model_gateway_models_post", "path": "/api/v1/model-gateway/models", "summary": "Create Model"},
	"patch_model_api_v1_model_gateway_models__model_id__patch": {"method": "PATCH", "operationId": "patch_model_api_v1_model_gateway_models__model_id__patch", "path": "/api/v1/model-gateway/models/{model_id}", "summary": "Patch Model"},
	"overview_api_v1_model_gateway_overview_get": {"method": "GET", "operationId": "overview_api_v1_model_gateway_overview_get", "path": "/api/v1/model-gateway/overview", "summary": "Overview"},
	"list_pricing_snapshots_api_v1_model_gateway_pricing_snapshots_get": {"method": "GET", "operationId": "list_pricing_snapshots_api_v1_model_gateway_pricing_snapshots_get", "path": "/api/v1/model-gateway/pricing-snapshots", "summary": "List Pricing Snapshots"},
	"create_pricing_snapshot_api_v1_model_gateway_pricing_snapshots_post": {"method": "POST", "operationId": "create_pricing_snapshot_api_v1_model_gateway_pricing_snapshots_post", "path": "/api/v1/model-gateway/pricing-snapshots", "summary": "Create Pricing Snapshot"},
	"list_provider_limits_api_v1_model_gateway_provider_limits_get": {"method": "GET", "operationId": "list_provider_limits_api_v1_model_gateway_provider_limits_get", "path": "/api/v1/model-gateway/provider-limits", "summary": "List Provider Limits"},
	"create_provider_limit_api_v1_model_gateway_provider_limits_post": {"method": "POST", "operationId": "create_provider_limit_api_v1_model_gateway_provider_limits_post", "path": "/api/v1/model-gateway/provider-limits", "summary": "Create Provider Limit"},
	"provider_limit_status_api_v1_model_gateway_provider_limits_status_get": {"method": "GET", "operationId": "provider_limit_status_api_v1_model_gateway_provider_limits_status_get", "path": "/api/v1/model-gateway/provider-limits/status", "summary": "Provider Limit Status"},
	"patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch": {"method": "PATCH", "operationId": "patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch", "path": "/api/v1/model-gateway/provider-limits/{limit_id}", "summary": "Patch Provider Limit"},
	"list_providers_api_v1_model_gateway_providers_get": {"method": "GET", "operationId": "list_providers_api_v1_model_gateway_providers_get", "path": "/api/v1/model-gateway/providers", "summary": "List Providers"},
	"create_provider_api_v1_model_gateway_providers_post": {"method": "POST", "operationId": "create_provider_api_v1_model_gateway_providers_post", "path": "/api/v1/model-gateway/providers", "summary": "Create Provider"},
	"get_provider_api_v1_model_gateway_providers__provider_id__get": {"method": "GET", "operationId": "get_provider_api_v1_model_gateway_providers__provider_id__get", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Get Provider"},
	"patch_provider_api_v1_model_gateway_providers__provider_id__patch": {"method": "PATCH", "operationId": "patch_provider_api_v1_model_gateway_providers__provider_id__patch", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Patch Provider"},
	"discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post": {"method": "POST", "operationId": "discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post", "path": "/api/v1/model-gateway/providers/{provider_id}/discover-models", "summary": "Discover Models"},
	"execute_provider_embedding_api_v1_model_gateway_providers__provider_id__embeddings_post": {"method": "POST", "operationId": "execute_provider_embedding_api_v1_model_gateway_providers__provider_id__embeddings_post", "path": "/api/v1/model-gateway/providers/{provider_id}/embeddings", "summary": "Execute Provider Embedding"},
	"provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post": {"method": "POST", "operationId": "provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post", "path": "/api/v1/model-gateway/providers/{provider_id}/health-check", "summary": "Provider Health Check"},
	"execute_provider_image_editing_api_v1_model_gateway_providers__provider_id__images_edits_post": {"method": "POST", "operationId": "execute_provider_image_editing_api_v1_model_gateway_providers__provider_id__images_edits_post", "path": "/api/v1/model-gateway/providers/{provider_id}/images/edits", "summary": "Execute Provider Image Editing"},
	"execute_provider_image_generation_api_v1_model_gateway_providers__provider_id__images_generations_post": {"method": "POST", "operationId": "execute_provider_image_generation_api_v1_model_gateway_providers__provider_id__images_generations_post", "path": "/api/v1/model-gateway/providers/{provider_id}/images/generations", "summary": "Execute Provider Image Generation"},
	"execute_provider_rerank_api_v1_model_gateway_providers__provider_id__rerank_post": {"method": "POST", "operationId": "execute_provider_rerank_api_v1_model_gateway_providers__provider_id__rerank_post", "path": "/api/v1/model-gateway/providers/{provider_id}/rerank", "summary": "Execute Provider Rerank"},
	"test_prompt_api_v1_model_gateway_providers__provider_id__test_prompt_post": {"method": "POST", "operationId": "test_prompt_api_v1_model_gateway_providers__provider_id__test_prompt_post", "path": "/api/v1/model-gateway/providers/{provider_id}/test-prompt", "summary": "Test Prompt"},
	"list_role_policies_api_v1_model_gateway_role_policies_get": {"method": "GET", "operationId": "list_role_policies_api_v1_model_gateway_role_policies_get", "path": "/api/v1/model-gateway/role-policies", "summary": "List Role Policies"},
	"create_role_policy_api_v1_model_gateway_role_policies_post": {"method": "POST", "operationId": "create_role_policy_api_v1_model_gateway_role_policies_post", "path": "/api/v1/model-gateway/role-policies", "summary": "Create Role Policy"},
	"patch_role_policy_api_v1_model_gateway_role_policies__policy_id__patch": {"method": "PATCH", "operationId": "patch_role_policy_api_v1_model_gateway_role_policies__policy_id__patch", "path": "/api/v1/model-gateway/role-policies/{policy_id}", "summary": "Patch Role Policy"},
	"route_execute_api_v1_model_gateway_route_execute_post": {"method": "POST", "operationId": "route_execute_api_v1_model_gateway_route_execute_post", "path": "/api/v1/model-gateway/route/execute", "summary": "Route Execute"},
	"route_preview_api_v1_model_gateway_route_preview_post": {"method": "POST", "operationId": "route_preview_api_v1_model_gateway_route_preview_post", "path": "/api/v1/model-gateway/route/preview", "summary": "Route Preview"},
	"list_routing_decisions_api_v1_model_gateway_routing_decisions_get": {"method": "GET", "operationId": "list_routing_decisions_api_v1_model_gateway_routing_decisions_get", "path": "/api/v1/model-gateway/routing-decisions", "summary": "List Routing Decisions"},
	"list_routing_profiles_api_v1_model_gateway_routing_profiles_get": {"method": "GET", "operationId": "list_routing_profiles_api_v1_model_gateway_routing_profiles_get", "path": "/api/v1/model-gateway/routing-profiles", "summary": "List Routing Profiles"},
	"create_routing_profile_api_v1_model_gateway_routing_profiles_post": {"method": "POST", "operationId": "create_routing_profile_api_v1_model_gateway_routing_profiles_post", "path": "/api/v1/model-gateway/routing-profiles", "summary": "Create Routing Profile"},
	"patch_routing_profile_api_v1_model_gateway_routing_profiles__profile_id__patch": {"method": "PATCH", "operationId": "patch_routing_profile_api_v1_model_gateway_routing_profiles__profile_id__patch", "path": "/api/v1/model-gateway/routing-profiles/{profile_id}", "summary": "Patch Routing Profile"},
	"list_usage_ledger_api_v1_model_gateway_usage_ledger_get": {"method": "GET", "operationId": "list_usage_ledger_api_v1_model_gateway_usage_ledger_get", "path": "/api/v1/model-gateway/usage-ledger", "summary": "List Usage Ledger"},
	"usage_summary_api_v1_model_gateway_usage_ledger_summary_get": {"method": "GET", "operationId": "usage_summary_api_v1_model_gateway_usage_ledger_summary_get", "path": "/api/v1/model-gateway/usage-ledger/summary", "summary": "Usage Summary"},
	"list_next_steps_api_v1_next_steps_get": {"method": "GET", "operationId": "list_next_steps_api_v1_next_steps_get", "path": "/api/v1/next-steps", "summary": "List Next Steps"},
	"create_next_step_api_v1_next_steps_post": {"method": "POST", "operationId": "create_next_step_api_v1_next_steps_post", "path": "/api/v1/next-steps", "summary": "Create Next Step"},
	"update_next_step_api_v1_next_steps__step_id__patch": {"method": "PATCH", "operationId": "update_next_step_api_v1_next_steps__step_id__patch", "path": "/api/v1/next-steps/{step_id}", "summary": "Update Next Step"},
	"local_preflight_api_v1_nvidia_nim_preflight_get": {"method": "GET", "operationId": "local_preflight_api_v1_nvidia_nim_preflight_get", "path": "/api/v1/nvidia-nim/preflight", "summary": "Local Preflight"},
	"list_endpoints_api_v1_ollama_endpoints_get": {"method": "GET", "operationId": "list_endpoints_api_v1_ollama_endpoints_get", "path": "/api/v1/ollama/endpoints", "summary": "List Endpoints"},
	"create_endpoint_api_v1_ollama_endpoints_post": {"method": "POST", "operationId": "create_endpoint_api_v1_ollama_endpoints_post", "path": "/api/v1/ollama/endpoints", "summary": "Create Endpoint"},
	"health_endpoint_api_v1_ollama_endpoints__endpoint_id__health_post": {"method": "POST", "operationId": "health_endpoint_api_v1_ollama_endpoints__endpoint_id__health_post", "path": "/api/v1/ollama/endpoints/{endpoint_id}/health", "summary": "Health Endpoint"},
	"sync_models_api_v1_ollama_endpoints__endpoint_id__sync_models_post": {"method": "POST", "operationId": "sync_models_api_v1_ollama_endpoints__endpoint_id__sync_models_post", "path": "/api/v1/ollama/endpoints/{endpoint_id}/sync-models", "summary": "Sync Models"},
	"open_design_api_v1_open_design_get": {"method": "GET", "operationId": "open_design_api_v1_open_design_get", "path": "/api/v1/open-design", "summary": "Open Design"},
	"overview_api_v1_overview_get": {"method": "GET", "operationId": "overview_api_v1_overview_get", "path": "/api/v1/overview", "summary": "Overview"},
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": {"method": "POST", "operationId": "revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post", "path": "/api/v1/permissions/grants/{grant_id}/revoke", "summary": "Revoke Permission Grant"},
	"list_plugins_api_v1_plugins_get": {"method": "GET", "operationId": "list_plugins_api_v1_plugins_get", "path": "/api/v1/plugins", "summary": "List Plugins"},
	"list_plugin_install_events_api_v1_plugins_install_events_get": {"method": "GET", "operationId": "list_plugin_install_events_api_v1_plugins_install_events_get", "path": "/api/v1/plugins/install-events", "summary": "List Plugin Install Events"},
	"install_local_plugin_api_v1_plugins_install_local_post": {"method": "POST", "operationId": "install_local_plugin_api_v1_plugins_install_local_post", "path": "/api/v1/plugins/install-local", "summary": "Install Local Plugin"},
	"scan_local_plugins_api_v1_plugins_scan_local_post": {"method": "POST", "operationId": "scan_local_plugins_api_v1_plugins_scan_local_post", "path": "/api/v1/plugins/scan-local", "summary": "Scan Local Plugins"},
	"disable_plugin_api_v1_plugins__plugin_id__disable_post": {"method": "POST", "operationId": "disable_plugin_api_v1_plugins__plugin_id__disable_post", "path": "/api/v1/plugins/{plugin_id}/disable", "summary": "Disable Plugin"},
	"enable_plugin_api_v1_plugins__plugin_id__enable_post": {"method": "POST", "operationId": "enable_plugin_api_v1_plugins__plugin_id__enable_post", "path": "/api/v1/plugins/{plugin_id}/enable", "summary": "Enable Plugin"},
	"validate_plugin_api_v1_plugins__plugin_id__validate_post": {"method": "POST", "operationId": "validate_plugin_api_v1_plugins__plugin_id__validate_post", "path": "/api/v1/plugins/{plugin_id}/validate", "summary": "Validate Plugin"},
	"list_policies_api_v1_policies_get": {"method": "GET", "operationId": "list_policies_api_v1_policies_get", "path": "/api/v1/policies", "summary": "List Policies"},
	"evaluate_policy_api_v1_policies_evaluate_post": {"method": "POST", "operationId": "evaluate_policy_api_v1_policies_evaluate_post", "path": "/api/v1/policies/evaluate", "summary": "Evaluate Policy"},
	"project_templates_api_v1_project_templates_get": {"method": "GET", "operationId": "project_templates_api_v1_project_templates_get", "path": "/api/v1/project-templates", "summary": "Project Templates"},
	"projects_api_v1_projects_get": {"method": "GET", "operationId": "projects_api_v1_projects_get", "path": "/api/v1/projects", "summary": "Projects"},
	"create_project_api_v1_projects_post": {"method": "POST", "operationId": "create_project_api_v1_projects_post", "path": "/api/v1/projects", "summary": "Create Project"},
	"discover_project_api_v1_projects_discover_post": {"method": "POST", "operationId": "discover_project_api_v1_projects_discover_post", "path": "/api/v1/projects/discover", "summary": "Discover Project"},
	"agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put": {"method": "PUT", "operationId": "agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put", "path": "/api/v1/projects/{project_id}/agent-profile-overrides/{profile_id}", "summary": "Agent Profile Override"},
	"get_project_generated_image_api_v1_projects__project_id__artifacts__artifact_id__get": {"method": "GET", "operationId": "get_project_generated_image_api_v1_projects__project_id__artifacts__artifact_id__get", "path": "/api/v1/projects/{project_id}/artifacts/{artifact_id}", "summary": "Get Project Generated Image"},
	"run_assessment_api_v1_projects__project_id__assessment_post": {"method": "POST", "operationId": "run_assessment_api_v1_projects__project_id__assessment_post", "path": "/api/v1/projects/{project_id}/assessment", "summary": "Run Assessment"},
	"list_assessments_api_v1_projects__project_id__assessments_get": {"method": "GET", "operationId": "list_assessments_api_v1_projects__project_id__assessments_get", "path": "/api/v1/projects/{project_id}/assessments", "summary": "List Assessments"},
	"list_findings_api_v1_projects__project_id__findings_get": {"method": "GET", "operationId": "list_findings_api_v1_projects__project_id__findings_get", "path": "/api/v1/projects/{project_id}/findings", "summary": "List Findings"},
	"project_functionality_api_v1_projects__project_id__functionality_get": {"method": "GET", "operationId": "project_functionality_api_v1_projects__project_id__functionality_get", "path": "/api/v1/projects/{project_id}/functionality", "summary": "Project Functionality"},
	"apply_git_branch_policy_api_v1_projects__project_id__git_branch_policy_apply_post": {"method": "POST", "operationId": "apply_git_branch_policy_api_v1_projects__project_id__git_branch_policy_apply_post", "path": "/api/v1/projects/{project_id}/git/branch-policy/apply", "summary": "Apply Git Branch Policy"},
	"git_branches_api_v1_projects__project_id__git_branches_get": {"method": "GET", "operationId": "git_branches_api_v1_projects__project_id__git_branches_get", "path": "/api/v1/projects/{project_id}/git/branches", "summary": "Git Branches"},
	"create_git_branch_api_v1_projects__project_id__git_branches_post": {"method": "POST", "operationId": "create_git_branch_api_v1_projects__project_id__git_branches_post", "path": "/api/v1/projects/{project_id}/git/branches", "summary": "Create Git Branch"},
	"checkout_git_branch_api_v1_projects__project_id__git_checkout_post": {"method": "POST", "operationId": "checkout_git_branch_api_v1_projects__project_id__git_checkout_post", "path": "/api/v1/projects/{project_id}/git/checkout", "summary": "Checkout Git Branch"},
	"git_diff_api_v1_projects__project_id__git_diff_get": {"method": "GET", "operationId": "git_diff_api_v1_projects__project_id__git_diff_get", "path": "/api/v1/projects/{project_id}/git/diff", "summary": "Git Diff"},
	"git_gitleaks_scan_api_v1_projects__project_id__git_gitleaks_scan_post": {"method": "POST", "operationId": "git_gitleaks_scan_api_v1_projects__project_id__git_gitleaks_scan_post", "path": "/api/v1/projects/{project_id}/git/gitleaks/scan", "summary": "Git Gitleaks Scan"},
	"init_git_repository_api_v1_projects__project_id__git_init_post": {"method": "POST", "operationId": "init_git_repository_api_v1_projects__project_id__git_init_post", "path": "/api/v1/projects/{project_id}/git/init", "summary": "Init Git Repository"},
	"add_git_remote_api_v1_projects__project_id__git_remotes_post": {"method": "POST", "operationId": "add_git_remote_api_v1_projects__project_id__git_remotes_post", "path": "/api/v1/projects/{project_id}/git/remotes", "summary": "Add Git Remote"},
	"test_git_remote_api_v1_projects__project_id__git_remotes__name__test_post": {"method": "POST", "operationId": "test_git_remote_api_v1_projects__project_id__git_remotes__name__test_post", "path": "/api/v1/projects/{project_id}/git/remotes/{name}/test", "summary": "Test Git Remote"},
	"git_status_api_v1_projects__project_id__git_status_get": {"method": "GET", "operationId": "git_status_api_v1_projects__project_id__git_status_get", "path": "/api/v1/projects/{project_id}/git/status", "summary": "Git Status"},
	"get_product_loop_state_api_v1_projects__project_id__product_loop_get": {"method": "GET", "operationId": "get_product_loop_state_api_v1_projects__project_id__product_loop_get", "path": "/api/v1/projects/{project_id}/product-loop", "summary": "Get Product Loop State"},
	"start_product_loop_api_v1_projects__project_id__product_loop_post": {"method": "POST", "operationId": "start_product_loop_api_v1_projects__project_id__product_loop_post", "path": "/api/v1/projects/{project_id}/product-loop", "summary": "Start Product Loop"},
	"approve_product_brief_api_v1_projects__project_id__product_loop_brief__brief_id__approve_post": {"method": "POST", "operationId": "approve_product_brief_api_v1_projects__project_id__product_loop_brief__brief_id__approve_post", "path": "/api/v1/projects/{project_id}/product-loop/brief/{brief_id}/approve", "summary": "Approve Product Brief"},
	"get_story_spec_api_v1_projects__project_id__product_loop_stories__story_id__spec_get": {"method": "GET", "operationId": "get_story_spec_api_v1_projects__project_id__product_loop_stories__story_id__spec_get", "path": "/api/v1/projects/{project_id}/product-loop/stories/{story_id}/spec", "summary": "Get Story Spec"},
	"aido_decide_product_loop_api_v1_projects__project_id__product_loop__loop_id__aido_decide_post": {"method": "POST", "operationId": "aido_decide_product_loop_api_v1_projects__project_id__product_loop__loop_id__aido_decide_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/aido-decide", "summary": "Aido Decide Product Loop"},
	"approve_product_loop_backlog_api_v1_projects__project_id__product_loop__loop_id__backlog_approve_post": {"method": "POST", "operationId": "approve_product_loop_backlog_api_v1_projects__project_id__product_loop__loop_id__backlog_approve_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/backlog/approve", "summary": "Approve Product Loop Backlog"},
	"apply_product_loop_feedback_api_v1_projects__project_id__product_loop__loop_id__feedback_post": {"method": "POST", "operationId": "apply_product_loop_feedback_api_v1_projects__project_id__product_loop__loop_id__feedback_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/feedback", "summary": "Apply Product Loop Feedback"},
	"transition_product_loop_api_v1_projects__project_id__product_loop__loop_id__transition_post": {"method": "POST", "operationId": "transition_product_loop_api_v1_projects__project_id__product_loop__loop_id__transition_post", "path": "/api/v1/projects/{project_id}/product-loop/{loop_id}/transition", "summary": "Transition Product Loop"},
	"team_activity_api_v1_projects__project_id__team_activity_get": {"method": "GET", "operationId": "team_activity_api_v1_projects__project_id__team_activity_get", "path": "/api/v1/projects/{project_id}/team-activity", "summary": "Team Activity"},
	"list_prompts_api_v1_prompts_get": {"method": "GET", "operationId": "list_prompts_api_v1_prompts_get", "path": "/api/v1/prompts", "summary": "List Prompts"},
	"upsert_prompt_api_v1_prompts_post": {"method": "POST", "operationId": "upsert_prompt_api_v1_prompts_post", "path": "/api/v1/prompts", "summary": "Upsert Prompt"},
	"create_account_from_catalog_api_v1_provider_accounts_from_catalog_post": {"method": "POST", "operationId": "create_account_from_catalog_api_v1_provider_accounts_from_catalog_post", "path": "/api/v1/provider-accounts/from-catalog", "summary": "Create Account From Catalog"},
	"sync_provider_account_models_api_v1_provider_accounts__account_id__sync_models_post": {"method": "POST", "operationId": "sync_provider_account_models_api_v1_provider_accounts__account_id__sync_models_post", "path": "/api/v1/provider-accounts/{account_id}/sync-models", "summary": "Sync Provider Account Models"},
	"providers_api_v1_providers_get": {"method": "GET", "operationId": "providers_api_v1_providers_get", "path": "/api/v1/providers", "summary": "Providers"},
	"list_catalog_api_v1_providers_catalog_get": {"method": "GET", "operationId": "list_catalog_api_v1_providers_catalog_get", "path": "/api/v1/providers/catalog", "summary": "List Catalog"},
	"dismiss_remediation_api_v1_remediations__remediation_id__dismiss_post": {"method": "POST", "operationId": "dismiss_remediation_api_v1_remediations__remediation_id__dismiss_post", "path": "/api/v1/remediations/{remediation_id}/dismiss", "summary": "Dismiss Remediation"},
	"execute_remediation_api_v1_remediations__remediation_id__execute_post": {"method": "POST", "operationId": "execute_remediation_api_v1_remediations__remediation_id__execute_post", "path": "/api/v1/remediations/{remediation_id}/execute", "summary": "Execute Remediation"},
	"retrieval_reindex_api_v1_retrieval_reindex_post": {"method": "POST", "operationId": "retrieval_reindex_api_v1_retrieval_reindex_post", "path": "/api/v1/retrieval/reindex", "summary": "Retrieval Reindex"},
	"retrieval_search_api_v1_retrieval_search_post": {"method": "POST", "operationId": "retrieval_search_api_v1_retrieval_search_post", "path": "/api/v1/retrieval/search", "summary": "Retrieval Search"},
	"retrieval_status_api_v1_retrieval_status_get": {"method": "GET", "operationId": "retrieval_status_api_v1_retrieval_status_get", "path": "/api/v1/retrieval/status", "summary": "Retrieval Status"},
	"list_risks_api_v1_risks_get": {"method": "GET", "operationId": "list_risks_api_v1_risks_get", "path": "/api/v1/risks", "summary": "List Risks"},
	"create_risk_api_v1_risks_post": {"method": "POST", "operationId": "create_risk_api_v1_risks_post", "path": "/api/v1/risks", "summary": "Create Risk"},
	"update_risk_api_v1_risks__risk_id__patch": {"method": "PATCH", "operationId": "update_risk_api_v1_risks__risk_id__patch", "path": "/api/v1/risks/{risk_id}", "summary": "Update Risk"},
	"list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get": {"method": "GET", "operationId": "list_runtime_provider_configuration_api_v1_runtime_provider_configuration_get", "path": "/api/v1/runtime/provider-configuration", "summary": "List Runtime Provider Configuration"},
	"list_runtime_providers_api_v1_runtime_providers_get": {"method": "GET", "operationId": "list_runtime_providers_api_v1_runtime_providers_get", "path": "/api/v1/runtime/providers", "summary": "List Runtime Providers"},
	"update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch": {"method": "PATCH", "operationId": "update_sandbox_profile_api_v1_sandbox_profiles__profile_id__patch", "path": "/api/v1/sandbox/profiles/{profile_id}", "summary": "Update Sandbox Profile"},
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": {"method": "POST", "operationId": "revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post", "path": "/api/v1/sandbox/profiles/{profile_id}/revoke", "summary": "Revoke Sandbox Profile"},
	"sandbox_status_api_v1_sandbox_status_get": {"method": "GET", "operationId": "sandbox_status_api_v1_sandbox_status_get", "path": "/api/v1/sandbox/status", "summary": "Sandbox Status"},
	"handshake_api_v1_security_handshake_get": {"method": "GET", "operationId": "handshake_api_v1_security_handshake_get", "path": "/api/v1/security/handshake", "summary": "Handshake"},
	"get_self_improvement_state_api_v1_self_improvement_get": {"method": "GET", "operationId": "get_self_improvement_state_api_v1_self_improvement_get", "path": "/api/v1/self-improvement", "summary": "Get Self Improvement State"},
	"record_self_improvement_lesson_api_v1_self_improvement_lessons_post": {"method": "POST", "operationId": "record_self_improvement_lesson_api_v1_self_improvement_lessons_post", "path": "/api/v1/self-improvement/lessons", "summary": "Record Self Improvement Lesson"},
	"promote_self_improvement_lesson_api_v1_self_improvement_lessons__lesson_id__promote_post": {"method": "POST", "operationId": "promote_self_improvement_lesson_api_v1_self_improvement_lessons__lesson_id__promote_post", "path": "/api/v1/self-improvement/lessons/{lesson_id}/promote", "summary": "Promote Self Improvement Lesson"},
	"record_self_improvement_performance_api_v1_self_improvement_performance_records_post": {"method": "POST", "operationId": "record_self_improvement_performance_api_v1_self_improvement_performance_records_post", "path": "/api/v1/self-improvement/performance-records", "summary": "Record Self Improvement Performance"},
	"create_self_improvement_proposal_api_v1_self_improvement_proposals_post": {"method": "POST", "operationId": "create_self_improvement_proposal_api_v1_self_improvement_proposals_post", "path": "/api/v1/self-improvement/proposals", "summary": "Create Self Improvement Proposal"},
	"get_settings_api_v1_settings_get": {"method": "GET", "operationId": "get_settings_api_v1_settings_get", "path": "/api/v1/settings", "summary": "Get Settings"},
	"delete_setting_api_v1_settings__key__delete": {"method": "DELETE", "operationId": "delete_setting_api_v1_settings__key__delete", "path": "/api/v1/settings/{key}", "summary": "Delete Setting"},
	"put_setting_api_v1_settings__key__put": {"method": "PUT", "operationId": "put_setting_api_v1_settings__key__put", "path": "/api/v1/settings/{key}", "summary": "Put Setting"},
	"list_skills_api_v1_skills_get": {"method": "GET", "operationId": "list_skills_api_v1_skills_get", "path": "/api/v1/skills", "summary": "List Skills"},
	"sync_skills_api_v1_skills_sync_post": {"method": "POST", "operationId": "sync_skills_api_v1_skills_sync_post", "path": "/api/v1/skills/sync", "summary": "Sync Skills"},
	"teams_api_v1_teams_get": {"method": "GET", "operationId": "teams_api_v1_teams_get", "path": "/api/v1/teams", "summary": "Teams"},
	"telemetry_status_api_v1_telemetry_status_get": {"method": "GET", "operationId": "telemetry_status_api_v1_telemetry_status_get", "path": "/api/v1/telemetry/status", "summary": "Telemetry Status"},
	"list_threads_api_v1_threads_get": {"method": "GET", "operationId": "list_threads_api_v1_threads_get", "path": "/api/v1/threads", "summary": "List Threads"},
	"create_thread_api_v1_threads_post": {"method": "POST", "operationId": "create_thread_api_v1_threads_post", "path": "/api/v1/threads", "summary": "Create Thread"},
	"find_similar_threads_api_v1_threads_similar_get": {"method": "GET", "operationId": "find_similar_threads_api_v1_threads_similar_get", "path": "/api/v1/threads/similar", "summary": "Find Similar Threads"},
	"delete_thread_api_v1_threads__thread_id__delete": {"method": "DELETE", "operationId": "delete_thread_api_v1_threads__thread_id__delete", "path": "/api/v1/threads/{thread_id}", "summary": "Delete Thread"},
	"get_thread_api_v1_threads__thread_id__get": {"method": "GET", "operationId": "get_thread_api_v1_threads__thread_id__get", "path": "/api/v1/threads/{thread_id}", "summary": "Get Thread"},
	"update_thread_api_v1_threads__thread_id__patch": {"method": "PATCH", "operationId": "update_thread_api_v1_threads__thread_id__patch", "path": "/api/v1/threads/{thread_id}", "summary": "Update Thread"},
	"archive_thread_api_v1_threads__thread_id__archive_post": {"method": "POST", "operationId": "archive_thread_api_v1_threads__thread_id__archive_post", "path": "/api/v1/threads/{thread_id}/archive", "summary": "Archive Thread"},
	"cancel_execution_api_v1_threads__thread_id__cancel_post": {"method": "POST", "operationId": "cancel_execution_api_v1_threads__thread_id__cancel_post", "path": "/api/v1/threads/{thread_id}/cancel", "summary": "Cancel Execution"},
	"thread_cost_performance_api_v1_threads__thread_id__cost_performance_get": {"method": "GET", "operationId": "thread_cost_performance_api_v1_threads__thread_id__cost_performance_get", "path": "/api/v1/threads/{thread_id}/cost-performance", "summary": "Thread Cost Performance"},
	"resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post": {"method": "POST", "operationId": "resolve_decision_api_v1_threads__thread_id__decisions__decision_id__resolve_post", "path": "/api/v1/threads/{thread_id}/decisions/{decision_id}/resolve", "summary": "Resolve Decision"},
	"thread_events_api_v1_threads__thread_id__events_get": {"method": "GET", "operationId": "thread_events_api_v1_threads__thread_id__events_get", "path": "/api/v1/threads/{thread_id}/events", "summary": "Thread Events"},
	"thread_memory_api_v1_threads__thread_id__memory_get": {"method": "GET", "operationId": "thread_memory_api_v1_threads__thread_id__memory_get", "path": "/api/v1/threads/{thread_id}/memory", "summary": "Thread Memory"},
	"reindex_thread_memory_api_v1_threads__thread_id__memory_reindex_post": {"method": "POST", "operationId": "reindex_thread_memory_api_v1_threads__thread_id__memory_reindex_post", "path": "/api/v1/threads/{thread_id}/memory/reindex", "summary": "Reindex Thread Memory"},
	"post_message_api_v1_threads__thread_id__messages_post": {"method": "POST", "operationId": "post_message_api_v1_threads__thread_id__messages_post", "path": "/api/v1/threads/{thread_id}/messages", "summary": "Post Message"},
	"post_note_api_v1_threads__thread_id__notes_post": {"method": "POST", "operationId": "post_note_api_v1_threads__thread_id__notes_post", "path": "/api/v1/threads/{thread_id}/notes", "summary": "Post Note"},
	"list_thread_remediations_api_v1_threads__thread_id__remediations_get": {"method": "GET", "operationId": "list_thread_remediations_api_v1_threads__thread_id__remediations_get", "path": "/api/v1/threads/{thread_id}/remediations", "summary": "List Thread Remediations"},
	"find_similar_to_thread_api_v1_threads__thread_id__similar_get": {"method": "GET", "operationId": "find_similar_to_thread_api_v1_threads__thread_id__similar_get", "path": "/api/v1/threads/{thread_id}/similar", "summary": "Find Similar To Thread"},
	"mark_similar_thread_api_v1_threads__thread_id__similar__candidate_id__mark_post": {"method": "POST", "operationId": "mark_similar_thread_api_v1_threads__thread_id__similar__candidate_id__mark_post", "path": "/api/v1/threads/{thread_id}/similar/{candidate_id}/mark", "summary": "Mark Similar Thread"},
	"unarchive_thread_api_v1_threads__thread_id__unarchive_post": {"method": "POST", "operationId": "unarchive_thread_api_v1_threads__thread_id__unarchive_post", "path": "/api/v1/threads/{thread_id}/unarchive", "summary": "Unarchive Thread"},
	"worker_pause_api_v1_workers_pause_post": {"method": "POST", "operationId": "worker_pause_api_v1_workers_pause_post", "path": "/api/v1/workers/pause", "summary": "Worker Pause"},
	"worker_resume_api_v1_workers_resume_post": {"method": "POST", "operationId": "worker_resume_api_v1_workers_resume_post", "path": "/api/v1/workers/resume", "summary": "Worker Resume"},
	"worker_run_once_api_v1_workers_run_once_post": {"method": "POST", "operationId": "worker_run_once_api_v1_workers_run_once_post", "path": "/api/v1/workers/run-once", "summary": "Worker Run Once"},
	"worker_status_api_v1_workers_status_get": {"method": "GET", "operationId": "worker_status_api_v1_workers_status_get", "path": "/api/v1/workers/status", "summary": "Worker Status"},
	"list_workflows_api_v1_workflows_get": {"method": "GET", "operationId": "list_workflows_api_v1_workflows_get", "path": "/api/v1/workflows", "summary": "List Workflows"},
	"create_workflow_api_v1_workflows_post": {"method": "POST", "operationId": "create_workflow_api_v1_workflows_post", "path": "/api/v1/workflows", "summary": "Create Workflow"},
	"run_issue_to_patch_api_v1_workflows_issue_to_patch_post": {"method": "POST", "operationId": "run_issue_to_patch_api_v1_workflows_issue_to_patch_post", "path": "/api/v1/workflows/issue-to-patch", "summary": "Run Issue To Patch"},
	"approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post": {"method": "POST", "operationId": "approve_issue_to_patch_api_v1_workflows_issue_to_patch__run_id__approve_post", "path": "/api/v1/workflows/issue-to-patch/{run_id}/approve", "summary": "Approve Issue To Patch"},
	"promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post": {"method": "POST", "operationId": "promote_patch_to_branch_api_v1_workflows_issue_to_patch__run_id__promote_post", "path": "/api/v1/workflows/issue-to-patch/{run_id}/promote", "summary": "Promote Patch To Branch"},
	"create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post": {"method": "POST", "operationId": "create_pull_request_from_promoted_branch_api_v1_workflows_issue_to_patch__run_id__pull_request_post", "path": "/api/v1/workflows/issue-to-patch/{run_id}/pull-request", "summary": "Create Pull Request From Promoted Branch"},
	"run_issue_to_pr_api_v1_workflows_issue_to_pr_post": {"method": "POST", "operationId": "run_issue_to_pr_api_v1_workflows_issue_to_pr_post", "path": "/api/v1/workflows/issue-to-pr", "summary": "Run Issue To Pr"},
	"approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post": {"method": "POST", "operationId": "approve_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__approve_post", "path": "/api/v1/workflows/issue-to-pr/{run_id}/approve", "summary": "Approve Issue To Pr"},
	"promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post": {"method": "POST", "operationId": "promote_issue_to_pr_branch_api_v1_workflows_issue_to_pr__run_id__promote_post", "path": "/api/v1/workflows/issue-to-pr/{run_id}/promote", "summary": "Promote Issue To Pr Branch"},
	"create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post": {"method": "POST", "operationId": "create_pull_request_from_issue_to_pr_api_v1_workflows_issue_to_pr__run_id__pull_request_post", "path": "/api/v1/workflows/issue-to-pr/{run_id}/pull-request", "summary": "Create Pull Request From Issue To Pr"},
	"get_workflow_api_v1_workflows__workflow_id__get": {"method": "GET", "operationId": "get_workflow_api_v1_workflows__workflow_id__get", "path": "/api/v1/workflows/{workflow_id}", "summary": "Get Workflow"},
	"cancel_workflow_api_v1_workflows__workflow_id__cancel_post": {"method": "POST", "operationId": "cancel_workflow_api_v1_workflows__workflow_id__cancel_post", "path": "/api/v1/workflows/{workflow_id}/cancel", "summary": "Cancel Workflow"},
	"pause_workflow_api_v1_workflows__workflow_id__pause_post": {"method": "POST", "operationId": "pause_workflow_api_v1_workflows__workflow_id__pause_post", "path": "/api/v1/workflows/{workflow_id}/pause", "summary": "Pause Workflow"},
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": {"method": "POST", "operationId": "resume_workflow_api_v1_workflows__workflow_id__resume_post", "path": "/api/v1/workflows/{workflow_id}/resume", "summary": "Resume Workflow"},
	"start_workflow_api_v1_workflows__workflow_id__start_post": {"method": "POST", "operationId": "start_workflow_api_v1_workflows__workflow_id__start_post", "path": "/api/v1/workflows/{workflow_id}/start", "summary": "Start Workflow"},
	"advance_workflow_gate_api_v1_workflows__workflow_id__steps__step_id__advance_post": {"method": "POST", "operationId": "advance_workflow_gate_api_v1_workflows__workflow_id__steps__step_id__advance_post", "path": "/api/v1/workflows/{workflow_id}/steps/{step_id}/advance", "summary": "Advance Workflow Gate"},
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

/**
 * Resolves a human-readable detail from an error response body. Tries the JSON
 * `detail`/`error` shape first and falls back to the raw text for non-JSON bodies
 * (e.g. a plain-text "Internal Server Error"), so a 5xx never surfaces as an opaque
 * `JSON.parse` SyntaxError ("Unexpected token 'I'...").
 */
export function extractErrorDetail(body: string, statusText: string): string {
	if (!body) return statusText;
	try {
		const parsed = JSON.parse(body) as { detail?: unknown; error?: unknown };
		const detail = parsed.detail ?? parsed.error;
		if (typeof detail === "string") return detail;
		if (detail !== undefined && detail !== null) return JSON.stringify(detail);
		return statusText;
	} catch {
		return body.slice(0, 500);
	}
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
	if (!response.ok) {
		throw new Error(extractErrorDetail(text, response.statusText));
	}
	try {
		return (text ? JSON.parse(text) : {}) as TResponse;
	} catch {
		throw new Error(
			`Malformed JSON response from ${endpoint.method} ${endpoint.path}.`,
		);
	}
}
