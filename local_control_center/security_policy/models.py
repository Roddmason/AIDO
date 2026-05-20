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
    decision: dict[str, Any]


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
    permission_grant: dict[str, Any] = Field(alias="permissionGrant")


class SandboxProfileResponse(BaseModel):
    sandbox_profile: dict[str, Any] = Field(alias="sandboxProfile")


class SandboxProfileMutationResponse(BaseModel):
    sandbox_profile: dict[str, Any] = Field(alias="sandboxProfile")
    policy_revision: dict[str, Any] | None = Field(default=None, alias="policyRevision")
