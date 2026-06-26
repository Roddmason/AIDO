"""Ensambla la aplicacion FastAPI del Local Control Center: routers, middleware y estaticos.

Punto unico de cableado HTTP: inicializa el runtime del control plane, monta los 15 routers
de dominio (jobs, memoria, workflows, seguridad, evidencia, agents, gateway, etc.), instala el
middleware que serializa el acceso al runtime y registra correlacion/telemetria, expone las rutas
de salud/handshake/overview/eventos y, si hay build web, sirve los estaticos con fallback al SPA.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents.api import create_router as create_agents_router
from .agents.cli_session_stream_api import create_router as create_cli_session_stream_router
from .agents.model_gateway_api import create_router as create_model_gateway_router
from .control_plane.models import OverviewResponse
from .control_plane.overview import build_overview_from_connection
from .control_plane.runtime import ControlCenterRuntime
from .credentials.api import create_router as create_credentials_router
from .evidence.api import create_router as create_evidence_router
from .governance.api import create_router as create_governance_router
from .i18n.api import create_router as create_i18n_router
from .integrations.api import create_router as create_integrations_router
from .jobs_approvals.api import create_router as create_jobs_approvals_router
from .memory_retrieval.api import create_router as create_memory_retrieval_router
from .pipelines.api import create_router as create_pipelines_router
from .product_loop.api import create_router as create_product_loop_router
from .projects.api import create_router as create_projects_router
from .prompts.api import create_router as create_prompts_router
from .security_policy.api import create_router as create_security_policy_router
from .sessions_chats.api import create_router as create_sessions_chats_router
from .shared.db import open_sqlite_connection
from .shared.migrations import initialize_platform_schema
from .shared.schemas import HandshakeResponse, HealthResponse, TelemetryStatusResponse
from .shared.telemetry import (
    configure_external_telemetry_from_env,
    elapsed_ms,
    external_telemetry_status,
    monotonic_ms,
    record_http_request,
    resolve_correlation_id,
)
from .settings.api import create_router as create_settings_router
from .team_activity.api import create_router as create_team_activity_router
from .workflows.api import create_router as create_workflows_router
from .workspaces_projects.api import create_router as create_workspaces_router

logger = logging.getLogger(__name__)


def create_app(
    *,
    runtime: Any | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    """Construye y devuelve la app FastAPI cableada con runtime, routers, middleware y estaticos.

    Args:
        runtime: runtime del control plane ya inicializable; si es None crea uno por defecto.
        static_dir: directorio del build web a servir; si no existe, la app queda solo-API.

    El acceso de escritura exige el token de loopback del handshake (`X-Local-Control-Token`).
    """
    platform = runtime or ControlCenterRuntime()
    platform.init()
    platform.ensure_runtime_project()
    configure_external_telemetry_from_env()
    app = FastAPI(title="Local Control Center", version="0.1.0")
    app.state.runtime = platform
    store_request_lock = threading.Lock()

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Sin esto, una excepción no controlada (p. ej. ResponseValidationError por una
        # deriva entre lo persistido y el response_model) devolvía el texto plano
        # "Internal Server Error" de Starlette: sin traza para diagnosticar y, al no ser
        # JSON, el cliente fallaba al parsearla ("Unexpected token 'I'..."). Registramos la
        # traza completa para seguimiento y respondemos un cuerpo JSON estable.
        correlation_id = resolve_correlation_id(request.headers)
        logger.exception(
            "Unhandled error on %s %s (correlation_id=%s): %s",
            request.method,
            request.url.path,
            correlation_id,
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error. Check the control-plane logs for the traceback.",
                "error": type(exc).__name__,
            },
            headers={"X-Correlation-ID": correlation_id},
        )

    @app.middleware("http")
    async def serialize_runtime_access(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        correlation_id = resolve_correlation_id(request.headers)
        started_ms = monotonic_ms()
        await anyio.to_thread.run_sync(store_request_lock.acquire)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            record_http_request(
                platform.connection,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=elapsed_ms(started_ms),
                correlation_id=correlation_id,
            )
            return response
        finally:
            store_request_lock.release()

    def require_write(request: Request) -> None:
        expected = platform.get_handshake()["token"]
        provided = request.headers.get("X-Local-Control-Token")
        if not provided or provided != expected:
            raise HTTPException(status_code=403, detail="A valid loopback write token is required.")

    app.include_router(create_jobs_approvals_router(platform=platform, require_write=require_write))
    app.include_router(create_memory_retrieval_router(platform=platform, require_write=require_write))
    app.include_router(create_workflows_router(platform=platform, require_write=require_write))
    app.include_router(create_security_policy_router(platform=platform, require_write=require_write))
    app.include_router(create_evidence_router(platform=platform, require_write=require_write))
    app.include_router(create_agents_router(platform=platform, require_write=require_write))
    app.include_router(create_cli_session_stream_router(platform=platform, require_write=require_write))
    app.include_router(create_model_gateway_router(platform=platform, require_write=require_write))
    app.include_router(create_workspaces_router(platform=platform, require_write=require_write))
    app.include_router(create_governance_router(platform=platform, require_write=require_write))
    app.include_router(create_sessions_chats_router(platform=platform, require_write=require_write))
    app.include_router(create_pipelines_router(platform=platform, require_write=require_write))
    app.include_router(create_integrations_router(platform=platform, require_write=require_write))
    app.include_router(create_prompts_router(platform=platform, require_write=require_write))
    app.include_router(create_projects_router(platform=platform, require_write=require_write))
    app.include_router(create_product_loop_router(platform=platform, require_write=require_write))
    app.include_router(create_team_activity_router(platform=platform, require_write=require_write))
    app.include_router(create_settings_router(platform=platform, require_write=require_write))
    app.include_router(create_i18n_router(platform=platform, require_write=require_write))
    app.include_router(create_credentials_router(platform=platform, require_write=require_write))

    def snapshot_overview() -> dict[str, Any]:
        connection = open_sqlite_connection(platform.db_path)
        try:
            initialize_platform_schema(connection)
            return build_overview_from_connection(connection=connection, cwd=platform.cwd)
        finally:
            connection.close()

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return {"ok": True}

    @app.get("/api/v1/security/handshake", response_model=HandshakeResponse)
    async def handshake() -> dict[str, Any]:
        return platform.get_handshake()

    @app.get("/api/v1/overview", response_model=OverviewResponse)
    async def overview() -> dict[str, Any]:
        return build_overview_from_connection(connection=platform.connection, cwd=platform.cwd)

    @app.get("/api/v1/telemetry/status", response_model=TelemetryStatusResponse)
    async def telemetry_status() -> dict[str, Any]:
        return {"externalExporter": external_telemetry_status()}

    @app.get("/api/v1/events")
    async def events() -> StreamingResponse:
        payload = json.dumps(snapshot_overview(), ensure_ascii=False)
        return StreamingResponse(
            iter([f"event: snapshot\ndata: {payload}\n\n"]),
            media_type="text/event-stream",
        )

    resolved_static_dir = Path(static_dir).expanduser().resolve() if static_dir is not None else None
    if resolved_static_dir and resolved_static_dir.exists():
        assets_dir = resolved_static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/")
        async def root():
            return FileResponse(resolved_static_dir / "index.html")

        @app.get("/{asset_name}")
        async def static_asset(asset_name: str):
            candidate = resolved_static_dir / asset_name
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(resolved_static_dir / "index.html")

    return app
