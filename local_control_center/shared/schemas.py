from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool


class HandshakeResponse(BaseModel):
    token: str
    loopback_only: bool = Field(alias="loopbackOnly")


class RetrievalStatusResponse(BaseModel):
    backend: str
    degraded: bool
    faiss_available: bool = Field(alias="faissAvailable")
    index_dir: str = Field(alias="indexDir")
    indexed: int
    dimensions: int


class TelemetryStatusResponse(BaseModel):
    external_exporter: dict[str, Any] = Field(alias="externalExporter")


class EventRecord(BaseModel):
    id: str
    job_id: str | None = Field(default=None, alias="jobId")
    project_id: str | None = Field(default=None, alias="projectId")
    type: str
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class AuditEventRecord(BaseModel):
    id: str
    project_id: str | None = Field(default=None, alias="projectId")
    action: str
    actor: str
    target: str
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class OverviewResponse(BaseModel):
    project_templates: list[dict[str, Any]] = Field(alias="projectTemplates")
    projects: list[dict[str, Any]]
    providers: list[dict[str, Any]]
    teams: list[dict[str, Any]]
    agents: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    chats: list[dict[str, Any]]
    pipelines: list[dict[str, Any]]
    jobs: list[dict[str, Any]]
    job_runs: list[dict[str, Any]] = Field(alias="jobRuns")
    events: list[dict[str, Any]]
    audit_events: list[dict[str, Any]] = Field(alias="auditEvents")
    memory_items: list[dict[str, Any]] = Field(alias="memoryItems")
    prompt_templates: list[dict[str, Any]] = Field(alias="promptTemplates")
    action_requests: list[dict[str, Any]] = Field(alias="actionRequests")
    ide_connections: list[dict[str, Any]] = Field(alias="ideConnections")
    mcp_servers: list[dict[str, Any]] = Field(alias="mcpServers")
    workflows: list[dict[str, Any]]
    workflow_runs: list[dict[str, Any]] = Field(alias="workflowRuns")
    workflow_steps: list[dict[str, Any]] = Field(alias="workflowSteps")
    permission_decisions: list[dict[str, Any]] = Field(alias="permissionDecisions")
    policy_revisions: list[dict[str, Any]] = Field(alias="policyRevisions")
    permission_grants: list[dict[str, Any]] = Field(alias="permissionGrants")
    sandbox_profiles: list[dict[str, Any]] = Field(alias="sandboxProfiles")
    evidence_packages: list[dict[str, Any]] = Field(alias="evidencePackages")
    artifacts: list[dict[str, Any]]
    test_result_records: list[dict[str, Any]] = Field(alias="testResultRecords")
    agent_profiles: list[dict[str, Any]] = Field(alias="agentProfiles")
    agent_runs: list[dict[str, Any]] = Field(alias="agentRuns")
    model_policies: list[dict[str, Any]] = Field(alias="modelPolicies")
    model_providers: list[dict[str, Any]] = Field(alias="modelProviders")
    agent_tool_calls: list[dict[str, Any]] = Field(alias="agentToolCalls")
    model_calls: list[dict[str, Any]] = Field(alias="modelCalls")
    cost_usage: list[dict[str, Any]] = Field(alias="costUsage")
    runtime_workspaces: list[dict[str, Any]] = Field(alias="runtimeWorkspaces")
    skills: list[dict[str, Any]]
    architecture_decisions: list[dict[str, Any]] = Field(alias="architectureDecisions")
    risk_register: list[dict[str, Any]] = Field(alias="riskRegister")
    next_steps: list[dict[str, Any]] = Field(alias="nextSteps")
    open_design: dict[str, Any] = Field(alias="openDesign")
    security: dict[str, Any]
