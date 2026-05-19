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


def row_to_agent_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "jobId": row["job_id"],
        "status": row["status"],
        "input": json_loads(row["input"]),
        "output": json_loads(row["output"]),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_agent_tool_call(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentRunId": row["agent_run_id"],
        "toolName": row["tool_name"],
        "status": row["status"],
        "payload": json_loads(row["payload"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_model_call(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "agentRunId": row["agent_run_id"],
        "modelPolicyId": row["model_policy_id"],
        "provider": row["provider"],
        "model": row["model"],
        "status": row["status"],
        "promptTokens": row["prompt_tokens"],
        "completionTokens": row["completion_tokens"],
        "costUsd": row["cost_usd"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


def row_to_cost_usage(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "scope": row["scope"],
        "amountUsd": row["amount_usd"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
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

    def create_agent_run(
        self,
        *,
        project_id: str,
        agent_profile_id: str,
        task_id: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        status: str = "completed",
    ) -> dict[str, Any]:
        profile = self.get_agent_profile(agent_profile_id)
        timestamp = utc_now()
        run_id = f"agent-run-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO agent_runs (id, project_id, job_id, status, input, output, metadata, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                project_id,
                status,
                json_dumps(input_payload),
                json_dumps(output_payload),
                json_dumps({"agentProfileId": agent_profile_id, "taskId": task_id, "runtimeType": profile["runtimeType"]}),
                timestamp,
                timestamp,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO agent_tool_calls
                (id, agent_run_id, tool_name, status, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"agent-tool-call-{uuid.uuid4()}",
                run_id,
                "internal_mock.complete",
                "completed",
                json_dumps({"taskId": task_id}),
                timestamp,
                timestamp,
            ),
        )
        policy_id = profile.get("modelPolicyId")
        self.connection.execute(
            """
            INSERT INTO model_calls
                (id, project_id, agent_run_id, model_policy_id, provider, model, status,
                 prompt_tokens, completion_tokens, cost_usd, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"model-call-{uuid.uuid4()}",
                project_id,
                run_id,
                policy_id,
                "internal_mock",
                "mock",
                "completed",
                0,
                0,
                0.0,
                json_dumps({"redacted": True}),
                timestamp,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO cost_usage (id, project_id, scope, amount_usd, metadata, created_at)
            VALUES (?, ?, 'model_call', 0.0, ?, ?)
            """,
            (f"cost-{uuid.uuid4()}", project_id, json_dumps({"agentRunId": run_id}), timestamp),
        )
        return self.get_agent_run(run_id)

    def get_agent_run(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Agent run not found: {run_id}")
        return row_to_agent_run(row)

    def list_agent_runs(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM agent_runs ORDER BY created_at DESC").fetchall()
        return [row_to_agent_run(row) for row in rows]

    def list_agent_tool_calls(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM agent_tool_calls ORDER BY created_at DESC").fetchall()
        return [row_to_agent_tool_call(row) for row in rows]

    def list_model_calls(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM model_calls ORDER BY created_at DESC").fetchall()
        return [row_to_model_call(row) for row in rows]

    def list_cost_usage(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM cost_usage ORDER BY created_at DESC").fetchall()
        return [row_to_cost_usage(row) for row in rows]
