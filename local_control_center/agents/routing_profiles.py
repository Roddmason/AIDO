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


def _bool(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, int) else bool(value)


def row_to_routing_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "mode": row["mode"],
        "objective": row["objective"],
        "rules": json_loads(row["rules_json"], {}),
        "enabled": _bool(row["enabled"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_role_policy(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "routingProfileId": row["routing_profile_id"],
        "preferred": json_loads(row["preferred_json"], []),
        "fallback": json_loads(row["fallback_json"], []),
        "escalation": json_loads(row["escalation_json"], []),
        "blocked": json_loads(row["blocked_json"], []),
        "maxCostPerTaskUsd": row["max_cost_per_task_usd"],
        "maxTokensPerRun": row["max_tokens_per_run"],
        "requiresApprovalOverUsd": row["requires_approval_over_usd"],
        "requiresApprovalForReasoningMax": _bool(row["requires_approval_for_reasoning_max"]),
        "allowRemote": _bool(row["allow_remote"]),
        "allowLocal": _bool(row["allow_local"]),
        "allowCli": _bool(row["allow_cli"]),
        "allowApi": _bool(row["allow_api"]),
        "allowUnknownCost": _bool(row["allow_unknown_cost"]) if "allow_unknown_cost" in row.keys() else True,
        "requireApprovalForUnknownCost": _bool(row["require_approval_for_unknown_cost"])
        if "require_approval_for_unknown_cost" in row.keys()
        else True,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_provider_limit(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "rpm": row["rpm"],
        "tpm": row["tpm"],
        "dailyRequests": row["daily_requests"],
        "dailyTokens": row["daily_tokens"],
        "monthlyRequests": row["monthly_requests"],
        "monthlyTokens": row["monthly_tokens"],
        "monthlyBudgetUsd": row["monthly_budget_usd"],
        "currentWindow": json_loads(row["current_window_json"], {}),
        "cooldownUntil": row["cooldown_until"],
        "last429At": row["last_429_at"],
        "lastLimitErrorAt": row["last_limit_error_at"],
        "unknownLimitStrategy": row["unknown_limit_strategy"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_budget_rule(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scopeType": row["scope_type"],
        "scopeId": row["scope_id"],
        "maxCostUsd": row["max_cost_usd"],
        "maxTokens": row["max_tokens"],
        "period": row["period"],
        "actionOnExceed": row["action_on_exceed"],
        "enabled": _bool(row["enabled"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_routing_decision(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "taskType": row["task_type"],
        "mode": row["mode"],
        "selectedProvider": row["selected_provider"],
        "selectedModel": row["selected_model"],
        "selectedRuntime": row["selected_runtime"],
        "selectedEffort": row["selected_effort"],
        "workflowRunId": row["workflow_run_id"] if "workflow_run_id" in row.keys() else None,
        "workflowStepId": row["workflow_step_id"] if "workflow_step_id" in row.keys() else None,
        "agentId": row["agent_id"] if "agent_id" in row.keys() else None,
        "jobId": row["job_id"] if "job_id" in row.keys() else None,
        "taskId": row["task_id"] if "task_id" in row.keys() else None,
        "estimatedCostUsd": row["estimated_cost_usd"],
        "estimatedTokens": row["estimated_tokens"],
        "candidates": json_loads(row["candidates_json"], []),
        "rejected": json_loads(row["rejected_json"], []),
        "decisionReason": row["decision_reason"],
        "scoreBreakdown": json_loads(row["score_breakdown_json"], {}),
        "policyResult": json_loads(row["policy_result_json"], {}),
        "createdAt": row["created_at"],
    }


def row_to_cli_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runtime": row["runtime"],
        "executable": row["executable"],
        "workspaceId": row["workspace_id"],
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["workflow_step_id"],
        "agentId": row["agent_id"],
        "command": json_loads(row["command_json"], []),
        "envPolicy": json_loads(row["env_policy_json"], {}),
        "status": row["status"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "usageLedgerId": row["usage_ledger_id"],
        "stdoutArtifactId": row["stdout_artifact_id"],
        "stderrArtifactId": row["stderr_artifact_id"],
        "logsArtifactId": row["logs_artifact_id"],
        "error": row["error"],
        "createdAt": row["created_at"],
    }


class RoutingProfileStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_routing_profiles(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM routing_profiles ORDER BY id ASC").fetchall()
        return [row_to_routing_profile(row) for row in rows]

    def get_routing_profile(self, profile_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM routing_profiles WHERE id = ? OR name = ?", (profile_id, profile_id)).fetchone()
        if not row:
            raise KeyError(f"Routing profile not found: {profile_id}")
        return row_to_routing_profile(row)

    def upsert_routing_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(body.get("id") or body["name"])
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO routing_profiles (id, name, mode, objective, rules_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                mode = excluded.mode,
                objective = excluded.objective,
                rules_json = excluded.rules_json,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                body.get("name") or profile_id,
                body.get("mode") or body.get("name") or profile_id,
                body.get("objective", ""),
                json_dumps(body.get("rules") or {}),
                1 if body.get("enabled", True) else 0,
                now,
                now,
            ),
        )
        return self.get_routing_profile(profile_id)

    def patch_routing_profile(self, profile_id: str, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_routing_profile(profile_id)
        return self.upsert_routing_profile({**existing, **body, "id": existing["id"]})

    def list_role_policies(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM role_model_policies ORDER BY role ASC").fetchall()
        return [row_to_role_policy(row) for row in rows]

    def get_role_policy(self, role: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM role_model_policies WHERE role = ? OR id = ?", (role, role)).fetchone()
        if not row:
            raise KeyError(f"Role policy not found: {role}")
        return row_to_role_policy(row)

    def upsert_role_policy(self, body: dict[str, Any]) -> dict[str, Any]:
        role = str(body["role"])
        policy_id = str(body.get("id") or role)
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO role_model_policies
                (id, role, routing_profile_id, preferred_json, fallback_json, escalation_json, blocked_json,
                 max_cost_per_task_usd, max_tokens_per_run, requires_approval_over_usd,
                 requires_approval_for_reasoning_max, allow_remote, allow_local, allow_cli, allow_api,
                 allow_unknown_cost, require_approval_for_unknown_cost, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(role) DO UPDATE SET
                routing_profile_id = excluded.routing_profile_id,
                preferred_json = excluded.preferred_json,
                fallback_json = excluded.fallback_json,
                escalation_json = excluded.escalation_json,
                blocked_json = excluded.blocked_json,
                max_cost_per_task_usd = excluded.max_cost_per_task_usd,
                max_tokens_per_run = excluded.max_tokens_per_run,
                requires_approval_over_usd = excluded.requires_approval_over_usd,
                requires_approval_for_reasoning_max = excluded.requires_approval_for_reasoning_max,
                allow_remote = excluded.allow_remote,
                allow_local = excluded.allow_local,
                allow_cli = excluded.allow_cli,
                allow_api = excluded.allow_api,
                allow_unknown_cost = excluded.allow_unknown_cost,
                require_approval_for_unknown_cost = excluded.require_approval_for_unknown_cost,
                updated_at = excluded.updated_at
            """,
            (
                policy_id,
                role,
                body.get("routingProfileId") or body.get("routing_profile_id") or body.get("mode") or "balanced_best_value",
                json_dumps(body.get("preferred") or []),
                json_dumps(body.get("fallback") or []),
                json_dumps(body.get("escalation") or []),
                json_dumps(body.get("blocked") or []),
                body.get("maxCostPerTaskUsd", 0),
                body.get("maxTokensPerRun", 0),
                body.get("requiresApprovalOverUsd"),
                1 if body.get("requiresApprovalForReasoningMax", False) else 0,
                1 if body.get("allowRemote", True) else 0,
                1 if body.get("allowLocal", True) else 0,
                1 if body.get("allowCli", True) else 0,
                1 if body.get("allowApi", True) else 0,
                1 if body.get("allowUnknownCost", True) else 0,
                1 if body.get("requireApprovalForUnknownCost", True) else 0,
                now,
                now,
            ),
        )
        return self.get_role_policy(role)

    def patch_role_policy(self, policy_id: str, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_role_policy(policy_id)
        return self.upsert_role_policy({**existing, **body, "id": existing["id"], "role": existing["role"]})

    def list_provider_limits(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM provider_limits ORDER BY provider_id ASC, model ASC").fetchall()
        return [row_to_provider_limit(row) for row in rows]

    def patch_provider_limit(self, limit_id: str, body: dict[str, Any]) -> dict[str, Any]:
        existing_row = self.connection.execute("SELECT * FROM provider_limits WHERE id = ?", (limit_id,)).fetchone()
        if not existing_row:
            raise KeyError(f"Provider limit not found: {limit_id}")
        existing = row_to_provider_limit(existing_row)
        merged = {**existing, **body}
        now = utc_now()
        self.connection.execute(
            """
            UPDATE provider_limits
            SET rpm = ?, tpm = ?, daily_requests = ?, daily_tokens = ?, monthly_requests = ?,
                monthly_tokens = ?, monthly_budget_usd = ?, current_window_json = ?, cooldown_until = ?,
                last_429_at = ?, last_limit_error_at = ?, unknown_limit_strategy = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                merged.get("rpm"),
                merged.get("tpm"),
                merged.get("dailyRequests"),
                merged.get("dailyTokens"),
                merged.get("monthlyRequests"),
                merged.get("monthlyTokens"),
                merged.get("monthlyBudgetUsd"),
                json_dumps(merged.get("currentWindow") or {}),
                merged.get("cooldownUntil"),
                merged.get("last429At"),
                merged.get("lastLimitErrorAt"),
                merged.get("unknownLimitStrategy", "conservative"),
                now,
                limit_id,
            ),
        )
        return row_to_provider_limit(self.connection.execute("SELECT * FROM provider_limits WHERE id = ?", (limit_id,)).fetchone())

    def list_budget_rules(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM budget_rules ORDER BY scope_type ASC, scope_id ASC").fetchall()
        return [row_to_budget_rule(row) for row in rows]

    def upsert_budget_rule(self, body: dict[str, Any]) -> dict[str, Any]:
        rule_id = str(body.get("id") or f"budget-{uuid.uuid4()}")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO budget_rules
                (id, scope_type, scope_id, max_cost_usd, max_tokens, period, action_on_exceed, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                scope_type = excluded.scope_type,
                scope_id = excluded.scope_id,
                max_cost_usd = excluded.max_cost_usd,
                max_tokens = excluded.max_tokens,
                period = excluded.period,
                action_on_exceed = excluded.action_on_exceed,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                rule_id,
                body.get("scopeType", "global"),
                body.get("scopeId"),
                body.get("maxCostUsd"),
                body.get("maxTokens"),
                body.get("period", "month"),
                body.get("actionOnExceed", "require_approval"),
                1 if body.get("enabled", True) else 0,
                now,
                now,
            ),
        )
        row = self.connection.execute("SELECT * FROM budget_rules WHERE id = ?", (rule_id,)).fetchone()
        return row_to_budget_rule(row)

    def patch_budget_rule(self, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM budget_rules WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            raise KeyError(f"Budget rule not found: {rule_id}")
        return self.upsert_budget_rule({**row_to_budget_rule(row), **body, "id": rule_id})

    def record_routing_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        decision_id = str(decision.get("id") or f"routing-decision-{uuid.uuid4()}")
        self.connection.execute(
            """
            INSERT INTO routing_decisions
                (id, role, task_type, mode, selected_provider, selected_model, selected_runtime, selected_effort,
                 workflow_run_id, workflow_step_id, agent_id, job_id, task_id,
                 estimated_cost_usd, estimated_tokens, candidates_json, rejected_json, decision_reason,
                 score_breakdown_json, policy_result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                decision.get("role", ""),
                decision.get("taskType", ""),
                decision.get("mode", ""),
                decision.get("selectedProvider"),
                decision.get("selectedModel"),
                decision.get("selectedRuntime"),
                decision.get("selectedEffort"),
                decision.get("workflowRunId"),
                decision.get("workflowStepId"),
                decision.get("agentId"),
                decision.get("jobId"),
                decision.get("taskId"),
                decision.get("estimatedCostUsd"),
                decision.get("estimatedTokens"),
                json_dumps(decision.get("candidates") or []),
                json_dumps(decision.get("rejected") or []),
                decision.get("decisionReason", ""),
                json_dumps(decision.get("scoreBreakdown") or {}),
                json_dumps(decision.get("policyResult") or {}),
                utc_now(),
            ),
        )
        row = self.connection.execute("SELECT * FROM routing_decisions WHERE id = ?", (decision_id,)).fetchone()
        return row_to_routing_decision(row)

    def list_routing_decisions(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM routing_decisions ORDER BY created_at DESC").fetchall()
        return [row_to_routing_decision(row) for row in rows]

    def list_cli_sessions(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM cli_sessions ORDER BY created_at DESC").fetchall()
        return [row_to_cli_session(row) for row in rows]

    def get_cli_session(self, session_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM cli_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise KeyError(f"CLI session not found: {session_id}")
        return row_to_cli_session(row)
