from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..shared.event_bus import EventBus
from .repository import GovernanceRepository


HIGH_RISK_SEVERITIES = {"high", "critical"}
ALLOWED_DECISION_STATUSES = {"proposed", "accepted", "rejected", "superseded", "deprecated"}
ALLOWED_RISK_SEVERITIES = {"low", "medium", "high", "critical"}
ALLOWED_RISK_STATUSES = {"open", "monitoring", "mitigating", "mitigated", "accepted", "closed"}
ALLOWED_NEXT_STEP_PRIORITIES = {"low", "medium", "high", "urgent"}
ALLOWED_NEXT_STEP_STATUSES = {"planned", "in_progress", "blocked", "completed", "cancelled"}


def validate_choice(field: str, value: str | None, allowed: set[str]) -> str:
    normalized = (value or "").lower()
    if normalized not in allowed:
        raise HTTPException(status_code=422, detail=f"{field} must be one of: {', '.join(sorted(allowed))}.")
    return normalized


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> GovernanceRepository:
        return GovernanceRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/governance")
    async def governance() -> dict[str, Any]:
        repo = repository()
        return {
            "architectureDecisions": repo.list_architecture_decisions(),
            "risks": repo.list_risks(),
            "nextSteps": repo.list_next_steps(),
        }

    @router.get("/api/v1/architecture-decisions")
    async def list_architecture_decisions() -> dict[str, Any]:
        return {"architectureDecisions": repository().list_architecture_decisions()}

    @router.post("/api/v1/architecture-decisions", status_code=201)
    async def create_architecture_decision(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        body["status"] = validate_choice("status", body.get("status", "proposed"), ALLOWED_DECISION_STATUSES)
        if body.get("status") == "accepted" and not (body.get("context") and body.get("decision")):
            raise HTTPException(status_code=422, detail="Accepted decisions require context and decision text.")
        decision = repository().create_architecture_decision(body)
        event_bus().record_audit(
            project_id=decision["projectId"],
            action="architecture_decision.create",
            target=decision["id"],
            payload={"status": decision["status"]},
        )
        event_bus().record_event(
            project_id=decision["projectId"],
            event_type="architecture_decision.created",
            payload={"architectureDecisionId": decision["id"], "status": decision["status"]},
        )
        return {"architectureDecision": decision}

    @router.get("/api/v1/risks")
    async def list_risks() -> dict[str, Any]:
        return {"risks": repository().list_risks()}

    @router.post("/api/v1/risks", status_code=201)
    async def create_risk(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        body["severity"] = validate_choice("severity", body.get("severity", "medium"), ALLOWED_RISK_SEVERITIES)
        body["status"] = validate_choice("status", body.get("status", "open"), ALLOWED_RISK_STATUSES)
        if body["severity"] in HIGH_RISK_SEVERITIES and not body.get("mitigation"):
            raise HTTPException(status_code=422, detail="High and critical risks require a mitigation.")
        risk = repository().create_risk(body)
        event_bus().record_audit(
            project_id=risk["projectId"],
            action="risk.create",
            target=risk["id"],
            payload={"severity": risk["severity"], "status": risk["status"]},
        )
        event_bus().record_event(
            project_id=risk["projectId"],
            event_type="risk.created",
            payload={"riskId": risk["id"], "severity": risk["severity"]},
        )
        return {"risk": risk}

    @router.patch("/api/v1/risks/{risk_id}", status_code=202)
    async def update_risk(risk_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        if "severity" in body:
            body["severity"] = validate_choice("severity", body.get("severity"), ALLOWED_RISK_SEVERITIES)
        if "status" in body:
            body["status"] = validate_choice("status", body.get("status"), ALLOWED_RISK_STATUSES)
        try:
            risk = repository().update_risk(risk_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        event_bus().record_audit(
            project_id=risk["projectId"],
            action="risk.update",
            target=risk["id"],
            payload={"status": risk["status"]},
        )
        return {"risk": risk}

    @router.get("/api/v1/next-steps")
    async def list_next_steps() -> dict[str, Any]:
        return {"nextSteps": repository().list_next_steps()}

    @router.post("/api/v1/next-steps", status_code=201)
    async def create_next_step(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        body["priority"] = validate_choice("priority", body.get("priority", "medium"), ALLOWED_NEXT_STEP_PRIORITIES)
        body["status"] = validate_choice("status", body.get("status", "planned"), ALLOWED_NEXT_STEP_STATUSES)
        next_step = repository().create_next_step(body)
        event_bus().record_audit(
            project_id=next_step["projectId"],
            action="next_step.create",
            target=next_step["id"],
            payload={"priority": next_step["priority"], "status": next_step["status"]},
        )
        return {"nextStep": next_step}

    @router.patch("/api/v1/next-steps/{step_id}", status_code=202)
    async def update_next_step(step_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        if "priority" in body:
            body["priority"] = validate_choice("priority", body.get("priority"), ALLOWED_NEXT_STEP_PRIORITIES)
        if "status" in body:
            body["status"] = validate_choice("status", body.get("status"), ALLOWED_NEXT_STEP_STATUSES)
        try:
            next_step = repository().update_next_step(step_id, body)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        event_bus().record_audit(
            project_id=next_step["projectId"],
            action="next_step.update",
            target=next_step["id"],
            payload={"status": next_step["status"]},
        )
        return {"nextStep": next_step}

    return router
