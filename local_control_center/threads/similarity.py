"""Servicio de similitud lexical para detectar trabajo ya tratado en otros threads.

Mantiene un índice SQLite derivado de ``project_threads`` + mensajes/decisiones/artifacts recientes y calcula
similitud inicial sin proveedores externos: normalización lexical, keywords y overlap de tokens.
Embeddings/memory_retrieval quedan fuera del camino crítico hasta que exista un contrato operativo
disponible; el índice relacional sigue siendo la fuente auditable.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
import uuid
from collections import Counter
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads, stable_hash
from local_control_center.shared.time import utc_now

SIMILARITY_ACTIONS = (
    "continue_existing",
    "improve_existing",
    "performance_pass",
    "create_new_anyway",
)
HIGH_SIMILARITY_THRESHOLD = 0.6
MAX_INDEX_TEXT_CHARS = 8_000
MAX_KEYWORDS = 24
MAX_CANDIDATES = 20
FUNCTIONALITY_SOURCE_STATUSES = {"resolved", "archived"}
FUNCTIONALITY_MATCH_THRESHOLD = 0.45
MAX_FILE_PATHS = 50
MAX_PERFORMANCE_NOTES = 20
_PATH_PATTERN = re.compile(
    r"(?:(?:[A-Za-z]:)?[\\/])?(?:[A-Za-z0-9_.@() -]+[\\/])+[A-Za-z0-9_.@() -]+\.[A-Za-z0-9]+"
)

_STOPWORDS = {
    "a",
    "al",
    "and",
    "are",
    "as",
    "con",
    "de",
    "del",
    "el",
    "en",
    "for",
    "in",
    "is",
    "la",
    "las",
    "lo",
    "los",
    "of",
    "or",
    "para",
    "por",
    "que",
    "sin",
    "the",
    "to",
    "un",
    "una",
    "use",
    "y",
}


def row_to_similarity_event(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea un evento de similitud a la forma camelCase de API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "sourceThreadId": row["source_thread_id"],
        "candidateThreadId": row["candidate_thread_id"],
        "score": float(row["score"]),
        "reason": row["reason"],
        "action": row["action"],
        "functionalityId": row["functionality_id"] if "functionality_id" in row.keys() else "",
        "createdAt": row["created_at"],
    }


def row_to_functionality(row: sqlite3.Row) -> dict[str, Any]:
    """Mapea una fila del registry de funcionalidad al contrato camelCase."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "summary": row["summary"],
        "normalizedName": row["normalized_name"],
        "fingerprint": row["fingerprint"],
        "sourceThreadId": row["source_thread_id"] or "",
        "status": row["status"],
        "filePaths": json_loads(row["file_paths_json"], []),
        "performanceNotes": json_loads(row["performance_notes_json"], []),
        "metadata": json_loads(row["metadata"], {}),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class ThreadSimilarityService:
    """Indexa threads y calcula candidatos similares dentro del mismo proyecto."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def index_thread(self, thread_id: str) -> dict[str, Any]:
        """Reconstruye el índice de un thread desde su header, últimos mensajes y artifacts."""
        thread = self._thread_row(thread_id)
        title = str(redact_secrets(thread["title"] or "")).strip()
        summary = str(redact_secrets(thread["summary"] or "")).strip()
        artifact_refs, artifact_text = self._artifact_context(thread_id)
        material = " ".join(
            part
            for part in [
                title,
                summary,
                self._recent_message_text(thread_id),
                self._recent_decision_text(thread_id),
                self._recent_event_text(thread_id),
                artifact_text,
            ]
            if part
        )[:MAX_INDEX_TEXT_CHARS]
        normalized_goal = normalize_text(material)
        keywords = extract_keywords(normalized_goal)
        timestamp = utc_now()
        existing = self.connection.execute(
            "SELECT id FROM thread_memory_index WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        index_id = existing["id"] if existing else f"thread-index-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO thread_memory_index
                (id, project_id, thread_id, title, summary, normalized_goal, fingerprint,
                 keywords_json, artifact_refs_json, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                project_id = excluded.project_id,
                title = excluded.title,
                summary = excluded.summary,
                normalized_goal = excluded.normalized_goal,
                fingerprint = excluded.fingerprint,
                keywords_json = excluded.keywords_json,
                artifact_refs_json = excluded.artifact_refs_json,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                index_id,
                thread["project_id"],
                thread_id,
                title,
                summary,
                normalized_goal,
                stable_hash(normalized_goal),
                json_dumps(keywords),
                json_dumps(artifact_refs),
                thread["status"],
                timestamp,
            ),
        )
        return self.get_index(thread_id)

    def get_index(self, thread_id: str) -> dict[str, Any]:
        """Devuelve la fila indexada de un thread o lanza ``KeyError`` si no existe."""
        row = self.connection.execute(
            "SELECT * FROM thread_memory_index WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Thread index not found: {thread_id}")
        return _row_to_index(row)

    def index_project(self, project_id: str) -> None:
        """Reconstrucción total del índice del proyecto (vía explícita de ``POST .../memory/reindex``)."""
        rows = self.connection.execute(
            "SELECT id FROM project_threads WHERE project_id = ? ORDER BY updated_at ASC",
            (project_id,),
        ).fetchall()
        for row in rows:
            self.index_thread(row["id"])

    def ensure_project_index(self, project_id: str) -> int:
        """Backfill mínimo: indexa solo threads sin fila de índice o con índice más viejo que el thread.

        Es la vía barata para rutas de lectura; en estado estacionario (los mutadores del
        repositorio ya indexan en cada escritura) devuelve 0 sin reescribir nada.
        """
        rows = self.connection.execute(
            """
            SELECT t.id FROM project_threads t
            LEFT JOIN thread_memory_index i ON i.thread_id = t.id
            WHERE t.project_id = ? AND (i.thread_id IS NULL OR i.updated_at < t.updated_at)
            ORDER BY t.updated_at ASC, t.rowid ASC
            """,
            (project_id,),
        ).fetchall()
        for row in rows:
            self.index_thread(row["id"])
        return len(rows)

    def ensure_project_functionality(self, project_id: str) -> int:
        """Materializa funcionalidad solo para threads resueltos/archivados sin registro fresco.

        El JOIN por fingerprint evita re-upserts en bucle cuando dos threads comparten huella.
        """
        statuses = sorted(FUNCTIONALITY_SOURCE_STATUSES)
        placeholders = ", ".join("?" for _ in statuses)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT t.id, t.updated_at, t.rowid AS thread_rowid
            FROM project_threads t
            JOIN thread_memory_index i ON i.thread_id = t.id
            LEFT JOIN functionality_registry f
                ON f.project_id = t.project_id
               AND (f.source_thread_id = t.id OR f.fingerprint = i.fingerprint)
            WHERE t.project_id = ?
              AND t.status IN ({placeholders})
              AND (f.id IS NULL OR f.updated_at < t.updated_at)
            ORDER BY t.updated_at ASC, thread_rowid ASC
            """,
            (project_id, *statuses),
        ).fetchall()
        for row in rows:
            self.upsert_functionality_from_thread(row["id"])
        return len(rows)

    def reindex_thread_memory(self, thread_id: str) -> dict[str, Any]:
        """Reconstruye índice y registry derivado para un thread concreto."""
        index = self.index_thread(thread_id)
        functionality = None
        if index["status"] in FUNCTIONALITY_SOURCE_STATUSES:
            functionality = self.upsert_functionality_from_thread(thread_id)
        return {"index": index, "functionality": functionality}

    def reindex_project_memory(self, project_id: str) -> None:
        """Reconstrucción total de índice + funcionalidad (vía explícita de ``POST .../memory/reindex``)."""
        rows = self.connection.execute(
            "SELECT id, status FROM project_threads WHERE project_id = ? ORDER BY updated_at ASC",
            (project_id,),
        ).fetchall()
        for row in rows:
            self.index_thread(row["id"])
            if row["status"] in FUNCTIONALITY_SOURCE_STATUSES:
                self.upsert_functionality_from_thread(row["id"])

    def upsert_functionality_from_thread(self, thread_id: str) -> dict[str, Any]:
        """Materializa un thread como funcionalidad del proyecto para decisiones futuras."""
        thread = self._thread_row(thread_id)
        if thread["status"] == "deleted" or ("deleted_at" in thread.keys() and thread["deleted_at"]):
            raise ValueError("Deleted threads cannot be registered as functionality")
        index = self.index_thread(thread_id)
        name = str(redact_secrets(thread["title"] or "")).strip()
        summary = str(redact_secrets(thread["summary"] or "")).strip()
        normalized_name = normalize_text(" ".join([name, summary, str(index["normalizedGoal"])]))
        if not normalized_name:
            raise ValueError("Functionality must have indexable text")
        timestamp = utc_now()
        fingerprint = stable_hash(normalized_name)
        existing = self.connection.execute(
            """
            SELECT * FROM functionality_registry
            WHERE project_id = ? AND (fingerprint = ? OR source_thread_id = ?)
            ORDER BY CASE WHEN source_thread_id = ? THEN 0 ELSE 1 END, updated_at DESC
            LIMIT 1
            """,
            (thread["project_id"], fingerprint, thread_id, thread_id),
        ).fetchone()
        functionality_id = existing["id"] if existing else f"functionality-{uuid.uuid4()}"
        created_at = existing["created_at"] if existing else timestamp
        file_paths = self._file_paths_for_thread(thread_id)
        performance_notes = self._performance_notes_for_thread(thread_id)
        metadata = {
            "source": "thread_memory",
            "threadStatus": thread["status"],
            "keywords": index["keywords"],
            "artifactRefs": index["artifactRefs"],
        }
        self.connection.execute(
            """
            INSERT INTO functionality_registry
                (id, project_id, name, summary, normalized_name, fingerprint, source_thread_id, status,
                 file_paths_json, performance_notes_json, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                summary = excluded.summary,
                normalized_name = excluded.normalized_name,
                fingerprint = excluded.fingerprint,
                source_thread_id = excluded.source_thread_id,
                status = excluded.status,
                file_paths_json = excluded.file_paths_json,
                performance_notes_json = excluded.performance_notes_json,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            ON CONFLICT(project_id, fingerprint) DO UPDATE SET
                name = excluded.name,
                summary = excluded.summary,
                normalized_name = excluded.normalized_name,
                source_thread_id = excluded.source_thread_id,
                status = excluded.status,
                file_paths_json = excluded.file_paths_json,
                performance_notes_json = excluded.performance_notes_json,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                functionality_id,
                thread["project_id"],
                name,
                summary,
                normalized_name,
                fingerprint,
                thread_id,
                thread["status"],
                json_dumps(file_paths),
                json_dumps(performance_notes),
                json_dumps(metadata),
                created_at,
                timestamp,
            ),
        )
        return self._functionality_by_fingerprint(project_id=thread["project_id"], fingerprint=fingerprint)

    def list_project_functionality(
        self,
        *,
        project_id: str,
        query: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Lista funcionalidad registrada; si hay query, ordena por similitud lexical."""
        self.ensure_project_index(project_id)
        self.ensure_project_functionality(project_id)
        bounded_limit = max(1, min(int(limit), 100))
        rows = self.connection.execute(
            """
            SELECT * FROM functionality_registry
            WHERE project_id = ? AND status <> 'deleted'
            ORDER BY updated_at DESC, rowid DESC
            """,
            (project_id,),
        ).fetchall()
        records = [row_to_functionality(row) for row in rows]
        query_text = str(query or "").strip()
        if not query_text:
            return records[:bounded_limit]
        scored: list[tuple[float, dict[str, Any]]] = []
        query_normalized = normalize_text(str(redact_secrets(query_text)))
        query_tokens = set(query_normalized.split())
        for record in records:
            candidate = {
                "title": record["name"],
                "normalizedGoal": record["normalizedName"],
                "keywords": extract_keywords(record["normalizedName"]),
            }
            score, reason = _score_candidate(query_tokens, query_normalized, candidate)
            if score <= 0:
                continue
            scored_record = {**record, "score": round(score, 3), "reason": reason}
            scored.append((score, scored_record))
        scored.sort(key=lambda item: (item[0], item[1]["updatedAt"]), reverse=True)
        return [item[1] for item in scored[:bounded_limit]]

    def find_existing_functionality(
        self,
        *,
        project_id: str,
        query: str,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Busca funcionalidad existente suficientemente parecida a la solicitud."""
        return [
            item
            for item in self.list_project_functionality(project_id=project_id, query=query, limit=limit)
            if float(item.get("score") or 0.0) >= FUNCTIONALITY_MATCH_THRESHOLD
        ]

    def find_similar(
        self,
        *,
        project_id: str,
        query: str | None = None,
        source_thread_id: str | None = None,
        include_deleted: bool = False,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Busca candidatos similares por texto libre o por el propio índice de un thread fuente."""
        self.ensure_project_index(project_id)
        source_index: dict[str, Any] | None = None
        if source_thread_id:
            source_index = self.index_thread(source_thread_id)
        query_text = str(query or "").strip()
        if not query_text and source_index is not None:
            query_text = " ".join(
                [
                    str(source_index["title"]),
                    str(source_index["summary"]),
                    str(source_index["normalizedGoal"]),
                ]
            )
        if not query_text.strip():
            raise ValueError("Similarity query is required")

        query_normalized = normalize_text(str(redact_secrets(query_text)))
        query_tokens = set(query_normalized.split())
        if not query_tokens:
            return []

        clauses = ["project_id = ?"]
        params: list[Any] = [project_id]
        if source_thread_id:
            clauses.append("thread_id <> ?")
            params.append(source_thread_id)
        if not include_deleted:
            clauses.append("status <> 'deleted'")
        bounded_limit = max(1, min(int(limit), MAX_CANDIDATES))
        rows = self.connection.execute(
            f"""
            SELECT * FROM thread_memory_index
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, rowid DESC
            """,
            params,
        ).fetchall()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidate = _row_to_index(row)
            score, reason = _score_candidate(query_tokens, query_normalized, candidate)
            if score <= 0:
                continue
            candidates.append(
                {
                    "threadId": candidate["threadId"],
                    "projectId": candidate["projectId"],
                    "title": candidate["title"],
                    "summary": candidate["summary"],
                    "status": candidate["status"],
                    "score": round(score, 3),
                    "reason": reason,
                    "keywords": candidate["keywords"],
                    "artifactRefs": candidate["artifactRefs"],
                    "updatedAt": candidate["updatedAt"],
                }
            )
        candidates.sort(key=lambda item: (item["score"], item["updatedAt"]), reverse=True)
        return candidates[:bounded_limit]

    def mark_similarity(
        self,
        *,
        project_id: str,
        source_thread_id: str,
        candidate_thread_id: str,
        score: float,
        reason: str,
        action: str,
    ) -> dict[str, Any]:
        """Registra la decisión tomada sobre un candidato similar y la enlaza al thread fuente."""
        if action not in SIMILARITY_ACTIONS:
            raise ValueError(f"Unknown similarity action: {action}")
        source = self._thread_row(source_thread_id)
        candidate = self._thread_row(candidate_thread_id)
        if source["project_id"] != project_id or candidate["project_id"] != project_id:
            raise ValueError("Similarity candidates must belong to the same project")
        functionality_id = ""
        if action != "create_new_anyway":
            functionality_id = self.upsert_functionality_from_thread(candidate_thread_id)["id"]
        event_id = f"thread-similarity-{uuid.uuid4()}"
        clean_reason = str(redact_secrets(reason or "")).strip()
        bounded_score = max(0.0, min(float(score), 1.0))
        self.connection.execute(
            """
            INSERT INTO thread_similarity_events
                (id, project_id, source_thread_id, candidate_thread_id, score, reason, action,
                 functionality_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                project_id,
                source_thread_id,
                candidate_thread_id,
                bounded_score,
                clean_reason,
                action,
                functionality_id,
                utc_now(),
            ),
        )
        if action == "performance_pass" and functionality_id:
            self._refresh_functionality_performance_notes(functionality_id)
        from local_control_center.threads.repository import ThreadsRepository

        ThreadsRepository(self.connection).record_event(
            thread_id=source_thread_id,
            type="similarity_marked",
            agent_role="aido_lead",
            payload={
                "candidateThreadId": candidate_thread_id,
                "score": bounded_score,
                "reason": clean_reason,
                "action": action,
            },
        )
        row = self.connection.execute(
            "SELECT * FROM thread_similarity_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return row_to_similarity_event(row)

    def list_similarity_events(self, *, source_thread_id: str) -> list[dict[str, Any]]:
        """Lista decisiones de similitud registradas para un thread fuente."""
        rows = self.connection.execute(
            """
            SELECT * FROM thread_similarity_events
            WHERE source_thread_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (source_thread_id,),
        ).fetchall()
        return [row_to_similarity_event(row) for row in rows]

    def _thread_row(self, thread_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM project_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Thread not found: {thread_id}")
        return row

    def _functionality_by_fingerprint(self, *, project_id: str, fingerprint: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT * FROM functionality_registry
            WHERE project_id = ? AND fingerprint = ?
            """,
            (project_id, fingerprint),
        ).fetchone()
        if not row:
            raise KeyError(f"Functionality not found: {fingerprint}")
        return row_to_functionality(row)

    def _refresh_functionality_performance_notes(self, functionality_id: str) -> None:
        row = self.connection.execute(
            "SELECT source_thread_id FROM functionality_registry WHERE id = ?",
            (functionality_id,),
        ).fetchone()
        if not row or not row["source_thread_id"]:
            return
        notes = self._performance_notes_for_thread(str(row["source_thread_id"]))
        self.connection.execute(
            """
            UPDATE functionality_registry
            SET performance_notes_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (json_dumps(notes), utc_now(), functionality_id),
        )

    def _file_paths_for_thread(self, thread_id: str) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT payload AS value FROM thread_agent_events WHERE thread_id = ?
            UNION ALL
            SELECT metadata AS value FROM thread_agent_events WHERE thread_id = ?
            UNION ALL
            SELECT metadata AS value FROM thread_messages WHERE thread_id = ?
            UNION ALL
            SELECT metadata AS value FROM thread_artifacts WHERE thread_id = ?
            UNION ALL
            SELECT prompt AS value FROM thread_decisions WHERE thread_id = ?
            UNION ALL
            SELECT options AS value FROM thread_decisions WHERE thread_id = ?
            UNION ALL
            SELECT resolution AS value FROM thread_decisions WHERE thread_id = ?
            UNION ALL
            SELECT metadata AS value FROM thread_decisions WHERE thread_id = ?
            """,
            (thread_id, thread_id, thread_id, thread_id, thread_id, thread_id, thread_id, thread_id),
        ).fetchall()
        paths: list[str] = []
        for row in rows:
            stored_value = row["value"]
            parsed_value = (
                json_loads(stored_value, stored_value) if isinstance(stored_value, str) else stored_value
            )
            for path in _extract_file_paths(parsed_value):
                if path not in paths:
                    paths.append(path)
                if len(paths) >= MAX_FILE_PATHS:
                    return paths
        return paths

    def _performance_notes_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, reason, action, created_at
            FROM thread_similarity_events
            WHERE action = 'performance_pass'
              AND (source_thread_id = ? OR candidate_thread_id = ?)
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (thread_id, thread_id, MAX_PERFORMANCE_NOTES),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "source": "similarity_event",
                "action": row["action"],
                "note": str(redact_secrets(row["reason"] or ""))[:240],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def _recent_message_text(self, thread_id: str) -> str:
        rows = self.connection.execute(
            """
            SELECT content FROM thread_messages
            WHERE thread_id = ?
            ORDER BY sequence DESC
            LIMIT 5
            """,
            (thread_id,),
        ).fetchall()
        text = " ".join(str(redact_secrets(row["content"] or "")) for row in rows)
        return text[:MAX_INDEX_TEXT_CHARS]

    def _recent_decision_text(self, thread_id: str) -> str:
        rows = self.connection.execute(
            """
            SELECT title, prompt, options, resolution, metadata FROM thread_decisions
            WHERE thread_id = ? AND status = 'resolved'
            ORDER BY COALESCE(decided_at, updated_at, created_at) DESC, rowid DESC
            LIMIT 5
            """,
            (thread_id,),
        ).fetchall()
        text_parts: list[str] = []
        for row in rows:
            metadata = redact_secrets(json_loads(row["metadata"], {}))
            options = redact_secrets(json_loads(row["options"], []))
            text_parts.append(
                " ".join(
                    [
                        str(redact_secrets(row["title"] or "")),
                        str(redact_secrets(row["prompt"] or "")),
                        _flatten_text(options),
                        str(redact_secrets(row["resolution"] or "")),
                        _flatten_text(metadata),
                    ]
                )
            )
        return " ".join(text_parts)[:MAX_INDEX_TEXT_CHARS]

    def _recent_event_text(self, thread_id: str) -> str:
        rows = self.connection.execute(
            """
            SELECT type, payload, metadata FROM thread_agent_events
            WHERE thread_id = ?
            ORDER BY sequence DESC
            LIMIT 5
            """,
            (thread_id,),
        ).fetchall()
        text_parts: list[str] = []
        for row in rows:
            payload = redact_secrets(json_loads(row["payload"], {}))
            metadata = redact_secrets(json_loads(row["metadata"], {}))
            text_parts.append(
                " ".join([str(row["type"] or ""), _flatten_text(payload), _flatten_text(metadata)])
            )
        return " ".join(text_parts)[:MAX_INDEX_TEXT_CHARS]

    def _artifact_context(self, thread_id: str) -> tuple[list[dict[str, str]], str]:
        rows = self.connection.execute(
            """
            SELECT artifact_id, kind, title, metadata FROM thread_artifacts
            WHERE thread_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 5
            """,
            (thread_id,),
        ).fetchall()
        refs: list[dict[str, str]] = []
        text_parts: list[str] = []
        for row in rows:
            title = str(redact_secrets(row["title"] or "")).strip()
            refs.append(
                {
                    "artifactId": str(row["artifact_id"]),
                    "kind": str(row["kind"]),
                    "title": title,
                }
            )
            metadata = redact_secrets(json_loads(row["metadata"], {}))
            text_parts.append(" ".join([title, _flatten_text(metadata)]))
        return refs, " ".join(text_parts)[:MAX_INDEX_TEXT_CHARS]


def normalize_text(value: str) -> str:
    """Normaliza texto para comparación lexical estable y tokenizable."""
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    decomposed = unicodedata.normalize("NFKD", expanded)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [_stem_token(token) for token in cleaned.split() if len(token) >= 3 and token not in _STOPWORDS]
    return " ".join(tokens)


def extract_keywords(normalized_text: str) -> list[str]:
    """Extrae keywords determinísticas por frecuencia y orden alfabético para desempate."""
    counts = Counter(token for token in normalized_text.split() if token)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _count in ranked[:MAX_KEYWORDS]]


def _row_to_index(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "threadId": row["thread_id"],
        "title": row["title"],
        "summary": row["summary"],
        "normalizedGoal": row["normalized_goal"],
        "fingerprint": row["fingerprint"],
        "keywords": json_loads(row["keywords_json"], []),
        "artifactRefs": json_loads(row["artifact_refs_json"], []),
        "status": row["status"],
        "updatedAt": row["updated_at"],
    }


def _score_candidate(
    query_tokens: set[str],
    query_normalized: str,
    candidate: dict[str, Any],
) -> tuple[float, str]:
    candidate_tokens = set(str(candidate["normalizedGoal"]).split())
    if not candidate_tokens:
        return 0.0, "No indexable candidate text."
    shared = query_tokens & candidate_tokens
    if not shared:
        return 0.0, "No lexical overlap."
    union = query_tokens | candidate_tokens
    jaccard = len(shared) / len(union)
    coverage = len(shared) / len(query_tokens)
    query_keywords = set(extract_keywords(query_normalized))
    candidate_keywords = {str(item) for item in candidate.get("keywords", [])}
    keyword_basis = max(1, min(len(query_keywords), len(candidate_keywords)))
    keyword_overlap = len(query_keywords & candidate_keywords) / keyword_basis
    title_tokens = set(normalize_text(str(candidate.get("title") or "")).split())
    title_basis = max(1, min(len(query_tokens), len(title_tokens)))
    title_overlap = len(query_tokens & title_tokens) / title_basis
    score = min(1.0, (coverage * 0.45) + (jaccard * 0.25) + (keyword_overlap * 0.2) + (title_overlap * 0.1))
    shared_keywords = sorted(shared)[:8]
    reason = f"Lexical overlap on {len(shared)} tokens: {', '.join(shared_keywords)}."
    return score, reason


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _extract_file_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            paths.extend(_extract_file_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(_extract_file_paths(item))
    elif isinstance(value, str):
        for match in _PATH_PATTERN.findall(value):
            normalized = match.replace("\\", "/").strip()
            if normalized and normalized not in paths:
                paths.append(normalized)
    return paths


def _stem_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


class ThreadMemoryService(ThreadSimilarityService):
    """Nombre publico del servicio de memoria de threads y funcionalidad existente."""
