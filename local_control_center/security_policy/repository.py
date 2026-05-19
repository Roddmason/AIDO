from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from local_control_center.store import utc_now


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if fallback is None else fallback


def row_to_policy(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "profile": row["profile"],
        "rules": json_loads(row["rules"], []),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_decision(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "workspaceId": row["workspace_id"],
        "agentId": row["agent_id"],
        "role": row["role"],
        "tool": row["tool"],
        "command": row["command"],
        "path": row["path"],
        "decision": row["decision"],
        "riskLevel": row["risk_level"],
        "reason": row["reason"],
        "payload": json_loads(row["payload"]),
        "createdAt": row["created_at"],
    }


class SecurityPolicyRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_policies(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM permission_policies ORDER BY id ASC").fetchall()
        return [row_to_policy(row) for row in rows]

    def record_decision(
        self,
        *,
        project_id: str | None,
        workspace_id: str | None,
        agent_id: str | None,
        role: str | None,
        tool: str | None,
        command: str | None,
        path: str | None,
        decision: str,
        risk_level: str,
        reason: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        decision_id = f"permission-decision-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO permission_decisions
                (id, project_id, workspace_id, agent_id, role, tool, command, path,
                 decision, risk_level, reason, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                project_id,
                workspace_id,
                agent_id,
                role,
                tool,
                command,
                path,
                decision,
                risk_level,
                reason,
                json_dumps(payload),
                utc_now(),
            ),
        )
        row = self.connection.execute("SELECT * FROM permission_decisions WHERE id = ?", (decision_id,)).fetchone()
        return row_to_decision(row)

    def list_decisions(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM permission_decisions WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM permission_decisions ORDER BY created_at DESC").fetchall()
        return [row_to_decision(row) for row in rows]
