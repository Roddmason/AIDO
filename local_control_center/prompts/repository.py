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


def row_to_prompt(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "mode": row["mode"],
        "body": row["body"],
        "optimizer": row["optimizer"],
        "appliesTo": json_loads(row["applies_to"]),
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class PromptsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert_prompt_template(
        self,
        *,
        project_id: str,
        name: str,
        body: str,
        mode: str = "manual",
        optimizer: str = "",
        applies_to: dict[str, Any] | None = None,
        prompt_id: str | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        existing = (
            self.connection.execute("SELECT * FROM prompt_templates WHERE id = ?", (prompt_id,)).fetchone()
            if prompt_id
            else None
        )
        next_id = prompt_id or f"prompt-{uuid.uuid4()}"
        version = (existing["version"] + 1) if existing else 1
        serialized_applies_to = json_dumps(applies_to or {})
        if existing:
            self.connection.execute(
                """
                UPDATE prompt_templates
                SET name = ?, mode = ?, body = ?, optimizer = ?, applies_to = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, mode, body, optimizer, serialized_applies_to, version, timestamp, next_id),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO prompt_templates
                    (id, project_id, name, mode, body, optimizer, applies_to, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_id,
                    project_id,
                    name,
                    mode,
                    body,
                    optimizer,
                    serialized_applies_to,
                    version,
                    timestamp,
                    timestamp,
                ),
            )
        self.connection.execute(
            """
            INSERT INTO prompt_versions
                (id, prompt_id, version, body, mode, optimizer, applies_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"prompt-version-{uuid.uuid4()}",
                next_id,
                version,
                body,
                mode,
                optimizer,
                serialized_applies_to,
                timestamp,
            ),
        )
        return self.get_prompt_template(next_id)

    def get_prompt_template(self, prompt_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM prompt_templates WHERE id = ?", (prompt_id,)).fetchone()
        if not row:
            raise KeyError(f"Prompt template not found: {prompt_id}")
        return row_to_prompt(row)

    def list_prompt_templates(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM prompt_templates WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM prompt_templates ORDER BY updated_at DESC"
            ).fetchall()
        return [row_to_prompt(row) for row in rows]
