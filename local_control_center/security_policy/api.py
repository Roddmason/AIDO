from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from ..governance.signals import record_governance_risk
from .policy_engine import evaluate_action
from .repository import SecurityPolicyRepository
from ..workspaces_projects.repository import WorkspacesRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> SecurityPolicyRepository:
        return SecurityPolicyRepository(platform.connection)

    def workspaces() -> WorkspacesRepository:
        return WorkspacesRepository(platform.connection, root=platform.cwd)

    @router.get("/api/v1/policies")
    async def list_policies() -> dict[str, Any]:
        repo = repository()
        return {"policies": repo.list_policies(), "permissionDecisions": repo.list_decisions()}

    @router.post("/api/v1/policies/evaluate")
    async def evaluate_policy(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        if body.get("workspaceId"):
            try:
                workspace = workspaces().get_workspace(body["workspaceId"])
                body = {**body, "workspacePath": workspace["path"], "workspaceStatus": workspace["status"]}
            except KeyError:
                body = {**body, "workspacePath": None, "workspaceStatus": "unknown"}
        result = evaluate_action(body)
        decision_payload = {**body, "categories": result.get("categories", [])}
        decision = repository().record_decision(
            project_id=body.get("projectId"),
            workspace_id=body.get("workspaceId"),
            agent_id=body.get("agentId"),
            role=body.get("role"),
            tool=body.get("tool"),
            command=body.get("command"),
            path=body.get("path"),
            decision=result["decision"],
            risk_level=result["riskLevel"],
            reason=result["reason"],
            payload=decision_payload,
        )
        platform.record_event(
            project_id=body.get("projectId"),
            event_type=f"policy.{decision['decision']}",
            payload={"permissionDecisionId": decision["id"], "riskLevel": decision["riskLevel"]},
        )
        platform.record_audit(
            project_id=body.get("projectId"),
            action="policy.evaluate",
            target=decision["id"],
            payload={"decision": decision["decision"], "riskLevel": decision["riskLevel"]},
        )
        if decision["decision"] in {"deny", "requires_approval", "requires_human"}:
            risk = record_governance_risk(
                platform.connection,
                project_id=decision.get("projectId"),
                title=f"Policy gated action: {decision.get('tool') or 'unknown tool'}",
                source_type="policy_decision",
                source_id=decision["id"],
                severity=decision["riskLevel"],
                description=decision["reason"],
                mitigation="Review the policy decision, approve only with a reason, and keep execution inside the allocated workspace.",
                owner=decision.get("role") or "",
                metadata={"decision": decision["decision"], "command": decision.get("command")},
            )
            if risk:
                platform.record_event(
                    project_id=risk["projectId"],
                    event_type="risk.created",
                    payload={"riskId": risk["id"], "sourceType": "policy_decision"},
                )
        return {"decision": decision}

    return router
