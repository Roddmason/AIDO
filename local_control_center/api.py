"""Ensambla la aplicacion FastAPI del Local Control Center: routers, middleware y estaticos.

Punto unico de cableado HTTP: inicializa el runtime del control plane, monta los routers
de dominio (jobs, memoria, workflows, seguridad, evidencia, agents, gateway, etc.), instala el
middleware que aísla la conexión por request y registra correlacion/telemetria, expone las rutas
de salud/handshake/overview/eventos y, si hay build web, sirve los estaticos con fallback al SPA.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents.api import create_router as create_agents_router
from .agents.cli_session_stream_api import create_router as create_cli_session_stream_router
from .agents.model_gateway_api import create_router as create_model_gateway_router
from .agents.provider_catalog_api import create_router as create_provider_catalog_router
from .control_plane.models import OverviewResponse
from .control_plane.overview import build_overview_from_connection
from .control_plane.runtime import ControlCenterRuntime
from .credentials.api import create_router as create_credentials_router
from .decision_engine.api import create_router as create_decision_engine_router
from .evidence.api import create_router as create_evidence_router
from .executions.api import create_router as create_executions_router
from .git_workspace.api import create_router as create_git_workspace_router
from .governance.api import create_router as create_governance_router
from .host_resources.api import create_router as create_host_resources_router
from .i18n.api import create_router as create_i18n_router
from .integrations.api import create_router as create_integrations_router
from .jobs_approvals.api import create_router as create_jobs_approvals_router
from .memory_retrieval.api import create_router as create_memory_retrieval_router
from .nvidia_nim.api import create_router as create_nvidia_nim_router
from .ollama.api import create_router as create_ollama_router
from .pipelines.api import create_router as create_pipelines_router
from .plugins.api import create_router as create_plugins_router
from .process_supervision.api import create_router as create_process_supervision_router
from .product_loop.api import create_router as create_product_loop_router
from .project_constitution.api import create_router as create_project_constitution_router
from .projects.api import create_router as create_projects_router
from .prompts.api import create_router as create_prompts_router
from .remediations.api import create_router as create_remediations_router
from .security_policy.api import create_router as create_security_policy_router
from .self_improvement.api import create_router as create_self_improvement_router
from .sessions_chats.api import create_router as create_sessions_chats_router
from .settings.api import create_router as create_settings_router
from .shared.db import open_sqlite_connection, passive_wal_checkpoint, sqlite_database_diagnostics
from .shared.schemas import (
    HandshakeResponse,
    HealthResponse,
    SqliteCheckpointResponse,
    SqliteDiagnosticsResponse,
    TelemetryStatusResponse,
)
from .shared.telemetry import (
    configure_external_telemetry_from_env,
    elapsed_ms,
    external_telemetry_status,
    monotonic_ms,
    prune_high_volume_events,
    record_http_request,
    resolve_correlation_id,
)
from .team_activity.api import create_router as create_team_activity_router
from .threads.api import create_router as create_threads_router
from .workers.api import create_router as create_workers_router
from .workflows.api import create_router as create_workflows_router
from .workspaces_projects.api import create_router as create_workspaces_router

logger = logging.getLogger(__name__)
HTTP_TELEMETRY_BUSY_TIMEOUT_MS = 75


@contextmanager
def _operation_connection_scope(platform: Any) -> Iterator[Any]:
    """Adapta runtimes productivos y el fixture de composición sin compartir conexión HTTP."""
    runtime_boundary = getattr(platform, "runtime", platform)
    operation_connection = getattr(runtime_boundary, "operation_connection", None)
    if callable(operation_connection):
        with operation_connection() as connection:
            yield connection
        return
    yield platform.connection


def _record_http_request_without_blocking(
    connection: sqlite3.Connection,
    *,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    correlation_id: str,
) -> None:
    """Record HTTP telemetry best-effort without blocking a read behind a busy writer."""
    original_timeout_ms: int | None = None
    try:
        original_timeout_ms = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
        connection.execute(f"PRAGMA busy_timeout = {HTTP_TELEMETRY_BUSY_TIMEOUT_MS}")
        record_http_request(
            connection,
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
        )
    except sqlite3.Error as error:
        from local_control_center.shared.diagnostics import diagnostic_event

        diagnostic_event(
            "sqlite.http_telemetry.error",
            component="http",
            requestId=correlation_id,
            error=error,
            operation="http_telemetry",
            connectionId=f"conn-{id(connection):x}",
        )
        logger.warning(
            "HTTP telemetry write skipped (correlation_id=%s, error=%s).",
            correlation_id,
            type(error).__name__,
        )
    finally:
        if original_timeout_ms is not None:
            try:
                connection.execute(f"PRAGMA busy_timeout = {original_timeout_ms}")
            except sqlite3.Error:
                logger.debug("Could not restore SQLite busy_timeout after HTTP telemetry.")


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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:

            def prune_stale_http_telemetry() -> int:
                with _operation_connection_scope(platform) as connection:
                    return prune_high_volume_events(connection)

            deleted = await anyio.to_thread.run_sync(prune_stale_http_telemetry)
            if deleted:
                logger.info("Pruned %d telemetry.http.request events past retention.", deleted)
        except Exception as error:  # pragma: no cover - la retención nunca debe impedir el arranque
            logger.warning("HTTP telemetry pruning failed at startup: %s", error)
        yield

    app = FastAPI(title="Local Control Center", version="0.1.0", lifespan=lifespan)
    app.state.runtime = platform

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Prevent visual request bodies, especially prompts, from being echoed in 422 responses."""
        path = request.url.path
        is_visual_execution = path.startswith("/api/v1/model-gateway/providers/") and path.endswith(
            ("/images/generations", "/images/edits")
        )
        if not is_visual_execution:
            return await request_validation_exception_handler(request, exc)
        safe_errors = [
            {
                "loc": list(error.get("loc") or []),
                "msg": str(error.get("msg") or "Invalid request value."),
                "type": str(error.get("type") or "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        from local_control_center.shared.diagnostics import diagnostic_event

        correlation_id = getattr(request.state, "correlation_id", None) or resolve_correlation_id(
            request.headers
        )
        diagnostic_event("http.error", component="http", requestId=correlation_id, error=exc, level="ERROR")
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error. Check the control-plane logs for the traceback.",
                "error": type(exc).__name__,
            },
            headers={"X-Correlation-ID": correlation_id},
        )

    @app.middleware("http")
    async def bind_request_database_connection(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        correlation_id = resolve_correlation_id(request.headers)
        request.state.correlation_id = correlation_id
        started_ms = monotonic_ms()
        from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
        from local_control_center.shared.diagnostics import diagnostic_event

        with (
            _operation_connection_scope(platform) as connection,
            execution_scope(
                ProcessExecutionContext(
                    db_path=platform.db_path, connection=connection, request_id=correlation_id
                )
            ),
        ):
            try:
                response = await call_next(request)
            except Exception:
                _record_http_request_without_blocking(
                    connection,
                    method=request.method,
                    path=request.url.path,
                    status_code=500,
                    duration_ms=elapsed_ms(started_ms),
                    correlation_id=correlation_id,
                )
                raise
            response.headers["X-Correlation-ID"] = correlation_id
            diagnostic_event(
                "http.response",
                component="http",
                statusCode=response.status_code,
                durationMs=elapsed_ms(started_ms),
                method=request.method,
                outcome="http_response",
            )
            _record_http_request_without_blocking(
                connection,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=elapsed_ms(started_ms),
                correlation_id=correlation_id,
            )
            return response

    def require_write(request: Request) -> None:
        expected = platform.get_handshake()["token"]
        provided = request.headers.get("X-Local-Control-Token")
        if not provided or not secrets.compare_digest(str(provided), str(expected)):
            raise HTTPException(status_code=403, detail="A valid loopback write token is required.")

    app.include_router(create_jobs_approvals_router(platform=platform, require_write=require_write))
    app.include_router(create_memory_retrieval_router(platform=platform, require_write=require_write))
    app.include_router(create_workflows_router(platform=platform, require_write=require_write))
    app.include_router(create_security_policy_router(platform=platform, require_write=require_write))
    app.include_router(create_evidence_router(platform=platform, require_write=require_write))
    app.include_router(create_agents_router(platform=platform, require_write=require_write))
    app.include_router(create_cli_session_stream_router(platform=platform, require_write=require_write))
    app.include_router(create_model_gateway_router(platform=platform, require_write=require_write))
    app.include_router(create_provider_catalog_router(platform=platform, require_write=require_write))
    app.include_router(create_nvidia_nim_router())
    app.include_router(create_ollama_router(platform=platform, require_write=require_write))
    app.include_router(create_workspaces_router(platform=platform, require_write=require_write))
    app.include_router(create_governance_router(platform=platform, require_write=require_write))
    app.include_router(create_host_resources_router(platform=platform, require_write=require_write))
    app.include_router(create_process_supervision_router(platform=platform, require_write=require_write))
    app.include_router(create_executions_router(platform=platform, require_write=require_write))
    app.include_router(create_sessions_chats_router(platform=platform, require_write=require_write))
    app.include_router(create_pipelines_router(platform=platform, require_write=require_write))
    app.include_router(create_plugins_router(platform=platform, require_write=require_write))
    app.include_router(create_integrations_router(platform=platform, require_write=require_write))
    app.include_router(create_prompts_router(platform=platform, require_write=require_write))
    app.include_router(create_projects_router(platform=platform, require_write=require_write))
    app.include_router(create_git_workspace_router(platform=platform, require_write=require_write))
    app.include_router(create_product_loop_router(platform=platform, require_write=require_write))
    app.include_router(create_project_constitution_router(platform=platform, require_write=require_write))
    app.include_router(create_self_improvement_router(platform=platform, require_write=require_write))
    app.include_router(create_team_activity_router(platform=platform, require_write=require_write))
    app.include_router(create_remediations_router(platform=platform, require_write=require_write))
    app.include_router(create_threads_router(platform=platform, require_write=require_write))
    app.include_router(create_workers_router(platform=platform, require_write=require_write))
    app.include_router(create_settings_router(platform=platform, require_write=require_write))
    app.include_router(create_decision_engine_router(platform=platform))
    app.include_router(create_i18n_router(platform=platform, require_write=require_write))
    app.include_router(create_credentials_router(platform=platform, require_write=require_write))

    def snapshot_overview() -> dict[str, Any]:
        connection = open_sqlite_connection(platform.db_path)
        try:
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
        from local_control_center.shared.diagnostics import ensure_diagnostics

        return {"externalExporter": external_telemetry_status(), "diagnostics": ensure_diagnostics().status()}

    @app.post("/api/v1/telemetry/ui-error")
    async def ui_error(request: Request) -> dict[str, str]:
        """Recibe sólo una señal fija: ni mensaje de excepción, ni formulario, ni stack de UI."""
        require_write(request)
        from local_control_center.shared.diagnostics import diagnostic_event

        diagnostic_event("ui.render.error", component="ui", level="ERROR")
        return {"requestId": request.state.correlation_id}

    @app.get("/api/v1/operations/database", response_model=SqliteDiagnosticsResponse)
    async def database_status() -> dict[str, Any]:
        return sqlite_database_diagnostics(platform.connection, db_path=platform.db_path)

    @app.post(
        "/api/v1/operations/database/checkpoint",
        response_model=SqliteCheckpointResponse,
    )
    async def checkpoint_database(request: Request) -> dict[str, Any]:
        require_write(request)
        return passive_wal_checkpoint(platform.connection, db_path=platform.db_path)

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

    from local_control_center.executions.openapi import install_execution_openapi

    install_execution_openapi(app)
    return app
