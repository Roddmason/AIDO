from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.shared.event_bus import EventBus

from .repository import SessionsChatsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> SessionsChatsRepository:
        return SessionsChatsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {"sessions": repository().list_sessions()}

    @router.post("/api/v1/sessions", status_code=201)
    async def create_session(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        session = repository().create_session(
            project_id=body["projectId"],
            name=body.get("name") or "Session",
            team_id=body.get("teamId"),
        )
        event_bus().record_event(
            project_id=session["projectId"],
            event_type="session.created",
            payload={"sessionId": session["id"]},
        )
        return {"session": session}

    @router.get("/api/v1/chats")
    async def list_chats() -> dict[str, Any]:
        return {"chats": repository().list_chats()}

    @router.post("/api/v1/chats", status_code=201)
    async def create_chat(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(status_code=400, detail="prompt is required.")
        chat = repository().create_chat(
            project_id=body["projectId"],
            session_id=body.get("sessionId"),
            prompt=prompt,
            title=body.get("title"),
        )
        event_bus().record_event(
            project_id=chat["projectId"],
            event_type="chat.created",
            payload={"chatId": chat["id"], "sessionId": chat["sessionId"]},
        )
        return {"chat": chat}

    return router
