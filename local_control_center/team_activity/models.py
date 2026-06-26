"""Pydantic contracts for the Team Activity HTTP response (camelCase on the wire).

Defines the validated shape the shell consumes: a list of per-run activity entries, each
carrying the headline fields (agent, role, runtime, assignment, blocked reason, completed
artifact, reviewer, duration, cost) plus collapsible low-level model/tool events and a
developer-details block with the run's already-redacted input/output/metadata. Python keeps
snake_case while keys travel as camelCase via ``alias``; the service emits that camelCase.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntryState = Literal["active", "blocked", "done"]
CostSource = Literal["actual", "estimated"]


class _Aliased(BaseModel):
    """Base allowing population by field name or camelCase alias for both read and write."""

    model_config = ConfigDict(populate_by_name=True)


class AssignmentSummary(_Aliased):
    """The work item an agent is currently on, resolved to its human title."""

    assignment_id: str = Field(alias="assignmentId")
    task_id: str = Field(alias="taskId")
    task_title: str | None = Field(default=None, alias="taskTitle")
    assignment_status: str = Field(alias="assignmentStatus")


class ArtifactSummary(_Aliased):
    """A delivered artifact (the agent's completed output) resolved from the evidence store."""

    artifact_id: str = Field(alias="artifactId")
    kind: str
    name: str | None = None
    path: str | None = None


class ReviewerSummary(_Aliased):
    """The agent reviewing the work and the current review verdict, when one exists."""

    reviewer_agent_id: str = Field(alias="reviewerAgentId")
    status: str
    decision: str | None = None


class ModelCallSummary(_Aliased):
    """One low-level model invocation: provider, model, token counts and cost."""

    id: str
    provider: str
    model: str
    status: str
    prompt_tokens: int = Field(alias="promptTokens")
    completion_tokens: int = Field(alias="completionTokens")
    cost_usd: float | None = Field(default=None, alias="costUsd")
    created_at: str = Field(alias="createdAt")


class ToolCallSummary(_Aliased):
    """One low-level tool call: which tool ran and its outcome."""

    id: str
    tool_name: str = Field(alias="toolName")
    status: str
    created_at: str = Field(alias="createdAt")


class LowLevelEvents(_Aliased):
    """The collapsed-by-default model and tool events behind an activity entry."""

    model_calls: list[ModelCallSummary] = Field(default_factory=list, alias="modelCalls")
    tool_calls: list[ToolCallSummary] = Field(default_factory=list, alias="toolCalls")


class DeveloperDetails(_Aliased):
    """The run's raw, already-redacted structured payloads for the developer-details reveal."""

    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamActivityEntry(_Aliased):
    """A single agent run enriched into a Team Activity row the shell renders as a card."""

    id: str
    agent_id: str = Field(alias="agentId")
    agent_name: str = Field(alias="agentName")
    role: str
    state: EntryState
    status: str
    runtime: str
    provider: str | None = None
    current_assignment: AssignmentSummary | None = Field(default=None, alias="currentAssignment")
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    completed_artifact: ArtifactSummary | None = Field(default=None, alias="completedArtifact")
    reviewer: ReviewerSummary | None = None
    started_at: str | None = Field(default=None, alias="startedAt")
    ended_at: str | None = Field(default=None, alias="endedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    cost_usd: float | None = Field(default=None, alias="costUsd")
    cost_source: CostSource | None = Field(default=None, alias="costSource")
    model_call_count: int = Field(alias="modelCallCount")
    tool_call_count: int = Field(alias="toolCallCount")
    low_level_events: LowLevelEvents = Field(alias="lowLevelEvents")
    developer_details: DeveloperDetails = Field(alias="developerDetails")


class TeamActivityResponse(_Aliased):
    """The Team Activity board for one project: counts plus the ordered activity entries."""

    project_id: str = Field(alias="projectId")
    generated_at: str = Field(alias="generatedAt")
    active_count: int = Field(alias="activeCount")
    blocked_count: int = Field(alias="blockedCount")
    total_count: int = Field(alias="totalCount")
    truncated: bool
    entries: list[TeamActivityEntry] = Field(default_factory=list)
