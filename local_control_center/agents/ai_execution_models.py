"""Strict public contracts for bounded one-or-many chat execution."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AIExecutionStrategy = Literal["single", "parallel_compare", "quorum"]
AIExecutionStatus = Literal["blocked", "completed", "partial", "failed"]
AIExecutionBranchStatus = Literal["blocked", "completed", "failed"]

MAX_EXECUTION_BRANCHES = 8
MAX_EXECUTION_MESSAGES = 64
MAX_EXECUTION_MESSAGE_BYTES = 262_144


class AIExecutionContract(BaseModel):
    """Camel-case API model with strict, assignment-validated fields."""

    model_config = ConfigDict(
        alias_generator=None,
        populate_by_name=True,
        extra="forbid",
        validate_assignment=True,
    )


class AIExecutionMessage(AIExecutionContract):
    """Text-only chat message accepted by the multi-model executor."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1, max_length=131_072)


class AIExecutionBranchPlan(AIExecutionContract):
    """One explicit endpoint/model branch; no implicit fallback is inferred."""

    provider_id: str = Field(alias="providerId", min_length=1, max_length=128, pattern=r"^\S+$")
    model: str = Field(min_length=1, max_length=192, pattern=r"^\S+$")
    max_tokens: int = Field(alias="maxTokens", ge=1, le=131_072)


class AIExecutionPlan(AIExecutionContract):
    """A bounded chat plan whose branches are all explicit and independently auditable."""

    project_id: str = Field(alias="projectId", min_length=1, max_length=128)
    strategy: AIExecutionStrategy = "single"
    messages: list[AIExecutionMessage] = Field(
        min_length=1,
        max_length=MAX_EXECUTION_MESSAGES,
    )
    branches: list[AIExecutionBranchPlan] = Field(
        min_length=1,
        max_length=MAX_EXECUTION_BRANCHES,
    )
    max_parallelism: int = Field(default=1, alias="maxParallelism", ge=1, le=MAX_EXECUTION_BRANCHES)
    min_successful: int = Field(default=1, alias="minSuccessful", ge=1, le=MAX_EXECUTION_BRANCHES)
    temperature: float | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def validate_plan_shape(self) -> AIExecutionPlan:
        """Reject ambiguous cardinality, duplicates and unbounded request text."""
        branch_count = len(self.branches)
        if self.max_parallelism > branch_count:
            raise ValueError("maxParallelism cannot exceed the number of branches")
        identities = {(branch.provider_id, branch.model) for branch in self.branches}
        if len(identities) != branch_count:
            raise ValueError("providerId/model branches must be unique")
        if self.strategy == "single":
            if branch_count != 1 or self.min_successful != 1:
                raise ValueError("single requires exactly one branch and minSuccessful=1")
        elif self.strategy == "parallel_compare":
            if self.min_successful != 1:
                raise ValueError("parallel_compare requires minSuccessful=1")
        elif branch_count < 2 or not 2 <= self.min_successful <= branch_count:
            raise ValueError("quorum requires at least two branches and minSuccessful between 2 and branch count")
        message_bytes = sum(
            len(message.role.encode("utf-8")) + len(message.content.encode("utf-8"))
            for message in self.messages
        )
        if message_bytes > MAX_EXECUTION_MESSAGE_BYTES:
            raise ValueError(f"messages exceed {MAX_EXECUTION_MESSAGE_BYTES} UTF-8 bytes")
        return self


class AIExecutionBranchResult(AIExecutionContract):
    """Public result for one branch; raw provider payloads are intentionally absent."""

    branch_id: str = Field(alias="branchId")
    provider_id: str = Field(alias="providerId")
    model: str
    status: AIExecutionBranchStatus
    lease_id: str | None = Field(default=None, alias="leaseId")
    content: str | None = None
    error_code: str | None = Field(default=None, alias="errorCode")
    reserved_tokens: int = Field(alias="reservedTokens")
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    pricing_source: str = Field(alias="pricingSource")
    input_tokens: int | None = Field(default=None, alias="inputTokens")
    output_tokens: int | None = Field(default=None, alias="outputTokens")
    total_tokens: int | None = Field(default=None, alias="totalTokens")
    usage_status: Literal["actual", "unknown"] = Field(alias="usageStatus")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    cost_status: Literal["actual", "free", "unknown"] = Field(alias="costStatus")
    latency_ms: int | None = Field(default=None, alias="latencyMs")


class AIExecutionResult(AIExecutionContract):
    """Outcome of a synchronous chat execution plan."""

    execution_id: str = Field(alias="executionId")
    project_id: str = Field(alias="projectId")
    strategy: AIExecutionStrategy
    status: AIExecutionStatus
    succeeded: bool
    min_successful: int = Field(alias="minSuccessful")
    max_parallelism: int = Field(alias="maxParallelism")
    successful_branches: int = Field(alias="successfulBranches")
    failed_branches: int = Field(alias="failedBranches")
    branches: list[AIExecutionBranchResult]
    created_at: str = Field(alias="createdAt")
    completed_at: str = Field(alias="completedAt")


class AIExecutionResponse(AIExecutionContract):
    """HTTP wrapper for the execution result."""

    execution: AIExecutionResult
