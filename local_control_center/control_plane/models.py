from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from local_control_center.evidence.models import ArtifactRecord, EvidencePackageRecord, TestResultRecord
from local_control_center.governance.models import ArchitectureDecisionRecord, NextStepRecord, RiskRecord
from local_control_center.jobs_approvals.models import ActionRequestRecord, JobRecord, JobRunRecord
from local_control_center.projects.models import (
    CatalogAgentRecord,
    ProjectRecord,
    ProjectTemplateRecord,
    ProviderRecord,
    TeamRecord,
)
from local_control_center.shared.schemas import AuditEventRecord, EventRecord
from local_control_center.workspaces_projects.models import WorkspaceRecord


class OverviewResponse(BaseModel):
    project_templates: list[ProjectTemplateRecord] = Field(alias="projectTemplates")
    projects: list[ProjectRecord]
    providers: list[ProviderRecord]
    teams: list[TeamRecord]
    agents: list[CatalogAgentRecord]
    sessions: list[dict[str, Any]]
    chats: list[dict[str, Any]]
    pipelines: list[dict[str, Any]]
    jobs: list[JobRecord]
    job_runs: list[JobRunRecord] = Field(alias="jobRuns")
    events: list[EventRecord]
    audit_events: list[AuditEventRecord] = Field(alias="auditEvents")
    memory_items: list[dict[str, Any]] = Field(alias="memoryItems")
    prompt_templates: list[dict[str, Any]] = Field(alias="promptTemplates")
    action_requests: list[ActionRequestRecord] = Field(alias="actionRequests")
    ide_connections: list[dict[str, Any]] = Field(alias="ideConnections")
    mcp_servers: list[dict[str, Any]] = Field(alias="mcpServers")
    workflows: list[dict[str, Any]]
    workflow_runs: list[dict[str, Any]] = Field(alias="workflowRuns")
    workflow_steps: list[dict[str, Any]] = Field(alias="workflowSteps")
    permission_decisions: list[dict[str, Any]] = Field(alias="permissionDecisions")
    policy_revisions: list[dict[str, Any]] = Field(alias="policyRevisions")
    permission_grants: list[dict[str, Any]] = Field(alias="permissionGrants")
    sandbox_profiles: list[dict[str, Any]] = Field(alias="sandboxProfiles")
    evidence_packages: list[EvidencePackageRecord] = Field(alias="evidencePackages")
    artifacts: list[ArtifactRecord]
    test_result_records: list[TestResultRecord] = Field(alias="testResultRecords")
    agent_profiles: list[dict[str, Any]] = Field(alias="agentProfiles")
    agent_runs: list[dict[str, Any]] = Field(alias="agentRuns")
    model_policies: list[dict[str, Any]] = Field(alias="modelPolicies")
    model_providers: list[dict[str, Any]] = Field(alias="modelProviders")
    agent_tool_calls: list[dict[str, Any]] = Field(alias="agentToolCalls")
    model_calls: list[dict[str, Any]] = Field(alias="modelCalls")
    cost_usage: list[dict[str, Any]] = Field(alias="costUsage")
    runtime_workspaces: list[WorkspaceRecord] = Field(alias="runtimeWorkspaces")
    skills: list[dict[str, Any]]
    architecture_decisions: list[ArchitectureDecisionRecord] = Field(alias="architectureDecisions")
    risk_register: list[RiskRecord] = Field(alias="riskRegister")
    next_steps: list[NextStepRecord] = Field(alias="nextSteps")
    open_design: dict[str, Any] = Field(alias="openDesign")
    security: dict[str, Any]
