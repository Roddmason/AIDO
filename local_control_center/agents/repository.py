from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.telemetry import record_agent_run, record_model_call

from local_control_center.shared.time import utc_now


def row_to_agent_profile(row: sqlite3.Row) -> dict[str, Any]:
    runtime_mode = row["runtime_type"]
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "runtimeType": runtime_mode,
        "runtimeMode": runtime_mode,
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
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["workflow_step_id"],
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


def row_to_model_provider(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "label": row["label"],
        "status": row["status"],
        "allowRemote": bool(row["allow_remote"]),
        "metadata": json_loads(row["metadata"]),
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
                body.get("runtimeMode") or body.get("runtimeType", "internal_mock"),
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

    def list_model_providers(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM model_providers ORDER BY id ASC").fetchall()
        return [row_to_model_provider(row) for row in rows]

    def total_cost_usage(self, *, project_id: str, scope: str = "model_call") -> float:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM cost_usage WHERE project_id = ? AND scope = ?",
            (project_id, scope),
        ).fetchone()
        return float(row["total"] or 0)

    def record_model_call(
        self,
        *,
        project_id: str,
        provider: str,
        model: str,
        status: str,
        model_policy_id: str | None = None,
        agent_run_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
        timestamp = utc_now()
        call_id = f"model-call-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO model_calls
                (id, project_id, agent_run_id, model_policy_id, provider, model, status,
                 prompt_tokens, completion_tokens, cost_usd, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                project_id,
                agent_run_id,
                model_policy_id,
                provider,
                model,
                status,
                prompt_tokens,
                completion_tokens,
                cost_usd,
                json_dumps(metadata or {}),
                timestamp,
            ),
        )
        if cost_usd:
            self.connection.execute(
                """
                INSERT INTO cost_usage (id, project_id, scope, amount_usd, metadata, created_at)
                VALUES (?, ?, 'model_call', ?, ?, ?)
                """,
                (
                    f"cost-{uuid.uuid4()}",
                    project_id,
                    cost_usd,
                    json_dumps({"modelCallId": call_id, "modelPolicyId": model_policy_id}),
                    timestamp,
                ),
            )
        row = self.connection.execute("SELECT * FROM model_calls WHERE id = ?", (call_id,)).fetchone()
        return row_to_model_call(row)

    def create_agent_run(
        self,
        *,
        project_id: str,
        agent_profile_id: str,
        task_id: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        job_id: str | None = None,
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
        status: str = "completed",
    ) -> dict[str, Any]:
        profile = self.get_agent_profile(agent_profile_id)
        timestamp = utc_now()
        run_id = f"agent-run-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO agent_runs
                (id, project_id, job_id, workflow_run_id, workflow_step_id, status,
                 input, output, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                project_id,
                job_id,
                workflow_run_id or input_payload.get("workflowRunId"),
                workflow_step_id or input_payload.get("workflowStepId"),
                status,
                json_dumps(input_payload),
                json_dumps(output_payload),
                json_dumps({"agentProfileId": agent_profile_id, "taskId": task_id, "runtimeType": profile["runtimeType"]}),
                timestamp,
                timestamp,
            ),
        )
        if profile["runtimeType"] == "internal_mock":
            self.record_agent_tool_call(
                agent_run_id=run_id,
                tool_name="internal_mock.complete",
                status="completed",
                payload={"taskId": task_id},
                timestamp=timestamp,
            )
        policy_id = profile.get("modelPolicyId")
        model_call_id = f"model-call-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO model_calls
                (id, project_id, agent_run_id, model_policy_id, provider, model, status,
                 prompt_tokens, completion_tokens, cost_usd, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_call_id,
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
        model_call_row = self.connection.execute("SELECT * FROM model_calls WHERE id = ?", (model_call_id,)).fetchone()
        record_model_call(self.connection, row_to_model_call(model_call_row))
        self.connection.execute(
            """
            INSERT INTO cost_usage (id, project_id, scope, amount_usd, metadata, created_at)
            VALUES (?, ?, 'model_call', 0.0, ?, ?)
            """,
            (f"cost-{uuid.uuid4()}", project_id, json_dumps({"agentRunId": run_id}), timestamp),
        )
        agent_run = self.get_agent_run(run_id)
        record_agent_run(self.connection, agent_run)
        return agent_run

    def update_agent_run_status(self, run_id: str, *, status: str, output_payload: dict[str, Any]) -> dict[str, Any]:
        self.connection.execute(
            """
            UPDATE agent_runs
            SET status = ?, output = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, json_dumps(output_payload), utc_now(), run_id),
        )
        return self.get_agent_run(run_id)

    def record_agent_tool_call(
        self,
        *,
        agent_run_id: str,
        tool_name: str,
        status: str,
        payload: dict[str, Any],
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        now = timestamp or utc_now()
        call_id = f"agent-tool-call-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO agent_tool_calls
                (id, agent_run_id, tool_name, status, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                agent_run_id,
                tool_name,
                status,
                json_dumps(payload),
                now,
                now,
            ),
        )
        row = self.connection.execute("SELECT * FROM agent_tool_calls WHERE id = ?", (call_id,)).fetchone()
        return row_to_agent_tool_call(row)

    def get_agent_run(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Agent run not found: {run_id}")
        return row_to_agent_run(row)

    def list_agent_runs(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM agent_runs ORDER BY created_at DESC").fetchall()
        return [row_to_agent_run(row) for row in rows]

    def list_agent_runs_for_workflow_runs(self, workflow_run_ids: list[str]) -> list[dict[str, Any]]:
        if not workflow_run_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_run_ids)
        rows = self.connection.execute(
            f"SELECT * FROM agent_runs WHERE workflow_run_id IN ({placeholders}) ORDER BY created_at DESC",
            tuple(workflow_run_ids),
        ).fetchall()
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


