from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentRole = Literal["product_owner", "technical_lead", "implementer", "qa_reviewer", "security_reviewer"]
PermissionProfile = Literal["plan", "dev_safe", "qa", "release"]
RuntimeMode = Literal["api", "cli", "ollama", "hybrid", "manual", "internal_mock"]
PolicyStatus = Literal["active", "disabled"]


class ModelProviderCandidate(BaseModel):
    provider: str
    model: str


class AgentProfileUpsertRequest(BaseModel):
    id: str
    name: str | None = None
    role: AgentRole = "implementer"
    runtime_mode: RuntimeMode = Field(default="internal_mock", alias="runtimeMode")
    runtime_type: RuntimeMode | None = Field(default=None, alias="runtimeType")
    model_policy_id: str | None = Field(default=None, alias="modelPolicyId")
    allowed_skills: list[str] = Field(default_factory=list, alias="allowedSkills")
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    permission_profile: PermissionProfile = Field(default="plan", alias="permissionProfile")
    memory_scope: str = Field(default="project", alias="memoryScope")
    max_cost_per_run: float = Field(default=0, alias="maxCostPerRun")
    max_runtime_seconds: int = Field(default=900, alias="maxRuntimeSeconds")
    output_schema: dict[str, Any] = Field(default_factory=dict, alias="outputSchema")
    quality_gates: list[Any] = Field(default_factory=list, alias="qualityGates")
    status: PolicyStatus = "active"


class AgentProfileResponse(BaseModel):
    agent_profile: dict[str, Any] = Field(alias="agentProfile")


class AgentRunCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    agent_profile_id: str = Field(alias="agentProfileId")
    task_id: str = Field(default="task", alias="taskId")
    input: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = Field(default=None, alias="jobId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")


class AgentRunResponse(BaseModel):
    agent_run: dict[str, Any] = Field(alias="agentRun")


class SkillsSyncRequest(BaseModel):
    skills_path: str = Field(default="skills", alias="skillsPath")


class SkillsSyncResponse(BaseModel):
    synced: int
    skills: list[dict[str, Any]]


class ModelPolicyUpsertRequest(BaseModel):
    id: str
    name: str | None = None
    preferred: list[ModelProviderCandidate] = Field(default_factory=list)
    fallback: list[ModelProviderCandidate] = Field(default_factory=list)
    max_cost_usd: float = Field(default=0, alias="maxCostUsd")
    max_tokens: int = Field(default=0, alias="maxTokens")
    temperature: float = 0.2
    allow_remote: bool = Field(default=True, alias="allowRemote")
    allow_local: bool = Field(default=True, alias="allowLocal")
    status: PolicyStatus = "active"


class ModelPolicyResponse(BaseModel):
    model_policy: dict[str, Any] = Field(alias="modelPolicy")
