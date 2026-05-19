from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..governance.signals import record_governance_risk
from ..shared.event_bus import EventBus
from ..workspaces_projects.repository import WorkspacesRepository
from .policy_engine import evaluate_action
from .repository import SecurityPolicyRepository
from .sandbox import DockerSandbox


def required_reason(body: dict[str, Any]) -> str:
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Revocation reason is required.")
    return reason


IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,127}$")
RESOURCE_RE = re.compile(r"^\d+(?:\.\d+)?[kKmMgG]?$")
SAFE_DOCKER_NETWORKS = {"none"}


def validate_sandbox_profile_patch(body: dict[str, Any]) -> dict[str, Any]:
    patch = {key: value for key, value in body.items() if key != "reason"}
    if "allowedImages" in patch:
        images = patch["allowedImages"]
        if not isinstance(images, list) or not images or not all(isinstance(item, str) for item in images):
            raise HTTPException(status_code=422, detail="allowedImages must be a non-empty list of catalog image strings.")
        if any(not IMAGE_RE.match(item) or any(char.isspace() for char in item) for item in images):
            raise HTTPException(status_code=422, detail="allowedImages contains an invalid Docker image catalog value.")
    if "allowedNetworks" in patch:
        networks = patch["allowedNetworks"]
        if not isinstance(networks, list) or not networks or not all(isinstance(item, str) for item in networks):
            raise HTTPException(status_code=422, detail="allowedNetworks must be a non-empty list.")
        if not set(networks) <= SAFE_DOCKER_NETWORKS:
            raise HTTPException(status_code=422, detail="Sandbox network edits are limited to network=none in MVP.")
    if "defaultNetwork" in patch:
        network = str(patch["defaultNetwork"])
        if network not in SAFE_DOCKER_NETWORKS:
            raise HTTPException(status_code=422, detail="defaultNetwork must be none in MVP.")
        if "allowedNetworks" in patch and network not in patch["allowedNetworks"]:
            raise HTTPException(status_code=422, detail="defaultNetwork must be included in allowedNetworks.")
    for key in ("memory", "cpus"):
        if key in patch and (not isinstance(patch[key], str) or not RESOURCE_RE.match(patch[key])):
            raise HTTPException(status_code=422, detail=f"{key} must be a Docker resource value.")
    if "timeoutSeconds" in patch:
        try:
            timeout = int(patch["timeoutSeconds"])
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="timeoutSeconds must be an integer.") from error
        if timeout < 1 or timeout > 900:
            raise HTTPException(status_code=422, detail="timeoutSeconds must be between 1 and 900.")
        patch["timeoutSeconds"] = timeout
    if "status" in patch and patch["status"] not in {"active", "disabled"}:
        raise HTTPException(status_code=422, detail="status can only be active or disabled through edit.")
    return patch


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> SecurityPolicyRepository:
        return SecurityPolicyRepository(platform.connection)

    def workspaces() -> WorkspacesRepository:
        return WorkspacesRepository(platform.connection, root=platform.cwd)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/policies")
    async def list_policies() -> dict[str, Any]:
        repo = repository()
        return {
            "policies": repo.list_policies(),
            "permissionDecisions": repo.list_decisions(),
            "permissionGrants": repo.list_grants(),
            "sandboxProfiles": repo.list_sandbox_profiles(),
        }

    @router.get("/api/v1/sandbox/status")
    async def sandbox_status() -> dict[str, Any]:
        docker_status = DockerSandbox().status()
        docker_status["policy"] = repository().get_sandbox_profile("default_docker")
        return {
            "docker": docker_status,
            "restrictedSubprocess": {
                "available": True,
                "shell": False,
                "requiresArgv": True,
                "workspaceBound": True,
                "fallbackOnlyForLowRisk": True,
            },
        }

    @router.post("/api/v1/permissions/grants/{grant_id}/revoke", status_code=202)
    async def revoke_permission_grant(grant_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        reason = required_reason(await request.json())
        try:
            grant = repository().revoke_grant(grant_id, reason=reason)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        event_bus().record_event(
            project_id=grant["projectId"],
            job_id=grant.get("jobId"),
            event_type="permission.grant.revoked",
            payload={"permissionGrantId": grant["id"], "reason": reason},
        )
        event_bus().record_audit(
            project_id=grant["projectId"],
            action="permission.grant.revoke",
            target=grant["id"],
            payload={"reason": reason, "status": grant["status"]},
        )
        return {"permissionGrant": grant}

    @router.post("/api/v1/sandbox/profiles/{profile_id}/revoke", status_code=202)
    async def revoke_sandbox_profile(profile_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        reason = required_reason(await request.json())
        try:
            profile = repository().revoke_sandbox_profile(profile_id, reason=reason)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        event_bus().record_event(
            event_type="sandbox.profile.revoked",
            payload={"sandboxProfileId": profile["id"], "reason": reason},
        )
        event_bus().record_audit(
            action="sandbox.profile.revoke",
            target=profile["id"],
            payload={"reason": reason, "status": profile["status"]},
        )
        return {"sandboxProfile": profile}

    @router.patch("/api/v1/sandbox/profiles/{profile_id}", status_code=202)
    async def update_sandbox_profile(profile_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        reason = required_reason(body)
        patch = validate_sandbox_profile_patch(body)
        try:
            previous = repository().get_sandbox_profile(profile_id)
            profile = repository().update_sandbox_profile(profile_id, patch)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        event_bus().record_event(
            event_type="sandbox.profile.updated",
            payload={"sandboxProfileId": profile["id"], "reason": reason},
        )
        event_bus().record_audit(
            action="sandbox.profile.update",
            target=profile["id"],
            payload={
                "reason": reason,
                "previous": previous,
                "updated": profile,
            },
        )
        return {"sandboxProfile": profile}

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
        event_bus().record_event(
            project_id=body.get("projectId"),
            event_type=f"policy.{decision['decision']}",
            payload={"permissionDecisionId": decision["id"], "riskLevel": decision["riskLevel"]},
        )
        event_bus().record_audit(
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
                event_bus().record_event(
                    project_id=risk["projectId"],
                    event_type="risk.created",
                    payload={"riskId": risk["id"], "sourceType": "policy_decision"},
                )
        return {"decision": decision}

    return router
