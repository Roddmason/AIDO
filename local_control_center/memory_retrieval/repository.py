from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Iterable

from local_control_center.shared.time import utc_now

from local_control_center.shared.serialization import json_dumps, json_loads, stable_hash


def row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
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
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
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
    ) -> dict[str, Any]:
        timestamp = utc_now()
        memory_id = f"memory-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO memory_items
                (id, project_id, scope, scope_id, kind, content, source_ref, version, hash,
                 supersedes_id, created_by_run_id, valid_from, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json_dumps(metadata or {}),
                timestamp,
                timestamp,
            ),
        )
        return self.get_memory_item(memory_id)

    def get_memory_item(self, memory_id: str) -> dict[str, Any]:
        row = self._query_one("SELECT * FROM memory_items WHERE id = ?", (memory_id,))
        if not row:
            raise KeyError(f"Memory item not found: {memory_id}")
        return row_to_memory(row)

    def list_memory_items(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._query("SELECT * FROM memory_items WHERE project_id = ? ORDER BY created_at ASC", (project_id,))
        else:
            rows = self._query("SELECT * FROM memory_items ORDER BY created_at ASC")
        return [row_to_memory(row) for row in rows]

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


