from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Iterable

from local_control_center.shared.time import utc_now

from local_control_center.shared.serialization import json_dumps, json_loads, stable_hash


def row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
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
    return {
        "memoryItemId": row["memory_item_id"],
        "projectId": row["project_id"],
        "provider": row["provider"],
        "model": row["model"],
        "dimensions": int(row["dimensions"]),
        "embedding": json_loads(row["embedding_json"], []),
        "indexedAt": row["indexed_at"],
    }


class MemoryRepository:
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
    ) -> list[dict[str, Any]]:
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
        rows = self._query(f"SELECT * FROM memory_items {where} ORDER BY created_at ASC", params)
        return [row_to_memory(row) for row in rows]

    def delete_memory_item(self, memory_id: str, *, reason: str = "") -> dict[str, Any]:
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

    def upsert_memory_embedding(
        self,
        *,
        memory_item_id: str,
        provider: str,
        model: str,
        embedding: list[float],
    ) -> None:
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
            ORDER BY m.created_at ASC
            """,
            (project_id, utc_now()),
        )
        return [row_to_embedding(row) for row in rows]


