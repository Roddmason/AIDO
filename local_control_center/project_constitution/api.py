"""Endpoints HTTP de la constitución del proyecto: lectura agregada y upsert del operador.

La lectura devuelve el documento vigente (o ``null``) con su historial; el upsert exige el token de
escritura, valida vía el repositorio (única puerta de escritura) y confirma documento + versión en
una transacción. No contiene SQL: delega en ``ProjectConstitutionRepository``.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.shared.db import immediate_transaction

from .models import ProjectConstitutionResponse, ProjectConstitutionUpsertRequest
from .repository import ProjectConstitutionRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router de la constitución: GET agregado + PUT versionado protegido por token."""
    router = APIRouter()

    def repository() -> ProjectConstitutionRepository:
        return ProjectConstitutionRepository(platform.connection)

    @router.get(
        "/api/v1/projects/{project_id}/constitution",
        response_model=ProjectConstitutionResponse,
    )
    async def get_project_constitution(project_id: str) -> dict[str, Any]:
        repo = repository()
        constitution = repo.get_for_project(project_id)
        versions = repo.list_versions(constitution["id"]) if constitution else []
        return {"constitution": constitution, "versions": versions}

    @router.put(
        "/api/v1/projects/{project_id}/constitution",
        response_model=ProjectConstitutionResponse,
    )
    async def put_project_constitution(
        project_id: str, body: ProjectConstitutionUpsertRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        repo = repository()
        payload = {
            "projectId": project_id,
            "title": body.title,
            "principles": body.principles,
            "nonNegotiables": body.non_negotiables or [],
            "qualityGates": body.quality_gates or [],
            "source": "operator",
            "enforcement": body.enforcement or "advisory",
            "changeSummary": body.change_summary or "",
            "authoredBy": body.authored_by or "operator",
        }
        try:
            with immediate_transaction(platform.connection):
                constitution = repo.upsert(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"constitution": constitution, "versions": repo.list_versions(constitution["id"])}

    return router
