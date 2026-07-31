"""Persistencia SQLite de la constitución del proyecto sobre la conexión del caller.

Una constitución por proyecto (``project_constitutions``, ``project_id UNIQUE``) con historial
inmutable en ``project_constitution_versions`` (``UNIQUE(constitution_id, version)``), calcando el
par ``product_briefs``/``product_brief_versions``. El documento es fuente de verdad en BD: cualquier
copia en disco es un render derivado que se verifica por ``content_hash`` (stable_hash de los campos
de contenido) antes de usarse; un mismatch se trata como tampering y se ignora. El bootstrap deriva
una constitución base desde los settings del proyecto y la marca ``source='bootstrapped'`` para que
un run nunca quede sin gobierno, sin inventar reglas del operador.

Transacciones: la conexión llega en autocommit y estos métodos NO abren transacciones propias;
``upsert`` escribe la fila principal y su versión en dos sentencias, así que el caller debe
agruparlas en una única ``immediate_transaction`` para no desincronizar documento e historial.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads, stable_hash
from local_control_center.shared.time import utc_now

CONSTITUTION_SOURCES = ("operator", "bootstrapped")
CONSTITUTION_ENFORCEMENTS = ("advisory", "enforced")
# Campos de contenido que participan del hash: cambiar cualquiera produce versión nueva.
_CONTENT_FIELDS = ("title", "principles", "nonNegotiables", "qualityGates")

# Principios base del bootstrap: el estándar mínimo que todo proyecto hereda hasta que el operador
# escriba su propia constitución. Deliberadamente cortos: viajan al prompt de cada agente.
_BOOTSTRAP_PRINCIPLES = [
    "Surgical changes only: every modified line must trace to the current request.",
    "No secrets in code, logs, fixtures or prompts.",
    "Existing architecture and conventions win over personal preference.",
    "Every delivery needs verifiable evidence (tests, diffs, logs); never claim success without it.",
]


def content_hash_for(body: dict[str, Any]) -> str:
    """Hash estable del contenido de la constitución; ignora metadata operativa (source, timestamps)."""
    return stable_hash({field: body.get(field) for field in _CONTENT_FIELDS})


def row_to_constitution(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``project_constitutions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "title": row["title"],
        "principles": json_loads(row["principles"], []),
        "nonNegotiables": json_loads(row["non_negotiables"], []),
        "qualityGates": json_loads(row["quality_gates"], []),
        "source": row["source"],
        "enforcement": row["enforcement"],
        "contentHash": row["content_hash"],
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_constitution_version(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila de ``project_constitution_versions`` al dict camelCase del contrato."""
    return {
        "id": row["id"],
        "constitutionId": row["constitution_id"],
        "projectId": row["project_id"],
        "version": row["version"],
        "title": row["title"],
        "principles": json_loads(row["principles"], []),
        "nonNegotiables": json_loads(row["non_negotiables"], []),
        "qualityGates": json_loads(row["quality_gates"], []),
        "source": row["source"],
        "enforcement": row["enforcement"],
        "contentHash": row["content_hash"],
        "changeSummary": row["change_summary"],
        "authoredBy": row["authored_by"],
        "createdAt": row["created_at"],
    }


