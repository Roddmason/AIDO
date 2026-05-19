from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .jobs_approvals.api import create_router as create_jobs_approvals_router
from .memory_retrieval.api import create_router as create_memory_retrieval_router
from .store import PlatformStore, json_dumps, utc_now


def default_config_catalog() -> dict[str, Any]:
    return {
        "pipelinePolicy": {"mode": "auto", "runQaAndPentestInParallel": False},
        "gitPolicy": {"mode": "local-only", "ticketPrefix": "PIPE", "baseBranch": ""},
    }


def default_workspace_state(workspace_path: Path) -> dict[str, Any]:
    return {
        "version": 4,
        "activeTeamId": None,
        "activeSessionId": None,
        "activeChatId": None,
        "activePipelineId": None,
        "activeWorkspacePath": str(workspace_path),
        "teams": [],
        "sessions": [],
        "chats": [],
        "promptPacks": [],
        "pipelines": [],
        "configCatalog": default_config_catalog(),
        "memoryByTeamId": {},
        "dashboard": {"collapsedWorkspaceIds": [], "pinnedWorkspaceIds": []},
    }


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

    def load_state() -> dict[str, Any]:
        platform.ensure_runtime_project()
        try:
            loaded = platform.load_workspace_state(platform.cwd)
            state = loaded["state"]
        except Exception:
            state = default_workspace_state(platform.cwd)
            platform.save_workspace_state(platform.cwd, state, source="python-runtime")
        state.setdefault("version", 4)
        state.setdefault("activeWorkspacePath", str(platform.cwd))
        state.setdefault("sessions", [])
        state.setdefault("chats", [])
        state.setdefault("promptPacks", [])
        state.setdefault("pipelines", [])
        state.setdefault("teams", [])
        state.setdefault("configCatalog", default_config_catalog())
        state.setdefault("dashboard", {"collapsedWorkspaceIds": [], "pinnedWorkspaceIds": []})
        state["configCatalog"].setdefault("pipelinePolicy", default_config_catalog()["pipelinePolicy"])
        state["configCatalog"].setdefault("gitPolicy", default_config_catalog()["gitPolicy"])
        return state

    def save_state(state: dict[str, Any]) -> dict[str, Any]:
        state["activeWorkspacePath"] = str(platform.cwd)
        platform.save_workspace_state(platform.cwd, state, source="python-runtime")
        return state

    def snapshot_overview() -> dict[str, Any]:
        snapshot_store = PlatformStore(cwd=platform.cwd, db_path=platform.db_path)
        snapshot_store.init()
        try:
            return snapshot_store.get_overview()
        finally:
            snapshot_store.close()

    def find_by_id(items: list[dict[str, Any]], item_id: str | None) -> dict[str, Any] | None:
        if not item_id:
            return None
        return next((item for item in items if item.get("id") == item_id), None)

    def view_model(state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or load_state()
        active_session = find_by_id(state["sessions"], state.get("activeSessionId"))
        active_chat = find_by_id(state["chats"], state.get("activeChatId"))
        active_pipeline = find_by_id(state["pipelines"], state.get("activePipelineId"))
        active_team = find_by_id(state["teams"], state.get("activeTeamId"))
        return {
            "state": state,
            "activeTeam": active_team,
            "activeSession": active_session,
            "activeChat": active_chat,
            "activePipeline": active_pipeline,
            "connectors": [],
            "extensions": {"plugins": [], "skills": []},
            "dashboard": {"backend": "python"},
        }

    def ensure_session(state: dict[str, Any], name: str = "Main") -> dict[str, Any]:
        active = find_by_id(state["sessions"], state.get("activeSessionId"))
        if active:
            return active
        session = {
            "id": f"session-{uuid.uuid4()}",
            "name": name,
            "teamId": state.get("activeTeamId"),
            "pinned": False,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        state["sessions"].append(session)
        state["activeSessionId"] = session["id"]
        return session

    def persist_session_table(project_id: str, session: dict[str, Any], status: str = "active") -> None:
        timestamp = utc_now()
        platform.connection.execute(
            """
            INSERT INTO sessions
                (id, project_id, team_id, name, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                team_id = excluded.team_id,
                name = excluded.name,
                status = excluded.status,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                session["id"],
                project_id,
                session.get("teamId"),
                session.get("name") or "Main",
                status,
                json_dumps({"pinned": session.get("pinned", False)}),
                session.get("createdAt") or timestamp,
                timestamp,
            ),
        )

    def current_project() -> dict[str, Any]:
        return platform.ensure_runtime_project()

    def find_project_or_404(ref: str) -> dict[str, Any]:
        project = platform.find_project(ref)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {ref}")
        return project

    def create_chat_record(state: dict[str, Any], prompt: str, mode: str = "auto") -> dict[str, Any]:
        session = ensure_session(state)
        chat = {
            "id": f"chat-{uuid.uuid4()}",
            "teamId": state.get("activeTeamId"),
            "sessionId": session["id"],
            "title": prompt[:80] or "Chat",
            "mode": mode,
            "runs": [
                {
                    "id": f"run-{uuid.uuid4()}",
                    "stage": "route",
                    "status": "completed",
                    "prompt": prompt,
                    "output": "RESULT: OK\nPrompt routed by Python backend.",
                    "createdAt": utc_now(),
                    "updatedAt": utc_now(),
                }
            ],
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        state["chats"].append(chat)
        state["activeChatId"] = chat["id"]
        session["activeChatId"] = chat["id"]
        session["updatedAt"] = utc_now()
        return chat

    def create_pipeline_record(state: dict[str, Any], idea: str) -> dict[str, Any]:
        session = ensure_session(state)
        chat = create_chat_record(state, idea, "pipeline")
        ticket = f"PIPE-{len(state['pipelines']) + 1:04d}"
        prompt_pack = {
            "id": f"pack-{uuid.uuid4()}",
            "sessionId": session["id"],
            "ticketId": ticket,
            "title": idea[:80] or "Pipeline",
            "researchBrief": "",
            "modules": [],
            "createdAt": utc_now(),
        }
        pipeline = {
            "id": f"pipeline-{uuid.uuid4()}",
            "sessionId": session["id"],
            "teamId": state.get("activeTeamId"),
            "chatId": chat["id"],
            "promptPackId": prompt_pack["id"],
            "ticketId": ticket,
            "name": idea[:80] or "Pipeline",
            "status": "queued",
            "modules": [
                {
                    "id": f"module-{uuid.uuid4()}",
                    "name": "Implementation",
                    "kind": "code",
                    "status": "pending",
                    "assignedMemberId": None,
                    "stages": {
                        "analyze": {"id": f"stage-{uuid.uuid4()}", "stage": "analyze", "status": "pending", "gate": "pending"},
                        "develop": {"id": f"stage-{uuid.uuid4()}", "stage": "develop", "status": "pending", "gate": "pending"},
                        "revalidate": {"id": f"stage-{uuid.uuid4()}", "stage": "revalidate", "status": "pending", "gate": "pending"},
                        "qa": {"id": f"stage-{uuid.uuid4()}", "stage": "qa", "status": "pending", "gate": "pending"},
                        "pentest": {"id": f"stage-{uuid.uuid4()}", "stage": "pentest", "status": "pending", "gate": "pending"},
                        "review": {"id": f"stage-{uuid.uuid4()}", "stage": "review", "status": "pending", "gate": "pending"},
                    },
                    "corrections": [],
                }
            ],
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        state["promptPacks"].append(prompt_pack)
        state["pipelines"].append(pipeline)
        state["activePipelineId"] = pipeline["id"]
        session["activePipelineId"] = pipeline["id"]
        session["updatedAt"] = utc_now()
        return pipeline

    def update_pipeline_stage(pipeline: dict[str, Any], payload: dict[str, Any], status: str) -> None:
        stage_name = payload.get("stageName") or payload.get("stage") or "analyze"
        module = pipeline.get("modules", [{}])[0]
        stages = module.setdefault("stages", {})
        stage = stages.setdefault(
            stage_name,
            {"id": f"stage-{uuid.uuid4()}", "stage": stage_name, "status": "pending", "gate": "pending"},
        )
        stage["status"] = status
        stage["updatedAt"] = utc_now()
        if "memberId" in payload:
            stage["assignedMemberId"] = payload["memberId"]
        if "gate" in payload:
            stage["gate"] = payload["gate"]
        pipeline["updatedAt"] = utc_now()

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

    @app.get("/api/state")
    async def legacy_state() -> dict[str, Any]:
        return view_model()

    @app.get("/api/workspaces")
    async def legacy_workspaces() -> dict[str, Any]:
        projects = platform.list_projects()
        return {
            "workspaces": [
                {
                    "id": project["id"],
                    "path": project["path"],
                    "name": project["name"],
                    "active": Path(project["path"]) == platform.cwd,
                }
                for project in projects
            ],
            "snapshot": view_model(),
        }

    @app.post("/api/workspaces/select", status_code=202)
    async def legacy_workspace_select(request: Request) -> dict[str, Any]:
        body = await request.json()
        ref = body.get("cwd") or body.get("path") or ""
        project = platform.find_project(ref) or platform.create_project(
            name=Path(ref or platform.cwd).name or "Workspace",
            path=ref or platform.cwd,
            template_id="other",
            create_directory=True,
            source="workspace-select",
        )
        platform.cwd = Path(project["path"])
        state = load_state()
        state["activeWorkspacePath"] = project["path"]
        save_state(state)
        return view_model(state)

    @app.post("/api/workspaces/{workspace_ref}/{action}")
    async def legacy_workspace_action(workspace_ref: str, action: str) -> dict[str, Any]:
        if action not in {"collapse", "expand", "pin", "unpin"}:
            raise HTTPException(status_code=404, detail="Unsupported workspace action")
        state = load_state()
        dashboard = state.setdefault("dashboard", {"collapsedWorkspaceIds": [], "pinnedWorkspaceIds": []})
        collapsed = set(dashboard.get("collapsedWorkspaceIds") or [])
        pinned = set(dashboard.get("pinnedWorkspaceIds") or [])
        if action == "collapse":
            collapsed.add(workspace_ref)
        elif action == "expand":
            collapsed.discard(workspace_ref)
        elif action == "pin":
            pinned.add(workspace_ref)
        elif action == "unpin":
            pinned.discard(workspace_ref)
        dashboard["collapsedWorkspaceIds"] = sorted(collapsed)
        dashboard["pinnedWorkspaceIds"] = sorted(pinned)
        save_state(state)
        return {**await legacy_workspaces(), "snapshot": view_model(state)}

    @app.get("/api/git")
    async def legacy_git() -> dict[str, Any]:
        return {"available": False, "isRepo": False, "branch": "", "baseBranch": "", "status": "not-a-git-repo"}

    @app.post("/api/git/checkout", status_code=202)
    async def legacy_git_checkout(request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        state.setdefault("configCatalog", default_config_catalog()).setdefault("gitPolicy", default_config_catalog()["gitPolicy"])
        state["configCatalog"]["gitPolicy"]["currentBranch"] = body.get("branch", "")
        save_state(state)
        return view_model(state)

    @app.get("/api/sessions")
    async def legacy_sessions() -> dict[str, Any]:
        state = load_state()
        return {"sessions": state["sessions"]}

    @app.post("/api/sessions", status_code=201)
    async def legacy_create_session(request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        session = {
            "id": f"session-{uuid.uuid4()}",
            "name": body.get("name") or "Main",
            "teamId": state.get("activeTeamId"),
            "pinned": False,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        state["sessions"].append(session)
        state["activeSessionId"] = session["id"]
        project = current_project()
        persist_session_table(project["id"], session)
        save_state(state)
        return view_model(state)

    @app.post("/api/sessions/select")
    async def legacy_select_session(request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        ref = body.get("sessionId") or body.get("name")
        session = find_by_id(state["sessions"], ref) or next(
            (entry for entry in state["sessions"] if entry.get("name") == ref),
            None,
        )
        if not session:
            raise HTTPException(status_code=404, detail=f"Session not found: {ref}")
        state["activeSessionId"] = session["id"]
        state["activeChatId"] = session.get("activeChatId")
        state["activePipelineId"] = session.get("activePipelineId")
        save_state(state)
        return view_model(state)

    @app.post("/api/sessions/{session_id}/clone", status_code=201)
    async def legacy_clone_session(session_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        source = find_by_id(state["sessions"], session_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        session = {
            **source,
            "id": f"session-{uuid.uuid4()}",
            "name": body.get("name") or f"{source.get('name', 'Session')} copy",
            "activeChatId": None,
            "activePipelineId": None,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        state["sessions"].append(session)
        state["activeSessionId"] = session["id"]
        state["activeChatId"] = None
        state["activePipelineId"] = None
        persist_session_table(current_project()["id"], session)
        save_state(state)
        return view_model(state)

    @app.post("/api/sessions/{session_id}/{action}")
    async def legacy_session_action(session_id: str, action: str) -> dict[str, Any]:
        if action not in {"pin", "unpin"}:
            raise HTTPException(status_code=404, detail="Unsupported session action")
        state = load_state()
        session = find_by_id(state["sessions"], session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        session["pinned"] = action == "pin"
        session["updatedAt"] = utc_now()
        persist_session_table(current_project()["id"], session)
        save_state(state)
        return view_model(state)

    @app.patch("/api/sessions/{session_id}")
    async def legacy_patch_session(session_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        session = find_by_id(state["sessions"], session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        for key in ("name", "pinned"):
            if key in body:
                session[key] = body[key]
        session["updatedAt"] = utc_now()
        persist_session_table(current_project()["id"], session)
        save_state(state)
        return view_model(state)

    @app.delete("/api/sessions/{session_id}")
    async def legacy_delete_session(session_id: str) -> dict[str, Any]:
        state = load_state()
        state["sessions"] = [session for session in state["sessions"] if session.get("id") != session_id]
        if state.get("activeSessionId") == session_id:
            state["activeSessionId"] = state["sessions"][0]["id"] if state["sessions"] else None
        platform.connection.execute("UPDATE sessions SET status = 'archived', updated_at = ? WHERE id = ?", (utc_now(), session_id))
        save_state(state)
        return view_model(state)

    @app.get("/api/config/options")
    async def legacy_config_options() -> dict[str, Any]:
        return {"providers": platform.list_providers(), "templates": platform.list_project_templates()}

    @app.get("/api/extensions/catalog")
    async def legacy_extensions_catalog() -> dict[str, list[Any]]:
        return {"plugins": [], "skills": []}

    @app.get("/api/config")
    async def legacy_config() -> dict[str, Any]:
        state = load_state()
        return {
            "configCatalog": state.get("configCatalog", default_config_catalog()),
            "connectors": [],
            "extensions": {"plugins": [], "skills": []},
            "dashboard": {"backend": "python"},
        }

    @app.patch("/api/config", status_code=202)
    async def legacy_patch_config(request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        config = state.setdefault("configCatalog", default_config_catalog())
        for key, value in body.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
        save_state(state)
        return view_model(state)

    @app.post("/api/extensions/marketplaces", status_code=202)
    async def legacy_extension_marketplace(request: Request) -> dict[str, Any]:
        body = await request.json()
        return {"accepted": True, "result": {"target": body.get("target"), "source": body.get("source")}, "snapshot": view_model()}

    @app.post("/api/extensions/plugins/install", status_code=202)
    async def legacy_extension_plugin_install(request: Request) -> dict[str, Any]:
        body = await request.json()
        return {"accepted": True, "result": {"pluginRef": body.get("pluginRef")}, "syncResult": {"synced": False}, "snapshot": view_model()}

    @app.post("/api/extensions/plugins/sync-skills", status_code=202)
    async def legacy_extension_plugin_sync(request: Request) -> dict[str, Any]:
        body = await request.json()
        return {"accepted": True, "result": {"pluginKey": body.get("pluginKey"), "synced": True}, "snapshot": view_model()}

    @app.post("/api/extensions/skills/install", status_code=202)
    async def legacy_extension_skill_install(request: Request) -> dict[str, Any]:
        body = await request.json()
        return {"accepted": True, "result": {"sourcePath": body.get("sourcePath")}, "snapshot": view_model()}

    @app.post("/api/chats/send", status_code=202)
    async def legacy_chat_send(request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        chat = create_chat_record(state, body.get("prompt", ""), body.get("mode", "auto"))
        save_state(state)
        return {**view_model(state), "message": "Chat routed", "chat": chat, "intent": "auto"}

    @app.get("/api/chats/{chat_id}")
    async def legacy_get_chat(chat_id: str) -> dict[str, Any]:
        state = load_state()
        chat = find_by_id(state["chats"], chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail=f"Chat not found: {chat_id}")
        return chat

    @app.post("/api/idea/intake", status_code=202)
    async def legacy_idea_intake(request: Request) -> dict[str, Any]:
        body = await request.json()
        state = load_state()
        pipeline = create_pipeline_record(state, body.get("idea", "Untitled pipeline"))
        save_state(state)
        return {**view_model(state), "pipeline": pipeline}

    @app.get("/api/pipelines/{pipeline_id}")
    async def legacy_get_pipeline(pipeline_id: str) -> dict[str, Any]:
        state = load_state()
        pipeline = find_by_id(state["pipelines"], pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail=f"Pipeline not found: {pipeline_id}")
        return pipeline

    @app.post("/api/pipelines/{pipeline_id}/{action}", status_code=202)
    async def legacy_pipeline_action(pipeline_id: str, action: str) -> dict[str, Any]:
        if action not in {"start", "retry", "archive"}:
            raise HTTPException(status_code=404, detail="Unsupported pipeline action")
        state = load_state()
        pipeline = find_by_id(state["pipelines"], pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail=f"Pipeline not found: {pipeline_id}")
        pipeline["status"] = "archived" if action == "archive" else "running"
        pipeline["updatedAt"] = utc_now()
        state["activePipelineId"] = pipeline["id"]
        save_state(state)
        return view_model(state)

    @app.post("/api/pipelines/{pipeline_id}/stages/{action}", status_code=202)
    async def legacy_pipeline_stage_action(pipeline_id: str, action: str, request: Request) -> dict[str, Any]:
        if action not in {"retry", "assign", "override"}:
            raise HTTPException(status_code=404, detail="Unsupported stage action")
        body = await request.json()
        state = load_state()
        pipeline = find_by_id(state["pipelines"], pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail=f"Pipeline not found: {pipeline_id}")
        status = "pending" if action == "retry" else "assigned" if action == "assign" else "completed"
        update_pipeline_stage(pipeline, body, status)
        state["activePipelineId"] = pipeline["id"]
        save_state(state)
        return view_model(state)

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


app = create_app()
