// Generated from FastAPI OpenAPI. Do not edit by hand.
// No network access is required; run `corepack pnpm@10.24.0 run openapi:generate`.

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export type ActionRequestRecord = { "actionType": string; "command": string; "decidedAt"?: null | string; "decidedBy"?: null | string; "id": string; "jobId": string; "payload": JsonObject; "projectId": string; "reason": string; "requestedAt": string; "riskLevel": string; "status": string };
export type AgentProfileRecord = { "allowApi": boolean; "allowCli": boolean; "allowRemote": boolean; "allowedProviders": Array<string>; "allowedRuntimes": Array<string>; "allowedSkills": Array<string>; "allowedTools": Array<string>; "createdAt": string; "id": string; "maxCostPerRun": number; "maxRuntimeSeconds": number; "maxTokensPerRun": number; "memoryScope": string; "modelPolicyId"?: null | string; "name": string; "outputSchema": JsonObject; "permissionProfile": "plan" | "dev_safe" | "qa" | "release"; "qualityGates": Array<JsonValue>; "requiresApprovalOverUsd"?: null | number; "role": "analyst" | "product_owner" | "technical_lead" | "technical_lead_shadow" | "developer" | "backend_engineer" | "frontend_engineer" | "implementer" | "qa" | "qa_reviewer" | "security_reviewer" | "release_manager"; "roleModelPolicyId"?: null | string; "routingProfileId"?: null | string; "runtimeMode": "api" | "cli" | "ollama" | "hybrid" | "manual"; "runtimeType": "api" | "cli" | "ollama" | "hybrid" | "manual"; "status": "active" | "disabled"; "updatedAt": string };
export type AgentProfileResponse = { "agentProfile": AgentProfileRecord };
export type AgentProfileUpsertRequest = { "allowApi"?: boolean; "allowCli"?: boolean; "allowRemote"?: boolean; "allowedProviders"?: Array<string>; "allowedRuntimes"?: Array<string>; "allowedSkills"?: Array<string>; "allowedTools"?: Array<string>; "id": string; "maxCostPerRun"?: number; "maxRuntimeSeconds"?: number; "maxTokensPerRun"?: number; "memoryScope"?: string; "modelPolicyId"?: null | string; "name"?: null | string; "outputSchema"?: JsonObject; "permissionProfile"?: "plan" | "dev_safe" | "qa" | "release"; "qualityGates"?: Array<JsonValue>; "requiresApprovalOverUsd"?: null | number; "role"?: "analyst" | "product_owner" | "technical_lead" | "technical_lead_shadow" | "developer" | "backend_engineer" | "frontend_engineer" | "implementer" | "qa" | "qa_reviewer" | "security_reviewer" | "release_manager"; "roleModelPolicyId"?: null | string; "routingProfileId"?: null | string; "runtimeMode"?: "api" | "cli" | "ollama" | "hybrid" | "manual"; "runtimeType"?: "api" | "cli" | "ollama" | "hybrid" | "manual" | null; "status"?: "active" | "disabled" };
export type AgentProfilesListResponse = { "agentProfiles": Array<AgentProfileRecord> };
export type AgentRunCreateRequest = { "agentProfileId": string; "input"?: JsonObject; "jobId"?: null | string; "projectId": string; "taskId"?: string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type AgentRunRecord = { "createdAt": string; "id": string; "input": JsonObject; "jobId"?: null | string; "metadata": JsonObject; "output": JsonObject; "projectId": string; "status": string; "updatedAt": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type AgentRunResponse = { "agentRun": AgentRunRecord };
export type AgentRunsListResponse = { "agentRuns": Array<AgentRunRecord> };
export type AgentToolCallRecord = { "agentRunId": string; "createdAt": string; "id": string; "payload": JsonObject; "status": string; "toolName": string; "updatedAt": string };
export type AgentsListResponse = { "agents": Array<CatalogAgentRecord> };
export type ApiRuntimeProviderStatus = { "adapters": Array<string>; "available": boolean; "provider": string };
export type ApprovalReasonRequest = { "reason": string };
export type ApprovalsListResponse = { "actionRequests": Array<ActionRequestRecord> };
export type ArchitectureDecisionCreateRequest = { "consequences"?: Array<JsonValue> | string; "context"?: string; "decision"?: string; "linkedRiskIds"?: Array<string>; "metadata"?: JsonObject; "nextStepIds"?: Array<string>; "projectId": string; "status"?: "proposed" | "accepted" | "rejected" | "superseded" | "deprecated"; "title": string };
export type ArchitectureDecisionRecord = { "consequences"?: Array<JsonValue> | string; "context": string; "createdAt": string; "decision": string; "id": string; "linkedRiskIds": Array<string>; "metadata": JsonObject; "nextStepIds": Array<string>; "projectId": string; "status": "proposed" | "accepted" | "rejected" | "superseded" | "deprecated"; "title": string; "updatedAt": string };
export type ArchitectureDecisionResponse = { "architectureDecision": ArchitectureDecisionRecord };
export type ArchitectureDecisionsListResponse = { "architectureDecisions": Array<ArchitectureDecisionRecord> };
export type ArtifactCleanupRequest = { "dryRun"?: boolean };
export type ArtifactCleanupResponse = { "artifactRoot": string; "deletedFiles": Array<ArtifactFileRecord>; "dryRun": boolean; "keptReferencedFiles": number; "orphanFiles": Array<ArtifactFileRecord> };
export type ArtifactFileRecord = { "path": string; "sizeBytes": number };
export type ArtifactIngestRequest = { "content"?: null | string; "contentBase64"?: null | string; "kind": "execution_log" | "screenshot" | "test_report" | "qa_report" | "generic_artifact"; "mimeType"?: null | string; "name"?: null | string };
export type ArtifactRecord = { "createdAt": string; "evidencePackageId"?: null | string; "hash"?: null | string; "id": string; "kind": string; "metadata": JsonObject; "path": string; "projectId": string };
export type ArtifactResponse = { "artifact": ArtifactRecord };
export type ArtifactRetentionActionRequest = { "action": string; "artifactIds": Array<string>; "now"?: null | string; "reason": string };
export type ArtifactRetentionActionResponse = { "action": string; "artifacts": Array<ArtifactRetentionResultRecord> };
export type ArtifactRetentionPlanRequest = { "dryRun"?: boolean; "now"?: null | string };
export type ArtifactRetentionPlanResponse = { "dryRun": boolean; "expiredArtifacts": Array<ExpiredArtifactRecord>; "now": string; "riskIds": Array<string> };
export type ArtifactRetentionResultRecord = { "createdAt": string; "evidencePackageId"?: null | string; "hash"?: null | string; "id": string; "kind": string; "metadata": JsonObject; "path": string; "projectId": string; "retentionAction": JsonObject };
export type AuditEventRecord = { "action": string; "actor": string; "createdAt": string; "id": string; "payload": JsonObject; "projectId"?: null | string; "target": string };
export type BudgetRulePatchRequest = { "actionOnExceed"?: string; "enabled"?: boolean; "id"?: null | string; "maxCostUsd"?: null | number; "maxTokens"?: null | number; "period"?: string; "scopeId"?: null | string; "scopeType"?: null | string };
export type BudgetRuleRecord = { "actionOnExceed": string; "createdAt": string; "enabled": boolean; "id": string; "maxCostUsd"?: null | number; "maxTokens"?: null | number; "period": string; "scopeId"?: null | string; "scopeType": string; "updatedAt": string };
export type BudgetRuleResponse = { "budgetRule": BudgetRuleRecord };
export type BudgetRuleUpsertRequest = { "actionOnExceed"?: string; "enabled"?: boolean; "id"?: null | string; "maxCostUsd"?: null | number; "maxTokens"?: null | number; "period"?: string; "scopeId"?: null | string; "scopeType"?: string };
export type BudgetRulesListResponse = { "budgetRules": Array<BudgetRuleRecord> };
export type CatalogAgentRecord = { "capabilities": Array<JsonValue>; "createdAt": string; "id": string; "kind": string; "model": string; "name": string; "permissions": JsonObject; "providerId": string; "role": string; "teamId": string; "updatedAt": string };
export type ChatCreateRequest = { "projectId": string; "prompt": string; "sessionId"?: null | string; "title"?: null | string };
export type ChatRecord = { "createdAt": string; "id": string; "metadata": JsonObject; "projectId": string; "prompt": string; "sessionId"?: null | string; "status": string; "title": string; "updatedAt": string };
export type ChatResponse = { "chat": ChatRecord };
export type ChatsListResponse = { "chats": Array<ChatRecord> };
export type CliAdaptersStatus = { "cli_claude": boolean; "cli_codex": boolean };
export type CliRuntimeProviderStatus = { "adapters": CliAdaptersStatus; "available": boolean; "provider": string };
export type CliRuntimeRecord = { "executable"?: null | string; "id"?: null | string; "message"?: string; "runtime": string; "status": string; "version"?: null | string };
export type CliRuntimesListResponse = { "cliRuntimes": Array<CliRuntimeRecord> };
export type CliSessionRecord = { "agentId"?: null | string; "command": Array<JsonValue>; "createdAt": string; "envPolicy": JsonObject; "error"?: null | string; "executable": string; "finishedAt"?: null | string; "id": string; "logsArtifactId"?: null | string; "runtime": string; "startedAt"?: null | string; "status": string; "stderrArtifactId"?: null | string; "stdoutArtifactId"?: null | string; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string; "workspaceId": string };
export type CliSessionResponse = { "cliSession": CliSessionRecord };
export type CliSessionsListResponse = { "cliSessions": Array<CliSessionRecord> };
export type CostUsageRecord = { "amountUsd": number; "createdAt": string; "id": string; "metadata": JsonObject; "projectId": string; "scope": string };
export type DevcontainerMetadata = { "enabled"?: boolean; "features"?: Array<string>; "image"?: string; "templateId"?: string };
export type DeveloperAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean };
export type DeveloperAgentRunRequest = { "approvalGrantId"?: null | string; "instruction": string; "maxCostUsd"?: null | number; "metadata"?: JsonObject; "model"?: null | string; "preferredRuntime"?: null | string; "projectId": string; "qaCommands"?: Array<Array<string>>; "requireApproval"?: boolean; "taskId"?: string; "workspaceId": string };
export type DeveloperAgentRunResponse = { "agentRun": AgentRunRecord; "developerAgent": DeveloperAgentStatus; "diffSummary": JsonObject; "evidencePackage": JsonObject; "job": JsonObject; "qaResults": Array<JsonObject>; "reason": string; "runtime": JsonObject; "runtimeResult": JsonObject; "status": string; "workspace": JsonObject };
export type DeveloperAgentStatus = { "candidateRuntimeIds"?: Array<string>; "contract": DeveloperAgentContract; "executable": boolean; "id": string; "reason": string; "selectedRuntimeId"?: null | string; "status": string };
export type DeveloperAgentStatusResponse = { "developerAgent": DeveloperAgentStatus };
export type DirectoryPickerRequest = { "initialPath"?: null | string; "title"?: string };
export type DirectoryPickerResponse = { "reason"?: null | string; "selectedPath"?: null | string; "status": string };
export type DiscoverModelsResponse = { "models": Array<ModelCatalogRecord> };
export type DockerSandboxStatus = { "available": boolean; "defaultNetwork": string; "executable"?: null | string; "fallback": string; "hostMount": string; "mode": string; "policy": SandboxProfileRecord; "required": boolean; "writes": string };
export type EmptyObjectRequest = JsonObject;
export type EventRecord = { "createdAt": string; "id": string; "jobId"?: null | string; "payload": JsonObject; "projectId"?: null | string; "severity"?: string; "type": string };
export type EvidenceCreateRequest = { "acceptanceChecklist"?: Array<JsonValue>; "actualCostUsd"?: null | number; "agentId"?: null | string; "agentRunId"?: null | string; "artifactIds"?: Array<string>; "diffRefs"?: Array<JsonValue>; "diffSummary"?: JsonObject; "estimatedCostUsd"?: null | number; "jobId"?: null | string; "latencyMs"?: null | number; "logs"?: Array<JsonValue>; "model"?: null | string; "projectId": string; "providerId"?: null | string; "qaVerdict"?: string; "rework"?: boolean | null; "riskNotes"?: Array<JsonValue>; "role"?: null | string; "runtimeId"?: null | string; "runtimeType"?: null | string; "screenshotRefs"?: Array<JsonValue>; "taskId"?: string; "testPlan"?: string; "testResultReports"?: Array<JsonObject>; "testResults"?: Array<JsonObject>; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string; "workspaceId"?: null | string };
export type EvidenceDetailResponse = { "artifacts": Array<ArtifactRecord>; "evidencePackage": EvidencePackageRecord; "testResultRecords": Array<TestResultRecord> };
export type EvidenceListResponse = { "evidencePackages": Array<EvidencePackageRecord> };
export type EvidencePackageRecord = { "acceptanceChecklist": Array<JsonValue>; "agentId"?: null | string; "agentRunId"?: null | string; "artifactIds"?: Array<string>; "createdAt": string; "diffRefs": Array<JsonValue>; "diffSummary"?: JsonObject; "id": string; "jobId"?: null | string; "logs": Array<JsonValue>; "projectId": string; "qaVerdict": string; "riskNotes": Array<JsonValue>; "runtimeId"?: null | string; "screenshotRefs": Array<JsonValue>; "taskId": string; "testPlan": string; "testResults": Array<JsonValue>; "workflowRunId"?: null | string; "workflowStepId"?: null | string; "workspaceId"?: null | string };
export type EvidencePackageResponse = { "evidencePackage": EvidencePackageRecord };
export type ExpiredArtifactRecord = { "createdAt": string; "evidencePackageId"?: null | string; "expiresAt": string; "hash"?: null | string; "id": string; "kind": string; "metadata": JsonObject; "path": string; "projectId": string; "retentionStatus": string };
export type ExternalTelemetryStatus = { "available": boolean; "enabled": boolean; "metricsEnabled": boolean; "metricsEndpoint"?: null | string; "mode": string; "reason": string; "serviceName"?: null | string; "tracesEnabled": boolean; "tracesEndpoint"?: null | string };
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
export type IntegrationRecord = { "config": JsonObject; "createdAt": string; "id": string; "kind": string; "status": string; "updatedAt": string };
export type IntegrationsListResponse = { "integrations": Array<IntegrationRecord>; "mcpServers": Array<McpServerRecord>; "optionalAdapters": JsonObject };
export type IssueToPatchRequest = { "issueText": string; "maxCostUsd"?: null | number; "preferredRuntime"?: null | string; "projectId": string; "qaCommands"?: Array<Array<string>>; "requireApproval"?: boolean; "targetPath"?: null | string; "title": string };
export type IssueToPatchResponse = { "agentRun": AgentRunRecord; "diffSummary": JsonObject; "evidencePackage": EvidencePackageRecord; "job": JobRecord; "qaResults": Array<JsonObject>; "reason": string; "runtime": JsonObject; "runtimeResult": JsonObject; "status": string; "workflow": WorkflowRecord; "workflowRun": WorkflowRunRecord; "workflowSteps": Array<WorkflowStepRecord>; "workspace": WorkspaceRecord };
export type JobCreateRequest = { "idempotencyKey"?: null | string; "kind": string; "payload"?: JsonObject; "projectId": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type JobMutationResponse = { "actionRequest"?: ActionRequestRecord | null; "actionRequests"?: Array<ActionRequestRecord>; "auditEvent"?: AuditEventRecord | null; "events"?: Array<EventRecord>; "job": JobRecord; "permissionGrant"?: PermissionGrantRecord | null };
export type JobRecord = { "createdAt": string; "id": string; "idempotencyKey"?: null | string; "kind": string; "leaseExpiresAt"?: null | string; "leaseOwner"?: null | string; "payload": JsonObject; "projectId": string; "status": "queued" | "running" | "approval_required" | "completed" | "failed" | "cancelled"; "updatedAt": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type JobRunRecord = { "completedAt"?: null | string; "id": string; "jobId": string; "metadata": JsonObject; "providerId"?: null | string; "startedAt": string; "status": string; "summary": string };
export type JobsListResponse = { "events"?: Array<EventRecord>; "jobs": Array<JobRecord> };
export type McpServerRecord = { "command": string; "createdAt": string; "id": string; "metadata": JsonObject; "status": string; "transport": "stdio"; "updatedAt": string };
export type McpServerRegisterRequest = { "command": string; "id": string; "metadata"?: JsonObject; "transport"?: "stdio" };
export type McpServerResponse = { "mcpServer": McpServerRecord };
export type MemoryCreateRequest = { "content": string; "kind"?: string; "metadata"?: JsonObject; "projectId": string; "scope"?: string; "scopeId"?: null | string; "sourceRef"?: string };
export type MemoryItemRecord = { "content": string; "createdAt": string; "createdByRunId"?: null | string; "hash": string; "id": string; "kind": string; "metadata": JsonObject; "projectId": string; "scope": string; "scopeId"?: null | string; "sourceRef": string; "supersedesId"?: null | string; "updatedAt": string; "validFrom": string; "version": number };
export type MemoryListResponse = { "memoryItems": Array<MemoryItemRecord> };
export type MemoryResponse = { "memoryItem": MemoryItemRecord };
export type ModelBenchmarkOutcomeCreateRequest = { "actualCostUsd"?: null | number; "agentId"?: null | string; "estimatedCostUsd"?: null | number; "jobId"?: null | string; "latencyMs"?: null | number; "metadata"?: JsonObject; "model": string; "providerId": string; "qaPass"?: boolean | null; "rework"?: boolean | null; "role"?: null | string; "runtimeType"?: string; "success"?: boolean | null; "taskId"?: null | string; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type ModelBenchmarkOutcomeRecord = { "actualCostUsd"?: null | number; "agentId"?: null | string; "createdAt": string; "estimatedCostUsd"?: null | number; "id": string; "jobId"?: null | string; "latencyMs"?: null | number; "metadata": JsonObject; "model": string; "providerId": string; "qaPass"?: boolean | null; "rework"?: boolean | null; "role"?: null | string; "runtimeType": string; "success"?: boolean | null; "taskId"?: null | string; "usageLedgerId"?: null | string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type ModelBenchmarkOutcomeResponse = { "outcome": ModelBenchmarkOutcomeRecord };
export type ModelBenchmarkOutcomesListResponse = { "outcomes": Array<ModelBenchmarkOutcomeRecord> };
export type ModelBenchmarkRecord = { "avgCost"?: null | number; "avgLatencyMs"?: null | number; "createdAt": string; "id": string; "insufficientData": boolean; "lastUsedAt"?: null | string; "metadata": JsonObject; "model": string; "providerId": string; "qaPassRate"?: null | number; "reworkRate"?: null | number; "role"?: null | string; "successRate"?: null | number; "tasksAttempted": number; "updatedAt": string };
export type ModelBenchmarksListResponse = { "benchmarks": Array<ModelBenchmarkRecord> };
export type ModelCallRecord = { "agentRunId"?: null | string; "completionTokens": number; "costUsd": number; "createdAt": string; "id": string; "metadata": JsonObject; "model": string; "modelPolicyId"?: null | string; "projectId": string; "promptTokens": number; "provider": string; "status": string };
export type ModelCatalogListResponse = { "models": Array<ModelCatalogRecord> };
export type ModelCatalogPatchRequest = { "cachedInputPricePerMtok"?: null | number; "contextWindow"?: null | number; "displayName"?: null | string; "effortLevels"?: Array<string> | null; "enabled"?: boolean | null; "freeTier"?: boolean | null; "freeTierNotes"?: null | string; "inputPricePerMtok"?: null | number; "maxOutputTokens"?: null | number; "modelFamily"?: null | string; "outputPricePerMtok"?: null | number; "reasoningPricePerMtok"?: null | number; "source"?: null | string; "supportsEmbeddings"?: boolean | null; "supportsJson"?: boolean | null; "supportsReasoning"?: boolean | null; "supportsRerank"?: boolean | null; "supportsStreaming"?: boolean | null; "supportsThinking"?: boolean | null; "supportsTools"?: boolean | null; "supportsVision"?: boolean | null };
export type ModelCatalogRecord = { "cachedInputPricePerMtok"?: null | number; "contextWindow": number; "createdAt": string; "displayName": string; "effortLevels": Array<string>; "enabled": boolean; "freeTier": boolean; "freeTierNotes": string; "id": string; "inputPricePerMtok"?: null | number; "maxOutputTokens": number; "model": string; "modelFamily": string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "source": string; "supportsEmbeddings": boolean; "supportsJson": boolean; "supportsReasoning": boolean; "supportsRerank": boolean; "supportsStreaming": boolean; "supportsThinking": boolean; "supportsTools": boolean; "supportsVision": boolean; "updatedAt": string };
export type ModelCatalogResponse = { "model": ModelCatalogRecord };
export type ModelCatalogUpsertRequest = { "cachedInputPricePerMtok"?: null | number; "contextWindow"?: number; "displayName"?: null | string; "effortLevels"?: Array<string>; "enabled"?: boolean; "freeTier"?: boolean; "freeTierNotes"?: string; "inputPricePerMtok"?: null | number; "maxOutputTokens"?: number; "model": string; "modelFamily"?: string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "source"?: string; "supportsEmbeddings"?: boolean; "supportsJson"?: boolean; "supportsReasoning"?: boolean; "supportsRerank"?: boolean; "supportsStreaming"?: boolean; "supportsThinking"?: boolean; "supportsTools"?: boolean; "supportsVision"?: boolean };
export type ModelGatewayOverviewRecord = { "activeCliSessions": number; "actualCostToday": number; "apiProviders": number; "cliRuntimes": number; "degraded": number; "estimatedCostToday": number; "healthy": number; "localProviders": number; "offline": number; "pendingModelApprovals": number; "providersEnabled": number; "providersInCooldown": number; "totalTokensToday": number };
export type ModelGatewayOverviewResponse = { "overview": ModelGatewayOverviewRecord };
export type ModelPoliciesListResponse = { "modelPolicies": Array<ModelPolicyRecord> };
export type ModelPolicyRecord = { "allowLocal": boolean; "allowRemote": boolean; "createdAt": string; "fallback": Array<ModelProviderCandidate>; "id": string; "maxCostUsd": number; "maxTokens": number; "name": string; "preferred": Array<ModelProviderCandidate>; "status": "active" | "disabled"; "temperature": number; "updatedAt": string };
export type ModelPolicyResponse = { "modelPolicy": ModelPolicyRecord };
export type ModelPolicyUpsertRequest = { "allowLocal"?: boolean; "allowRemote"?: boolean; "fallback"?: Array<ModelProviderCandidate>; "id": string; "maxCostUsd"?: number; "maxTokens"?: number; "name"?: null | string; "preferred"?: Array<ModelProviderCandidate>; "status"?: "active" | "disabled"; "temperature"?: number };
export type ModelProviderCandidate = { "model": string; "provider": string };
export type ModelProviderRecord = { "allowRemote": boolean; "createdAt": string; "id": string; "label": string; "metadata": JsonObject; "provider": string; "status": string; "updatedAt": string };
export type ModelProvidersListResponse = { "modelProviders": Array<ModelProviderRecord> };
export type NextStepCreateRequest = { "dueAt"?: null | string; "metadata"?: JsonObject; "owner"?: string; "priority"?: "low" | "medium" | "high" | "urgent"; "projectId": string; "sourceDecisionId"?: null | string; "sourceRiskId"?: null | string; "status"?: "planned" | "in_progress" | "blocked" | "completed" | "cancelled"; "title": string };
export type NextStepRecord = { "createdAt": string; "dueAt"?: null | string; "id": string; "metadata": JsonObject; "owner": string; "priority": "low" | "medium" | "high" | "urgent"; "projectId": string; "sourceDecisionId"?: null | string; "sourceRiskId"?: null | string; "status": "planned" | "in_progress" | "blocked" | "completed" | "cancelled"; "title": string; "updatedAt": string };
export type NextStepResponse = { "nextStep": NextStepRecord };
export type NextStepUpdateRequest = { "dueAt"?: null | string; "metadata"?: JsonObject | null; "owner"?: null | string; "priority"?: "low" | "medium" | "high" | "urgent" | null; "status"?: "planned" | "in_progress" | "blocked" | "completed" | "cancelled" | null };
export type NextStepsListResponse = { "nextSteps": Array<NextStepRecord> };
export type OllamaRuntimeProviderStatus = { "available": boolean; "models": Array<string>; "provider": string; "reason"?: string };
export type OpenDesignResponse = { "backend": string; "runtime": string; "status": string };
export type OpenDesignStatus = { "status": string };
export type OptionalReasonRequest = { "reason"?: string };
export type OverviewResponse = { "actionRequests": Array<ActionRequestRecord>; "agentProfiles": Array<AgentProfileRecord>; "agentRuns": Array<AgentRunRecord>; "agentToolCalls": Array<AgentToolCallRecord>; "agents": Array<CatalogAgentRecord>; "architectureDecisions": Array<ArchitectureDecisionRecord>; "artifacts": Array<ArtifactRecord>; "auditEvents": Array<AuditEventRecord>; "chats": Array<ChatRecord>; "costUsage": Array<CostUsageRecord>; "events": Array<EventRecord>; "evidencePackages": Array<EvidencePackageRecord>; "ideConnections": Array<IdeConnectionRecord>; "jobRuns": Array<JobRunRecord>; "jobs": Array<JobRecord>; "mcpServers": Array<McpServerRecord>; "memoryItems": Array<MemoryItemRecord>; "modelCalls": Array<ModelCallRecord>; "modelPolicies": Array<ModelPolicyRecord>; "modelProviders": Array<ModelProviderRecord>; "nextSteps": Array<NextStepRecord>; "openDesign": OpenDesignStatus; "permissionDecisions": Array<PermissionDecisionRecord>; "permissionGrants": Array<PermissionGrantRecord>; "pipelines": Array<PipelineRecord>; "policyRevisions": Array<PolicyRevisionRecord>; "projectTemplates": Array<ProjectTemplateRecord>; "projects": Array<ProjectRecord>; "promptTemplates": Array<PromptTemplateRecord>; "providers": Array<ProviderRecord>; "riskRegister": Array<RiskRecord>; "runtimeWorkspaces": Array<WorkspaceRecord>; "sandboxProfiles": Array<SandboxProfileRecord>; "security": SecurityPosture; "sessions": Array<SessionRecord>; "skills": Array<SkillRecord>; "teams": Array<TeamRecord>; "testResultRecords": Array<TestResultRecord>; "workflowRuns": Array<WorkflowRunRecord>; "workflowSteps": Array<WorkflowStepRecord>; "workflows": Array<WorkflowRecord> };
export type PermissionDecisionRecord = { "agentId"?: null | string; "command"?: null | string; "createdAt": string; "decision": string; "id": string; "path"?: null | string; "payload": JsonObject; "projectId"?: null | string; "reason": string; "riskLevel": string; "role"?: null | string; "tool"?: null | string; "workspaceId"?: null | string };
export type PermissionGrantRecord = { "actionRequestId"?: null | string; "agentId"?: null | string; "command"?: null | string; "consumedAt"?: null | string; "consumedByAgentRunId"?: null | string; "grantedAt"?: null | string; "grantedBy"?: null | string; "id": string; "jobId"?: null | string; "path"?: null | string; "payload": JsonObject; "permissionDecisionId"?: null | string; "projectId"?: null | string; "reason": string; "revokeReason"?: null | string; "revokedAt"?: null | string; "revokedBy"?: null | string; "status": string; "tool"?: null | string };
export type PermissionGrantResponse = { "permissionGrant": PermissionGrantRecord };
export type PipelineCreateRequest = { "chatId"?: null | string; "projectId": string; "sessionId"?: null | string; "stages"?: Array<JsonObject> | null; "title"?: null | string };
export type PipelineRecord = { "chatId"?: null | string; "createdAt": string; "id": string; "metadata": JsonObject; "projectId": string; "sessionId"?: null | string; "stages": Array<JsonObject>; "status": string; "title": string; "updatedAt": string };
export type PipelineResponse = { "pipeline": PipelineRecord };
export type PipelinesListResponse = { "pipelines": Array<PipelineRecord> };
export type PoliciesListResponse = { "permissionDecisions": Array<PermissionDecisionRecord>; "permissionGrants": Array<PermissionGrantRecord>; "policies": Array<PolicyRecord>; "policyRevisions": Array<PolicyRevisionRecord>; "sandboxProfiles": Array<SandboxProfileRecord> };
export type PolicyEvaluateRequest = { "agentId"?: null | string; "command"?: null | string; "deploymentTarget"?: null | string; "environment"?: null | string; "gitOperation"?: null | string; "networkRequired"?: boolean | null; "operation"?: null | string; "path"?: null | string; "permissionProfile"?: null | string; "projectId"?: null | string; "riskLevel"?: null | string; "role"?: null | string; "secretsRequired"?: boolean | null; "tool"?: null | string; "workspaceId"?: null | string };
export type PolicyEvaluationResponse = { "decision": PermissionDecisionRecord };
export type PolicyRecord = { "createdAt": string; "id": string; "name": string; "profile": string; "rules": Array<JsonObject>; "updatedAt": string };
export type PolicyRevisionRecord = { "actor": string; "changedFields": Array<string>; "createdAt": string; "id": string; "previous": JsonObject; "reason": string; "subjectId": string; "subjectType": string; "updated": JsonObject; "version": number };
export type PricingSnapshotCreateRequest = { "applyToCatalog"?: boolean; "cachedInputPricePerMtok"?: null | number; "effectiveAt"?: null | string; "freeTier"?: boolean; "inputPricePerMtok"?: null | number; "metadata"?: JsonObject; "model": string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "sourceRef"?: string };
export type PricingSnapshotRecord = { "applyToCatalog": boolean; "cachedInputPricePerMtok"?: null | number; "createdAt": string; "effectiveAt"?: null | string; "freeTier": boolean; "id": string; "inputPricePerMtok"?: null | number; "metadata": JsonObject; "model": string; "outputPricePerMtok"?: null | number; "providerId": string; "reasoningPricePerMtok"?: null | number; "sourceRef": string };
export type PricingSnapshotResponse = { "pricingSnapshot": PricingSnapshotRecord };
export type PricingSnapshotsListResponse = { "pricingSnapshots": Array<PricingSnapshotRecord> };
export type ProjectCreateRequest = { "createDirectory"?: boolean; "metadata"?: JsonObject; "name"?: null | string; "path"?: null | string; "projectDirectoryName"?: null | string; "templateId"?: null | string; "workspaceBasePath"?: null | string };
export type ProjectDiscoveryRequest = { "path": string };
export type ProjectDiscoveryResponse = { "discovery": JsonObject };
export type ProjectRecord = { "createdAt": string; "id": string; "metadata": JsonObject; "name": string; "path": string; "source": string; "status": string; "templateId": string; "updatedAt": string };
export type ProjectResponse = { "auditEvent"?: AuditEventRecord | null; "project": ProjectRecord };
export type ProjectTemplateRecord = { "id": string; "kind": string; "name": string };
export type ProjectTemplatesResponse = { "projectTemplates": Array<ProjectTemplateRecord> };
export type ProjectsListResponse = { "projects": Array<ProjectRecord> };
export type PromptResponse = { "promptTemplate": PromptTemplateRecord };
export type PromptTemplateRecord = { "appliesTo": JsonObject; "body": string; "createdAt": string; "id": string; "mode": string; "name": string; "optimizer": string; "projectId": string; "updatedAt": string; "version": number };
export type PromptTemplatesListResponse = { "promptTemplates": Array<PromptTemplateRecord> };
export type PromptUpsertRequest = { "appliesTo"?: JsonObject; "body": string; "id"?: null | string; "mode"?: string; "name": string; "optimizer"?: string; "projectId": string };
export type ProviderAccountPatchRequest = { "apiFormat"?: null | string; "baseUrl"?: null | string; "credentialRef"?: null | string; "displayName"?: null | string; "enabled"?: boolean | null; "healthStatus"?: null | string; "lastError"?: null | string; "lastHealthCheckAt"?: null | string; "metadata"?: JsonObject | null; "providerType"?: null | string; "quotaMode"?: null | string };
export type ProviderAccountRecord = { "apiFormat": string; "baseUrl"?: null | string; "createdAt": string; "credentialRef"?: null | string; "credentialStatus": string; "displayName": string; "enabled": boolean; "healthStatus": string; "id": string; "lastError": string; "lastHealthCheckAt"?: null | string; "metadata"?: JsonObject; "providerId": string; "providerType": string; "quotaMode": string; "updatedAt": string };
export type ProviderAccountResponse = { "provider": ProviderAccountRecord };
export type ProviderAccountUpsertRequest = { "apiFormat"?: string; "baseUrl"?: string; "credentialRef"?: string; "displayName"?: null | string; "enabled"?: boolean; "healthStatus"?: string; "lastError"?: string; "lastHealthCheckAt"?: null | string; "metadata"?: JsonObject; "providerId": string; "providerType"?: string; "quotaMode"?: string };
export type ProviderAccountsListResponse = { "providers": Array<ProviderAccountRecord> };
export type ProviderHealth = { "healthStatus": string; "lastError"?: null | string; "message"?: string; "providerId": string; "status": string };
export type ProviderHealthResponse = { "health": ProviderHealth };
export type ProviderLimitPatchRequest = { "cooldownUntil"?: null | string; "currentWindow"?: JsonObject | null; "dailyRequests"?: null | number; "dailyTokens"?: null | number; "last429At"?: null | string; "lastLimitErrorAt"?: null | string; "monthlyBudgetUsd"?: null | number; "monthlyRequests"?: null | number; "monthlyTokens"?: null | number; "rpm"?: null | number; "tpm"?: null | number; "unknownLimitStrategy"?: null | string };
export type ProviderLimitRecord = { "cooldownUntil"?: null | string; "createdAt": string; "currentWindow": JsonObject; "dailyRequests"?: null | number; "dailyTokens"?: null | number; "id": string; "last429At"?: null | string; "lastLimitErrorAt"?: null | string; "model": string; "monthlyBudgetUsd"?: null | number; "monthlyRequests"?: null | number; "monthlyTokens"?: null | number; "providerId": string; "rpm"?: null | number; "tpm"?: null | number; "unknownLimitStrategy": string; "updatedAt": string };
export type ProviderLimitResponse = { "providerLimit": ProviderLimitRecord };
export type ProviderLimitsListResponse = { "providerLimits": Array<ProviderLimitRecord> };
export type ProviderRecord = { "capabilities": Array<JsonValue>; "id": string; "kind": string; "label": string; "metadata": JsonObject; "models": Array<JsonValue>; "status": string; "updatedAt": string };
export type ProvidersListResponse = { "providers": Array<ProviderRecord> };
export type QAAgentCommandRequest = { "argv": Array<string>; "critical"?: boolean; "label"?: null | string; "timeoutSeconds"?: null | number };
export type QAAgentContract = { "allowedTools": Array<string>; "id": string; "inputSchema": JsonObject; "outputSchema": JsonObject; "requiredEvidence": boolean; "requiredRuntimeCapabilities": Array<string>; "requiredWorkspace": boolean; "verdictSource": string };
export type QAAgentRunRequest = { "commands"?: Array<QAAgentCommandRequest>; "metadata"?: JsonObject; "projectId": string; "taskId"?: string; "workspaceId": string };
export type QAAgentRunResponse = { "agentRun": AgentRunRecord; "contract": QAAgentContract; "evidencePackage": JsonObject; "job": JsonObject; "reason": string; "results": Array<JsonObject>; "status": string; "verdict": string; "workspace": JsonObject };
export type RequiredReasonRequest = { "reason": string };
export type RestrictedSubprocessStatus = { "available": boolean; "fallbackOnlyForLowRisk": boolean; "requiresArgv": boolean; "shell": boolean; "workspaceBound": boolean };
export type RetrievalIndexSummary = { "backend": string; "dimensions": number; "ids": Array<string>; "indexed": number };
export type RetrievalReindexResponse = { "index": RetrievalIndexSummary };
export type RetrievalSearchRequest = { "limit"?: number; "query"?: string };
export type RetrievalSearchResponse = { "results": Array<RetrievalSearchResultRecord> };
export type RetrievalSearchResultRecord = { "memoryItem": MemoryItemRecord; "score": number };
export type RetrievalStatusResponse = { "backend": string; "degraded": boolean; "dimensions": number; "faissAvailable": boolean; "indexDir": string; "indexed": number };
export type RiskCreateRequest = { "description"?: string; "evidenceRefs"?: Array<string>; "metadata"?: JsonObject; "mitigation"?: string; "owner"?: string; "projectId": string; "severity"?: "low" | "medium" | "high" | "critical"; "status"?: "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed"; "title": string };
export type RiskRecord = { "createdAt": string; "description": string; "evidenceRefs": Array<string>; "id": string; "metadata": JsonObject; "mitigation": string; "owner": string; "projectId": string; "severity": "low" | "medium" | "high" | "critical"; "status": "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed"; "title": string; "updatedAt": string };
export type RiskResponse = { "risk": RiskRecord };
export type RiskUpdateRequest = { "evidenceRefs"?: Array<string> | null; "metadata"?: JsonObject | null; "mitigation"?: null | string; "owner"?: null | string; "severity"?: "low" | "medium" | "high" | "critical" | null; "status"?: "open" | "monitoring" | "mitigating" | "mitigated" | "accepted" | "closed" | null };
export type RisksListResponse = { "risks": Array<RiskRecord> };
export type RolePoliciesListResponse = { "rolePolicies": Array<RolePolicyRecord> };
export type RolePolicyPatchRequest = { "allowApi"?: boolean; "allowCli"?: boolean; "allowLocal"?: boolean; "allowRemote"?: boolean; "blocked"?: Array<JsonObject>; "escalation"?: Array<JsonObject>; "fallback"?: Array<JsonObject>; "id"?: null | string; "maxCostPerTaskUsd"?: number; "maxTokensPerRun"?: number; "preferred"?: Array<JsonObject>; "requiresApprovalForReasoningMax"?: boolean; "requiresApprovalOverUsd"?: null | number; "role"?: null | string; "routingProfileId"?: null | string };
export type RolePolicyRecord = { "allowApi": boolean; "allowCli": boolean; "allowLocal": boolean; "allowRemote": boolean; "blocked": Array<JsonObject>; "createdAt": string; "escalation": Array<JsonObject>; "fallback": Array<JsonObject>; "id": string; "maxCostPerTaskUsd": number; "maxTokensPerRun": number; "preferred": Array<JsonObject>; "requiresApprovalForReasoningMax": boolean; "requiresApprovalOverUsd"?: null | number; "role": string; "routingProfileId": string; "updatedAt": string };
export type RolePolicyResponse = { "rolePolicy": RolePolicyRecord };
export type RolePolicyUpsertRequest = { "allowApi"?: boolean; "allowCli"?: boolean; "allowLocal"?: boolean; "allowRemote"?: boolean; "blocked"?: Array<JsonObject>; "escalation"?: Array<JsonObject>; "fallback"?: Array<JsonObject>; "id"?: null | string; "maxCostPerTaskUsd"?: number; "maxTokensPerRun"?: number; "preferred"?: Array<JsonObject>; "requiresApprovalForReasoningMax"?: boolean; "requiresApprovalOverUsd"?: null | number; "role": string; "routingProfileId"?: null | string };
export type RouteExecuteResponse = { "content"?: null | string; "routing": RoutingPreviewResponse; "usage"?: UsageLedgerRecord | null };
export type RoutingCandidateRecord = { "effort"?: null | string; "estimatedCostUsd"?: null | number; "freeTier"?: boolean; "model": string; "priceKnown"?: boolean; "pricingSource"?: string; "pricingStaleness"?: string; "provider": string; "runtime": string; "score": number; "scoreBreakdown": JsonObject };
export type RoutingDecisionRecord = { "agentId"?: null | string; "candidates": Array<JsonObject>; "createdAt": string; "decisionReason": string; "estimatedCostUsd"?: null | number; "estimatedTokens"?: null | number; "id": string; "jobId"?: null | string; "mode": string; "policyResult": JsonObject; "rejected": Array<JsonObject>; "role": string; "scoreBreakdown": JsonObject; "selectedEffort"?: null | string; "selectedModel"?: null | string; "selectedProvider"?: null | string; "selectedRuntime"?: null | string; "taskId"?: null | string; "taskType": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type RoutingDecisionsListResponse = { "routingDecisions": Array<RoutingDecisionRecord> };
export type RoutingPolicyResult = { "maxCostPerTaskUsd"?: null | number; "requiresApproval": boolean; "rolePolicyId": string };
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
export type RuntimeProviderConfigurationRecord = { "configured": boolean; "displayName": string; "id": string; "kind": string; "missing": Array<string>; "reason": string; "status": "configured" | "configuration_required"; "variables": Array<RuntimeProviderConfigurationVariable> };
export type RuntimeProviderConfigurationResponse = { "providers": Array<RuntimeProviderConfigurationRecord> };
export type RuntimeProviderConfigurationVariable = { "configured": boolean; "fingerprint"?: null | string; "key": string; "name": string; "required": boolean; "secret": boolean };
export type RuntimeProviderSafety = { "network"?: string; "shell"?: boolean; "structuredArgv"?: boolean; "workspaceBound"?: boolean };
export type RuntimeProviderStatus = { "available": boolean; "capabilities"?: Array<string>; "configured": boolean; "detected"?: boolean; "detectedCommand"?: null | string; "displayName": string; "executable": boolean; "healthCheckedAt"?: null | string; "id": string; "kind": string; "reason": string; "requiredConfiguration"?: Array<string>; "requiresApproval"?: boolean; "safety"?: RuntimeProviderSafety; "version"?: null | string };
export type RuntimeProvidersResponse = { "api": ApiRuntimeProviderStatus; "cli": CliRuntimeProviderStatus; "developerAgent": DeveloperAgentStatus; "ollama": OllamaRuntimeProviderStatus; "providers": Array<RuntimeProviderStatus>; "runtimeModes": Array<"api" | "cli" | "ollama" | "hybrid" | "manual"> };
export type SandboxProfileMutationResponse = { "policyRevision"?: PolicyRevisionRecord | null; "sandboxProfile": SandboxProfileRecord };
export type SandboxProfilePatchRequest = { "allowedImages"?: Array<string> | null; "allowedNetworks"?: Array<string> | null; "cpus"?: null | string; "defaultNetwork"?: null | string; "memory"?: null | string; "name"?: null | string; "reason": string; "status"?: null | string; "timeoutSeconds"?: null | number };
export type SandboxProfileRecord = { "allowedImages": Array<string>; "allowedNetworks": Array<string>; "cpus": string; "createdAt": string; "defaultNetwork": string; "id": string; "memory": string; "name": string; "revokeReason"?: null | string; "revokedAt"?: null | string; "revokedBy"?: null | string; "status": string; "timeoutSeconds": number; "updatedAt": string };
export type SandboxProfileResponse = { "sandboxProfile": SandboxProfileRecord };
export type SandboxStatusResponse = { "docker": DockerSandboxStatus; "restrictedSubprocess": RestrictedSubprocessStatus };
export type SecurityPosture = { "loopbackOnly": boolean; "writeTokenRequired": boolean };
export type SessionCreateRequest = { "name"?: null | string; "projectId": string; "teamId"?: null | string };
export type SessionRecord = { "createdAt": string; "id": string; "metadata": JsonObject; "name": string; "projectId": string; "status": string; "teamId"?: null | string; "updatedAt": string };
export type SessionResponse = { "session": SessionRecord };
export type SessionsListResponse = { "sessions": Array<SessionRecord> };
export type SkillRecord = { "compatibility": string; "createdAt": string; "description": string; "id": string; "license": string; "metadata": JsonObject; "name": string; "path": string; "riskLevel": string; "updatedAt": string };
export type SkillsListResponse = { "skills": Array<SkillRecord> };
export type SkillsSyncRequest = { "skillsPath"?: string };
export type SkillsSyncResponse = { "skills": Array<SkillRecord>; "synced": number };
export type TeamRecord = { "capabilities": Array<JsonValue>; "createdAt": string; "id": string; "metadata": JsonObject; "name": string; "projectId": string; "updatedAt": string; "version": string };
export type TeamsListResponse = { "teams": Array<TeamRecord> };
export type TelemetryStatusResponse = { "externalExporter": ExternalTelemetryStatus };
export type TestResultRecord = { "command": string; "createdAt": string; "durationMs"?: null | number; "evidencePackageId": string; "id": string; "metadata": JsonObject; "outputRef"?: null | string; "projectId": string; "status": string };
export type UsageLedgerListResponse = { "usageLedger": Array<UsageLedgerRecord> };
export type UsageLedgerRecord = { "actualCostUsd"?: null | number; "agentId"?: null | string; "cachedInputTokens": number; "costStatus"?: string; "createdAt": string; "currency": string; "estimatedCostUsd"?: null | number; "id": string; "inputTokens": number; "jobId"?: null | string; "latencyMs"?: null | number; "model": string; "outputTokens": number; "providerId": string; "rawUsage": JsonObject; "reasoningTokens": number; "requestId"?: null | string; "role"?: null | string; "runtimeType": string; "sessionId"?: null | string; "taskId"?: null | string; "tokenStatus"?: string; "toolTokens": number; "totalTokens": number; "usageSource": string; "workflowRunId"?: null | string; "workflowStepId"?: null | string };
export type UsageSummaryProvider = { "estimatedCostUsd": number; "providerId": string; "totalTokens": number };
export type UsageSummaryRecord = { "actualCostUsd": number; "byProvider": Array<UsageSummaryProvider>; "estimatedCostUsd": number; "totalTokens": number };
export type UsageSummaryResponse = { "summary": UsageSummaryRecord };
export type ValidationError = { "ctx"?: JsonObject; "input"?: JsonValue; "loc": Array<number | string>; "msg": string; "type": string };
export type WorkflowCreateRequest = { "idea"?: null | string; "kind"?: "idea_to_pr" | "project_discovery" | "issue_to_patch" | "issue_to_pr" | "qa_validation" | "release_candidate" | "pr_release_retro"; "metadata"?: JsonObject; "projectId": string; "title"?: null | string };
export type WorkflowDetailResponse = { "agentRuns": Array<AgentRunRecord>; "evidencePackages": Array<EvidencePackageRecord>; "jobs": Array<JobRecord>; "workflow": WorkflowRecord; "workflowRuns": Array<WorkflowRunRecord>; "workflowSteps": Array<WorkflowStepRecord>; "workspaces": Array<WorkspaceRecord> };
export type WorkflowGateAdvanceRequest = { "evidencePackageId"?: null | string; "reason"?: string };
export type WorkflowGateAdvanceResponse = { "advanced": boolean; "gateState": string; "reason": string; "workflowStep": WorkflowStepRecord };
export type WorkflowRecord = { "createdAt": string; "id": string; "kind": string; "metadata": JsonObject; "projectId": string; "status": string; "title": string; "updatedAt": string };
export type WorkflowResponse = { "workflow": WorkflowRecord };
export type WorkflowRunRecord = { "completedAt"?: null | string; "id": string; "metadata": JsonObject; "projectId": string; "startedAt": string; "status": string; "workflowId": string };
export type WorkflowStartResponse = { "workflow": WorkflowRecord; "workflowRun": WorkflowRunRecord; "workflowSteps": Array<WorkflowStepRecord> };
export type WorkflowStatusChangeRequest = { "reason"?: string };
export type WorkflowStepRecord = { "agentProfileId"?: null | string; "createdAt": string; "id": string; "input": JsonObject; "manualModelOverride"?: null | string; "metadata": JsonObject; "modelMode"?: null | string; "name": string; "output": JsonObject; "projectId": string; "riskLevel"?: null | string; "role"?: null | string; "status": string; "taskType"?: null | string; "updatedAt": string; "workflowId": string; "workflowRunId": string };
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
	{"method": "POST", "operationId": "run_developer_agent_api_v1_agents_developer_runs_post", "path": "/api/v1/agents/developer/runs", "summary": "Run Developer Agent"},
	{"method": "GET", "operationId": "developer_agent_status_api_v1_agents_developer_status_get", "path": "/api/v1/agents/developer/status", "summary": "Developer Agent Status"},
	{"method": "POST", "operationId": "run_qa_agent_api_v1_agents_qa_runs_post", "path": "/api/v1/agents/qa/runs", "summary": "Run Qa Agent"},
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
	{"method": "GET", "operationId": "get_i18n_catalog_api_v1_i18n_catalog_get", "path": "/api/v1/i18n/catalog", "summary": "Get I18N Catalog"},
	{"method": "PUT", "operationId": "put_i18n_catalog_api_v1_i18n_catalog_put", "path": "/api/v1/i18n/catalog", "summary": "Put I18N Catalog"},
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
	{"method": "POST", "operationId": "select_directory_api_v1_local_paths_select_directory_post", "path": "/api/v1/local-paths/select-directory", "summary": "Select Directory"},
	{"method": "GET", "operationId": "list_memory_api_v1_memory_get", "path": "/api/v1/memory", "summary": "List Memory"},
	{"method": "POST", "operationId": "create_memory_api_v1_memory_post", "path": "/api/v1/memory", "summary": "Create Memory"},
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
	{"method": "PATCH", "operationId": "patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch", "path": "/api/v1/model-gateway/provider-limits/{limit_id}", "summary": "Patch Provider Limit"},
	{"method": "GET", "operationId": "list_providers_api_v1_model_gateway_providers_get", "path": "/api/v1/model-gateway/providers", "summary": "List Providers"},
	{"method": "POST", "operationId": "create_provider_api_v1_model_gateway_providers_post", "path": "/api/v1/model-gateway/providers", "summary": "Create Provider"},
	{"method": "GET", "operationId": "get_provider_api_v1_model_gateway_providers__provider_id__get", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Get Provider"},
	{"method": "PATCH", "operationId": "patch_provider_api_v1_model_gateway_providers__provider_id__patch", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Patch Provider"},
	{"method": "POST", "operationId": "discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post", "path": "/api/v1/model-gateway/providers/{provider_id}/discover-models", "summary": "Discover Models"},
	{"method": "POST", "operationId": "provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post", "path": "/api/v1/model-gateway/providers/{provider_id}/health-check", "summary": "Provider Health Check"},
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
	{"method": "POST", "operationId": "discover_project_api_v1_projects_discover_post", "path": "/api/v1/projects/discover", "summary": "Discover Project"},
	{"method": "GET", "operationId": "list_prompts_api_v1_prompts_get", "path": "/api/v1/prompts", "summary": "List Prompts"},
	{"method": "POST", "operationId": "upsert_prompt_api_v1_prompts_post", "path": "/api/v1/prompts", "summary": "Upsert Prompt"},
	{"method": "GET", "operationId": "providers_api_v1_providers_get", "path": "/api/v1/providers", "summary": "Providers"},
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
	{"method": "GET", "operationId": "list_sessions_api_v1_sessions_get", "path": "/api/v1/sessions", "summary": "List Sessions"},
	{"method": "POST", "operationId": "create_session_api_v1_sessions_post", "path": "/api/v1/sessions", "summary": "Create Session"},
	{"method": "GET", "operationId": "list_skills_api_v1_skills_get", "path": "/api/v1/skills", "summary": "List Skills"},
	{"method": "POST", "operationId": "sync_skills_api_v1_skills_sync_post", "path": "/api/v1/skills/sync", "summary": "Sync Skills"},
	{"method": "GET", "operationId": "teams_api_v1_teams_get", "path": "/api/v1/teams", "summary": "Teams"},
	{"method": "GET", "operationId": "telemetry_status_api_v1_telemetry_status_get", "path": "/api/v1/telemetry/status", "summary": "Telemetry Status"},
	{"method": "GET", "operationId": "list_workflows_api_v1_workflows_get", "path": "/api/v1/workflows", "summary": "List Workflows"},
	{"method": "POST", "operationId": "create_workflow_api_v1_workflows_post", "path": "/api/v1/workflows", "summary": "Create Workflow"},
	{"method": "POST", "operationId": "run_issue_to_patch_api_v1_workflows_issue_to_patch_post", "path": "/api/v1/workflows/issue-to-patch", "summary": "Run Issue To Patch"},
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
	"advance_workflow_gate_api_v1_workflows__workflow_id__steps__step_id__advance_post": WorkflowGateAdvanceRequest,
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
	"create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post": ModelBenchmarkOutcomeCreateRequest,
	"create_budget_rule_api_v1_model_gateway_budget_rules_post": BudgetRuleUpsertRequest,
	"create_chat_api_v1_chats_post": ChatCreateRequest,
	"create_evidence_api_v1_evidence_post": EvidenceCreateRequest,
	"create_job_api_v1_jobs_post": JobCreateRequest,
	"create_memory_api_v1_memory_post": MemoryCreateRequest,
	"create_model_api_v1_model_gateway_models_post": ModelCatalogUpsertRequest,
	"create_next_step_api_v1_next_steps_post": NextStepCreateRequest,
	"create_pipeline_api_v1_pipelines_post": PipelineCreateRequest,
	"create_pricing_snapshot_api_v1_model_gateway_pricing_snapshots_post": PricingSnapshotCreateRequest,
	"create_project_api_v1_projects_post": ProjectCreateRequest,
	"create_provider_api_v1_model_gateway_providers_post": ProviderAccountUpsertRequest,
	"create_risk_api_v1_risks_post": RiskCreateRequest,
	"create_role_policy_api_v1_model_gateway_role_policies_post": RolePolicyUpsertRequest,
	"create_routing_profile_api_v1_model_gateway_routing_profiles_post": RoutingProfileUpsertRequest,
	"create_session_api_v1_sessions_post": SessionCreateRequest,
	"create_workflow_api_v1_workflows_post": WorkflowCreateRequest,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": OptionalReasonRequest,
	"detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post": unknown,
	"developer_agent_status_api_v1_agents_developer_status_get": never,
	"discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post": unknown,
	"discover_project_api_v1_projects_discover_post": ProjectDiscoveryRequest,
	"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluateRequest,
	"events_api_v1_events_get": never,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_cli_session_api_v1_model_gateway_cli_sessions__session_id__get": never,
	"get_evidence_api_v1_evidence__evidence_id__get": never,
	"get_i18n_catalog_api_v1_i18n_catalog_get": never,
	"get_provider_api_v1_model_gateway_providers__provider_id__get": never,
	"get_workflow_api_v1_workflows__workflow_id__get": never,
	"governance_api_v1_governance_get": never,
	"handshake_api_v1_security_handshake_get": never,
	"health_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__health_check_post": unknown,
	"healthz_healthz_get": never,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactIngestRequest,
	"list_agent_profiles_api_v1_agent_profiles_get": never,
	"list_agent_runs_api_v1_agent_runs_get": never,
	"list_architecture_decisions_api_v1_architecture_decisions_get": never,
	"list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get": never,
	"list_benchmarks_api_v1_model_gateway_benchmarks_get": never,
	"list_budget_rules_api_v1_model_gateway_budget_rules_get": never,
	"list_chats_api_v1_chats_get": never,
	"list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get": never,
	"list_cli_sessions_api_v1_model_gateway_cli_sessions_get": never,
	"list_evidence_api_v1_evidence_get": never,
	"list_ide_connections_api_v1_ide_connections_get": never,
	"list_integrations_api_v1_integrations_get": never,
	"list_jobs_api_v1_jobs_get": never,
	"list_memory_api_v1_memory_get": never,
	"list_model_policies_api_v1_model_policies_get": never,
	"list_model_providers_api_v1_model_providers_get": never,
	"list_models_api_v1_model_gateway_models_get": never,
	"list_next_steps_api_v1_next_steps_get": never,
	"list_pipelines_api_v1_pipelines_get": never,
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
	"list_sessions_api_v1_sessions_get": never,
	"list_skills_api_v1_skills_get": never,
	"list_usage_ledger_api_v1_model_gateway_usage_ledger_get": never,
	"list_workflows_api_v1_workflows_get": never,
	"list_workspaces_api_v1_workspaces_get": never,
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
	"project_templates_api_v1_project_templates_get": never,
	"projects_api_v1_projects_get": never,
	"provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post": unknown,
	"providers_api_v1_providers_get": never,
	"put_i18n_catalog_api_v1_i18n_catalog_put": I18nCatalog,
	"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerRegisterRequest,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": WorkflowStatusChangeRequest,
	"retrieval_reindex_api_v1_retrieval_reindex_post": EmptyObjectRequest,
	"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchRequest,
	"retrieval_status_api_v1_retrieval_status_get": never,
	"retry_job_api_v1_jobs__job_id__retry_post": OptionalReasonRequest,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": RequiredReasonRequest,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": RequiredReasonRequest,
	"route_execute_api_v1_model_gateway_route_execute_post": RoutingPreviewRequest,
	"route_preview_api_v1_model_gateway_route_preview_post": RoutingPreviewRequest,
	"run_developer_agent_api_v1_agents_developer_runs_post": DeveloperAgentRunRequest,
	"run_issue_to_patch_api_v1_workflows_issue_to_patch_post": IssueToPatchRequest,
	"run_qa_agent_api_v1_agents_qa_runs_post": QAAgentRunRequest,
	"sandbox_status_api_v1_sandbox_status_get": never,
	"select_directory_api_v1_local_paths_select_directory_post": DirectoryPickerRequest,
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
	"upsert_prompt_api_v1_prompts_post": PromptUpsertRequest,
	"usage_summary_api_v1_model_gateway_usage_ledger_summary_get": never
};

export type OperationResponseBodies = {
	"advance_workflow_gate_api_v1_workflows__workflow_id__steps__step_id__advance_post": WorkflowGateAdvanceResponse,
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
	"create_benchmark_outcome_api_v1_model_gateway_benchmark_outcomes_post": ModelBenchmarkOutcomeResponse,
	"create_budget_rule_api_v1_model_gateway_budget_rules_post": BudgetRuleResponse,
	"create_chat_api_v1_chats_post": ChatResponse,
	"create_evidence_api_v1_evidence_post": EvidencePackageResponse,
	"create_job_api_v1_jobs_post": JobMutationResponse,
	"create_memory_api_v1_memory_post": MemoryResponse,
	"create_model_api_v1_model_gateway_models_post": ModelCatalogResponse,
	"create_next_step_api_v1_next_steps_post": NextStepResponse,
	"create_pipeline_api_v1_pipelines_post": PipelineResponse,
	"create_pricing_snapshot_api_v1_model_gateway_pricing_snapshots_post": PricingSnapshotResponse,
	"create_project_api_v1_projects_post": ProjectResponse,
	"create_provider_api_v1_model_gateway_providers_post": ProviderAccountResponse,
	"create_risk_api_v1_risks_post": RiskResponse,
	"create_role_policy_api_v1_model_gateway_role_policies_post": RolePolicyResponse,
	"create_routing_profile_api_v1_model_gateway_routing_profiles_post": RoutingProfileResponse,
	"create_session_api_v1_sessions_post": SessionResponse,
	"create_workflow_api_v1_workflows_post": WorkflowResponse,
	"deny_action_api_v1_jobs__job_id__actions__action_id__deny_post": JobMutationResponse,
	"detect_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__detect_post": RuntimeDetectionResponse,
	"developer_agent_status_api_v1_agents_developer_status_get": DeveloperAgentStatusResponse,
	"discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post": DiscoverModelsResponse,
	"discover_project_api_v1_projects_discover_post": ProjectDiscoveryResponse,
	"evaluate_policy_api_v1_policies_evaluate_post": PolicyEvaluationResponse,
	"events_api_v1_events_get": never,
	"export_evidence_report_api_v1_evidence__evidence_id__report_get": never,
	"get_artifact_api_v1_evidence__evidence_id__artifacts__artifact_id__get": never,
	"get_cli_session_api_v1_model_gateway_cli_sessions__session_id__get": CliSessionResponse,
	"get_evidence_api_v1_evidence__evidence_id__get": EvidenceDetailResponse,
	"get_i18n_catalog_api_v1_i18n_catalog_get": I18nCatalogResponse,
	"get_provider_api_v1_model_gateway_providers__provider_id__get": ProviderAccountResponse,
	"get_workflow_api_v1_workflows__workflow_id__get": WorkflowDetailResponse,
	"governance_api_v1_governance_get": GovernanceResponse,
	"handshake_api_v1_security_handshake_get": HandshakeResponse,
	"health_cli_runtime_api_v1_model_gateway_cli_runtimes__runtime_id__health_check_post": RuntimeHealthResponse,
	"healthz_healthz_get": HealthResponse,
	"ingest_artifact_api_v1_evidence__evidence_id__artifacts_post": ArtifactResponse,
	"list_agent_profiles_api_v1_agent_profiles_get": AgentProfilesListResponse,
	"list_agent_runs_api_v1_agent_runs_get": AgentRunsListResponse,
	"list_architecture_decisions_api_v1_architecture_decisions_get": ArchitectureDecisionsListResponse,
	"list_benchmark_outcomes_api_v1_model_gateway_benchmark_outcomes_get": ModelBenchmarkOutcomesListResponse,
	"list_benchmarks_api_v1_model_gateway_benchmarks_get": ModelBenchmarksListResponse,
	"list_budget_rules_api_v1_model_gateway_budget_rules_get": BudgetRulesListResponse,
	"list_chats_api_v1_chats_get": ChatsListResponse,
	"list_cli_runtimes_api_v1_model_gateway_cli_runtimes_get": CliRuntimesListResponse,
	"list_cli_sessions_api_v1_model_gateway_cli_sessions_get": CliSessionsListResponse,
	"list_evidence_api_v1_evidence_get": EvidenceListResponse,
	"list_ide_connections_api_v1_ide_connections_get": IdeConnectionsListResponse,
	"list_integrations_api_v1_integrations_get": IntegrationsListResponse,
	"list_jobs_api_v1_jobs_get": JobsListResponse,
	"list_memory_api_v1_memory_get": MemoryListResponse,
	"list_model_policies_api_v1_model_policies_get": ModelPoliciesListResponse,
	"list_model_providers_api_v1_model_providers_get": ModelProvidersListResponse,
	"list_models_api_v1_model_gateway_models_get": ModelCatalogListResponse,
	"list_next_steps_api_v1_next_steps_get": NextStepsListResponse,
	"list_pipelines_api_v1_pipelines_get": PipelinesListResponse,
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
	"list_sessions_api_v1_sessions_get": SessionsListResponse,
	"list_skills_api_v1_skills_get": SkillsListResponse,
	"list_usage_ledger_api_v1_model_gateway_usage_ledger_get": UsageLedgerListResponse,
	"list_workflows_api_v1_workflows_get": WorkflowsListResponse,
	"list_workspaces_api_v1_workspaces_get": WorkspacesListResponse,
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
	"project_templates_api_v1_project_templates_get": ProjectTemplatesResponse,
	"projects_api_v1_projects_get": ProjectsListResponse,
	"provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post": ProviderHealthResponse,
	"providers_api_v1_providers_get": ProvidersListResponse,
	"put_i18n_catalog_api_v1_i18n_catalog_put": I18nCatalogResponse,
	"register_mcp_server_api_v1_integrations_mcp_register_post": McpServerResponse,
	"resume_workflow_api_v1_workflows__workflow_id__resume_post": WorkflowResponse,
	"retrieval_reindex_api_v1_retrieval_reindex_post": RetrievalReindexResponse,
	"retrieval_search_api_v1_retrieval_search_post": RetrievalSearchResponse,
	"retrieval_status_api_v1_retrieval_status_get": RetrievalStatusResponse,
	"retry_job_api_v1_jobs__job_id__retry_post": JobMutationResponse,
	"revoke_permission_grant_api_v1_permissions_grants__grant_id__revoke_post": PermissionGrantResponse,
	"revoke_sandbox_profile_api_v1_sandbox_profiles__profile_id__revoke_post": SandboxProfileResponse,
	"route_execute_api_v1_model_gateway_route_execute_post": RouteExecuteResponse,
	"route_preview_api_v1_model_gateway_route_preview_post": RoutingPreviewResponse,
	"run_developer_agent_api_v1_agents_developer_runs_post": DeveloperAgentRunResponse,
	"run_issue_to_patch_api_v1_workflows_issue_to_patch_post": IssueToPatchResponse,
	"run_qa_agent_api_v1_agents_qa_runs_post": QAAgentRunResponse,
	"sandbox_status_api_v1_sandbox_status_get": SandboxStatusResponse,
	"select_directory_api_v1_local_paths_select_directory_post": DirectoryPickerResponse,
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
	"upsert_prompt_api_v1_prompts_post": PromptResponse,
	"usage_summary_api_v1_model_gateway_usage_ledger_summary_get": UsageSummaryResponse
};

export type OperationRequestBody<T extends ApiOperationId> = OperationRequestBodies[T];
export type OperationResponse<T extends ApiOperationId> = OperationResponseBodies[T];

export const OPERATIONS_BY_ID = {
	"list_agent_profiles_api_v1_agent_profiles_get": {"method": "GET", "operationId": "list_agent_profiles_api_v1_agent_profiles_get", "path": "/api/v1/agent-profiles", "summary": "List Agent Profiles"},
	"upsert_agent_profile_api_v1_agent_profiles_post": {"method": "POST", "operationId": "upsert_agent_profile_api_v1_agent_profiles_post", "path": "/api/v1/agent-profiles", "summary": "Upsert Agent Profile"},
	"list_agent_runs_api_v1_agent_runs_get": {"method": "GET", "operationId": "list_agent_runs_api_v1_agent_runs_get", "path": "/api/v1/agent-runs", "summary": "List Agent Runs"},
	"create_agent_run_api_v1_agent_runs_post": {"method": "POST", "operationId": "create_agent_run_api_v1_agent_runs_post", "path": "/api/v1/agent-runs", "summary": "Create Agent Run"},
	"agents_api_v1_agents_get": {"method": "GET", "operationId": "agents_api_v1_agents_get", "path": "/api/v1/agents", "summary": "Agents"},
	"run_developer_agent_api_v1_agents_developer_runs_post": {"method": "POST", "operationId": "run_developer_agent_api_v1_agents_developer_runs_post", "path": "/api/v1/agents/developer/runs", "summary": "Run Developer Agent"},
	"developer_agent_status_api_v1_agents_developer_status_get": {"method": "GET", "operationId": "developer_agent_status_api_v1_agents_developer_status_get", "path": "/api/v1/agents/developer/status", "summary": "Developer Agent Status"},
	"run_qa_agent_api_v1_agents_qa_runs_post": {"method": "POST", "operationId": "run_qa_agent_api_v1_agents_qa_runs_post", "path": "/api/v1/agents/qa/runs", "summary": "Run Qa Agent"},
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
	"get_i18n_catalog_api_v1_i18n_catalog_get": {"method": "GET", "operationId": "get_i18n_catalog_api_v1_i18n_catalog_get", "path": "/api/v1/i18n/catalog", "summary": "Get I18N Catalog"},
	"put_i18n_catalog_api_v1_i18n_catalog_put": {"method": "PUT", "operationId": "put_i18n_catalog_api_v1_i18n_catalog_put", "path": "/api/v1/i18n/catalog", "summary": "Put I18N Catalog"},
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
	"select_directory_api_v1_local_paths_select_directory_post": {"method": "POST", "operationId": "select_directory_api_v1_local_paths_select_directory_post", "path": "/api/v1/local-paths/select-directory", "summary": "Select Directory"},
	"list_memory_api_v1_memory_get": {"method": "GET", "operationId": "list_memory_api_v1_memory_get", "path": "/api/v1/memory", "summary": "List Memory"},
	"create_memory_api_v1_memory_post": {"method": "POST", "operationId": "create_memory_api_v1_memory_post", "path": "/api/v1/memory", "summary": "Create Memory"},
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
	"patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch": {"method": "PATCH", "operationId": "patch_provider_limit_api_v1_model_gateway_provider_limits__limit_id__patch", "path": "/api/v1/model-gateway/provider-limits/{limit_id}", "summary": "Patch Provider Limit"},
	"list_providers_api_v1_model_gateway_providers_get": {"method": "GET", "operationId": "list_providers_api_v1_model_gateway_providers_get", "path": "/api/v1/model-gateway/providers", "summary": "List Providers"},
	"create_provider_api_v1_model_gateway_providers_post": {"method": "POST", "operationId": "create_provider_api_v1_model_gateway_providers_post", "path": "/api/v1/model-gateway/providers", "summary": "Create Provider"},
	"get_provider_api_v1_model_gateway_providers__provider_id__get": {"method": "GET", "operationId": "get_provider_api_v1_model_gateway_providers__provider_id__get", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Get Provider"},
	"patch_provider_api_v1_model_gateway_providers__provider_id__patch": {"method": "PATCH", "operationId": "patch_provider_api_v1_model_gateway_providers__provider_id__patch", "path": "/api/v1/model-gateway/providers/{provider_id}", "summary": "Patch Provider"},
	"discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post": {"method": "POST", "operationId": "discover_models_api_v1_model_gateway_providers__provider_id__discover_models_post", "path": "/api/v1/model-gateway/providers/{provider_id}/discover-models", "summary": "Discover Models"},
	"provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post": {"method": "POST", "operationId": "provider_health_check_api_v1_model_gateway_providers__provider_id__health_check_post", "path": "/api/v1/model-gateway/providers/{provider_id}/health-check", "summary": "Provider Health Check"},
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
	"discover_project_api_v1_projects_discover_post": {"method": "POST", "operationId": "discover_project_api_v1_projects_discover_post", "path": "/api/v1/projects/discover", "summary": "Discover Project"},
	"list_prompts_api_v1_prompts_get": {"method": "GET", "operationId": "list_prompts_api_v1_prompts_get", "path": "/api/v1/prompts", "summary": "List Prompts"},
	"upsert_prompt_api_v1_prompts_post": {"method": "POST", "operationId": "upsert_prompt_api_v1_prompts_post", "path": "/api/v1/prompts", "summary": "Upsert Prompt"},
	"providers_api_v1_providers_get": {"method": "GET", "operationId": "providers_api_v1_providers_get", "path": "/api/v1/providers", "summary": "Providers"},
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
	"list_sessions_api_v1_sessions_get": {"method": "GET", "operationId": "list_sessions_api_v1_sessions_get", "path": "/api/v1/sessions", "summary": "List Sessions"},
	"create_session_api_v1_sessions_post": {"method": "POST", "operationId": "create_session_api_v1_sessions_post", "path": "/api/v1/sessions", "summary": "Create Session"},
	"list_skills_api_v1_skills_get": {"method": "GET", "operationId": "list_skills_api_v1_skills_get", "path": "/api/v1/skills", "summary": "List Skills"},
	"sync_skills_api_v1_skills_sync_post": {"method": "POST", "operationId": "sync_skills_api_v1_skills_sync_post", "path": "/api/v1/skills/sync", "summary": "Sync Skills"},
	"teams_api_v1_teams_get": {"method": "GET", "operationId": "teams_api_v1_teams_get", "path": "/api/v1/teams", "summary": "Teams"},
	"telemetry_status_api_v1_telemetry_status_get": {"method": "GET", "operationId": "telemetry_status_api_v1_telemetry_status_get", "path": "/api/v1/telemetry/status", "summary": "Telemetry Status"},
	"list_workflows_api_v1_workflows_get": {"method": "GET", "operationId": "list_workflows_api_v1_workflows_get", "path": "/api/v1/workflows", "summary": "List Workflows"},
	"create_workflow_api_v1_workflows_post": {"method": "POST", "operationId": "create_workflow_api_v1_workflows_post", "path": "/api/v1/workflows", "summary": "Create Workflow"},
	"run_issue_to_patch_api_v1_workflows_issue_to_patch_post": {"method": "POST", "operationId": "run_issue_to_patch_api_v1_workflows_issue_to_patch_post", "path": "/api/v1/workflows/issue-to-patch", "summary": "Run Issue To Patch"},
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
