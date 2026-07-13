"""Records per-call token usage and cost in the usage ledger with redaction.

Persists one `usage_ledger` row per model/runtime call and, when a cost is known, a
paired `cost_usage` row, sanitizing raw usage payloads before storage. Also derives a
trustworthy `usageSource`/`tokenStatus`/`costStatus` so downstream summaries can tell
provider-reported usage from estimates.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_usage(row: sqlite3.Row) -> dict[str, Any]:
    """Map a `usage_ledger` row to the camelCase usage dict, deriving usage/token/cost status."""
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


def _usage_source_from_raw(raw_usage: dict[str, Any] | None, _actual_cost_usd: float | None) -> str:
    raw_source = str((raw_usage or {}).get("usage_source") or "").strip().lower()
    if raw_source in {"actual", "estimated", "unavailable", "unknown"}:
        return raw_source
    if raw_source in {"not_available", "none"}:
        return "unavailable"
    if raw_source in {"provider", "provider_reported", "cli_output"}:
        return "actual"
    return "estimated"


class UsageLedger:
    """Append-only ledger of model/runtime token usage and cost."""

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
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        tool_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        actual_cost_usd: float | None = None,
        currency: str = "USD",
        latency_ms: int | None = None,
        raw_usage: dict[str, Any] | None = None,
        usage_source: str | None = None,
    ) -> dict[str, Any]:
        """Insert a ledger row (and a cost_usage row when cost is known) and return it.

        Raw usage is redacted before storage. The two inserts are emitted on the caller's
        connection without an explicit commit, so they are atomic only within the caller's
        transaction.
        """
        ledger_id = f"usage-{uuid.uuid4()}"
        sanitized_usage = redact_secrets(raw_usage or {"usage_source": "estimated"})
        resolved_usage_source = usage_source or _usage_source_from_raw(sanitized_usage, actual_cost_usd)
        if resolved_usage_source == "actual":
            stored_tokens = (
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_tokens,
                tool_tokens,
            )
            if total_tokens is not None:
                stored_total_tokens = int(total_tokens)
            elif input_tokens is not None or output_tokens is not None:
                stored_total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
            else:
                stored_total_tokens = sum(int(value or 0) for value in stored_tokens)
        else:
            stored_tokens = (None, None, None, None, None)
            stored_total_tokens = None
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
                *stored_tokens,
                stored_total_tokens,
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
        """Return all ledger entries, newest first."""
        rows = self.connection.execute("SELECT * FROM usage_ledger ORDER BY created_at DESC").fetchall()
        return [row_to_usage(row) for row in rows]

    def list_usage_for_task_prefixes(self, task_prefixes: list[str]) -> list[dict[str, Any]]:
        """Return ledger entries whose ``task_id`` starts with any prefix, newest first.

        Scopes a thread's usage by its product-loop ``task_id`` prefixes (the ledger has no
        ``thread_id`` column). Prefixes are internally derived loop ids that never contain LIKE
        wildcards. Returns an empty list when no prefixes are given, so an unrun thread yields no
        fabricated rows.
        """
        prefixes = [prefix for prefix in task_prefixes if prefix]
        if not prefixes:
            return []
        clause = " OR ".join("task_id LIKE ?" for _ in prefixes)
        params = [f"{prefix}%" for prefix in prefixes]
        rows = self.connection.execute(
            f"SELECT * FROM usage_ledger WHERE {clause} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [row_to_usage(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        """Aggregate total tokens and estimated/actual cost overall and per provider."""
        row = self.connection.execute(
            """
            SELECT SUM(total_tokens) AS total_tokens,
                   SUM(estimated_cost_usd) AS estimated_cost,
                   SUM(actual_cost_usd) AS actual_cost,
                   COUNT(*) AS total_rows,
                   SUM(CASE WHEN usage_source = 'actual' THEN 0 ELSE 1 END) AS unknown_token_rows,
                   COUNT(estimated_cost_usd) AS estimated_cost_rows,
                   COUNT(actual_cost_usd) AS actual_cost_rows
            FROM usage_ledger
            """
        ).fetchone()
        by_provider = self.connection.execute(
            """
            SELECT provider_id, SUM(total_tokens) AS total_tokens,
                   SUM(estimated_cost_usd) AS estimated_cost,
                   COUNT(*) AS total_rows,
                   SUM(CASE WHEN usage_source = 'actual' THEN 0 ELSE 1 END) AS unknown_token_rows,
                   COUNT(estimated_cost_usd) AS estimated_cost_rows
            FROM usage_ledger
            GROUP BY provider_id
            ORDER BY provider_id ASC
            """
        ).fetchall()
        actual_cost_usd = (
            float(row["actual_cost"] or 0)
            if int(row["actual_cost_rows"] or 0) == int(row["total_rows"] or 0)
            else None
        )
        total_rows = int(row["total_rows"] or 0)
        estimated_cost_usd = (
            0.0
            if total_rows == 0
            else float(row["estimated_cost"] or 0)
            if int(row["estimated_cost_rows"] or 0) == total_rows
            else None
        )
        total_tokens = (
            0
            if total_rows == 0
            else None
            if int(row["unknown_token_rows"] or 0) > 0
            else int(row["total_tokens"] or 0)
        )
        return {
            "totalTokens": total_tokens,
            "estimatedCostUsd": estimated_cost_usd,
            "actualCostUsd": actual_cost_usd,
            "byProvider": [
                {
                    "providerId": item["provider_id"],
                    "totalTokens": None
                    if int(item["unknown_token_rows"] or 0) > 0
                    else int(item["total_tokens"] or 0),
                    "estimatedCostUsd": float(item["estimated_cost"] or 0)
                    if int(item["estimated_cost_rows"] or 0) == int(item["total_rows"] or 0)
                    else None,
                }
                for item in by_provider
            ],
        }
