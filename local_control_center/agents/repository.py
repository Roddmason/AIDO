"""SQLite persistence for agent profiles, model policies, runs, tool/model calls, cost.

Owns CRUD for the agent-execution tables and the row<->dict mapping the API layer
consumes. Sensitive payloads (inputs, outputs, metadata) are redacted before insertion.

Transaction boundaries: each write method emits its statements on the caller's
connection without an explicit `commit`, so the caller owns the transaction. Methods
that touch two tables (e.g. `record_model_call` writing `model_calls` plus a
`cost_usage` row, `create_agent_run` writing `agent_runs` plus telemetry) are atomic
only within that caller-managed transaction.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.telemetry import record_agent_run
from local_control_center.shared.time import utc_now

PRODUCT_RUNTIME_MODES = {"api", "cli", "ollama", "hybrid", "manual"}


def row_to_agent_profile(row: sqlite3.Row) -> dict[str, Any]:
    """Map an `agent_profiles` row to its camelCase dict, tolerating older schema columns."""
    runtime_mode = row["runtime_type"]
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "runtimeType": runtime_mode,
        "runtimeMode": runtime_mode,
        "modelPolicyId": row["model_policy_id"],
        "routingProfileId": row["routing_profile_id"] if "routing_profile_id" in row.keys() else None,
        "roleModelPolicyId": row["role_model_policy_id"] if "role_model_policy_id" in row.keys() else None,
        "allowedProviders": json_loads(
            row["allowed_providers"] if "allowed_providers" in row.keys() else "[]", []
        ),
        "allowedRuntimes": json_loads(
            row["allowed_runtimes"] if "allowed_runtimes" in row.keys() else "[]", []
        ),
        "allowedSkills": json_loads(row["allowed_skills"], []),
        "allowedTools": json_loads(row["allowed_tools"], []),
        "permissionProfile": row["permission_profile"],
        "memoryScope": row["memory_scope"],
        "maxCostPerRun": row["max_cost_per_run"],
        "maxTokensPerRun": row["max_tokens_per_run"] if "max_tokens_per_run" in row.keys() else 0,
        "maxRuntimeSeconds": row["max_runtime_seconds"],
        "allowRemote": bool(row["allow_remote"]) if "allow_remote" in row.keys() else True,
        "allowCli": bool(row["allow_cli"]) if "allow_cli" in row.keys() else True,
        "allowApi": bool(row["allow_api"]) if "allow_api" in row.keys() else True,
        "requiresApprovalOverUsd": row["requires_approval_over_usd"]
        if "requires_approval_over_usd" in row.keys()
        else None,
        "outputSchema": json_loads(row["output_schema"]),
        "qualityGates": json_loads(row["quality_gates"], []),
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_model_policy(row: sqlite3.Row) -> dict[str, Any]:
    """Map a `model_policies` row to its camelCase dict."""
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
    """Map an `agent_runs` row to its camelCase dict (input/output/metadata decoded)."""
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
    """Map an `agent_tool_calls` row to its camelCase dict."""
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
    """Map a `model_calls` row to its camelCase dict."""
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
    """Map a `cost_usage` row to its camelCase dict."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "scope": row["scope"],
        "amountUsd": row["amount_usd"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
    }


