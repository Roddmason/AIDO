"""Persistencia SQLite de memory items y sus embeddings.

Traduce filas a dicts con claves camelCase del contrato y emite los
INSERT/UPDATE del slice. La conexión recibida opera en autocommit
(isolation_level=None): cada execute persiste de forma independiente, no hay
transacción multi-statement aquí, y el caller es dueño de cualquier commit
o BEGIN explícito si necesita atomicidad entre varias operaciones.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads, stable_hash
from local_control_center.shared.time import utc_now

CANONICAL_EXPIRY_PATTERN = "____-__-__T__:__:__.___Z"
"""Forma canónica de ``shared.time.utc_now``: única comparable lexicográficamente sin ambigüedad."""


def row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de memory_items al dict camelCase del contrato HTTP."""
    row_keys = set(row.keys())
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "scope": row["scope"],
        "scopeId": row["scope_id"],
        "kind": row["kind"],
        "content": row["content"],
        "sourceRef": row["source_ref"],
        "version": row["version"],
        "hash": row["hash"],
        "supersedesId": row["supersedes_id"],
        "createdByRunId": row["created_by_run_id"],
        "validFrom": row["valid_from"],
        "expiresAt": row["expires_at"] if "expires_at" in row_keys else None,
        "deletedAt": row["deleted_at"] if "deleted_at" in row_keys else None,
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_embedding(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de embedding (join con su memory item) al dict camelCase."""
    return {
        "memoryItemId": row["memory_item_id"],
        "projectId": row["project_id"],
        "provider": row["provider"],
        "model": row["model"],
        "dimensions": int(row["dimensions"]),
        "embedding": json_loads(row["embedding_json"], []),
        "indexedAt": row["indexed_at"],
    }


def row_to_memory_conflict(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de memory_conflicts al dict camelCase del contrato HTTP."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "leftMemoryItemId": row["left_memory_item_id"],
        "rightMemoryItemId": row["right_memory_item_id"],
        "scope": row["scope"],
        "scopeId": row["scope_id"],
        "kind": row["kind"],
        "score": float(row["score"]),
        "threshold": float(row["threshold"]),
        "detector": row["detector"],
        "status": row["status"],
        "detectedAt": row["detected_at"],
        "updatedAt": row["updated_at"],
    }


class MemoryRepository:
    """Acceso a memory_items y memory_embeddings sobre la conexión SQLite del caller.

    Cada método escribe con execute en autocommit; no abre transacciones
    propias. Las lecturas excluyen por defecto los items borrados o expirados.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, tuple(params)).fetchall()

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, tuple(params)).fetchone()

    def create_memory_item(
        self,
        *,
        project_id: str,
        scope: str,
        scope_id: str,
        kind: str,
        content: str,
        source_ref: str = "",
        version: int = 1,
        metadata: dict[str, Any] | None = None,
        supersedes_id: str | None = None,
        created_by_run_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Inserta un memory item con id e hash de contenido y devuelve la fila creada.

        El INSERT se autocommitea por sí solo; no agrupa otras escrituras.
        """
        timestamp = utc_now()
        memory_id = f"memory-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO memory_items
                (id, project_id, scope, scope_id, kind, content, source_ref, version, hash,
                 supersedes_id, created_by_run_id, valid_from, expires_at, deleted_at, metadata,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                memory_id,
                project_id,
                scope,
                scope_id,
                kind,
                content,
                source_ref,
                version,
                stable_hash(content),
                supersedes_id,
                created_by_run_id,
                timestamp,
                expires_at,
                json_dumps(metadata or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_memory_item(memory_id, include_inactive=True)

    def get_memory_item(self, memory_id: str, *, include_inactive: bool = False) -> dict[str, Any]:
        """Recupera un memory item por id, omitiendo borrados/expirados salvo include_inactive.

        Raises:
            KeyError: si no existe un item visible para ese id.
        """
        if include_inactive:
            row = self._query_one("SELECT * FROM memory_items WHERE id = ?", (memory_id,))
        else:
            row = self._query_one(
                """
                SELECT * FROM memory_items
                WHERE id = ?
                  AND deleted_at IS NULL
                  AND (expires_at IS NULL OR expires_at = '' OR expires_at > ?)
                """,
                (memory_id, utc_now()),
            )
        if not row:
            raise KeyError(f"Memory item not found: {memory_id}")
        return row_to_memory(row)

    def list_memory_items(
        self,
        project_id: str | None = None,
        *,
        include_inactive: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista memory items por proyecto, activos primero por created_at ascendente.

        ``limit`` conserva el tail más reciente sin alterar el orden ascendente final.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if not include_inactive:
            clauses.append("deleted_at IS NULL")
            clauses.append("(expires_at IS NULL OR expires_at = '' OR expires_at > ?)")
            params.append(utc_now())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        if limit is None:
            rows = self._query(f"SELECT * FROM memory_items {where} ORDER BY created_at ASC", params)
        else:
            # Tail: trae los N más recientes en DESC determinista y los invierte a ASC.
            rows = list(
                reversed(
                    self._query(
                        f"SELECT * FROM memory_items {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                        [*params, int(limit)],
                    )
                )
            )
        return [row_to_memory(row) for row in rows]

    def delete_memory_item(self, memory_id: str, *, reason: str = "") -> dict[str, Any]:
        """Borra lógicamente un item (fija deleted_at, conserva el primero) y guarda el motivo.

        deleted_at usa COALESCE: un segundo borrado no pisa la marca original.
        El UPDATE se autocommitea; no es atómico con el get previo.
        """
        timestamp = utc_now()
        existing = self.get_memory_item(memory_id, include_inactive=True)
        metadata = {**existing["metadata"], "deleteReason": reason}
        self.connection.execute(
            """
            UPDATE memory_items
            SET deleted_at = COALESCE(deleted_at, ?),
                metadata = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (timestamp, json_dumps(metadata), timestamp, memory_id),
        )
        return self.get_memory_item(memory_id, include_inactive=True)

    def list_expired_memory_items(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista los items vivos cuya expiración ya venció, en orden determinista.

        Sólo considera ``expires_at`` en el formato canónico de ``shared.time`` (milisegundos y
        sufijo ``Z``). Todas las comparaciones de tiempo del repositorio son lexicográficas sobre
        texto, y un valor con offset (``...+00:00``, ``...-03:00``) se ordena mal contra la forma
        ``Z``: para un filtro de lectura eso es cosmético, pero para un borrado sería pérdida de
        datos. Lo no canónico se cuenta aparte con ``count_non_canonical_expiry`` y no se borra.

        Excluir los ya borrados es lo que hace idempotente al olvido: una segunda corrida no
        selecciona nada.
        """
        clauses = [
            "deleted_at IS NULL",
            "expires_at IS NOT NULL",
            "expires_at != ''",
            f"expires_at LIKE '{CANONICAL_EXPIRY_PATTERN}'",
            "expires_at <= ?",
        ]
        params: list[Any] = [utc_now()]
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        rows = self._query(
            f"SELECT * FROM memory_items WHERE {' AND '.join(clauses)} ORDER BY created_at ASC, id ASC",
            params,
        )
        return [row_to_memory(row) for row in rows]

    def count_non_canonical_expiry(self, project_id: str | None = None) -> int:
        """Cuenta items vivos con una expiración que el predicado lexicográfico no puede juzgar."""
        clauses = [
            "deleted_at IS NULL",
            "expires_at IS NOT NULL",
            "expires_at != ''",
            f"expires_at NOT LIKE '{CANONICAL_EXPIRY_PATTERN}'",
        ]
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        row = self._query_one(
            f"SELECT COUNT(*) AS total FROM memory_items WHERE {' AND '.join(clauses)}", params
        )
        return int(row["total"]) if row else 0

    def upsert_memory_embedding(
        self,
        *,
        memory_item_id: str,
        provider: str,
        model: str,
        embedding: list[float],
    ) -> None:
        """Inserta o reemplaza el embedding de un memory item (upsert por memory_item_id)."""
        self.connection.execute(
            """
            INSERT INTO memory_embeddings
                (memory_item_id, provider, model, dimensions, embedding_json, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_item_id) DO UPDATE SET
                provider = excluded.provider,
                model = excluded.model,
                dimensions = excluded.dimensions,
                embedding_json = excluded.embedding_json,
                indexed_at = excluded.indexed_at
            """,
            (memory_item_id, provider, model, len(embedding), json_dumps(embedding), utc_now()),
        )

    def list_indexable_embeddings(self, project_id: str) -> list[dict[str, Any]]:
        """Devuelve embeddings indexables de items vivos cuyo proveedor es api/gateway real.

        Filtra borrados y expirados, y exige un provider_account de tipo api o
        gateway para no indexar embeddings de proveedores no confiables.
        """
        rows = self._query(
            """
            SELECT e.*, m.project_id
            FROM memory_embeddings e
            JOIN memory_items m ON m.id = e.memory_item_id
            JOIN provider_accounts p ON p.provider_id = e.provider
            WHERE m.project_id = ?
              AND m.deleted_at IS NULL
              AND (m.expires_at IS NULL OR m.expires_at = '' OR m.expires_at > ?)
              AND p.provider_type IN ('api', 'gateway')
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (project_id, utc_now()),
        )
        return [row_to_embedding(row) for row in rows]

    def list_embedded_memory_items(self, project_id: str) -> list[dict[str, Any]]:
        """Devuelve items vivos con embedding indexable, con su contenido y su clave de agrupación.

        ``list_indexable_embeddings`` no proyecta scope, scope_id, kind, hash ni content: sirve
        para reconstruir el índice, no para razonar sobre los items. El detector de conflictos
        necesita la clave de agrupación y el hash, y el briefing necesita además el contenido.
        El orden es determinista por ``created_at`` e ``id`` para que dos corridas enumeren lo
        mismo en la misma secuencia; ``created_at`` tiene resolución de milisegundos y empata.
        """
        rows = self._query(
            """
            SELECT m.id, m.scope, m.scope_id, m.kind, m.hash, m.content, m.created_at,
                   e.provider, e.model, e.dimensions, e.embedding_json
            FROM memory_embeddings e
            JOIN memory_items m ON m.id = e.memory_item_id
            JOIN provider_accounts p ON p.provider_id = e.provider
            WHERE m.project_id = ?
              AND m.deleted_at IS NULL
              AND (m.expires_at IS NULL OR m.expires_at = '' OR m.expires_at > ?)
              AND p.provider_type IN ('api', 'gateway')
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (project_id, utc_now()),
        )
        return [
            {
                "memoryItemId": row["id"],
                "scope": row["scope"],
                "scopeId": row["scope_id"],
                "kind": row["kind"],
                "hash": row["hash"],
                "content": row["content"],
                "createdAt": row["created_at"],
                "provider": row["provider"],
                "model": row["model"],
                "dimensions": int(row["dimensions"]),
                "embedding": json_loads(row["embedding_json"], []),
            }
            for row in rows
        ]

    def record_memory_conflict(
        self,
        *,
        project_id: str,
        left_memory_item_id: str,
        right_memory_item_id: str,
        scope: str,
        scope_id: str,
        kind: str,
        score: float,
        threshold: float,
        detector: str,
    ) -> dict[str, Any]:
        """Registra un conflicto detectado, o refresca el existente para ese mismo par.

        El par se normaliza (id menor primero) y la clave única impide duplicar el mismo conflicto
        entre corridas. No toca los memory items: registrar una detección nunca supersede nada.
        """
        left, right = sorted((left_memory_item_id, right_memory_item_id))
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO memory_conflicts
                (id, project_id, left_memory_item_id, right_memory_item_id, scope, scope_id, kind,
                 score, threshold, detector, status, detected_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'detected', ?, ?)
            ON CONFLICT(project_id, left_memory_item_id, right_memory_item_id) DO UPDATE SET
                score = excluded.score,
                threshold = excluded.threshold,
                detector = excluded.detector,
                updated_at = excluded.updated_at
            """,
            (
                f"memory-conflict-{uuid.uuid4()}",
                project_id,
                left,
                right,
                scope,
                scope_id,
                kind,
                float(score),
                float(threshold),
                detector,
                timestamp,
                timestamp,
            ),
        )
        row = self._query_one(
            """
            SELECT * FROM memory_conflicts
            WHERE project_id = ? AND left_memory_item_id = ? AND right_memory_item_id = ?
            """,
            (project_id, left, right),
        )
        return row_to_memory_conflict(row)

    def list_memory_conflicts(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista los conflictos registrados, del más reciente al más antiguo de forma determinista."""
        if project_id:
            rows = self._query(
                """
                SELECT * FROM memory_conflicts WHERE project_id = ?
                ORDER BY detected_at DESC, rowid DESC
                """,
                (project_id,),
            )
        else:
            rows = self._query("SELECT * FROM memory_conflicts ORDER BY detected_at DESC, rowid DESC")
        return [row_to_memory_conflict(row) for row in rows]
