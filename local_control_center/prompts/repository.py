"""Acceso a datos de plantillas de prompts sobre SQLite, con versionado por escritura.

Persiste plantillas en `prompt_templates` y anexa un snapshot inmutable a `prompt_versions` en
cada upsert. Emite las sentencias sobre la conexión recibida y delega el commit/rollback al
caller: un upsert escribe varias filas que solo son atómicas si la conexión las agrupa en una
única transacción.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now


def row_to_prompt(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de `prompt_templates` al dict camelCase del contrato, deserializando `applies_to`."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "mode": row["mode"],
        "body": row["body"],
        "optimizer": row["optimizer"],
        "appliesTo": json_loads(row["applies_to"]),
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class PromptsRepository:
    """Repositorio SQLite de plantillas de prompts y su historial de versiones.

    No abre ni cierra transacciones: ejecuta sobre la `connection` provista y confía en que el
    caller haga commit/rollback, de modo que las múltiples escrituras de un upsert puedan
    confirmarse como una sola unidad.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert_prompt_template(
        self,
        *,
        project_id: str,
        name: str,
        body: str,
        mode: str = "manual",
        optimizer: str = "",
        applies_to: dict[str, Any] | None = None,
        prompt_id: str | None = None,
    ) -> dict[str, Any]:
        """Crea o actualiza una plantilla e inserta su nuevo snapshot de versión.

        Sin `prompt_id` (o si no existe) inserta una plantilla nueva en versión 1; si existe,
        la actualiza e incrementa `version`. En ambos casos anexa la versión resultante a
        `prompt_versions`. Estas escrituras (UPDATE/INSERT + INSERT de versión) deben confirmarse
        juntas por el caller para no dejar la plantilla y su historial desincronizados.
        """
        timestamp = utc_now()
        existing = (
            self.connection.execute("SELECT * FROM prompt_templates WHERE id = ?", (prompt_id,)).fetchone()
            if prompt_id
            else None
        )
        next_id = prompt_id or f"prompt-{uuid.uuid4()}"
        version = (existing["version"] + 1) if existing else 1
        serialized_applies_to = json_dumps(applies_to or {})
        if existing:
            self.connection.execute(
                """
                UPDATE prompt_templates
                SET name = ?, mode = ?, body = ?, optimizer = ?, applies_to = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, mode, body, optimizer, serialized_applies_to, version, timestamp, next_id),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO prompt_templates
                    (id, project_id, name, mode, body, optimizer, applies_to, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_id,
                    project_id,
                    name,
                    mode,
                    body,
                    optimizer,
                    serialized_applies_to,
                    version,
                    timestamp,
                    timestamp,
                ),
            )
        self.connection.execute(
            """
            INSERT INTO prompt_versions
                (id, prompt_id, version, body, mode, optimizer, applies_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"prompt-version-{uuid.uuid4()}",
                next_id,
                version,
                body,
                mode,
                optimizer,
                serialized_applies_to,
                timestamp,
            ),
        )
        return self.get_prompt_template(next_id)

    def get_prompt_template(self, prompt_id: str) -> dict[str, Any]:
        """Devuelve la plantilla por id; lanza `KeyError` si no existe.

        Raises:
            KeyError: si no hay plantilla con ese id.
        """
        row = self.connection.execute("SELECT * FROM prompt_templates WHERE id = ?", (prompt_id,)).fetchone()
        if not row:
            raise KeyError(f"Prompt template not found: {prompt_id}")
        return row_to_prompt(row)

    def list_prompt_templates(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista plantillas ordenadas por actualización descendente, filtrando por proyecto si se indica."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM prompt_templates WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM prompt_templates ORDER BY updated_at DESC"
            ).fetchall()
        return [row_to_prompt(row) for row in rows]
