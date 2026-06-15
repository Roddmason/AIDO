"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads

from local_control_center.shared.time import utc_now


def row_to_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "teamId": row["team_id"],
        "name": row["name"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_chat(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "sessionId": row["session_id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class SessionsChatsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_session(self, *, project_id: str, name: str, team_id: str | None = None) -> dict[str, Any]:
        timestamp = utc_now()
        session_id = f"session-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO sessions (id, project_id, team_id, name, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (session_id, project_id, team_id, name, json_dumps({}), timestamp, timestamp),
        )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise KeyError(f"Session not found: {session_id}")
        return row_to_session(row)

    def list_sessions(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        return [row_to_session(row) for row in rows]

    def create_chat(
        self,
        *,
        project_id: str,
        session_id: str | None,
        prompt: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        chat_id = f"chat-{uuid.uuid4()}"
        resolved_title = title or prompt.strip().splitlines()[0][:80] or "Untitled chat"
        self.connection.execute(
            """
            INSERT INTO chats (id, project_id, session_id, title, prompt, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (chat_id, project_id, session_id, resolved_title, prompt, json_dumps({}), timestamp, timestamp),
        )
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not row:
            raise KeyError(f"Chat not found: {chat_id}")
        return row_to_chat(row)

    def list_chats(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM chats WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM chats ORDER BY created_at DESC").fetchall()
        return [row_to_chat(row) for row in rows]


