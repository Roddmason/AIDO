"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    name: str | None = None
    team_id: str | None = Field(default=None, alias="teamId")


class SessionRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    team_id: str | None = Field(default=None, alias="teamId")
    name: str
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class SessionResponse(BaseModel):
    session: SessionRecord


class SessionsListResponse(BaseModel):
    sessions: list[SessionRecord]


class ChatCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    prompt: str
    title: str | None = None


class ChatRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    title: str
    prompt: str
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ChatResponse(BaseModel):
    chat: ChatRecord


class ChatsListResponse(BaseModel):
    chats: list[ChatRecord]
