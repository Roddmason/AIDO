from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..store import PlatformStore, default_config_catalog, default_workspace_state, json_dumps, utc_now


def create_router(*, platform: PlatformStore, require_write) -> APIRouter:
    router = APIRouter()

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

    @router.get("/api/state")
    async def legacy_state() -> dict[str, Any]:
        return view_model()

    @router.get("/api/workspaces")
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

    @router.post("/api/workspaces/select", status_code=202)
    async def legacy_workspace_select(request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.post("/api/workspaces/{workspace_ref}/{action}")
    async def legacy_workspace_action(workspace_ref: str, action: str, request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.get("/api/git")
    async def legacy_git() -> dict[str, Any]:
        return {"available": False, "isRepo": False, "branch": "", "baseBranch": "", "status": "not-a-git-repo"}

    @router.post("/api/git/checkout", status_code=202)
    async def legacy_git_checkout(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        state = load_state()
        state.setdefault("configCatalog", default_config_catalog()).setdefault("gitPolicy", default_config_catalog()["gitPolicy"])
        state["configCatalog"]["gitPolicy"]["currentBranch"] = body.get("branch", "")
        save_state(state)
        return view_model(state)

    @router.get("/api/sessions")
    async def legacy_sessions() -> dict[str, Any]:
        state = load_state()
        return {"sessions": state["sessions"]}

    @router.post("/api/sessions", status_code=201)
    async def legacy_create_session(request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.post("/api/sessions/select")
    async def legacy_select_session(request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.post("/api/sessions/{session_id}/clone", status_code=201)
    async def legacy_clone_session(session_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.post("/api/sessions/{session_id}/{action}")
    async def legacy_session_action(session_id: str, action: str, request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.patch("/api/sessions/{session_id}")
    async def legacy_patch_session(session_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.delete("/api/sessions/{session_id}")
    async def legacy_delete_session(session_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        state = load_state()
        state["sessions"] = [session for session in state["sessions"] if session.get("id") != session_id]
        if state.get("activeSessionId") == session_id:
            state["activeSessionId"] = state["sessions"][0]["id"] if state["sessions"] else None
        platform.connection.execute("UPDATE sessions SET status = 'archived', updated_at = ? WHERE id = ?", (utc_now(), session_id))
        save_state(state)
        return view_model(state)

    @router.get("/api/config/options")
    async def legacy_config_options() -> dict[str, Any]:
        return {"providers": platform.list_providers(), "templates": platform.list_project_templates()}

    @router.get("/api/extensions/catalog")
    async def legacy_extensions_catalog() -> dict[str, list[Any]]:
        return {"plugins": [], "skills": []}

    @router.get("/api/config")
    async def legacy_config() -> dict[str, Any]:
        state = load_state()
        return {
            "configCatalog": state.get("configCatalog", default_config_catalog()),
            "connectors": [],
            "extensions": {"plugins": [], "skills": []},
            "dashboard": {"backend": "python"},
        }

    @router.patch("/api/config", status_code=202)
    async def legacy_patch_config(request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.post("/api/extensions/marketplaces", status_code=202)
    async def legacy_extension_marketplace(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        return {"accepted": True, "result": {"target": body.get("target"), "source": body.get("source")}, "snapshot": view_model()}

    @router.post("/api/extensions/plugins/install", status_code=202)
    async def legacy_extension_plugin_install(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        return {"accepted": True, "result": {"pluginRef": body.get("pluginRef")}, "syncResult": {"synced": False}, "snapshot": view_model()}

    @router.post("/api/extensions/plugins/sync-skills", status_code=202)
    async def legacy_extension_plugin_sync(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        return {"accepted": True, "result": {"pluginKey": body.get("pluginKey"), "synced": True}, "snapshot": view_model()}

    @router.post("/api/extensions/skills/install", status_code=202)
    async def legacy_extension_skill_install(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        return {"accepted": True, "result": {"sourcePath": body.get("sourcePath")}, "snapshot": view_model()}

    @router.post("/api/chats/send", status_code=202)
    async def legacy_chat_send(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        state = load_state()
        chat = create_chat_record(state, body.get("prompt", ""), body.get("mode", "auto"))
        save_state(state)
        return {**view_model(state), "message": "Chat routed", "chat": chat, "intent": "auto"}

    @router.get("/api/chats/{chat_id}")
    async def legacy_get_chat(chat_id: str) -> dict[str, Any]:
        state = load_state()
        chat = find_by_id(state["chats"], chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail=f"Chat not found: {chat_id}")
        return chat

    @router.post("/api/idea/intake", status_code=202)
    async def legacy_idea_intake(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        state = load_state()
        pipeline = create_pipeline_record(state, body.get("idea", "Untitled pipeline"))
        save_state(state)
        return {**view_model(state), "pipeline": pipeline}

    @router.get("/api/pipelines/{pipeline_id}")
    async def legacy_get_pipeline(pipeline_id: str) -> dict[str, Any]:
        state = load_state()
        pipeline = find_by_id(state["pipelines"], pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail=f"Pipeline not found: {pipeline_id}")
        return pipeline

    @router.post("/api/pipelines/{pipeline_id}/{action}", status_code=202)
    async def legacy_pipeline_action(pipeline_id: str, action: str, request: Request) -> dict[str, Any]:
        require_write(request)
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

    @router.post("/api/pipelines/{pipeline_id}/stages/{action}", status_code=202)
    async def legacy_pipeline_stage_action(pipeline_id: str, action: str, request: Request) -> dict[str, Any]:
        require_write(request)
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

    return router
