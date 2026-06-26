"""Read-only builder that joins agent telemetry into Team Activity entries for one project.

Mirrors the overview builder: it instantiates the agents, backlog and evidence repositories
over the caller's connection and composes a camelCase dict aligned with ``TeamActivityResponse``.
The spine is ``agent_runs`` (the actual activity, with timing, redacted payloads and status);
each run is enriched with its assignment (matched by agent identity, because the run's
``metadata.taskId`` is corrupted by secret redaction), the assignment's handoff (blocked reason,
delivered artifact) and review (reviewer), its model/tool calls (cost, duration inputs, low-level
events) and the agent profile (role, runtime). It only reads; no statement here writes or commits.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

from local_control_center.agents.repository import AgentsRepository
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

# Run statuses that count as in-flight work shown at the top of the board.
ACTIVE_RUN_STATUSES = frozenset({"queued", "running", "awaiting_permission", "approval_required"})
# In-flight work that is stuck; surfaced with its blocked reason.
BLOCKED_RUN_STATUSES = frozenset({"blocked", "runtime_unavailable", "qa_failed"})
# Run statuses that mean the unit of work finished (its artifact can be considered delivered).
DONE_RUN_STATUSES = frozenset({"completed", "approved", "evidence_ready"})
# Assignment statuses that mean the agent is actively on that assignment (preferred as "current").
ACTIVE_ASSIGNMENT_STATUSES = frozenset({"active", "in_progress", "running", "accepted", "proposed"})
# Assignment statuses that mean the assignment delivered its canonical artifact.
COMPLETED_ASSIGNMENT_STATUSES = frozenset({"released", "completed", "done"})
# Handoff statuses that mean the handed-off artifact was accepted downstream.
COMPLETED_HANDOFF_STATUSES = frozenset({"accepted", "resolved", "completed", "approved"})
# Model-call cost statuses that mean the cost was actually measured (vs. "unknown"/"not_started").
KNOWN_COST_STATUSES = frozenset({"actual", "free"})

# Default cap on terminal entries returned alongside the in-flight ones, to bound the payload.
DEFAULT_RECENT_LIMIT = 12


def _state_for(status: str) -> str:
    """Map a raw agent-run status to the board's coarse state (active/blocked/done)."""
    if status in BLOCKED_RUN_STATUSES:
        return "blocked"
    if status in ACTIVE_RUN_STATUSES:
        return "active"
    return "done"


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a decoded JSON payload to a dict so developer details are always object-shaped."""
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    return {"value": value}


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a canonical ISO-8601 (``Z`` suffix) timestamp, returning None when unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: str | None, ended_at: str | None) -> int | None:
    """Return the elapsed milliseconds between two ISO timestamps, or None if either is missing."""
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None or end is None:
        return None
    delta_ms = int((end - start).total_seconds() * 1000)
    return max(delta_ms, 0)


def _runtime_label(runtime_mode: str | None, provider: str | None) -> str:
    """Build the provider/runtime label, e.g. ``anthropic_api · api`` or just the runtime mode."""
    mode = runtime_mode or "unknown"
    if provider:
        return f"{provider} · {mode}"
    return mode


def _select_assignment(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick an agent's current assignment: an active one if any, else the most recent.

    ``candidates`` are the agent's assignments (newest first). The run links to its task through
    the agent rather than ``metadata.taskId``, because that value is corrupted by secret redaction
    (a task id ``agent-task-...`` contains the literal ``sk-`` that the redactor masks). The
    assignment carries the clean ``taskId`` used downstream to resolve the task title.
    """
    if not candidates:
        return None
    for assignment in candidates:
        if assignment.get("status") in ACTIVE_ASSIGNMENT_STATUSES:
            return assignment
    return candidates[0]


