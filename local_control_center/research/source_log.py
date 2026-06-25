"""Persistencia de fuentes de research como artefactos de evidencia (sin tabla ni migración nuevas).

Cada fuente investigada se guarda como un artefacto (``kind="research_source"``): su contenido textual
se escribe como archivo (hash de contenido) y su procedencia —URL, publisher, ``fetchedAt``,
``trustLevel`` y artefacto relacionado— viaja en la metadata del artefacto, reutilizando la tabla
``artifacts`` existente. Así toda fuente queda trazable y verificable por hash sin introducir un nuevo
esquema. ``list_research_sources`` reconstruye los registros de procedencia desde esos artefactos.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.serialization import json_loads

RESEARCH_SOURCE_KIND = "research_source"


def persist_source(
    evidence: EvidenceRepository,
    *,
    root: str | Path,
    project_id: str,
    record: dict[str, Any],
    content: str,
) -> dict[str, Any]:
    """Crea un artefacto ``research_source`` con la fuente (contenido + procedencia) y lo devuelve.

    Escribe el contenido como archivo de artefacto y guarda la procedencia (``url``, ``publisher``,
    ``fetchedAt``, ``trustLevel``, ``relatedArtifact``) en la metadata, con ``content_hash`` igual al
    ``hash`` del registro para que el artefacto sea verificable por hash.
    """
    artifact_id = f"artifact-{uuid.uuid4()}"
    written = write_text_artifact(root=Path(root), artifact_id=artifact_id, suffix=".txt", content=content)
    return evidence.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        evidence_package_id=None,
        kind=RESEARCH_SOURCE_KIND,
        path=written["path"],
        content_hash=record["hash"],
        metadata={
            "url": record["url"],
            "publisher": record["publisher"],
            "fetchedAt": record["fetchedAt"],
            "trustLevel": record["trustLevel"],
            "relatedArtifact": record.get("relatedArtifact"),
            "hashAlgorithm": "sha256",
        },
    )


def list_research_sources(connection: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Lista los registros de procedencia de las fuentes persistidas de un proyecto, recientes primero."""
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
                "publisher": metadata.get("publisher"),
                "fetchedAt": metadata.get("fetchedAt"),
                "hash": row["hash"],
                "trustLevel": metadata.get("trustLevel"),
                "relatedArtifact": metadata.get("relatedArtifact"),
            }
        )
    return sources
