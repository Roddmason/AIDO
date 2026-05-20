from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    name: str | None = None
    team_id: str | None = Field(default=None, alias="teamId")


class SessionResponse(BaseModel):
    session: dict[str, Any]


class SessionsListResponse(BaseModel):
    sessions: list[dict[str, Any]]


class ChatCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    prompt: str
    title: str | None = None


class ChatResponse(BaseModel):
    chat: dict[str, Any]


class ChatsListResponse(BaseModel):
    chats: list[dict[str, Any]]
