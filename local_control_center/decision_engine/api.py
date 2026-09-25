"""Lecturas seguras de estado, evidencia y comparación offline del motor shadow.

La configuración usa el API de Settings existente y su token; ninguna ruta aquí
dispara inferencia, modifica outcomes o promociona recomendaciones.
@author Rodrigo Mason
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Query

from .config import real_jev_calls_enabled, resolve_config
from .models import DecisionEngineStatusResponse, DecisionListResponse, DecisionReportResponse
from .reporting import decision_report
from .repository import DecisionRepository


def create_router(*, platform: Any) -> APIRouter:
    """Expone únicamente lecturas locales usando la conexión de operación de AIDO."""
    router = APIRouter()

    @router.get(
        "/api/v1/decision-engine",
        response_model=DecisionEngineStatusResponse,
        response_model_exclude_unset=True,
    )
    async def status(projectId: str | None = None) -> dict:
        """Separa configuración efectiva del estado observado sin revelar credenciales."""
        try:
            config = resolve_config(platform.connection, projectId)
        except ValueError:
            return {
                "status": "unavailable",
                "reasonCode": "configuration_invalid",
                "enabled": False,
                "mode": "shadow",
            }
        health = DecisionRepository(platform.connection).health(config.configuration_fingerprint)
        credential_configured = bool(os.environ.get(config.api_key_reference.removeprefix("env:")))
        if not config.active:
            state = "disabled"
        elif (
            config.provider == "jev"
            and (not config.jev_enabled or not real_jev_calls_enabled() or not credential_configured)
        ) or health["open_until"] > time.time():
            state = "unavailable"
        elif health["failures"]:
            state = "degraded"
        else:
            observed = platform.connection.execute(
                """SELECT 1 FROM decision_receipts WHERE configuration_fingerprint=? AND status='completed'
                AND json_extract(payload, '$.recommendation') IS NOT NULL LIMIT 1""",
                (config.configuration_fingerprint,),
            ).fetchone()
            state = "healthy" if observed else "unknown"
        return {
            "enabled": config.active,
            "provider": config.provider,
            "mode": config.mode,
            "model": config.model,
            "version": config.version,
            "status": state,
            "configurationFingerprint": config.configuration_fingerprint,
            "confidenceThreshold": config.confidence_threshold,
            "marginThreshold": config.margin_threshold,
            "consecutiveProviderFailures": health["failures"],
            "credentialConfigured": credential_configured,
            "realCallsEnabled": real_jev_calls_enabled(),
        }

    @router.get("/api/v1/decision-engine/decisions", response_model=DecisionListResponse)
    async def decisions(
        projectId: str | None = None,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict:
        """Lee una página estable de receipts con outcomes separados y valores desconocidos."""
        items = DecisionRepository(platform.connection).list_receipts(
            project_id=projectId, after=after, limit=limit
        )
        return {"items": items, "nextAfter": items[-1]["sequence"] if items else after}

    @router.get("/api/v1/decision-engine/report", response_model=DecisionReportResponse)
    async def report(
        projectId: str | None = None,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=1000, ge=1, le=1000),
    ) -> dict:
        """Compara decisiones y outcomes sin inferir exactitud o victoria contrafactual."""
        return decision_report(platform.connection, project_id=projectId, after=after, limit=limit)

    return router
