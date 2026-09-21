"""Receipts y outcomes durables con finalización condicional e idempotencia explícita.

Cada escritura es una sentencia atómica en autocommit; no modifica ejecuciones,
grants, cuotas ni reservas. Nunca permite sustituir un receipt ya finalizado.
@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from local_control_center.shared.time import utc_now

from .models import DecisionOutcome


def _json(payload: dict) -> str:
    return json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))


class DecisionRepository:
    """Persiste evidencia sin abrir transacciones que abarquen llamadas externas."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def start(self, receipt: dict[str, Any]) -> None:
        """Registra el intento antes de llamar al proveedor para conservar fallos/crashes."""
        self.connection.execute(
            """INSERT INTO decision_receipts
            (id, project_id, source_decision_id, configuration_fingerprint, status, payload, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (
                receipt["decisionId"],
                receipt["projectId"],
                receipt["sourceDecisionId"],
                receipt["configurationFingerprint"],
                _json(receipt),
                receipt["timestamp"],
            ),
        )

    def finish(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """Finaliza una sola vez; rechaza replay o sustitución de evidencia histórica."""
        cursor = self.connection.execute(
            "UPDATE decision_receipts SET status=?, payload=? WHERE id=? AND status='pending'",
            (receipt["status"], _json(receipt), receipt["decisionId"]),
        )
        if cursor.rowcount != 1:
            raise ValueError("decision_already_finalized")
        return self.get(receipt["decisionId"])

    def get(self, decision_id: str) -> dict[str, Any]:
        """Obtiene una evidencia sin lanzar evaluación o efectos externos."""
        row = self.connection.execute(
            "SELECT payload FROM decision_receipts WHERE id=?", (decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return json.loads(row["payload"])

    def record_outcome(self, decision_id: str, outcome: DecisionOutcome) -> None:
        """Añade mediciones observadas; sólo permite reintentar el mismo outcome exacto."""
        receipt = self.get(decision_id)
        if receipt["status"] != "completed" or receipt["effectiveDecision"] is None:
            raise ValueError("decision_has_no_effective_outcome")
        serialized = _json(outcome.model_dump())
        self.connection.execute(
            "INSERT OR IGNORE INTO decision_outcomes(decision_id, payload, observed_at) VALUES (?, ?, ?)",
            (decision_id, serialized, utc_now()),
        )
        stored = self.connection.execute(
            "SELECT payload FROM decision_outcomes WHERE decision_id=?", (decision_id,)
        ).fetchone()
        if stored["payload"] != serialized:
            raise ValueError("outcome_already_observed")

    def list_receipts(self, *, project_id: str | None = None, after: int = 0, limit: int = 100) -> list[dict]:
        """Pagina por secuencia durable; incluye outcome sólo si se observó realmente."""
        if not 1 <= limit <= 1000 or after < 0:
            raise ValueError("invalid_page")
        rows = self.connection.execute(
            """SELECT d.sequence, d.payload, o.payload AS outcome
            FROM decision_receipts d LEFT JOIN decision_outcomes o ON o.decision_id=d.id
            WHERE d.sequence>? AND (? IS NULL OR d.project_id=?) ORDER BY d.sequence LIMIT ?""",
            (after, project_id, project_id, limit),
        ).fetchall()
        return [
            dict(
                json.loads(row["payload"]),
                sequence=row["sequence"],
                outcome=json.loads(row["outcome"]) if row["outcome"] else None,
            )
            for row in rows
        ]

    def health(self, fingerprint: str) -> dict:
        """Lee el breaker de esta configuración, separado de salud generativa y permisos."""
        row = self.connection.execute(
            "SELECT failures, open_until FROM decision_provider_health WHERE configuration_fingerprint=?",
            (fingerprint,),
        ).fetchone()
        return dict(row) if row else {"failures": 0, "open_until": 0.0}

    def provider_result(
        self, fingerprint: str, *, failed: bool, now: float, threshold: int, cooldown: float
    ) -> None:
        """Cuenta fallos atómicamente entre procesos y abre una pausa de inferencia acotada."""
        if not failed:
            self.connection.execute(
                "DELETE FROM decision_provider_health WHERE configuration_fingerprint=?", (fingerprint,)
            )
            return
        self.connection.execute(
            """INSERT INTO decision_provider_health(configuration_fingerprint, failures, open_until)
            VALUES (?, 1, CASE WHEN ?<=1 THEN ? ELSE 0 END)
            ON CONFLICT(configuration_fingerprint) DO UPDATE SET
            failures=failures+1,
            open_until=CASE WHEN failures+1>=? THEN ? ELSE open_until END""",
            (fingerprint, threshold, now + cooldown, threshold, now + cooldown),
        )
