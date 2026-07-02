"""Persistencia de fuentes de research como entidad canónica y artifact de evidencia.

Cada fuente investigada se guarda como un artefacto (``kind="research_source"``): su contenido textual
se escribe como archivo (hash de contenido) y su procedencia —URL, publisher, ``fetchedAt``,
``trustLevel`` y artefacto relacionado— viaja en la metadata del artefacto. Además inserta una fila
``research_sources`` para que Research Source sea una entidad durable del producto y no metadata suelta.
``list_research_sources`` lee esa tabla canónica y mantiene la forma expuesta por el agente.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

RESEARCH_SOURCE_KIND = "research_source"


def persist_source(
    evidence: EvidenceRepository,
    *,
    root: str | Path,
    project_id: str,
    record: dict[str, Any],
    content: str,
    thread_id: str | None = None,
    task_id: str | None = None,
    agent_run_id: str | None = None,
    evidence_package_id: str | None = None,
    status: str = "research_ready",
) -> dict[str, Any]:
    """Crea una fila ``research_sources`` y su artifact ``research_source`` asociado.

    Escribe el contenido como archivo de artefacto y guarda la procedencia (``url``, ``publisher``,
    ``fetchedAt``, ``trustLevel``, ``relatedArtifact``) en la metadata, con ``content_hash`` igual al
    ``hash`` del registro para que el artefacto sea verificable por hash.
    """
    artifact_id = f"artifact-{uuid.uuid4()}"
    written = write_text_artifact(root=Path(root), artifact_id=artifact_id, suffix=".txt", content=content)
    artifact = evidence.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        evidence_package_id=evidence_package_id,
        kind=RESEARCH_SOURCE_KIND,
        path=written["path"],
        content_hash=record["hash"],
        metadata={
            "url": record["url"],
            "title": record["title"],
            "publisher": record["publisher"],
            "sourceType": record["sourceType"],
            "fetchedAt": record["fetchedAt"],
            "trustLevel": record["trustLevel"],
            "relatedArtifact": record.get("relatedArtifact"),
            "relatedThreadId": record.get("relatedThreadId") or thread_id,
            "relatedTaskId": record.get("relatedTaskId") or task_id,
            "hashAlgorithm": "sha256",
        },
    )
    source_id = f"research-source-{uuid.uuid4()}"
    timestamp = utc_now()
    related_thread_id = record.get("relatedThreadId") or thread_id
    related_task_id = record.get("relatedTaskId") or task_id
    evidence.connection.execute(
        """
        INSERT INTO research_sources
            (id, project_id, thread_id, related_thread_id, agent_run_id, evidence_package_id, artifact_id,
             source_url, url, title, publisher, source_type, trust_level, fetched_at, content_hash,
             related_artifact_id, related_task_id, status, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            project_id,
            thread_id,
            related_thread_id,
            agent_run_id,
            evidence_package_id,
            artifact["id"],
            record["url"],
            record["url"],
            record["title"],
            record["publisher"],
            record["sourceType"],
            record["trustLevel"],
            record["fetchedAt"],
            record["hash"],
            record.get("relatedArtifact"),
            related_task_id,
            status,
            json_dumps(redact_secrets({"artifactPath": artifact["path"], "hashAlgorithm": "sha256"})),
            timestamp,
        ),
    )
    return {
        "id": source_id,
        "artifactId": artifact["id"],
        "kind": artifact["kind"],
        "path": artifact["path"],
        "url": record["url"],
        "title": record["title"],
        "publisher": record["publisher"],
        "sourceType": record["sourceType"],
        "fetchedAt": record["fetchedAt"],
        "hash": record["hash"],
        "trustLevel": record["trustLevel"],
        "relatedArtifact": record.get("relatedArtifact"),
        "relatedThreadId": related_thread_id,
        "relatedTaskId": related_task_id,
        "status": status,
        "createdAt": timestamp,
        "artifact": artifact,
    }


def list_research_sources(connection: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Lista los registros de procedencia de las fuentes persistidas de un proyecto, recientes primero."""
    if _has_table(connection, "research_sources"):
        rows = connection.execute(
            """
            SELECT * FROM research_sources
            WHERE project_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (project_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "artifactId": row["artifact_id"],
                "url": _row_value(row, "url") or row["source_url"],
                "title": _row_value(row, "title") or row["publisher"],
                "publisher": row["publisher"],
                "sourceType": _row_value(row, "source_type") or "web",
                "fetchedAt": row["fetched_at"],
                "hash": row["content_hash"],
                "trustLevel": row["trust_level"],
                "relatedArtifact": row["related_artifact_id"],
                "status": row["status"],
                "threadId": row["thread_id"],
                "relatedThreadId": _row_value(row, "related_thread_id") or row["thread_id"],
                "relatedTaskId": _row_value(row, "related_task_id"),
                "agentRunId": row["agent_run_id"],
                "evidencePackageId": row["evidence_package_id"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    rows = connection.execute(
        "SELECT * FROM artifacts WHERE project_id = ? AND kind = ? ORDER BY created_at DESC, rowid DESC",
        (project_id, RESEARCH_SOURCE_KIND),
    ).fetchall()
    sources: list[dict[str, Any]] = []
    for row in rows:
        metadata = json_loads(row["metadata"])
        sources.append(
            {
                "artifactId": row["id"],
                "url": metadata.get("url"),
                "title": metadata.get("title") or metadata.get("publisher"),
                "publisher": metadata.get("publisher"),
                "sourceType": metadata.get("sourceType") or "web",
                "fetchedAt": metadata.get("fetchedAt"),
                "hash": row["hash"],
                "trustLevel": metadata.get("trustLevel"),
                "relatedArtifact": metadata.get("relatedArtifact"),
                "relatedThreadId": metadata.get("relatedThreadId"),
                "relatedTaskId": metadata.get("relatedTaskId"),
            }
        )
    return sources


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _row_value(row: sqlite3.Row, column: str, default: Any = None) -> Any:
    return row[column] if column in row.keys() else default
