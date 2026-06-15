"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.shared.event_bus import EventBus

from .models import (
    ChatCreateRequest,
    ChatResponse,
    ChatsListResponse,
    SessionCreateRequest,
    SessionResponse,
    SessionsListResponse,
)
from .repository import SessionsChatsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> SessionsChatsRepository:
        return SessionsChatsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/sessions", response_model=SessionsListResponse)
    async def list_sessions() -> dict[str, Any]:
        return {"sessions": repository().list_sessions()}

    @router.post("/api/v1/sessions", status_code=201, response_model=SessionResponse)
    async def create_session(body: SessionCreateRequest, request: Request) -> SessionResponse:
        require_write(request)
        session = repository().create_session(
            project_id=body.project_id,
            name=body.name or "Session",
            team_id=body.team_id,
        )
        event_bus().record_event(
            project_id=session["projectId"],
            event_type="session.created",
            payload={"sessionId": session["id"]},
        )
        return SessionResponse(session=session)

    @router.get("/api/v1/chats", response_model=ChatsListResponse)
    async def list_chats() -> dict[str, Any]:
        return {"chats": repository().list_chats()}

    @router.post("/api/v1/chats", status_code=201, response_model=ChatResponse)
    async def create_chat(body: ChatCreateRequest, request: Request) -> ChatResponse:
        require_write(request)
        prompt = body.prompt
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(status_code=400, detail="prompt is required.")
        chat = repository().create_chat(
            project_id=body.project_id,
            session_id=body.session_id,
            prompt=prompt,
            title=body.title,
        )
        event_bus().record_event(
            project_id=chat["projectId"],
            event_type="chat.created",
            payload={"chatId": chat["id"], "sessionId": chat["sessionId"]},
        )
        return ChatResponse(chat=chat)

    return router
