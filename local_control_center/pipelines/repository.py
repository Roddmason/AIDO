"""Persistencia SQLite de los pipelines: alta, lectura por id y listado por proyecto.

Traduce filas de la tabla `pipelines` a/desde dicts con claves camelCase. La conexión recibida
opera en autocommit (`isolation_level=None`): cada `execute` confirma de forma independiente y el
repositorio no abre transacciones. `create_pipeline` hace un `INSERT` y luego un `SELECT` (vía
`get_pipeline`) en dos statements separados, por lo que no es una operación atómica.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

DEFAULT_STAGES = [
    {"name": "idea_intake", "status": "pending"},
    {"name": "project_discovery", "status": "pending"},
    {"name": "implementation", "status": "pending"},
    {"name": "qa_validation", "status": "pending"},
    {"name": "technical_review", "status": "pending"},
]


def row_to_pipeline(row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de `pipelines` al dict camelCase del contrato, deserializando JSON."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "sessionId": row["session_id"],
        "chatId": row["chat_id"],
        "title": row["title"],
        "status": row["status"],
        "stages": json_loads(row["stages"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class PipelinesRepository:
    """Acceso a la tabla `pipelines` sobre la conexión SQLite del caller (en autocommit)."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_pipeline(
        self,
        *,
        project_id: str,
        title: str,
        session_id: str | None = None,
        chat_id: str | None = None,
        stages: list[dict[str, Any]] | None = None,
        status: str = "queued",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inserta un pipeline `queued` (etapas por defecto si no se pasan) y lo relee ya persistido.

        Genera id (`pipeline-<uuid>`) y timestamps. El `INSERT` se autocommitea por sí solo y la
        relectura va en un `SELECT` aparte, así que la pareja escritura+lectura no es atómica.
        """
        timestamp = utc_now()
        pipeline_id = f"pipeline-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO pipelines
                (id, project_id, session_id, chat_id, title, status, stages, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pipeline_id,
                project_id,
                session_id,
                chat_id,
                title,
                status,
                json_dumps(stages or DEFAULT_STAGES),
                json_dumps(metadata or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_pipeline(pipeline_id)

    def update_pipeline(
        self,
        pipeline_id: str,
        *,
        status: str,
        stages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Actualiza estado, etapas y metadata de un pipeline existente."""
        self.connection.execute(
            """
            UPDATE pipelines
            SET status = ?, stages = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, json_dumps(stages), json_dumps(metadata), utc_now(), pipeline_id),
        )
        return self.get_pipeline(pipeline_id)

    def get_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        """Devuelve el pipeline por id; lanza `KeyError` si no existe."""
        row = self.connection.execute("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)).fetchone()
        if not row:
            raise KeyError(f"Pipeline not found: {pipeline_id}")
        return row_to_pipeline(row)

    def list_pipelines(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista pipelines del proyecto (o todos si no se filtra), del más reciente al más antiguo."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM pipelines WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM pipelines ORDER BY created_at DESC").fetchall()
        return [row_to_pipeline(row) for row in rows]
