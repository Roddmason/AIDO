"""Expone los endpoints HTTP del streaming de sesiones CLI sobre FastAPI.

Una mutación arranca la sesión (corre el comando en un hilo de fondo y emite los nueve eventos en vivo),
una lectura incremental devuelve los eventos nuevos desde un ``seq`` dado para que la UI muestre actividad
real sin spinner indefinido, y una mutación la cancela. Arranque y cancelación exigen el token de
escritura; la lectura es libre. No contiene lógica de negocio: delega en el ejecutor y el recorder.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .cli_session_events import CliSessionEventStore
from .cli_session_stream import cancel_cli_session, is_running, start_cli_session
from .cli_session_stream_models import (
    CliSessionCancelResponse,
    CliSessionEventsResponse,
    CliSessionStartRequest,
    CliSessionStartResponse,
)


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router de streaming de sesiones CLI: arrancar (token), leer eventos incrementales y cancelar (token)."""
    router = APIRouter()

    @router.post("/api/v1/cli-sessions", status_code=202, response_model=CliSessionStartResponse)
    async def start_session(body: CliSessionStartRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        argv = list(body.argv)
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise HTTPException(status_code=422, detail="argv must be a non-empty list of non-empty strings.")
        workspace = platform.connection.execute(
            "SELECT project_id, path FROM workspaces WHERE id = ?", (body.workspace_id,)
        ).fetchone()
        if not workspace:
            raise HTTPException(status_code=404, detail=f"Workspace not found: {body.workspace_id}")
        return start_cli_session(
            platform.connection,
            db_path=platform.db_path,
            project_id=str(workspace["project_id"]),
            workspace_id=body.workspace_id,
            workspace_path=str(workspace["path"]),
            runtime=body.runtime or argv[0],
            executable=argv[0],
            argv=argv,
            env_policy=body.env_policy,
            agent_id=body.agent_id,
        )

    @router.get("/api/v1/cli-sessions/{session_id}/events", response_model=CliSessionEventsResponse)
    async def session_events(session_id: str, afterSeq: int = 0) -> dict[str, Any]:
        store = CliSessionEventStore(platform.connection)
        return {
            "events": store.list_events(session_id, after_seq=afterSeq),
            "latestSeq": store.latest_seq(session_id),
            "running": is_running(session_id),
        }

    @router.post("/api/v1/cli-sessions/{session_id}/cancel", response_model=CliSessionCancelResponse)
    async def cancel_session(session_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        return {"cancelled": cancel_cli_session(session_id)}

    return router
