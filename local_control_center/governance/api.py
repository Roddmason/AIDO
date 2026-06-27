"""Rutas HTTP de gobernanza: ADRs, riesgos y next steps sobre FastAPI.

Valida los enums de cada recurso, aplica las reglas de negocio (ADR aceptada exige
contexto y decisión; riesgo high/critical exige mitigación) y delega la persistencia
en `GovernanceRepository`. Cada escritura emite auditoría y/o evento de dominio vía
`EventBus`. Las mutaciones quedan tras `require_write` (control de escritura del caller).

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..shared.event_bus import EventBus
from .models import (
    ArchitectureDecisionCreateRequest,
    ArchitectureDecisionResponse,
    ArchitectureDecisionsListResponse,
    GovernanceResponse,
    NextStepCreateRequest,
    NextStepResponse,
    NextStepsListResponse,
    NextStepUpdateRequest,
    RiskCreateRequest,
    RiskResponse,
    RisksListResponse,
    RiskUpdateRequest,
)
from .repository import GovernanceRepository

HIGH_RISK_SEVERITIES = {"high", "critical"}
ALLOWED_DECISION_STATUSES = {"proposed", "accepted", "rejected", "superseded", "deprecated"}
ALLOWED_RISK_SEVERITIES = {"low", "medium", "high", "critical"}
ALLOWED_RISK_STATUSES = {"open", "monitoring", "mitigating", "mitigated", "accepted", "closed"}
ALLOWED_NEXT_STEP_PRIORITIES = {"low", "medium", "high", "urgent"}
ALLOWED_NEXT_STEP_STATUSES = {"planned", "in_progress", "blocked", "completed", "cancelled"}


def validate_choice(field: str, value: str | None, allowed: set[str]) -> str:
    """Normaliza a minúsculas y exige que el valor pertenezca al enum.

    Raises:
        HTTPException: 422 si el valor (o vacío) no está en ``allowed``.
    """
    normalized = (value or "").lower()
    if normalized not in allowed:
        raise HTTPException(status_code=422, detail=f"{field} must be one of: {', '.join(sorted(allowed))}.")
    return normalized


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Construye el router de gobernanza ligado a la conexión y al guard de escritura."""
    router = APIRouter()

    def repository() -> GovernanceRepository:
        return GovernanceRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/governance", response_model=GovernanceResponse)
    async def governance() -> dict[str, Any]:
        """Devuelve la vista agregada de ADRs, riesgos y next steps."""
        repo = repository()
        return {
            "architectureDecisions": repo.list_architecture_decisions(),
            "risks": repo.list_risks(),
            "nextSteps": repo.list_next_steps(),
        }

    @router.get("/api/v1/architecture-decisions", response_model=ArchitectureDecisionsListResponse)
    async def list_architecture_decisions() -> dict[str, Any]:
        """Lista las decisiones de arquitectura registradas."""
        return {"architectureDecisions": repository().list_architecture_decisions()}

    @router.post(
        "/api/v1/architecture-decisions", status_code=201, response_model=ArchitectureDecisionResponse
    )
    async def create_architecture_decision(
        body: ArchitectureDecisionCreateRequest, request: Request
    ) -> ArchitectureDecisionResponse:
        """Crea una ADR; una aceptada exige contexto y decisión. Audita y emite evento."""
        require_write(request)
        payload = body.model_dump(by_alias=True, exclude_none=True)
        payload["status"] = validate_choice(
            "status", payload.get("status", "proposed"), ALLOWED_DECISION_STATUSES
        )
        if payload.get("status") == "accepted" and not (payload.get("context") and payload.get("decision")):
            raise HTTPException(
                status_code=422, detail="Accepted decisions require context and decision text."
            )
        decision = repository().create_architecture_decision(payload)
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
        return ArchitectureDecisionResponse(architectureDecision=decision)

    @router.get("/api/v1/risks", response_model=RisksListResponse)
    async def list_risks() -> dict[str, Any]:
        """Lista los riesgos del registro (ordenados por severidad)."""
        return {"risks": repository().list_risks()}

    @router.post("/api/v1/risks", status_code=201, response_model=RiskResponse)
    async def create_risk(body: RiskCreateRequest, request: Request) -> RiskResponse:
        """Crea un riesgo; los high/critical exigen mitigación. Audita y emite evento."""
        require_write(request)
        payload = body.model_dump(by_alias=True, exclude_none=True)
        payload["severity"] = validate_choice(
            "severity", payload.get("severity", "medium"), ALLOWED_RISK_SEVERITIES
        )
        payload["status"] = validate_choice("status", payload.get("status", "open"), ALLOWED_RISK_STATUSES)
        if payload["severity"] in HIGH_RISK_SEVERITIES and not payload.get("mitigation"):
            raise HTTPException(status_code=422, detail="High and critical risks require a mitigation.")
        risk = repository().create_risk(payload)
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
        return RiskResponse(risk=risk)

    @router.patch("/api/v1/risks/{risk_id}", status_code=202, response_model=RiskResponse)
    async def update_risk(risk_id: str, body: RiskUpdateRequest, request: Request) -> RiskResponse:
        """Actualiza parcialmente un riesgo (404 si no existe) y registra auditoría."""
        require_write(request)
        payload = body.model_dump(by_alias=True, exclude_none=True)
        if "severity" in payload:
            payload["severity"] = validate_choice(
                "severity", payload.get("severity"), ALLOWED_RISK_SEVERITIES
            )
        if "status" in payload:
            payload["status"] = validate_choice("status", payload.get("status"), ALLOWED_RISK_STATUSES)
        try:
            risk = repository().update_risk(risk_id, payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        event_bus().record_audit(
            project_id=risk["projectId"],
            action="risk.update",
            target=risk["id"],
            payload={"status": risk["status"]},
        )
        return RiskResponse(risk=risk)

    @router.get("/api/v1/next-steps", response_model=NextStepsListResponse)
    async def list_next_steps() -> dict[str, Any]:
        """Lista los next steps planificados."""
        return {"nextSteps": repository().list_next_steps()}

    @router.post("/api/v1/next-steps", status_code=201, response_model=NextStepResponse)
    async def create_next_step(body: NextStepCreateRequest, request: Request) -> NextStepResponse:
        """Crea un next step con prioridad/status validados y registra auditoría."""
        require_write(request)
        payload = body.model_dump(by_alias=True, exclude_none=True)
        payload["priority"] = validate_choice(
            "priority", payload.get("priority", "medium"), ALLOWED_NEXT_STEP_PRIORITIES
        )
        payload["status"] = validate_choice(
            "status", payload.get("status", "planned"), ALLOWED_NEXT_STEP_STATUSES
        )
        next_step = repository().create_next_step(payload)
        event_bus().record_audit(
            project_id=next_step["projectId"],
            action="next_step.create",
            target=next_step["id"],
            payload={"priority": next_step["priority"], "status": next_step["status"]},
        )
        return NextStepResponse(nextStep=next_step)

    @router.patch("/api/v1/next-steps/{step_id}", status_code=202, response_model=NextStepResponse)
    async def update_next_step(
        step_id: str, body: NextStepUpdateRequest, request: Request
    ) -> NextStepResponse:
        """Actualiza parcialmente un next step (404 si no existe) y registra auditoría."""
        require_write(request)
        payload = body.model_dump(by_alias=True, exclude_none=True)
        if "priority" in payload:
            payload["priority"] = validate_choice(
                "priority", payload.get("priority"), ALLOWED_NEXT_STEP_PRIORITIES
            )
        if "status" in payload:
            payload["status"] = validate_choice("status", payload.get("status"), ALLOWED_NEXT_STEP_STATUSES)
        try:
            next_step = repository().update_next_step(step_id, payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        event_bus().record_audit(
            project_id=next_step["projectId"],
            action="next_step.update",
            target=next_step["id"],
            payload={"status": next_step["status"]},
        )
        return NextStepResponse(nextStep=next_step)

    return router