class ProjectConstitutionRepository:
    """CRUD versionado de la constitución; la conexión y sus transacciones son del caller."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_for_project(self, project_id: str) -> dict[str, Any] | None:
        """Devuelve la constitución vigente del proyecto o ``None`` si aún no existe."""
        row = self.connection.execute(
            "SELECT * FROM project_constitutions WHERE project_id = ?", (project_id,)
        ).fetchone()
        return row_to_constitution(row) if row else None

    def list_versions(self, constitution_id: str) -> list[dict[str, Any]]:
        """Historial inmutable de la constitución, versión más reciente primero."""
        rows = self.connection.execute(
            "SELECT * FROM project_constitution_versions WHERE constitution_id = ? ORDER BY version DESC",
            (constitution_id,),
        ).fetchall()
        return [row_to_constitution_version(row) for row in rows]

    def upsert(self, body: dict[str, Any]) -> dict[str, Any]:
        """Crea o actualiza la constitución del proyecto y anexa el snapshot al historial.

        Incrementa ``version`` cuando ya existe; el hash de contenido se recalcula siempre. El
        caller debe envolver la llamada en ``immediate_transaction`` (dos escrituras).

        Raises:
            ValueError: ante ``source``/``enforcement`` fuera de vocabulario o contenido vacío.
        """
        project_id = str(body["projectId"])
        source = str(body.get("source") or "operator")
        enforcement = str(body.get("enforcement") or "advisory")
        if source not in CONSTITUTION_SOURCES:
            raise ValueError(f"Unknown constitution source: {source}")
        if enforcement not in CONSTITUTION_ENFORCEMENTS:
            raise ValueError(f"Unknown constitution enforcement: {enforcement}")
        principles = [str(item).strip() for item in body.get("principles") or [] if str(item).strip()]
        if not principles:
            raise ValueError("A project constitution requires at least one principle.")
        non_negotiables = [
            str(item).strip() for item in body.get("nonNegotiables") or [] if str(item).strip()
        ]
        quality_gates = [str(item).strip() for item in body.get("qualityGates") or [] if str(item).strip()]
        title = str(body.get("title") or "Project constitution").strip()
        digest = content_hash_for(
            {
                "title": title,
                "principles": principles,
                "nonNegotiables": non_negotiables,
                "qualityGates": quality_gates,
            }
        )
        timestamp = utc_now()
        existing = self.connection.execute(
            "SELECT * FROM project_constitutions WHERE project_id = ?", (project_id,)
        ).fetchone()
        if existing:
            constitution_id = existing["id"]
            version = existing["version"] + 1
            self.connection.execute(
                """
                UPDATE project_constitutions
                SET title = ?, principles = ?, non_negotiables = ?, quality_gates = ?,
                    source = ?, enforcement = ?, content_hash = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    json_dumps(principles),
                    json_dumps(non_negotiables),
                    json_dumps(quality_gates),
                    source,
                    enforcement,
                    digest,
                    version,
                    timestamp,
                    constitution_id,
                ),
            )
        else:
            constitution_id = f"project-constitution-{uuid.uuid4()}"
            version = 1
            self.connection.execute(
                """
                INSERT INTO project_constitutions
                    (id, project_id, title, principles, non_negotiables, quality_gates,
                     source, enforcement, content_hash, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    constitution_id,
                    project_id,
                    title,
                    json_dumps(principles),
                    json_dumps(non_negotiables),
                    json_dumps(quality_gates),
                    source,
                    enforcement,
                    digest,
                    version,
                    timestamp,
                    timestamp,
                ),
            )
        self.connection.execute(
            """
            INSERT INTO project_constitution_versions
                (id, constitution_id, project_id, version, title, principles, non_negotiables,
                 quality_gates, source, enforcement, content_hash, change_summary, authored_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"project-constitution-version-{uuid.uuid4()}",
                constitution_id,
                project_id,
                version,
                title,
                json_dumps(principles),
                json_dumps(non_negotiables),
                json_dumps(quality_gates),
                source,
                enforcement,
                digest,
                str(body.get("changeSummary") or ""),
                str(body.get("authoredBy") or "operator"),
                timestamp,
            ),
        )
        result = self.get_for_project(project_id)
        if result is None:
            raise ValueError(f"Constitution upsert did not persist for project: {project_id}")
        return result

    def bootstrap_if_missing(
        self, project_id: str, *, goal_statement: str = "", authored_by: str = "aido_bootstrap"
    ) -> dict[str, Any]:
        """Materializa la constitución base cuando el proyecto no tiene una; nunca pisa la existente.

        Deriva el contenido de los settings disponibles (hoy: el goal statement como preámbulo) más
        los principios base de ingeniería, marcada ``source='bootstrapped'`` y ``advisory`` para que
        el run continúe: el gobierno duro solo lo activa el operador subiendo a ``enforced``.
        """
        existing = self.get_for_project(project_id)
        if existing:
            return existing
        principles = list(_BOOTSTRAP_PRINCIPLES)
        goal = str(goal_statement or "").strip()
        if goal:
            principles.insert(0, f"Project goal: {goal}")
        return self.upsert(
            {
                "projectId": project_id,
                "title": "Project constitution (bootstrapped)",
                "principles": principles,
                "nonNegotiables": [],
                "qualityGates": [],
                "source": "bootstrapped",
                "enforcement": "advisory",
                "changeSummary": "Bootstrapped from project settings; edit to make it yours.",
                "authoredBy": authored_by,
            }
        )
