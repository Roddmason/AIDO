from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents.api import create_router as create_agents_router
from .evidence.api import create_router as create_evidence_router
from .governance.api import create_router as create_governance_router
from .jobs_approvals.api import create_router as create_jobs_approvals_router
from .legacy_compat.api import create_router as create_legacy_compat_router
from .memory_retrieval.api import create_router as create_memory_retrieval_router
from .security_policy.api import create_router as create_security_policy_router
from .store import PlatformStore
from .workspaces_projects.api import create_router as create_workspaces_router
from .workflows.api import create_router as create_workflows_router


def create_app(*, store: PlatformStore | None = None, static_dir: str | Path | None = None) -> FastAPI:
    platform = store or PlatformStore()
    platform.init()
    platform.ensure_runtime_project()
    app = FastAPI(title="Local Control Center", version="0.1.0")
    app.state.store = platform
    store_request_lock = threading.Lock()

    @app.middleware("http")
    async def serialize_platform_store_access(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        await anyio.to_thread.run_sync(store_request_lock.acquire)
        try:
            return await call_next(request)
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
    app.include_router(create_workspaces_router(platform=platform, require_write=require_write))
    app.include_router(create_governance_router(platform=platform, require_write=require_write))
    app.include_router(create_legacy_compat_router(platform=platform, require_write=require_write))

    def snapshot_overview() -> dict[str, Any]:
        snapshot_store = PlatformStore(cwd=platform.cwd, db_path=platform.db_path)
        snapshot_store.init()
        try:
            return snapshot_store.get_overview()
        finally:
            snapshot_store.close()

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/security/handshake")
    async def handshake() -> dict[str, Any]:
        return platform.get_handshake()

    @app.get("/api/v1/overview")
    async def overview() -> dict[str, Any]:
        return platform.get_overview()

    @app.get("/api/v1/events")
    async def events() -> StreamingResponse:
        payload = json.dumps(snapshot_overview(), ensure_ascii=False)
        return StreamingResponse(
            iter([f"event: snapshot\ndata: {payload}\n\n"]),
            media_type="text/event-stream",
        )

    @app.get("/api/v1/project-templates")
    async def project_templates() -> dict[str, Any]:
        return {"projectTemplates": platform.list_project_templates()}

    @app.get("/api/v1/projects")
    async def projects() -> dict[str, Any]:
        return {"projects": platform.list_projects()}

    @app.post("/api/v1/projects", status_code=201)
    async def create_project(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        project = platform.create_project(
            name=body.get("name") or Path(body.get("path") or platform.cwd).name or "Project",
            path=body.get("path") or platform.cwd,
            template_id=body.get("templateId") or "other",
            create_directory=body.get("createDirectory", True),
            source="api",
        )
        return {"project": project, "auditEvent": platform.record_audit(project_id=project["id"], action="project.create", target=project["id"], payload=body)}

    @app.get("/api/v1/providers")
    async def providers() -> dict[str, Any]:
        return {"providers": platform.list_providers()}

    @app.get("/api/v1/teams")
    async def teams() -> dict[str, Any]:
        return {"teams": platform.list_teams(), "agents": platform.list_agents()}

    @app.get("/api/v1/agents")
    async def agents() -> dict[str, Any]:
        return {"agents": platform.list_agents()}

    @app.get("/api/v1/prompts")
    async def list_prompts() -> dict[str, Any]:
        return {"promptTemplates": platform.list_prompt_templates()}

    @app.post("/api/v1/prompts", status_code=201)
    async def upsert_prompt(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        return {
            "promptTemplate": platform.upsert_prompt_template(
                prompt_id=body.get("id"),
                project_id=body["projectId"],
                name=body["name"],
                body=body["body"],
                mode=body.get("mode", "manual"),
                optimizer=body.get("optimizer", ""),
                applies_to=body.get("appliesTo") or {},
            )
        }

    @app.get("/api/v1/ide-connections")
    async def ide_connections() -> dict[str, list[Any]]:
        return {"ideConnections": platform.list_ide_connections()}

    @app.post("/api/v1/ide-connections", status_code=201)
    async def upsert_ide_connection(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        project = platform.get_project(body["projectId"])
        connection = platform.upsert_ide_connection(
            project_id=project["id"],
            editor=body.get("editor", "unknown"),
            workspace_root=body.get("workspaceRoot") or body.get("workspace_root") or project["path"],
            status=body.get("status", "connected"),
            open_files=body.get("openFiles") or [],
            diagnostics=body.get("diagnostics") or [],
            selection=body.get("selection") or {},
            terminal_context=body.get("terminalContext") or {},
        )
        return {"ideConnection": connection}

    @app.get("/api/v1/open-design")
    async def open_design() -> dict[str, Any]:
        return {"status": "python-backend", "backend": "fastapi", "runtime": "windows-native"}

    resolved_static_dir = Path(static_dir) if static_dir is not None else None
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