def row_to_model_provider(row: sqlite3.Row) -> dict[str, Any]:
    """Map a `model_providers` row to its camelCase dict."""
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
    """SQLite repository for agent profiles, policies, runs, and tool/model/cost records."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert_agent_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert or update an agent profile, validating its runtime mode against the catalog.

        Raises:
            ValueError: if the requested runtime mode is not a product runtime mode.
        """
        profile_id = body["id"]
        runtime_type = str(body.get("runtimeMode") or body.get("runtimeType") or "hybrid")
        if runtime_type not in PRODUCT_RUNTIME_MODES:
            raise ValueError(f"Agent profile runtime is not in the product catalog: {runtime_type}")
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO agent_profiles
                (id, name, role, runtime_type, model_policy_id, allowed_skills, allowed_tools,
                 permission_profile, memory_scope, max_cost_per_run, max_runtime_seconds,
                 output_schema, quality_gates, status, routing_profile_id, role_model_policy_id,
                 allowed_providers, allowed_runtimes, max_tokens_per_run, allow_remote, allow_cli,
                 allow_api, requires_approval_over_usd, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                routing_profile_id = excluded.routing_profile_id,
                role_model_policy_id = excluded.role_model_policy_id,
                allowed_providers = excluded.allowed_providers,
                allowed_runtimes = excluded.allowed_runtimes,
                max_tokens_per_run = excluded.max_tokens_per_run,
                allow_remote = excluded.allow_remote,
                allow_cli = excluded.allow_cli,
                allow_api = excluded.allow_api,
                requires_approval_over_usd = excluded.requires_approval_over_usd,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                body.get("name", profile_id),
                body.get("role", "implementer"),
                runtime_type,
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
                body.get("routingProfileId"),
                body.get("roleModelPolicyId"),
                json_dumps(body.get("allowedProviders") or []),
                json_dumps(body.get("allowedRuntimes") or []),
                body.get("maxTokensPerRun", 0),
                1 if body.get("allowRemote", True) else 0,
                1 if body.get("allowCli", True) else 0,
                1 if body.get("allowApi", True) else 0,
                body.get("requiresApprovalOverUsd"),
                timestamp,
                timestamp,
            ),
        )
        return self.get_agent_profile(profile_id)

    def get_agent_profile(self, profile_id: str) -> dict[str, Any]:
        """Fetch one agent profile, rejecting profiles whose runtime mode left the catalog.

        Raises:
            KeyError: if the profile is missing or its runtime mode is no longer supported.
        """
        row = self.connection.execute("SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            raise KeyError(f"Agent profile not found: {profile_id}")
        if row["runtime_type"] not in PRODUCT_RUNTIME_MODES:
            raise KeyError(f"Agent profile runtime is no longer in the product catalog: {profile_id}")
        return row_to_agent_profile(row)

    def list_agent_profiles(self) -> list[dict[str, Any]]:
        """List profiles whose runtime mode is still in the product catalog, ordered by id."""
        runtime_modes = sorted(PRODUCT_RUNTIME_MODES)
        placeholders = ",".join("?" for _ in runtime_modes)
        rows = self.connection.execute(
            f"SELECT * FROM agent_profiles WHERE runtime_type IN ({placeholders}) ORDER BY id ASC",
            tuple(runtime_modes),
        ).fetchall()
        return [row_to_agent_profile(row) for row in rows]

    def upsert_model_policy(self, body: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a model policy keyed by id and return the stored row."""
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
        """Fetch one model policy.

        Raises:
            KeyError: if no policy has the given id.
        """
        row = self.connection.execute("SELECT * FROM model_policies WHERE id = ?", (policy_id,)).fetchone()
        if not row:
            raise KeyError(f"Model policy not found: {policy_id}")
        return row_to_model_policy(row)

    def list_model_policies(self) -> list[dict[str, Any]]:
        """List all model policies ordered by id."""
        rows = self.connection.execute("SELECT * FROM model_policies ORDER BY id ASC").fetchall()
        return [row_to_model_policy(row) for row in rows]

    def list_model_providers(self) -> list[dict[str, Any]]:
        """List all model providers ordered by id."""
        rows = self.connection.execute("SELECT * FROM model_providers ORDER BY id ASC").fetchall()
        return [row_to_model_provider(row) for row in rows]

    def total_cost_usage(self, *, project_id: str, scope: str = "model_call") -> float:
        """Sum recorded cost (USD) for a project within a cost-usage scope."""
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
        """Record a model call and, when cost is non-zero, a paired `cost_usage` row.

        Metadata is redacted before storage. Both inserts run on the caller's connection
        without an explicit commit, so they are atomic only within the caller's transaction.
        """
        timestamp = utc_now()
        call_id = f"model-call-{uuid.uuid4()}"
        clean_metadata = redact_secrets(metadata or {})
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
                json_dumps(clean_metadata),
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
                    json_dumps(redact_secrets({"modelCallId": call_id, "modelPolicyId": model_policy_id})),
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
        """Create an agent-run record (inputs/outputs/metadata redacted) and emit telemetry.

        The `agent_runs` insert and the telemetry write share the caller's transaction;
        no explicit commit is issued here.
        """
        profile = self.get_agent_profile(agent_profile_id)
        clean_input = redact_secrets(input_payload)
        clean_output = redact_secrets(output_payload)
        clean_metadata = redact_secrets(
            {"agentProfileId": agent_profile_id, "taskId": task_id, "runtimeType": profile["runtimeType"]}
        )
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
                json_dumps(clean_input),
                json_dumps(clean_output),
                json_dumps(clean_metadata),
                timestamp,
                timestamp,
            ),
        )
        agent_run = self.get_agent_run(run_id)
        record_agent_run(self.connection, agent_run)
        return agent_run

    def update_agent_run_status(
        self, run_id: str, *, status: str, output_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an agent run's status and (redacted) output, returning the refreshed run."""
        clean_output = redact_secrets(output_payload)
        self.connection.execute(
            """
            UPDATE agent_runs
            SET status = ?, output = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, json_dumps(clean_output), utc_now(), run_id),
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
        """Record a tool call against an agent run with its payload redacted before storage."""
        now = timestamp or utc_now()
        call_id = f"agent-tool-call-{uuid.uuid4()}"
        clean_payload = redact_secrets(payload)
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
                json_dumps(clean_payload),
                now,
                now,
            ),
        )
        row = self.connection.execute("SELECT * FROM agent_tool_calls WHERE id = ?", (call_id,)).fetchone()
        return row_to_agent_tool_call(row)

    def get_agent_run(self, run_id: str) -> dict[str, Any]:
        """Fetch one agent run.

        Raises:
            KeyError: if no run has the given id.
        """
        row = self.connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Agent run not found: {run_id}")
        return row_to_agent_run(row)

    def list_agent_runs(self) -> list[dict[str, Any]]:
        """List all agent runs, newest first."""
        rows = self.connection.execute("SELECT * FROM agent_runs ORDER BY created_at DESC").fetchall()
        return [row_to_agent_run(row) for row in rows]

    def list_agent_runs_for_workflow_runs(self, workflow_run_ids: list[str]) -> list[dict[str, Any]]:
        """List agent runs belonging to the given workflow run ids, newest first."""
        if not workflow_run_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_run_ids)
        rows = self.connection.execute(
            f"SELECT * FROM agent_runs WHERE workflow_run_id IN ({placeholders}) ORDER BY created_at DESC",
            tuple(workflow_run_ids),
        ).fetchall()
        return [row_to_agent_run(row) for row in rows]

    def list_agent_tool_calls(self) -> list[dict[str, Any]]:
        """List all recorded tool calls, newest first."""
        rows = self.connection.execute("SELECT * FROM agent_tool_calls ORDER BY created_at DESC").fetchall()
        return [row_to_agent_tool_call(row) for row in rows]

    def list_model_calls(self) -> list[dict[str, Any]]:
        """List all recorded model calls, newest first."""
        rows = self.connection.execute("SELECT * FROM model_calls ORDER BY created_at DESC").fetchall()
        return [row_to_model_call(row) for row in rows]

    def list_cost_usage(self) -> list[dict[str, Any]]:
        """List all cost-usage entries, newest first."""
        rows = self.connection.execute("SELECT * FROM cost_usage ORDER BY created_at DESC").fetchall()
        return [row_to_cost_usage(row) for row in rows]
