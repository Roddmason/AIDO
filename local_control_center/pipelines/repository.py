from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads

from local_control_center.shared.time import utc_now


DEFAULT_STAGES = [
    {"name": "idea_intake", "status": "pending"},
    {"name": "project_discovery", "status": "pending"},
    {"name": "implementation", "status": "pending"},
    {"name": "qa_validation", "status": "pending"},
    {"name": "technical_review", "status": "pending"},
]


def row_to_pipeline(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "sessionId": row["session_id"],
        "chatId": row["chat_id"],
        "title": row["title"],
        "status": row["status"],
        "stages": json_loads(row["stages"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class PipelinesRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_pipeline(
        self,
        *,
        project_id: str,
        title: str,
        session_id: str | None = None,
        chat_id: str | None = None,
        stages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        pipeline_id = f"pipeline-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO pipelines
                (id, project_id, session_id, chat_id, title, status, stages, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                pipeline_id,
                project_id,
                session_id,
                chat_id,
                title,
                json_dumps(stages or DEFAULT_STAGES),
                json_dumps({}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_pipeline(pipeline_id)

    def get_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)).fetchone()
        if not row:
            raise KeyError(f"Pipeline not found: {pipeline_id}")
        return row_to_pipeline(row)

    def list_pipelines(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM pipelines WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM pipelines ORDER BY created_at DESC").fetchall()
        return [row_to_pipeline(row) for row in rows]