def _completed_artifact(
    assignment: dict[str, Any] | None,
    handoffs: list[dict[str, Any]],
    run_status: str,
    artifacts_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve the delivered artifact for an assignment, or None when nothing is completed yet.

    Prefers a handed-off artifact whose handoff was accepted downstream; otherwise falls back to
    the assignment's canonical artifact once the assignment or its run has finished.
    """
    if not assignment:
        return None
    artifact_id = ""
    delivered = [
        handoff
        for handoff in handoffs
        if handoff.get("status") in COMPLETED_HANDOFF_STATUSES and handoff.get("artifactId")
    ]
    if delivered:
        artifact_id = str(delivered[-1]["artifactId"])
    elif assignment.get("status") in COMPLETED_ASSIGNMENT_STATUSES or run_status in DONE_RUN_STATUSES:
        artifact_id = str(assignment.get("canonicalArtifactId") or "")
    if not artifact_id:
        return None
    artifact = artifacts_by_id.get(artifact_id)
    if not artifact:
        return None
    metadata = artifact.get("metadata") or {}
    return {
        "artifactId": artifact["id"],
        "kind": artifact.get("kind") or "unknown",
        "name": metadata.get("name"),
        "path": artifact.get("path"),
    }


def _blocked_reason(state: str, handoffs: list[dict[str, Any]], output: dict[str, Any]) -> str | None:
    """Derive a human blocked reason from a blocked handoff, else the run output, when stuck.

    The handoff ``blockedReason`` is persisted raw (unlike the run output, which is redacted at
    write time), so it is re-redacted here before it can travel in the HTTP response.
    """
    for handoff in handoffs:
        reason = (handoff.get("blockedReason") or "").strip()
        if handoff.get("status") == "blocked" and reason:
            return redact_secrets(reason)
    if state == "blocked":
        for key in ("reason", "blockedReason", "summary"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return redact_secrets(value.strip())
    return None


def _reviewer(reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the most recent review with a named reviewer, or None when no agent reviewed."""
    for review in reversed(reviews):
        reviewer_id = (review.get("reviewerAgentId") or "").strip()
        if reviewer_id:
            decision = (review.get("decision") or "").strip()
            return {
                "reviewerAgentId": reviewer_id,
                "status": review.get("status") or "pending",
                "decision": decision or None,
            }
    return None


def _call_has_known_cost(call: dict[str, Any]) -> bool:
    """Report whether a model call's cost was actually measured.

    Prefers the explicit ``metadata.costStatus`` written by the model gateway: a row persisted
    with ``cost_usd`` 0.0 and ``costStatus`` ``unknown`` (price never measured, or a budget-blocked
    call) is NOT a real $0.00 and must not count. Falls back to ``costUsd is not None`` for calls
    recorded without a cost status, preserving the directly-recorded-cost path.
    """
    status = (call.get("metadata") or {}).get("costStatus")
    if status is not None:
        return status in KNOWN_COST_STATUSES
    return call.get("costUsd") is not None


def _cost(model_calls: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    """Sum the run's measured model-call costs; (None, None) when none has a known cost.

    Returning None (rendered as "not recorded") rather than 0.0 keeps an unmeasured run honestly
    distinct from a genuinely-zero one, per the "cost when known" requirement.
    """
    priced = [call for call in model_calls if _call_has_known_cost(call)]
    if not priced:
        return None, None
    total = sum(float(call.get("costUsd") or 0.0) for call in priced)
    return round(total, 6), "actual"


def _model_summary(call: dict[str, Any]) -> dict[str, Any]:
    """Project a model_call dict to its Team Activity summary shape."""
    return {
        "id": call["id"],
        "provider": call.get("provider") or "unknown",
        "model": call.get("model") or "unknown",
        "status": call.get("status") or "unknown",
        "promptTokens": int(call.get("promptTokens") or 0),
        "completionTokens": int(call.get("completionTokens") or 0),
        "costUsd": call.get("costUsd"),
        "createdAt": call.get("createdAt") or "",
    }


def _tool_summary(call: dict[str, Any]) -> dict[str, Any]:
    """Project an agent_tool_call dict to its Team Activity summary shape."""
    return {
        "id": call["id"],
        "toolName": call.get("toolName") or "unknown",
        "status": call.get("status") or "unknown",
        "createdAt": call.get("createdAt") or "",
    }


def _group_by(records: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """Group dict records by a key, preserving each input order within a bucket."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(key) or "")].append(record)
    return grouped


def build_team_activity(
    *,
    connection: sqlite3.Connection,
    project_id: str,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    """Aggregate the project's agent runs into Team Activity entries (in-flight first).

    Batch-loads every source once (no per-entry queries), joins them in memory and returns a
    camelCase dict matching ``TeamActivityResponse``. ``recent_limit`` caps how many terminal
    ("done") entries accompany the in-flight ones; ``truncated`` reports when terminal entries
    were dropped so the cap is never silent.
    """
    agents = AgentsRepository(connection)
    backlog = BacklogRepository(connection)
    evidence = EvidenceRepository(connection)

    runs = [run for run in agents.list_agent_runs() if run.get("projectId") == project_id]
    profiles_by_id = {profile["id"]: profile for profile in agents.list_agent_profiles()}
    model_calls_by_run = _group_by(agents.list_model_calls(), "agentRunId")
    tool_calls_by_run = _group_by(agents.list_agent_tool_calls(), "agentRunId")

    assignments_by_agent = _group_by(backlog.list_agent_assignments(project_id=project_id), "agentId")
    handoffs_by_assignment = _group_by(
        backlog.list_assignment_handoffs(project_id=project_id), "assignmentId"
    )
    reviews_by_assignment = _group_by(backlog.list_assignment_reviews(project_id=project_id), "assignmentId")
    tasks_by_id = {task["id"]: task for task in backlog.list_agent_tasks(project_id=project_id)}
    artifacts_by_id = {artifact["id"]: artifact for artifact in evidence.list_all_artifacts()}

    in_flight: list[dict[str, Any]] = []
    done: list[dict[str, Any]] = []

    for run in runs:
        metadata = _as_dict(run.get("metadata"))
        output = _as_dict(run.get("output"))
        profile_id = str(metadata.get("agentProfileId") or "")
        agent_identity = str(metadata.get("agentId") or profile_id)
        profile = profiles_by_id.get(profile_id)

        status = str(run.get("status") or "unknown")
        state = _state_for(status)

        assignment = _select_assignment(assignments_by_agent.get(agent_identity, []))
        assignment_id = str(assignment.get("id")) if assignment else ""
        task_id = str(assignment.get("taskId") or "") if assignment else ""
        handoffs = handoffs_by_assignment.get(assignment_id, []) if assignment else []
        reviews = reviews_by_assignment.get(assignment_id, []) if assignment else []

        model_calls = model_calls_by_run.get(run["id"], [])
        tool_calls = tool_calls_by_run.get(run["id"], [])
        cost_usd, cost_source = _cost(model_calls)

        provider = model_calls[0].get("provider") if model_calls else None
        runtime_mode = (profile or {}).get("runtimeMode") or metadata.get("runtimeType")

        current_assignment = None
        if assignment:
            current_assignment = {
                "assignmentId": assignment["id"],
                "taskId": assignment.get("taskId") or task_id,
                "taskTitle": (tasks_by_id.get(task_id, {}) or {}).get("title"),
                "assignmentStatus": assignment.get("status") or "unknown",
            }

        role = (profile or {}).get("role") or assignment_role(assignment) or "unknown"
        agent_name = (profile or {}).get("name") or agent_identity or profile_id or "agent"

        entry = {
            "id": run["id"],
            "agentId": agent_identity or profile_id or run["id"],
            "agentName": agent_name,
            "role": role,
            "state": state,
            "status": status,
            "runtime": _runtime_label(runtime_mode, provider),
            "provider": provider,
            "currentAssignment": current_assignment,
            "blockedReason": _blocked_reason(state, handoffs, output),
            "completedArtifact": _completed_artifact(assignment, handoffs, status, artifacts_by_id),
            "reviewer": _reviewer(reviews),
            "startedAt": run.get("createdAt"),
            "endedAt": run.get("updatedAt"),
            "durationMs": _duration_ms(run.get("createdAt"), run.get("updatedAt")),
            "costUsd": cost_usd,
            "costSource": cost_source,
            "modelCallCount": len(model_calls),
            "toolCallCount": len(tool_calls),
            "lowLevelEvents": {
                # Sorted ascending so the disclosure reads in execution order; the repository
                # returns calls newest-first, which the provider/cost reads above still rely on.
                "modelCalls": [
                    _model_summary(call)
                    for call in sorted(model_calls, key=lambda call: call.get("createdAt") or "")
                ],
                "toolCalls": [
                    _tool_summary(call)
                    for call in sorted(tool_calls, key=lambda call: call.get("createdAt") or "")
                ],
            },
            "developerDetails": {
                "input": _as_dict(run.get("input")),
                "output": output,
                "metadata": metadata,
            },
        }

        if state == "done":
            done.append(entry)
        else:
            in_flight.append(entry)

    in_flight.sort(key=lambda item: (item["startedAt"] or "", item["id"]), reverse=True)
    done.sort(key=lambda item: (item["endedAt"] or "", item["id"]), reverse=True)

    capped_done = done[:recent_limit] if recent_limit >= 0 else done
    entries = in_flight + capped_done

    return {
        "projectId": project_id,
        "generatedAt": utc_now(),
        "activeCount": sum(1 for entry in in_flight if entry["state"] == "active"),
        "blockedCount": sum(1 for entry in in_flight if entry["state"] == "blocked"),
        "totalCount": len(in_flight) + len(done),
        "truncated": len(done) > len(capped_done),
        "entries": entries,
    }


def assignment_role(assignment: dict[str, Any] | None) -> str | None:
    """Return the assignment's role when present, used as a fallback when no profile resolves."""
    if not assignment:
        return None
    role = (assignment.get("role") or "").strip()
    return role or None
