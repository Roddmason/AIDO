from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.shared.serialization import json_loads
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


class ModelBenchmarkStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_benchmarks(self) -> list[dict[str, Any]]:
        explicit_rows = self.connection.execute(
            "SELECT * FROM model_benchmarks ORDER BY provider_id ASC, model ASC, role ASC"
        ).fetchall()
        benchmarks = [_row_to_benchmark(row) for row in explicit_rows]
        seen = {_benchmark_key(item["providerId"], item["model"], item["role"]) for item in benchmarks}

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
        now = utc_now()
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
