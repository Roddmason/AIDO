from __future__ import annotations

import json
import sqlite3
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


def row_to_agent_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "runtimeType": row["runtime_type"],
        "modelPolicyId": row["model_policy_id"],
        "allowedSkills": json_loads(row["allowed_skills"], []),
        "allowedTools": json_loads(row["allowed_tools"], []),
        "permissionProfile": row["permission_profile"],
        "memoryScope": row["memory_scope"],
        "maxCostPerRun": row["max_cost_per_run"],
        "maxRuntimeSeconds": row["max_runtime_seconds"],
        "outputSchema": json_loads(row["output_schema"]),
        "qualityGates": json_loads(row["quality_gates"], []),
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_model_policy(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "preferred": json_loads(row["preferred"], []),
        "fallback": json_loads(row["fallback"], []),
        "maxCostUsd": row["max_cost_usd"],
        "maxTokens": row["max_tokens"],
        "temperature": row["temperature"],
        "allowRemote": bool(row["allow_remote"]),
        "allowLocal": bool(row["allow_local"]),
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class AgentsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert_agent_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        profile_id = body["id"]
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO agent_profiles
                (id, name, role, runtime_type, model_policy_id, allowed_skills, allowed_tools,
                 permission_profile, memory_scope, max_cost_per_run, max_runtime_seconds,
                 output_schema, quality_gates, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                runtime_type = excluded.runtime_type,
                model_policy_id = excluded.model_policy_id,
                allowed_skills = excluded.allowed_skills,
                allowed_tools = excluded.allowed_tools,
                permission_profile = excluded.permission_profile,
                memory_scope = excluded.memory_scope,
                max_cost_per_run = excluded.max_cost_per_run,
                max_runtime_seconds = excluded.max_runtime_seconds,
                output_schema = excluded.output_schema,
                quality_gates = excluded.quality_gates,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                body.get("name", profile_id),
                body.get("role", "implementer"),
                body.get("runtimeType", "internal_mock"),
                body.get("modelPolicyId"),
                json_dumps(body.get("allowedSkills") or []),
                json_dumps(body.get("allowedTools") or []),
                body.get("permissionProfile", "plan"),
                body.get("memoryScope", "project"),
                body.get("maxCostPerRun", 0),
                body.get("maxRuntimeSeconds", 900),
                json_dumps(body.get("outputSchema") or {}),
                json_dumps(body.get("qualityGates") or []),
                body.get("status", "active"),
                timestamp,
                timestamp,
            ),
        )
        return self.get_agent_profile(profile_id)

    def get_agent_profile(self, profile_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            raise KeyError(f"Agent profile not found: {profile_id}")
        return row_to_agent_profile(row)

    def list_agent_profiles(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM agent_profiles ORDER BY id ASC").fetchall()
        return [row_to_agent_profile(row) for row in rows]

    def upsert_model_policy(self, body: dict[str, Any]) -> dict[str, Any]:
        policy_id = body["id"]
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO model_policies
                (id, name, preferred, fallback, max_cost_usd, max_tokens, temperature,
                 allow_remote, allow_local, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                preferred = excluded.preferred,
                fallback = excluded.fallback,
                max_cost_usd = excluded.max_cost_usd,
                max_tokens = excluded.max_tokens,
                temperature = excluded.temperature,
                allow_remote = excluded.allow_remote,
                allow_local = excluded.allow_local,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                policy_id,
                body.get("name", policy_id),
                json_dumps(body.get("preferred") or []),
                json_dumps(body.get("fallback") or []),
                body.get("maxCostUsd", 0),
                body.get("maxTokens", 0),
                body.get("temperature", 0.2),
                1 if body.get("allowRemote", True) else 0,
                1 if body.get("allowLocal", True) else 0,
                body.get("status", "active"),
                timestamp,
                timestamp,
            ),
        )
        return self.get_model_policy(policy_id)

    def get_model_policy(self, policy_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM model_policies WHERE id = ?", (policy_id,)).fetchone()
        if not row:
            raise KeyError(f"Model policy not found: {policy_id}")
        return row_to_model_policy(row)

    def list_model_policies(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM model_policies ORDER BY id ASC").fetchall()
        return [row_to_model_policy(row) for row in rows]
