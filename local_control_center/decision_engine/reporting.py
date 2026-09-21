"""Comparación offline de decisión, recomendación y outcome sin atribuir contrafactuales.

Las tasas excluyen observaciones desconocidas y declaran denominadores y ventana.
@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

from .repository import DecisionRepository


def decision_report(
    connection: sqlite3.Connection, *, project_id: str | None = None, after: int = 0, limit: int = 1000
) -> dict:
    """Calcula métricas sobre una página explícita; nunca llama accuracy al agreement."""
    rows = DecisionRepository(connection).list_receipts(project_id=project_id, after=after, limit=limit)
    completed = [row for row in rows if row["status"] == "completed"]
    jev = [row for row in completed if row["provider"] == "jev"]
    comparable = [
        row
        for row in jev
        if row["mode"] == "shadow"
        and row["jevRecommendation"] is not None
        and row["effectiveDecision"] is not None
    ]
    outcomes = [row["outcome"] for row in completed if row["outcome"] is not None]
    latencies = [row["latencyMs"] for row in completed if row["latencyMs"] is not None]

    def rate(values: list[bool]) -> float | None:
        return sum(values) / len(values) if values else None

    def outcome_rate(key: str, *, invert: bool = False) -> float | None:
        return rate(
            [
                not outcome[key] if invert else bool(outcome[key])
                for outcome in outcomes
                if outcome[key] is not None
            ]
        )

    agreement = rate([row["effectiveDecision"] == row["jevRecommendation"] for row in comparable])
    metrics = {
        "decision_count": len(completed),
        "provider_errors": sum(row["providerFailed"] for row in completed),
        "timeouts": sum(row["reasonCode"] == "timeout" for row in completed),
        "timeout_rate": rate([row["reasonCode"] == "timeout" for row in completed]),
        "fallback_rate": rate([row["fallbackUsed"] for row in completed]),
        "agreement_rate": agreement,
        "disagreement_rate": 1 - agreement if agreement is not None else None,
        "decision_latency_ms": {
            "count": len(latencies),
            "average": sum(latencies) / len(latencies) if latencies else None,
            "maximum": max(latencies) if latencies else None,
        },
        "routing_latency_ms": [
            row["routingLatencyMs"] for row in completed if row["routingLatencyMs"] is not None
        ],
        "total_routing_latency_ms": [
            row["totalRoutingLatencyMs"] for row in completed if row.get("totalRoutingLatencyMs") is not None
        ],
        "confidence_distribution": [row["confidence"] for row in jev if row["confidence"] is not None],
        "margin_distribution": [row["margin"] for row in jev if row["margin"] is not None],
        "human_override_rate": outcome_rate("human_override"),
        "success_rate": outcome_rate("execution_succeeded"),
        "retry_rate": outcome_rate("retries"),
        "review_failure_rate": outcome_rate("review_passed", invert=True),
        "test_failure_rate": outcome_rate("tests_passed", invert=True),
    }
    total = connection.execute(
        "SELECT COUNT(*) FROM decision_receipts WHERE (? IS NULL OR project_id=?)", (project_id, project_id)
    ).fetchone()[0]
    return {
        "metrics": metrics,
        "totalReceipts": total,
        "window": {"after": after, "limit": limit, "returned": len(rows)},
        "nextAfter": rows[-1]["sequence"] if rows else after,
        "denominators": {
            "completed": len(completed),
            "agreement": len(comparable),
            **{
                key: sum(item[key] is not None for item in outcomes)
                for key in (
                    "human_override",
                    "execution_succeeded",
                    "retries",
                    "review_passed",
                    "tests_passed",
                )
            },
        },
        "configurationFingerprints": sorted({row["configurationFingerprint"] for row in rows}),
        "comparisons": [dict(row, recommendationOutcome="not_observed") for row in rows],
        "calibration": "not_computed_without_labeled_ground_truth",
    }
