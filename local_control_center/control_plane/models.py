"""Contrato Pydantic de la respuesta de overview: tipa el snapshot agregado de todos los slices.

Define el esquema de salida que ``build_overview_from_connection`` produce: una coleccion por
slice mas la postura de seguridad y el estado de Open Design. Los alias camelCase fijan el
contrato JSON que consume el frontend; cada campo reusa el record tipado de su slice de origen.

@author Rodrigo Mason
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from local_control_center.agents.contracts import (
    AgentProfileRecord,
    AgentRunRecord,
    AgentToolCallRecord,
    CostUsageRecord,
    ModelCallRecord,
    ModelPolicyRecord,
    ModelProviderRecord,
    SkillRecord,
)
from local_control_center.evidence.models import ArtifactRecord, EvidencePackageRecord, TestResultRecord
from local_control_center.governance.models import ArchitectureDecisionRecord, NextStepRecord, RiskRecord
from local_control_center.integrations.models import IdeConnectionRecord, McpServerRecord
from local_control_center.jobs_approvals.models import ActionRequestRecord, JobRecord, JobRunRecord
from local_control_center.memory_retrieval.models import MemoryItemRecord
from local_control_center.pipelines.models import PipelineRecord
from local_control_center.projects.models import (
    CatalogAgentRecord,
    ProjectRecord,
    ProjectTemplateRecord,
    ProviderRecord,
    TeamRecord,
)
from local_control_center.prompts.models import PromptTemplateRecord
from local_control_center.security_policy.models import (
    PermissionDecisionRecord,
    PermissionGrantRecord,
    PolicyRevisionRecord,
    SandboxProfileRecord,
)
from local_control_center.sessions_chats.models import ChatRecord, SessionRecord
from local_control_center.shared.schemas import AuditEventRecord, EventRecord
from local_control_center.workflows.models import (
    WorkflowEventRecord,
    WorkflowRecord,
    WorkflowRunRecord,
    WorkflowStepRecord,
)
from local_control_center.workspaces_projects.models import WorkspaceRecord


class OpenDesignStatus(BaseModel):
    """Estado del subsistema Open Design tal como lo reporta el backend al overview."""

    status: str


class SecurityPosture(BaseModel):
    """Garantias de seguridad del runtime expuestas al cliente (loopback y token de escritura)."""

    loopback_only: bool = Field(alias="loopbackOnly")
    write_token_required: bool = Field(alias="writeTokenRequired")


class OverviewResponse(BaseModel):
    """Respuesta agregada del overview: una coleccion tipada por cada slice de la plataforma."""

    project_templates: list[ProjectTemplateRecord] = Field(alias="projectTemplates")
    projects: list[ProjectRecord]
    providers: list[ProviderRecord]
    teams: list[TeamRecord]
    agents: list[CatalogAgentRecord]
    sessions: list[SessionRecord]
    chats: list[ChatRecord]
    pipelines: list[PipelineRecord]
    jobs: list[JobRecord]
    job_runs: list[JobRunRecord] = Field(alias="jobRuns")
    events: list[EventRecord]
    audit_events: list[AuditEventRecord] = Field(alias="auditEvents")
    memory_items: list[MemoryItemRecord] = Field(alias="memoryItems")
    prompt_templates: list[PromptTemplateRecord] = Field(alias="promptTemplates")
    action_requests: list[ActionRequestRecord] = Field(alias="actionRequests")
    ide_connections: list[IdeConnectionRecord] = Field(alias="ideConnections")
    mcp_servers: list[McpServerRecord] = Field(alias="mcpServers")
    workflows: list[WorkflowRecord]
    workflow_runs: list[WorkflowRunRecord] = Field(alias="workflowRuns")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")
    workflow_events: list[WorkflowEventRecord] = Field(alias="workflowEvents")
    permission_decisions: list[PermissionDecisionRecord] = Field(alias="permissionDecisions")
    policy_revisions: list[PolicyRevisionRecord] = Field(alias="policyRevisions")
    permission_grants: list[PermissionGrantRecord] = Field(alias="permissionGrants")
    sandbox_profiles: list[SandboxProfileRecord] = Field(alias="sandboxProfiles")
    evidence_packages: list[EvidencePackageRecord] = Field(alias="evidencePackages")
    artifacts: list[ArtifactRecord]
    test_result_records: list[TestResultRecord] = Field(alias="testResultRecords")
    agent_profiles: list[AgentProfileRecord] = Field(alias="agentProfiles")
    agent_runs: list[AgentRunRecord] = Field(alias="agentRuns")
    model_policies: list[ModelPolicyRecord] = Field(alias="modelPolicies")
    model_providers: list[ModelProviderRecord] = Field(alias="modelProviders")
    agent_tool_calls: list[AgentToolCallRecord] = Field(alias="agentToolCalls")
    model_calls: list[ModelCallRecord] = Field(alias="modelCalls")
    cost_usage: list[CostUsageRecord] = Field(alias="costUsage")
    runtime_workspaces: list[WorkspaceRecord] = Field(alias="runtimeWorkspaces")
    skills: list[SkillRecord]
    architecture_decisions: list[ArchitectureDecisionRecord] = Field(alias="architectureDecisions")
    risk_register: list[RiskRecord] = Field(alias="riskRegister")
    next_steps: list[NextStepRecord] = Field(alias="nextSteps")
    open_design: OpenDesignStatus = Field(alias="openDesign")
    security: SecurityPosture
