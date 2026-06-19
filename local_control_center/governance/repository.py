"""Persistencia SQLite de gobernanza: ADRs, riesgos y next steps.

Mapea filas a dicts con claves camelCase (contrato del API) y ejecuta las
escrituras (INSERT/UPDATE) sobre la conexión del caller.

Transacciones: la conexión se abre en autocommit (``isolation_level=None``, ver
``shared/db.py``) y estos métodos NO envuelven sus statements; cada ``execute``
confía en el autocommit, de modo que un create se confirma con su único INSERT y
un update con su único UPDATE. No hay atomicidad entre statements: si el caller
necesita agrupar una escritura con su auditoría/evento debe abrir él mismo una
``immediate_transaction``. Las lecturas de ``get_*`` tras la escritura ven el dato
ya confirmado por estar en la misma conexión.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_architecture_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``architecture_decisions`` al dict camelCase del API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "title": row["title"],
        "status": row["status"],
        "context": row["context"],
        "decision": row["decision"],
        "consequences": json_loads(row["consequences"], []),
        "linkedRiskIds": json_loads(row["linked_risk_ids"], []),
        "nextStepIds": json_loads(row["next_step_ids"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_risk(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``risk_register`` al dict camelCase del API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "title": row["title"],
        "severity": row["severity"],
        "status": row["status"],
        "description": row["description"],
        "mitigation": row["mitigation"],
        "owner": row["owner"],
        "evidenceRefs": json_loads(row["evidence_refs"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_next_step(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``next_steps`` al dict camelCase del API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "title": row["title"],
        "status": row["status"],
        "priority": row["priority"],
        "sourceRiskId": row["source_risk_id"],
        "sourceDecisionId": row["source_decision_id"],
        "owner": row["owner"],
        "dueAt": row["due_at"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class GovernanceRepository:
    """Acceso a datos de gobernanza sobre la conexión SQLite del caller (autocommit por statement)."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_architecture_decision(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta una ADR (id ``adr-<uuid>``) y devuelve el registro recién creado."""
        decision_id = f"adr-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO architecture_decisions
                (id, project_id, title, status, context, decision, consequences,
                 linked_risk_ids, next_step_ids, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                body["projectId"],
                body["title"],
                body.get("status", "proposed"),
                body.get("context", ""),
                body.get("decision", ""),
                json_dumps(body.get("consequences") or []),
                json_dumps(body.get("linkedRiskIds") or []),
                json_dumps(body.get("nextStepIds") or []),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_architecture_decision(decision_id)

    def get_architecture_decision(self, decision_id: str) -> dict[str, Any]:
        """Recupera una ADR por id.

        Raises:
            KeyError: si no existe ninguna ADR con ese id.
        """
        row = self.connection.execute(
            "SELECT * FROM architecture_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Architecture decision not found: {decision_id}")
        return row_to_architecture_decision(row)

    def list_architecture_decisions(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista ADRs (todas o filtradas por proyecto), más recientes primero."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM architecture_decisions WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM architecture_decisions ORDER BY updated_at DESC"
            ).fetchall()
        return [row_to_architecture_decision(row) for row in rows]

    def create_risk(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta un riesgo (id ``risk-<uuid>``) y devuelve el registro recién creado."""
        risk_id = f"risk-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO risk_register
                (id, project_id, title, severity, status, description, mitigation, owner,
                 evidence_refs, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                risk_id,
                body["projectId"],
                body["title"],
                body.get("severity", "medium"),
                body.get("status", "open"),
                body.get("description", ""),
                body.get("mitigation", ""),
                body.get("owner", ""),
                json_dumps(body.get("evidenceRefs") or []),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_risk(risk_id)

    def get_risk(self, risk_id: str) -> dict[str, Any]:
        """Recupera un riesgo por id.

        Raises:
            KeyError: si no existe ningún riesgo con ese id.
        """
        row = self.connection.execute("SELECT * FROM risk_register WHERE id = ?", (risk_id,)).fetchone()
        if not row:
            raise KeyError(f"Risk not found: {risk_id}")
        return row_to_risk(row)

    def list_risks(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista riesgos (todos o por proyecto), ordenados por severidad y recencia."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM risk_register WHERE project_id = ? ORDER BY severity DESC, updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM risk_register ORDER BY severity DESC, updated_at DESC"
            ).fetchall()
        return [row_to_risk(row) for row in rows]

    def update_risk(self, risk_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre un riesgo y devuelve el registro actualizado.

        Mezcla ``body`` sobre el estado actual y persiste status, mitigation, owner,
        evidence_refs y metadata; la severidad no se reescribe aquí.

        Raises:
            KeyError: si el riesgo no existe.
        """
        current = self.get_risk(risk_id)
        timestamp = utc_now()
        next_value = {**current, **body}
        self.connection.execute(
            """
            UPDATE risk_register
            SET status = ?, mitigation = ?, owner = ?, evidence_refs = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value.get("status", current["status"]),
                next_value.get("mitigation", current["mitigation"]),
                next_value.get("owner", current["owner"]),
                json_dumps(next_value.get("evidenceRefs") or current["evidenceRefs"]),
                json_dumps(next_value.get("metadata") or current["metadata"]),
                timestamp,
                risk_id,
            ),
        )
        return self.get_risk(risk_id)

    def create_next_step(self, body: dict[str, Any]) -> dict[str, Any]:
        """Inserta un next step (id ``next-step-<uuid>``) y devuelve el registro creado."""
        step_id = f"next-step-{uuid.uuid4()}"
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO next_steps
                (id, project_id, title, status, priority, source_risk_id, source_decision_id,
                 owner, due_at, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                body["projectId"],
                body["title"],
                body.get("status", "planned"),
                body.get("priority", "medium"),
                body.get("sourceRiskId"),
                body.get("sourceDecisionId"),
                body.get("owner", ""),
                body.get("dueAt"),
                json_dumps(body.get("metadata") or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_next_step(step_id)

    def get_next_step(self, step_id: str) -> dict[str, Any]:
        """Recupera un next step por id.

        Raises:
            KeyError: si no existe ningún next step con ese id.
        """
        row = self.connection.execute("SELECT * FROM next_steps WHERE id = ?", (step_id,)).fetchone()
        if not row:
            raise KeyError(f"Next step not found: {step_id}")
        return row_to_next_step(row)

    def list_next_steps(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista next steps (todos o por proyecto), más recientes primero."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM next_steps WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM next_steps ORDER BY updated_at DESC").fetchall()
        return [row_to_next_step(row) for row in rows]

    def update_next_step(self, step_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Aplica un patch sobre un next step y devuelve el registro actualizado.

        Mezcla ``body`` sobre el estado actual y persiste status, priority, owner,
        due_at y metadata.

        Raises:
            KeyError: si el next step no existe.
        """
        current = self.get_next_step(step_id)
        timestamp = utc_now()
        next_value = {**current, **body}
        self.connection.execute(
            """
            UPDATE next_steps
            SET status = ?, priority = ?, owner = ?, due_at = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_value.get("status", current["status"]),
                next_value.get("priority", current["priority"]),
                next_value.get("owner", current["owner"]),
                next_value.get("dueAt", current["dueAt"]),
                json_dumps(next_value.get("metadata") or current["metadata"]),
                timestamp,
                step_id,
            ),
        )
        return self.get_next_step(step_id)
