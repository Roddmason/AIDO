from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.agents.model_gateway import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def _benchmark_key(provider_id: str, model: str, role: str | None) -> str:
    return f"{provider_id}:{model}:{role or '*'}"


def _row_to_benchmark(row: sqlite3.Row) -> dict[str, Any]:
    tasks_attempted = int(row["tasks_attempted"] or 0)
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "role": row["role"],
        "tasksAttempted": tasks_attempted,
        "successRate": row["success_rate"],
        "qaPassRate": row["qa_pass_rate"],
        "avgCost": row["avg_cost"],
        "avgLatencyMs": row["avg_latency_ms"],
        "reworkRate": row["rework_rate"],
        "lastUsedAt": row["last_used_at"],
        "insufficientData": tasks_attempted < 3 or row["success_rate"] is None,
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _row_to_outcome(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "runtimeType": row["runtime_type"],
        "role": row["role"],
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["workflow_step_id"],
        "agentId": row["agent_id"],
        "jobId": row["job_id"],
        "taskId": row["task_id"],
        "usageLedgerId": row["usage_ledger_id"],
        "success": _optional_bool(row["success"]),
        "qaPass": _optional_bool(row["qa_pass"]),
        "rework": _optional_bool(row["rework"]),
        "estimatedCostUsd": row["estimated_cost_usd"],
        "actualCostUsd": row["actual_cost_usd"],
        "latencyMs": row["latency_ms"],
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
    }


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(int(value))


class ModelBenchmarkStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_benchmarks(self) -> list[dict[str, Any]]:
        explicit_rows = self.connection.execute(
            "SELECT * FROM model_benchmarks ORDER BY provider_id ASC, model ASC, role ASC"
        ).fetchall()
        benchmarks = [_row_to_benchmark(row) for row in explicit_rows]
        seen = {_benchmark_key(item["providerId"], item["model"], item["role"]) for item in benchmarks}
        outcome_rows = self.connection.execute(
            """
            SELECT provider_id,
                   model,
                   role,
                   COUNT(*) AS tasks_attempted,
                   AVG(CASE WHEN success IS NULL THEN NULL ELSE success END) AS success_rate,
                   AVG(CASE WHEN qa_pass IS NULL THEN NULL ELSE qa_pass END) AS qa_pass_rate,
                   AVG(CASE WHEN rework IS NULL THEN NULL ELSE rework END) AS rework_rate,
                   AVG(COALESCE(actual_cost_usd, estimated_cost_usd)) AS avg_cost,
                   AVG(latency_ms) AS avg_latency_ms,
                   MAX(created_at) AS last_used_at
            FROM model_benchmark_outcomes
            GROUP BY provider_id, model, role
            ORDER BY provider_id ASC, model ASC, role ASC
            """
        ).fetchall()
        now = utc_now()
        for row in outcome_rows:
            key = _benchmark_key(row["provider_id"], row["model"], row["role"])
            if key in seen:
                continue
            tasks_attempted = int(row["tasks_attempted"] or 0)
            benchmarks.append(
                {
                    "id": f"outcome-derived:{key}",
                    "providerId": row["provider_id"],
                    "model": row["model"],
                    "role": row["role"],
                    "tasksAttempted": tasks_attempted,
                    "successRate": row["success_rate"],
                    "qaPassRate": row["qa_pass_rate"],
                    "avgCost": row["avg_cost"],
                    "avgLatencyMs": int(row["avg_latency_ms"]) if row["avg_latency_ms"] is not None else None,
                    "reworkRate": row["rework_rate"],
                    "lastUsedAt": row["last_used_at"],
                    "insufficientData": tasks_attempted < 3,
                    "metadata": {"source": "model_benchmark_outcomes"},
                    "createdAt": row["last_used_at"] or now,
                    "updatedAt": row["last_used_at"] or now,
                }
            )
            seen.add(key)

        usage_rows = self.connection.execute(
            """
            SELECT provider_id,
                   model,
                   role,
                   COUNT(*) AS tasks_attempted,
                   AVG(COALESCE(actual_cost_usd, estimated_cost_usd)) AS avg_cost,
                   AVG(latency_ms) AS avg_latency_ms,
                   MAX(created_at) AS last_used_at
            FROM usage_ledger
            GROUP BY provider_id, model, role
            ORDER BY provider_id ASC, model ASC, role ASC
            """
        ).fetchall()
        for row in usage_rows:
            key = _benchmark_key(row["provider_id"], row["model"], row["role"])
            if key in seen:
                continue
            tasks_attempted = int(row["tasks_attempted"] or 0)
            benchmarks.append(
                {
                    "id": f"usage-derived:{key}",
                    "providerId": row["provider_id"],
                    "model": row["model"],
                    "role": row["role"],
                    "tasksAttempted": tasks_attempted,
                    "successRate": None,
                    "qaPassRate": None,
                    "avgCost": row["avg_cost"],
                    "avgLatencyMs": int(row["avg_latency_ms"]) if row["avg_latency_ms"] is not None else None,
                    "reworkRate": None,
                    "lastUsedAt": row["last_used_at"],
                    "insufficientData": True,
                    "metadata": {"source": "usage_ledger", "outcome_metrics": "not_collected"},
                    "createdAt": row["last_used_at"] or now,
                    "updatedAt": row["last_used_at"] or now,
                }
            )
        return benchmarks

    def record_outcome(self, body: dict[str, Any]) -> dict[str, Any]:
        outcome_id = str(body.get("id") or f"benchmark-outcome-{uuid.uuid4()}")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO model_benchmark_outcomes
                (id, provider_id, model, runtime_type, role, workflow_run_id, workflow_step_id,
                 agent_id, job_id, task_id, usage_ledger_id, success, qa_pass, rework,
                 estimated_cost_usd, actual_cost_usd, latency_ms, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome_id,
                body["providerId"],
                body["model"],
                body.get("runtimeType", "api"),
                body.get("role"),
                body.get("workflowRunId"),
                body.get("workflowStepId"),
                body.get("agentId"),
                body.get("jobId"),
                body.get("taskId"),
                body.get("usageLedgerId"),
                _bool_to_int(body.get("success")),
                _bool_to_int(body.get("qaPass")),
                _bool_to_int(body.get("rework")),
                body.get("estimatedCostUsd"),
                body.get("actualCostUsd"),
                body.get("latencyMs"),
                json_dumps(redact_secrets(body.get("metadata") or {})),
                now,
            ),
        )
        row = self.connection.execute("SELECT * FROM model_benchmark_outcomes WHERE id = ?", (outcome_id,)).fetchone()
        return _row_to_outcome(row)

    def record_evidence_outcome(
        self,
        *,
        evidence: dict[str, Any],
        payload: dict[str, Any],
        test_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        usage = self._usage_for_outcome(payload.get("usageLedgerId"))
        provider_id = payload.get("providerId") or (usage or {}).get("providerId")
        model = payload.get("model") or (usage or {}).get("model")
        if not provider_id or not model:
            return None
        usage_ledger_id = payload.get("usageLedgerId") or (usage or {}).get("id")
        if usage_ledger_id and self._outcome_exists_for_usage(usage_ledger_id):
            return None
        verdict = str(evidence.get("qaVerdict") or "").lower()
        failed_statuses = {"failed", "error", "denied", "blocked", "timeout"}
        any_failed = any(str(result.get("status") or "").lower() in failed_statuses for result in test_results)
        qa_pass = True if verdict == "passed" else False if verdict in {"failed", "blocked", "needs_human_review"} or any_failed else None
        success = True if qa_pass is True else False if qa_pass is False else None
        rework = False if qa_pass is True else True if qa_pass is False else None
        return self.record_outcome(
            {
                "providerId": provider_id,
                "model": model,
                "runtimeType": payload.get("runtimeType") or (usage or {}).get("runtimeType") or "api",
                "role": payload.get("role") or (usage or {}).get("role"),
                "workflowRunId": evidence.get("workflowRunId") or (usage or {}).get("workflowRunId"),
                "workflowStepId": payload.get("workflowStepId") or (usage or {}).get("workflowStepId"),
                "agentId": evidence.get("agentId") or (usage or {}).get("agentId"),
                "jobId": payload.get("jobId") or (usage or {}).get("jobId"),
                "taskId": evidence.get("taskId") or (usage or {}).get("taskId"),
                "usageLedgerId": usage_ledger_id,
                "success": success,
                "qaPass": qa_pass,
                "rework": rework if payload.get("rework") is None else payload.get("rework"),
                "estimatedCostUsd": _value_or_default(payload.get("estimatedCostUsd"), (usage or {}).get("estimatedCostUsd")),
                "actualCostUsd": _value_or_default(payload.get("actualCostUsd"), (usage or {}).get("actualCostUsd")),
                "latencyMs": _value_or_default(payload.get("latencyMs"), (usage or {}).get("latencyMs")),
                "metadata": {
                    "source": "evidence_ingestion",
                    "evidencePackageId": evidence.get("id"),
                    "qaVerdict": evidence.get("qaVerdict"),
                },
            }
        )

    def list_outcomes(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM model_benchmark_outcomes ORDER BY created_at DESC").fetchall()
        return [_row_to_outcome(row) for row in rows]

    def _usage_for_outcome(self, usage_ledger_id: Any) -> dict[str, Any] | None:
        if not usage_ledger_id:
            return None
        row = self.connection.execute("SELECT * FROM usage_ledger WHERE id = ?", (str(usage_ledger_id),)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "providerId": row["provider_id"],
            "model": row["model"],
            "runtimeType": row["runtime_type"],
            "role": row["role"],
            "workflowRunId": row["workflow_run_id"],
            "workflowStepId": row["workflow_step_id"],
            "agentId": row["agent_id"],
            "jobId": row["job_id"],
            "taskId": row["task_id"],
            "estimatedCostUsd": row["estimated_cost_usd"],
            "actualCostUsd": row["actual_cost_usd"],
            "latencyMs": row["latency_ms"],
        }

    def _outcome_exists_for_usage(self, usage_ledger_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM model_benchmark_outcomes WHERE usage_ledger_id = ? LIMIT 1",
            (usage_ledger_id,),
        ).fetchone()
        return row is not None


def _bool_to_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _value_or_default(value: Any, default: Any) -> Any:
    return default if value is None else value
