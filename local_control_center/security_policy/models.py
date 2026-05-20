from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequiredReasonRequest(BaseModel):
    reason: str


class PolicyEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_id: str | None = Field(default=None, alias="projectId")
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    agent_id: str | None = Field(default=None, alias="agentId")
    role: str | None = None
    tool: str | None = None
    command: str | None = None
    operation: str | None = None
    path: str | None = None
    environment: str | None = None
    permission_profile: str | None = Field(default=None, alias="permissionProfile")
    risk_level: str | None = Field(default=None, alias="riskLevel")
    network_required: bool | None = Field(default=None, alias="networkRequired")
    secrets_required: bool | None = Field(default=None, alias="secretsRequired")
    git_operation: str | None = Field(default=None, alias="gitOperation")
    deployment_target: str | None = Field(default=None, alias="deploymentTarget")


class PolicyEvaluationResponse(BaseModel):
    decision: "PermissionDecisionRecord"


class PolicyRecord(BaseModel):
    id: str
    name: str
    profile: str
    rules: list[dict[str, Any]]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class PolicyRevisionRecord(BaseModel):
    id: str
    subject_type: str = Field(alias="subjectType")
    subject_id: str = Field(alias="subjectId")
    version: int
    reason: str
    actor: str
    previous: dict[str, Any]
    updated: dict[str, Any]
    changed_fields: list[str] = Field(alias="changedFields")
    created_at: str = Field(alias="createdAt")


class PermissionDecisionRecord(BaseModel):
    id: str
    project_id: str | None = Field(default=None, alias="projectId")
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    agent_id: str | None = Field(default=None, alias="agentId")
    role: str | None = None
    tool: str | None = None
    command: str | None = None
    path: str | None = None
    decision: str
    risk_level: str = Field(alias="riskLevel")
    reason: str
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class PermissionGrantRecord(BaseModel):
    id: str
    project_id: str | None = Field(default=None, alias="projectId")
    job_id: str | None = Field(default=None, alias="jobId")
    action_request_id: str | None = Field(default=None, alias="actionRequestId")
    permission_decision_id: str | None = Field(default=None, alias="permissionDecisionId")
    agent_id: str | None = Field(default=None, alias="agentId")
    tool: str | None = None
    command: str | None = None
    path: str | None = None
    status: str
    reason: str
    granted_by: str | None = Field(default=None, alias="grantedBy")
    granted_at: str | None = Field(default=None, alias="grantedAt")
    consumed_at: str | None = Field(default=None, alias="consumedAt")
    consumed_by_agent_run_id: str | None = Field(default=None, alias="consumedByAgentRunId")
    revoked_at: str | None = Field(default=None, alias="revokedAt")
    revoked_by: str | None = Field(default=None, alias="revokedBy")
    revoke_reason: str | None = Field(default=None, alias="revokeReason")
    payload: dict[str, Any]


class SandboxProfileRecord(BaseModel):
    id: str
    name: str
    allowed_images: list[str] = Field(alias="allowedImages")
    allowed_networks: list[str] = Field(alias="allowedNetworks")
    default_network: str = Field(alias="defaultNetwork")
    memory: str
    cpus: str
    timeout_seconds: int = Field(alias="timeoutSeconds")
    status: str
    revoked_at: str | None = Field(default=None, alias="revokedAt")
    revoked_by: str | None = Field(default=None, alias="revokedBy")
    revoke_reason: str | None = Field(default=None, alias="revokeReason")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class PoliciesListResponse(BaseModel):
    policies: list[PolicyRecord]
    policy_revisions: list[PolicyRevisionRecord] = Field(alias="policyRevisions")
    permission_decisions: list[PermissionDecisionRecord] = Field(alias="permissionDecisions")
    permission_grants: list[PermissionGrantRecord] = Field(alias="permissionGrants")
    sandbox_profiles: list[SandboxProfileRecord] = Field(alias="sandboxProfiles")


class SandboxProfilePatchRequest(BaseModel):
    reason: str
    name: str | None = None
    allowed_images: list[str] | None = Field(default=None, alias="allowedImages")
    allowed_networks: list[str] | None = Field(default=None, alias="allowedNetworks")
    default_network: str | None = Field(default=None, alias="defaultNetwork")
    memory: str | None = None
    cpus: str | None = None
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds")
    status: str | None = None

    @field_validator("status", "default_network", mode="before")
    @classmethod
    def normalize_scalar_choices(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @field_validator("allowed_networks", mode="before")
    @classmethod
    def normalize_networks(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [item.lower() if isinstance(item, str) else item for item in value]
        return value


class PermissionGrantResponse(BaseModel):
    permission_grant: PermissionGrantRecord = Field(alias="permissionGrant")


class SandboxProfileResponse(BaseModel):
    sandbox_profile: SandboxProfileRecord = Field(alias="sandboxProfile")


class SandboxStatusResponse(BaseModel):
    docker: dict[str, Any]
    restricted_subprocess: dict[str, Any] = Field(alias="restrictedSubprocess")


class SandboxProfileMutationResponse(BaseModel):
    sandbox_profile: SandboxProfileRecord = Field(alias="sandboxProfile")
    policy_revision: PolicyRevisionRecord | None = Field(default=None, alias="policyRevision")
