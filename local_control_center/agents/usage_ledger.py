"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_usage(row: sqlite3.Row) -> dict[str, Any]:
    raw_usage = json_loads(row["raw_usage_json"], {})
    usage_source = (
        row["usage_source"] if "usage_source" in row.keys() else _usage_source_from_raw(raw_usage, None)
    )
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "runtimeType": row["runtime_type"],
        "agentId": row["agent_id"],
        "role": row["role"],
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["workflow_step_id"],
        "jobId": row["job_id"],
        "taskId": row["task_id"],
        "requestId": row["request_id"],
        "sessionId": row["session_id"],
        "inputTokens": row["input_tokens"],
        "cachedInputTokens": row["cached_input_tokens"],
        "outputTokens": row["output_tokens"],
        "reasoningTokens": row["reasoning_tokens"],
        "toolTokens": row["tool_tokens"],
        "totalTokens": row["total_tokens"],
        "estimatedCostUsd": row["estimated_cost_usd"],
        "actualCostUsd": row["actual_cost_usd"],
        "currency": row["currency"],
        "latencyMs": row["latency_ms"],
        "usageSource": usage_source,
        "tokenStatus": raw_usage.get("token_status") or ("actual" if usage_source == "actual" else "unknown"),
        "costStatus": raw_usage.get("cost_status")
        or ("actual" if row["actual_cost_usd"] is not None else "unknown"),
        "rawUsage": raw_usage,
        "createdAt": row["created_at"],
    }


def _usage_source_from_raw(raw_usage: dict[str, Any] | None, actual_cost_usd: float | None) -> str:
    raw_source = str((raw_usage or {}).get("usage_source") or "").strip().lower()
    if raw_source in {"actual", "estimated", "unavailable", "unknown"}:
        return raw_source
    if raw_source in {"not_available", "none"}:
        return "unavailable"
    if raw_source in {"provider", "provider_reported", "cli_output"}:
        return "actual"
    if actual_cost_usd is not None:
        return "actual"
    return "estimated"


class UsageLedger:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def record_usage(
        self,
        *,
        provider_id: str,
        model: str,
        runtime_type: str,
        agent_id: str | None = None,
        role: str | None = None,
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
        job_id: str | None = None,
        task_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        tool_tokens: int = 0,
        estimated_cost_usd: float | None = None,
        actual_cost_usd: float | None = None,
        currency: str = "USD",
        latency_ms: int | None = None,
        raw_usage: dict[str, Any] | None = None,
        usage_source: str | None = None,
    ) -> dict[str, Any]:
        total_tokens = input_tokens + cached_input_tokens + output_tokens + reasoning_tokens + tool_tokens
        ledger_id = f"usage-{uuid.uuid4()}"
        sanitized_usage = redact_secrets(raw_usage or {"usage_source": "estimated"})
        resolved_usage_source = usage_source or _usage_source_from_raw(sanitized_usage, actual_cost_usd)
        self.connection.execute(
            """
            INSERT INTO usage_ledger
                (id, provider_id, model, runtime_type, agent_id, role, workflow_run_id, workflow_step_id,
                 job_id, task_id, request_id, session_id, input_tokens, cached_input_tokens, output_tokens,
                 reasoning_tokens, tool_tokens, total_tokens, estimated_cost_usd, actual_cost_usd,
                 currency, latency_ms, raw_usage_json, usage_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ledger_id,
                provider_id,
                model,
                runtime_type,
                agent_id,
                role,
                workflow_run_id,
                workflow_step_id,
                job_id,
                task_id,
                request_id,
                session_id,
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_tokens,
                tool_tokens,
                total_tokens,
                estimated_cost_usd,
                actual_cost_usd,
                currency,
                latency_ms,
                json_dumps(sanitized_usage),
                resolved_usage_source,
                utc_now(),
            ),
        )
        if estimated_cost_usd is not None or actual_cost_usd is not None:
            self.connection.execute(
                """
                INSERT INTO cost_usage (id, project_id, scope, amount_usd, metadata, created_at)
                VALUES (?, NULL, 'model_gateway', ?, ?, ?)
                """,
                (
                    f"cost-{uuid.uuid4()}",
                    float(actual_cost_usd if actual_cost_usd is not None else estimated_cost_usd or 0),
                    json_dumps({"usageLedgerId": ledger_id, "providerId": provider_id, "model": model}),
                    utc_now(),
                ),
            )
        row = self.connection.execute("SELECT * FROM usage_ledger WHERE id = ?", (ledger_id,)).fetchone()
        return row_to_usage(row)

    def list_usage(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM usage_ledger ORDER BY created_at DESC").fetchall()
        return [row_to_usage(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) AS actual_cost
            FROM usage_ledger
            """
        ).fetchone()
        by_provider = self.connection.execute(
            """
            SELECT provider_id, COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost
            FROM usage_ledger
            GROUP BY provider_id
            ORDER BY provider_id ASC
            """
        ).fetchall()
        return {
            "totalTokens": int(row["total_tokens"] or 0),
            "estimatedCostUsd": float(row["estimated_cost"] or 0),
            "actualCostUsd": float(row["actual_cost"] or 0),
            "byProvider": [
                {
                    "providerId": item["provider_id"],
                    "totalTokens": int(item["total_tokens"] or 0),
                    "estimatedCostUsd": float(item["estimated_cost"] or 0),
                }
                for item in by_provider
            ],
        }
